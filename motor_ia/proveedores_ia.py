#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Adaptadores de proveedores de IA.

Vendoreado casi textual desde chat_bot/motor_ia/proveedores_ia.py: es
genérico (no depende del dominio energético ni de este), así que se reusa
tal cual. El bot trabaja con una misma lista de herramientas de Python. Cada
clase de este archivo traduce esa conversación a la API del proveedor
respectivo. Para agregar otro proveedor basta implementar ``SesionProveedor``
y registrarlo en ``crear_sesion``.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import requests


URL_GEMINI = "https://generativelanguage.googleapis.com/v1beta/models"
URL_DEEPSEEK = "https://api.deepseek.com/chat/completions"


@dataclass
class LlamadaHerramienta:
    id: str
    nombre: str
    argumentos: dict[str, Any]


@dataclass
class RespuestaProveedor:
    texto: str
    llamadas: list[LlamadaHerramienta]
    cruda: dict[str, Any]
    uso: dict[str, Any] = field(default_factory=dict)
    modelo_servido: str = ""


class SesionProveedor:
    """Contrato común para el ciclo modelo -> herramienta -> modelo."""

    def consultar(self) -> RespuestaProveedor:
        raise NotImplementedError

    def cerrar_herramientas(self) -> None:
        """La corrección final usa los datos obtenidos y no inicia más acciones."""

    def agregar_resultados(
        self, respuesta: RespuestaProveedor, resultados: list[tuple[LlamadaHerramienta, dict]]
    ) -> None:
        raise NotImplementedError

    def agregar_instruccion(self, texto: str) -> None:
        raise NotImplementedError


class SesionGemini(SesionProveedor):
    def __init__(self, modelo: str, api_key: str, sistema: str, pregunta: str, declaraciones: list[dict]):
        self.modelo = modelo
        self.api_key = api_key
        self.contents: list[dict] = [{"role": "user", "parts": [{"text": pregunta}]}]
        self.body: dict = {
            "systemInstruction": {"parts": [{"text": sistema}]},
            "contents": self.contents,
        }
        if declaraciones:
            self.body["tools"] = [{"functionDeclarations": declaraciones}]

    def consultar(self) -> RespuestaProveedor:
        url = f"{URL_GEMINI}/{self.modelo}:generateContent"
        respuesta = requests.post(url, params={"key": self.api_key}, json=self.body, timeout=120)
        respuesta.raise_for_status()
        datos = respuesta.json()
        contenido = datos["candidates"][0]["content"]
        llamadas = []
        textos = []
        for parte in contenido.get("parts", []):
            if "functionCall" in parte:
                llamada = parte["functionCall"]
                llamadas.append(LlamadaHerramienta(
                    id=str(llamada.get("id", "")),
                    nombre=llamada["name"],
                    argumentos=llamada.get("args", {}),
                ))
            elif parte.get("text") and not parte.get("thought", False):
                textos.append(parte["text"])
        return RespuestaProveedor("\n".join(textos).strip(), llamadas, contenido, datos.get("usageMetadata", {}),
                                  str(datos.get("modelVersion") or ""))

    def agregar_resultados(
        self, respuesta: RespuestaProveedor, resultados: list[tuple[LlamadaHerramienta, dict]]
    ) -> None:
        self.contents.append({"role": "model", "parts": respuesta.cruda["parts"]})
        partes = [
            {"functionResponse": {"name": llamada.nombre, "id": llamada.id, "response": resultado}}
            for llamada, resultado in resultados
        ]
        self.contents.append({"role": "user", "parts": partes})

    def agregar_instruccion(self, texto: str) -> None:
        self.contents.append({"role": "user", "parts": [{"text": texto}]})

    def cerrar_herramientas(self) -> None:
        self.body.pop("tools", None)


class SesionDeepSeek(SesionProveedor):
    def __init__(self, modelo: str, api_key: str, sistema: str, pregunta: str, declaraciones: list[dict]):
        self.modelo = modelo
        self.api_key = api_key
        self.messages: list[dict] = [
            {"role": "system", "content": sistema},
            {"role": "user", "content": pregunta},
        ]
        self.tools = [{"type": "function", "function": declaracion} for declaracion in declaraciones]

    def consultar(self) -> RespuestaProveedor:
        body: dict = {
            "model": self.modelo,
            "messages": self.messages,
            "thinking": {"type": "disabled"},
            "stream": False,
        }
        if self.tools:
            body["tools"] = self.tools
            body["tool_choice"] = "auto"
        respuesta = requests.post(
            URL_DEEPSEEK,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            json=body,
            timeout=120,
        )
        respuesta.raise_for_status()
        datos = respuesta.json()
        mensaje = datos["choices"][0]["message"]
        llamadas = []
        for llamada in mensaje.get("tool_calls") or []:
            funcion = llamada.get("function", {})
            try:
                argumentos = json.loads(funcion.get("arguments") or "{}")
            except json.JSONDecodeError as error:
                raise ValueError(f"DeepSeek devolvió argumentos inválidos para {funcion.get('name')}: {error}") from error
            llamadas.append(LlamadaHerramienta(
                id=str(llamada["id"]), nombre=funcion["name"], argumentos=argumentos,
            ))
        return RespuestaProveedor((mensaje.get("content") or "").strip(), llamadas, mensaje, datos.get("usage", {}),
                                  str(datos.get("model") or ""))

    def agregar_resultados(
        self, respuesta: RespuestaProveedor, resultados: list[tuple[LlamadaHerramienta, dict]]
    ) -> None:
        self.messages.append(respuesta.cruda)
        for llamada, resultado in resultados:
            self.messages.append({
                "role": "tool",
                "tool_call_id": llamada.id,
                "content": json.dumps(resultado, ensure_ascii=False, allow_nan=False),
            })

    def agregar_instruccion(self, texto: str) -> None:
        self.messages.append({"role": "user", "content": texto})

    def cerrar_herramientas(self) -> None:
        self.tools = []


def crear_sesion(
    proveedor: str, modelo: str, api_key: str, sistema: str, pregunta: str, declaraciones: list[dict]
) -> SesionProveedor:
    proveedores = {"gemini": SesionGemini, "deepseek": SesionDeepSeek}
    clase = proveedores.get(proveedor.lower())
    if clase is None:
        disponibles = ", ".join(sorted(proveedores))
        raise ValueError(f"Proveedor IA desconocido '{proveedor}'. Disponibles: {disponibles}.")
    if not api_key:
        raise ValueError(f"Falta la API key para el proveedor {proveedor} en config.ini.")
    if not modelo:
        raise ValueError(f"Falta el modelo para el proveedor {proveedor} en config.ini.")
    return clase(modelo, api_key, sistema, pregunta, declaraciones)
