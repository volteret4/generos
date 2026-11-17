"""Modo «Jugar»: lotes de discos pendientes parecidos a un género o a un artista para etiquetarlos de golpe.

Lote por género:  discos ya etiquetados con ese género (semillas) → AudioMuse similar_tracks →
                  los pendientes que salen, más los pendientes cuya etiqueta del fichero ya apunta
                  a ese género. Si hay pocas semillas, AudioMuse CLAP (texto → audio) como apoyo.
Lote por artista: sus pendientes, y los de artistas parecidos según AudioMuse similar_artists.

Cada disco lleva sus propios géneros (las pistas sueltas de un mismo disco van juntas).
Sin tiempo: puntos por disco etiquetado, nivel, racha de días y logros.

AudioMuse (opcional): AUDIOMUSE_URL, AUDIOMUSE_TOKEN y AUDIOMUSE_SERVERS
(«raíz=biblioteca de AudioMuse;…», p. ej. «musica=navidrome admin»).
"""
import json
import math
import os
import random
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta

import requests
from flask import abort, jsonify, request, send_file

import biblioteca as B
import db as D
import fuentes as F

AM_URL = os.environ.get("AUDIOMUSE_URL", "").rstrip("/")
AM_TOKEN = os.environ.get("AUDIOMUSE_TOKEN", "")
AM_SERVERS = dict(p.split("=", 1) for p in os.environ.get("AUDIOMUSE_SERVERS", "").split(";") if "=" in p)
AM_TTL = 30 * 86400
AUDIO_MIME = {".flac": "audio/flac", ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".mp4": "audio/mp4",
              ".aac": "audio/aac", ".ogg": "audio/ogg", ".oga": "audio/ogg", ".opus": "audio/ogg",
              ".wav": "audio/wav", ".aif": "audio/aiff", ".aiff": "audio/aiff", ".wma": "audio/x-ms-wma"}
XP_ITEM = 10
XP_LOTE = 20          # bonus por guardar 10 o más de golpe
SEEDS = 8

SCHEMA = """
CREATE TABLE IF NOT EXISTS juego_log (
    id      INTEGER PRIMARY KEY,
    ts      TEXT DEFAULT CURRENT_TIMESTAMP,
    day     TEXT NOT NULL,
    item_id INTEGER NOT NULL,
    genres  TEXT,
    mode    TEXT,
    seed    TEXT,
    xp      INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS juego_log_day ON juego_log(day);
CREATE TABLE IF NOT EXISTS juego_skip (
    seed    TEXT NOT NULL,
    item_id INTEGER NOT NULL,
    PRIMARY KEY (seed, item_id)
);
CREATE TABLE IF NOT EXISTS juego_logro (
    key  TEXT PRIMARY KEY,
    ts   TEXT DEFAULT CURRENT_TIMESTAMP
);
"""

TITULOS = ["Oyente", "Curioso", "Crate digger", "Coleccionista", "Archivero", "Taxónomo", "Melómano",
           "Bibliotecario sónico", "Gurú de géneros", "Oráculo del GENRE"]


def con():
    c = D.connect()
    c.executescript(SCHEMA)
    return c


# ---------------------------------------------------------------- AudioMuse

def am_ok():
    return bool(AM_URL and AM_TOKEN)


def _am_get(c, path, params, ttl=AM_TTL):
    key = "am:" + path + ":" + json.dumps(params, sort_keys=True)
    row = c.execute("SELECT data, fetched FROM cache WHERE key = ?", (key,)).fetchone()
    if row and time.time() - row["fetched"] < ttl:
        return json.loads(row["data"])
    try:
        r = requests.get(AM_URL + path, params=params, timeout=30, headers={"Authorization": f"Bearer {AM_TOKEN}"})
        data = r.json() if r.status_code == 200 else []
    except Exception:
        return []
    c.execute("INSERT OR REPLACE INTO cache (key, data, fetched) VALUES (?, ?, ?)", (key, json.dumps(data), time.time()))
    c.commit()
    return data


def _am_post(c, path, body, ttl=AM_TTL):
    key = "am:" + path + ":" + json.dumps(body, sort_keys=True)
    row = c.execute("SELECT data, fetched FROM cache WHERE key = ?", (key,)).fetchone()
    if row and time.time() - row["fetched"] < ttl:
        return json.loads(row["data"])
    try:
        r = requests.post(AM_URL + path, json=body, timeout=90, headers={"Authorization": f"Bearer {AM_TOKEN}"})
        data = r.json() if r.status_code == 200 else {}
    except Exception:
        return {}
    c.execute("INSERT OR REPLACE INTO cache (key, data, fetched) VALUES (?, ?, ?)", (key, json.dumps(data), time.time()))
    c.commit()
    return data


def similar_tracks(artist, title, root, n=40):
    if not (am_ok() and artist and title):
        return []
    c = con()
    params = {"artist": artist, "title": title, "n": n, "eliminate_duplicates": "true"}
    if AM_SERVERS.get(root):
        params["server"] = AM_SERVERS[root]
    data = _am_get(c, "/api/similar_tracks", params)
    return data if isinstance(data, list) else []


def similar_artists(artist, n=15):
    if not (am_ok() and artist):
        return []
    data = _am_get(con(), "/api/similar_artists", {"artist": artist, "n": n})
    return data if isinstance(data, list) else []


def clap(query, limit=80):
    if not (am_ok() and query):
        return []
    data = _am_post(con(), "/api/clap/search", {"query": query, "limit": limit})
    return data.get("results", []) if isinstance(data, dict) else []


# ---------------------------------------------------------------- índices

def _pending_index(c, seed_key):
    """Pendientes no descartados para esta semilla, indexados por (artista, disco) y (artista, título)."""
    skip = {r[0] for r in c.execute("SELECT item_id FROM juego_skip WHERE seed = ?", (seed_key,))}
    by_album, by_title, by_artist, rows = defaultdict(list), defaultdict(list), defaultdict(list), {}
    for r in c.execute("SELECT * FROM item WHERE status = 'pendiente'"):
        if r["id"] in skip:
            continue
        rows[r["id"]] = r
        a = D.norm(r["artist"])
        by_artist[a].append(r["id"])
        if r["kind"] == "album":
            by_album[(a, D.norm(r["album"]))].append(r["id"])
        else:
            by_title[(a, D.norm(r["title"]))].append(r["id"])
            by_album[(a, D.norm(r["album"]))].append(r["id"])
    return rows, by_album, by_title, by_artist


def _match(hit, by_album, by_title):
    """Items pendientes que corresponden a un resultado de AudioMuse."""
    out = set()
    for a in {D.norm(hit.get("author")), D.norm(hit.get("album_artist"))} - {""}:
        out.update(by_title.get((a, D.norm(hit.get("title"))), []))
        out.update(by_album.get((a, D.norm(hit.get("album"))), []))
    return out


def _seed_track(it):
    """(artista, título) de una pista del item, para pedir parecidos a AudioMuse."""
    if it["kind"] == "track":
        return it["artist"], it["title"]
    local, _ = B.to_local(it["path"])
    files = B.album_files(local) if local else []
    random.shuffle(files)
    for f in files[:3]:
        t = B.read_tags(f) or {}
        if t.get("title"):
            return t.get("artist") or it["artist"], t["title"]
    return None, None


def _genre_family(c, gid):
    """El género y, si es supergénero, sus subgéneros."""
    ids = {gid}
    ids.update(r[0] for r in c.execute("SELECT id FROM genre WHERE parent_id = ?", (gid,)))
    return ids


def _suggest_for(c, gid):
    g = c.execute("SELECT * FROM genre WHERE id = ?", (gid,)).fetchone()
    return [g["parent_id"], gid] if g["parent_id"] else [gid]


def _artist_labels(c, artist_norm):
    """Combinación de géneros más usada en los discos ya etiquetados de un artista."""
    combos = Counter()
    for r in c.execute("SELECT id, artist FROM item WHERE status != 'pendiente'"):
        if D.norm(r["artist"]) != artist_norm:
            continue
        gs = tuple(x[0] for x in c.execute("SELECT genre_id FROM item_genre WHERE item_id = ? ORDER BY pos", (r["id"],)))
        if gs:
            combos[gs] += 1
    return list(combos.most_common(1)[0][0]) if combos else []


# ---------------------------------------------------------------- lotes

def lote_genero(c, gid, n):
    seed_key = f"g:{gid}"
    rows, by_album, by_title, _ = _pending_index(c, seed_key)
    family = _genre_family(c, gid)
    name = c.execute("SELECT name FROM genre WHERE id = ?", (gid,)).fetchone()["name"]
    score, reasons = defaultdict(float), defaultdict(list)

    # 1. la etiqueta del fichero ya apunta a este género
    for iid, r in rows.items():
        for part in D.split_genres(r["genre_raw"]):
            g = D.resolve(c, part)
            if g and g["id"] in family:
                score[iid] += 1.5
                reasons[iid].append(f"etiqueta: {part}")
                break

    # 2. parecidos (AudioMuse) a discos tuyos ya etiquetados con este género
    marks = ",".join("?" * len(family))
    seeds = [dict(r) for r in c.execute(
        f"SELECT DISTINCT i.* FROM item i JOIN item_genre ig ON ig.item_id = i.id "
        f"WHERE ig.genre_id IN ({marks}) AND i.status != 'pendiente'", list(family))]
    random.shuffle(seeds)
    seeds = seeds[:SEEDS]
    if am_ok() and seeds:
        def one(s):
            artist, title = _seed_track(s)
            return s, similar_tracks(artist, title, s["root"])
        with ThreadPoolExecutor(max_workers=4) as ex:
            for s, hits in ex.map(one, seeds):
                label = f"{s['artist']} – {s['album'] or s['title']}"
                for h in hits:
                    sim = max(0.0, 1 - float(h.get("distance", 1)) * 4)   # distancias típicas 0–0.25
                    for iid in _match(h, by_album, by_title):
                        score[iid] += 0.5 + sim
                        if len(reasons[iid]) < 3 and f"suena como {label}" not in reasons[iid]:
                            reasons[iid].append(f"suena como {label}")

    # 3. pocas semillas: búsqueda por texto en AudioMuse (más floja)
    if am_ok() and len(seeds) < 3:
        for h in clap(name):
            for iid in _match(h, by_album, by_title):
                score[iid] += 0.6 * float(h.get("similarity", 0.5))
                if "AudioMuse: «" + name + "»" not in reasons[iid]:
                    reasons[iid].append("AudioMuse: «" + name + "»")

    return _pack(c, rows, score, reasons, n, _suggest_for(c, gid), seed_key, len(seeds))


def lote_artista(c, artist, n):
    a_norm = D.norm(artist)
    seed_key = f"a:{a_norm}"
    rows, by_album, by_title, by_artist = _pending_index(c, seed_key)
    score, reasons = defaultdict(float), defaultdict(list)
    own = _artist_labels(c, a_norm)
    per_item = {}

    for iid in by_artist.get(a_norm, []):
        score[iid] += 10
        reasons[iid].append("mismo artista")
        if own:
            per_item[iid] = own

    for sa in similar_artists(artist):
        other = D.norm(sa.get("artist"))
        if not other or other == a_norm:
            continue
        sim = max(0.0, 1 - float(sa.get("divergence", 0.1)) * 10)
        labels = _artist_labels(c, other)
        for iid in by_artist.get(other, []):
            score[iid] += 2 + 3 * sim
            reasons[iid].append(f"artista parecido a {artist}")
            if labels:
                per_item[iid] = labels

    return _pack(c, rows, score, reasons, n, own, seed_key, 0, per_item)


def _file_genres(c, raw):
    """Géneros de la taxonomía que ya dice la etiqueta del fichero, con el supergénero primero."""
    found = []
    for part in D.split_genres(raw):
        g = D.resolve(c, part)
        if g and g["id"] not in [x["id"] for x in found]:
            found.append(g)
    if not found:
        return []
    supers = [g for g in found if g["parent_id"] is None]
    sup = supers[0]["id"] if supers else found[0]["parent_id"]
    out = [sup]
    for g in found:
        if g["id"] != sup and g["id"] not in out:
            out.append(g["id"])
    return out


def _merge(base, extra):
    """Añade a base los géneros de extra que sean del mismo supergénero."""
    if not base or not extra or base[0] != extra[0]:
        return list(base)
    return list(base) + [g for g in extra[1:] if g not in base]


def _group_key(r):
    """Un disco = una tarjeta: van juntas las pistas sueltas del mismo disco y las copias
    del mismo disco en otra raíz."""
    a, al = D.norm(r["artist"]), D.norm(r["album"])
    if not a or not al:
        return ("i", r["id"])
    return ("d", a, al)


def _pack(c, rows, score, reasons, n, suggest, seed_key, n_seeds, per_item=None):
    members = defaultdict(list)
    for iid, r in rows.items():
        members[_group_key(r)].append(iid)
    gscore, greasons = defaultdict(float), defaultdict(list)
    for iid, sc in score.items():
        k = _group_key(rows[iid])
        gscore[k] = max(gscore[k], sc)
        for why in reasons[iid]:
            if why not in greasons[k] and len(greasons[k]) < 3:
                greasons[k].append(why)
    best = sorted(gscore, key=lambda k: -gscore[k])[:n]
    top = max([gscore[k] for k in best] or [1])
    artist_cache = {}
    items = []
    for k in best:
        ids = sorted(members[k], key=lambda i: (rows[i]["kind"] != "album", rows[i]["root"], rows[i]["path"] or ""))
        r = dict(rows[ids[0]])
        r["ids"] = ids
        r["roots"] = sorted({rows[i]["root"] for i in ids})
        r["n_tracks"] = sum(1 for i in ids if rows[i]["kind"] == "track")
        r["genre_raw"] = next((rows[i]["genre_raw"] for i in ids if rows[i]["genre_raw"]), None)
        r["score"] = round(gscore[k] / top, 2)
        r["reasons"] = greasons[k]
        fichero = _file_genres(c, r["genre_raw"])
        a = D.norm(r["artist"])
        if a not in artist_cache:
            artist_cache[a] = _artist_labels(c, a)
        artista = (per_item or {}).get(ids[0]) or artist_cache[a]
        # sugerencia de este disco: su artista > el género elegido (+ subgéneros del fichero) > el fichero
        if artista:
            sug = _merge(_merge(artista, suggest), fichero)
        elif suggest:
            sug = _merge(suggest, fichero)
        else:
            sug = fichero
        r["suggest"] = sug
        alts = {}
        for name, gs in (("fichero", fichero), ("artista", artista), ("lote", list(suggest or []))):
            if gs and gs != sug and gs not in alts.values():
                alts[name] = gs
        r["alts"] = alts
        items.append(r)
    return {"items": items, "suggest": suggest, "seed": seed_key, "seeds": n_seeds, "audiomuse": am_ok()}


# ---------------------------------------------------------------- un disco suelto (DSub ebm)

def buscar_disco(c, artist, album, title):
    """Items del disco que suena en un reproductor: mismo álbum (normalizado) y artista compatible;
    si no hay álbum, la pista por artista + título. Junta las copias de varias raíces."""
    a, al, ti = D.norm(artist), D.norm(album), D.norm(title)
    cands = []
    if al:
        for r in c.execute("SELECT * FROM item WHERE album IS NOT NULL"):
            if D.norm(r["album"]) == al:
                cands.append(r)
        exact = [r for r in cands if D.norm(r["artist"]) == a]
        loose = [r for r in cands if a and (a in D.norm(r["artist"]) or D.norm(r["artist"]) in a)]
        if exact:
            cands = exact
        elif loose:
            cands = loose
        elif len({D.norm(r["artist"]) for r in cands}) != 1:
            cands = []                      # mismo nombre de disco de artistas distintos: no adivino
    if not cands and ti:
        cands = [r for r in c.execute("SELECT * FROM item WHERE kind = 'track' AND title IS NOT NULL")
                 if D.norm(r["title"]) == ti and D.norm(r["artist"]) == a]
    return cands


def disco(c, artist, album, title, web=True):
    rows = buscar_disco(c, artist, album, title)
    if not rows:
        return {"found": False}
    rows.sort(key=lambda r: (r["kind"] != "album", r["root"], r["path"]))
    first = dict(rows[0])
    ids = [r["id"] for r in rows]
    actuales = []
    for r in rows:
        actuales = [x[0] for x in c.execute("SELECT genre_id FROM item_genre WHERE item_id = ? ORDER BY pos", (r["id"],))]
        if actuales:
            break
    raw = next((r["genre_raw"] for r in rows if r["genre_raw"]), None)
    fichero = _file_genres(c, raw)
    artista = _artist_labels(c, D.norm(first["artist"]))
    sug = actuales or (_merge(artista, fichero) if artista else fichero)
    alts = {}
    for name, gs in (("actuales", actuales), ("artista", artista), ("fichero", fichero)):
        if gs and gs != sug and gs not in alts.values():
            alts[name] = gs
    props = []
    try:
        first["genre_raw"] = raw
        for pr in F.proposals(c, first, web=web):
            props.append({"text": pr["text"], "genre_id": pr["genre"]["id"] if pr["genre"] else None,
                          "sources": pr["sources"]})
    except Exception:
        pass
    statuses = Counter(r["status"] for r in rows)
    return {"found": True, "ids": ids, "artist": first["artist"], "album": first["album"] or first["title"],
            "year": first["year"], "roots": sorted({r["root"] for r in rows}), "status": statuses.most_common(1)[0][0],
            "genre_raw": raw, "genres": actuales, "suggest": sug, "alts": alts, "proposals": props[:25]}


# ---------------------------------------------------------------- puntos y logros

def nivel(xp):
    lvl = int(math.sqrt(xp / 50)) + 1
    nxt = 50 * lvl * lvl
    return {"nivel": lvl, "titulo": TITULOS[min(lvl - 1, len(TITULOS) - 1)], "xp": xp, "siguiente": nxt,
            "desde": 50 * (lvl - 1) ** 2}


def racha(c):
    days = {r[0] for r in c.execute("SELECT DISTINCT day FROM juego_log")}
    d, n = date.today(), 0
    if d.isoformat() not in days:
        d -= timedelta(days=1)          # la racha sigue viva hasta que acabe hoy
    while d.isoformat() in days:
        n += 1
        d -= timedelta(days=1)
    return n


LOGROS = [
    ("primer_lote", "Primer lote", "Guarda tu primer lote"),
    ("cien", "Centenario", "100 discos etiquetados jugando"),
    ("quinientos", "Medio millar", "500 discos etiquetados jugando"),
    ("mil", "Mil y una noches", "1000 discos etiquetados jugando"),
    ("lote_grande", "A lo grande", "Un lote de 30 o más de golpe"),
    ("dia_50", "Maratón", "50 en un día"),
    ("racha_7", "Semana completa", "7 días seguidos"),
    ("explorador", "Explorador", "10 supergéneros distintos en un día"),
    ("temp_vacio", "temp a cero", "Ningún pendiente en temp"),
    ("artista_completo", "Discografía al día", "Deja sin pendientes a un artista"),
]


def _check_logros(c, lote_size=0, artist_done=False):
    total = c.execute("SELECT count(*) FROM juego_log WHERE xp > 0").fetchone()[0]
    today = date.today().isoformat()
    hoy = c.execute("SELECT count(*) FROM juego_log WHERE day = ? AND xp > 0", (today,)).fetchone()[0]
    supers_hoy = set()
    for (gs,) in c.execute("SELECT genres FROM juego_log WHERE day = ?", (today,)):
        if gs:
            supers_hoy.add(json.loads(gs)[0])
    temp_pend = c.execute("SELECT count(*) FROM item WHERE root = 'temp' AND status = 'pendiente'").fetchone()[0]
    cond = {
        "primer_lote": total > 0, "cien": total >= 100, "quinientos": total >= 500, "mil": total >= 1000,
        "lote_grande": lote_size >= 30, "dia_50": hoy >= 50, "racha_7": racha(c) >= 7,
        "explorador": len(supers_hoy) >= 10, "temp_vacio": temp_pend == 0, "artista_completo": artist_done,
    }
    have = {r[0] for r in c.execute("SELECT key FROM juego_logro")}
    new = [k for k, ok in cond.items() if ok and k not in have]
    c.executemany("INSERT OR IGNORE INTO juego_logro (key) VALUES (?)", [(k,) for k in new])
    names = {k: (t, d) for k, t, d in LOGROS}
    return [{"key": k, "titulo": names[k][0], "desc": names[k][1]} for k in new]


def stats(c):
    xp = c.execute("SELECT COALESCE(sum(xp), 0) FROM juego_log").fetchone()[0]
    today = date.today().isoformat()
    hoy = c.execute("SELECT count(*) FROM juego_log WHERE day = ? AND xp > 0", (today,)).fetchone()[0]
    pend = c.execute("SELECT count(*) FROM item WHERE status = 'pendiente'").fetchone()[0]
    total = c.execute("SELECT count(*) FROM item").fetchone()[0]
    dias = [{"day": d, "n": n} for d, n in c.execute(
        "SELECT day, count(*) FROM juego_log WHERE day >= ? AND xp > 0 GROUP BY day ORDER BY day",
        ((date.today() - timedelta(days=29)).isoformat(),))]
    have = {r[0]: r[1] for r in c.execute("SELECT key, ts FROM juego_logro")}
    logros = [{"key": k, "titulo": t, "desc": d, "ts": have.get(k)} for k, t, d in LOGROS]
    return {**nivel(xp), "hoy": hoy, "racha": racha(c), "pendientes": pend, "total": total, "dias": dias,
            "logros": logros, "audiomuse": am_ok()}


# ---------------------------------------------------------------- rutas

def register(app, save_fn, item_dict):

    @app.get("/api/juego/stats")
    def juego_stats():
        return jsonify(stats(con()))

    @app.get("/api/juego/artistas")
    def juego_artistas():
        q = D.norm(request.args.get("q", ""))
        c = con()
        cnt = Counter()
        names = {}
        for r in c.execute("SELECT artist FROM item WHERE status = 'pendiente' AND artist IS NOT NULL"):
            k = D.norm(r["artist"])
            if q and q not in k:
                continue
            cnt[k] += 1
            names.setdefault(k, r["artist"])
        return jsonify([{"artist": names[k], "pendientes": n} for k, n in cnt.most_common(30)])

    @app.get("/api/juego/lote")
    def juego_lote():
        c = con()
        n = min(int(request.args.get("n", 24)), 100)
        if request.args.get("genre_id"):
            gid = int(request.args["genre_id"])
            if not c.execute("SELECT 1 FROM genre WHERE id = ?", (gid,)).fetchone():
                abort(404)
            res = lote_genero(c, gid, n)
        elif request.args.get("artist"):
            res = lote_artista(c, request.args["artist"].strip(), n)
        else:
            return jsonify(error="falta genre_id o artist"), 400
        for it in res["items"]:
            it["genres"] = item_dict(c, c.execute("SELECT * FROM item WHERE id = ?", (it["id"],)).fetchone())["genres"]
        return jsonify(res)

    @app.get("/api/juego/disco")
    def juego_disco():
        """El disco que suena en DSub ebm: géneros actuales, propuesta, alternativas y fuentes."""
        c = con()
        return jsonify(disco(c, request.args.get("artist", ""), request.args.get("album", ""),
                             request.args.get("title", ""), web=request.args.get("web", "1") == "1"))

    @app.post("/api/juego/hecho")
    def juego_hecho():
        """Guarda los géneros de cada disco: items = [{ids: [...], genres: [...]}, …] y suma puntos."""
        c = con()
        body = request.json or {}
        groups = [([int(i) for i in g.get("ids", [])], [int(x) for x in g.get("genres", [])])
                  for g in body.get("items", [])]
        groups = [(ids, gs) for ids, gs in groups if ids]
        if not groups:
            return jsonify(error="nada que guardar"), 400
        before = stats(c)
        artists = set()
        logged = 0
        try:
            for ids, gs in groups:
                first = True
                for iid in ids:
                    prev = c.execute("SELECT status, artist FROM item WHERE id = ?", (iid,)).fetchone()
                    save_fn(c, iid, gs)
                    if prev and prev["status"] == "pendiente":
                        artists.add(D.norm(prev["artist"]))
                        c.execute("INSERT INTO juego_log (day, item_id, genres, mode, seed, xp) VALUES (?, ?, ?, ?, ?, ?)",
                                  (date.today().isoformat(), iid, json.dumps(gs), body.get("mode"), body.get("seed"),
                                   XP_ITEM if first else 0))
                        if first:
                            logged += 1
                        first = False
        except ValueError as e:
            c.rollback()
            return jsonify(error=str(e)), 400
        bonus = XP_LOTE if logged >= 10 else 0
        if bonus:
            c.execute("UPDATE juego_log SET xp = xp + ? WHERE id = (SELECT max(id) FROM juego_log)", (bonus,))
        done_artists = []
        pending_artists = {D.norm(r["artist"]) for r in c.execute("SELECT artist FROM item WHERE status = 'pendiente'")}
        for a in artists:
            if a and a not in pending_artists:
                name = next((r["artist"] for r in c.execute("SELECT artist FROM item WHERE artist IS NOT NULL")
                             if D.norm(r["artist"]) == a), a)
                done_artists.append(name)
        logros = _check_logros(c, logged, bool(done_artists))
        c.commit()
        after = stats(c)
        return jsonify(ok=True, xp=after["xp"] - before["xp"], bonus=bonus, stats=after, logros=logros,
                       subida=after["nivel"] > before["nivel"], artistas_completos=done_artists)

    @app.post("/api/juego/no")
    def juego_no():
        """Este item no va en este lote: no volverá a salir para esta semilla."""
        c = con()
        body = request.json or {}
        c.executemany("INSERT OR IGNORE INTO juego_skip (seed, item_id) VALUES (?, ?)",
                      [(body.get("seed", ""), int(i)) for i in body.get("ids", [])])
        c.commit()
        return jsonify(ok=True)

    def _files(iid):
        c = con()
        it = c.execute("SELECT * FROM item WHERE id = ?", (iid,)).fetchone()
        if not it:
            abort(404)
        local, _ = B.to_local(it["path"])
        if not local:
            abort(404)
        return it, local

    @app.get("/api/juego/portada/<int:iid>")
    def juego_portada(iid):
        it, local = _files(iid)
        folder = local if it["kind"] == "album" else os.path.dirname(local)
        cands = []
        for root, dirs, names in os.walk(folder):
            for nm in names:
                if nm.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
                    pri = 0 if os.path.splitext(nm.lower())[0] in ("cover", "folder", "front") else 1
                    cands.append((pri, root.count(os.sep), os.path.join(root, nm)))
            if it["kind"] == "track":
                break
        if cands:
            return send_file(sorted(cands)[0][2], max_age=86400)
        files = [local] if it["kind"] == "track" else B.album_files(local)
        for f in files[:2]:
            try:
                import mutagen
                m = mutagen.File(f)
                pics = getattr(m, "pictures", None)
                data = pics[0].data if pics else None
                if not data and m is not None and m.tags is not None:
                    if hasattr(m.tags, "getall"):
                        apic = m.tags.getall("APIC")
                        data = apic[0].data if apic else None
                    elif "covr" in m.tags:
                        data = bytes(m.tags["covr"][0])
                if data:
                    import io
                    return send_file(io.BytesIO(data), mimetype="image/jpeg", max_age=86400)
            except Exception:
                pass
        abort(404)

    def _tracks(iid):
        it, local = _files(iid)
        return it, ([local] if it["kind"] == "track" else B.album_files(local))

    @app.get("/api/juego/pistas")
    def juego_pistas():
        """Pistas de un disco (todos sus items), en orden, para ir probando un trozo de cada una."""
        out = []
        for iid in [int(x) for x in request.args.get("ids", "").split(",") if x.strip().isdigit()][:20]:
            try:
                it, files = _tracks(iid)
            except Exception:
                continue
            for n, f in enumerate(files):
                t = B.read_tags(f) or {}
                out.append({"id": iid, "n": n, "root": it["root"],
                            "title": t.get("title") or os.path.splitext(os.path.basename(f))[0],
                            "artist": t.get("artist") or it["artist"]})
        return jsonify(out)

    @app.get("/api/juego/audio/<int:iid>")
    def juego_audio(iid):
        """Una pista del item: la n-ésima con ?n= (en el orden de /api/juego/pistas) o una al azar."""
        it, files = _tracks(iid)
        if not files:
            abort(404)
        n = request.args.get("n")
        f = files[int(n) % len(files)] if n is not None else random.choice(files)
        mime = AUDIO_MIME.get(os.path.splitext(f)[1].lower())
        return send_file(f, mimetype=mime, conditional=True, max_age=3600)
