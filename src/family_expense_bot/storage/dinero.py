"""Dinero en centavos.

Los montos se guardan como enteros, no como float: 0.1 + 0.2 no es 0.3 en
punto flotante, y estos son gastos reales de una familia.
"""

from decimal import ROUND_HALF_UP, Decimal

CENTAVOS = Decimal("0.01")

MAXIMO_CENTAVOS = 2**63 - 1
"""El entero más grande que guarda SQLite. Un monto mayor no es un gasto real."""


def a_centavos(monto: Decimal) -> int:
    """Redondea a centavos (medio hacia arriba, como en una factura)."""
    if monto <= 0:
        raise ValueError(f"El monto debe ser positivo, llegó {monto}")
    centavos = int(monto.quantize(CENTAVOS, rounding=ROUND_HALF_UP) * 100)
    if centavos > MAXIMO_CENTAVOS:
        raise ValueError(f"El monto {monto} es demasiado grande para guardarse")
    return centavos


def a_decimal(centavos: int) -> Decimal:
    return (Decimal(centavos) / 100).quantize(CENTAVOS)
