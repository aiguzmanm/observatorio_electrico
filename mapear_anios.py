#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mapear_anios.py — script de UN SOLO USO (no es el scraper final, no corre en
la VM). Hace clic en cada botón de año (2004-2026) en /discrepancias/
tramitadas/ y registra el id de carpeta (lastFolder) que devuelve cada uno,
más el listtoken/account_id/drive_id vigentes en esta carga de página.
Guarda todo en anios_map.json para que el scraper real no dependa de
Playwright.
"""
from __future__ import annotations

import json
import sys
from playwright.sync_api import sync_playwright

URL = "https://panelexpertos.cl/discrepancias/tramitadas/"
ANIOS = list(range(2004, 2027))


def main() -> None:
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")

    mapa = {}
    ultima_respuesta = {}

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()

        def on_resp(resp):
            if "admin-ajax.php" in resp.url and resp.request.method == "POST":
                try:
                    ultima_respuesta["body"] = resp.json()
                    ultima_respuesta["post_data"] = resp.request.post_data
                except Exception:
                    pass

        page.on("response", on_resp)
        page.goto(URL, wait_until="networkidle", timeout=60000)

        for anio in ANIOS:
            ultima_respuesta.clear()
            try:
                page.click(f"#bk-discre-tra-{anio}", timeout=5000)
            except Exception as e:
                print(f"{anio}: no se pudo hacer clic ({e})")
                continue
            page.wait_for_timeout(16000)  # el AJAX real tarda ~10-15s, damos margen por año
            cuerpo = ultima_respuesta.get("body", {})
            last_folder = cuerpo.get("lastFolder")
            drive_id = cuerpo.get("driveId")
            account_id = cuerpo.get("accountId")
            # el listtoken viaja en el post_data, no en la respuesta
            post_data = ultima_respuesta.get("post_data", "") or ""
            listtoken = None
            for parte in post_data.split("&"):
                if parte.startswith("listtoken="):
                    listtoken = parte.split("=", 1)[1]
            print(f"{anio}: lastFolder={last_folder} listtoken={listtoken}")
            mapa[str(anio)] = {
                "lastFolder": last_folder,
                "driveId": drive_id,
                "accountId": account_id,
                "listtoken": listtoken,
            }

        browser.close()

    with open("anios_map.json", "w", encoding="utf-8") as f:
        json.dump(mapa, f, ensure_ascii=False, indent=2)
    print(f"\nGuardado en anios_map.json ({len(mapa)} años)")


if __name__ == "__main__":
    main()
