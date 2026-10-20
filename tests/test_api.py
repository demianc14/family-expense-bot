"""HTTP de la Mini App, con TestClient y SQLite en memoria. Sin Telegram ni LLM."""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine
from sqlalchemy.pool import StaticPool

from family_expense_bot.app.api import ServiciosApp, crear_app
from family_expense_bot.dashboard.presupuesto import evaluar_gasto, resumen_presupuesto
from family_expense_bot.extraction.categorias import cargar_taxonomia
from family_expense_bot.extraction.schema import Confianza, GastoExtraido
from family_expense_bot.storage.models import Base
from family_expense_bot.storage.repository import RepositorioExtracciones, RepositorioGastos
from tests.test_auth_webapp import TOKEN, init_data

TAXONOMIA = cargar_taxonomia()
HOY = date(2026, 10, 20)
AHORA = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
DEMIAN, MAMA, EXTRANO = 42, 43, 99


@pytest.fixture
def engine() -> Engine:
    # Una sola conexión: TestClient corre las rutas en otro hilo y "sqlite://" es por conexión.
    e = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(e)
    return e


@pytest.fixture
def repo(engine: Engine) -> RepositorioGastos:
    return RepositorioGastos(engine)


@pytest.fixture
def cliente(repo: RepositorioGastos) -> TestClient:
    servicios = ServiciosApp(
        repositorio=repo,
        taxonomia=TAXONOMIA,
        bot_token=TOKEN,
        usuarios={DEMIAN: "demian", MAMA: "mama"},
        ingresos={"demian": Decimal("1200")},
        hoy=lambda: HOY,
        ahora=lambda: AHORA,
    )
    return TestClient(crear_app(servicios))


def _como(id_telegram: int) -> dict[str, str]:
    return {"Authorization": "tma " + init_data(id_telegram, firmado_en=AHORA)}


def _gasto(**cambios: Any) -> dict[str, Any]:
    return {
        "monto": "12.50",
        "categoria": "comida_fuera",
        "fecha": HOY.isoformat(),
        "atribuido_a": "demian",
        "descripcion": "almuerzo",
    } | cambios


def _ya_guardado(repo: RepositorioGastos, monto: str, categoria: str) -> None:
    repo.guardar(
        GastoExtraido(
            monto=Decimal(monto),
            categoria=categoria,
            fecha=date(2026, 10, 5),
            atribuido_a="demian",
            descripcion="x",
            texto_original="x",
            remitente="demian",
            confianza_monto=Confianza.ALTA,
            confianza_categoria=Confianza.ALTA,
            confianza_fecha=Confianza.ALTA,
            confianza_atribuido_a=Confianza.ALTA,
        ),
        registrado_por=DEMIAN,
    )


# --- quién entra -----------------------------------------------------------------


@pytest.mark.parametrize("ruta", ["/api/opciones", "/api/presupuesto"])
def test_sin_initdata_no_entra(cliente: TestClient, ruta: str) -> None:
    assert cliente.get(ruta).status_code == 401
    assert cliente.get(ruta, headers={"Authorization": "Bearer x"}).status_code == 401


def test_initdata_falso_no_entra(cliente: TestClient) -> None:
    falso = init_data(DEMIAN, firmado_en=AHORA, token="999:otro-bot")
    r = cliente.get("/api/opciones", headers={"Authorization": "tma " + falso})
    assert r.status_code == 401


def test_un_id_fuera_de_la_allowlist_no_entra(cliente: TestClient) -> None:
    assert cliente.get("/api/opciones", headers=_como(EXTRANO)).status_code == 403


def test_guardar_sin_identidad_no_escribe(cliente: TestClient, repo: RepositorioGastos) -> None:
    r = cliente.post("/api/gastos", json=_gasto(confirmado=True))
    assert r.status_code == 401
    assert repo.listar() == []


def test_la_pagina_se_sirve_sin_identidad(cliente: TestClient) -> None:
    """El HTML no tiene datos: los pide después, con el initData."""
    r = cliente.get("/")
    assert r.status_code == 200
    assert "telegram-web-app.js" in r.text


# --- opciones y presupuesto --------------------------------------------------------


def test_opciones_solo_trae_categorias_activas(cliente: TestClient) -> None:
    datos = cliente.get("/api/opciones", headers=_como(DEMIAN)).json()
    nombres = [c["nombre"] for c in datos["categorias"]]
    assert "salud" not in nombres and "servicios" not in nombres
    assert nombres == list(TAXONOMIA.nombres)
    assert datos["rol"] == "demian"
    assert datos["tiene_presupuesto"] is True
    assert [b["nombre"] for b in datos["bolsillos_con_marcador"]] == ["fondo"]


def test_presupuesto_coincide_con_resumen_presupuesto(
    cliente: TestClient, repo: RepositorioGastos
) -> None:
    _ya_guardado(repo, "40", "comida_fuera")
    datos = cliente.get("/api/presupuesto", headers=_como(DEMIAN)).json()
    esperado = resumen_presupuesto(repo.listar(), TAXONOMIA, "demian", Decimal("1200"), 2026, 10)
    assert [b["disponible"] for b in datos["bolsillos"]] == [
        f"{b.disponible:.2f}" for b in esperado.bolsillos
    ]
    lujos = next(b for b in datos["bolsillos"] if b["nombre"] == "lujos")
    assert lujos == {
        "nombre": "lujos",
        "etiqueta": "Lujos",
        "porcentaje": 20,
        "se_gasta": True,
        "asignado": "240.00",
        "gastado": "40.00",
        "disponible": "200.00",
        "excedido": False,
        "cantidad": 1,
    }
    assert datos["dias_restantes"] == 11


def test_sin_ingreso_no_hay_presupuesto(cliente: TestClient) -> None:
    r = cliente.get("/api/presupuesto", headers=_como(MAMA))
    assert r.status_code == 409


# --- ¿puedo? -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("monto", "categoria", "bolsillo"),
    [
        ("30", "entretenimiento", None),
        ("150", "entretenimiento", None),
        ("230", "entretenimiento", None),
        ("230", "entretenimiento", "fondo"),
        ("10", "otros", None),
    ],
)
def test_puedo_da_el_mismo_veredicto_que_evaluar_gasto(
    cliente: TestClient,
    repo: RepositorioGastos,
    monto: str,
    categoria: str,
    bolsillo: str | None,
) -> None:
    _ya_guardado(repo, "40", "comida_fuera")
    r = cliente.post(
        "/api/puedo",
        json={"monto": monto, "categoria": categoria, "bolsillo": bolsillo},
        headers=_como(DEMIAN),
    )
    assert r.status_code == 200
    resumen = resumen_presupuesto(repo.listar(), TAXONOMIA, "demian", Decimal("1200"), 2026, 10)
    destino = TAXONOMIA.bolsillo_de(categoria, "del fondo" if bolsillo else "")
    esperado = evaluar_gasto(resumen, TAXONOMIA, destino, Decimal(monto), HOY)
    assert r.json()["veredicto"] == esperado.veredicto.value


def test_puedo_no_guarda_nada(cliente: TestClient, engine: Engine) -> None:
    cliente.post("/api/puedo", json={"monto": "30", "categoria": "ropa"}, headers=_como(DEMIAN))
    assert RepositorioGastos(engine).listar() == []
    assert RepositorioExtracciones(engine).listar(datetime.min.replace(tzinfo=UTC), AHORA) == []


def test_puedo_rechaza_un_monto_mal_escrito(cliente: TestClient) -> None:
    r = cliente.post(
        "/api/puedo", json={"monto": "treinta", "categoria": "ropa"}, headers=_como(DEMIAN)
    )
    assert r.status_code == 422
    assert "no es un monto" in r.json()["detail"]


# --- registrar ---------------------------------------------------------------------


def test_previsualizar_no_escribe_nada(cliente: TestClient, repo: RepositorioGastos) -> None:
    r = cliente.post("/api/gastos/previsualizar", json=_gasto(), headers=_como(DEMIAN))
    assert r.status_code == 200
    assert r.json() == {
        "gasto": {
            "monto": "12.50",
            "categoria": "comida_fuera",
            "fecha": "2026-10-20",
            "atribuido_a": "demian",
            "de": "Demian",
            "descripcion": "almuerzo",
        },
        "bolsillo": "Lujos",
    }
    assert repo.listar() == []


def test_guardar_exige_la_confirmacion(cliente: TestClient, repo: RepositorioGastos) -> None:
    for cuerpo in (_gasto(), _gasto(confirmado=False)):
        assert cliente.post("/api/gastos", json=cuerpo, headers=_como(DEMIAN)).status_code == 422
    assert repo.listar() == []


def test_guardar_marca_el_origen_y_no_toca_la_calidad(cliente: TestClient, engine: Engine) -> None:
    r = cliente.post("/api/gastos", json=_gasto(confirmado=True), headers=_como(DEMIAN))
    assert r.status_code == 201
    assert r.json()["linea"] == "Lujos de octubre: quedan $227.50 de $240.00."

    [guardado] = RepositorioGastos(engine).listar()
    assert guardado.origen == "formulario"
    assert guardado.registrado_por == DEMIAN
    assert guardado.monto == Decimal("12.50")
    # Nadie leyó nada con el LLM: no es un intento para el reporte de calidad.
    assert RepositorioExtracciones(engine).listar(datetime.min.replace(tzinfo=UTC), AHORA) == []


def test_guardar_revalida_todo(cliente: TestClient, repo: RepositorioGastos) -> None:
    """No confía en el preview: un cuerpo armado a mano pasa por las mismas reglas."""
    r = cliente.post(
        "/api/gastos",
        json=_gasto(categoria="salud", confirmado=True),
        headers=_como(DEMIAN),
    )
    assert r.status_code == 422
    assert repo.listar() == []


def test_un_gasto_de_otra_persona_no_dice_bolsillo(cliente: TestClient) -> None:
    r = cliente.post(
        "/api/gastos", json=_gasto(atribuido_a="casa", confirmado=True), headers=_como(MAMA)
    )
    assert r.status_code == 201
    assert r.json()["linea"] is None


def test_si_falla_el_guardado_se_avisa(
    cliente: TestClient, repo: RepositorioGastos, monkeypatch: pytest.MonkeyPatch
) -> None:
    def romper(*_: object, **__: object) -> None:
        raise RuntimeError("disco lleno")

    monkeypatch.setattr(repo, "guardar", romper)
    r = cliente.post("/api/gastos", json=_gasto(confirmado=True), headers=_como(DEMIAN))
    assert r.status_code == 500
    assert "no quedó registrado" in r.json()["detail"]
