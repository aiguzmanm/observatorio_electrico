#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
herramientas_ia.py — tres herramientas para el modelo, sobre
datos/discrepancias.sqlite (esquema basado en la API real de
discrepancias.panelexpertos.cl -- ver adquisicion/scraper_panel_api.py).
Mismo principio que correspondencia_cen: el modelo decide CUÁNDO llamarlas,
el código que corre siempre es nuestro.

- buscar_discrepancias: filtra por metadata del CASO (empresa involucrada,
  materia/submateria de catálogo real, año, estado) -- para "¿qué
  discrepancias hay abiertas?", "¿qué discrepancias tiene Guacolda?". Una
  discrepancia puede tener más de una empresa por lado, por eso cada
  resultado trae la lista completa de empresas con su rol.
- buscar_en_documentos: full-text sobre el CONTENIDO de escritos/dictámenes
  -- para "¿en qué discrepancia se pidió tal cosa?" cuando no se sabe el
  nombre del caso de antemano.
- comunicados_de_caso: todos los comunicados oficiales (programa de trabajo,
  plazos, prórrogas, abstenciones) de UN caso puntual -- más confiable que
  buscar_en_documentos para esto, que depende de que el texto use la misma
  palabra que la pregunta (ej. "plazo" vs "fecha").
- obtener_documento_completo: texto ÍNTEGRO de un documento puntual (nunca
  un extracto cortado a N caracteres -- mismo aprendizaje que
  correspondencia_cen).

El link que se devuelve siempre es el de la ficha del caso (`link_pagina`),
no un link directo al archivo: los links directos a los adjuntos son URLs
firmadas de S3 que expiran, así que no se guardan -- la ficha del caso es
estable y desde ahí la persona puede abrir el documento igual.
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
            "eléctrico chileno, filtrando por los datos del CASO: empresa involucrada (como "
            "discrepante o como interesada -- un caso puede tener más de una empresa por lado), "
            "materia, submateria, año, estado. Devuelve una lista de casos con su nombre "
            "completo y sus empresas, no el contenido de los documentos -- para eso usar "
            "buscar_en_documentos u obtener_documento_completo."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "texto": {"type": "string", "description": "Palabras libres a buscar en el nombre/materia/submateria del caso."},
                "empresa": {"type": "string", "description": "Nombre (parcial) de una empresa involucrada, en cualquier rol, ej. 'Guacolda'."},
                "materia": {"type": "string", "description": "Materia del catálogo real del Panel, ej. 'Coordinador', 'Servicios complementarios'."},
                "submateria": {"type": "string", "description": "Submateria del catálogo real, ej. 'Acceso abierto (art. 79)'."},
                "anio": {"type": "integer", "description": "Año de presentación de la discrepancia."},
                "estado": {"type": "string", "enum": ["en_tramitacion", "terminada"], "description": "Si sigue abierta o ya se cerró."},
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
        "name": "comunicados_de_caso",
        "description": (
            "Devuelve TODOS los comunicados oficiales de un caso puntual (por su número y "
            "año): el que admite a tramitación con el programa de trabajo inicial (fecha de "
            "audiencia pública, plazo para observaciones de partes e interesados, orden y "
            "duración de exposiciones), y cualquier comunicado posterior (prórrogas de "
            "plazos, solicitudes de información a la CNE u otros organismos y sus "
            "resoluciones, abstención de integrantes del Panel, ampliación del plazo del "
            "dictamen). Usar para CUALQUIER pregunta sobre plazos, fechas, programa de "
            "trabajo, audiencia pública, prórrogas, abstenciones u otros aspectos "
            "administrativos/procedimentales de un caso -- son pocos documentos por caso y "
            "se devuelven completos, no hace falta buscar_en_documentos para esto (esa "
            "herramienta busca por coincidencia exacta de palabras y puede no encontrar "
            "nada aunque el dato esté, porque el texto real puede no usar la palabra exacta "
            "de la pregunta, ej. preguntar por 'plazos' cuando el documento dice 'fechas')."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "numero": {"type": "integer", "description": "Número de la discrepancia, ej. 23."},
                "anio": {"type": "integer", "description": "Año de la discrepancia, ej. 2026."},
            },
            "required": ["numero", "anio"],
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
                "documento_id": {"type": "integer", "description": "id del documento (viene en los resultados de las otras herramientas)."},
            },
            "required": ["documento_id"],
        },
    },
]


def _empresas_de(con, discrepancia_id: int) -> list[dict]:
    filas = con.execute(
        """
        SELECT e.nombre, de.rol FROM discrepancia_empresas de
        JOIN empresas e ON e.id = de.empresa_id
        WHERE de.discrepancia_id = ?
        """,
        (discrepancia_id,),
    ).fetchall()
    return [{"nombre": f[0], "rol": f[1]} for f in filas]


def buscar_discrepancias(texto="", empresa="", materia="", submateria="", anio=None, estado="", limite=15) -> dict:
    con = sqlite3.connect(DB)
    try:
        condiciones, filtros = [], []
        if empresa:
            condiciones.append(
                "d.id IN (SELECT de.discrepancia_id FROM discrepancia_empresas de "
                "JOIN empresas e ON e.id = de.empresa_id WHERE e.nombre LIKE ?)"
            )
            filtros.append(f"%{empresa}%")
        if materia:
            condiciones.append("d.materia LIKE ?")
            filtros.append(f"%{materia}%")
        if submateria:
            condiciones.append("d.submateria LIKE ?")
            filtros.append(f"%{submateria}%")
        if anio:
            condiciones.append("d.anio = ?")
            filtros.append(anio)
        if estado:
            condiciones.append("d.estado = ?")
            filtros.append(estado)
        where_extra = (" AND " + " AND ".join(condiciones)) if condiciones else ""
        limite = max(1, min(int(limite or 15), 30))

        cols = "d.id, d.numero, d.anio, d.nombre, d.materia, d.submateria, d.estado, d.motivo_cierre, d.link_pagina"
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

        nombres = ["id", "numero", "anio", "nombre", "materia", "submateria", "estado", "motivo_cierre", "link"]
        resultados = []
        for f in filas:
            d = dict(zip(nombres, f))
            d["empresas"] = _empresas_de(con, d["id"])
            del d["id"]
            resultados.append(d)
        return {"total": len(resultados), "discrepancias": resultados}
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
            SELECT doc.id, doc.titulo, doc.tipo, d.numero, d.anio, d.nombre,
                   d.link_pagina, substr(doc.texto, 1, 400) AS extracto
            FROM documentos_fts
            JOIN documentos doc ON doc.id = documentos_fts.rowid
            JOIN discrepancias d ON d.id = doc.discrepancia_id
            WHERE documentos_fts MATCH ?
            ORDER BY bm25(documentos_fts)
            LIMIT ?
            """,
            (texto, limite),
        ).fetchall()
        nombres = ["documento_id", "titulo", "tipo", "numero_discrepancia", "anio", "nombre_discrepancia", "link", "extracto"]
        return {"total": len(filas), "resultados": [dict(zip(nombres, f)) for f in filas]}
    except sqlite3.OperationalError as e:
        return {"error": f"Búsqueda inválida ({e})."}
    finally:
        con.close()


def comunicados_de_caso(numero: int, anio: int) -> dict:
    con = sqlite3.connect(DB)
    try:
        disc = con.execute(
            "SELECT id, nombre, link_pagina FROM discrepancias WHERE numero = ? AND anio = ?",
            (numero, anio),
        ).fetchone()
        if not disc:
            return {"error": f"No encontré ninguna discrepancia {numero}-{anio}."}
        discrepancia_id, nombre, link = disc
        filas = con.execute(
            """
            SELECT titulo, tipo, fecha, texto FROM documentos
            WHERE discrepancia_id = ? AND tipo IN ('Comunicado', 'Comunicado y pauta')
            ORDER BY fecha
            """,
            (discrepancia_id,),
        ).fetchall()
        nombres = ["titulo", "tipo", "fecha", "texto"]
        return {
            "numero_discrepancia": numero, "anio": anio, "nombre_discrepancia": nombre, "link": link,
            "total": len(filas), "comunicados": [dict(zip(nombres, f)) for f in filas],
        }
    finally:
        con.close()


def obtener_documento_completo(documento_id: int) -> dict:
    con = sqlite3.connect(DB)
    try:
        fila = con.execute(
            """
            SELECT doc.titulo, doc.tipo, doc.texto, d.numero, d.anio, d.nombre, d.link_pagina
            FROM documentos doc JOIN discrepancias d ON d.id = doc.discrepancia_id
            WHERE doc.id = ?
            """,
            (documento_id,),
        ).fetchone()
        if not fila:
            return {"error": f"No encontré ningún documento con documento_id '{documento_id}'."}
        nombres = ["titulo", "tipo", "texto", "numero_discrepancia", "anio", "nombre_discrepancia", "link"]
        return dict(zip(nombres, fila))
    finally:
        con.close()


FUNCIONES = {
    "buscar_discrepancias": buscar_discrepancias,
    "buscar_en_documentos": buscar_en_documentos,
    "comunicados_de_caso": comunicados_de_caso,
    "obtener_documento_completo": obtener_documento_completo,
}
