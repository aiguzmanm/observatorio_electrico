#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
chequeo_diario.py -- corre una vez al día (ver systemd/observatorioelectrico-
chequeo.timer): revisa novedades del Panel de Expertos, la CNE (Normas
Técnicas/Servicios Complementarios) y el Diario Oficial, carga lo nuevo, y
manda UN resumen por Telegram si hay algo -- si no hay nada en ninguna de
las tres fuentes, no manda mensaje.

Panel de Expertos: pide /api/v1/home UNA vez (liviano) y compara los ids
contra lo ya guardado -- discrepancias nuevas, documentos nuevos en
cualquier caso (nuevo o viejo), y casos que pasaron a terminada.

CNE: pide /normas y /servicios (livianos también) y compara codigo_carpeta
(resoluciones) / link (normativa vigente) contra lo ya guardado.

Diario Oficial: revisa la edición electrónica del día, junta TODAS las
publicaciones (no solo las de Energía) y le pide a la IA que catalogue
cuáles son del sector eléctrico -- ver scraper_diario_oficial.py.
"""
from __future__ import annotations

from datetime import date

import requests

import configuracion
from adquisicion.scraper_panel_api import BASE, obtener_home, procesar_caso, DB_PATH as DB_PANEL
from adquisicion.scraper_cne_normativa import (
    CATEGORIA_NORMATIVA_VIGENTE, DB_PATH as DB_CNE,
    obtener_normas, obtener_servicios, procesar_normativa_vigente, procesar_resolucion,
)
from adquisicion.scraper_diario_oficial import revisar_dia as revisar_diario_oficial

URL_SEND_MESSAGE = f"https://api.telegram.org/bot{configuracion.TELEGRAM_TOKEN}/sendMessage"


def _enviar_telegram(texto: str) -> None:
    if not configuracion.TELEGRAM_TOKEN or not configuracion.TELEGRAM_CHAT_ID:
        print("  AVISO: falta token/chat_id de Telegram, no se manda el resumen (queda solo en el log).")
        return
    datos = {"chat_id": configuracion.TELEGRAM_CHAT_ID, "text": texto[:4096], "disable_web_page_preview": "true"}
    r = requests.post(URL_SEND_MESSAGE, data=datos, timeout=15)
    if not r.ok:
        print(f"  AVISO: Telegram rechazó el envío ({r.status_code}): {r.text[:200]}")


def _chequear_panel() -> tuple[list, list, dict]:
    import sqlite3

    print("[Panel] Descargando /api/v1/home para comparar...")
    home = obtener_home()
    catalogos = {
        "legalMatters": home["legalMatters"],
        "legalSubMatters": home["legalSubMatters"],
        "legalEntities": home["legalEntities"],
        "entities": home["entities"],
    }

    con = sqlite3.connect(DB_PANEL)
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
            print("[Panel] Sin novedades.")
            return [], [], {}

        print(f"[Panel] {len(casos_a_tocar)} caso(s) con novedades: procesando...")
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
        return nuevas, cerrados, docs_nuevos_info
    finally:
        con.close()


def _chequear_cne() -> list[dict]:
    import sqlite3

    print("[CNE] Descargando /normas y /servicios para comparar...")
    normas = obtener_normas()
    servicios = obtener_servicios()

    con = sqlite3.connect(DB_CNE)
    try:
        codigos_locales = {
            row[0] for row in con.execute("SELECT codigo_carpeta FROM resoluciones")
        }
        links_locales = {
            row[0] for row in con.execute("SELECT link FROM normativa_vigente")
        }

        nuevos: list[dict] = []
        for categoria, items in normas.items():
            if categoria == CATEGORIA_NORMATIVA_VIGENTE:
                for item in items:
                    if item.get("Link Carpeta") and item["Link Carpeta"] not in links_locales:
                        procesar_normativa_vigente(item, con)
                        nuevos.append({"tipo": "normativa_vigente", "sigla": item.get("Sigla NT"), "nombre": item.get("Normas Técnicas", "")})
            else:
                for item in items:
                    if item.get("Código Carpeta") not in codigos_locales:
                        procesar_resolucion("normas", categoria, item, con)
                        nuevos.append({"tipo": "resolucion", "categoria": categoria, "nombre": item.get("Nombre Resolución", ""), "resolucion": item.get("Resolución/Decreto", "")})

        for categoria, items in servicios.items():
            for item in items:
                if item.get("Código Carpeta") not in codigos_locales:
                    procesar_resolucion("servicios", categoria, item, con)
                    nuevos.append({"tipo": "resolucion", "categoria": categoria, "nombre": item.get("Nombre Resolución", ""), "resolucion": item.get("Resolución/Decreto", "")})

        if not nuevos:
            print("[CNE] Sin novedades.")
        else:
            print(f"[CNE] {len(nuevos)} novedad(es).")
        return nuevos
    finally:
        con.close()


def _armar_mensaje(nuevas: list, cerrados: list, docs_nuevos: dict, novedades_cne: list, novedades_diario: list) -> str:
    partes = ["📋 Novedades de hoy:\n"]

    if nuevas:
        partes.append(f"🆕 {len(nuevas)} discrepancia(s) nueva(s) (Panel de Expertos):")
        for d in nuevas:
            partes.append(f"  N°{d['number']}-{d.get('_anio', '?')} — {d['cover'][:90]}")
            partes.append(f"  {BASE}/discrepancies/{d['id']}/entities")
        partes.append("")

    if cerrados:
        partes.append(f"✅ {len(cerrados)} discrepancia(s) recién cerrada(s):")
        for d in cerrados:
            partes.append(f"  N°{d['number']}-{d.get('_anio', '?')} — {d['cover'][:90]}")
            partes.append(f"  {BASE}/discrepancies/{d['id']}/entities")
        partes.append("")

    if docs_nuevos:
        partes.append(f"📄 Documentos nuevos en {len(docs_nuevos)} caso(s) del Panel ya conocido(s):")
        for api_id, info in docs_nuevos.items():
            partes.append(f"  N°{info['numero']}-{info['anio']} ({info['n']} doc. nuevo(s)) — {info['cover'][:70]}")
        partes.append("")

    if novedades_cne:
        partes.append(f"⚡ {len(novedades_cne)} novedad(es) de CNE (Normas Técnicas/Servicios Complementarios):")
        for n in novedades_cne:
            if n["tipo"] == "resolucion":
                partes.append(f"  [{n['categoria'][:40]}] {n['resolucion']} — {n['nombre'][:80]}")
            else:
                partes.append(f"  [Normativa vigente, {n['sigla']}] {n['nombre'][:80]}")
        partes.append("")

    if novedades_diario:
        partes.append(f"📰 {len(novedades_diario)} publicación(es) del Diario Oficial de hoy (sector eléctrico, catalogadas por IA):")
        for p in novedades_diario:
            partes.append(f"  [{p['ministerio'][:35]}] {p['titulo'][:90]}")
            partes.append(f"  {p['link_pdf']}")
        partes.append("")

    return "\n".join(partes).strip()


def chequear_y_avisar() -> None:
    nuevas, cerrados, docs_nuevos_info = _chequear_panel()
    novedades_cne = _chequear_cne()
    novedades_diario = revisar_diario_oficial(date.today().strftime("%d-%m-%Y"))

    if not (nuevas or cerrados or docs_nuevos_info or novedades_cne or novedades_diario):
        print("Sin novedades en ninguna fuente.")
        return

    mensaje = _armar_mensaje(nuevas, cerrados, docs_nuevos_info, novedades_cne, novedades_diario)
    print(mensaje)
    _enviar_telegram(mensaje)


def main() -> None:
    import sys
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    chequear_y_avisar()


if __name__ == "__main__":
    main()
