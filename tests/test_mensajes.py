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


# --- presupuesto ---------------------------------------------------------------


def test_reporte_de_presupuesto() -> None:
    from datetime import UTC, date, datetime
    from decimal import Decimal

    from family_expense_bot.dashboard.presupuesto import resumen_presupuesto
    from family_expense_bot.extraction.categorias import cargar_taxonomia
    from family_expense_bot.storage.repository import GastoRegistrado

    def gasto(monto: str, categoria: str, id: int) -> GastoRegistrado:
        return GastoRegistrado(
            id=id,
            monto=Decimal(monto),
            categoria=categoria,
            atribuido_a="demian",
            fecha=date(2026, 10, 3),
            descripcion="x",
            texto_original="x",
            registrado_por=1,
            creado_en=datetime(2026, 10, 3, tzinfo=UTC),
            corregido_manualmente=False,
            anulado=False,
        )

    resumen = resumen_presupuesto(
        [
            gasto("120.50", "alimentacion", 1),
            gasto("250", "comida_fuera", 2),
            gasto("15", "otros", 3),
        ],
        cargar_taxonomia(),
        "demian",
        Decimal("1200"),
        2026,
        10,
    )
    assert mensajes.presupuesto(resumen) == (
        "Presupuesto de octubre 2026\n"
        "Ingreso: $1,200.00\n"
        "\n"
        "Ahorro (30%): aparta $360.00\n"
        "Necesarios (30%): $120.50 de $360.00 · quedan $239.50\n"
        "Fondo personal (20%): $0.00 de $240.00 · quedan $240.00\n"
        "Lujos (20%): $250.00 de $240.00 · ⚠️ te pasaste $10.00\n"
        "\n"
        "Sin bolsillo: $15.00 (otros)"
    )
    lujos = resumen.bolsillo("lujos")
    assert mensajes.linea_de_bolsillo(lujos, 10) == (
        "⚠️ Lujos de octubre: te pasaste $10.00 (llevas $250.00 de $240.00)."
    )
    assert mensajes.linea_de_bolsillo(resumen.bolsillo("necesarios"), 10) == (
        "Necesarios de octubre: quedan $239.50 de $360.00."
    )
    assert "ningún bolsillo" in mensajes.linea_de_bolsillo(None, 10)


def test_respuestas_a_una_consulta() -> None:
    from datetime import date
    from decimal import Decimal

    from family_expense_bot.dashboard.presupuesto import evaluar_gasto, resumen_presupuesto
    from family_expense_bot.extraction.categorias import cargar_taxonomia

    taxonomia = cargar_taxonomia()
    vacio = resumen_presupuesto([], taxonomia, "demian", Decimal("1200"), 2026, 10)

    def texto(monto: str, bolsillo: str | None, dia: int, dudosa: bool = False) -> str:
        consulta = evaluar_gasto(vacio, taxonomia, bolsillo, Decimal(monto), date(2026, 10, dia))
        return mensajes.respuesta_consulta(consulta, "entretenimiento", dudosa)

    pie = "No guardé nada. Si decides hacerlo, escríbemelo como gasto."
    assert texto("30", "lujos", 16) == (
        "✅ Sí, te alcanza. En Lujos te quedan $240.00 de $240.00; con este gasto quedarías "
        "con $210.00, y a los 15 días que faltan del mes les tocan $116.13.\n\n" + pie
    )
    assert texto("235", "lujos", 30) == (
        "⏸️ Mejor difiérelo. Te alcanza, pero en Lujos quedarías con $5.00, y al día que "
        "falta del mes le tocan $7.74.\n\n" + pie
    )
    assert texto("239", "lujos", 31) == (
        "✅ Sí, te alcanza. En Lujos te quedan $240.00 de $240.00; con este gasto quedarías "
        "con $1.00.\n\n" + pie
    )
    assert texto("241", "lujos", 16) == (
        "❌ No te lo recomiendo: en Lujos te quedan $240.00 y el gasto es de $241.00; te "
        "pasarías por $1.00.\n\n" + pie
    )
    assert texto("10", None, 16, dudosa=True) == (
        "Un gasto de entretenimiento no cuenta en ningún bolsillo de tu presupuesto, así que "
        "no tengo con qué compararlo.\n\n"
        "❓ Lo tomé como entretenimiento; si es otra cosa, la respuesta cambia.\n\n" + pie
    )


def test_la_consulta_sugiere_el_fondo_cuando_no_cabe() -> None:
    from datetime import UTC, date, datetime
    from decimal import Decimal

    from family_expense_bot.dashboard.presupuesto import evaluar_gasto, resumen_presupuesto
    from family_expense_bot.extraction.categorias import cargar_taxonomia
    from family_expense_bot.storage.repository import GastoRegistrado

    taxonomia = cargar_taxonomia()
    lujo = GastoRegistrado(
        id=1,
        monto=Decimal("230"),
        categoria="comida_fuera",
        atribuido_a="demian",
        fecha=date(2026, 10, 2),
        descripcion="x",
        texto_original="x",
        registrado_por=1,
        creado_en=datetime(2026, 10, 2, tzinfo=UTC),
        corregido_manualmente=False,
        anulado=False,
    )
    r = resumen_presupuesto([lujo], taxonomia, "demian", Decimal("1200"), 2026, 10)
    consulta = evaluar_gasto(r, taxonomia, "lujos", Decimal("30"), date(2026, 10, 16))
    assert (
        "En tu Fondo personal sí te alcanza (te quedan $240.00): si lo sacas de ahí, di "
        '"del fondo" al registrarlo.'
    ) in mensajes.respuesta_consulta(consulta, "entretenimiento", False)
