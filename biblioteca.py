"""Escaneo de las bibliotecas y lectura/escritura de la etiqueta GENRE.

Raíces (GENEROS_ROOTS, «nombre=ruta_en_contenedor=ruta_en_host;…»). Por defecto, cada
carpeta es un álbum (las «CD1», «Disc 2» se juntan con la de encima). Además:
  GENEROS_PISTAS   raíces de canciones sueltas: una canción por item, y sus carpetas se
                   proponen como géneros (Techno/techno_groove → «techno groove»)
  GENEROS_SUELTAS  «raíz/subcarpeta,…»: dentro de esas subcarpetas, una canción por item
  GENEROS_DIFERIDAS raíces que aquí solo se leen: lo que se guarda queda en /api/mix/pendientes
                   para que lo escriba otro equipo (p. ej. el que tiene esa carpeta en local)
"""
import os
import re
import threading
import time

import mutagen
from mutagen.id3 import TCON

import db as D

AUDIO = (".flac", ".mp3", ".m4a", ".ogg", ".opus", ".wv", ".ape", ".aiff", ".aif", ".dsf")
def _lista(var, default=""):
    return {p.strip().strip("/") for p in os.environ.get(var, default).split(",") if p.strip()}


TRACK_ROOTS = _lista("GENEROS_PISTAS")
LOOSE_DIRS = _lista("GENEROS_SUELTAS")
DEFERRED_ROOTS = _lista("GENEROS_DIFERIDAS")
DISC_RE = re.compile(r"^(cd|disc|disk|disco)\s*\d+", re.I)


def roots():
    raw = os.environ.get("GENEROS_ROOTS", "musica=/music=/music")
    out = []
    for part in raw.split(";"):
        bits = part.split("=")
        if len(bits) == 3:
            out.append({"name": bits[0], "local": bits[1].rstrip("/"), "host": bits[2].rstrip("/")})
    return out


def to_local(host_path):
    for r in roots():
        if host_path == r["host"] or host_path.startswith(r["host"] + "/"):
            return r["local"] + host_path[len(r["host"]):], r
    return None, None


def to_host(local_path, r):
    return r["host"] + local_path[len(r["local"]):]


# ---------- etiquetas ----------

def _first(v):
    if isinstance(v, (list, tuple)):
        return str(v[0]) if v else ""
    return str(v) if v is not None else ""


def read_tags(path):
    """Etiquetas que interesan, de un fichero (easy=True unifica FLAC/MP3/M4A…)."""
    try:
        f = mutagen.File(path, easy=True)
    except Exception:
        return None
    if f is None or f.tags is None:
        return {}
    t = f.tags

    def g(*keys):
        for k in keys:
            try:
                if k in t and t[k]:
                    return t[k]
            except Exception:
                pass
        return None

    genre = g("genre")
    return {
        "genre": " / ".join(str(x) for x in genre) if isinstance(genre, list) and len(genre) > 1 else _first(genre),
        "artist": _first(g("albumartist", "album artist", "artist")),
        "album": _first(g("album")),
        "title": _first(g("title")),
        "year": _first(g("originaldate", "date", "year"))[:4],
        "mb_release": _first(g("musicbrainz_albumid")),
        "mb_rgroup": _first(g("musicbrainz_releasegroupid")),
    }


def write_genre(path, value):
    """GENRE = value (una sola etiqueta) en cualquier formato habitual."""
    f = mutagen.File(path)
    if f is None:
        raise ValueError(f"formato no reconocido: {path}")
    name = type(f).__name__
    if name in ("MP3", "EasyMP3", "AIFF", "WAVE", "DSF"):
        if f.tags is None:
            f.add_tags()
        f.tags.setall("TCON", [TCON(encoding=3, text=[value])])
    elif name in ("MP4", "EasyMP4"):
        f["\xa9gen"] = [value]
    else:                                   # FLAC, Ogg, Opus, WavPack, APE…
        if f.tags is None:
            f.add_tags()
        f.tags["genre"] = [value]
    f.save()


def album_files(local_dir):
    out = []
    for root, dirs, names in os.walk(local_dir):
        dirs.sort()
        for n in sorted(names):
            if n.lower().endswith(AUDIO):
                out.append(os.path.join(root, n))
    return out


# ---------- escaneo ----------

estado = {"activo": False, "fase": "", "hechos": 0, "total": 0, "inicio": 0, "fin": 0, "error": ""}
_lock = threading.Lock()


def _units(r):
    """(ruta_local_del_item, kind, [ficheros]) de una raíz."""
    track_mode = r["name"] in TRACK_ROOTS
    albums = {}
    for root, dirs, names in os.walk(r["local"]):
        dirs.sort()
        audio = sorted(n for n in names if n.lower().endswith(AUDIO))
        if not audio:
            continue
        rel = os.path.relpath(root, r["local"])
        if track_mode or f'{r["name"]}/{rel.split(os.sep)[0]}' in LOOSE_DIRS:
            for n in audio:
                p = os.path.join(root, n)
                yield p, "track", [p]
            continue
        key = os.path.dirname(root) if DISC_RE.match(os.path.basename(root)) else root
        albums.setdefault(key, []).extend(os.path.join(root, n) for n in audio)
    for k, files in albums.items():
        yield k, "album", files


def _auto_unify(con, item_id, raw):
    """Si GENRE ya está unificado («Super / otro», todos en la taxonomía), lo da por escrito."""
    if not raw:
        return False
    parts = [p.strip() for p in raw.split(D.SEP)]
    gs = [D.resolve(con, p) for p in parts]
    if not gs or any(g is None for g in gs) or gs[0]["parent_id"] is not None:
        return False
    if D.SEP.join(g["name"] for g in gs) != raw:
        return False
    con.execute("DELETE FROM item_genre WHERE item_id = ?", (item_id,))
    con.executemany("INSERT INTO item_genre (item_id, pos, genre_id) VALUES (?, ?, ?)",
                    [(item_id, i, g["id"]) for i, g in enumerate(gs)])
    con.execute("UPDATE item SET status = 'escrito' WHERE id = ?", (item_id,))
    return True


def scan(only=None):
    if not _lock.acquire(blocking=False):
        return False
    try:
        estado.update(activo=True, hechos=0, total=0, inicio=time.time(), fin=0, error="")
        con = D.connect()
        for r in roots():
            if only and r["name"] not in only:
                continue
            if not os.path.isdir(r["local"]):
                continue
            estado["fase"] = f"listando {r['name']}"
            units = list(_units(r))
            estado["total"] += len(units)
            estado["fase"] = f"leyendo {r['name']}"
            con.execute("UPDATE item SET seen = 0 WHERE root = ?", (r["name"],))
            known = {row["path"]: dict(row) for row in con.execute(
                "SELECT id, path, mtime FROM item WHERE root = ?", (r["name"],))}
            for i, (local, kind, files) in enumerate(units):
                host = to_host(local, r)
                try:
                    mtime = max(os.path.getmtime(f) for f in files)
                except OSError:
                    continue
                k = known.get(host)
                if k and k["mtime"] == mtime:
                    con.execute("UPDATE item SET seen = 1 WHERE id = ?", (k["id"],))
                else:
                    t = read_tags(files[0]) or {}
                    vals = (r["name"], kind, t.get("artist"), t.get("album"), t.get("title") if kind == "track" else None,
                            t.get("year"), t.get("mb_release"), t.get("mb_rgroup"), t.get("genre"), len(files), mtime)
                    if k:
                        con.execute("""UPDATE item SET root=?, kind=?, artist=?, album=?, title=?, year=?, mb_release=?,
                                       mb_rgroup=?, genre_raw=?, tracks=?, mtime=?, seen=1 WHERE id=?""", vals + (k["id"],))
                        iid = k["id"]
                    else:
                        iid = con.execute("""INSERT INTO item (root, kind, artist, album, title, year, mb_release,
                                             mb_rgroup, genre_raw, tracks, mtime, path) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                                          vals + (host,)).lastrowid
                    if not con.execute("SELECT 1 FROM item_genre WHERE item_id = ?", (iid,)).fetchone():
                        _auto_unify(con, iid, t.get("genre"))
                estado["hechos"] += 1
                if i % 200 == 0:
                    con.commit()
            con.execute("DELETE FROM item WHERE root = ? AND seen = 0", (r["name"],))
            con.commit()
        con.close()
        estado["fase"] = "terminado"
    except Exception as e:
        estado["error"] = repr(e)
        estado["fase"] = "error"
    finally:
        estado.update(activo=False, fin=time.time())
        _lock.release()
    return True


def scan_async(only=None):
    if estado["activo"]:
        return False
    threading.Thread(target=scan, args=(only,), daemon=True).start()
    return True
