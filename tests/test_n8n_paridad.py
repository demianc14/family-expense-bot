"""Paridad entre la verificación Python y su port JS para n8n (`n8n/lib/`).

Python es el oráculo: cada caso se corre en las dos implementaciones y se
compara el resultado completo, notas incluidas. Así no hay valores esperados
escritos a mano que puedan quedar mal en los dos lados a la vez.

Todo pasa por un solo proceso de node (`n8n/test/oraculo.js`). Si node no está
instalado, estos tests se saltan con un motivo visible, no pasan en silencio.
"""

import itertools
import json
import random
import shutil
import subprocess
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import yaml

from family_expense_bot.bot import mensajes
from family_expense_bot.dashboard.report import resumen_mensual
from family_expense_bot.extraction.categorias import cargar_taxonomia, normalizar
from family_expense_bot.extraction.parser import ErrorDeExtraccion, extraer
from family_expense_bot.extraction.verificacion import montos_en_texto, parsear_monto
from family_expense_bot.storage.dinero import a_decimal
from family_expense_bot.storage.models import Extraccion
from family_expense_bot.storage.repository import GastoRegistrado
from tests.test_parser import BASE_LLM, HOY, ClienteFalso

ROOT = Path(__file__).resolve().parents[1]
N8N = ROOT / "n8n"
NODE = shutil.which("node")
TAXONOMIA = cargar_taxonomia()
FIXTURES = yaml.safe_load((Path(__file__).parent / "fixtures" / "mensajes.yaml").read_text())

pytestmark = pytest.mark.skipif(NODE is None, reason="node no está instalado: sin paridad n8n")


def correr_js(casos: list[tuple[str, list[Any]]]) -> list[dict[str, Any]]:
    entrada = json.dumps([{"fn": fn, "args": args} for fn, args in casos], ensure_ascii=False)
    salida = subprocess.run(
        [str(NODE), str(N8N / "test" / "oraculo.js")],
        input=entrada,
        capture_output=True,
        text=True,
        check=True,
    )
    resultados: list[dict[str, Any]] = json.loads(salida.stdout)
    assert len(resultados) == len(casos)
    return resultados


def comparar(fn: str, entradas: list[list[Any]], oraculo: Any) -> None:
    """Corre `fn` en JS sobre cada entrada y la compara con `oraculo(*entrada)` en Python."""
    resultados = correr_js([(fn, args) for args in entradas])
    diferencias = []
    for args, js in zip(entradas, resultados, strict=True):
        try:
            py: dict[str, Any] = {"ok": oraculo(*args)}
        except ErrorDeExtraccion:
            py = {"error": "ErrorDeExtraccion"}
        except ValueError:
            py = {"error": "Error"}
        if py != js:
            diferencias.append(f"{fn}{tuple(args)!r}\n  python: {py}\n  js:     {js}")
    assert not diferencias, f"{len(diferencias)} diferencia(s):\n" + "\n".join(diferencias[:10])


# --- La taxonomía que usa n8n es la del yaml ------------------------------------


def test_taxonomia_json_esta_sincronizada_con_el_yaml() -> None:
    """Si falla: python n8n/exportar_taxonomia.py"""
    exportada = json.loads((N8N / "taxonomia.json").read_text(encoding="utf-8"))
    assert exportada == TAXONOMIA.model_dump(mode="json")


# --- Primitivas ------------------------------------------------------------------

TEXTOS_RAROS = [
    "",
    " ",
    "Común",
    "ÑAÑA",
    "mi ñaña",
    "Mi Hija",
    "comunión",
    "﻿hola﻿",
    "\x1c200\x1f",
    "\t 15 \n",
    "\u00a0mercado\u2003",
    "\uff26\uff55\uff4c\uff4c \uff57\uff49\uff44\uff54\uff48",  # ancho completo
    "ﬁesta",
    "straße",
    "İstanbul",
    "ΟΔΟΣ",
    "it's",
    'dijo "hola"',
    "a'b\"c",
    "barra \\ invertida",
    "línea\nnueva",
    "\x00\x7f​",
    "emoji 🍕 y \U0001f600",
]


def test_normalizar() -> None:
    comparar("normalizar", [[t] for t in TEXTOS_RAROS], normalizar)


def test_strip() -> None:
    comparar("strip", [[t] for t in TEXTOS_RAROS], str.strip)


def test_repr_de_las_notas() -> None:
    comparar("repr", [[t] for t in TEXTOS_RAROS], repr)


MONTOS = [
    "200",
    "200.50",
    "200,50",
    "1.200",
    "1,200",
    "1.200,50",
    "1,200.50",
    "$35",
    " 15 ",
    "0",
    "00",
    "0.00",
    "000.50",
    "0200",
    ".50",
    ",50",
    "50.",
    "1.2.50",
    "12.34.56.78",
    "1.234.567",
    "1.234.567,89",
    "1,234,567.89",
    "12.345",
    "123.4567",
    "1.23",
    "1.2",
    "1.",
    "--",
    "",
    "abc",
    "20 dólares",
    "$ 1 200",
    "99999999999999999999",
    "1.200.50",
    "1,200,50",
    "1.20,5",
    "﻿200",
]


def _decimal_o_none(texto: str) -> str | None:
    monto = parsear_monto(texto)
    return None if monto is None else str(monto)


def test_parsear_monto() -> None:
    comparar("parsear_monto", [[m] for m in MONTOS], _decimal_o_none)


TEXTOS_CON_MONTOS = [
    "20 de gasolina y 8,50 del almuerzo",
    "1.200 del arriendo",
    "1.200,50 y 3",
    "compré cosas en el super",
    "12.34.56.78",
    "pagué $15 ayer 14/03",
    "abc123def 4,567.8",
    "0 y 00 y 0.00",
    "2026-03-14 200",
    "12,345,678.90",
]


def _centavos(texto: str) -> list[str]:
    return sorted(str(int(m * 100)) for m in montos_en_texto(texto))


def test_montos_en_texto() -> None:
    comparar("montos_en_texto", [[t] for t in TEXTOS_CON_MONTOS], _centavos)


def test_divergencia_documentada_digitos_no_ascii() -> None:
    """Python acepta "٢٠٠" como 200; el port JS no. Es más estricto, nunca más laxo."""
    assert parsear_monto("٢٠٠") == Decimal(200)
    assert correr_js([("parsear_monto", ["٢٠٠"])]) == [{"ok": None}]


def _fecha_o_none(texto: str) -> str | None:
    try:
        return date.fromisoformat(texto).isoformat()
    except ValueError:
        return None


FECHAS_RARAS = [
    "2026-03-14",
    "20260314",
    "2026-W11",
    "2026W11",
    "2026-W11-6",
    "2026W116",
    "2026-W116",
    "2026W11-6",
    "2026-0314",
    " 2026-03-14",
    "2026-3-14",
    "2026-02-30",
    "2024-02-29",
    "2025-02-29",
    "2026-W53",
    "2025-W53",
    "2020-W53-7",
    "2026-03-14T10:00",
    "0000-01-01",
    "0001-01-01",
    "2026-03",
    "2026-W00-1",
    "2026-W11-0",
    "2026-W11-8",
    "2026/03/14",
    "ayer",
    "",
]


def test_fechas_iso_raras() -> None:
    comparar("fecha_desde_iso", [[f] for f in FECHAS_RARAS], _fecha_o_none)


def test_todas_las_semanas_iso() -> None:
    entradas = [
        [f"{anio}-W{semana:02d}-{dia}"]
        for anio in range(2015, 2032)
        for semana in range(1, 54)
        for dia in range(1, 8)
    ]
    comparar("fecha_desde_iso", entradas, _fecha_o_none)


def test_todos_los_dias_de_varios_anios() -> None:
    inicio = date(2023, 1, 1)
    entradas = [[(inicio + timedelta(days=i)).strftime("%Y%m%d")] for i in range(365 * 4 + 1)]
    comparar("fecha_desde_iso", entradas, _fecha_o_none)


def test_describir_categorias_para_el_prompt() -> None:
    comparar("describir_para_prompt", [[]], TAXONOMIA.describir_para_prompt)


CATEGORIAS = ["ropa", " Ropa ", "ROPA", "ropá", "salud", "servicios", "otros", "inventada", ""]


def test_contiene_categoria() -> None:
    comparar("contiene", [[c] for c in [*CATEGORIAS, None]], TAXONOMIA.contiene)


# --- Parentescos y gasto compartido -------------------------------------------

CITAS_EXTRA = [
    "mi tía",
    "mi hija y mi esposo",
    "Mi Hija",
    "MI ÑAÑA",
    "mi nana",
    "casa",
    "Casa",
    "la casa",
    "llegué a casa",
    "casamiento",
    "comunión",
    "comun",
    "para la casa",
    "mi hijastra",
    "",
]


def _todas_las_citas() -> list[str]:
    expresiones = {
        e for a in TAXONOMIA.atribuciones for lista in a.como_lo_llaman.values() for e in lista
    }
    return sorted(expresiones | set(TAXONOMIA.compartida.marcadores) | set(CITAS_EXTRA))


def test_personas_en_cada_cita_desde_cada_remitente() -> None:
    entradas = [[c, r] for c in _todas_las_citas() for r in TAXONOMIA.personas]
    comparar("personas_en", entradas, TAXONOMIA.personas_en)


def test_gasto_compartido() -> None:
    entradas = [[c] for c in _todas_las_citas()]
    comparar("menciona_gasto_compartido", entradas, TAXONOMIA.menciona_gasto_compartido)
    comparar("es_compartida", entradas, TAXONOMIA.es_compartida)


# --- La compuerta completa ------------------------------------------------------


def _extraer_py(respuesta: Any, texto: str, remitente: str, hoy: str) -> dict[str, Any]:
    gasto = extraer(
        texto, ClienteFalso(json.dumps(respuesta)), TAXONOMIA, remitente, date.fromisoformat(hoy)
    )
    return gasto.model_dump(mode="json") | {
        "campos_dudosos": list(gasto.campos_dudosos),
        "listo_para_guardar": gasto.listo_para_guardar,
    }


def test_compuerta_sobre_las_fixtures() -> None:
    entradas = [
        [BASE_LLM | c["respuesta_llm"], c["texto"], c["remitente"], HOY.isoformat()]
        for c in FIXTURES
    ]
    comparar("verificar_gasto", entradas, _extraer_py)


# La batería de docs/decisiones.md, con la cita que devolvería un LLM que sigue el prompt.
# Lo que se prueba acá es la resolución, no al LLM.
BATERIA_README = [
    ("mama", "mi hija pagó 45 de ropa con su plata", "45", "mi hija", "alta", "hermana", "alta"),
    ("papa", "mi hija pagó 45 de ropa con su plata", "45", "mi hija", "alta", "hermana", "alta"),
    (
        "mama",
        "mi esposo pagó 60 de zapatos con su plata",
        "60",
        "mi esposo",
        "alta",
        "papa",
        "alta",
    ),
    ("demian", "mi ñaña pagó 30 del cine con su plata", "30", "mi ñaña", "alta", "hermana", "alta"),
    (
        "demian",
        "mi hermana pagó 45 de ropa con su plata",
        "45",
        "mi hermana",
        "alta",
        "hermana",
        "alta",
    ),
    ("mama", "mi hermana pagó 45 de ropa con su plata", "45", "mi hermana", "alta", "mama", "baja"),
    ("papa", "300 de la pensión de mi hija", "300", None, "baja", "papa", "baja"),
    ("mama", "80 de gasolina, gasto de la casa", "80", "gasto de la casa", "alta", "casa", "alta"),
    ("papa", "300 de pensión", "300", None, "baja", "papa", "baja"),
    ("mama", "200 dólares supermercado", "200", None, "alta", "mama", "media"),
]


@pytest.mark.parametrize(
    ("remitente", "texto", "monto", "cita", "conf_llm", "rol", "confianza"),
    BATERIA_README,
    ids=[f"{r}: {t}" for r, t, *_ in BATERIA_README],
)
def test_bateria_del_readme(
    remitente: str,
    texto: str,
    monto: str,
    cita: str | None,
    conf_llm: str,
    rol: str,
    confianza: str,
) -> None:
    respuesta = BASE_LLM | {
        "monto": monto,
        "categoria": "otros",
        "quien_paga": cita,
        "confianza_quien_paga": conf_llm,
    }
    [js] = correr_js([("verificar_gasto", [respuesta, texto, remitente, HOY.isoformat()])])
    assert js["ok"]["atribuido_a"] == rol
    assert js["ok"]["confianza_atribuido_a"] == confianza
    assert js["ok"] == _extraer_py(respuesta, texto, remitente, HOY.isoformat())


def test_compuerta_sobre_todas_las_citas() -> None:
    """Cada cita, desde cada remitente, apareciendo o no en el mensaje, con cada confianza."""
    entradas = []
    for cita, remitente, confianza, en_texto in itertools.product(
        [*_todas_las_citas(), None], TAXONOMIA.personas, ["alta", "media", "baja"], [True, False]
    ):
        texto = f"45 de ropa, {cita}" if en_texto and cita else "45 de ropa"
        respuesta = BASE_LLM | {
            "monto": "45",
            "categoria": "ropa",
            "quien_paga": cita,
            "confianza_quien_paga": confianza,
        }
        entradas.append([respuesta, texto, remitente, HOY.isoformat()])
    comparar("verificar_gasto", entradas, _extraer_py)


def test_compuerta_sobre_montos() -> None:
    textos = ["200 dólares mercado", "12,50 en pan", "1.200 del arriendo", "compré cosas", "0 y 0"]
    entradas = [
        [BASE_LLM | {"monto": m, "categoria": "otros"}, t, "demian", HOY.isoformat()]
        for m in [*MONTOS, None]
        for t in textos
    ]
    comparar("verificar_gasto", entradas, _extraer_py)


def test_compuerta_sobre_fechas_y_categorias() -> None:
    fechas = [
        *FECHAS_RARAS,
        None,
        "2026-03-15",
        "2026-03-16",
        "2025-03-15",
        "2025-03-14",
        "2025-03-16",
    ]
    entradas = [
        [
            BASE_LLM | {"monto": "20", "categoria": c, "fecha": f, "confianza_fecha": conf},
            "20 de algo",
            "papa",
            HOY.isoformat(),
        ]
        for f in fechas
        for c in [*CATEGORIAS, None]
        for conf in ["alta", "media"]
    ]
    comparar("verificar_gasto", entradas, _extraer_py)


def test_compuerta_rechaza_lo_mismo_que_python() -> None:
    validas = BASE_LLM | {"monto": "20"}
    malas: list[Any] = [
        validas | {"monto": 20},
        validas | {"categoria": 3},
        validas | {"confianza_monto": "muy alta"},
        validas | {"confianza_monto": "Alta"},
        {k: v for k, v in validas.items() if k != "descripcion"},
        {k: v for k, v in validas.items() if k != "confianza_fecha"},
        validas | {"descripcion": None},
        [validas],
        "texto",
        None,
    ]
    entradas = [[r, "20 de algo", "demian", HOY.isoformat()] for r in malas]
    # Campos extra (como `accion` del agente) se ignoran en los dos lados.
    entradas.append([validas | {"accion": "proponer", "rol": "casa"}, "20", "demian", "2026-03-15"])
    # Remitente que no es persona: error de configuración, antes que nada.
    entradas += [[validas, "20", r, HOY.isoformat()] for r in ["casa", "tia", ""]]
    comparar("verificar_gasto", entradas, _extraer_py)


# --- Lo que ve la persona -------------------------------------------------------


def _gasto_py(respuesta: Any, texto: str, remitente: str, hoy: str) -> Any:
    return extraer(
        texto, ClienteFalso(json.dumps(respuesta)), TAXONOMIA, remitente, date.fromisoformat(hoy)
    )


def test_resumen_y_falta_monto_son_el_mismo_texto() -> None:
    entradas = [
        [BASE_LLM | c["respuesta_llm"], c["texto"], c["remitente"], HOY.isoformat()]
        for c in FIXTURES
    ] + [
        [
            BASE_LLM | {"monto": "1.234.567,8", "categoria": "otros"},
            "1.234.567,8",
            "papa",
            "2026-03-15",
        ]
    ]
    comparar(
        "resumen",
        entradas,
        lambda *a: mensajes.resumen(_gasto_py(*a), TAXONOMIA.etiquetas),
    )
    comparar(
        "falta_monto",
        entradas,
        lambda *a: mensajes.falta_monto(_gasto_py(*a), TAXONOMIA.etiquetas),
    )


def _fila_gasto(
    id_: int, centavos: int, categoria: str, fecha: str, anulado: bool
) -> dict[str, Any]:
    return {
        "id": id_,
        "monto_centavos": centavos,
        "categoria": categoria,
        "atribuido_a": random.choice(TAXONOMIA.nombres_atribucion),
        "fecha": fecha,
        "anulado_en": "2026-03-20T00:00:00Z" if anulado else None,
    }


def _registrado(fila: dict[str, Any]) -> GastoRegistrado:
    return GastoRegistrado(
        id=fila["id"],
        monto=a_decimal(fila["monto_centavos"]),
        categoria=fila["categoria"],
        atribuido_a=fila["atribuido_a"],
        fecha=date.fromisoformat(fila["fecha"]),
        descripcion="x",
        texto_original="x",
        registrado_por=1,
        creado_en=datetime(2026, 3, 1, tzinfo=UTC),
        corregido_manualmente=False,
        anulado=fila["anulado_en"] is not None,
    )


def test_guardado_es_el_mismo_texto() -> None:
    random.seed(7)
    filas = [
        _fila_gasto(i, c, "ropa", "2026-03-05", False)
        for i, c in enumerate([1, 5, 50, 99, 100, 1250, 123456789, 100000000])
    ]
    comparar(
        "guardado",
        [[f] for f in filas],
        lambda f: mensajes.guardado(_registrado(f), TAXONOMIA.etiquetas),
    )


def _reporte_py(
    filas: list[dict[str, Any]], extracciones: list[dict[str, Any]], anio: int, mes: int
) -> str:
    del_mes = sorted(
        (f for f in filas if f["anulado_en"] is None and f["fecha"][:7] == f"{anio}-{mes:02d}"),
        key=lambda f: (f["fecha"], f["id"]),
        reverse=True,
    )  # como RepositorioGastos.listar: fecha e id descendente, sin anulados
    registros = [
        Extraccion(
            confirmada=e["confirmada"],
            corregida=e["corregida"],
            tenia_monto=e["tenia_monto"],
            confianza_monto=e["confianza_monto"],
            confianza_categoria=e["confianza_categoria"],
            confianza_fecha=e["confianza_fecha"],
            confianza_atribuido_a=e["confianza_atribuido_a"],
        )
        for e in extracciones
    ]
    return mensajes.reporte(
        resumen_mensual([_registrado(f) for f in del_mes], registros, anio, mes)
    )


def test_reporte_es_el_mismo_texto() -> None:
    random.seed(11)
    categorias = ["transporte", "alimentacion", "comida_fuera", "ropa", "otros"]
    entradas: list[list[Any]] = [[[], [], 2026, 3]]
    for n_filas, n_extr in [(1, 1), (5, 8), (30, 40), (3, 0), (4, 7), (2, 3)]:
        filas = [
            _fila_gasto(
                i,
                random.choice([100, 250, 1250, 99999, random.randint(1, 5_000_000)]),
                random.choice(categorias),
                f"2026-{random.choice(['02', '03', '03', '04'])}-{random.randint(1, 28):02d}",
                random.random() < 0.1,
            )
            for i in range(1, n_filas + 1)
        ]
        extracciones = [
            {
                "confirmada": (c := random.random() < 0.7),
                "corregida": c and random.random() < 0.3,
                "tenia_monto": random.random() < 0.9,
                **{
                    f"confianza_{campo}": random.choice(["alta", "alta", "media", "baja"])
                    for campo in ("monto", "categoria", "fecha", "atribuido_a")
                },
            }
            for _ in range(n_extr)
        ]
        entradas.append([filas, extracciones, 2026, 3])
    # Empate exacto en el porcentaje: 1/8 = 12.5 %. Python redondea al par.
    entradas.append(
        [
            [_fila_gasto(1, 100, "ropa", "2026-03-01", False)],
            [
                {
                    "confirmada": i == 0,
                    "corregida": False,
                    "tenia_monto": True,
                    **{
                        f"confianza_{c}": "alta"
                        for c in ("monto", "categoria", "fecha", "atribuido_a")
                    },
                }
                for i in range(8)
            ],
            2026,
            3,
        ]
    )
    comparar("reporte", entradas, _reporte_py)


# --- Presupuesto ------------------------------------------------------------------


def test_bolsillo_de_cada_categoria_con_y_sin_marcador() -> None:
    textos = ["algo 5", "algo 5 del fondo", "5 con mi FONDO", "5 fondos", "5 del fondo común"]
    entradas = [[c.nombre, t] for c in TAXONOMIA.categorias for t in textos]
    comparar("bolsillo_de", entradas, TAXONOMIA.bolsillo_de)


def _ingresos_py(texto: str) -> dict[str, str]:
    from decimal import Decimal

    from family_expense_bot.config import Settings

    ingresos = Settings(
        _env_file=None,  # type: ignore[call-arg]
        telegram_bot_token="1:x",  # type: ignore[arg-type]
        ingresos_mensuales=texto,
    ).ingresos
    # La misma validación que hace Servicios al arrancar.
    if set(ingresos) - set(TAXONOMIA.personas):
        raise ValueError("rol que no es persona")
    return {rol: str(int(m * Decimal(100))) for rol, m in ingresos.items()}


def test_ingresos_se_leen_igual() -> None:
    textos = [
        "",
        "demian:1200",
        " Demian : 1200.50 , mama:800,",
        "demian:0100",
        "demian",
        "demian:",
        "demian:mil",
        "demian:-5",
        "demian:0",
        "demian:0.00",
        "demian:1.005",
        "demian:1e3",
        "demian:1_200",
        "demian:+5",
        "demian:1,demian:2",
        ":100",
        "casa:100",
        "tia:100",
    ]
    comparar("parsear_ingresos", [[t] for t in textos], _ingresos_py)


def _filas_presupuesto(semilla: int, n: int) -> list[dict[str, Any]]:
    rnd = random.Random(semilla)
    categorias = [c.nombre for c in TAXONOMIA.categorias if not c.candidata]
    return [
        {
            "id": i,
            "monto_centavos": rnd.choice([100, 1250, 3000, 99999, rnd.randint(1, 500_000)]),
            "categoria": rnd.choice(categorias),
            "atribuido_a": rnd.choice(["demian", "demian", "mama", "casa"]),
            "fecha": f"2026-{rnd.choice(['09', '10', '10', '11'])}-{rnd.randint(1, 28):02d}",
            "texto_original": rnd.choice(["x", "algo del fondo", "con mi fondo", "nada"]),
            "anulado_en": "2026-10-20T00:00:00Z" if rnd.random() < 0.1 else None,
        }
        for i in range(1, n + 1)
    ]


def _resumen_py(
    filas: list[dict[str, Any]], persona: str, ingreso: str, anio: int, mes: int
) -> Any:
    from decimal import Decimal

    from family_expense_bot.dashboard.presupuesto import resumen_presupuesto

    vivas = [f for f in filas if f["anulado_en"] is None]
    vivas.sort(key=lambda f: (f["fecha"], f["id"]), reverse=True)
    gastos = [
        _registrado(f).model_copy(update={"texto_original": f["texto_original"]}) for f in vivas
    ]
    return resumen_presupuesto(gastos, TAXONOMIA, persona, Decimal(ingreso), anio, mes)


def test_presupuesto_es_el_mismo_texto() -> None:
    entradas: list[list[Any]] = [[[], "demian", "1200", 2026, 10]]
    for semilla, n in [(1, 5), (2, 30), (3, 80), (4, 12)]:
        for ingreso in ["1200", "1000.01", "0.03", "350.5"]:
            entradas.append([_filas_presupuesto(semilla, n), "demian", ingreso, 2026, 10])
    comparar(
        "presupuesto",
        entradas,
        lambda *a: mensajes.presupuesto(_resumen_py(*a)),
    )


def test_linea_de_bolsillo_es_el_mismo_texto() -> None:
    entradas = [
        [_filas_presupuesto(semilla, 40), "demian", ingreso, 2026, 10, nombre]
        for semilla in (5, 6)
        for ingreso in ("300", "1200")
        for nombre in ("necesarios", "fondo", "lujos", None)
    ]

    def py(filas: Any, persona: str, ingreso: str, anio: int, mes: int, nombre: Any) -> str:
        resumen = _resumen_py(filas, persona, ingreso, anio, mes)
        return mensajes.linea_de_bolsillo(resumen.bolsillo(nombre) if nombre else None, mes)

    comparar("linea_de_bolsillo", entradas, py)


def test_la_confirmacion_muestra_el_mismo_bolsillo() -> None:
    from decimal import Decimal

    from family_expense_bot.bot.handlers import Servicios, _bolsillo

    ingresos = {"demian": "1200", "mama": "800"}
    servicios = Servicios(
        repositorio=None,  # type: ignore[arg-type]
        extracciones=None,  # type: ignore[arg-type]
        cliente=ClienteFalso("{}"),
        taxonomia=TAXONOMIA,
        ingresos={rol: Decimal(m) for rol, m in ingresos.items()},
    )

    def py(respuesta: Any, texto: str, remitente: str, hoy: str, _: Any) -> str:
        gasto = _gasto_py(respuesta, texto, remitente, hoy)
        return mensajes.resumen(gasto, TAXONOMIA.etiquetas, bolsillo=_bolsillo(servicios, gasto))

    entradas = [
        [BASE_LLM | c["respuesta_llm"], c["texto"], c["remitente"], HOY.isoformat(), ingresos]
        for c in FIXTURES
    ] + [
        [
            BASE_LLM | {"monto": "30", "categoria": "ropa"},
            "30 de camisa del fondo",
            "demian",
            HOY.isoformat(),
            ingresos,
        ],
        [
            BASE_LLM | {"monto": "30", "categoria": "otros"},
            "30 de algo",
            "demian",
            HOY.isoformat(),
            ingresos,
        ],
    ]
    comparar("resumen_con_bolsillo", entradas, py)


def test_la_respuesta_a_una_consulta_es_la_misma() -> None:
    from decimal import Decimal

    from family_expense_bot.dashboard.presupuesto import evaluar_gasto

    def py(
        filas: Any,
        persona: str,
        ingreso: str,
        monto: str,
        categoria: str,
        texto: str,
        hoy: str,
        dudosa: bool,
    ) -> str:
        dia = date.fromisoformat(hoy)
        resumen = _resumen_py(filas, persona, ingreso, dia.year, dia.month)
        bolsillo = TAXONOMIA.bolsillo_de(categoria, texto)
        consulta = evaluar_gasto(resumen, TAXONOMIA, bolsillo, Decimal(monto), dia)
        return mensajes.respuesta_consulta(consulta, categoria, dudosa)

    rnd = random.Random(21)
    casos = [
        ("entretenimiento", "concierto"),
        ("comida_fuera", "pizza"),
        ("personal", "curso"),
        ("alimentacion", "super"),
        ("ropa", "camisa del fondo"),
        ("otros", "algo"),
    ]
    entradas = []
    for semilla in (7, 8, 9):
        filas = _filas_presupuesto(semilla, 40)
        for hoy in ("2026-10-01", "2026-10-16", "2026-10-30", "2026-10-31", "2026-09-30"):
            for categoria, texto in casos:
                monto = rnd.choice(["1", "30", "116.13", "123.88", "239.99", "500", "12.5"])
                entradas.append(
                    [
                        filas,
                        "demian",
                        rnd.choice(["1200", "400", "1000.01"]),
                        monto,
                        categoria,
                        texto,
                        hoy,
                        rnd.random() < 0.3,
                    ]
                )
    comparar("consulta", entradas, py)


# --- Tests propios del port (node --test) ---------------------------------------


def test_tests_de_node_pasan() -> None:
    resultado = subprocess.run(
        [str(NODE), "--test", str(N8N / "test" / "*.test.js")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert resultado.returncode == 0, resultado.stdout + resultado.stderr
