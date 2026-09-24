#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
explorar_sitio.py — script exploratorio (no el scraper final). Objetivo:
averiguar CÓMO trae los datos panelexpertos.cl -- ¿hay una API JSON por
debajo que alimenta la página, o hay que leer el DOM ya renderizado?

Abre la página de "Tramitadas" con un navegador headless (Playwright),
escucha TODAS las respuestas de red que parezcan JSON, y además vuelca el
texto/enlaces visibles después de que cargue el JavaScript. Con eso se decide
el enfoque real del scraper.
"""
from __future__ import annotations

import json
import sys
from playwright.sync_api import sync_playwright

URL = "https://panelexpertos.cl/discrepancias/tramitadas/"


def main() -> None:
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")

    respuestas_json = []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()

        def al_recibir_respuesta(response):
            ctype = response.headers.get("content-type", "")
            if "json" in ctype.lower():
                try:
                    cuerpo = response.json()
                except Exception:
                    cuerpo = None
                respuestas_json.append({
                    "url": response.url,
                    "status": response.status,
                    "cuerpo_preview": str(cuerpo)[:2000] if cuerpo is not None else None,
                })
                print(f"[JSON] {response.status} {response.url}")

        page.on("response", al_recibir_respuesta)

        print(f"Cargando {URL} ...")
        page.goto(URL, wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(3000)  # margen para JS tardío

        print("\n--- Enlaces visibles en la página (primeros 60) ---")
        enlaces = page.eval_on_selector_all(
            "a[href]", "els => els.map(e => ({texto: e.innerText.trim(), href: e.href}))"
        )
        vistos = set()
        for e in enlaces:
            clave = (e["texto"], e["href"])
            if clave in vistos or not e["texto"]:
                continue
            vistos.add(clave)
            print(f"  {e['texto'][:60]!r} -> {e['href']}")
            if len(vistos) >= 60:
                break

        print("\n--- Texto visible de la página (primeros 3000 caracteres) ---")
        texto = page.inner_text("body")
        print(texto[:3000])

        browser.close()

    print(f"\n--- Respuestas JSON capturadas: {len(respuestas_json)} ---")
    with open("explorar_sitio_json.json", "w", encoding="utf-8") as f:
        json.dump(respuestas_json, f, ensure_ascii=False, indent=2)
    print("Guardado detalle en explorar_sitio_json.json")


if __name__ == "__main__":
    main()
