# Imagen única para el bot y la Mini App; docker-compose.yml decide cuál corre.
# No lleva datos ni secretos: .env llega por env_file y la base por volumen.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY pyproject.toml README.md ./
COPY src/ src/
COPY config/ config/
# Editable a propósito: la taxonomía se busca en config/ relativo al paquete
# (extraction/categorias.py), así que el código tiene que quedar en el layout del repo.
RUN pip install --no-cache-dir -e . \
    && useradd --uid 1000 --create-home app \
    && mkdir -p data/processed \
    && chown app:app data/processed

USER app
ENTRYPOINT ["family-expense-bot"]
