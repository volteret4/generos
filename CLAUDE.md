# generos

Flask (`app.py`) + web estática (`static/`). SQLite en `GENEROS_DB` (esquema en `db.py`, se crea solo).

- `biblioteca.py`: raíces (`GENEROS_ROOTS`), escaneo incremental y lectura/escritura de GENRE con mutagen. Solo se escribe GENRE.
- `fuentes.py`: propuestas (fichero, carpeta, escuchowsky, Last.fm, Discogs, MusicBrainz) con caché de 30 días.
- `juego.py`: modo «Jugar» y AudioMuse opcional.
- Una raíz en `GENEROS_DIFERIDAS` nunca se escribe aquí: va a `mix_pending` (`/api/mix/pendientes`, `/api/mix/hecho`).
- Nada de rutas ni nombres de bibliotecas concretas en el código: todo por entorno (`.env.example`).
