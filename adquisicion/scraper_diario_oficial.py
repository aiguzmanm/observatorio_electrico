#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scraper_diario_oficial.py -- revisa la edición electrónica del Diario
Oficial de Chile del día, junta TODAS las publicaciones (de todos los
ministerios, no solo Energía), y le pasa la lista de títulos a la IA para
que decida cuáles son relevantes al sector eléctrico -- no se filtra por
nombre de sección en código, la catalogación la hace el modelo.

Mecanismo real (investigado, sin necesidad de navegador):
- GET select_edition.php?date=DD-MM-YYYY -- resuelve la(s) edición(es) de
  ese día (puede haber más de una, ej. "44557" y "44557-B"). Domingo/feriado
  sin publicación: devuelve la página sin ningún link de edición.
- GET index.php?date=DD-MM-YYYY&edition=<N>&v=<v> -- el contenido de esa
  edición, organizado en tablas por Ministerio (<td class="title4">) con
  una fila por publicación (título + link a PDF directo, sin login).
- Ojo: pedir index.php directo (sin haber pasado antes por
  select_edition.php en la MISMA sesión/cookies) devuelve 403 -- por eso
  todo esto usa una sola requests.Session().

No se guardan las publicaciones irrelevantes -- solo las que la IA marca
como del sector eléctrico, con el motivo que dio.
"""
from __future__ import annotations

import json
import re
from datetime import date

import requests

import configuracion
from motor_ia.proveedores_ia import crear_sesion

BASE = "https://www.diariooficial.interior.gob.cl/edicionelectronica"
DB_PATH = configuracion.RAIZ_DATOS / "cne.sqlite"

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

RE_MINISTERIO = re.compile(r'<td class="title4">([^<]*)</td>', re.IGNORECASE)
RE_FILA = re.compile(
    r'<tr class="content">\s*<td>(?P<titulo>.*?)<span class="border dotted">.*?'
    r'href="(?P<link>[^"]*\.pdf)"[^>]*>Ver PDF \((?P<cve>CVE-\d+)\)',
    re.DOTALL,
)
RE_EDICIONES = re.compile(r'index\.php\?date=([\d-]+)&edition=([\w-]+)&v=(\d+)')


def resolver_ediciones_del_dia(fecha_ddmmyyyy: str, sesion: requests.Session) -> list[tuple[str, str, str]]:
    """Devuelve [(date, edition, v), ...] -- normalmente una, a veces más de una."""
    resp = sesion.get(f"{BASE}/select_edition.php", params={"date": fecha_ddmmyyyy}, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return RE_EDICIONES.findall(resp.text)


def obtener_publicaciones(fecha: str, edicion: str, v: str, sesion: requests.Session) -> list[dict]:
    resp = sesion.get(
        f"{BASE}/index.php", params={"date": fecha, "edition": edicion, "v": v}, headers=HEADERS, timeout=30,
    )
    resp.raise_for_status()
    html = resp.text

    publicaciones = []
    ministerio_actual = ""
    # recorrer intercalando headers de ministerio y filas de publicación en orden de aparición
    partes = re.split(r'(<td class="title4">[^<]*</td>)', html)
    for parte in partes:
        m = RE_MINISTERIO.search(parte)
        if m:
            ministerio_actual = m.group(1).strip()
            continue
        for fila in RE_FILA.finditer(parte):
            titulo = re.sub(r"\s+", " ", fila.group("titulo")).strip()
            publicaciones.append({
                "ministerio": ministerio_actual, "titulo": titulo,
                "link_pdf": fila.group("link"), "cve": fila.group("cve"),
            })
    return publicaciones


def clasificar_con_ia(publicaciones: list[dict]) -> list[dict]:
    """Le pasa los títulos a la IA y devuelve solo las que marca relevantes
    al sector eléctrico, con el motivo. Gemini primero (tarea simple, no
    hace falta tanta potencia -- mismo criterio que correspondencia_cen
    para resúmenes), DeepSeek de respaldo."""
    if not publicaciones:
        return []

    listado = "\n".join(
        f"{i}. [{p['ministerio']}] {p['titulo']}" for i, p in enumerate(publicaciones)
    )
    sistema = (
        "Sos un clasificador. Te doy una lista numerada de publicaciones del Diario Oficial de "
        "Chile de un día (ministerio entre corchetes + título). Marcá cuáles son relevantes para "
        "el sector eléctrico chileno: CNE, Ministerio de Energía, SEC, Coordinador Eléctrico "
        "Nacional, o cualquier decreto/resolución de OTRO organismo que regule explícitamente "
        "electricidad, energía o combustibles para generación eléctrica. No marques publicaciones "
        "de otros sectores aunque el organismo emisor a veces publique sobre energía (juzgá por el "
        "título). Respondé SOLO con un JSON: una lista de objetos {\"indice\": N, \"motivo\": "
        "\"...\"} -- nada de texto fuera del JSON. Si ninguna es relevante, respondé []."
    )
    pregunta = listado

    for proveedor, modelo in (
        (configuracion.IA_PROVEEDOR_SECUNDARIO, configuracion.IA_MODELO_SECUNDARIO),
        (configuracion.IA_PROVEEDOR_PRIMARIO, configuracion.IA_MODELO_PRIMARIO),
    ):
        api_key = {"deepseek": configuracion.IA_API_KEY_DEEPSEEK, "gemini": configuracion.IA_API_KEY_GEMINI}.get(proveedor, "")
        if not api_key or not modelo:
            continue
        try:
            sesion = crear_sesion(proveedor, modelo, api_key, sistema, pregunta, [])
            respuesta = sesion.consultar()
            texto = respuesta.texto.strip()
            texto = re.sub(r"^```(json)?|```$", "", texto, flags=re.MULTILINE).strip()
            marcadas = json.loads(texto)
            resultado = []
            for m in marcadas:
                idx = m.get("indice")
                if idx is not None and 0 <= idx < len(publicaciones):
                    pub = dict(publicaciones[idx])
                    pub["motivo_ia"] = m.get("motivo", "")
                    resultado.append(pub)
            return resultado
        except Exception as e:
            print(f"  [{proveedor}] falló clasificando, probando siguiente si hay: {e}")
            continue
    return []


def guardar_y_devolver_nuevas(fecha_iso: str, edicion: str, relevantes: list[dict]) -> list[dict]:
    import sqlite3

    con = sqlite3.connect(DB_PATH)
    try:
        con.execute("""
            CREATE TABLE IF NOT EXISTS diario_oficial (
                id            INTEGER PRIMARY KEY,
                cve           TEXT UNIQUE,
                fecha         TEXT,
                edicion       TEXT,
                ministerio    TEXT,
                titulo        TEXT,
                link_pdf      TEXT,
                motivo_ia     TEXT,
                fecha_ingesta TEXT
            )
        """)
        con.commit()
        nuevas = []
        for pub in relevantes:
            ya = con.execute("SELECT id FROM diario_oficial WHERE cve = ?", (pub["cve"],)).fetchone()
            if ya:
                continue
            con.execute(
                """INSERT INTO diario_oficial (cve, fecha, edicion, ministerio, titulo, link_pdf, motivo_ia, fecha_ingesta)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (pub["cve"], fecha_iso, edicion, pub["ministerio"], pub["titulo"], pub["link_pdf"],
                 pub.get("motivo_ia", ""), date.today().isoformat()),
            )
            nuevas.append(pub)
        con.commit()
        return nuevas
    finally:
        con.close()


def revisar_dia(fecha_ddmmyyyy: str) -> list[dict]:
    """fecha_ddmmyyyy: 'DD-MM-YYYY'. Devuelve las publicaciones nuevas y
    relevantes que se acaban de guardar (vacío si no hay edición ese día,
    o si no hay nada nuevo relevante)."""
    sesion = requests.Session()
    ediciones = resolver_ediciones_del_dia(fecha_ddmmyyyy, sesion)
    if not ediciones:
        print(f"[Diario Oficial] Sin edición el {fecha_ddmmyyyy}.")
        return []

    fecha_iso = "-".join(reversed(fecha_ddmmyyyy.split("-")))
    todas_nuevas = []
    for fecha, edicion, v in ediciones:
        publicaciones = obtener_publicaciones(fecha, edicion, v, sesion)
        print(f"[Diario Oficial] Edición {edicion}: {len(publicaciones)} publicaciones totales.")
        relevantes = clasificar_con_ia(publicaciones)
        print(f"[Diario Oficial] Edición {edicion}: {len(relevantes)} marcadas relevantes por la IA.")
        nuevas = guardar_y_devolver_nuevas(fecha_iso, edicion, relevantes)
        todas_nuevas.extend(nuevas)
    return todas_nuevas


def main() -> None:
    import sys
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    fecha = sys.argv[1] if len(sys.argv) > 1 else date.today().strftime("%d-%m-%Y")
    nuevas = revisar_dia(fecha)
    for pub in nuevas:
        print(f"  [{pub['ministerio']}] {pub['titulo'][:80]} -- {pub['motivo_ia']}")
        print(f"    {pub['link_pdf']}")


if __name__ == "__main__":
    main()
