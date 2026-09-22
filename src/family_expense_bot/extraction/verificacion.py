"""Verificación determinista del monto contra el texto original.

Un LLM se declara seguro con la misma soltura cuando acierta que cuando
inventa, y acá se trata de dinero real. Así que el monto no se acepta por
confianza declarada: tiene que aparecer literalmente en el mensaje.
"""

import re
from decimal import Decimal, InvalidOperation

# Números con separadores de miles y/o decimales: 200, 200.50, 200,50, 1.200,50
_NUMERO = re.compile(r"\d{1,3}(?:[.,]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?")


def parsear_monto(texto: str) -> Decimal | None:
    """Convierte un número escrito por una persona a Decimal.

    Reglas, en orden: si hay dos separadores distintos, el último es el decimal;
    si hay uno solo seguido de exactamente tres dígitos, es de miles ("1.200" son
    mil doscientos, no uno coma dos); si no, es decimal.
    """
    limpio = texto.strip().replace(" ", "").replace("$", "")
    if not limpio or not re.fullmatch(r"[\d.,]+", limpio):
        return None

    tiene_punto, tiene_coma = "." in limpio, "," in limpio
    if tiene_punto and tiene_coma:
        decimal_sep = "." if limpio.rfind(".") > limpio.rfind(",") else ","
        entero, _, decimales = limpio.rpartition(decimal_sep)
        normalizado = _combinar(entero, decimales, "," if decimal_sep == "." else ".")
    elif tiene_punto or tiene_coma:
        sep = "." if tiene_punto else ","
        entero, _, resto = limpio.rpartition(sep)
        if len(resto) == 3:  # "1.200" son mil doscientos, no uno coma dos
            normalizado = _sin_separador_de_miles(limpio, sep)
        else:
            normalizado = _combinar(entero, resto, sep)
    else:
        normalizado = limpio

    if normalizado is None:
        return None
    try:
        return Decimal(normalizado)
    except InvalidOperation:
        return None


def _sin_separador_de_miles(entero: str, sep: str) -> str | None:
    """Quita el separador de miles, pero solo si agrupa de tres en tres.

    Sin esta validación, "12.34.56.78" pasaría como 12 345 678.
    """
    if sep not in entero:
        return entero
    if not re.fullmatch(rf"\d{{1,3}}(?:{re.escape(sep)}\d{{3}})+", entero):
        return None
    return entero.replace(sep, "")


def _combinar(entero: str, decimales: str, miles_sep: str) -> str | None:
    if not re.fullmatch(r"\d{1,2}", decimales):  # el dólar no tiene tres decimales
        return None
    entero_limpio = _sin_separador_de_miles(entero, miles_sep)
    if entero_limpio is None or not entero_limpio.isdigit():
        return None
    return f"{entero_limpio}.{decimales}"


def montos_en_texto(texto: str) -> set[Decimal]:
    """Todos los números que una persona escribió en el mensaje."""
    encontrados = (parsear_monto(m.group()) for m in _NUMERO.finditer(texto))
    return {m for m in encontrados if m is not None}


def monto_respaldado_por_texto(monto: Decimal, texto: str) -> bool:
    """¿El monto que devolvió el LLM está realmente escrito en el mensaje?"""
    return monto in montos_en_texto(texto)
