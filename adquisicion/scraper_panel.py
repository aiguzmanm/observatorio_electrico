#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scraper_panel.py — scraper de producción para panelexpertos.cl. Sin
navegador: replica el flujo AJAX del plugin "ShareoneDrive" con `requests`
puro (investigado con Playwright en explorar_sitio.py / mapear_anios.py,
esos scripts no corren en la VM).

Mecanismo (ver README para el detalle completo):
- Cada "año" es una carpeta de OneDrive con un id fijo (datos/anios_map.json,
  generado una sola vez por mapear_anios.py -- los ids no cambian).
- Listar contenido: POST a admin-ajax.php, action=shareonedrive-get-filelist.
  Necesita un `_ajax_nonce` fresco (se saca de la página HTML, dura ~24h) y
  el `listtoken`/`driveId`/`accountId` propios del año (del mapeo).
- Descargar un archivo: GET a admin-ajax.php,
  action=shareonedrive-download&id=<file_id>&...

Estructura de cada caso (comprobada en un caso real): subcarpetas
"01.Actuaciones del Panel", "02.Escritos Presentados", "03.Actas", y un
archivo "Dictamen ...pdf" directo en la carpeta del caso.

No guarda PDF/PPT -- solo el texto extraído (igual que correspondencia_cen).
"""
from __future__ import annotations

import base64
import json
import re
import tempfile
import time
from datetime import date
from pathlib import Path
from typing import Optional

import requests

import configuracion

BASE = "https://panelexpertos.cl"
URL_TRAMITADAS = f"{BASE}/discrepancias/tramitadas/"
URL_AJAX = f"{BASE}/wp-admin/admin-ajax.php"

RAIZ_PROYECTO = Path(__file__).resolve().parent.parent
RUTA_ANIOS_MAP = RAIZ_PROYECTO / "datos" / "anios_map.json"
DB_PATH = configuracion.RAIZ_DATOS / "discrepancias.sqlite"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "X-Requested-With": "XMLHttpRequest",
}

ENTRY_RE = re.compile(
    r"class='entry\s+(?P<tipo>folder|file)\s*'\s+data-id='(?P<id>[^']+)'[^>]*?data-name='(?P<nombre>[^']+)'",
    re.DOTALL,
)

NOMBRE_CASO_RE = re.compile(
    r"^(?P<numero>\d+-\d+)\s+"
    r"(?:Disc[.,]?\s*)?"
    r"(?P<requirente>.+?)\s+"
    r"(?:en\s+contra\s+(?:del?|de\s+la)|contra)\s+"
    r"(?P<contraparte>.+?)"
    r"(?:\s+por\s+(?P<materia>.+))?$",
    re.IGNORECASE,
)


# ---------- Nonce (vigente ~24h, hay que refrescarlo cada corrida) ----------
def obtener_nonce() -> str:
    resp = requests.get(URL_TRAMITADAS, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    m = re.search(r'"refresh_nonce":"([a-f0-9]+)"', resp.text)
    if not m:
        raise RuntimeError("No se encontró refresh_nonce en la página -- el sitio pudo haber cambiado.")
    return m.group(1)


def _folder_path_b64(cadena_ids: list[str]) -> str:
    """El sitio manda folderPath como base64 de un array JSON de ids -- o
    'null' en base64 cuando la cadena está vacía (raíz)."""
    valor = json.dumps(cadena_ids) if cadena_ids else "null"
    return base64.b64encode(valor.encode()).decode()


# ---------- Listar una carpeta ----------
def listar_carpeta(
    nonce: str, listtoken: str, drive_id: str, account_id: str,
    last_folder: str, cadena_ids: list[str],
) -> tuple[list[dict], dict]:
    """Devuelve (entradas, cuerpo_json_completo). entradas: [{'tipo','id','nombre'}]"""
    datos = {
        "listtoken": listtoken,
        "drive_id": drive_id,
        "account_id": account_id,
        "lastFolder": last_folder,
        "folderPath": _folder_path_b64(cadena_ids),
        "sort": "name:asc",
        "action": "shareonedrive-get-filelist",
        "_ajax_nonce": nonce,
        "mobile": "false",
        "query": "",
        "page_url": URL_TRAMITADAS,
    }
    resp = requests.post(URL_AJAX, data=datos, headers=HEADERS, timeout=60)
    resp.raise_for_status()
    cuerpo = resp.json()
    html = cuerpo.get("html", "")
    entradas = [
        {"tipo": m.group("tipo"), "id": m.group("id"), "nombre": m.group("nombre")}
        for m in ENTRY_RE.finditer(html)
    ]
    return entradas, cuerpo


def url_descarga(file_id: str, drive_id_real: str, account_id: str, listtoken: str) -> str:
    return (
        f"{URL_AJAX}?action=shareonedrive-download&dl=1&id={file_id}"
        f"&drive_id={drive_id_real}&account_id={account_id}&listtoken={listtoken}"
    )


def extraer_texto_pdf(contenido: bytes) -> str:
    import fitz  # PyMuPDF

    fd, ruta = tempfile.mkstemp(suffix=".pdf")
    try:
        with open(fd, "wb") as f:
            f.write(contenido)
        with fitz.open(ruta) as doc:
            paginas = [p.get_text() for p in doc]
        return "\n".join(paginas).strip()
    finally:
        Path(ruta).unlink(missing_ok=True)


def categoria_por_carpeta(nombre_subcarpeta: str) -> str:
    n = nombre_subcarpeta.lower()
    if "escrito" in n:
        return "escrito"
    if "acta" in n:
        return "acta"
    if "actuacion" in n or "actuación" in n:
        return "actuacion"
    return "otro"


def parsear_nombre_caso(nombre: str) -> dict:
    """Mejor esfuerzo -- el nombre de la carpeta es texto libre escrito a
    mano por el Panel, no un campo estructurado. Si no calza el patrón,
    se guardan requirente/contraparte/materia en None; nombre_completo
    siempre queda, así que nada se pierde para la búsqueda FTS."""
    m = NOMBRE_CASO_RE.match(nombre.strip())
    if not m:
        return {"numero": None, "requirente": None, "contraparte": None, "materia": None}
    return {
        "numero": m.group("numero"),
        "requirente": m.group("requirente").strip(),
        "contraparte": m.group("contraparte").strip(),
        "materia": (m.group("materia") or "").strip() or None,
    }


# ---------- Flujo principal ----------
def procesar_anio(anio: int, mapa_anios: dict, con, limite_casos: Optional[int] = None) -> None:
    info_anio = mapa_anios[str(anio)]
    nonce = obtener_nonce()

    casos, _ = listar_carpeta(
        nonce, info_anio["listtoken"], "drive", info_anio["accountId"],
        info_anio["lastFolder"], [],
    )
    casos = [c for c in casos if c["tipo"] == "folder"]
    print(f"{anio}: {len(casos)} discrepancias encontradas")
    if limite_casos:
        casos = casos[:limite_casos]

    for caso in casos:
        procesar_caso(anio, caso, info_anio, nonce, con)
        time.sleep(1)  # no golpear el sitio de golpe


def procesar_caso(anio: int, caso: dict, info_anio: dict, nonce: str, con) -> None:
    import sqlite3

    parsed = parsear_nombre_caso(caso["nombre"])
    ahora = date.today().isoformat()

    cur = con.execute(
        "SELECT id FROM discrepancias WHERE folder_id = ?", (caso["id"],)
    ).fetchone()
    if cur:
        discrepancia_id = cur[0]
    else:
        con.execute(
            """INSERT INTO discrepancias
               (folder_id, numero, anio, nombre_completo, requirente, contraparte, materia, estado, fecha_ingesta)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (caso["id"], parsed["numero"], anio, caso["nombre"], parsed["requirente"],
             parsed["contraparte"], parsed["materia"], "tramitada", ahora),
        )
        con.commit()
        discrepancia_id = con.execute(
            "SELECT id FROM discrepancias WHERE folder_id = ?", (caso["id"],)
        ).fetchone()[0]

    print(f"  {caso['nombre'][:70]}")
    contenido, cuerpo = listar_carpeta(
        nonce, info_anio["listtoken"], "drive", info_anio["accountId"],
        caso["id"], [info_anio["lastFolder"], caso["id"]],
    )
    drive_id_real = cuerpo.get("driveId", "")

    for item in contenido:
        if item["tipo"] == "file":
            _procesar_archivo(item, "dictamen" if "dictamen" in item["nombre"].lower() else "otro",
                               discrepancia_id, info_anio, drive_id_real, con)
        else:
            categoria = categoria_por_carpeta(item["nombre"])
            subcontenido, _ = listar_carpeta(
                nonce, info_anio["listtoken"], "drive", info_anio["accountId"],
                item["id"], [info_anio["lastFolder"], caso["id"], item["id"]],
            )
            for archivo in subcontenido:
                if archivo["tipo"] == "file":
                    _procesar_archivo(archivo, categoria, discrepancia_id, info_anio, drive_id_real, con)


def _procesar_archivo(archivo: dict, categoria: str, discrepancia_id: int, info_anio: dict, drive_id_real: str, con) -> None:
    ya = con.execute("SELECT id FROM documentos WHERE file_id = ?", (archivo["id"],)).fetchone()
    if ya:
        return

    link = url_descarga(archivo["id"], drive_id_real, info_anio["accountId"], info_anio["listtoken"])
    texto = ""
    try:
        resp = requests.get(link, headers=HEADERS, timeout=90)
        resp.raise_for_status()
        if resp.content.startswith(b"%PDF"):
            texto = extraer_texto_pdf(resp.content)
        else:
            texto = "[No es un PDF -- probablemente .docx/.pptx, sin extraer todavía]"
    except Exception as e:
        texto = f"[Error descargando/extrayendo: {e}]"

    con.execute(
        """INSERT INTO documentos (discrepancia_id, file_id, nombre, categoria, texto, link_descarga, fecha_ingesta)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (discrepancia_id, archivo["id"], archivo["nombre"], categoria, texto, link, date.today().isoformat()),
    )
    con.commit()


def main() -> None:
    import sqlite3
    import sys

    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")

    if not RUTA_ANIOS_MAP.exists():
        print(f"Falta {RUTA_ANIOS_MAP} -- correr mapear_anios.py primero (una sola vez, local, con Playwright).")
        return

    mapa_anios = json.loads(RUTA_ANIOS_MAP.read_text(encoding="utf-8"))

    anio = int(sys.argv[1]) if len(sys.argv) > 1 else date.today().year
    limite = int(sys.argv[2]) if len(sys.argv) > 2 else None

    con = sqlite3.connect(DB_PATH)
    try:
        procesar_anio(anio, mapa_anios, con, limite_casos=limite)
    finally:
        con.close()


if __name__ == "__main__":
    main()
