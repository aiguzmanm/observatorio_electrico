#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scraper_panel_historico.py -- rescata las discrepancias 2004-2021, que NO
están en la API nueva (discrepancias.panelexpertos.cl solo trackea desde
2022). Única fuente para esos años: el sitio WordPress viejo
(panelexpertos.cl/discrepancias/tramitadas/), backeado por una carpeta de
OneDrive por año (mismo mecanismo que el scraper_panel.py original, ahora
obsoleto para 2022+ pero revivido acá solo para lo histórico).

Escribe en la MISMA datos/discrepancias.sqlite que usa scraper_panel_api.py,
pero esta fuente es mucho más pobre en metadata: no hay estado real (todo lo
que aparece en "tramitadas" quedó cerrado, así que estado='terminada' fijo),
no hay materia/submateria de catálogo, no hay empresas estructuradas por
rol. Esos campos quedan NULL a propósito -- no se inventan ni se adivinan
con regex. numero/anio sí se sacan del nombre de la carpeta (ej. "01-2004").

Identificador único: folder_id/file_id (OneDrive), no api_id (eso es de la
API nueva y no existe para estos años).

Dos mejoras sobre el scraper original de OneDrive, ya resueltas para la API
nueva y reaplicadas acá:
- extrae texto de .docx además de .pdf (antes solo dejaba un placeholder).
- desciende recursivamente en subcarpetas (antes "02.Escritos Presentados"
  nunca se abría más allá de un nivel, y sus 4 subsubcarpetas quedaban sin
  procesar -- cero documentos categoría 'escrito' en la base).
- descargas con tope de tiempo real (mismo bug que se encontró en
  scraper_panel_api.py: el timeout de requests no corta si la conexión
  gotea datos muy lento).
"""
from __future__ import annotations

import base64
import json
import re
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturoTimeoutError
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

# La API nueva ya cubre 2022 en adelante -- no volver a procesar esos años
# acá o quedarían discrepancias duplicadas de dos fuentes distintas.
ULTIMO_ANIO_HISTORICO = 2021

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "X-Requested-With": "XMLHttpRequest",
}

ENTRY_RE = re.compile(
    r"class='entry\s+(?P<tipo>folder|file)\s*'\s+data-id='(?P<id>[^']+)'[^>]*?data-name='(?P<nombre>[^']+)'",
    re.DOTALL,
)

NUMERO_RE = re.compile(r"^(\d+)-(\d+)")

_EJECUTOR = ThreadPoolExecutor(max_workers=4)


def _con_tope_de_tiempo(func, *args, segundos: int, **kwargs):
    futuro = _EJECUTOR.submit(func, *args, **kwargs)
    return futuro.result(timeout=segundos)


def obtener_nonce() -> str:
    def _pedir():
        resp = requests.get(URL_TRAMITADAS, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        m = re.search(r'"refresh_nonce":"([a-f0-9]+)"', resp.text)
        if not m:
            raise RuntimeError("No se encontró refresh_nonce -- el sitio pudo haber cambiado.")
        return m.group(1)
    return _con_tope_de_tiempo(_pedir, segundos=30)


def _folder_path_b64(cadena_ids: list[str]) -> str:
    valor = json.dumps(cadena_ids) if cadena_ids else "null"
    return base64.b64encode(valor.encode()).decode()


def listar_carpeta(nonce, listtoken, drive_id, account_id, last_folder, cadena_ids) -> tuple[list[dict], dict]:
    def _pedir():
        datos = {
            "listtoken": listtoken, "drive_id": drive_id, "account_id": account_id,
            "lastFolder": last_folder, "folderPath": _folder_path_b64(cadena_ids),
            "sort": "name:asc", "action": "shareonedrive-get-filelist",
            "_ajax_nonce": nonce, "mobile": "false", "query": "", "page_url": URL_TRAMITADAS,
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
    return _con_tope_de_tiempo(_pedir, segundos=30)


def url_descarga(file_id, drive_id_real, account_id, listtoken) -> str:
    return (
        f"{URL_AJAX}?action=shareonedrive-download&dl=1&id={file_id}"
        f"&drive_id={drive_id_real}&account_id={account_id}&listtoken={listtoken}"
    )


def extraer_texto(contenido: bytes, nombre_archivo: str) -> str:
    n = nombre_archivo.lower()
    if contenido.startswith(b"%PDF") or n.endswith(".pdf"):
        import fitz  # PyMuPDF
        fd, ruta = tempfile.mkstemp(suffix=".pdf")
        try:
            with open(fd, "wb") as f:
                f.write(contenido)
            with fitz.open(ruta) as doc:
                paginas = [doc[i].get_text() for i in range(doc.page_count)]
            return "\n".join(paginas).strip()
        finally:
            Path(ruta).unlink(missing_ok=True)
    elif n.endswith(".docx"):
        import docx2txt
        fd, ruta = tempfile.mkstemp(suffix=".docx")
        try:
            with open(fd, "wb") as f:
                f.write(contenido)
            return (docx2txt.process(ruta) or "").strip()
        finally:
            Path(ruta).unlink(missing_ok=True)
    return f"[Tipo de archivo no soportado para extracción de texto: {nombre_archivo}]"


def categoria_por_carpeta(nombre_subcarpeta: str) -> str:
    n = nombre_subcarpeta.lower()
    if "escrito" in n:
        return "escrito"
    if "acta" in n:
        return "acta"
    if "actuacion" in n or "actuación" in n:
        return "actuacion"
    return "otro"


def numero_de_nombre(nombre: str) -> Optional[int]:
    m = NUMERO_RE.match(nombre.strip())
    return int(m.group(1)) if m else None


def procesar_anio(anio: int, mapa_anios: dict, con) -> None:
    info_anio = mapa_anios[str(anio)]
    nonce = obtener_nonce()

    casos, _ = listar_carpeta(nonce, info_anio["listtoken"], "drive", info_anio["accountId"], info_anio["lastFolder"], [])
    casos = [c for c in casos if c["tipo"] == "folder"]
    print(f"{anio}: {len(casos)} discrepancias encontradas")

    for caso in casos:
        try:
            procesar_caso(anio, caso, info_anio, nonce, con)
        except Exception as e:
            print(f"    [error caso {caso['nombre'][:50]}]: {e}")
        time.sleep(0.5)


def procesar_caso(anio: int, caso: dict, info_anio: dict, nonce: str, con) -> None:
    t_inicio = time.time()
    ya = con.execute("SELECT id FROM discrepancias WHERE folder_id = ?", (caso["id"],)).fetchone()
    if ya:
        discrepancia_id = ya[0]
    else:
        con.execute(
            """INSERT INTO discrepancias (folder_id, numero, anio, nombre, estado, fecha_ingesta)
               VALUES (?, ?, ?, ?, 'terminada', ?)""",
            (caso["id"], numero_de_nombre(caso["nombre"]), anio, caso["nombre"], date.today().isoformat()),
        )
        con.commit()
        discrepancia_id = con.execute("SELECT id FROM discrepancias WHERE folder_id = ?", (caso["id"],)).fetchone()[0]

    print(f"  {caso['nombre'][:70]}")
    n_docs = _procesar_carpeta_recursiva(caso["id"], [info_anio["lastFolder"], caso["id"]], "otro",
                                          discrepancia_id, info_anio, nonce, con)
    print(f"    ({n_docs} documentos, {time.time() - t_inicio:.1f}s)")


def _procesar_carpeta_recursiva(folder_id: str, cadena_ids: list[str], categoria: str,
                                 discrepancia_id: int, info_anio: dict, nonce: str, con, profundidad: int = 0) -> int:
    """Baja recursivamente (con tope de profundidad por seguridad) para no
    perderse las subsubcarpetas de 'Escritos Presentados'."""
    if profundidad > 3:
        return 0
    contenido, cuerpo = listar_carpeta(nonce, info_anio["listtoken"], "drive", info_anio["accountId"], folder_id, cadena_ids)
    drive_id_real = cuerpo.get("driveId", "")
    total = 0
    for item in contenido:
        if item["tipo"] == "file":
            cat = "dictamen" if "dictamen" in item["nombre"].lower() else categoria
            if _procesar_archivo(item, cat, discrepancia_id, info_anio, drive_id_real, con):
                total += 1
        else:
            sub_categoria = categoria_por_carpeta(item["nombre"]) if categoria == "otro" else categoria
            total += _procesar_carpeta_recursiva(
                item["id"], cadena_ids + [item["id"]], sub_categoria,
                discrepancia_id, info_anio, nonce, con, profundidad + 1,
            )
    return total


def _procesar_archivo(archivo: dict, categoria: str, discrepancia_id: int, info_anio: dict, drive_id_real: str, con) -> bool:
    ya = con.execute("SELECT id FROM documentos WHERE file_id = ?", (archivo["id"],)).fetchone()
    if ya:
        return False

    link = url_descarga(archivo["id"], drive_id_real, info_anio["accountId"], info_anio["listtoken"])
    try:
        def _bajar():
            r = requests.get(link, headers=HEADERS, timeout=60)
            r.raise_for_status()
            return r.content
        contenido = _con_tope_de_tiempo(_bajar, segundos=45)
        texto = extraer_texto(contenido, archivo["nombre"])
    except FuturoTimeoutError:
        texto = "[Error: la descarga superó los 45s de tope y se abandonó]"
    except Exception as e:
        texto = f"[Error descargando/extrayendo: {e}]"

    con.execute(
        """INSERT INTO documentos (discrepancia_id, file_id, titulo, tipo, texto, fecha_ingesta)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (discrepancia_id, archivo["id"], archivo["nombre"], categoria, texto, date.today().isoformat()),
    )
    con.commit()
    return True


def main() -> None:
    import sqlite3
    import sys

    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")

    if not RUTA_ANIOS_MAP.exists():
        print(f"Falta {RUTA_ANIOS_MAP}.")
        return

    mapa_anios = json.loads(RUTA_ANIOS_MAP.read_text(encoding="utf-8"))
    anios = [int(a) for a in sorted(mapa_anios.keys()) if int(a) <= ULTIMO_ANIO_HISTORICO]

    if len(sys.argv) > 1:
        anios = [int(sys.argv[1])]

    con = sqlite3.connect(DB_PATH)
    try:
        for anio in anios:
            procesar_anio(anio, mapa_anios, con)
    finally:
        con.close()


if __name__ == "__main__":
    main()
