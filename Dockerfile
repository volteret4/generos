FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1 \
    GENEROS_DB=/app/data/generos.db \
    ESCUCHOWSKY_DB=/data/escuchowsky/must_hear.db \
    RYM_GENRES_JSON=/data/escuchowsky/genres.json

EXPOSE 5000

CMD ["python", "app.py"]
