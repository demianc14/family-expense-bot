"""Dinero en centavos.

Los montos se guardan como enteros, no como float: 0.1 + 0.2 no es 0.3 en
punto flotante, y estos son gastos reales de una familia.
"""

from decimal import ROUND_HALF_UP, Decimal

CENTAVOS = Decimal("0.01")


def a_centavos(monto: Decimal) -> int:
    """Redondea a centavos (medio hacia arriba, como en una factura)."""
    if monto <= 0:
        raise ValueError(f"El monto debe ser positivo, llegó {monto}")
    return int(monto.quantize(CENTAVOS, rounding=ROUND_HALF_UP) * 100)


def a_decimal(centavos: int) -> Decimal:
    return (Decimal(centavos) / 100).quantize(CENTAVOS)
