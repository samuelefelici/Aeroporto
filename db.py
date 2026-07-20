"""
db.py — Persistenza server-side (progetti mensili + lavori per categoria di giorno).

Usa il DATABASE_URL dell'ambiente (Postgres su Coolify). In assenza di
DATABASE_URL (o se il DB non è raggiungibile) l'app resta funzionante: gli
endpoint di persistenza rispondono 503 e il frontend ricade su localStorage.

Modello:
  projects(year, month) UNIQUE  → meta (jsonb) + flights (jsonb)
  works(year, month, weekday) UNIQUE → state (jsonb) + shift_count
"""

import os
import re
import threading
import time
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

from sqlalchemy import (
    create_engine, text, MetaData, Table, Column, Integer, String, JSON, DateTime,
    UniqueConstraint, select, insert, update, delete,
)
from sqlalchemy.engine import Engine

_engine: Optional[Engine] = None
_init_error: Optional[str] = None
_last_attempt: float = 0.0
_init_lock = threading.Lock()
# frammenti della password del DATABASE_URL da mascherare in QUALSIASI messaggio
# (anche quando l'errore non contiene un URL ben formato, es. password con @ non
# codificata che finisce dentro l'hostname dell'errore DNS)
_secret_parts: tuple = ()
# se il DB non era raggiungibile, ritenta la connessione al massimo ogni 30s
# (chiamato da ensure_db su /api/persistence e sugli endpoint di persistenza):
# così quando il DB torna su l'app si riaggancia DA SOLA, senza redeploy.
RETRY_COOLDOWN_S = 30.0
metadata = MetaData()


def _extract_secret_parts(raw: str) -> tuple:
    """Estrae password (e suoi frammenti) dal DATABASE_URL grezzo.

    Serve per il caso in cui la password contenga caratteri speciali NON
    codificati (@ : /): SQLAlchemy la spezza e i frammenti finiscono nei
    messaggi di errore (es. dentro l'hostname). Mascheriamo ogni frammento
    letterale di lunghezza ≥ 3.
    """
    if not raw:
        return ()
    at = raw.rfind("@")
    if at <= 0:
        return ()
    userinfo = re.sub(r"^[A-Za-z0-9+.\-]+:/*", "", raw[:at])   # via lo scheme
    if ":" not in userinfo:
        return ()
    pwd = userinfo.split(":", 1)[1]
    parts = {pwd}
    for sep in ("@", ":", "/"):
        parts |= {piece for frag in list(parts) for piece in frag.split(sep)}
    return tuple(sorted((p for p in parts if len(p) >= 3), key=len, reverse=True))


def _sanitize(text_: str) -> str:
    """Maschera la password del DATABASE_URL dentro un messaggio di errore.

    Doppia difesa: (1) pattern URL user:pass@ (greedy fino all'ULTIMO @ del
    token, semantica userinfo); (2) sostituzione LETTERALE della password e dei
    suoi frammenti (per errori che non contengono '://', es. echo dell'URL
    malformato o frammenti finiti nell'hostname).
    """
    out = re.sub(r"://([^:/@\s]+):(\S+)@", r"://\1:***@", text_ or "")
    for part in _secret_parts:
        out = out.replace(part, "***")
    return out


# alias pubblico (usato da app.py per i messaggi degli errori runtime)
sanitize = _sanitize


def _hint_for(err: Optional[str]) -> Optional[str]:
    """Suggerimento operativo (in italiano) per gli errori di connessione tipici."""
    if not err:
        return None
    low = err.lower()
    if ("could not parse" in low or "malformed" in low or "invalid literal" in low
            or "***@" in low):   # frammento di password mascherato finito nell'host
        return ("URL non interpretabile: se la password contiene caratteri speciali "
                "(@ / : #), va codificata URL (es. @ → %40).")
    if "does not support ssl" in low or "ssl was required" in low:
        return ("Il server NON supporta SSL ma l'URL lo richiede: RIMUOVI "
                "?sslmode=require dal DATABASE_URL.")
    if "no encryption" in low:
        return ("Il server richiede una connessione cifrata: aggiungi "
                "?sslmode=require in fondo al DATABASE_URL.")
    if "no pg_hba.conf entry" in low:
        return ("Il server rifiuta questo host/utente (pg_hba.conf): abilita "
                "l'accesso dal host dell'app o controlla utente/database nell'URL.")
    if "ssl" in low:
        return ("Problema SSL: prova ad aggiungere ?sslmode=require in fondo al "
                "DATABASE_URL (o a rimuoverlo, se già presente).")
    if "password authentication failed" in low:
        return "Credenziali errate: controlla utente/password nel DATABASE_URL."
    if ("could not translate host name" in low or "name or service not known" in low
            or "failure in name resolution" in low):
        return ("Hostname non risolvibile: usa l'hostname INTERNO del DB "
                "(app e DB sulla stessa rete Docker/Coolify) o l'host pubblico corretto.")
    if "timed out" in low or "timeout" in low:
        return ("Host non raggiungibile (timeout): su Coolify app e DB devono stare "
                "sulla stessa rete Docker (abilita \"Connect To Predefined Network\" "
                "sull'app o usa l'URL interno del DB), oppure esponi il DB e usa "
                "host/porta pubblici.")
    if "refused" in low:
        return ("Connessione rifiutata: porta sbagliata o DB non in ascolto su "
                "quell'host (controlla host:porta nel DATABASE_URL).")
    if "database" in low and "does not exist" in low:
        return "Il database indicato non esiste: crealo oppure correggi il nome nel DATABASE_URL."
    if "permission denied" in low:
        return ("L'utente non ha i permessi per creare le tabelle: usa l'utente "
                "proprietario del database o concedi i permessi sullo schema public.")
    return None

projects = Table(
    "projects", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("year", Integer, nullable=False),
    Column("month", Integer, nullable=False),
    Column("meta", JSON),
    Column("flights", JSON),
    Column("updated_at", DateTime(timezone=True)),
    UniqueConstraint("year", "month", name="uq_project_year_month"),
)

works = Table(
    "works", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("year", Integer, nullable=False),
    Column("month", Integer, nullable=False),
    Column("weekday", String(8), nullable=False),
    Column("state", JSON),
    Column("shift_count", Integer, default=0),
    Column("updated_at", DateTime(timezone=True)),
    UniqueConstraint("year", "month", "weekday", name="uq_work_ymw"),
)


def _normalize_url(url: str) -> str:
    """Normalizza lo schema del DATABASE_URL per il driver psycopg (v3)."""
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def _init_db_locked() -> bool:
    """Corpo di init_db: chiamare SOLO con _init_lock acquisito."""
    global _engine, _init_error, _last_attempt, _secret_parts
    _last_attempt = time.monotonic()
    # strip anche di eventuali apici/virgolette incollati con il valore
    raw = (os.environ.get("DATABASE_URL") or "").strip().strip('"').strip("'")
    _secret_parts = _extract_secret_parts(raw)
    url = _normalize_url(raw) if raw else "sqlite:///./data/app.db"
    # connect_timeout: se il DB è irraggiungibile all'avvio, fallisce in fretta
    # (5s) invece di bloccare lo startup dell'app.
    connect_args = {} if url.startswith("sqlite") else {"connect_timeout": 5}
    try:
        if url.startswith("sqlite"):
            os.makedirs("./data", exist_ok=True)
        eng = create_engine(url, pool_pre_ping=True, future=True, connect_args=connect_args)
        metadata.create_all(eng)
        old, _engine = _engine, eng
        if old is not None and old is not eng:
            try:
                old.dispose()
            except Exception:
                pass
        _init_error = None
        print(f"[db] Persistenza ATTIVA ({'sqlite locale' if url.startswith('sqlite') else 'postgres'})", flush=True)
        return True
    except Exception as exc:  # DB assente/irraggiungibile → degradazione elegante
        _engine = None
        _init_error = _sanitize(str(exc))
        print(f"[db] Persistenza DISABILITATA: {_init_error}", flush=True)
        hint = _hint_for(_init_error)
        if hint:
            print(f"[db] Suggerimento: {hint}", flush=True)
        return False


def init_db() -> bool:
    """Inizializza l'engine e crea le tabelle. Non solleva: in errore → disabilitata."""
    with _init_lock:
        return _init_db_locked()


def ensure_db(force: bool = False) -> bool:
    """Se il DB non era disponibile, ritenta la connessione.

    - rispetta un cooldown di 30s (force=True lo bypassa: retry esplicito
      dell'operatore dal pulsante «Riprova connessione»);
    - un solo thread ritenta alla volta: gli altri NON si accodano (rispondono
      subito con lo stato corrente) — niente valanga di connect da 5s.
    """
    if _engine is not None:
        return True
    if not force and time.monotonic() - _last_attempt < RETRY_COOLDOWN_S:
        return False
    if not _init_lock.acquire(blocking=False):
        return _engine is not None
    try:
        if _engine is not None:
            return True
        if not force and time.monotonic() - _last_attempt < RETRY_COOLDOWN_S:
            return False
        return _init_db_locked()
    finally:
        _init_lock.release()


def ping() -> bool:
    """Verifica che la connessione sia VIVA (rileva un DB caduto dopo l'avvio).

    In caso di errore disattiva la persistenza (con messaggio sanitizzato):
    ensure_db la riattiverà quando il DB torna raggiungibile.
    """
    global _engine, _init_error, _last_attempt
    eng = _engine
    if eng is None:
        return False
    try:
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:
        with _init_lock:
            if _engine is eng:   # nessun altro thread l'ha già sostituito
                _init_error = _sanitize(str(exc))
                _last_attempt = time.monotonic()
                _engine = None
                try:
                    eng.dispose()
                except Exception:
                    pass
                print(f"[db] Connessione DB PERSA: {_init_error}", flush=True)
        return False


def enabled() -> bool:
    return _engine is not None


def init_error() -> Optional[str]:
    """Ultimo errore di connessione (password mascherata), o None se attiva."""
    return None if _engine is not None else _init_error


def init_hint() -> Optional[str]:
    """Suggerimento operativo per l'ultimo errore, o None."""
    return None if _engine is not None else _hint_for(_init_error)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ─── Progetti ───
def upsert_project(year: int, month: int, meta: Any, flights: Any) -> None:
    with _engine.begin() as conn:
        res = conn.execute(
            update(projects).where(projects.c.year == year, projects.c.month == month)
            .values(meta=meta, flights=flights, updated_at=_now())
        )
        if res.rowcount == 0:
            conn.execute(insert(projects).values(
                year=year, month=month, meta=meta, flights=flights, updated_at=_now()))


def list_projects() -> List[Dict[str, Any]]:
    with _engine.connect() as conn:
        rows = conn.execute(
            select(projects.c.year, projects.c.month, projects.c.meta, projects.c.updated_at)
            .order_by(projects.c.year.desc(), projects.c.month.desc())
        ).all()
    return [
        {"year": r.year, "month": r.month, "meta": r.meta,
         "updated_at": r.updated_at.isoformat() if r.updated_at else None}
        for r in rows
    ]


def get_project(year: int, month: int) -> Optional[Dict[str, Any]]:
    with _engine.connect() as conn:
        r = conn.execute(
            select(projects.c.year, projects.c.month, projects.c.meta, projects.c.flights)
            .where(projects.c.year == year, projects.c.month == month)
        ).first()
    if not r:
        return None
    return {"year": r.year, "month": r.month, "meta": r.meta, "flights": r.flights}


def delete_project(year: int, month: int) -> None:
    with _engine.begin() as conn:
        conn.execute(delete(works).where(works.c.year == year, works.c.month == month))
        conn.execute(delete(projects).where(projects.c.year == year, projects.c.month == month))


# ─── Lavori (per categoria di giorno) ───
def upsert_work(year: int, month: int, weekday: str, state: Any, shift_count: int) -> None:
    with _engine.begin() as conn:
        res = conn.execute(
            update(works).where(works.c.year == year, works.c.month == month, works.c.weekday == weekday)
            .values(state=state, shift_count=shift_count, updated_at=_now())
        )
        if res.rowcount == 0:
            conn.execute(insert(works).values(
                year=year, month=month, weekday=weekday, state=state,
                shift_count=shift_count, updated_at=_now()))


def list_works(year: int, month: int) -> List[Dict[str, Any]]:
    with _engine.connect() as conn:
        rows = conn.execute(
            select(works.c.weekday, works.c.shift_count, works.c.updated_at)
            .where(works.c.year == year, works.c.month == month)
        ).all()
    return [
        {"weekday": r.weekday, "shiftCount": r.shift_count or 0,
         "updated_at": r.updated_at.isoformat() if r.updated_at else None}
        for r in rows
    ]


def get_work(year: int, month: int, weekday: str) -> Optional[Dict[str, Any]]:
    with _engine.connect() as conn:
        r = conn.execute(
            select(works.c.state).where(
                works.c.year == year, works.c.month == month, works.c.weekday == weekday)
        ).first()
    return {"state": r.state} if r else None


def delete_work(year: int, month: int, weekday: str) -> None:
    with _engine.begin() as conn:
        conn.execute(delete(works).where(
            works.c.year == year, works.c.month == month, works.c.weekday == weekday))
