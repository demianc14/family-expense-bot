"""Cliente de Gemini. Única parte del paquete que habla con un LLM real.

Aislado detrás del protocolo `ClienteLLM` para que los tests no lo toquen y
para que cambiar de proveedor no obligue a reescribir el parser.
"""

from typing import Any

MODELO_POR_DEFECTO = "gemini-3.8-flash"


class GeminiCliente:
    def __init__(self, api_key: str, modelo: str = MODELO_POR_DEFECTO) -> None:
        from google import genai  # import perezoso: el paquete solo hace falta en runtime

        self._cliente = genai.Client(api_key=api_key)
        self._modelo = modelo

    def completar_json(self, prompt: str, schema: dict[str, object]) -> str:
        interaccion: Any = self._cliente.interactions.create(
            model=self._modelo,
            input=prompt,
            response_format={
                "type": "text",
                "mime_type": "application/json",
                "schema": schema,
            },
        )
        salida = interaccion.output_text
        if not isinstance(salida, str) or not salida.strip():
            raise RuntimeError("Gemini devolvió una respuesta vacía")
        return salida
