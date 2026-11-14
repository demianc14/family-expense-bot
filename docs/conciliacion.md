# Conciliación de estados de cuenta

[← README](../README.md) · [Decisiones](decisiones.md) · [Presupuesto](presupuesto.md) · [Calidad](calidad.md) · [Evaluación](evaluacion.md) · [Mini App](mini-app.md) · [n8n](../n8n/README.md)

El bot sabe lo que le escribo; el banco sabe lo que de verdad salió. Esta parte cruza las dos fuentes. Hoy está hecha la **ingesta**: un estado de cuenta en CSV entra a la base completo y verificado, o no entra. El cruce contra los gastos (reglas, vistas SQL y Power BI) es lo siguiente en [`ROADMAP.md`](../ROADMAP.md).

Todo lo de esta página se hizo con datos inventados. Ningún estado real entra al repo ni va a un LLM.

## G0. Fuentes (pendiente, la escribe Demian)

- [ ] Banco y tarjeta; si exportan CSV o Excel; si el PDF trae texto.
- [ ] Desde qué mes hay estados de cuenta y cuánto se paga en efectivo.
- [ ] Dónde corre Power BI.

Mientras no esté, `config/bancos.yaml` solo trae dos bancos inventados. Excel no se lee todavía: pide una dependencia nueva (`openpyxl`) y no se agrega hasta saber si hace falta.

## Importar un estado

```bash
family-expense-bot --importar-estado marzo.csv --banco banco_sintetico --cuenta ahorros --desde 2026-03-01 --hasta 2026-03-31 --saldo-anterior 1000.00 --saldo-final 1714.73
```

- `--banco` es la clave del banco en `config/bancos.yaml`.
- `--cuenta` es una etiqueta ("ahorros", "visa"). Se rechaza cualquier cosa con cuatro dígitos seguidos: el número de cuenta nunca se guarda.
- `--saldo-anterior` y `--saldo-final` van como los muestra el banco, con punto decimal. Son **obligatorios siempre**, aunque el archivo traiga columna de saldo (ver [decisiones](#decisiones)).

Si entra, dice cuántos movimientos guardó y de qué tipo. Si no, lista **todos** los problemas, termina con código 1 y no guarda nada.

Para probar sin tocar la base real:

```bash
python scripts/estado_sintetico.py --base /tmp/sintetica.db
```

Crea una base nueva con 11 gastos inventados y los estados de marzo y abril ya importados (22 movimientos). Se niega a escribir sobre un archivo que ya existe.

## Agregar un banco

Es una entrada en [`config/bancos.yaml`](../config/bancos.yaml), no código: nombre de cada columna, formato de fecha, separador decimal, delimitador, líneas de título antes del encabezado, convención de signo y las reglas de `tipo`. El archivo explica cada campo. Un yaml mal escrito no carga (campos desconocidos, monto en dos lugares, formato de fecha sin año de cuatro dígitos).

`movimientos.tipo` lo asignan esas reglas, en orden y sin LLM: gana la primera cuya frase aparece en la descripción como palabras completas, sin distinguir tildes ni mayúsculas ("comisionista" no es una comisión). Una regla puede exigir signo: la devolución de una comisión es plata que entra, no costo del banco. Si ninguna coincide, lo que sale es `debito` y lo que entra, `credito`.

## Qué se verifica

**Al leer** (`conciliacion/ingesta.py`). Nada se adivina; una sola fila mala rechaza el archivo:

- El archivo no pasa del tope (`tope_bytes`, 1 MiB). Se mira el tamaño antes de abrirlo.
- Está en la codificación del banco. UTF-8 entra con o sin BOM.
- El encabezado trae todas las columnas del yaml, sin repetir. Las columnas de más se ignoran.
- Cada fila tiene las mismas columnas que el encabezado, con fecha, descripción, monto y saldo (si el banco lo trae).
- La fecha existe y está en el formato del banco.
- El monto son dígitos y hasta dos decimales con el separador del banco. Sin separador de miles, sin símbolo de moneda, sin paréntesis, y distinto de cero.
- La descripción cabe en 500 caracteres. Si no, se rechaza: cortarla sería adivinar.

**Después de leer** (`conciliacion/verificacion.py`), todo en centavos enteros:

1. **Archivo repetido:** el mismo SHA-256 que un estado vigente.
2. **Periodo solapado:** se cruza con otro estado vigente del mismo banco y cuenta. Atrapa el mismo mes exportado de nuevo, que trae otros bytes y otro hash.
3. **Fechas:** cada movimiento cae dentro del periodo.
4. **Saldo corrido** (solo con columna de saldo): cada fila encadena con la anterior, y la última da el saldo final.
5. **Invariante:** saldo anterior + créditos − débitos = saldo final.
6. **Bloque duplicado:** dos o más filas seguidas que se repiten justo después. Si el estado no cuadra, el mensaje dice qué líneas son y si suman la diferencia.

**Avisos** (no rechazan; se muestran al importar y no se guardan):

- **Continuidad:** si hay un estado vigente de la misma cuenta que termina el día anterior (o empieza el día siguiente) y su saldo no empata con el de este.
- **Bloque repetido en un estado que cuadra:** el saldo confirma que los movimientos son reales, pero conviene mirarlo.

**Al guardar**, el estado y sus movimientos van en una sola transacción. El índice único del hash es la última defensa si dos importaciones corren a la vez.

## Decisiones

- **Línea totalmente vacía se salta; fila a medio llenar se rechaza.** La numeración de líneas se conserva, así que "línea 8" es la línea 8 del archivo en un editor. Una fila con solo separadores (`,,,`) cuenta como vacía: así exporta Excel una fila en blanco.
- **Las descripciones se guardan literales, aunque empiecen con `=`, `+`, `-` o `@`.** La base guarda lo que dice el banco: cambiar el texto al importar rompería la comparación de descripciones del cruce y lo que se guarda dejaría de ser lo que trae el archivo. El riesgo (que Excel o Power BI lo ejecuten como fórmula) existe solo cuando el texto sale a un CSV, así que se neutraliza **al exportar**, tanto en el `--exportar` de gastos que ya existe como en `--exportar-bi`. Va en una PR aparte (decidido el 2026-11-14, anotado en el roadmap); hasta que entre, `--exportar` no neutraliza.
- **Dos cargos idénticos el mismo día son dos movimientos.** Una sola fila repetida nunca se toma como duplicado; un bloque de dos o más, sí se señala.
- **La continuidad avisa, no rechaza.** Un estado anterior mal importado no debe bloquear al siguiente.
- **Los saldos declarados son obligatorios siempre** (decidido el 2026-11-14). Los escribe quien importa, mirando el estado; nunca se deducen del archivo. Si el saldo anterior saliera de la primera fila, esa fila no se verificaría contra nada y un archivo cortado al principio o al final cuadraría. Con los dos declarados, la primera fila tiene que encadenar con el saldo anterior y la última tiene que dar el saldo final (caso `primera_fila_corrida`).
- **Los avisos se muestran y no se guardan** (decidido el 2026-11-14). Guardarlos pediría una columna nueva en el esquema.
- **Los saldos declarados van como los muestra el banco.** En una tarjeta (`signo: positivo_sale`) lo que se debe va en positivo; adentro todo queda con signo negativo si sale plata.

## Límites conocidos

- **Sin columna de saldo, solo se verifica el total.** Una fila perdida se nota porque el total no cuadra, pero no se sabe cuál fue. Dos errores que se compensan (+1 y −1 centavo) pasan sin que nada los delate: es el caso `errores_compensados_sin_saldo` de la tabla. Con columna de saldo, los dos se detectan en la línea exacta.
- **Los avisos no quedan en la base.** Salen una vez, al importar. Después solo se pueden reconstruir comparando los saldos de dos estados seguidos.
- **Una fila repetida suelta no se detecta por sí sola.** Si no es real, la delata el saldo.
- **El archivo va del movimiento más viejo al más nuevo.** Un banco que exporte al revés rompe el saldo corrido; haría falta una opción en el yaml. Se queda así hasta ver el banco real (G0).
- **No hay filas de totales al final.** Una fila "Total" se rechaza como fila a medio llenar. Se queda así hasta ver el banco real (G0).
- **Solo CSV.** Excel y PDF dependen de G0.
- **Una fecha en mes/día que también sirve como día/mes no se nota en esa fila** (05/03 es 5 de marzo o 3 de mayo). El archivo se rechaza igual en cuanto una fila tiene día mayor a 12 o cae fuera del periodo.

## Qué rechaza y con qué mensaje

Cada fila es un archivo de `tests/fixtures/estados/` importado de verdad contra una base en memoria. La tabla sale de un comando, y un test falla si esta página no coincide con él:

```bash
python scripts/estado_sintetico.py --tabla
```

<!-- tabla-casos:inicio -->
| Caso | Qué trae | Resultado | Mensaje exacto |
|---|---|---|---|
| `marzo` | Estado que cuadra, con columna de saldo | Entra | 17 movimientos |
| `abril` | El mes siguiente; empata con marzo | Entra | 5 movimientos |
| `marzo_coma` | Coma decimal, punto y coma, débito y crédito aparte, sin columna de saldo | Entra | 17 movimientos |
| `saldo_un_centavo` | Saldo final declarado un centavo arriba | Rechazado | El estado no cuadra: saldo anterior 1000.00 + créditos 1200.12 - débitos 485.39 = 1714.73, pero el saldo final es 1714.74 (diferencia 0.01). |
| `fecha_fuera_de_periodo` | Un movimiento fechado un día después del periodo | Rechazado | Línea 18: la fecha 2026-04-01 está fuera del periodo 2026-03-01 a 2026-03-31. |
| `coma_en_banco_de_punto` | Un monto con coma decimal ("-85,40") en un banco de punto | Rechazado | Línea 2: el monto '-85,40' no es válido: van solo dígitos y hasta dos decimales con punto, sin separador de miles. |
| `lineas_vacias` | Líneas totalmente vacías en medio y al final | Entra | 17 movimientos |
| `fila_a_medias` | Una fila sin monto ni saldo | Rechazado | Línea 8: fila a medio llenar, falta el monto y el saldo. |
| `archivo_repetido` | El mismo archivo por segunda vez | Rechazado | Este archivo ya se importó: es el estado #1 (banco_sintetico/ahorros, 2026-03-01 a 2026-03-31). |
| `reexportado` | El mismo mes exportado de nuevo (otros bytes: fin de línea de Windows) | Rechazado | El periodo 2026-03-01 a 2026-03-31 se cruza con el del estado #1 (2026-03-01 a 2026-03-31) de la misma cuenta. |
| `separador_de_miles` | Un monto con separador de miles ("1,200.00") | Rechazado | Línea 3: el monto '1,200.00' no es válido: van solo dígitos y hasta dos decimales con punto, sin separador de miles. |
| `tres_decimales` | Un monto con tres decimales ("-85.400") | Rechazado | Línea 2: el monto '-85.400' no es válido: van solo dígitos y hasta dos decimales con punto, sin separador de miles. |
| `encabezado_de_otro_banco` | Encabezado con las columnas de otro banco | Rechazado | Línea 1: al encabezado le faltan las columnas 'Fecha', 'Descripción', 'Monto' y 'Saldo'; trae 'Date', 'Description', 'Amount' y 'Balance'. ¿Es un estado de Banco Sintético? |
| `fecha_imposible` | Una fecha que no existe (31/02/2026) | Rechazado | Línea 6: la fecha '31/02/2026' no tiene el formato %d/%m/%Y. |
| `fecha_mes_dia` | Fechas en mes/día/año en un banco de día/mes/año | Rechazado | Línea 11: la fecha '03/15/2026' no tiene el formato %d/%m/%Y.<br>Línea 12: la fecha '03/15/2026' no tiene el formato %d/%m/%Y.<br>Línea 13: la fecha '03/18/2026' no tiene el formato %d/%m/%Y.<br>(y 5 más) |
| `errores_compensados` | Dos montos corridos +1 y -1 centavo; el total cuadra | Rechazado | Línea 2: el saldo no encadena: 1000.00 - 85.39 = 914.61, pero la fila dice 914.60.<br>Línea 5: el saldo no encadena: 2092.60 - 10.00 = 2082.60, pero la fila dice 2082.61. |
| `errores_compensados_sin_saldo` | Lo mismo en un banco sin columna de saldo (límite conocido) | Entra | 17 movimientos |
| `primera_fila_corrida` | El monto de la primera fila corrido un centavo | Rechazado | Línea 2: el saldo no encadena: 1000.00 - 85.39 = 914.61, pero la fila dice 914.60.<br>El estado no cuadra: saldo anterior 1000.00 + créditos 1200.12 - débitos 485.38 = 1714.74, pero el saldo final es 1714.73 (diferencia -0.01). |
| `fila_perdida` | Falta una fila, con columna de saldo | Rechazado | Línea 9: el saldo no encadena: 2063.41 - 35.00 = 2028.41, pero la fila dice 1983.41.<br>El estado no cuadra: saldo anterior 1000.00 + créditos 1200.12 - débitos 440.39 = 1759.73, pero el saldo final es 1714.73 (diferencia -45.00). |
| `fila_perdida_sin_saldo` | Falta una fila, sin columna de saldo | Rechazado | El estado no cuadra: saldo anterior 1000.00 + créditos 1200.12 - débitos 440.39 = 1759.73, pero el saldo final es 1714.73 (diferencia -45.00). |
| `bloque_duplicado` | Tres filas pegadas dos veces, con columna de saldo | Rechazado | Línea 7: el saldo no encadena: 2064.11 - 22.00 = 2042.11, pero la fila dice 2092.60.<br>El estado no cuadra: saldo anterior 1000.00 + créditos 1200.12 - débitos 535.88 = 1664.24, pero el saldo final es 1714.73 (diferencia 50.49).<br>Las líneas 7 a 9 repiten las líneas 4 a 6 y suman -50.49, justo la diferencia. |
| `bloque_duplicado_sin_saldo` | Tres filas pegadas dos veces, sin columna de saldo | Rechazado | El estado no cuadra: saldo anterior 1000.00 + créditos 1200.12 - débitos 535.88 = 1664.24, pero el saldo final es 1714.73 (diferencia 50.49).<br>Las líneas 9 a 11 repiten las líneas 6 a 8 y suman -50.49, justo la diferencia. |
| `descripciones_con_formula` | Descripciones que empiezan con = + - @ | Entra | 17 movimientos |
| `con_bom` | UTF-8 con BOM | Entra | 17 movimientos |
| `archivo_enorme` | Un archivo un byte más grande que el tope | Rechazado | El archivo pesa 1048577 bytes y el tope es 1048576: no se leyó. |
| `signo_invertido` | Débitos y créditos cambiados de columna | Rechazado | El estado no cuadra: saldo anterior 1000.00 + créditos 485.39 - débitos 1200.12 = 285.27, pero el saldo final es 1714.73 (diferencia 1429.46). |
| `monto_cero` | Un movimiento en cero | Rechazado | Línea 4: el monto es cero. |
| `sin_movimientos` | Sin movimientos, pero con saldo final distinto del anterior | Rechazado | El estado no cuadra: saldo anterior 1000.00 + créditos 0.00 - débitos 0.00 = 1000.00, pero el saldo final es 1005.00 (diferencia 5.00). |
| `continuidad` | Abril empieza con un saldo distinto del que cerró marzo | Entra con aviso | El estado #1 (2026-03-01 a 2026-03-31) cerró con 1714.73 y este empieza con 1713.73: hay -1.00 de diferencia entre los dos. |
<!-- tabla-casos:fin -->

Los mensajes de los casos que no están en la tabla (codificación, CSV mal formado, columna repetida, descripción larga, cuenta que parece un número) están en `tests/test_ingesta.py`, también completos.
