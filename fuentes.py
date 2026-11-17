"""Propuestas de género para un item.

fichero      GENRE actual (cualquier separador)
carpeta      solo raíces de canciones sueltas (GENEROS_PISTAS): sus carpetas
escuchowsky  etiquetas de MusicBrainz/Last.fm guardadas en must_hear.db (por MBID o nombre)
lastfm       album.getTopTags + artist.getTopTags
discogs      géneros y estilos del master
musicbrainz  géneros del release group
Las respuestas web se guardan en la tabla cache (30 días).
"""
import json
import os
import re
import sqlite3
import time

import requests

import biblioteca as B
import db as D

UA = {"User-Agent": "generos/1.0 (+https://github.com/volteret4/generos)"}
TTL = 30 * 86400
ESCUCHOWSKY_DB = os.environ.get("ESCUCHOWSKY_DB", "/data/escuchowsky/must_hear.db")
RYM_JSON = os.environ.get("RYM_GENRES_JSON", "/data/escuchowsky/genres.json")
NOISE = re.compile(r"^(seen live|albums? i own|favou?rites?|my |owned|vinyl|cd|flac|mp3|\d{2,4}s?|"
                   r"under \d+|spotify|check out|love|beautiful|awesome|best|good|female vocalists?|"
                   r"male vocalists?|.*(album|track)s?$)", re.I)


def _cached(con, key, fn):
    row = con.execute("SELECT data, fetched FROM cache WHERE key = ?", (key,)).fetchone()
    if row and time.time() - row["fetched"] < TTL:
        return json.loads(row["data"])
    try:
        data = fn()
    except Exception as e:
        return {"error": type(e).__name__}
    con.execute("INSERT OR REPLACE INTO cache (key, data, fetched) VALUES (?, ?, ?)",
                (key, json.dumps(data), time.time()))
    con.commit()
    return data


# ---------- fuentes ----------

def from_file(item):
    return [(g, 1.0) for g in D.split_genres(item["genre_raw"])]


def from_folder(item):
    if item["root"] not in B.TRACK_ROOTS:
        return []
    root = next((r for r in B.roots() if r["name"] == item["root"]), None)
    path = item["path"]
    if root and path.startswith(root["host"] + "/"):
        path = path[len(root["host"]) + 1:]
    parts = path.split("/")[:-1]
    return [(p.replace("_", " ").replace("-", " ").strip(), 1.0) for p in parts if p.strip()]


def from_escuchowsky(item):
    if not os.path.exists(ESCUCHOWSKY_DB):
        return []
    con = sqlite3.connect(f"file:{ESCUCHOWSKY_DB}?mode=ro", uri=True, timeout=10)
    try:
        ids = []
        if item["mb_rgroup"]:
            ids = [r[0] for r in con.execute("SELECT id FROM albums WHERE release_group_mbid = ?", (item["mb_rgroup"],))]
        if not ids and item["mb_release"]:
            ids = [r[0] for r in con.execute("SELECT id FROM albums WHERE mbid = ?", (item["mb_release"],))]
        if not ids and item["artist"] and item["album"]:
            ids = [r[0] for r in con.execute(
                "SELECT al.id FROM albums al JOIN artists ar ON ar.id = al.artist_id "
                "WHERE al.name = ? COLLATE NOCASE AND ar.name = ? COLLATE NOCASE LIMIT 5",
                (item["album"], item["artist"]))]
        if not ids:
            return []
        q = ",".join("?" * len(ids))
        rows = con.execute(f"SELECT g.name, max(coalesce(ag.weight, 1)) FROM album_genres ag "
                           f"JOIN genres g ON g.id = ag.genre_id WHERE ag.album_id IN ({q}) GROUP BY lower(g.name)",
                           ids).fetchall()
        return [(n, float(w or 1)) for n, w in rows]
    except sqlite3.Error:
        return []
    finally:
        con.close()


def from_lastfm(con, item):
    key = os.environ.get("LASTFM_API_KEY")
    if not key or not item["artist"]:
        return []

    def call(method, **kw):
        r = requests.get("https://ws.audioscrobbler.com/2.0/", params=dict(method=method, api_key=key,
                         format="json", autocorrect=1, **kw), headers=UA, timeout=10)
        return r.json().get("toptags", {}).get("tag", [])

    out = []
    if item["album"] and item["kind"] == "album":
        tags = _cached(con, f"lfm:album:{item['artist']}|{item['album']}",
                       lambda: call("album.gettoptags", artist=item["artist"], album=item["album"]))
        out += [(t["name"], int(t.get("count", 0)) / 100) for t in (tags if isinstance(tags, list) else [])[:12]]
    if item["kind"] == "track" and item["title"]:
        tags = _cached(con, f"lfm:track:{item['artist']}|{item['title']}",
                       lambda: call("track.gettoptags", artist=item["artist"], track=item["title"]))
        out += [(t["name"], int(t.get("count", 0)) / 100) for t in (tags if isinstance(tags, list) else [])[:12]]
    tags = _cached(con, f"lfm:artist:{item['artist']}", lambda: call("artist.gettoptags", artist=item["artist"]))
    out += [(t["name"], int(t.get("count", 0)) / 200) for t in (tags if isinstance(tags, list) else [])[:8]]
    return [(n, w) for n, w in out if w > 0.05 and not NOISE.match(n)]


def from_discogs(con, item):
    tok = os.environ.get("DISCOGS_TOKEN")
    if not tok or not item["artist"] or not (item["album"] or item["title"]):
        return []

    def call():
        params = {"artist": item["artist"], "type": "master", "per_page": 3}
        if item["kind"] == "album":
            params["release_title"] = item["album"]
        else:
            params["track"] = item["title"]
        r = requests.get("https://api.discogs.com/database/search", params=params,
                         headers={**UA, "Authorization": f"Discogs token={tok}"}, timeout=10)
        res = r.json().get("results", [])
        if not res:
            return {}
        return {"genre": res[0].get("genre", []), "style": res[0].get("style", [])}

    d = _cached(con, f"discogs:{item['kind']}:{item['artist']}|{item['album'] or item['title']}", call)
    return [(g, 0.8) for g in d.get("style", [])] + [(g, 0.6) for g in d.get("genre", [])]


_mb_last = [0.0]


def from_musicbrainz(con, item):
    def get(url, **params):
        wait = 1.1 - (time.time() - _mb_last[0])
        if wait > 0:
            time.sleep(wait)
        _mb_last[0] = time.time()
        return requests.get(url, params=dict(fmt="json", **params), headers=UA, timeout=10).json()

    def call():
        rg = item["mb_rgroup"]
        if not rg and item["kind"] == "album" and item["artist"] and item["album"]:
            q = f'releasegroup:"{item["album"]}" AND artist:"{item["artist"]}"'
            res = get("https://musicbrainz.org/ws/2/release-group", query=q, limit=1).get("release-groups", [])
            rg = res[0]["id"] if res and res[0].get("score", 0) >= 90 else None
        if not rg:
            return []
        d = get(f"https://musicbrainz.org/ws/2/release-group/{rg}", inc="genres")
        return [(g["name"], g.get("count", 1)) for g in d.get("genres", [])]

    if item["kind"] != "album":
        return []
    d = _cached(con, f"mb:{item['mb_rgroup'] or item['artist']}|{item['album']}", call)
    if isinstance(d, dict):
        return []
    top = max([c for _, c in d] or [1])
    return [(n, c / top) for n, c in d]


# ---------- todo junto ----------

def proposals(con, item, web=True):
    """[{text, norm, genre (de la taxonomía o None), sources: [..], score}] ordenado."""
    srcs = [("fichero", from_file(item)), ("carpeta", from_folder(item)), ("escuchowsky", from_escuchowsky(item))]
    if web:
        srcs += [("lastfm", from_lastfm(con, item)), ("discogs", from_discogs(con, item)),
                 ("musicbrainz", from_musicbrainz(con, item))]
    agg = {}
    for src, pairs in srcs:
        for text, w in pairs:
            n = D.norm(text)
            if not n:
                continue
            g = D.resolve(con, text)
            k = f"g{g['id']}" if g else n
            a = agg.setdefault(k, {"text": g["name"] if g else text, "norm": n, "genre": g, "sources": [], "score": 0.0})
            if src not in a["sources"]:
                a["sources"].append(src)
            a["score"] += float(w) + (1.0 if src == "fichero" else 0.0)
    out = list(agg.values())
    out.sort(key=lambda a: (-(a["genre"] is not None), -len(a["sources"]), -a["score"]))
    return out[:40]


# ---------- árbol de RYM (escuchowsky/genres.json) para sugerir el padre ----------

_rym = None


def _rym_index():
    global _rym
    if _rym is not None:
        return _rym
    _rym = {}
    try:
        tree = json.load(open(RYM_JSON, encoding="utf-8"))
    except (OSError, ValueError):
        return _rym

    def walk(nodes, path):
        for n in nodes:
            p = path + [n["name"]]
            _rym.setdefault(D.norm(n["name"]), p)
            walk(n.get("subgenres", []), p)

    walk(tree, [])
    return _rym


def suggest_parent(con, name):
    """Supergénero de la taxonomía que propone el árbol de RYM (o por palabras del nombre)."""
    path = _rym_index().get(D.norm(name), [])
    for anc in reversed(path[:-1]):
        g = D.resolve(con, anc)
        if g:
            return (g if g["parent_id"] is None else dict(con.execute(
                "SELECT * FROM genre WHERE id = ?", (g["parent_id"],)).fetchone())), path
    words = re.findall(r"[a-z0-9]+", name.lower())
    for w in reversed(words):
        g = D.resolve(con, w)
        if g:
            return (g if g["parent_id"] is None else dict(con.execute(
                "SELECT * FROM genre WHERE id = ?", (g["parent_id"],)).fetchone())), path
    return None, path
