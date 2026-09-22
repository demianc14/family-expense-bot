"""Tests del cliente de Gemini. No sale a la red: el SDK está falseado."""

from types import SimpleNamespace
from typing import Any

import pytest

from family_expense_bot.extraction.gemini import TIMEOUT_MS, ErrorDelLLM, GeminiCliente


@pytest.fixture
def sdk_falso(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Reemplaza google.genai.Client y guarda con qué lo llamaron."""
    registro: dict[str, Any] = {}

    def client_falso(**kwargs: Any) -> Any:
        registro["kwargs"] = kwargs
        return SimpleNamespace(
            interactions=SimpleNamespace(create=lambda **k: registro["respuesta"])
        )

    monkeypatch.setattr("google.genai.Client", client_falso)
    return registro


def test_configura_timeout(sdk_falso: dict[str, Any]) -> None:
    GeminiCliente("fake-key")
    assert sdk_falso["kwargs"]["http_options"].timeout == TIMEOUT_MS


def test_devuelve_el_texto_de_la_respuesta(sdk_falso: dict[str, Any]) -> None:
    sdk_falso["respuesta"] = SimpleNamespace(output_text='{"monto": "15"}')
    assert GeminiCliente("fake-key").completar_json("prompt", {}) == '{"monto": "15"}'


@pytest.mark.parametrize("vacia", ["", "   ", None])
def test_respuesta_vacia_falla_explicito(sdk_falso: dict[str, Any], vacia: str | None) -> None:
    sdk_falso["respuesta"] = SimpleNamespace(output_text=vacia)
    with pytest.raises(ErrorDelLLM):
        GeminiCliente("fake-key").completar_json("prompt", {})


def test_error_del_sdk_se_traduce(monkeypatch: pytest.MonkeyPatch) -> None:
    def client_que_falla(**kwargs: Any) -> Any:
        def explota(**k: Any) -> Any:
            raise TimeoutError("deadline exceeded")

        return SimpleNamespace(interactions=SimpleNamespace(create=explota))

    monkeypatch.setattr("google.genai.Client", client_que_falla)
    with pytest.raises(ErrorDelLLM, match="no respondió"):
        GeminiCliente("fake-key").completar_json("prompt", {})
