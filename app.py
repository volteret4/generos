"""Web para unificar la etiqueta GENRE de una o varias bibliotecas de música.

GENRE final = «Supergénero / otro / otro» (separador único « / »), supergénero primero.
"""
import os
import threading

from flask import Flask, abort, jsonify, request, send_from_directory

import biblioteca as B
import db as D
import fuentes as F
import juego as J

app = Flask(__name__, static_folder="static", static_url_path="/static")
_write_lock = threading.Lock()


def con():
    return D.connect()


def item_dict(c, row):
    it = dict(row)
    it["genres"] = [dict(r) for r in c.execute(
        "SELECT g.id, g.name, g.parent_id, ig.pos FROM item_genre ig JOIN genre g ON g.id = ig.genre_id "
        "WHERE ig.item_id = ? ORDER BY ig.pos", (it["id"],))]
    return it


def genre_string(c, item_id):
    names = [r["name"] for r in c.execute(
        "SELECT g.name FROM item_genre ig JOIN genre g ON g.id = ig.genre_id WHERE ig.item_id = ? ORDER BY ig.pos",
        (item_id,))]
    return D.SEP.join(names)


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


# ---------- items ----------

@app.get("/api/stats")
def stats():
    c = con()
    rows = c.execute("SELECT root, status, count(*) n FROM item GROUP BY 1, 2").fetchall()
    out = {}
    for r in rows:
        out.setdefault(r["root"], {})[r["status"]] = r["n"]
    pend = c.execute("SELECT count(*) FROM mix_pending WHERE done IS NULL").fetchone()[0]
    return jsonify(roots=out, mix_pending=pend, scan=B.estado)


@app.get("/api/items")
def items():
    c = con()
    where, args = [], []
    for k in ("root", "status", "kind"):
        if request.args.get(k):
            where.append(f"{k} = ?")
            args.append(request.args[k])
    q = request.args.get("q", "").strip()
    if q:
        where.append("(artist LIKE ? OR album LIKE ? OR title LIKE ? OR genre_raw LIKE ? OR path LIKE ?)")
        args += [f"%{q}%"] * 5
    if request.args.get("genre_id"):
        where.append("id IN (SELECT item_id FROM item_genre WHERE genre_id = ?)")
        args.append(request.args["genre_id"])
    sql = "FROM item" + (" WHERE " + " AND ".join(where) if where else "")
    total = c.execute("SELECT count(*) " + sql, args).fetchone()[0]
    limit = min(int(request.args.get("limit", 100)), 500)
    offset = int(request.args.get("offset", 0))
    rows = c.execute(f"SELECT * {sql} ORDER BY root, artist COLLATE NOCASE, year, album COLLATE NOCASE, path "
                     f"LIMIT ? OFFSET ?", args + [limit, offset]).fetchall()
    return jsonify(total=total, items=[item_dict(c, r) for r in rows])


@app.get("/api/item/<int:iid>")
def item(iid):
    c = con()
    row = c.execute("SELECT * FROM item WHERE id = ?", (iid,)).fetchone()
    if not row:
        abort(404)
    it = item_dict(c, row)
    it["proposals"] = F.proposals(c, it, web=request.args.get("web", "1") == "1")
    return jsonify(it)


def _save(c, iid, genre_ids):
    if not genre_ids:
        raise ValueError("hace falta al menos un género")
    gs = [c.execute("SELECT * FROM genre WHERE id = ?", (g,)).fetchone() for g in genre_ids]
    if any(g is None for g in gs):
        raise ValueError("género desconocido")
    if gs[0]["parent_id"] is not None:
        raise ValueError("el primer género tiene que ser un supergénero")
    if len(set(genre_ids)) != len(genre_ids):
        raise ValueError("género repetido")
    c.execute("DELETE FROM item_genre WHERE item_id = ?", (iid,))
    c.executemany("INSERT INTO item_genre (item_id, pos, genre_id) VALUES (?, ?, ?)",
                  [(iid, i, g) for i, g in enumerate(genre_ids)])
    c.execute("UPDATE item SET status = 'guardado' WHERE id = ?", (iid,))


@app.post("/api/item/<int:iid>/generos")
def save_item(iid):
    c = con()
    try:
        _save(c, iid, [int(g) for g in request.json.get("genres", [])])
    except ValueError as e:
        return jsonify(error=str(e)), 400
    c.commit()
    return jsonify(ok=True, genre=genre_string(c, iid))


@app.post("/api/lote")
def save_batch():
    """Mismos géneros para varios items."""
    c = con()
    ids = [int(i) for i in request.json.get("ids", [])]
    genres = [int(g) for g in request.json.get("genres", [])]
    try:
        for iid in ids:
            _save(c, iid, genres)
    except ValueError as e:
        return jsonify(error=str(e)), 400
    c.commit()
    return jsonify(ok=True, n=len(ids))


def _apply(c, iid):
    """Escribe GENRE en los ficheros del item, o lo deja pendiente si su raíz es diferida."""
    it = c.execute("SELECT * FROM item WHERE id = ?", (iid,)).fetchone()
    value = genre_string(c, iid)
    if not it or not value:
        return {"id": iid, "error": "sin géneros guardados"}
    if it["root"] in B.DEFERRED_ROOTS:
        c.execute("INSERT INTO mix_pending (path, genre) VALUES (?, ?)", (it["path"], value))
        c.execute("INSERT INTO history (item_id, path, old, new) VALUES (?, ?, ?, ?)",
                  (iid, it["path"], it["genre_raw"], value))
        c.execute("UPDATE item SET status = 'escrito', written_at = CURRENT_TIMESTAMP WHERE id = ?", (iid,))
        return {"id": iid, "pendiente": True}
    local, _ = B.to_local(it["path"])
    files = [local] if it["kind"] == "track" else B.album_files(local)
    if not files:
        return {"id": iid, "error": "no encuentro los ficheros"}
    errors = []
    for f in files:
        try:
            B.write_genre(f, value)
        except Exception as e:
            errors.append(f"{os.path.basename(f)}: {e}")
    c.execute("INSERT INTO history (item_id, path, old, new) VALUES (?, ?, ?, ?)",
              (iid, it["path"], it["genre_raw"], value))
    if not errors:
        mtime = max(os.path.getmtime(f) for f in files)
        c.execute("UPDATE item SET status = 'escrito', written_at = CURRENT_TIMESTAMP, genre_raw = ?, mtime = ? "
                  "WHERE id = ?", (value, mtime, iid))
    return {"id": iid, "files": len(files), "errors": errors}


@app.post("/api/aplicar")
def apply():
    ids = [int(i) for i in request.json.get("ids", [])]
    with _write_lock:
        c = con()
        res = [_apply(c, iid) for iid in ids]
        c.commit()
    return jsonify(results=res)


# ---------- taxonomía ----------

@app.get("/api/taxonomia")
def get_taxonomy():
    return jsonify(D.taxonomy(con()))


@app.post("/api/genero")
def new_genre():
    c = con()
    j = request.json
    parent = j.get("parent_id")
    if j.get("parent_name") and not parent:          # padre nuevo: se crea como supergénero
        p = D.resolve(c, j["parent_name"])
        parent = p["id"] if p else D.add_genre(c, j["parent_name"])
    try:
        gid = D.add_genre(c, j["name"], int(parent) if parent else None)
        for a in j.get("aliases", []):
            c.execute("INSERT OR REPLACE INTO alias (norm, genre_id) VALUES (?, ?)", (D.norm(a), gid))
    except (ValueError, Exception) as e:
        return jsonify(error=str(e)), 400
    c.commit()
    return jsonify(ok=True, id=gid)


@app.patch("/api/genero/<int:gid>")
def edit_genre(gid):
    """Renombrar o cambiar de padre. Los items con ese género vuelven a «guardado» (hay que reescribirlos)."""
    c = con()
    j = request.json
    g = c.execute("SELECT * FROM genre WHERE id = ?", (gid,)).fetchone()
    if not g:
        abort(404)
    if "name" in j and j["name"].strip() != g["name"]:
        name = j["name"].strip()
        if not name or any(s in name for s in "/;,"):
            return jsonify(error="nombre vacío o con un separador ( / ; , )"), 400
        c.execute("INSERT OR REPLACE INTO alias (norm, genre_id) VALUES (?, ?)", (D.norm(g["name"]), gid))
        c.execute("UPDATE genre SET name = ? WHERE id = ?", (name, gid))
        c.execute("INSERT OR REPLACE INTO alias (norm, genre_id) VALUES (?, ?)", (D.norm(name), gid))
    if "parent_id" in j:
        p = j["parent_id"]
        if p is not None:
            pr = c.execute("SELECT parent_id FROM genre WHERE id = ?", (p,)).fetchone()
            if not pr or pr["parent_id"] is not None or int(p) == gid:
                return jsonify(error="el padre tiene que ser otro supergénero"), 400
            if c.execute("SELECT 1 FROM genre WHERE parent_id = ?", (gid,)).fetchone():
                return jsonify(error="tiene subgéneros: no puede pasar a ser subgénero"), 400
        c.execute("UPDATE genre SET parent_id = ? WHERE id = ?", (p, gid))
    c.execute("UPDATE item SET status = 'guardado' WHERE status = 'escrito' AND id IN "
              "(SELECT item_id FROM item_genre WHERE genre_id = ?)", (gid,))
    c.commit()
    return jsonify(ok=True)


@app.delete("/api/genero/<int:gid>")
def delete_genre(gid):
    c = con()
    if c.execute("SELECT 1 FROM item_genre WHERE genre_id = ?", (gid,)).fetchone():
        return jsonify(error="hay discos con este género"), 400
    if c.execute("SELECT 1 FROM genre WHERE parent_id = ?", (gid,)).fetchone():
        return jsonify(error="tiene subgéneros"), 400
    c.execute("DELETE FROM genre WHERE id = ?", (gid,))
    c.commit()
    return jsonify(ok=True)


@app.post("/api/alias")
def add_alias():
    c = con()
    j = request.json
    n = D.norm(j.get("text", ""))
    if not n:
        return jsonify(error="vacío"), 400
    c.execute("INSERT OR REPLACE INTO alias (norm, genre_id) VALUES (?, ?)", (n, int(j["genre_id"])))
    c.commit()
    return jsonify(ok=True)


@app.delete("/api/alias/<norm>")
def del_alias(norm):
    c = con()
    own = {D.norm(r["name"]) for r in c.execute("SELECT name FROM genre")}
    if norm in own:
        return jsonify(error="es el nombre del propio género"), 400
    c.execute("DELETE FROM alias WHERE norm = ?", (norm,))
    c.commit()
    return jsonify(ok=True)


@app.get("/api/sugerir_padre")
def suggest():
    g, path = F.suggest_parent(con(), request.args.get("name", ""))
    return jsonify(parent=g, rym=path)


# ---------- escaneo y raíces diferidas ----------

@app.post("/api/escanear")
def scan():
    only = request.json.get("roots") if request.is_json else None
    return jsonify(started=B.scan_async(only), scan=B.estado)


@app.get("/api/mix/pendientes")
def mix_pending():
    rows = con().execute("SELECT id, path, genre FROM mix_pending WHERE done IS NULL ORDER BY id").fetchall()
    return jsonify([dict(r) for r in rows])


@app.post("/api/mix/hecho")
def mix_done():
    c = con()
    for i in request.json.get("ids", []):
        c.execute("UPDATE mix_pending SET done = CURRENT_TIMESTAMP WHERE id = ?", (int(i),))
    c.commit()
    return jsonify(ok=True)


J.register(app, _save, item_dict)


if __name__ == "__main__":
    con().close()
    app.run(host=os.environ.get("FLASK_HOST", "0.0.0.0"), port=int(os.environ.get("FLASK_PORT", 5000)),
            threaded=True)
