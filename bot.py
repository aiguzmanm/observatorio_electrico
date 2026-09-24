#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bot.py — cada mensaje que llega se pasa a motor_ia.ia.preguntar(): el modelo
(DeepSeek primero, Gemini de respaldo) decide si necesita llamar a
buscar_discrepancias / buscar_en_documentos / obtener_documento_completo y
arma la respuesta final. Mismo mecanismo que correspondencia_cen/bot.py:
requests directo a la API de Telegram (getUpdates long polling +
sendMessage), sin librería de por medio.

Uso:
    python bot.py
    (Ctrl+C para detenerlo)
"""
from __future__ import annotations

import time

import requests

import configuracion
from motor_ia.ia import preguntar

RUTA_OFFSET = configuracion.RAIZ_DATOS / "telegram_offset.txt"
URL_GET_UPDATES = f"https://api.telegram.org/bot{configuracion.TELEGRAM_TOKEN}/getUpdates"
URL_SEND_MESSAGE = f"https://api.telegram.org/bot{configuracion.TELEGRAM_TOKEN}/sendMessage"


def _leer_offset() -> int:
    if RUTA_OFFSET.exists():
        try:
            return int(RUTA_OFFSET.read_text().strip())
        except ValueError:
            pass
    return 0


def _guardar_offset(offset: int) -> None:
    RUTA_OFFSET.parent.mkdir(parents=True, exist_ok=True)
    RUTA_OFFSET.write_text(str(offset))


def _enviar(chat_id: int, texto: str) -> None:
    datos = {"chat_id": chat_id, "text": texto[:4096], "disable_web_page_preview": "true"}
    r = requests.post(URL_SEND_MESSAGE, data=datos, timeout=15)
    if not r.ok:
        print(f"  AVISO: Telegram rechazó el envío ({r.status_code}): {r.text[:200]}")


def main() -> None:
    if not configuracion.TELEGRAM_TOKEN:
        print("Falta [telegram] token en config.ini.")
        return

    offset = _leer_offset()
    print("Bot escuchando (Ctrl+C para salir)...")

    while True:
        try:
            r = requests.get(URL_GET_UPDATES, params={"timeout": 30, "offset": offset}, timeout=40)
            r.raise_for_status()
            actualizaciones = r.json().get("result", [])
        except requests.RequestException as e:
            print(f"  Error consultando Telegram: {e}")
            time.sleep(5)
            continue

        for act in actualizaciones:
            offset = act["update_id"] + 1
            mensaje = act.get("message") or {}
            texto = (mensaje.get("text") or "").strip()
            chat_id = mensaje.get("chat", {}).get("id")
            usuario_id = mensaje.get("from", {}).get("id")
            if not texto or chat_id is None:
                continue

            print(f"  Consulta de {chat_id}/{usuario_id}: {texto}")
            try:
                respuesta = preguntar(texto, chat_id=chat_id, usuario_id=usuario_id)
            except Exception as e:
                respuesta = f"Error inesperado respondiendo: {e}"
                print(f"  ERROR: {e}")
            _enviar(chat_id, respuesta)

        _guardar_offset(offset)


if __name__ == "__main__":
    main()
