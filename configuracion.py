#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
configuracion.py — carga config/config.ini (o config/config.ini.example
como respaldo de solo lectura, con aviso) y expone las rutas/ajustes que
usa el resto del proyecto. Mismo patrón que correspondencia_cen.
"""
from __future__ import annotations

import configparser
from pathlib import Path

RAIZ_PROYECTO = Path(__file__).resolve().parent
RUTA_CONFIG = RAIZ_PROYECTO / "config" / "config.ini"
RUTA_CONFIG_EJEMPLO = RAIZ_PROYECTO / "config" / "config.ini.example"


def _cargar() -> configparser.ConfigParser:
    cfg = configparser.ConfigParser(interpolation=None)
    if RUTA_CONFIG.exists():
        cfg.read(RUTA_CONFIG, encoding="utf-8")
    elif RUTA_CONFIG_EJEMPLO.exists():
        print(f"AVISO: no existe {RUTA_CONFIG}; uso {RUTA_CONFIG_EJEMPLO} (solo sirve para desarrollo)")
        cfg.read(RUTA_CONFIG_EJEMPLO, encoding="utf-8")
    else:
        raise FileNotFoundError(f"No encontré {RUTA_CONFIG} ni {RUTA_CONFIG_EJEMPLO}")
    return cfg


_cfg = _cargar()

RAIZ_DATOS = RAIZ_PROYECTO / _cfg.get("rutas", "datos", fallback="datos")

TELEGRAM_TOKEN = _cfg.get("telegram", "token", fallback="")
TELEGRAM_CHAT_ID = _cfg.get("telegram", "chat_id", fallback="")

# Mismo esquema que correspondencia_cen: DeepSeek primario, Gemini de respaldo.
IA_PROVEEDOR_PRIMARIO = _cfg.get("ia", "proveedor_primario", fallback="deepseek").strip().lower()
IA_MODELO_PRIMARIO = _cfg.get("ia", "modelo_primario", fallback="").strip()
IA_PROVEEDOR_SECUNDARIO = _cfg.get("ia", "proveedor_secundario", fallback="gemini").strip().lower()
IA_MODELO_SECUNDARIO = _cfg.get("ia", "modelo_secundario", fallback="").strip()
IA_API_KEY_DEEPSEEK = _cfg.get("ia", "api_key_deepseek", fallback="").strip()
IA_API_KEY_GEMINI = _cfg.get("ia", "api_key_gemini", fallback="").strip()
