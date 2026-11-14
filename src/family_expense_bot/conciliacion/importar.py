"""Importar un estado de cuenta: leer, verificar y guardar. Entra completo o no entra."""

from datetime import date
from pathlib import Path
from typing import get_args

from pydantic import BaseModel, ConfigDict
from sqlalchemy.exc import IntegrityError

from family_expense_bot.conciliacion.bancos import Banco
from family_expense_bot.conciliacion.contrato import (
    EstadoRechazado,
    EstadoVigente,
    Problema,
    TipoMovimiento,
)
from family_expense_bot.conciliacion.ingesta import leer_estado
from family_expense_bot.conciliacion.verificacion import verificar
from family_expense_bot.storage.repository import RepositorioEstados


class Importacion(BaseModel):
    model_config = ConfigDict(frozen=True)

    estado: EstadoVigente
    movimientos: int
    por_tipo: dict[str, int]
    """Cuántos movimientos de cada tipo, en el orden de los tipos y sin los que no hay."""
    avisos: tuple[str, ...]


def importar_estado(
    ruta: Path,
    banco: Banco,
    repositorio: RepositorioEstados,
    *,
    cuenta: str,
    desde: date,
    hasta: date,
    tope_bytes: int,
    saldo_anterior_centavos: int,
    saldo_final_centavos: int,
) -> Importacion:
    """Guarda el estado solo si se leyó entero y cuadra; si no, lanza `EstadoRechazado`."""
    estado = leer_estado(
        ruta,
        banco,
        cuenta=cuenta,
        desde=desde,
        hasta=hasta,
        tope_bytes=tope_bytes,
        saldo_anterior_centavos=saldo_anterior_centavos,
        saldo_final_centavos=saldo_final_centavos,
    )
    veredicto = verificar(estado, repositorio.vigentes())
    if not veredicto.cuadra:
        raise EstadoRechazado(veredicto.problemas)
    try:
        guardado = repositorio.guardar(estado)
    except IntegrityError:
        # Otra importación del mismo archivo ganó la carrera: la frena el índice único del hash.
        raise EstadoRechazado(
            [
                Problema(
                    codigo="archivo_repetido",
                    mensaje="Este archivo ya se importó: la base lo rechazó y no se guardó nada.",
                )
            ]
        ) from None
    tipos = [m.tipo for m in estado.movimientos]
    return Importacion(
        estado=guardado,
        movimientos=len(tipos),
        por_tipo={t: tipos.count(t) for t in get_args(TipoMovimiento) if t in tipos},
        avisos=veredicto.avisos,
    )
