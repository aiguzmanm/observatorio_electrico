#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
herramientas_ia_cne.py — dos herramientas para el modelo, sobre
datos/cne.sqlite (Normas Técnicas y Servicios Complementarios de la CNE --
ver adquisicion/scraper_cne_normativa.py). Módulo separado del Panel de
Expertos, base propia.

- buscar_resoluciones_cne: resoluciones/decretos que aprueban o modifican
  normas técnicas o servicios complementarios (el historial de cambios).
- buscar_normativa_vigente_cne: el texto consolidado VIGENTE de cada norma
  y sus anexos técnicos (la versión actual, no el historial).

Ningún archivo de este módulo tiene texto extraído (están en SharePoint
interno de la CNE, inaccesible sin navegador -- ver README/memoria del
proyecto). Todo es metadata + link para que la persona lo abra ella misma
-- por eso no hay una tercera herramienta de "texto completo" acá, los
campos ya vienen completos (no son extractos recortados).
"""
from __future__ import annotations

import sqlite3

import configuracion

DB_CNE = configuracion.RAIZ_DATOS / "cne.sqlite"

DECLARACIONES_CNE = [
    {
        "name": "buscar_resoluciones_cne",
        "description": (
            "Busca resoluciones/decretos de la CNE que aprueban o modifican Normas "
            "Técnicas del sector eléctrico o Servicios Complementarios -- el historial "
            "de cambios normativos, no el texto vigente. Para eso último usar "
            "buscar_normativa_vigente_cne. Cada resultado trae comentarios de qué cambió "
            "y hasta 3 links (resolución, publicación, antecedentes) -- casi todos son de "
            "SharePoint interno de la CNE, sin texto extraíble; se entrega el link igual "
            "para que la persona lo abra."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "texto": {"type": "string", "description": "Palabras libres a buscar en el nombre de la resolución y los comentarios."},
                "categoria": {"type": "string", "description": "Categoría, ej. '01 - NT de Seguridad y Calidad de Servicio', 'Informe SSCC'. Parcial, no hace falta el número exacto."},
                "fuente": {"type": "string", "enum": ["normas", "servicios"], "description": "'normas' = Normas Técnicas numeradas, 'servicios' = Servicios Complementarios."},
                "limite": {"type": "integer", "description": "Máximo de resultados, default 15."},
            },
        },
    },
    {
        "name": "buscar_normativa_vigente_cne",
        "description": (
            "Busca el texto VIGENTE actual de una Norma Técnica y sus anexos técnicos "
            "(no el historial de resoluciones que la modificaron -- para eso usar "
            "buscar_resoluciones_cne). Útil para '¿qué dice hoy la norma técnica de "
            "seguridad y calidad de servicio?', '¿qué anexos técnicos tiene la NTSyCS?'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "texto": {"type": "string", "description": "Palabras libres a buscar en el nombre del documento/anexo."},
                "sigla_nt": {"type": "string", "description": "Sigla de la norma, ej. 'NTSyCS', 'NTCO', 'NTSSCC'."},
                "limite": {"type": "integer", "description": "Máximo de resultados, default 15."},
            },
        },
    },
]


def _mejor_link(fila: dict) -> str:
    for campo in ("link_resolucion", "link_publicacion", "link_antecedentes"):
        if fila.get(campo):
            return fila[campo]
    return ""


def buscar_resoluciones_cne(texto="", categoria="", fuente="", limite=15) -> dict:
    con = sqlite3.connect(DB_CNE)
    try:
        condiciones, filtros = [], []
        if categoria:
            condiciones.append("r.categoria LIKE ?")
            filtros.append(f"%{categoria}%")
        if fuente:
            condiciones.append("r.fuente = ?")
            filtros.append(fuente)
        where_extra = (" AND " + " AND ".join(condiciones)) if condiciones else ""
        limite = max(1, min(int(limite or 15), 30))

        cols = ("r.codigo_carpeta, r.fuente, r.categoria, r.nombre_resolucion, r.resolucion_decreto, "
                "r.fecha_publicacion, r.comentarios, r.link_resolucion, r.link_publicacion, "
                "r.link_antecedentes, r.tiene_texto")
        if texto:
            sql = f"""
                SELECT {cols} FROM resoluciones_fts
                JOIN resoluciones r ON r.id = resoluciones_fts.rowid
                WHERE resoluciones_fts MATCH ? {where_extra}
                ORDER BY bm25(resoluciones_fts)
                LIMIT ?
            """
            filas = con.execute(sql, [texto] + filtros + [limite]).fetchall()
        else:
            sql = f"SELECT {cols} FROM resoluciones r WHERE 1=1 {where_extra} ORDER BY r.fecha_publicacion DESC LIMIT ?"
            filas = con.execute(sql, filtros + [limite]).fetchall()

        nombres = ["codigo_carpeta", "fuente", "categoria", "nombre_resolucion", "resolucion_decreto",
                   "fecha_publicacion", "comentarios", "link_resolucion", "link_publicacion",
                   "link_antecedentes", "tiene_texto"]
        resultados = []
        for f in filas:
            d = dict(zip(nombres, f))
            d["link"] = _mejor_link(d)
            resultados.append(d)
        return {"total": len(resultados), "resoluciones": resultados}
    except sqlite3.OperationalError as e:
        return {"error": f"Búsqueda inválida ({e})."}
    finally:
        con.close()


def buscar_normativa_vigente_cne(texto="", sigla_nt="", limite=15) -> dict:
    con = sqlite3.connect(DB_CNE)
    try:
        condiciones, filtros = [], []
        if sigla_nt:
            condiciones.append("n.sigla_nt LIKE ?")
            filtros.append(f"%{sigla_nt}%")
        where_extra = (" AND " + " AND ".join(condiciones)) if condiciones else ""
        limite = max(1, min(int(limite or 15), 30))

        cols = "n.sigla_nt, n.nombre, n.link, n.tiene_texto"
        if texto:
            sql = f"""
                SELECT {cols} FROM normativa_vigente_fts
                JOIN normativa_vigente n ON n.id = normativa_vigente_fts.rowid
                WHERE normativa_vigente_fts MATCH ? {where_extra}
                ORDER BY bm25(normativa_vigente_fts)
                LIMIT ?
            """
            filas = con.execute(sql, [texto] + filtros + [limite]).fetchall()
        else:
            sql = f"SELECT {cols} FROM normativa_vigente n WHERE 1=1 {where_extra} LIMIT ?"
            filas = con.execute(sql, filtros + [limite]).fetchall()

        nombres = ["sigla_nt", "nombre", "link", "tiene_texto"]
        return {"total": len(filas), "normas": [dict(zip(nombres, f)) for f in filas]}
    except sqlite3.OperationalError as e:
        return {"error": f"Búsqueda inválida ({e})."}
    finally:
        con.close()


FUNCIONES_CNE = {
    "buscar_resoluciones_cne": buscar_resoluciones_cne,
    "buscar_normativa_vigente_cne": buscar_normativa_vigente_cne,
}
