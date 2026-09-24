#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
memoria_chat.py — memoria conversacional breve, independiente por (chat,
persona). Simplificada desde chat_bot/mensajeria/memoria_chat.py: mismo
mecanismo (SQLite, ventana de tiempo, últimos turnos), sin la heurística de
detectar preguntas referenciales -- acá se reinyectan siempre los últimos
turnos dentro de la ventana, más simple a costa de algo más de tokens.
"""
from __future__ import annotations

import sqlite3
import time

import configuracion

RUTA_DB = configuracion.RAIZ_DATOS / "memoria_chat.sqlite"
VENTANA_SEGUNDOS = 30 * 60
MAX_TURNOS = 5
MAX_CARACTERES_GUARDADOS = 1500
MAX_CARACTERES_REINYECTADOS = 500


def _conexion() -> sqlite3.Connection:
    RUTA_DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(RUTA_DB)
    con.execute("""
        CREATE TABLE IF NOT EXISTS turnos (
            id INTEGER PRIMARY KEY,
            chat_id TEXT NOT NULL,
            usuario_id TEXT NOT NULL,
            pregunta TEXT NOT NULL,
            respuesta TEXT NOT NULL,
            creado_en REAL NOT NULL
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_memoria ON turnos(chat_id, usuario_id, creado_en)")
    return con


def _limpiar_expirados(con: sqlite3.Connection, ahora: float) -> None:
    con.execute("DELETE FROM turnos WHERE creado_en < ?", (ahora - VENTANA_SEGUNDOS,))


def contexto_como_texto(chat_id, usuario_id) -> str:
    """Últimos turnos de ESTA persona en ESTE chat, dentro de la ventana, como
    bloque de texto listo para anteponer a la pregunta actual. Vacío si no
    hay conversación reciente (no agrega nada al prompt en ese caso)."""
    ahora = time.time()
    with _conexion() as con:
        _limpiar_expirados(con, ahora)
        filas = con.execute(
            "SELECT pregunta, respuesta FROM turnos WHERE chat_id=? AND usuario_id=? "
            "ORDER BY creado_en DESC LIMIT ?",
            (str(chat_id), str(usuario_id), MAX_TURNOS),
        ).fetchall()
    if not filas:
        return ""

    lineas = ["Conversación reciente con esta misma persona (solo como referencia; "
              "no repitas exactamente su formato):"]
    for pregunta, respuesta in reversed(filas):
        p = (pregunta or "")[:MAX_CARACTERES_REINYECTADOS]
        r = (respuesta or "")[:MAX_CARACTERES_REINYECTADOS]
        lineas.append(f"Pregunta: {p}\nRespuesta: {r}")
    return "\n\n".join(lineas)


def registrar(chat_id, usuario_id, pregunta: str, respuesta: str) -> None:
    ahora = time.time()
    with _conexion() as con:
        _limpiar_expirados(con, ahora)
        con.execute(
            "INSERT INTO turnos (chat_id, usuario_id, pregunta, respuesta, creado_en) VALUES (?, ?, ?, ?, ?)",
            (str(chat_id), str(usuario_id), (pregunta or "")[:MAX_CARACTERES_GUARDADOS],
             (respuesta or "")[:MAX_CARACTERES_GUARDADOS], ahora),
        )
