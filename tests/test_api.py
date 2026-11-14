"""HTTP de la Mini App, con TestClient y SQLite en memoria. Sin Telegram ni LLM."""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine
from sqlalchemy.pool import StaticPool

from family_expense_bot.app.api import ServiciosApp, crear_app
from family_expense_bot.bot import envio
from family_expense_bot.dashboard.presupuesto import evaluar_gasto, resumen_presupuesto
from family_expense_bot.extraction.categorias import cargar_taxonomia
from family_expense_bot.extraction.schema import Confianza, GastoExtraido
from family_expense_bot.storage.models import Base
from family_expense_bot.storage.repository import (
    RepositorioExtracciones,
    RepositorioGastos,
    RepositorioIngresos,
    activar_llaves_foraneas,
)
from tests.test_auth_webapp import TOKEN, init_data

TAXONOMIA = cargar_taxonomia()
HOY = date(2026, 10, 20)
AHORA = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
DEMIAN, MAMA, EXTRANO = 42, 43, 99


@pytest.fixture
def engine() -> Engine:
    # Una sola conexión: TestClient corre las rutas en otro hilo y "sqlite://" es por conexión.
    e = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    activar_llaves_foraneas(e)  # igual que en producción (`crear_engine`)
    Base.metadata.create_all(e)
    return e


def test_la_api_corre_con_las_llaves_foraneas_activas(engine: Engine) -> None:
    with engine.connect() as con:
        assert con.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1


@pytest.fixture
def repo(engine: Engine) -> RepositorioGastos:
    return RepositorioGastos(engine)


@pytest.fixture
def repo_ingresos(engine: Engine) -> RepositorioIngresos:
    return RepositorioIngresos(engine)


@pytest.fixture
def avisos() -> list[tuple[int, str]]:
    """Lo que la app mandó al chat de Telegram."""
    return []


@pytest.fixture
def cliente(
    repo: RepositorioGastos,
    repo_ingresos: RepositorioIngresos,
    avisos: list[tuple[int, str]],
) -> TestClient:
    servicios = ServiciosApp(
        repositorio=repo,
        ingresos_extra=repo_ingresos,
        taxonomia=TAXONOMIA,
        bot_token=TOKEN,
        usuarios={DEMIAN: "demian", MAMA: "mama"},
        ingresos={"demian": Decimal("1200")},
        hoy=lambda: HOY,
        ahora=lambda: AHORA,
        notificar=lambda chat_id, texto: avisos.append((chat_id, texto)),
    )
    return TestClient(crear_app(servicios))


def _como(id_telegram: int) -> dict[str, str]:
    return {"Authorization": "tma " + init_data(id_telegram, firmado_en=AHORA)}


def _gasto(**cambios: Any) -> dict[str, Any]:
    return {
        "monto": "12.50",
        "categoria": "comida_fuera",
        "fecha": HOY.isoformat(),
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


@pytest.mark.parametrize("ruta", ["/api/opciones", "/api/presupuesto", "/api/gastos"])
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
    ropa = next(c for c in datos["categorias"] if c["nombre"] == "ropa")
    assert (ropa["bolsillo_nombre"], ropa["bolsillo"]) == ("lujos", "Lujos")
    otros = next(c for c in datos["categorias"] if c["nombre"] == "otros")
    assert (otros["bolsillo_nombre"], otros["bolsillo"]) == (None, None)


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
        ("10", "otros", "lujos"),
        ("10", "otros", "necesarios"),
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
            "subcategoria": None,
            "fecha": "2026-10-20",
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


def test_no_se_puede_registrar_un_gasto_de_otra_persona(
    cliente: TestClient, repo: RepositorioGastos
) -> None:
    r = cliente.post(
        "/api/gastos", json=_gasto(atribuido_a="mama", confirmado=True), headers=_como(DEMIAN)
    )
    assert r.status_code == 201
    [guardado] = repo.listar()
    assert guardado.atribuido_a == "demian"


def test_sin_ingreso_el_gasto_se_guarda_sin_linea_de_bolsillo(cliente: TestClient) -> None:
    r = cliente.post("/api/gastos", json=_gasto(confirmado=True), headers=_como(MAMA))
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


# --- coma, otros y subcategoría ------------------------------------------------


def test_el_monto_con_coma_se_guarda(cliente: TestClient, repo: RepositorioGastos) -> None:
    r = cliente.post(
        "/api/gastos", json=_gasto(monto="12,50", confirmado=True), headers=_como(DEMIAN)
    )
    assert r.status_code == 201
    assert repo.listar()[0].monto == Decimal("12.50")


def test_otros_sin_bolsillo_se_rechaza(cliente: TestClient, repo: RepositorioGastos) -> None:
    for ruta, cuerpo in [
        ("/api/gastos/previsualizar", _gasto(categoria="otros")),
        ("/api/gastos", _gasto(categoria="otros", confirmado=True)),
        ("/api/puedo", {"monto": "10", "categoria": "otros"}),
    ]:
        r = cliente.post(ruta, json=cuerpo, headers=_como(DEMIAN))
        assert r.status_code == 422, ruta
        assert "bolsillo" in r.json()["detail"]
    assert repo.listar() == []


def test_otros_con_bolsillo_cuenta_contra_ese_bolsillo(cliente: TestClient) -> None:
    r = cliente.post(
        "/api/gastos/previsualizar",
        json=_gasto(categoria="otros", bolsillo="necesarios"),
        headers=_como(DEMIAN),
    )
    assert r.json()["bolsillo"] == "Necesarios"


def test_opciones_dice_que_bolsillos_puede_tener_otros(cliente: TestClient) -> None:
    datos = cliente.get("/api/opciones", headers=_como(DEMIAN)).json()
    assert datos["fallback"] == "otros"
    assert [b["nombre"] for b in datos["bolsillos_para_fallback"]] == [
        "necesarios",
        "fondo",
        "lujos",
    ]
    assert datos["subcategorias"] == {}


def test_la_subcategoria_se_guarda_y_se_sugiere(
    cliente: TestClient, repo: RepositorioGastos
) -> None:
    cuerpo = _gasto(categoria="alimentacion", subcategoria=" Tipti ", confirmado=True)
    assert cliente.post("/api/gastos", json=cuerpo, headers=_como(DEMIAN)).status_code == 201
    assert repo.listar()[0].subcategoria == "Tipti"
    datos = cliente.get("/api/opciones", headers=_como(DEMIAN)).json()
    assert datos["subcategorias"] == {"alimentacion": ["Tipti"]}
    # Las de otra persona no se le sugieren a nadie más.
    assert cliente.get("/api/opciones", headers=_como(MAMA)).json()["subcategorias"] == {}


# --- historial, editar y borrar -----------------------------------------------------


def _guardar_por_app(cliente: TestClient, **cambios: Any) -> int:
    r = cliente.post("/api/gastos", json=_gasto(confirmado=True, **cambios), headers=_como(DEMIAN))
    assert r.status_code == 201
    return int(r.json()["id"])


def test_historial_trae_solo_los_gastos_propios(
    cliente: TestClient, repo: RepositorioGastos
) -> None:
    propio = _guardar_por_app(cliente, descripcion="mío")
    cliente.post("/api/gastos", json=_gasto(confirmado=True), headers=_como(MAMA))
    # De la etapa familiar: lo registró demian, pero es de otra persona.
    ajeno = repo.guardar(
        GastoExtraido(
            monto=Decimal("5"),
            categoria="ropa",
            fecha=HOY,
            atribuido_a="mama",
            descripcion="de mamá",
            texto_original="x",
            remitente="demian",
            confianza_monto=Confianza.ALTA,
            confianza_categoria=Confianza.ALTA,
            confianza_fecha=Confianza.ALTA,
            confianza_atribuido_a=Confianza.ALTA,
        ),
        registrado_por=DEMIAN,
    )
    gastos = cliente.get("/api/gastos", headers=_como(DEMIAN)).json()["gastos"]
    assert [g["id"] for g in gastos] == [propio]
    assert ajeno.id not in [g["id"] for g in gastos]


def test_historial_dice_que_bolsillo_eligio_la_persona(cliente: TestClient) -> None:
    _guardar_por_app(cliente, categoria="otros", bolsillo="lujos", descripcion="cosa")
    _guardar_por_app(cliente, categoria="entretenimiento", bolsillo="fondo", descripcion="cine")
    _guardar_por_app(cliente, categoria="ropa", descripcion="camisa")
    por_descripcion = {
        g["descripcion"]: g
        for g in cliente.get("/api/gastos", headers=_como(DEMIAN)).json()["gastos"]
    }
    assert por_descripcion["cosa"]["bolsillo_elegido"] == "lujos"
    assert por_descripcion["cine"]["bolsillo_elegido"] == "fondo"
    assert por_descripcion["camisa"]["bolsillo_elegido"] is None
    assert por_descripcion["camisa"]["bolsillo"] == "Lujos"


def test_editar_reemplaza_el_gasto(cliente: TestClient, repo: RepositorioGastos) -> None:
    viejo = _guardar_por_app(cliente)
    r = cliente.post(
        "/api/gastos",
        json=_gasto(monto="30", categoria="ropa", confirmado=True, reemplaza=viejo),
        headers=_como(DEMIAN),
    )
    assert r.status_code == 201
    [actual] = repo.listar()
    assert (actual.id, actual.monto, actual.categoria) == (r.json()["id"], Decimal("30"), "ropa")
    assert repo.obtener(viejo) is not None and repo.obtener(viejo).anulado  # type: ignore[union-attr]


def test_editar_sin_confirmar_no_cambia_nada(cliente: TestClient, repo: RepositorioGastos) -> None:
    viejo = _guardar_por_app(cliente)
    r = cliente.post("/api/gastos", json=_gasto(monto="30", reemplaza=viejo), headers=_como(DEMIAN))
    assert r.status_code == 422
    assert [g.id for g in repo.listar()] == [viejo]


def test_editar_un_gasto_ajeno_o_borrado_no_cambia_nada(
    cliente: TestClient, repo: RepositorioGastos
) -> None:
    de_mama = cliente.post("/api/gastos", json=_gasto(confirmado=True), headers=_como(MAMA)).json()[
        "id"
    ]
    borrado = _guardar_por_app(cliente)
    repo.anular(borrado, "x")
    for id_gasto in (de_mama, borrado, 999):
        r = cliente.post(
            "/api/gastos",
            json=_gasto(monto="1", confirmado=True, reemplaza=id_gasto),
            headers=_como(DEMIAN),
        )
        assert r.status_code == 404
    assert [g.id for g in repo.listar()] == [de_mama]


def test_editar_con_un_campo_invalido_no_anula_el_viejo(
    cliente: TestClient, repo: RepositorioGastos
) -> None:
    viejo = _guardar_por_app(cliente)
    r = cliente.post(
        "/api/gastos",
        json=_gasto(monto="1,200", confirmado=True, reemplaza=viejo),
        headers=_como(DEMIAN),
    )
    assert r.status_code == 422
    assert [g.id for g in repo.listar()] == [viejo]


def test_borrar_anula_con_confirmacion(cliente: TestClient, repo: RepositorioGastos) -> None:
    id_gasto = _guardar_por_app(cliente)
    sin_si = cliente.post(f"/api/gastos/{id_gasto}/anular", json={}, headers=_como(DEMIAN))
    assert sin_si.status_code == 422
    assert len(repo.listar()) == 1
    r = cliente.post(
        f"/api/gastos/{id_gasto}/anular", json={"confirmado": True}, headers=_como(DEMIAN)
    )
    assert r.status_code == 200
    assert repo.listar() == []
    assert repo.obtener(id_gasto) is not None  # anulado, no borrado
    otra_vez = cliente.post(
        f"/api/gastos/{id_gasto}/anular", json={"confirmado": True}, headers=_como(DEMIAN)
    )
    assert otra_vez.status_code == 404


def test_no_se_puede_borrar_un_gasto_ajeno(cliente: TestClient, repo: RepositorioGastos) -> None:
    de_mama = cliente.post("/api/gastos", json=_gasto(confirmado=True), headers=_como(MAMA)).json()[
        "id"
    ]
    r = cliente.post(
        f"/api/gastos/{de_mama}/anular", json={"confirmado": True}, headers=_como(DEMIAN)
    )
    assert r.status_code == 404
    assert [g.id for g in repo.listar()] == [de_mama]


# --- ingresos extra -------------------------------------------------------------------


def _ingreso(**cambios: Any) -> dict[str, Any]:
    return {
        "monto": "150",
        "categoria": "trabajo_extra",
        "fecha": HOY.isoformat(),
        "descripcion": "un logo",
    } | cambios


def test_previsualizar_ingreso_no_escribe(
    cliente: TestClient, repo_ingresos: RepositorioIngresos
) -> None:
    r = cliente.post("/api/ingresos/previsualizar", json=_ingreso(), headers=_como(DEMIAN))
    assert r.status_code == 200
    assert r.json()["ingreso"]["monto"] == "150.00"
    assert repo_ingresos.listar() == []


def test_un_ingreso_sin_confirmar_no_se_guarda(
    cliente: TestClient, repo_ingresos: RepositorioIngresos
) -> None:
    assert cliente.post("/api/ingresos", json=_ingreso(), headers=_como(DEMIAN)).status_code == 422
    assert repo_ingresos.listar() == []


def test_un_ingreso_sube_el_presupuesto_del_mes(cliente: TestClient) -> None:
    antes = cliente.get("/api/presupuesto", headers=_como(DEMIAN)).json()
    r = cliente.post("/api/ingresos", json=_ingreso(confirmado=True), headers=_como(DEMIAN))
    assert r.status_code == 201
    assert (
        r.json()["linea"]
        == "Tu ingreso de octubre queda en $1,350.00 y se reparte en tus bolsillos."
    )
    despues = cliente.get("/api/presupuesto", headers=_como(DEMIAN)).json()
    assert (antes["ingreso"], despues["ingreso"]) == ("1200.00", "1350.00")
    assert despues["ingreso_extra"] == "150.00"
    lujos = next(b for b in despues["bolsillos"] if b["nombre"] == "lujos")
    assert lujos["asignado"] == "270.00"  # 20% de 1350


def test_puedo_usa_el_ingreso_con_extras(cliente: TestClient) -> None:
    """Lujos tiene 240; con 150 extra pasa a 270 y un gasto de 250 ya no se pasa."""
    consulta = {"monto": "250", "categoria": "entretenimiento"}
    assert (
        cliente.post("/api/puedo", json=consulta, headers=_como(DEMIAN)).json()["veredicto"] == "no"
    )
    cliente.post("/api/ingresos", json=_ingreso(confirmado=True), headers=_como(DEMIAN))
    assert (
        cliente.post("/api/puedo", json=consulta, headers=_como(DEMIAN)).json()["veredicto"] != "no"
    )


def test_editar_y_borrar_un_ingreso(
    cliente: TestClient, repo_ingresos: RepositorioIngresos
) -> None:
    viejo = cliente.post("/api/ingresos", json=_ingreso(confirmado=True), headers=_como(DEMIAN))
    r = cliente.post(
        "/api/ingresos",
        json=_ingreso(monto="200", confirmado=True, reemplaza=viejo.json()["id"]),
        headers=_como(DEMIAN),
    )
    assert r.status_code == 201
    [actual] = repo_ingresos.listar()
    assert actual.monto == Decimal("200")
    lista = cliente.get("/api/ingresos", headers=_como(DEMIAN)).json()["ingresos"]
    assert [i["id"] for i in lista] == [actual.id]

    borrar = cliente.post(
        f"/api/ingresos/{actual.id}/anular", json={"confirmado": True}, headers=_como(DEMIAN)
    )
    assert borrar.status_code == 200
    assert repo_ingresos.listar() == []
    assert cliente.get("/api/presupuesto", headers=_como(DEMIAN)).json()["ingreso"] == "1200.00"


def test_los_ingresos_de_otra_persona_no_se_ven_ni_se_tocan(
    cliente: TestClient, repo_ingresos: RepositorioIngresos
) -> None:
    de_mama = cliente.post("/api/ingresos", json=_ingreso(confirmado=True), headers=_como(MAMA))
    id_mama = de_mama.json()["id"]
    assert cliente.get("/api/ingresos", headers=_como(DEMIAN)).json()["ingresos"] == []
    editar = cliente.post(
        "/api/ingresos", json=_ingreso(confirmado=True, reemplaza=id_mama), headers=_como(DEMIAN)
    )
    assert editar.status_code == 404
    borrar = cliente.post(
        f"/api/ingresos/{id_mama}/anular", json={"confirmado": True}, headers=_como(DEMIAN)
    )
    assert borrar.status_code == 404
    assert [i.id for i in repo_ingresos.listar()] == [id_mama]
    # Y no le suben el presupuesto a demian.
    assert cliente.get("/api/presupuesto", headers=_como(DEMIAN)).json()["ingreso"] == "1200.00"


def test_opciones_trae_las_categorias_de_ingreso(cliente: TestClient) -> None:
    datos = cliente.get("/api/opciones", headers=_como(DEMIAN)).json()
    assert [c["nombre"] for c in datos["categorias_ingreso"]] == [
        c.nombre for c in TAXONOMIA.ingresos
    ]
    assert datos["subcategorias_ingreso"] == {}


# --- alertas ---------------------------------------------------------------------
# Lujos con 1200 de ingreso: 240 asignados, el 80% son 192.


def test_cruzar_el_80_avisa_al_chat_de_quien_guarda(
    cliente: TestClient, repo: RepositorioGastos, avisos: list[tuple[int, str]]
) -> None:
    _ya_guardado(repo, "180", "comida_fuera")
    r = cliente.post("/api/gastos", json=_gasto(confirmado=True), headers=_como(DEMIAN))
    assert r.status_code == 201
    [(chat_id, texto)] = avisos
    assert chat_id == DEMIAN
    assert texto.startswith("🔔 Lujos de octubre llegó al 80%: llevas $192.50 de $240.00.")


def test_sin_cruzar_no_avisa(cliente: TestClient, avisos: list[tuple[int, str]]) -> None:
    r = cliente.post("/api/gastos", json=_gasto(confirmado=True), headers=_como(DEMIAN))
    assert r.status_code == 201
    assert avisos == []


def test_la_alerta_usa_el_presupuesto_con_ingresos_extra(
    cliente: TestClient, repo: RepositorioGastos, avisos: list[tuple[int, str]]
) -> None:
    """Con 150 extra, Lujos pasa de 240 a 270 y el 80% de 192 a 216: igual que en Bolsillos."""
    _ya_guardado(repo, "180", "comida_fuera")
    r = cliente.post("/api/ingresos", json=_ingreso(confirmado=True), headers=_como(DEMIAN))
    assert r.status_code == 201
    _guardar_por_app(cliente)  # 192.50: cruzaría el 80% de 240, no el de 270
    assert avisos == []
    _guardar_por_app(cliente, monto="25")  # 217.50 de 270.00
    [(_, texto)] = avisos
    assert texto.startswith("🔔 Lujos de octubre llegó al 80%: llevas $217.50 de $270.00.")
    presupuesto = cliente.get("/api/presupuesto", headers=_como(DEMIAN)).json()
    [lujos] = [b for b in presupuesto["bolsillos"] if b["nombre"] == "lujos"]
    assert (lujos["gastado"], lujos["asignado"]) == ("217.50", "270.00")


def test_un_reinicio_no_reenvia_una_alerta_ya_enviada(
    cliente: TestClient,
    repo: RepositorioGastos,
    repo_ingresos: RepositorioIngresos,
    avisos: list[tuple[int, str]],
) -> None:
    _ya_guardado(repo, "180", "comida_fuera")
    _guardar_por_app(cliente)
    assert len(avisos) == 1
    # Otro proceso sobre la misma base: no hay estado de alertas que perder ni que repetir.
    reiniciado = TestClient(
        crear_app(
            ServiciosApp(
                repositorio=repo,
                ingresos_extra=repo_ingresos,
                taxonomia=TAXONOMIA,
                bot_token=TOKEN,
                usuarios={DEMIAN: "demian"},
                ingresos={"demian": Decimal("1200")},
                hoy=lambda: HOY,
                ahora=lambda: AHORA,
                notificar=lambda chat_id, texto: avisos.append((chat_id, texto)),
            )
        )
    )
    assert reiniciado.get("/api/presupuesto", headers=_como(DEMIAN)).status_code == 200
    _guardar_por_app(reiniciado, monto="5")  # sigue entre el 80% y el 100%
    assert len(avisos) == 1


def test_editar_no_repite_la_alerta_pero_si_avisa_un_cruce_nuevo(
    cliente: TestClient, repo: RepositorioGastos, avisos: list[tuple[int, str]]
) -> None:
    _ya_guardado(repo, "180", "comida_fuera")
    id_gasto = _guardar_por_app(cliente)  # 192.50: cruza el 80%
    assert len(avisos) == 1
    id_gasto = _guardar_por_app(cliente, descripcion="almuerzo con Ana", reemplaza=id_gasto)
    assert len(avisos) == 1, "corregir la descripción no cambia el bolsillo"
    _guardar_por_app(cliente, monto="70", reemplaza=id_gasto)  # 250 de 240
    assert len(avisos) == 2
    assert avisos[1][1].startswith("🚨 Llegaste al tope en Lujos de octubre: llevas $250.00")


def test_si_falla_el_aviso_el_gasto_queda_guardado(
    repo: RepositorioGastos, repo_ingresos: RepositorioIngresos
) -> None:
    def romper(chat_id: int, texto: str) -> None:
        raise RuntimeError("Telegram caído")

    _ya_guardado(repo, "180", "comida_fuera")
    servicios = ServiciosApp(
        repositorio=repo,
        ingresos_extra=repo_ingresos,
        taxonomia=TAXONOMIA,
        bot_token=TOKEN,
        usuarios={DEMIAN: "demian"},
        ingresos={"demian": Decimal("1200")},
        hoy=lambda: HOY,
        ahora=lambda: AHORA,
        notificar=romper,
    )
    # El aviso corre después de responder: su error no le llega a quien guardó.
    cliente = TestClient(crear_app(servicios), raise_server_exceptions=False)
    r = cliente.post("/api/gastos", json=_gasto(confirmado=True), headers=_como(DEMIAN))
    assert r.status_code == 201
    assert len(repo.listar()) == 2


def test_enviar_no_explota_si_telegram_falla(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    class BotCaido:
        def __init__(self, token: str) -> None: ...

        async def __aenter__(self) -> "BotCaido":
            raise RuntimeError("sin red")

        async def __aexit__(self, *_: object) -> None: ...

    monkeypatch.setattr(envio, "Bot", BotCaido)
    envio.enviar(TOKEN, DEMIAN, "hola")
    assert "No se pudo mandar la alerta al chat" in caplog.text
