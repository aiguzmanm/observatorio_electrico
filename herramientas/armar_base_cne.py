#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
armar_base_cne.py -- crea datos/cne.sqlite, un módulo NUEVO y SEPARADO del
de discrepancias (base propia, no comparte tablas): normativa técnica y
servicios complementarios de la CNE, vía apinormastecnicas.cne.cl.

Dos formas de contenido en esa API, con campos distintos:
- "resoluciones": las 10 categorías de Normas Técnicas + "Informe SSCC" (de
  /normas) + Servicios Complementarios (de /servicios) -- todas comparten
  el mismo esquema de campos (resolución, fechas, comentarios, links).
- "normativa_vigente": el texto consolidado vigente de cada norma (de
  /normas, categoría "Normativa vigente") -- esquema más simple, sin fechas
  ni número de resolución.

No todos los archivos son descargables directo: algunos están en SharePoint
interno de la CNE y requieren navegador (costoso, ver medición real de
~1.2GB de RAM por descarga -- no viable en esta VPS). Para esos, tiene_texto
queda en 0 y solo se guarda la metadata + el link para que la persona lo
abra ella misma.
"""
from __future__ import annotations

import configuracion

DB_PATH = configuracion.RAIZ_DATOS / "cne.sqlite"

ESQUEMA = """
CREATE TABLE IF NOT EXISTS resoluciones (
    id                  INTEGER PRIMARY KEY,
    codigo_carpeta      TEXT UNIQUE,
    fuente              TEXT,     -- 'normas' | 'servicios'
    categoria           TEXT,     -- ej "01 - NT de Seguridad y Calidad de Servicio", "Informe SSCC"
    nombre_resolucion   TEXT,
    resolucion_decreto  TEXT,     -- ej "RE 45 Exenta"
    fecha_promulgacion  TEXT,
    fecha_publicacion   TEXT,     -- ISO (YYYY-MM-DD)
    comentarios         TEXT,
    link_resolucion     TEXT,
    link_publicacion    TEXT,
    link_antecedentes   TEXT,
    link_video          TEXT,
    link_presentacion   TEXT,
    link_informe        TEXT,
    texto               TEXT,     -- NULL si el único link disponible es SharePoint
    tiene_texto         INTEGER,  -- 0/1, para filtrar rápido
    fecha_ingesta       TEXT
);
CREATE INDEX IF NOT EXISTS idx_resoluciones_categoria ON resoluciones(categoria);
CREATE INDEX IF NOT EXISTS idx_resoluciones_fuente ON resoluciones(fuente);

CREATE TABLE IF NOT EXISTS normativa_vigente (
    id                       INTEGER PRIMARY KEY,
    codigo_carpeta_principal TEXT,
    sigla_nt                 TEXT,
    nombre                   TEXT,   -- título del documento/anexo (campo "Normas Técnicas" de la API)
    link                     TEXT UNIQUE,
    texto                    TEXT,
    tiene_texto              INTEGER,
    fecha_ingesta            TEXT
);
CREATE INDEX IF NOT EXISTS idx_normativa_vigente_sigla ON normativa_vigente(sigla_nt);

CREATE VIRTUAL TABLE IF NOT EXISTS resoluciones_fts USING fts5(
    nombre_resolucion, comentarios, texto,
    content='resoluciones', content_rowid='id'
);
CREATE TRIGGER IF NOT EXISTS resoluciones_ai AFTER INSERT ON resoluciones BEGIN
    INSERT INTO resoluciones_fts(rowid, nombre_resolucion, comentarios, texto)
    VALUES (new.id, new.nombre_resolucion, new.comentarios, new.texto);
END;
CREATE TRIGGER IF NOT EXISTS resoluciones_ad AFTER DELETE ON resoluciones BEGIN
    INSERT INTO resoluciones_fts(resoluciones_fts, rowid, nombre_resolucion, comentarios, texto)
    VALUES ('delete', old.id, old.nombre_resolucion, old.comentarios, old.texto);
END;
CREATE TRIGGER IF NOT EXISTS resoluciones_au AFTER UPDATE ON resoluciones BEGIN
    INSERT INTO resoluciones_fts(resoluciones_fts, rowid, nombre_resolucion, comentarios, texto)
    VALUES ('delete', old.id, old.nombre_resolucion, old.comentarios, old.texto);
    INSERT INTO resoluciones_fts(rowid, nombre_resolucion, comentarios, texto)
    VALUES (new.id, new.nombre_resolucion, new.comentarios, new.texto);
END;

CREATE VIRTUAL TABLE IF NOT EXISTS normativa_vigente_fts USING fts5(
    nombre, texto,
    content='normativa_vigente', content_rowid='id'
);
CREATE TRIGGER IF NOT EXISTS normativa_vigente_ai AFTER INSERT ON normativa_vigente BEGIN
    INSERT INTO normativa_vigente_fts(rowid, nombre, texto) VALUES (new.id, new.nombre, new.texto);
END;
CREATE TRIGGER IF NOT EXISTS normativa_vigente_ad AFTER DELETE ON normativa_vigente BEGIN
    INSERT INTO normativa_vigente_fts(normativa_vigente_fts, rowid, nombre, texto) VALUES ('delete', old.id, old.nombre, old.texto);
END;
CREATE TRIGGER IF NOT EXISTS normativa_vigente_au AFTER UPDATE ON normativa_vigente BEGIN
    INSERT INTO normativa_vigente_fts(normativa_vigente_fts, rowid, nombre, texto) VALUES ('delete', old.id, old.nombre, old.texto);
    INSERT INTO normativa_vigente_fts(rowid, nombre, texto) VALUES (new.id, new.nombre, new.texto);
END;
"""


def armar() -> None:
    import sqlite3

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    try:
        con.executescript(ESQUEMA)
        con.commit()
    finally:
        con.close()
    print(f"Base lista: {DB_PATH}")


if __name__ == "__main__":
    armar()
