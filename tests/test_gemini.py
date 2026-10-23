"""Tests del cliente de Gemini. No sale a la red: el SDK está falseado."""

from types import SimpleNamespace
from typing import Any

import pytest

from family_expense_bot.extraction.gemini import (
    INTENTOS,
    REINTENTABLES,
    TIMEOUT_MS,
    CuotaAgotada,
    ErrorDelLLM,
    GeminiCliente,
)


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


def test_limita_los_reintentos(sdk_falso: dict[str, Any]) -> None:
    """Ante un 429, reintentar con backoff solo alarga la espera de la persona."""
    GeminiCliente("fake-key")
    assert sdk_falso["kwargs"]["http_options"].retry_options.attempts == INTENTOS


def test_no_reintenta_la_cuota_agotada(sdk_falso: dict[str, Any]) -> None:
    """Con 429 el SDK duerme lo que diga el Retry-After, que pueden ser horas."""
    GeminiCliente("fake-key")
    codigos = sdk_falso["kwargs"]["http_options"].retry_options.http_status_codes
    assert 429 not in codigos
    assert codigos == list(REINTENTABLES)


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


@pytest.mark.parametrize(
    "mensaje",
    [
        "Error code: 429 - {'error': {'message': 'Rate limit exceeded'}}",
        "RESOURCE_EXHAUSTED: quota exceeded",
    ],
)
def test_la_cuota_agotada_se_distingue_del_resto(
    monkeypatch: pytest.MonkeyPatch, mensaje: str
) -> None:
    """El bot dice "vuelve mañana", no "no te entendí": son problemas distintos."""

    def client_con_429(**kwargs: Any) -> Any:
        def explota(**k: Any) -> Any:
            raise RuntimeError(mensaje)

        return SimpleNamespace(interactions=SimpleNamespace(create=explota))

    monkeypatch.setattr("google.genai.Client", client_con_429)
    with pytest.raises(CuotaAgotada):
        GeminiCliente("fake-key").completar_json("prompt", {})


def test_un_error_cualquiera_no_se_confunde_con_cuota(monkeypatch: pytest.MonkeyPatch) -> None:
    def client_roto(**kwargs: Any) -> Any:
        def explota(**k: Any) -> Any:
            raise RuntimeError("connection reset")

        return SimpleNamespace(interactions=SimpleNamespace(create=explota))

    monkeypatch.setattr("google.genai.Client", client_roto)
    with pytest.raises(ErrorDelLLM) as e:
        GeminiCliente("fake-key").completar_json("prompt", {})
    assert not isinstance(e.value, CuotaAgotada)
