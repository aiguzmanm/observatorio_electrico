#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
chequeo_diario.py -- corre una vez al día (ver systemd/observatorioelectrico-
chequeo.timer): pide /api/v1/home UNA vez (liviano, ~2-3s) y compara los ids
que trae contra lo que ya está guardado, para detectar sin adivinar:

- discrepancias nuevas (id que no está en discrepancias.api_id)
- documentos nuevos en CUALQUIER caso, nuevo o viejo (id que no está en
  documentos.api_id) -- cubre "alguien subió un archivo nuevo"
- casos que pasaron de en_tramitacion a terminada (típicamente porque se
  subió el dictamen)

Si no hay novedades, no descarga nada más y no manda mensaje. Si las hay,
recién ahí baja el detalle SOLO de los casos afectados (no de los 250+),
extrae texto de lo nuevo, actualiza la base, y manda un resumen a Telegram.
"""
from __future__ import annotations

import requests

import configuracion
from adquisicion.scraper_panel_api import BASE, obtener_home, procesar_caso, DB_PATH

URL_SEND_MESSAGE = f"https://api.telegram.org/bot{configuracion.TELEGRAM_TOKEN}/sendMessage"


def _enviar_telegram(texto: str) -> None:
    if not configuracion.TELEGRAM_TOKEN or not configuracion.TELEGRAM_CHAT_ID:
        print("  AVISO: falta token/chat_id de Telegram, no se manda el resumen (queda solo en el log).")
        return
    datos = {"chat_id": configuracion.TELEGRAM_CHAT_ID, "text": texto[:4096], "disable_web_page_preview": "true"}
    r = requests.post(URL_SEND_MESSAGE, data=datos, timeout=15)
    if not r.ok:
        print(f"  AVISO: Telegram rechazó el envío ({r.status_code}): {r.text[:200]}")


def _armar_mensaje(nuevas: list, cerrados: list, docs_nuevos: dict) -> str:
    partes = ["📋 Novedades del Panel de Expertos hoy:\n"]

    if nuevas:
        partes.append(f"🆕 {len(nuevas)} discrepancia(s) nueva(s):")
        for d in nuevas:
            partes.append(f"  N°{d['number']}-{d.get('_anio', '?')} — {d['cover'][:90]}")
            partes.append(f"  {BASE}/discrepancies/{d['id']}/entities")
        partes.append("")

    if cerrados:
        partes.append(f"✅ {len(cerrados)} discrepancia(s) recién cerrada(s) (dictamen u otro cierre):")
        for d in cerrados:
            partes.append(f"  N°{d['number']}-{d.get('_anio', '?')} — {d['cover'][:90]}")
            partes.append(f"  {BASE}/discrepancies/{d['id']}/entities")
        partes.append("")

    if docs_nuevos:
        partes.append(f"📄 Documentos nuevos en {len(docs_nuevos)} caso(s) ya conocido(s):")
        for api_id, info in docs_nuevos.items():
            partes.append(f"  N°{info['numero']}-{info['anio']} ({info['n']} doc. nuevo(s)) — {info['cover'][:70]}")
        partes.append("")

    return "\n".join(partes).strip()


def chequear_y_avisar() -> None:
    import sqlite3

    print("Descargando /api/v1/home para comparar...")
    home = obtener_home()
    catalogos = {
        "legalMatters": home["legalMatters"],
        "legalSubMatters": home["legalSubMatters"],
        "legalEntities": home["legalEntities"],
        "entities": home["entities"],
    }

    con = sqlite3.connect(DB_PATH)
    try:
        discrepancias_locales = {
            row[0]: row[1] for row in con.execute("SELECT api_id, estado FROM discrepancias WHERE api_id IS NOT NULL")
        }
        documentos_locales_ids = {
            row[0] for row in con.execute("SELECT api_id FROM documentos WHERE api_id IS NOT NULL")
        }

        nuevas_ids = {int(k) for k in home["discrepancies"]} - set(discrepancias_locales)

        docs_nuevos_por_caso: dict[int, int] = {}
        for doc in home["documents"].values():
            if doc["id"] not in documentos_locales_ids:
                docs_nuevos_por_caso[doc["discrepancyId"]] = docs_nuevos_por_caso.get(doc["discrepancyId"], 0) + 1

        cerrados_ids = {
            api_id for api_id, estado in discrepancias_locales.items()
            if estado == "en_tramitacion" and home["discrepancies"].get(str(api_id), {}).get("endedAt")
        }

        casos_a_tocar = nuevas_ids | set(docs_nuevos_por_caso) | cerrados_ids
        if not casos_a_tocar:
            print("Sin novedades.")
            return

        print(f"{len(casos_a_tocar)} caso(s) con novedades: procesando...")
        nuevas, cerrados, docs_nuevos_info = [], [], {}
        for api_id in casos_a_tocar:
            disc = home["discrepancies"][str(api_id)]
            anio = int(str(disc.get("presentationDate") or disc.get("createdAt") or "")[:4]) or None
            disc["_anio"] = anio

            procesar_caso(disc, catalogos, con)

            if api_id in nuevas_ids:
                nuevas.append(disc)
            elif api_id in cerrados_ids:
                cerrados.append(disc)
            elif api_id in docs_nuevos_por_caso:
                docs_nuevos_info[api_id] = {
                    "numero": disc.get("number"), "anio": anio,
                    "cover": disc.get("cover", ""), "n": docs_nuevos_por_caso[api_id],
                }
    finally:
        con.close()

    mensaje = _armar_mensaje(nuevas, cerrados, docs_nuevos_info)
    print(mensaje)
    _enviar_telegram(mensaje)


def main() -> None:
    import sys
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    chequear_y_avisar()


if __name__ == "__main__":
    main()
