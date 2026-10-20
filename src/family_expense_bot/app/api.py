"""HTTP de la Mini App. Sin lógica de negocio: igual que `bot/handlers.py`, solo I/O.

Cada ruta valida quién llama (firma de Telegram + allowlist) y delega en las
mismas funciones que usa el bot: `resumen_presupuesto`, `evaluar_gasto` y
`RepositorioGastos.guardar`. Lo único propio de la app es el formulario, que
vive en `extraction/formulario.py` y se testea sin levantar esto.

Los montos viajan como texto con dos decimales, nunca como float.
"""

import calendar
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from importlib.resources import files
from types import MappingProxyType
from typing import Annotated, Any, Literal, NamedTuple

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict

from family_expense_bot.app.auth import InitDataInvalido, validar_init_data
from family_expense_bot.bot import mensajes
from family_expense_bot.dashboard.presupuesto import (
    EstadoBolsillo,
    ResumenPresupuesto,
    estado_tras_gasto,
    evaluar_gasto,
    resumen_presupuesto,
)
from family_expense_bot.dashboard.report import rango_del_mes
from family_expense_bot.extraction.categorias import Taxonomia, validar_roles
from family_expense_bot.extraction.formulario import (
    CamposFormulario,
    FormularioInvalido,
    armar_gasto,
    bolsillo_destino,
    leer_categoria,
    leer_monto,
)
from family_expense_bot.extraction.schema import GastoExtraido
from family_expense_bot.storage.repository import GastoRegistrado, RepositorioGastos

log = logging.getLogger(__name__)

ORIGEN = "formulario"


def _ahora() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class ServiciosApp:
    """Todo lo que las rutas necesitan, inyectado. No hay LLM: la app no lo usa."""

    repositorio: RepositorioGastos
    taxonomia: Taxonomia
    bot_token: str
    """Con él se verifica la firma del initData. Nunca sale de este proceso."""

    usuarios: Mapping[int, str] = field(default_factory=lambda: MappingProxyType({}))
    """La misma allowlist del bot: ID de Telegram → rol."""

    ingresos: Mapping[str, Decimal] = field(default_factory=lambda: MappingProxyType({}))
    hoy: Callable[[], date] = date.today
    ahora: Callable[[], datetime] = _ahora

    def __post_init__(self) -> None:
        validar_roles(self.taxonomia, self.usuarios, self.ingresos)


class Identidad(NamedTuple):
    id: int
    rol: str


class Consulta(BaseModel):
    model_config = ConfigDict(frozen=True)

    monto: str
    categoria: str
    bolsillo: str | None = None


class Guardar(CamposFormulario):
    confirmado: Literal[True]
    """La persona vio el resumen y tocó "Sí, guardar". Sin esto no se guarda."""


def _dinero(monto: Decimal) -> str:
    return f"{monto:.2f}"


def _estado(estado: EstadoBolsillo) -> dict[str, Any]:
    return {
        "nombre": estado.nombre,
        "etiqueta": estado.etiqueta,
        "porcentaje": estado.porcentaje,
        "se_gasta": estado.se_gasta,
        "asignado": _dinero(estado.asignado),
        "gastado": _dinero(estado.gastado),
        "disponible": _dinero(estado.disponible),
        "excedido": estado.excedido,
        "cantidad": estado.cantidad,
    }


def _gasto(gasto: GastoExtraido, taxonomia: Taxonomia) -> dict[str, Any]:
    assert gasto.monto is not None and gasto.fecha is not None
    return {
        "monto": _dinero(gasto.monto),
        "categoria": gasto.categoria,
        "fecha": gasto.fecha.isoformat(),
        "atribuido_a": gasto.atribuido_a,
        "de": taxonomia.etiquetas[gasto.atribuido_a],
        "descripcion": gasto.descripcion,
    }


def crear_app(servicios: ServiciosApp) -> FastAPI:
    app = FastAPI(title="family-expense-bot", docs_url=None, redoc_url=None, openapi_url=None)
    taxonomia = servicios.taxonomia
    etiquetas_bolsillo = {b.nombre: b.etiqueta for b in taxonomia.bolsillos}

    def identidad(authorization: Annotated[str | None, Header()] = None) -> Identidad:
        if not authorization or not authorization.startswith("tma "):
            raise HTTPException(401, "Abre la app desde el bot de Telegram")
        try:
            id_telegram = validar_init_data(
                authorization.removeprefix("tma "), servicios.bot_token, servicios.ahora()
            )
        except InitDataInvalido as e:
            log.warning("initData rechazado: %s", e)
            raise HTTPException(401, str(e)) from e
        rol = servicios.usuarios.get(id_telegram)
        if rol is None:
            log.warning("La Mini App la abrió un ID no autorizado: %s", id_telegram)
            raise HTTPException(403, "Esta app es privada")
        return Identidad(id_telegram, rol)

    Quien = Annotated[Identidad, Depends(identidad)]

    def resumen_del_mes(rol: str) -> ResumenPresupuesto:
        ingreso = servicios.ingresos.get(rol)
        if ingreso is None:
            raise HTTPException(409, mensajes.sin_presupuesto())
        hoy = servicios.hoy()
        desde, hasta = rango_del_mes(hoy.year, hoy.month)
        return resumen_presupuesto(
            servicios.repositorio.listar(desde=desde, hasta=hasta),
            taxonomia,
            rol,
            ingreso,
            hoy.year,
            hoy.month,
        )

    @app.exception_handler(FormularioInvalido)
    async def _formulario_invalido(_: Request, e: FormularioInvalido) -> JSONResponse:
        return JSONResponse({"detail": str(e)}, status_code=422)

    @app.get("/", response_class=HTMLResponse)
    def pagina() -> HTMLResponse:
        html = files("family_expense_bot.app").joinpath("static/index.html").read_text("utf-8")
        return HTMLResponse(html, headers={"Cache-Control": "no-cache"})

    @app.get("/api/opciones")
    def opciones(quien: Quien) -> dict[str, Any]:
        return {
            "rol": quien.rol,
            "hoy": servicios.hoy().isoformat(),
            "tiene_presupuesto": quien.rol in servicios.ingresos,
            "categorias": [
                {
                    "nombre": c.nombre,
                    "descripcion": c.descripcion,
                    "bolsillo": etiquetas_bolsillo.get(taxonomia.bolsillo_de(c.nombre, "") or ""),
                }
                for c in taxonomia.activas
            ],
            # Sin preselección: la persona elige de quién es el gasto.
            "atribuciones": [{"nombre": n, "etiqueta": e} for n, e in taxonomia.etiquetas.items()],
            "bolsillos_con_marcador": [
                {"nombre": b.nombre, "etiqueta": b.etiqueta}
                for b in taxonomia.bolsillos
                if b.marcadores
            ],
        }

    @app.get("/api/presupuesto")
    def presupuesto(quien: Quien) -> dict[str, Any]:
        resumen = resumen_del_mes(quien.rol)
        hoy = servicios.hoy()
        return {
            "anio": resumen.anio,
            "mes": resumen.mes,
            "ingreso": _dinero(resumen.ingreso),
            "dias_restantes": calendar.monthrange(hoy.year, hoy.month)[1] - hoy.day,
            "bolsillos": [_estado(b) for b in resumen.bolsillos],
            "sin_bolsillo": _dinero(resumen.sin_bolsillo),
            "categorias_sin_bolsillo": list(resumen.categorias_sin_bolsillo),
        }

    @app.post("/api/puedo")
    def puedo(consulta: Consulta, quien: Quien) -> dict[str, Any]:
        """No guarda nada ni cuenta como intento: es la misma regla que /puedo, sin LLM."""
        monto = leer_monto(consulta.monto)
        categoria = leer_categoria(consulta.categoria, taxonomia)
        destino = bolsillo_destino(categoria, consulta.bolsillo, taxonomia)
        resultado = evaluar_gasto(
            resumen_del_mes(quien.rol), taxonomia, destino, monto, servicios.hoy()
        )
        return {
            "veredicto": resultado.veredicto.value,
            "monto": _dinero(resultado.monto),
            "bolsillo": _estado(resultado.bolsillo) if resultado.bolsillo else None,
            "disponible_despues": (
                _dinero(resultado.disponible_despues)
                if resultado.disponible_despues is not None
                else None
            ),
            "reserva": _dinero(resultado.reserva) if resultado.reserva is not None else None,
            "dias_restantes": resultado.dias_restantes,
            "alternativa": _estado(resultado.alternativa) if resultado.alternativa else None,
        }

    @app.post("/api/gastos/previsualizar")
    def previsualizar(campos: CamposFormulario, quien: Quien) -> dict[str, Any]:
        """Lo que se guardaría. No escribe nada: guardar exige el sí de la persona."""
        gasto = armar_gasto(campos, quien.rol, taxonomia, servicios.hoy())
        return {"gasto": _gasto(gasto, taxonomia), "bolsillo": _bolsillo(gasto)}

    @app.post("/api/gastos", status_code=201)
    def guardar(campos: Guardar, quien: Quien) -> dict[str, Any]:
        # Se valida todo de nuevo: no se confía en lo que dijo el preview.
        gasto = armar_gasto(campos, quien.rol, taxonomia, servicios.hoy())
        try:
            registrado = servicios.repositorio.guardar(
                gasto, registrado_por=quien.id, origen=ORIGEN
            )
        except Exception as e:
            log.exception("Falló el guardado de un gasto confirmado desde la Mini App")
            raise HTTPException(500, "No pude guardar el gasto: no quedó registrado.") from e
        # No se registra en `extracciones`: no hubo lectura del LLM que medir.
        return {
            "id": registrado.id,
            "gasto": _gasto(gasto, taxonomia),
            "linea": _linea_de_bolsillo(registrado),
        }

    def _bolsillo(gasto: GastoExtraido) -> str | None:
        """Etiqueta del bolsillo contra el que cuenta, si quien lo asume tiene presupuesto."""
        if gasto.atribuido_a not in servicios.ingresos:
            return None
        nombre = taxonomia.bolsillo_de(gasto.categoria, gasto.texto_original)
        return etiquetas_bolsillo.get(nombre or "", "ninguno")

    def _linea_de_bolsillo(registrado: GastoRegistrado) -> str | None:
        """Cómo quedó el bolsillo. Un fallo acá no tapa que el gasto sí se guardó."""
        ingreso = servicios.ingresos.get(registrado.atribuido_a)
        if ingreso is None:
            return None
        try:
            desde, hasta = rango_del_mes(registrado.fecha.year, registrado.fecha.month)
            estado = estado_tras_gasto(
                servicios.repositorio.listar(desde=desde, hasta=hasta),
                taxonomia,
                registrado,
                ingreso,
            )
            return mensajes.linea_de_bolsillo(estado, registrado.fecha.month)
        except Exception:
            log.exception("No se pudo calcular el presupuesto después de guardar")
            return None

    return app
