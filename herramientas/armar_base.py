#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
armar_base.py -- crea datos/discrepancias.sqlite con el esquema basado en la
API real de discrepancias.panelexpertos.cl (ver adquisicion/scraper_panel_api.py).

Reemplaza el esquema anterior (basado en scrapear carpetas de OneDrive): ahora
la fuente es la API propia del Panel, que entrega metadata estructurada real
(estado, materia/submateria, empresas por caso con su rol) en vez de tener que
adivinarla con regex sobre nombres de carpeta.

No guarda PDF/PPT/DOCX -- solo texto extraído. Mismo principio que siempre.
"""
from __future__ import annotations

import configuracion

DB_PATH = configuracion.RAIZ_DATOS / "discrepancias.sqlite"

ESQUEMA = """
CREATE TABLE IF NOT EXISTS discrepancias (
    id                  INTEGER PRIMARY KEY,
    api_id              INTEGER UNIQUE,   -- id en discrepancias.panelexpertos.cl
    numero              INTEGER,          -- ej 23 (dentro del año)
    anio                INTEGER,
    nombre              TEXT,             -- "cover" tal cual lo escribe el Panel
    materia             TEXT,             -- legalMatters.name (catálogo real)
    submateria          TEXT,             -- legalSubMatters.name (catálogo real)
    estado              TEXT,             -- 'en_tramitacion' | 'terminada'
    motivo_cierre       TEXT,             -- 'dictamen' | 'desistimiento' | NULL (abierta o no detectado)
    fecha_presentacion  TEXT,
    fecha_termino       TEXT,             -- endedAt, NULL si sigue abierta
    link_pagina         TEXT,             -- link estable a la ficha del caso (no expira)
    fecha_ingesta       TEXT
);
CREATE INDEX IF NOT EXISTS idx_discrepancias_anio ON discrepancias(anio);
CREATE INDEX IF NOT EXISTS idx_discrepancias_estado ON discrepancias(estado);

CREATE TABLE IF NOT EXISTS empresas (
    id       INTEGER PRIMARY KEY,
    api_id   INTEGER UNIQUE,   -- entities.id de la API
    nombre   TEXT,
    rut      TEXT
);
CREATE INDEX IF NOT EXISTS idx_empresas_nombre ON empresas(nombre);

-- Muchos-a-muchos: una discrepancia puede tener más de una empresa por
-- lado (ej. varias interesadas), y una empresa aparece en muchos casos.
CREATE TABLE IF NOT EXISTS discrepancia_empresas (
    discrepancia_id INTEGER REFERENCES discrepancias(id),
    empresa_id      INTEGER REFERENCES empresas(id),
    rol             TEXT,   -- 'discrepante' | 'interesado'
    PRIMARY KEY (discrepancia_id, empresa_id, rol)
);
CREATE INDEX IF NOT EXISTS idx_disc_emp_empresa ON discrepancia_empresas(empresa_id);
CREATE INDEX IF NOT EXISTS idx_disc_emp_disc ON discrepancia_empresas(discrepancia_id);

CREATE TABLE IF NOT EXISTS documentos (
    id                INTEGER PRIMARY KEY,
    discrepancia_id   INTEGER REFERENCES discrepancias(id),
    api_id            INTEGER UNIQUE,   -- documents.id de la API
    attachment_api_id INTEGER,          -- attachments.id del adjunto "principal" (para regenerar el link al vuelo)
    titulo            TEXT,
    tipo              TEXT,             -- documentTypes.name real (catálogo de 13 tipos)
    fecha             TEXT,             -- publishedAt/confirmedAt
    texto             TEXT,
    fecha_ingesta     TEXT
);
CREATE INDEX IF NOT EXISTS idx_documentos_discrepancia ON documentos(discrepancia_id);
CREATE INDEX IF NOT EXISTS idx_documentos_tipo ON documentos(tipo);

CREATE VIRTUAL TABLE IF NOT EXISTS documentos_fts USING fts5(
    titulo, texto,
    content='documentos', content_rowid='id'
);
CREATE TRIGGER IF NOT EXISTS documentos_ai AFTER INSERT ON documentos BEGIN
    INSERT INTO documentos_fts(rowid, titulo, texto) VALUES (new.id, new.titulo, new.texto);
END;
CREATE TRIGGER IF NOT EXISTS documentos_ad AFTER DELETE ON documentos BEGIN
    INSERT INTO documentos_fts(documentos_fts, rowid, titulo, texto) VALUES ('delete', old.id, old.titulo, old.texto);
END;
CREATE TRIGGER IF NOT EXISTS documentos_au AFTER UPDATE ON documentos BEGIN
    INSERT INTO documentos_fts(documentos_fts, rowid, titulo, texto) VALUES ('delete', old.id, old.titulo, old.texto);
    INSERT INTO documentos_fts(rowid, titulo, texto) VALUES (new.id, new.titulo, new.texto);
END;

CREATE VIRTUAL TABLE IF NOT EXISTS discrepancias_fts USING fts5(
    nombre, materia, submateria,
    content='discrepancias', content_rowid='id'
);
CREATE TRIGGER IF NOT EXISTS discrepancias_ai AFTER INSERT ON discrepancias BEGIN
    INSERT INTO discrepancias_fts(rowid, nombre, materia, submateria)
    VALUES (new.id, new.nombre, new.materia, new.submateria);
END;
CREATE TRIGGER IF NOT EXISTS discrepancias_ad AFTER DELETE ON discrepancias BEGIN
    INSERT INTO discrepancias_fts(discrepancias_fts, rowid, nombre, materia, submateria)
    VALUES ('delete', old.id, old.nombre, old.materia, old.submateria);
END;
CREATE TRIGGER IF NOT EXISTS discrepancias_au AFTER UPDATE ON discrepancias BEGIN
    INSERT INTO discrepancias_fts(discrepancias_fts, rowid, nombre, materia, submateria)
    VALUES ('delete', old.id, old.nombre, old.materia, old.submateria);
    INSERT INTO discrepancias_fts(rowid, nombre, materia, submateria)
    VALUES (new.id, new.nombre, new.materia, new.submateria);
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
