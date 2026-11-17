# generos

Una web para poner orden en la etiqueta **GENRE** de tu música: decides una sola taxonomía (supergénero → subgénero), la web te propone géneros para cada disco y, cuando das el visto bueno, los escribe en los ficheros con un formato único (`Rock / Post-Rock`).

- **Discos:** la lista de tu biblioteca, con filtros por estado (pendiente, guardado, escrito). Para cada disco muestra lo que dice el fichero y las propuestas de Last.fm, Discogs y MusicBrainz (y de escuchowsky, si lo tienes). Puedes elegir para un disco o para un lote.
- **Taxonomía:** crea supergéneros y subgéneros, y alias para que «synth-pop», «synth pop» y «Synthpop» sean lo mismo.
- **Jugar:** lotes de discos pendientes parecidos a un género o a un artista para etiquetarlos de golpe, con puntos, nivel y rachas. Con [AudioMuse-AI](https://github.com/NeptuneHub/AudioMuse-AI) los lotes salen por parecido de sonido.

Solo cambia `GENRE`; artista, álbum y título nunca se tocan. Puedes solo **guardar** la elección y escribirla después («Escribir marcados»), o guardar y escribir a la vez. Cada escritura guarda el valor anterior (tabla `history`).

## Instalación

Necesitas Docker.

```bash
git clone https://github.com/volteret4/generos.git && cd generos && cp .env.example .env
```

En `.env`, pon en `MUSICA` la carpeta de tu música (un disco por carpeta). Las claves de Last.fm y Discogs son opcionales, pero sin ellas hay menos propuestas. Después:

```bash
docker compose up -d --build
```

Abre <http://localhost:8785> y pulsa **Escanear**. La primera vez lee todas las etiquetas; después solo lo que haya cambiado. Para que se reescanee cada noche, añade esta línea a tu cron:

```bash
0 1 * * * docker exec generos python3 -c 'import biblioteca; biblioteca.scan()'
```

La web no tiene contraseña y puede escribir en tu música: no la abras a internet.

## Varias bibliotecas

`GENEROS_ROOTS` admite varias raíces (`nombre=ruta_en_el_contenedor=ruta_en_el_anfitrión`, separadas por `;`). Cada una necesita su volumen en `docker-compose.yml`. Por defecto, cada carpeta es un disco (las «CD1» o «Disc 2» se juntan con la de encima). Además:

| Variable | Para qué |
|---|---|
| `GENEROS_PISTAS` | Raíces de canciones sueltas, por ejemplo una carpeta de DJ: cada canción es un item, y sus subcarpetas (`Techno/minimal`) se proponen como géneros |
| `GENEROS_SUELTAS` | `raíz/subcarpeta`: dentro de una raíz de discos, una subcarpeta de canciones sueltas |
| `GENEROS_DIFERIDAS` | Raíces que aquí solo se leen: lo guardado espera en `GET /api/mix/pendientes` para que lo escriba otro equipo, que lo confirma con `POST /api/mix/hecho` |

Ejemplo con una biblioteca y una carpeta de DJ que solo se escribe desde el portátil:

```bash
GENEROS_ROOTS=discos=/music/discos=/srv/musica;dj=/music/dj=/srv/dj
GENEROS_PISTAS=dj
GENEROS_DIFERIDAS=dj
```

## Variables

| Variable | Para qué |
|---|---|
| `MUSICA` | Carpeta de música del anfitrión (solo en `docker-compose.yml`) |
| `GENEROS_ROOTS`, `GENEROS_PISTAS`, `GENEROS_SUELTAS`, `GENEROS_DIFERIDAS` | Bibliotecas (ver arriba) |
| `LASTFM_API_KEY` | Propuestas de Last.fm |
| `DISCOGS_TOKEN` | Propuestas de Discogs |
| `ESCUCHOWSKY_DB`, `RYM_GENRES_JSON` | Opcional: etiquetas guardadas por escuchowsky |
| `AUDIOMUSE_URL`, `AUDIOMUSE_TOKEN`, `AUDIOMUSE_SERVERS` | Opcional: parecidos por sonido en «Jugar» (`AUDIOMUSE_SERVERS`: `raíz=biblioteca de AudioMuse;…`) |
| `GENEROS_DB` | Base de la web (por defecto `data/generos.db`) |

Las respuestas de Last.fm, Discogs y MusicBrainz se guardan 30 días.

Es un módulo de [REPEAT-ONE](https://volteret4.github.io/repeat-one-web/), y funciona igual por separado.
