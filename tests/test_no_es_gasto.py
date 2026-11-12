"""La regla "no es gasto": devoluciones e ingresos no se guardan sin preguntar.

Sin LLM: la respuesta simulada siempre dice "gasto claro, confianza alta", que
es justo lo que hizo gemini con "me devolvieron 20 de una camisa".
"""

from datetime import date
from pathlib import Path
from typing import Any

import pytest
import yaml

from family_expense_bot.extraction.categorias import Taxonomia, cargar_taxonomia
from family_expense_bot.extraction.parser import NOTA_NO_ES_GASTO, extraer
from family_expense_bot.extraction.schema import Confianza
from tests.test_parser import ClienteFalso, respuesta

HOY = date(2026, 3, 15)
CASOS = yaml.safe_load((Path(__file__).parent / "fixtures" / "no_es_gasto.yaml").read_text())
TAXONOMIA = cargar_taxonomia()


def _extraer(caso: dict[str, Any], taxonomia: Taxonomia = TAXONOMIA) -> Any:
    crudo = respuesta(monto=caso["monto"], categoria=caso["categoria"])
    return extraer(caso["texto"], ClienteFalso(crudo), taxonomia, caso["remitente"], HOY)


@pytest.mark.parametrize("caso", CASOS, ids=[c["id"] for c in CASOS])
def test_la_regla_marca_lo_que_dice_el_fixture(caso: dict[str, Any]) -> None:
    gasto = _extraer(caso)
    marcado = NOTA_NO_ES_GASTO in [n.texto for n in gasto.notas_de("categoria")]

    assert marcado == caso["dispara"]
    assert TAXONOMIA.parece_no_gasto(caso["texto"]) == caso["dispara"]
    if marcado:
        assert gasto.confianza("categoria") is Confianza.BAJA
        assert not gasto.listo_para_guardar
    else:
        assert gasto.confianza("categoria") is Confianza.ALTA
    # La regla pregunta; nunca toca lo que se extrajo.
    assert str(gasto.monto) == caso["monto"]
    assert gasto.categoria == caso["categoria"]


def test_cuantos_atrapa_y_cuantos_falsos_positivos() -> None:
    """Las cifras de docs/evaluacion.md. Si cambian, cambia la doc en la misma PR."""
    no_gastos = [c for c in CASOS if not c["es_gasto"]]
    gastos = [c for c in CASOS if c["es_gasto"]]
    atrapados = [c["id"] for c in no_gastos if c["dispara"]]
    falsos_positivos = [c["id"] for c in gastos if c["dispara"]]

    assert (len(atrapados), len(no_gastos)) == (6, 10)
    assert (len(falsos_positivos), len(gastos)) == (1, 14)
    assert falsos_positivos == ["vuelto_mal_devuelto"]


def test_sin_marcadores_en_el_yaml_no_cambia_nada() -> None:
    sin_regla = TAXONOMIA.model_copy(update={"no_es_gasto": type(TAXONOMIA.no_es_gasto)()})
    for caso in CASOS:
        assert _extraer(caso, sin_regla).confianza("categoria") is Confianza.ALTA


def test_una_duda_del_llm_no_se_pisa_pero_gana_la_nota() -> None:
    crudo = respuesta(monto="20", categoria="ropa", confianza_categoria="media")
    gasto = extraer("me devolvieron 20 de una camisa", ClienteFalso(crudo), TAXONOMIA, "mama", HOY)
    assert gasto.confianza("categoria") is Confianza.BAJA
    assert [n.texto for n in gasto.notas_de("categoria")] == [NOTA_NO_ES_GASTO]
