#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
armar_base.py — crea datos/discrepancias.sqlite (si no existe) con el
esquema de dos tablas: discrepancias (el caso) y documentos (cada escrito/
acta/dictamen, texto completo, ligado por FK). FTS5 sobre documentos.

No guarda PDF/PPT -- solo texto extraído. Mismo patrón que
correspondencia_cen/herramientas/cargar_sqlite.py.
"""
from __future__ import annotations

import configuracion

DB_PATH = configuracion.RAIZ_DATOS / "discrepancias.sqlite"

ESQUEMA = """
CREATE TABLE IF NOT EXISTS discrepancias (
    id              INTEGER PRIMARY KEY,
    folder_id       TEXT UNIQUE,   -- id de OneDrive de la carpeta del caso
    numero          TEXT,          -- ej "04-2026"
    anio            INTEGER,
    nombre_completo TEXT,          -- nombre de la carpeta tal cual aparece en el sitio
    requirente      TEXT,          -- mejor esfuerzo, parseado del nombre; puede quedar NULL
    contraparte     TEXT,
    materia         TEXT,
    estado          TEXT,          -- 'en_curso' | 'tramitada'
    fecha_ingesta   TEXT
);
CREATE INDEX IF NOT EXISTS idx_discrepancias_anio ON discrepancias(anio);
CREATE INDEX IF NOT EXISTS idx_discrepancias_estado ON discrepancias(estado);

CREATE TABLE IF NOT EXISTS documentos (
    id                INTEGER PRIMARY KEY,
    discrepancia_id   INTEGER REFERENCES discrepancias(id),
    file_id           TEXT UNIQUE,  -- id de OneDrive del archivo
    nombre            TEXT,
    categoria         TEXT,         -- 'escrito' | 'acta' | 'actuacion' | 'dictamen' | 'otro'
    texto             TEXT,
    link_descarga     TEXT,         -- URL reconstruible (admin-ajax.php?action=shareonedrive-download...)
    fecha_ingesta     TEXT
);
CREATE INDEX IF NOT EXISTS idx_documentos_discrepancia ON documentos(discrepancia_id);
CREATE INDEX IF NOT EXISTS idx_documentos_categoria ON documentos(categoria);

CREATE VIRTUAL TABLE IF NOT EXISTS documentos_fts USING fts5(
    nombre, texto,
    content='documentos', content_rowid='id'
);

CREATE TRIGGER IF NOT EXISTS documentos_ai AFTER INSERT ON documentos BEGIN
    INSERT INTO documentos_fts(rowid, nombre, texto) VALUES (new.id, new.nombre, new.texto);
END;
CREATE TRIGGER IF NOT EXISTS documentos_ad AFTER DELETE ON documentos BEGIN
    INSERT INTO documentos_fts(documentos_fts, rowid, nombre, texto) VALUES ('delete', old.id, old.nombre, old.texto);
END;
CREATE TRIGGER IF NOT EXISTS documentos_au AFTER UPDATE ON documentos BEGIN
    INSERT INTO documentos_fts(documentos_fts, rowid, nombre, texto) VALUES ('delete', old.id, old.nombre, old.texto);
    INSERT INTO documentos_fts(rowid, nombre, texto) VALUES (new.id, new.nombre, new.texto);
END;

-- FTS liviano también sobre discrepancias, para encontrar casos por nombre
-- aunque el documento en sí todavía no se haya descargado.
CREATE VIRTUAL TABLE IF NOT EXISTS discrepancias_fts USING fts5(
    nombre_completo, requirente, contraparte, materia,
    content='discrepancias', content_rowid='id'
);
CREATE TRIGGER IF NOT EXISTS discrepancias_ai AFTER INSERT ON discrepancias BEGIN
    INSERT INTO discrepancias_fts(rowid, nombre_completo, requirente, contraparte, materia)
    VALUES (new.id, new.nombre_completo, new.requirente, new.contraparte, new.materia);
END;
CREATE TRIGGER IF NOT EXISTS discrepancias_ad AFTER DELETE ON discrepancias BEGIN
    INSERT INTO discrepancias_fts(discrepancias_fts, rowid, nombre_completo, requirente, contraparte, materia)
    VALUES ('delete', old.id, old.nombre_completo, old.requirente, old.contraparte, old.materia);
END;
CREATE TRIGGER IF NOT EXISTS discrepancias_au AFTER UPDATE ON discrepancias BEGIN
    INSERT INTO discrepancias_fts(discrepancias_fts, rowid, nombre_completo, requirente, contraparte, materia)
    VALUES ('delete', old.id, old.nombre_completo, old.requirente, old.contraparte, old.materia);
    INSERT INTO discrepancias_fts(rowid, nombre_completo, requirente, contraparte, materia)
    VALUES (new.id, new.nombre_completo, new.requirente, new.contraparte, new.materia);
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
