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
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

from sqlalchemy import (
    create_engine, MetaData, Table, Column, Integer, String, JSON, DateTime,
    UniqueConstraint, select, insert, update, delete,
)
from sqlalchemy.engine import Engine

_engine: Optional[Engine] = None
_init_error: Optional[str] = None
metadata = MetaData()

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


def init_db() -> bool:
    """Inizializza l'engine e crea le tabelle. Non solleva: in errore → disabilitata."""
    global _engine, _init_error
    raw = (os.environ.get("DATABASE_URL") or "").strip()
    url = _normalize_url(raw) if raw else "sqlite:///./data/app.db"
    # connect_timeout: se il DB è irraggiungibile all'avvio, fallisce in fretta
    # (5s) invece di bloccare lo startup dell'app.
    connect_args = {} if url.startswith("sqlite") else {"connect_timeout": 5}
    try:
        if url.startswith("sqlite"):
            os.makedirs("./data", exist_ok=True)
        eng = create_engine(url, pool_pre_ping=True, future=True, connect_args=connect_args)
        metadata.create_all(eng)
        _engine = eng
        _init_error = None
        return True
    except Exception as exc:  # DB assente/irraggiungibile → degradazione elegante
        _engine = None
        _init_error = str(exc)
        return False


def enabled() -> bool:
    return _engine is not None


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
