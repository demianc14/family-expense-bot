"""Cliente de Gemini. Única parte del paquete que habla con un LLM real.

Aislado detrás del protocolo `ClienteLLM` para que los tests no lo toquen y
para que cambiar de proveedor no obligue a reescribir el parser.
"""

from typing import Any

MODELO_POR_DEFECTO = "gemini-3.5-flash-lite"
"""Medido con el prompt real de este proyecto: flash-lite 2.8 s, flash 15.3 s.
Para un chat donde alguien espera la confirmación, esa diferencia es todo."""

TIMEOUT_MS = 30_000
"""Sin esto, una llamada colgada deja al bot sin responderle a la persona."""

INTENTOS = 2
"""El SDK reintenta con backoff por defecto. Ante un 429 eso significa más de
un minuto esperando una cuota que ya se agotó: mejor fallar y avisar."""


class ErrorDelLLM(Exception):
    """El proveedor falló: timeout, respuesta vacía, error de red."""


class CuotaAgotada(ErrorDelLLM):
    """Se acabó la cuota del plan gratuito (HTTP 429)."""


class GeminiCliente:
    def __init__(
        self, api_key: str, modelo: str = MODELO_POR_DEFECTO, timeout_ms: int = TIMEOUT_MS
    ) -> None:
        from google import genai  # import perezoso: el paquete solo hace falta en runtime
        from google.genai import types

        self._cliente = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(
                timeout=timeout_ms,
                retry_options=types.HttpRetryOptions(attempts=INTENTOS),
            ),
        )
        self._modelo = modelo

    def completar_json(self, prompt: str, schema: dict[str, object]) -> str:
        try:
            interaccion: Any = self._cliente.interactions.create(
                model=self._modelo,
                input=prompt,
                response_format={
                    "type": "text",
                    "mime_type": "application/json",
                    "schema": schema,
                },
            )
        except Exception as e:
            if _es_cuota_agotada(e):
                raise CuotaAgotada(f"Cuota de Gemini agotada: {e}") from e
            raise ErrorDelLLM(f"Gemini no respondió: {e}") from e

        salida = interaccion.output_text
        if not isinstance(salida, str) or not salida.strip():
            raise ErrorDelLLM("Gemini devolvió una respuesta vacía")
        return salida


def _es_cuota_agotada(error: Exception) -> bool:
    codigo = getattr(error, "code", None) or getattr(error, "status_code", None)
    return codigo == 429 or "429" in str(error) or "RESOURCE_EXHAUSTED" in str(error)
