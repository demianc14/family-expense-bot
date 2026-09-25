from datetime import date
from decimal import Decimal

from family_expense_bot.bot import mensajes
from family_expense_bot.extraction.categorias import cargar_taxonomia
from family_expense_bot.extraction.schema import Confianza, GastoExtraido, Nota

ETIQUETAS = cargar_taxonomia().etiquetas


def _gasto(**kwargs: object) -> GastoExtraido:
    base: dict[str, object] = {
        "monto": Decimal("15.50"),
        "categoria": "transporte",
        "fecha": date(2026, 3, 14),
        "atribuido_a": "mama",
        "descripcion": "taxi",
        "texto_original": "15,50 de taxi ayer",
        "remitente": "mama",
        "confianza_monto": Confianza.ALTA,
        "confianza_categoria": Confianza.ALTA,
        "confianza_fecha": Confianza.ALTA,
        "confianza_atribuido_a": Confianza.ALTA,
    }
    return GastoExtraido.model_validate(base | kwargs)


def _lineas(texto: str) -> dict[str, str]:
    return {linea.split(":")[0]: linea for linea in texto.splitlines() if ":" in linea}


def test_muestra_el_gasto_en_formato_legible() -> None:
    texto = mensajes.resumen(_gasto(), ETIQUETAS)
    assert "$15.50" in texto
    assert "14/03/2026" in texto  # día/mes/año, como se lee en Ecuador
    assert "transporte" in texto
    assert "De: Mamá" in texto  # la etiqueta, no el nombre interno


def test_marca_solo_los_campos_dudosos() -> None:
    lineas = _lineas(mensajes.resumen(_gasto(confianza_categoria=Confianza.BAJA)))
    assert mensajes.DUDOSO in lineas["Categoría"]
    assert mensajes.DUDOSO not in lineas["Monto"]
    assert mensajes.DUDOSO not in lineas["De"]


def test_la_atribucion_asumida_se_marca_como_duda() -> None:
    lineas = _lineas(mensajes.resumen(_gasto(confianza_atribuido_a=Confianza.MEDIA)))
    assert mensajes.DUDOSO in lineas["De"]


def test_un_campo_corregido_se_marca_distinto_y_pierde_sus_notas() -> None:
    """Después del toque de la persona ya no hay nada que dudar ahí."""
    gasto = _gasto(
        atribuido_a="casa",
        confianza_atribuido_a=Confianza.MEDIA,
        notas=(Nota(campo="atribuido_a", texto="asumí que es tuyo"),),
    )
    texto = mensajes.resumen(gasto, ETIQUETAS, corregidos={"atribuido_a"})

    assert mensajes.CORREGIDO in _lineas(texto)["De"]
    assert mensajes.DUDOSO not in _lineas(texto)["De"]
    assert "asumí que es tuyo" not in texto


def test_las_notas_de_otros_campos_siguen_visibles_tras_corregir() -> None:
    gasto = _gasto(
        notas=(
            Nota(campo="atribuido_a", texto="asumí que es tuyo"),
            Nota(campo="fecha", texto="se asume hoy"),
        )
    )
    texto = mensajes.resumen(gasto, ETIQUETAS, corregidos={"atribuido_a"})
    assert "se asume hoy" in texto


def test_separadores_de_miles_en_montos_grandes() -> None:
    assert "$1,200.00" in mensajes.resumen(_gasto(monto=Decimal("1200")))


def test_sin_monto_no_invita_a_guardar() -> None:
    texto = mensajes.falta_monto(_gasto(monto=None, confianza_monto=Confianza.BAJA))
    assert "no puedo guardarlo" in texto
    assert "¿Lo guardo?" not in texto


def test_pedir_confirmacion_pregunta_y_explica_los_botones() -> None:
    texto = mensajes.pedir_confirmacion(_gasto())
    assert "¿Lo guardo?" in texto
    assert "tócalo abajo" in texto
