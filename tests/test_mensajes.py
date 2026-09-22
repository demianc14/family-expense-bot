from datetime import date
from decimal import Decimal

from family_expense_bot.bot import mensajes
from family_expense_bot.extraction.schema import Confianza, GastoExtraido


def _gasto(**kwargs: object) -> GastoExtraido:
    base: dict[str, object] = {
        "monto": Decimal("15.50"),
        "categoria": "transporte",
        "fecha": date(2026, 3, 14),
        "descripcion": "taxi",
        "texto_original": "15,50 de taxi ayer",
        "confianza_monto": Confianza.ALTA,
        "confianza_categoria": Confianza.ALTA,
        "confianza_fecha": Confianza.ALTA,
    }
    return GastoExtraido.model_validate(base | kwargs)


def test_muestra_el_gasto_en_formato_legible() -> None:
    texto = mensajes.resumen(_gasto())
    assert "$15.50" in texto
    assert "14/03/2026" in texto  # día/mes/año, como se lee en Ecuador
    assert "transporte" in texto


def test_marca_solo_los_campos_dudosos() -> None:
    texto = mensajes.resumen(_gasto(confianza_categoria=Confianza.BAJA))
    lineas = {linea.split(":")[0]: linea for linea in texto.splitlines() if ":" in linea}

    assert mensajes.DUDOSO in lineas["Categoría"]
    assert mensajes.DUDOSO not in lineas["Monto"]
    assert mensajes.DUDOSO not in lineas["Fecha"]


def test_muestra_las_notas_que_explican_la_duda() -> None:
    texto = mensajes.resumen(_gasto(notas=("El monto 50 no aparece en el mensaje original.",)))
    assert "no aparece en el mensaje original" in texto


def test_separadores_de_miles_en_montos_grandes() -> None:
    assert "$1,200.00" in mensajes.resumen(_gasto(monto=Decimal("1200")))


def test_sin_monto_no_invita_a_guardar() -> None:
    texto = mensajes.falta_monto(_gasto(monto=None, confianza_monto=Confianza.BAJA))
    assert "no puedo guardarlo" in texto
    assert "¿Lo guardo?" not in texto


def test_pedir_confirmacion_pregunta_explicitamente() -> None:
    assert mensajes.pedir_confirmacion(_gasto()).endswith("¿Lo guardo?")
