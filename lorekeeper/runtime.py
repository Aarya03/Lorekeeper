"""Shared runtime infrastructure: configuration, storage, metrics, and fanout."""
from __future__ import annotations

import json
import os
import queue
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from prometheus_client import Counter, Gauge, Histogram
ROOT = Path(__file__).resolve().parent.parent

try:

    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")

except ImportError:

    pass



DB_PATH = Path(os.environ.get("LOREKEEPER_DB", ROOT / "data" / "lorekeeper.db"))

DB_PATH.parent.mkdir(parents=True, exist_ok=True)

DATABASE_URL=os.environ.get("DATABASE_URL","").strip()

def embedding_dimensions():
    try: dimensions=int(os.environ.get("EMBEDDING_DIMENSIONS","768"))
    except ValueError as exc: raise RuntimeError("EMBEDDING_DIMENSIONS must be an integer") from exc
    if not 1<=dimensions<=4096: raise RuntimeError("EMBEDDING_DIMENSIONS must be between 1 and 4096")
    return dimensions

db_pool=None

TOKEN_FILE = DB_PATH.parent / "twitch_tokens.bin"

lock = threading.RLock()

listeners: set[queue.Queue] = set()

stats = {"started_at": time.time(), "received": 0, "reactions": 0, "replay_running": False}

events_total=Counter("lorekeeper_events_total","Normalized events accepted by projection",["source","event_type"])

duplicates_total=Counter("lorekeeper_duplicate_events_total","Duplicate event IDs rejected")

projection_errors=Counter("lorekeeper_projection_errors_total","Projection errors")

projection_latency=Histogram("lorekeeper_projection_seconds","Event projection duration")

eventsub_connected=Gauge("lorekeeper_eventsub_connected","Whether Twitch EventSub is connected")

redis_rate_errors=Counter("lorekeeper_redis_rate_limit_errors_total","Redis rate limiter failures with local fallback")

embedding_requests=Counter("lorekeeper_embedding_requests_total","Lore candidates embedded by the configured provider",["provider"])

embedding_fallbacks=Counter("lorekeeper_embedding_fallbacks_total","Embedding calls that fell back to deterministic feature hashing")

guess_embedding_latency=Histogram("lorekeeper_guess_embedding_latency_seconds","Time from Twitch guess acceptance to semantic heat result",buckets=(.05,.1,.2,.35,.5,.75,1,1.5,2,3,5,10))
guess_moderation_rejections=Counter("lorekeeper_guess_moderation_rejections_total","Chat guesses rejected by word safety checks")

redis_client=None


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

def time_cutoff(seconds):
    return (datetime.now(timezone.utc)-timedelta(seconds=seconds)).isoformat(timespec="seconds").replace("+00:00","Z")

@contextmanager
def db():
    if DATABASE_URL:
        global db_pool
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool
        if db_pool is None:
            db_pool=ConnectionPool(DATABASE_URL,min_size=1,max_size=12,kwargs={"row_factory":dict_row},open=True)
        with db_pool.connection() as con:
            yield PgCompat(con)
        return
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=10000")
    con.execute("PRAGMA synchronous=FULL")
    con.execute("PRAGMA secure_delete=ON")
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()

class PgCompat:
    def __init__(self,connection): self.connection=connection
    def execute(self,query,params=()): return self.connection.execute(query.replace("?","%s"),params)

def init_db():
    with db() as c:
        if DATABASE_URL:
            schema=[
              "CREATE EXTENSION IF NOT EXISTS vector",
              "CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, type TEXT NOT NULL, viewer_id TEXT, viewer TEXT, text TEXT, source TEXT, mode TEXT, at TEXT NOT NULL, payload TEXT NOT NULL)",
              f"CREATE TABLE IF NOT EXISTS lore (id TEXT PRIMARY KEY, kind TEXT NOT NULL, summary TEXT NOT NULL, status TEXT NOT NULL, subjects TEXT NOT NULL, evidence TEXT NOT NULL, importance DOUBLE PRECISION NOT NULL, created_at TEXT NOT NULL, mentions INTEGER NOT NULL DEFAULT 1, embedding vector({embedding_dimensions()}))",
              "CREATE TABLE IF NOT EXISTS utterances (id TEXT PRIMARY KEY, text TEXT NOT NULL, trigger TEXT NOT NULL, viewer TEXT, at TEXT NOT NULL)",
              "CREATE TABLE IF NOT EXISTS moments (id TEXT PRIMARY KEY, title TEXT NOT NULL, detail TEXT NOT NULL, score DOUBLE PRECISION NOT NULL, at TEXT NOT NULL)",
              "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
              "CREATE TABLE IF NOT EXISTS forgotten (viewer_id TEXT PRIMARY KEY, at TEXT NOT NULL)",
              "CREATE TABLE IF NOT EXISTS viewers (viewer_id TEXT PRIMARY KEY, display_name TEXT NOT NULL, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, memory_enabled INTEGER NOT NULL DEFAULT 1)",
              "CREATE TABLE IF NOT EXISTS privacy_audit (id TEXT PRIMARY KEY, viewer_id TEXT NOT NULL, action TEXT NOT NULL, completed_at TEXT NOT NULL)",
              "CREATE TABLE IF NOT EXISTS whisper_recipients (utc_day TEXT NOT NULL, viewer_id TEXT NOT NULL, sent_at TEXT NOT NULL, PRIMARY KEY(utc_day,viewer_id))",
              "CREATE TABLE IF NOT EXISTS overlay_commands (seq BIGSERIAL PRIMARY KEY, command_id TEXT NOT NULL UNIQUE, payload TEXT NOT NULL, created_at TEXT NOT NULL)",
              "CREATE TABLE IF NOT EXISTS lore_enrichments (lore_id TEXT PRIMARY KEY, summary TEXT NOT NULL, relic_title TEXT NOT NULL, power_name TEXT NOT NULL, power_text TEXT NOT NULL, boss_name TEXT NOT NULL, boss_epithet TEXT NOT NULL, provider TEXT NOT NULL, updated_at TEXT NOT NULL)",
            ]
            for statement in schema: c.execute(statement)
            vector_type=c.execute("SELECT format_type(atttypid,atttypmod) AS vector_type FROM pg_attribute WHERE attrelid='lore'::regclass AND attname='embedding' AND NOT attisdropped").fetchone()["vector_type"]
            expected_type=f"vector({embedding_dimensions()})"
            if vector_type!=expected_type:
                c.execute(f"ALTER TABLE lore ALTER COLUMN embedding TYPE {expected_type} USING NULL::{expected_type}")
            c.execute("INSERT INTO settings VALUES('muted','false') ON CONFLICT(key) DO NOTHING")
            from .story import init_schema as init_story_schema
            init_story_schema(c)
            from .relics import init_schema as init_relic_schema
            init_relic_schema(c)
            from .game import init_schema as init_game_schema
            init_game_schema(c)
            from .signal_game import init_schema as init_signal_schema
            init_signal_schema(c)
            return
        c.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, type TEXT NOT NULL, viewer_id TEXT, viewer TEXT, text TEXT, source TEXT, mode TEXT, at TEXT NOT NULL, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS lore (id TEXT PRIMARY KEY, kind TEXT NOT NULL, summary TEXT NOT NULL, status TEXT NOT NULL, subjects TEXT NOT NULL, evidence TEXT NOT NULL, importance REAL NOT NULL, created_at TEXT NOT NULL, mentions INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS utterances (id TEXT PRIMARY KEY, text TEXT NOT NULL, trigger TEXT NOT NULL, viewer TEXT, at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS moments (id TEXT PRIMARY KEY, title TEXT NOT NULL, detail TEXT NOT NULL, score REAL NOT NULL, at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS forgotten (viewer_id TEXT PRIMARY KEY, at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS viewers (viewer_id TEXT PRIMARY KEY, display_name TEXT NOT NULL, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, memory_enabled INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS privacy_audit (id TEXT PRIMARY KEY, viewer_id TEXT NOT NULL, action TEXT NOT NULL, completed_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS whisper_recipients (utc_day TEXT NOT NULL, viewer_id TEXT NOT NULL, sent_at TEXT NOT NULL, PRIMARY KEY(utc_day,viewer_id));
        CREATE TABLE IF NOT EXISTS overlay_commands (seq INTEGER PRIMARY KEY AUTOINCREMENT, command_id TEXT NOT NULL UNIQUE, payload TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS lore_enrichments (lore_id TEXT PRIMARY KEY, summary TEXT NOT NULL, relic_title TEXT NOT NULL, power_name TEXT NOT NULL, power_text TEXT NOT NULL, boss_name TEXT NOT NULL, boss_epithet TEXT NOT NULL, provider TEXT NOT NULL, updated_at TEXT NOT NULL);
        """)
        c.execute("INSERT OR IGNORE INTO settings VALUES('muted','false')")
        from .story import init_schema as init_story_schema
        init_story_schema(c)
        from .relics import init_schema as init_relic_schema
        init_relic_schema(c)
        from .game import init_schema as init_game_schema
        init_game_schema(c)
        from .signal_game import init_schema as init_signal_schema
        init_signal_schema(c)

def prune_raw_events():
    days=max(1,int(os.environ.get("LOREKEEPER_RAW_RETENTION_DAYS","7")))
    cutoff=(datetime.now(timezone.utc)-timedelta(days=days)).isoformat(timespec="seconds").replace("+00:00","Z")
    with db() as c: c.execute("DELETE FROM events WHERE at<?",(cutoff,))

def raw_retention_worker():
    while True:
        time.sleep(3600)
        try: prune_raw_events()
        except Exception as exc: print(f"Raw event retention cleanup failed: {exc}",flush=True)

def publish(data):
    with lock:
        for q in listeners:
            try: q.put_nowait(data)
            except queue.Full:
                try: q.get_nowait(); q.put_nowait(data)
                except (queue.Empty,queue.Full): pass

def store_overlay_command(c,command,command_id):
    if DATABASE_URL:
        cursor=c.execute("INSERT INTO overlay_commands(command_id,payload,created_at) VALUES(?,?,?) RETURNING seq",(command_id,json.dumps(command,ensure_ascii=False),command["at"]))
        command["seq"]=cursor.fetchone()["seq"]
    else:
        cursor=c.execute("INSERT INTO overlay_commands(command_id,payload,created_at) VALUES(?,?,?)",(command_id,json.dumps(command,ensure_ascii=False),command["at"]))
        command["seq"]=cursor.lastrowid
    c.execute("UPDATE overlay_commands SET payload=? WHERE seq=?",(json.dumps(command,ensure_ascii=False),command["seq"]))


event_bus = None
twitch_tokens = None
