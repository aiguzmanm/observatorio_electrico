#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scraper_panel_api.py -- adquisidor de producción para el Panel de Expertos,
basado en la API propia de discrepancias.panelexpertos.cl (no en el sitio
WordPress viejo con OneDrive -- ver adquisicion/scraper_panel.py, que queda
obsoleto y no se usa más).

Mecanismo (investigado con Playwright, no hace falta en producción -- son
llamadas REST simples con `requests`):
- GET /api/v1/home  -> TODO el universo de una vez: las discrepancias, los
  catálogos reales (materia, submateria, tipo de documento) y las empresas
  (entities/legalEntities). Sin login, sin nonce.
- GET /api/v1/discrepancies/<id>  -> detalle de un caso: sus documentos,
  las partes interesadas (con su empresa), y los adjuntos con un link
  firmado a S3 (PDF y DOCX) -- ese link expira, así que nunca se guarda en
  la base, solo se usa al vuelo para bajar y extraer texto.

Reglas heredadas del resto del proyecto: no se guardan PDF/DOCX, solo el
texto extraído. Y de cada documento, solo se procesa el adjunto "principal"
(el archivo en sí) -- los "secondary" son casi siempre personerías/poderes,
sin contenido relevante para búsqueda.
"""
from __future__ import annotations

import tempfile
import time
from datetime import date
from pathlib import Path
from typing import Optional

import requests

import configuracion

BASE = "https://discrepancias.panelexpertos.cl"
URL_HOME = f"{BASE}/api/v1/home"
URL_CASO = f"{BASE}/api/v1/discrepancies/{{id}}"

DB_PATH = configuracion.RAIZ_DATOS / "discrepancias.sqlite"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
}

# code de documentType -> rol que le corresponde a la empresa que lo presentó
CODIGO_A_ROL = {
    "presentations": "discrepante",           # "Presentación de discrepancia"
    "interested_presentation": "interesado",  # "Presentación de interesado"
}
CODIGO_DESISTIMIENTO = "withdrawal"
CODIGO_DICTAMEN = "opinions"


def obtener_home() -> dict:
    resp = requests.get(URL_HOME, headers=HEADERS, timeout=60)
    resp.raise_for_status()
    return resp.json()["objects"]


def obtener_caso(api_id: int) -> dict:
    resp = requests.get(URL_CASO.format(id=api_id), headers=HEADERS, timeout=60)
    resp.raise_for_status()
    return resp.json()["objects"]


def nombre_entidad(entity: dict) -> str:
    nombre = (entity.get("names") or "").strip()
    apellido = (entity.get("surnames") or "").strip()
    return f"{nombre} {apellido}".strip()


def extraer_texto(contenido: bytes, content_type: str) -> str:
    if content_type == "pdf":
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
    elif content_type == "docx":
        import docx2txt

        fd, ruta = tempfile.mkstemp(suffix=".docx")
        try:
            with open(fd, "wb") as f:
                f.write(contenido)
            return (docx2txt.process(ruta) or "").strip()
        finally:
            Path(ruta).unlink(missing_ok=True)
    return f"[Tipo de archivo no soportado para extracción de texto: {content_type}]"


def _asegurar_empresa(con, api_id: int, nombre: str, rut: str) -> int:
    fila = con.execute("SELECT id FROM empresas WHERE api_id = ?", (api_id,)).fetchone()
    if fila:
        return fila[0]
    con.execute(
        "INSERT INTO empresas (api_id, nombre, rut) VALUES (?, ?, ?)",
        (api_id, nombre, rut),
    )
    return con.execute("SELECT id FROM empresas WHERE api_id = ?", (api_id,)).fetchone()[0]


def procesar_caso(disc: dict, catalogos: dict, con, max_reintentos: int = 2) -> None:
    api_id = disc["id"]
    ya = con.execute("SELECT id FROM discrepancias WHERE api_id = ?", (api_id,)).fetchone()
    if ya:
        discrepancia_id = ya[0]
    else:
        sub = catalogos["legalSubMatters"].get(str(disc.get("legalSubMatterId")))
        materia = catalogos["legalMatters"].get(str(sub["legalMatterId"])) if sub else None
        estado = "terminada" if disc.get("endedAt") else "en_tramitacion"
        anio = int(str(disc.get("presentationDate") or "")[:4]) if disc.get("presentationDate") else None
        con.execute(
            """INSERT INTO discrepancias
               (api_id, numero, anio, nombre, materia, submateria, estado,
                motivo_cierre, fecha_presentacion, fecha_termino, link_pagina, fecha_ingesta)
               VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?)""",
            (api_id, disc.get("number"), anio, disc.get("cover"),
             materia["name"] if materia else None, sub["name"] if sub else None, estado,
             disc.get("presentationDate"), disc.get("endedAt"),
             f"{BASE}/discrepancies/{api_id}/entities", date.today().isoformat()),
        )
        con.commit()
        discrepancia_id = con.execute("SELECT id FROM discrepancias WHERE api_id = ?", (api_id,)).fetchone()[0]

    print(f"  {disc.get('number')}-{disc.get('cover', '')[:60]}")

    for intento in range(max_reintentos + 1):
        try:
            detalle = obtener_caso(api_id)
            break
        except Exception as e:
            if intento == max_reintentos:
                print(f"    [error] no se pudo obtener el detalle del caso {api_id}: {e}")
                return
            time.sleep(2)

    document_types = detalle.get("documentTypes", {})
    documentos = detalle.get("documents", {})
    interested_parties = detalle.get("interestedParties", {})
    entities = detalle.get("entities", {})
    attachment_parts = detalle.get("attachmentParts", {})
    attachments = detalle.get("attachments", {})

    motivo_cierre = None
    tipos_vistos = set()

    for doc_api_id_str, doc in documentos.items():
        doc_api_id = doc["id"]
        tipo_info = document_types.get(str(doc.get("documentTypeId")))
        tipo_codigo = tipo_info["code"] if tipo_info else None
        tipo_nombre = tipo_info["name"] if tipo_info else None
        if tipo_codigo:
            tipos_vistos.add(tipo_codigo)

        # rol de la empresa que presentó este documento (si corresponde)
        rol = CODIGO_A_ROL.get(tipo_codigo)
        if rol:
            ip = interested_parties.get(str(doc.get("interestedPartyId")))
            legal_entity_id = ip.get("legalEntityId") if ip else None
            if legal_entity_id:
                # legalEntities no viene en el detalle del caso, solo en /home
                legal_entity = catalogos["legalEntities"].get(str(legal_entity_id))
                entity_id = legal_entity.get("entityId") if legal_entity else None
                # el detalle del caso puede no traer la entidad si no participó
                # directamente (ej. representantes) -- se cae al catálogo de /home
                entity = entities.get(str(entity_id)) or catalogos["entities"].get(str(entity_id)) if entity_id else None
                if entity:
                    empresa_id = _asegurar_empresa(con, entity["id"], nombre_entidad(entity), entity.get("rut", ""))
                    con.execute(
                        "INSERT OR IGNORE INTO discrepancia_empresas (discrepancia_id, empresa_id, rol) VALUES (?, ?, ?)",
                        (discrepancia_id, empresa_id, rol),
                    )

        ya_doc = con.execute("SELECT id FROM documentos WHERE api_id = ?", (doc_api_id,)).fetchone()
        if ya_doc:
            continue

        # solo el adjunto "principal" -- los "secondary" son casi siempre personerías/poderes
        adjunto_principal = None
        for part_id, part in attachment_parts.items():
            if part.get("documentId") == doc_api_id and part.get("type") == "principal":
                for att in attachments.values():
                    if att.get("attachmentPartId") == part["id"]:
                        adjunto_principal = att
                        break
                break

        texto = ""
        attachment_api_id = None
        if adjunto_principal:
            attachment_api_id = adjunto_principal["id"]
            try:
                resp = requests.get(adjunto_principal["url"], timeout=90)
                resp.raise_for_status()
                texto = extraer_texto(resp.content, adjunto_principal.get("contentType", ""))
            except Exception as e:
                texto = f"[Error descargando/extrayendo: {e}]"
        else:
            texto = "[Sin adjunto principal]"

        con.execute(
            """INSERT INTO documentos
               (discrepancia_id, api_id, attachment_api_id, titulo, tipo, fecha, texto, fecha_ingesta)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (discrepancia_id, doc_api_id, attachment_api_id, doc.get("title"), tipo_nombre,
             doc.get("publishedAt") or doc.get("confirmedAt"), texto, date.today().isoformat()),
        )
        con.commit()

    if CODIGO_DESISTIMIENTO in tipos_vistos:
        motivo_cierre = "desistimiento"
    elif CODIGO_DICTAMEN in tipos_vistos:
        motivo_cierre = "dictamen"
    if motivo_cierre:
        con.execute("UPDATE discrepancias SET motivo_cierre = ? WHERE id = ?", (motivo_cierre, discrepancia_id))
        con.commit()


def main() -> None:
    import sqlite3
    import sys

    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")

    limite = int(sys.argv[1]) if len(sys.argv) > 1 else None

    print("Descargando /api/v1/home (universo completo)...")
    home = obtener_home()
    catalogos = {
        "legalMatters": home["legalMatters"],
        "legalSubMatters": home["legalSubMatters"],
        "legalEntities": home["legalEntities"],
        "entities": home["entities"],
    }
    discrepancias = list(home["discrepancies"].values())
    discrepancias.sort(key=lambda d: d.get("presentationDate") or "", reverse=True)
    print(f"{len(discrepancias)} discrepancias en total en el sitio.")

    if limite:
        discrepancias = discrepancias[:limite]
        print(f"Procesando las {limite} más recientes (prueba).")

    con = sqlite3.connect(DB_PATH)
    try:
        for disc in discrepancias:
            procesar_caso(disc, catalogos, con)
            time.sleep(0.3)
    finally:
        con.close()


if __name__ == "__main__":
    main()
