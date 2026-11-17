"""Base de datos de la web de géneros.

genre      taxonomía: supergénero (parent_id NULL) o subgénero (parent_id = su supergénero)
alias      variante normalizada → género («synth-pop», «synth pop» → Synthpop)
item       álbum (una carpeta) o pista (un fichero, en raíces de canciones sueltas)
item_genre géneros unificados de cada item, en orden (pos 0 = supergénero)
history    valor anterior de GENRE de cada escritura (para deshacer)
mix_pending escrituras de raíces diferidas (GENEROS_DIFERIDAS), que aplica otro equipo
cache      respuestas de Last.fm / Discogs / MusicBrainz
"""
import os
import re
import sqlite3
import unicodedata

DB_PATH = os.environ.get("GENEROS_DB", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "generos.db"))

SEP = " / "          # separador único en la etiqueta GENRE (Navidrome y el panel de DeaDBeeF lo separan)
SPLIT_RE = re.compile(r"\s*(?:;|\||/|,|\\|\x00)\s*")

SCHEMA = """
CREATE TABLE IF NOT EXISTS genre (
    id        INTEGER PRIMARY KEY,
    name      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    parent_id INTEGER REFERENCES genre(id) ON DELETE RESTRICT,
    created   TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS alias (
    norm     TEXT PRIMARY KEY,
    genre_id INTEGER NOT NULL REFERENCES genre(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS item (
    id         INTEGER PRIMARY KEY,
    path       TEXT NOT NULL UNIQUE,      -- ruta en el host (la que ve DeaDBeeF)
    root       TEXT NOT NULL,             -- nombre de la raíz (GENEROS_ROOTS)
    kind       TEXT NOT NULL,             -- album | track
    artist     TEXT, album TEXT, title TEXT, year TEXT,
    mb_release TEXT, mb_rgroup TEXT,
    genre_raw  TEXT,                      -- GENRE actual del fichero
    tracks     INTEGER,
    mtime      REAL,
    status     TEXT DEFAULT 'pendiente',  -- pendiente | guardado | escrito
    written_at TEXT,
    seen       INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS item_root ON item(root, status);
CREATE TABLE IF NOT EXISTS item_genre (
    item_id  INTEGER NOT NULL REFERENCES item(id) ON DELETE CASCADE,
    pos      INTEGER NOT NULL,
    genre_id INTEGER NOT NULL REFERENCES genre(id) ON DELETE CASCADE,
    PRIMARY KEY (item_id, pos)
);
CREATE TABLE IF NOT EXISTS history (
    id      INTEGER PRIMARY KEY,
    item_id INTEGER, path TEXT, old TEXT, new TEXT,
    ts      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS mix_pending (
    id      INTEGER PRIMARY KEY,
    path    TEXT NOT NULL,                -- ruta en el anfitrión
    genre   TEXT NOT NULL,
    created TEXT DEFAULT CURRENT_TIMESTAMP,
    done    TEXT
);
CREATE TABLE IF NOT EXISTS cache (
    key     TEXT PRIMARY KEY,
    data    TEXT,
    fetched REAL
);
"""

# Taxonomía inicial (se puede cambiar todo desde la web)
SEED = {
    "Techno": ["Deep Techno", "Melodic Techno", "Industrial Techno", "EBM", "Minimal Techno", "Groove Techno",
               "Funky Techno", "Dub Techno", "Trance", "Oldschool Techno", "Techno Band"],
    "Breakbeat": [],
    "House": ["Deep House", "Dream House", "Jazz House", "Funky House", "Gospel House", "Progressive House",
              "Oldschool House", "House Band"],
    "IDM": [],
    "Electro": [],
    "Pop": ["Synthpop", "New Wave", "Dance Pop", "Oldies Pop"],
    "Jazz": ["Spiritual Jazz"],
    "Hip Hop": [],
    "Metal": ["Stoner Metal", "Black Metal", "Heavy Metal", "Power Metal", "Progressive Metal", "Nu Metal"],
    "Rock": ["Progressive Rock", "Shoegaze", "Oldies Rock"],
    "Ambient": ["Dark Ambient"],
    "Dance": [],
    "Soul": [],
    "Funk": [],
    "Disco": [],
}
# variantes habituales → género
SEED_ALIASES = {
    "Synthpop": ["synth-pop", "synth pop", "electropop"],
    "Hip Hop": ["hip-hop", "hiphop", "rap"],
    "New Wave": ["newwave"],
    "IDM": ["intelligent dance music"],
    "Stoner Metal": ["stoner", "stoner rock"],
    "Nu Metal": ["nu-metal", "numetal"],
    "Progressive House": ["progresive house"],
    "Progressive Rock": ["prog rock", "prog-rock", "progresive rock"],
    "Progressive Metal": ["prog metal", "progresive metal"],
    "Oldschool Techno": ["old school techno", "old-school techno"],
    "Oldschool House": ["old school house", "old-school house"],
    "Spiritual Jazz": ["espiritual jazz"],
    "EBM": ["electronic body music"],
}


def norm(s):
    """Clave para comparar géneros: sin acentos, minúsculas, solo letras y números."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower().replace("&", "and")
    return re.sub(r"[^a-z0-9]+", "", s)


def split_genres(raw):
    """«Techno, Synth-Pop / Electro» → ['Techno', 'Synth-Pop', 'Electro'] (sin repetidos)."""
    out, seen = [], set()
    for part in SPLIT_RE.split(raw or ""):
        p = part.strip()
        if p and norm(p) and norm(p) not in seen:
            seen.add(norm(p))
            out.append(p)
    return out


def connect():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("PRAGMA journal_mode = WAL")
    db.executescript(SCHEMA)
    if not db.execute("SELECT 1 FROM genre LIMIT 1").fetchone():
        seed(db)
    return db


def add_genre(db, name, parent_id=None):
    name = name.strip()
    if not name or "/" in name or ";" in name or "," in name:
        raise ValueError("nombre vacío o con un separador ( / ; , )")
    if parent_id is not None:
        p = db.execute("SELECT parent_id FROM genre WHERE id = ?", (parent_id,)).fetchone()
        if not p:
            raise ValueError("el padre no existe")
        if p["parent_id"] is not None:
            raise ValueError("el padre tiene que ser un supergénero")
    cur = db.execute("INSERT INTO genre (name, parent_id) VALUES (?, ?)", (name, parent_id))
    gid = cur.lastrowid
    db.execute("INSERT OR REPLACE INTO alias (norm, genre_id) VALUES (?, ?)", (norm(name), gid))
    return gid


def seed(db):
    for sup, subs in SEED.items():
        sid = add_genre(db, sup)
        for sub in subs:
            add_genre(db, sub, sid)
    for name, variants in SEED_ALIASES.items():
        gid = db.execute("SELECT id FROM genre WHERE name = ?", (name,)).fetchone()["id"]
        for v in variants:
            db.execute("INSERT OR IGNORE INTO alias (norm, genre_id) VALUES (?, ?)", (norm(v), gid))
    db.commit()


def resolve(db, text):
    """Género de la taxonomía para un texto, o None."""
    r = db.execute("SELECT g.* FROM alias a JOIN genre g ON g.id = a.genre_id WHERE a.norm = ?",
                   (norm(text),)).fetchone()
    return dict(r) if r else None


def taxonomy(db):
    rows = [dict(r) for r in db.execute("SELECT id, name, parent_id FROM genre ORDER BY name COLLATE NOCASE")]
    aliases = {}
    for r in db.execute("SELECT norm, genre_id FROM alias"):
        aliases.setdefault(r["genre_id"], []).append(r["norm"])
    counts = dict(db.execute("SELECT genre_id, count(DISTINCT item_id) FROM item_genre GROUP BY 1").fetchall())
    supers = [dict(r, subs=[], aliases=aliases.get(r["id"], []), items=counts.get(r["id"], 0))
              for r in rows if r["parent_id"] is None]
    by_id = {s["id"]: s for s in supers}
    for r in rows:
        if r["parent_id"] is not None and r["parent_id"] in by_id:
            by_id[r["parent_id"]]["subs"].append(dict(r, aliases=aliases.get(r["id"], []),
                                                      items=counts.get(r["id"], 0)))
    return supers
