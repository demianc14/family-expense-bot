from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from family_expense_bot.extraction.categorias import Taxonomia, cargar_taxonomia, normalizar

TAXONOMIA = cargar_taxonomia()

ATRIBUCIONES_MINIMAS: list[dict[str, Any]] = [
    {"nombre": "papa", "etiqueta": "Papá", "descripcion": "x"},
    {
        "nombre": "casa",
        "etiqueta": "Casa",
        "descripcion": "x",
        "compartida": True,
        "marcadores": ["de la casa"],
    },
]


def _taxonomia(**cambios: Any) -> Taxonomia:
    base: dict[str, Any] = {
        "fallback": "otros",
        "categorias": [{"nombre": "otros", "descripcion": "x"}],
        "atribuciones": ATRIBUCIONES_MINIMAS,
    }
    return Taxonomia.model_validate(base | cambios)


# --- la taxonomía real del repo ---------------------------------------------


def test_la_taxonomia_real_carga_con_las_dos_dimensiones() -> None:
    assert "alimentacion" in TAXONOMIA.nombres
    assert TAXONOMIA.fallback in TAXONOMIA.nombres
    assert set(TAXONOMIA.personas) == {"papa", "mama", "hermana", "demian"}
    assert TAXONOMIA.compartida.nombre == "casa"


def test_salud_y_servicios_son_candidatas_inactivas() -> None:
    """Propuestas sin confirmar con la familia: no reciben datos todavía."""
    assert set(TAXONOMIA.candidatas) == {"salud", "servicios"}
    assert not TAXONOMIA.contiene("salud")
    assert not TAXONOMIA.contiene("servicios")


def test_la_casa_no_es_una_persona() -> None:
    """La compartida nunca escribe mensajes, así que nunca es remitente."""
    assert "casa" not in TAXONOMIA.personas
    assert TAXONOMIA.contiene_atribucion("casa")


def test_cada_atribucion_tiene_etiqueta_para_los_botones() -> None:
    assert set(TAXONOMIA.etiquetas) == set(TAXONOMIA.nombres_atribucion)


def test_contiene_normaliza_mayusculas_y_espacios() -> None:
    assert TAXONOMIA.contiene(" Alimentacion ")
    assert not TAXONOMIA.contiene("regalos")
    assert not TAXONOMIA.contiene(None)
    assert TAXONOMIA.contiene_atribucion(" Mama ")
    assert not TAXONOMIA.contiene_atribucion("tia")


def test_describir_para_prompt_incluye_solo_las_activas() -> None:
    descripcion = TAXONOMIA.describir_para_prompt()
    for nombre in TAXONOMIA.nombres:
        assert f"- {nombre}:" in descripcion
    for candidata in TAXONOMIA.candidatas:
        assert f"- {candidata}:" not in descripcion


# --- marcadores de gasto compartido ------------------------------------------


@pytest.mark.parametrize(
    "texto",
    [
        "80 de gasolina, gasto de la casa",
        "60 de gasolina, gasto comun",  # sin tilde
        "60 de gasolina, gasto COMÚN",  # mayúsculas
        "pensión, compartida",
        "40 del súper para la casa",
    ],
)
def test_detecta_gastos_compartidos_explicitos(texto: str) -> None:
    assert TAXONOMIA.menciona_gasto_compartido(texto)


@pytest.mark.parametrize(
    "texto",
    [
        "300 de pensión",
        "llegué a casa en taxi, 5",  # "casa" suelta no es un marcador
        "50 regalo de primera comunión",  # "comunión" no es "común"
        "45 regalo de casamiento",
    ],
)
def test_no_confunde_palabras_parecidas_con_un_marcador(texto: str) -> None:
    assert not TAXONOMIA.menciona_gasto_compartido(texto)


def test_normalizar_quita_tildes_y_mayusculas() -> None:
    assert normalizar("Común ÉXITO") == "comun exito"


# --- validaciones: fallar al cargar, no al usar -------------------------------


def test_falla_si_el_fallback_no_es_una_categoria_activa() -> None:
    with pytest.raises(ValidationError, match="fallback"):
        _taxonomia(
            fallback="otros",
            categorias=[
                {"nombre": "comida", "descripcion": "x"},
                {"nombre": "otros", "descripcion": "x", "candidata": True},
            ],
        )


def test_falla_si_no_hay_categorias() -> None:
    with pytest.raises(ValidationError):
        _taxonomia(categorias=[])


def test_falla_con_categorias_repetidas() -> None:
    with pytest.raises(ValidationError, match="repetida"):
        _taxonomia(
            categorias=[
                {"nombre": "otros", "descripcion": "x"},
                {"nombre": "otros", "descripcion": "y"},
            ]
        )


def test_falla_sin_atribucion_compartida() -> None:
    with pytest.raises(ValidationError, match="compartida"):
        _taxonomia(atribuciones=[ATRIBUCIONES_MINIMAS[0]])


def test_falla_con_dos_atribuciones_compartidas() -> None:
    otra = ATRIBUCIONES_MINIMAS[1] | {"nombre": "fondo"}
    with pytest.raises(ValidationError, match="compartida"):
        _taxonomia(atribuciones=[*ATRIBUCIONES_MINIMAS, otra])


def test_falla_si_la_compartida_no_tiene_marcadores() -> None:
    """Sin marcadores, la casa nunca podría aceptarse con confianza."""
    sin_marcadores = ATRIBUCIONES_MINIMAS[1] | {"marcadores": []}
    with pytest.raises(ValidationError, match="marcadores"):
        _taxonomia(atribuciones=[ATRIBUCIONES_MINIMAS[0], sin_marcadores])


def test_falla_si_no_hay_ninguna_persona() -> None:
    with pytest.raises(ValidationError, match="persona"):
        _taxonomia(atribuciones=[ATRIBUCIONES_MINIMAS[1]])


def test_falla_si_el_archivo_no_existe(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        cargar_taxonomia(tmp_path / "no_existe.yaml")


# --- parentescos ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("remitente", "texto", "persona"),
    [
        ("mama", "mi hija se compró ropa", "hermana"),
        ("papa", "mi hija se compró ropa", "hermana"),
        ("demian", "mi hermana se compró ropa", "hermana"),
        ("demian", "mi ñaña se compró ropa", "hermana"),
        ("demian", "mi nana se compró ropa", "hermana"),  # sin la tilde de la ñ
        ("mama", "mi esposo pagó los zapatos", "papa"),
        ("hermana", "mi ñaño pagó el cine", "demian"),
    ],
)
def test_reconoce_parentescos_desde_quien_escribe(remitente: str, texto: str, persona: str) -> None:
    assert TAXONOMIA.menciona_a(persona, remitente, texto)


def test_mi_hermana_desde_mama_no_es_la_hija() -> None:
    """El caso que motivó el mapa: la hermana de mamá es una tía."""
    assert not TAXONOMIA.menciona_a("hermana", "mama", "mi hermana se compró ropa")


@pytest.mark.parametrize(
    ("cita", "remitente", "personas"),
    [
        ("mi hija", "mama", ["hermana"]),
        ("mi hija", "demian", []),  # demian no tiene hijos en la lista
        ("mi hermana", "demian", ["hermana"]),
        ("mi hermana", "mama", []),  # la tía
        ("lo pagó mi esposo", "mama", ["papa"]),
        ("mi hijo y mi hija", "papa", ["hermana", "demian"]),
    ],
)
def test_personas_en_resuelve_una_cita_desde_quien_escribe(
    cita: str, remitente: str, personas: list[str]
) -> None:
    assert sorted(TAXONOMIA.personas_en(cita, remitente)) == sorted(personas)


@pytest.mark.parametrize("cita", ["casa", "Casa", "gasto de la casa", "compartido"])
def test_es_compartida_reconoce_el_nombre_y_los_marcadores(cita: str) -> None:
    assert TAXONOMIA.es_compartida(cita)


def test_falla_si_una_expresion_apunta_a_dos_personas() -> None:
    ambiguas = [
        {
            "nombre": "papa",
            "etiqueta": "P",
            "descripcion": "x",
            "como_lo_llaman": {"hijo": ["mi viejo"]},
        },
        {
            "nombre": "abuelo",
            "etiqueta": "A",
            "descripcion": "x",
            "como_lo_llaman": {"hijo": ["mi viejo"]},
        },
        {"nombre": "hijo", "etiqueta": "H", "descripcion": "x"},
        ATRIBUCIONES_MINIMAS[1],
    ]
    with pytest.raises(ValidationError, match="mi viejo"):
        _taxonomia(atribuciones=ambiguas)


def test_falla_si_un_parentesco_usa_un_remitente_que_no_existe() -> None:
    con_tio = [
        ATRIBUCIONES_MINIMAS[0] | {"como_lo_llaman": {"tio": ["mi hermano"]}},
        ATRIBUCIONES_MINIMAS[1],
    ]
    with pytest.raises(ValidationError, match="tio"):
        _taxonomia(atribuciones=con_tio)
