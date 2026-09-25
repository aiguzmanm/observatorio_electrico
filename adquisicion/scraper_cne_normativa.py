#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scraper_cne_normativa.py -- adquisidor de Normas Técnicas y Servicios
Complementarios de la CNE, vía apinormastecnicas.cne.cl (API real detrás del
iframe de portalnormativo.cne.cl -- investigado con Playwright, no hace
falta en producción, son dos GET simples).

- GET /normas     -> 11 categorías de resoluciones + "Normativa vigente"
- GET /servicios  -> 1 categoría de resoluciones (Servicios Complementarios)

Reglas heredadas del resto del proyecto: no se guardan PDF, solo texto
extraído. PERO acá no todos los archivos son descargables -- varios están
en SharePoint interno de la CNE, que exige navegador (medido: ~1.2GB de RAM
por descarga, no viable en esta VPS). Para esos, se guarda toda la metadata
y el link, sin texto -- no se intenta ni se inventa.
"""
from __future__ import annotations

import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturoTimeoutError
from datetime import date
from pathlib import Path

import requests

import configuracion

URL_NORMAS = "https://apinormastecnicas.cne.cl/normas"
URL_SERVICIOS = "https://apinormastecnicas.cne.cl/servicios"
CATEGORIA_NORMATIVA_VIGENTE = "Normativa vigente"

DB_PATH = configuracion.RAIZ_DATOS / "cne.sqlite"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
}

_EJECUTOR = ThreadPoolExecutor(max_workers=4)


def _con_tope_de_tiempo(func, *args, segundos: int, **kwargs):
    futuro = _EJECUTOR.submit(func, *args, **kwargs)
    return futuro.result(timeout=segundos)


def obtener_normas() -> dict:
    def _pedir():
        resp = requests.get(URL_NORMAS, headers=HEADERS, timeout=60)
        resp.raise_for_status()
        return resp.json()
    return _con_tope_de_tiempo(_pedir, segundos=60)


def obtener_servicios() -> dict:
    def _pedir():
        resp = requests.get(URL_SERVICIOS, headers=HEADERS, timeout=60)
        resp.raise_for_status()
        return resp.json()
    return _con_tope_de_tiempo(_pedir, segundos=60)


def es_link_directo(url: str) -> bool:
    return bool(url) and "sharepoint.com" not in url


def _descargar_y_extraer(url: str) -> str:
    """Solo para links directos (ya filtrados) -- siempre PDF en este sitio."""
    try:
        def _bajar():
            r = requests.get(url, headers=HEADERS, timeout=60)
            r.raise_for_status()
            return r.content
        contenido = _con_tope_de_tiempo(_bajar, segundos=45)
        if not contenido.startswith(b"%PDF"):
            return "[Error: el contenido descargado no es un PDF real]"
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
    except FuturoTimeoutError:
        return "[Error: la descarga superó los 45s de tope y se abandonó]"
    except Exception as e:
        return f"[Error descargando/extrayendo: {e}]"


def procesar_resolucion(fuente: str, categoria: str, item: dict, con) -> None:
    codigo = item.get("Código Carpeta")
    ya = con.execute("SELECT id FROM resoluciones WHERE codigo_carpeta = ?", (codigo,)).fetchone()
    if ya:
        return

    links = {
        "resolucion": item.get("Resolución", ""),
        "publicacion": item.get("Publicación", ""),
        "antecedentes": item.get("Antecedentes", ""),
        "video": item.get("Video", ""),
        "presentacion": item.get("Presentación", ""),
        "informe": item.get("Informe Regulatorio y Anexos", ""),
    }

    texto, tiene_texto = None, 0
    for candidato in (links["resolucion"], links["publicacion"], links["antecedentes"]):
        if es_link_directo(candidato):
            texto = _descargar_y_extraer(candidato)
            tiene_texto = 1
            break

    print(f"  [{fuente}] {categoria[:35]} | {codigo} {'(texto ok)' if tiene_texto else '(solo metadata)'}")

    con.execute(
        """INSERT INTO resoluciones
           (codigo_carpeta, fuente, categoria, nombre_resolucion, resolucion_decreto,
            fecha_promulgacion, fecha_publicacion, comentarios,
            link_resolucion, link_publicacion, link_antecedentes, link_video,
            link_presentacion, link_informe, texto, tiene_texto, fecha_ingesta)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (codigo, fuente, categoria, item.get("Nombre Resolución"), item.get("Resolución/Decreto"),
         item.get("Fecha de promulgación"), item.get("Fecha de Publicación ISO") or item.get("Fecha de publicación"),
         item.get("Comentarios"), links["resolucion"], links["publicacion"], links["antecedentes"],
         links["video"], links["presentacion"], links["informe"], texto, tiene_texto, date.today().isoformat()),
    )
    con.commit()


def procesar_normativa_vigente(item: dict, con) -> None:
    link = item.get("Link Carpeta", "")
    if not link:
        return
    ya = con.execute("SELECT id FROM normativa_vigente WHERE link = ?", (link,)).fetchone()
    if ya:
        return

    texto, tiene_texto = None, 0
    if es_link_directo(link):
        texto = _descargar_y_extraer(link)
        tiene_texto = 1

    print(f"  [normativa_vigente] {item.get('Sigla NT', '')} | {item.get('Normas Técnicas', '')[:40]} {'(texto ok)' if tiene_texto else '(solo metadata)'}")

    con.execute(
        """INSERT INTO normativa_vigente
           (codigo_carpeta_principal, sigla_nt, nombre, link, texto, tiene_texto, fecha_ingesta)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (item.get("Código Carpeta Principal"), item.get("Sigla NT"), item.get("Normas Técnicas"),
         link, texto, tiene_texto, date.today().isoformat()),
    )
    con.commit()


def main() -> None:
    import sqlite3
    import sys

    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")

    print("Descargando /normas y /servicios...")
    normas = obtener_normas()
    servicios = obtener_servicios()

    con = sqlite3.connect(DB_PATH)
    try:
        for categoria, items in normas.items():
            if categoria == CATEGORIA_NORMATIVA_VIGENTE:
                for item in items:
                    procesar_normativa_vigente(item, con)
                    time.sleep(0.2)
            else:
                for item in items:
                    procesar_resolucion("normas", categoria, item, con)
                    time.sleep(0.2)

        for categoria, items in servicios.items():
            for item in items:
                procesar_resolucion("servicios", categoria, item, con)
                time.sleep(0.2)
    finally:
        con.close()

    print("Listo.")


if __name__ == "__main__":
    main()
