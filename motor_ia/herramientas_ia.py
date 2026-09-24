#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
herramientas_ia.py — tres herramientas para el modelo, sobre
datos/discrepancias.sqlite. Mismo principio que correspondencia_cen: el
modelo decide CUÁNDO llamarlas, el código que corre siempre es nuestro.

- buscar_discrepancias: filtra por metadata del CASO (requirente,
  contraparte, materia, año, estado) -- para "¿qué discrepancias hay
  abiertas?", "¿qué discrepancias tiene Guacolda?".
- buscar_en_documentos: full-text sobre el CONTENIDO de escritos/dictámenes
  -- para "¿en qué discrepancia se pidió tal cosa?" cuando no se sabe el
  nombre del caso de antemano.
- obtener_documento_completo: texto ÍNTEGRO de un documento puntual (nunca
  un extracto cortado a N caracteres -- mismo aprendizaje que
  correspondencia_cen).
"""
from __future__ import annotations

import sqlite3

import configuracion

DB = configuracion.RAIZ_DATOS / "discrepancias.sqlite"

DECLARACIONES = [
    {
        "name": "buscar_discrepancias",
        "description": (
            "Busca discrepancias (casos) presentadas ante el Panel de Expertos del sector "
            "eléctrico chileno, filtrando por los datos del CASO: quién la presentó, contra "
            "quién, materia, año, estado. Devuelve una lista de casos con su nombre completo, "
            "no el contenido de los documentos -- para eso usar buscar_en_documentos u "
            "obtener_documento_completo."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "texto": {"type": "string", "description": "Palabras libres a buscar en el nombre/requirente/contraparte/materia del caso."},
                "requirente": {"type": "string", "description": "Quién presenta la discrepancia, ej. 'Guacolda'."},
                "contraparte": {"type": "string", "description": "Contra quién se presenta, ej. 'Coordinador', 'CNE'."},
                "materia": {"type": "string", "description": "Tema de la discrepancia."},
                "anio": {"type": "integer", "description": "Año de la discrepancia."},
                "estado": {"type": "string", "enum": ["en_curso", "tramitada"], "description": "Si sigue abierta o ya se cerró."},
            },
        },
    },
    {
        "name": "buscar_en_documentos",
        "description": (
            "Busca texto libre dentro del CONTENIDO de los escritos, actas y dictámenes "
            "(no solo el nombre del caso). Usar cuando la pregunta es sobre algo que se dijo "
            "o pidió dentro de una discrepancia, y no se sabe de antemano cuál es el caso."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "texto": {"type": "string", "description": "Palabras clave a buscar en el texto de los documentos."},
                "limite": {"type": "integer", "description": "Máximo de resultados, default 8."},
            },
            "required": ["texto"],
        },
    },
    {
        "name": "obtener_documento_completo",
        "description": (
            "Devuelve el texto COMPLETO de un documento puntual (un escrito, acta o dictamen), "
            "sin resumir ni recortar. Usar después de buscar_discrepancias o buscar_en_documentos, "
            "cuando haga falta el detalle real de uno o pocos documentos."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "file_id": {"type": "string", "description": "id del documento (viene en los resultados de las otras herramientas)."},
            },
            "required": ["file_id"],
        },
    },
]


def buscar_discrepancias(texto="", requirente="", contraparte="", materia="", anio=None, estado="", limite=15) -> dict:
    con = sqlite3.connect(DB)
    try:
        condiciones, filtros = [], []
        if requirente:
            condiciones.append("d.requirente LIKE ?")
            filtros.append(f"%{requirente}%")
        if contraparte:
            condiciones.append("d.contraparte LIKE ?")
            filtros.append(f"%{contraparte}%")
        if materia:
            condiciones.append("d.materia LIKE ?")
            filtros.append(f"%{materia}%")
        if anio:
            condiciones.append("d.anio = ?")
            filtros.append(anio)
        if estado:
            condiciones.append("d.estado = ?")
            filtros.append(estado)
        where_extra = (" AND " + " AND ".join(condiciones)) if condiciones else ""
        limite = max(1, min(int(limite or 15), 30))

        cols = "d.numero, d.anio, d.requirente, d.contraparte, d.materia, d.estado, d.nombre_completo, d.folder_id"
        if texto:
            sql = f"""
                SELECT {cols} FROM discrepancias_fts
                JOIN discrepancias d ON d.id = discrepancias_fts.rowid
                WHERE discrepancias_fts MATCH ? {where_extra}
                ORDER BY bm25(discrepancias_fts)
                LIMIT ?
            """
            filas = con.execute(sql, [texto] + filtros + [limite]).fetchall()
        else:
            sql = f"SELECT {cols} FROM discrepancias d WHERE 1=1 {where_extra} ORDER BY d.anio DESC, d.numero DESC LIMIT ?"
            filas = con.execute(sql, filtros + [limite]).fetchall()

        nombres = ["numero", "anio", "requirente", "contraparte", "materia", "estado", "nombre_completo", "folder_id"]
        return {"total": len(filas), "discrepancias": [dict(zip(nombres, f)) for f in filas]}
    except sqlite3.OperationalError as e:
        return {"error": f"Búsqueda inválida ({e})."}
    finally:
        con.close()


def buscar_en_documentos(texto: str, limite: int = 8) -> dict:
    con = sqlite3.connect(DB)
    try:
        limite = max(1, min(int(limite or 8), 20))
        filas = con.execute(
            """
            SELECT doc.file_id, doc.nombre, doc.categoria, d.numero, d.requirente, d.contraparte,
                   substr(doc.texto, 1, 400) AS extracto
            FROM documentos_fts
            JOIN documentos doc ON doc.id = documentos_fts.rowid
            JOIN discrepancias d ON d.id = doc.discrepancia_id
            WHERE documentos_fts MATCH ?
            ORDER BY bm25(documentos_fts)
            LIMIT ?
            """,
            (texto, limite),
        ).fetchall()
        nombres = ["file_id", "nombre_documento", "categoria", "numero_discrepancia", "requirente", "contraparte", "extracto"]
        return {"total": len(filas), "resultados": [dict(zip(nombres, f)) for f in filas]}
    except sqlite3.OperationalError as e:
        return {"error": f"Búsqueda inválida ({e})."}
    finally:
        con.close()


def obtener_documento_completo(file_id: str) -> dict:
    con = sqlite3.connect(DB)
    try:
        fila = con.execute(
            """
            SELECT doc.nombre, doc.categoria, doc.texto, doc.link_descarga, d.numero, d.requirente, d.contraparte
            FROM documentos doc JOIN discrepancias d ON d.id = doc.discrepancia_id
            WHERE doc.file_id = ?
            """,
            (file_id,),
        ).fetchone()
        if not fila:
            return {"error": f"No encontré ningún documento con file_id '{file_id}'."}
        nombres = ["nombre_documento", "categoria", "texto", "link_descarga", "numero_discrepancia", "requirente", "contraparte"]
        return dict(zip(nombres, fila))
    finally:
        con.close()


FUNCIONES = {
    "buscar_discrepancias": buscar_discrepancias,
    "buscar_en_documentos": buscar_en_documentos,
    "obtener_documento_completo": obtener_documento_completo,
}
