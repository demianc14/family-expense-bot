"""Cliente de Gemini. Única parte del paquete que habla con un LLM real.

Aislado detrás del protocolo `ClienteLLM` para que los tests no lo toquen y
para que cambiar de proveedor no obligue a reescribir el parser.
"""

from typing import Any

MODELO_POR_DEFECTO = "gemini-3.8-flash"
TIMEOUT_MS = 30_000
"""Sin esto, una llamada colgada deja al bot sin responderle a la persona."""


class ErrorDelLLM(Exception):
    """El proveedor falló: timeout, cuota agotada, respuesta vacía."""


class GeminiCliente:
    def __init__(
        self, api_key: str, modelo: str = MODELO_POR_DEFECTO, timeout_ms: int = TIMEOUT_MS
    ) -> None:
        from google import genai  # import perezoso: el paquete solo hace falta en runtime
        from google.genai import types

        self._cliente = genai.Client(
            api_key=api_key, http_options=types.HttpOptions(timeout=timeout_ms)
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
            raise ErrorDelLLM(f"Gemini no respondió: {e}") from e

        salida = interaccion.output_text
        if not isinstance(salida, str) or not salida.strip():
            raise ErrorDelLLM("Gemini devolvió una respuesta vacía")
        return salida
