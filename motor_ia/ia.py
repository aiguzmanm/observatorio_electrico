#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ia.py — arma la conversación, llama al proveedor de IA (DeepSeek primero,
Gemini de respaldo), ejecuta las herramientas si el modelo las pide, y
devuelve la respuesta final. Mismo patrón que correspondencia_cen/motor_ia/ia.py.
"""
from __future__ import annotations

import configuracion
from mensajeria import memoria_chat
from motor_ia.herramientas_ia import DECLARACIONES, FUNCIONES
from motor_ia.proveedores_ia import crear_sesion

SISTEMA = (
    "Sos un asistente que responde preguntas sobre discrepancias presentadas ante el "
    "Panel de Expertos del sector eléctrico chileno. No inventes discrepancias ni "
    "contenidos que no vengan de las herramientas. Si no encontrás resultados, decilo "
    "claramente en vez de suponer. Respondé en español, breve y directo, en formato "
    "legible para un mensaje de Telegram (sin markdown pesado). Nunca ofrezcas mandar "
    "el archivo/PDF -- no lo tenés para enviar. Cuando cites un documento puntual, "
    "incluí siempre su link (el campo 'link' que devuelven las herramientas) para que "
    "la persona lo abra por su cuenta.\n\n"
    "Tenés cuatro herramientas:\n"
    "1) buscar_discrepancias: para encontrar CASOS por empresa involucrada, materia, "
    "submateria, año o estado (en_tramitacion/terminada). Un caso puede tener más de una "
    "empresa por lado (discrepante o interesada) -- cada resultado trae la lista completa.\n"
    "2) buscar_en_documentos: para encontrar en qué discrepancia se dijo o pidió algo "
    "puntual, buscando dentro del CONTENIDO de los escritos/dictámenes, no solo el "
    "nombre del caso.\n"
    "3) comunicados_de_caso: SIEMPRE que la pregunta sea sobre plazos, fechas, programa "
    "de trabajo, audiencia pública, prórrogas, abstenciones de integrantes del Panel, o "
    "cualquier otro aspecto administrativo/procedimental de UN caso puntual (ya sabés su "
    "número y año). Usala en vez de buscar_en_documentos para esto: esa otra herramienta "
    "busca por coincidencia exacta de palabras y puede no encontrar nada aunque el dato "
    "esté ahí, porque el documento puede no usar la palabra literal de la pregunta (ej. "
    "preguntan 'plazos' y el comunicado dice 'fechas' o 'se recibirán hasta el...').\n"
    "4) obtener_documento_completo: para leer el texto ÍNTEGRO de un documento puntual "
    "(por su documento_id) cuando el extracto no alcance -- cifras exactas, argumentos "
    "técnicos, el resultado final del dictamen."
)

VUELTAS_MAXIMAS = 4


def _proveedores_en_orden():
    yield configuracion.IA_PROVEEDOR_PRIMARIO, configuracion.IA_MODELO_PRIMARIO
    if configuracion.IA_PROVEEDOR_SECUNDARIO:
        yield configuracion.IA_PROVEEDOR_SECUNDARIO, configuracion.IA_MODELO_SECUNDARIO


def _api_key(proveedor: str) -> str:
    return {
        "deepseek": configuracion.IA_API_KEY_DEEPSEEK,
        "gemini": configuracion.IA_API_KEY_GEMINI,
    }.get(proveedor, "")


def preguntar(pregunta: str, chat_id=None, usuario_id=None) -> str:
    """chat_id/usuario_id opcionales: sin ellos, cada pregunta es
    independiente. Con ellos, se antepone la conversación reciente de esa
    persona en ese chat y se registra este turno al final."""
    pregunta_con_contexto = pregunta
    if chat_id is not None and usuario_id is not None:
        contexto = memoria_chat.contexto_como_texto(chat_id, usuario_id)
        if contexto:
            pregunta_con_contexto = f"{contexto}\n\n---\n\nPregunta actual: {pregunta}"

    ultimo_error: Exception | None = None
    intentado = False
    respuesta_final = None
    for proveedor, modelo in _proveedores_en_orden():
        api_key = _api_key(proveedor)
        if not api_key or not modelo:
            continue
        intentado = True
        try:
            respuesta_final = _responder_con(proveedor, modelo, api_key, pregunta_con_contexto)
            break
        except Exception as e:
            print(f"  [{proveedor}] falló, probando siguiente proveedor si hay: {e}")
            ultimo_error = e
            continue

    if respuesta_final is None:
        if not intentado:
            respuesta_final = "No hay ningún proveedor de IA configurado (falta modelo/api_key en config.ini)."
        else:
            respuesta_final = f"No pude responder (todos los proveedores de IA fallaron): {ultimo_error}"

    if chat_id is not None and usuario_id is not None:
        memoria_chat.registrar(chat_id, usuario_id, pregunta, respuesta_final)
    return respuesta_final


def _responder_con(proveedor: str, modelo: str, api_key: str, pregunta: str) -> str:
    sesion = crear_sesion(proveedor, modelo, api_key, SISTEMA, pregunta, DECLARACIONES)

    for _ in range(VUELTAS_MAXIMAS):
        respuesta = sesion.consultar()
        if not respuesta.llamadas:
            return respuesta.texto or "(el modelo no devolvió texto)"

        resultados = []
        for llamada in respuesta.llamadas:
            funcion = FUNCIONES.get(llamada.nombre)
            resultado = funcion(**llamada.argumentos) if funcion else {"error": f"herramienta desconocida: {llamada.nombre}"}
            resultados.append((llamada, resultado))
        sesion.agregar_resultados(respuesta, resultados)

    sesion.cerrar_herramientas()
    respuesta = sesion.consultar()
    return respuesta.texto or "(no se pudo cerrar la respuesta tras varias vueltas)"
