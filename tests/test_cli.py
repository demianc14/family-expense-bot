"""Opciones de la línea de comandos: los valores por defecto son los seguros."""

from family_expense_bot.cli import _parser


def test_la_app_escucha_solo_en_local_por_defecto() -> None:
    args = _parser().parse_args(["--app"])
    assert args.host == "127.0.0.1"
    assert args.puerto == 8080


def test_docker_puede_abrir_la_interfaz_del_contenedor() -> None:
    args = _parser().parse_args(["--app", "--host", "0.0.0.0"])
    assert args.host == "0.0.0.0"
