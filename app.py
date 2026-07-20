"""
app.py — Backend FastAPI per Flight Matrix / Shuttle Aeroporto.

Sostituisce la vecchia app Streamlit con una web app "normale":
- il backend fa SOLO il parsing del PDF (riusa flight_parser, robusto);
- tutta la UI (matrice, filtri, generazione corse, Finestra di Lavoro) è nel
  frontend statico (static/index.html + app.js + styles.css).

Avvio locale:
    uvicorn app:app --host 0.0.0.0 --port 8501

Endpoint:
    GET  /                                   → SPA
    GET  /health                             → healthcheck ("ok")
    POST /api/parse                          → multipart PDF → { flights, meta }
    GET  /api/persistence                    → { enabled: bool }
    GET/PUT/DELETE /api/projects[/{y}/{m}]   → progetti mensili (persistenza)
    GET/PUT/DELETE /api/works/{y}/{m}[/{wd}] → lavori per categoria di giorno
"""

from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import Body, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import DBAPIError

import db
from flight_parser import parse_pdf_to_flights

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

# Limite upload lato applicazione (i PDF orari sono piccoli): 25 MB.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024

app = FastAPI(title="Flight Matrix — Shuttle Aeroporto", version="3.0.0")

# Inizializza il DB all'avvio (non solleva: se assente, persistenza disabilitata).
db.init_db()


def _require_db() -> None:
    # ensure_db: se il DB era giù all'avvio ma ora è raggiungibile, si riaggancia
    # da solo (senza redeploy) al primo endpoint di persistenza chiamato.
    db.ensure_db()
    if not db.enabled():
        err = db.init_error()
        raise HTTPException(status_code=503,
                            detail=f"Persistenza server non disponibile.{' ' + err if err else ''}")


@app.exception_handler(DBAPIError)
async def _db_runtime_error(request: Request, exc: DBAPIError) -> JSONResponse:
    """DB caduto DOPO l'avvio: gli endpoint di persistenza rispondono 503
    (non 500) con messaggio sanitizzato, e lo stato viene aggiornato (ping)
    così /api/persistence e il banner riflettono subito la realtà."""
    db.ping()
    return JSONResponse(status_code=503,
                        content={"detail": "Database non raggiungibile: "
                                 + db.sanitize(str(exc.orig or exc))[:300]})


@app.get("/health", response_class=PlainTextResponse)
def health() -> str:
    """Healthcheck usato da Docker/Coolify."""
    return "ok"


@app.get("/api/persistence")
def persistence_status(force: bool = False) -> Dict[str, Any]:
    """Stato della persistenza server-side: se disabilitata, spiega PERCHÉ.

    `error` è il messaggio di connessione (password mascherata) e `hint` un
    suggerimento operativo (SSL, rete Docker, credenziali, permessi...).
    - ensure_db: se il DB è tornato raggiungibile, si riattiva da sola
      (force=1 bypassa il cooldown → pulsante «Riprova connessione»);
    - ping: rileva anche un DB caduto DOPO l'avvio (enabled non mente).
    """
    db.ensure_db(force=force)
    if db.enabled():
        db.ping()
    return {"enabled": db.enabled(), "error": db.init_error(), "hint": db.init_hint()}


@app.post("/api/parse")
async def parse(
    file: UploadFile = File(...),
    month: Optional[int] = Form(None),
    year: Optional[int] = Form(None),
) -> JSONResponse:
    """Riceve il PDF orario voli (mensile) + mese/anno e restituisce i voli PAX."""
    if file.content_type not in ("application/pdf", "application/octet-stream", None) \
            and not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(status_code=415, detail="Carica un file PDF.")

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="File vuoto.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File troppo grande (max 25 MB).")

    import io
    try:
        flights = parse_pdf_to_flights(io.BytesIO(data), ref_month=month, ref_year=year)
    except Exception as exc:  # parsing robusto: errori → 422 con messaggio
        raise HTTPException(status_code=422, detail=f"PDF non riconosciuto: {exc}") from exc

    dates = sorted({f["date"] for f in flights})
    meta = {
        "count": len(flights),
        "days": len(dates),
        "start": dates[0] if dates else None,
        "end": dates[-1] if dates else None,
    }
    return JSONResponse({"flights": flights, "meta": meta})


# ══════════════ Persistenza: progetti mensili ══════════════

@app.get("/api/projects")
def api_list_projects() -> Dict[str, Any]:
    _require_db()
    return {"projects": db.list_projects()}


@app.get("/api/projects/{year}/{month}")
def api_get_project(year: int, month: int) -> Dict[str, Any]:
    _require_db()
    proj = db.get_project(year, month)
    if not proj:
        raise HTTPException(status_code=404, detail="Progetto non trovato.")
    return proj


@app.put("/api/projects/{year}/{month}")
def api_put_project(year: int, month: int, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    _require_db()
    if not (1 <= month <= 12) or not (2000 <= year <= 2100):
        raise HTTPException(status_code=400, detail="Mese/anno non validi.")
    db.upsert_project(year, month, payload.get("meta"), payload.get("flights"))
    return {"ok": True}


@app.delete("/api/projects/{year}/{month}")
def api_delete_project(year: int, month: int) -> Dict[str, Any]:
    _require_db()
    db.delete_project(year, month)
    return {"ok": True}


# ══════════════ Persistenza: lavori per categoria di giorno ══════════════

@app.get("/api/works/{year}/{month}")
def api_list_works(year: int, month: int) -> Dict[str, Any]:
    _require_db()
    return {"works": db.list_works(year, month)}


@app.get("/api/works/{year}/{month}/{weekday}")
def api_get_work(year: int, month: int, weekday: str) -> Dict[str, Any]:
    _require_db()
    w = db.get_work(year, month, weekday)
    if not w:
        raise HTTPException(status_code=404, detail="Lavoro non trovato.")
    return w


@app.put("/api/works/{year}/{month}/{weekday}")
def api_put_work(year: int, month: int, weekday: str, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    _require_db()
    state = payload.get("state")
    shifts = state.get("shifts") if isinstance(state, dict) else None
    shift_count = len(shifts) if isinstance(shifts, list) else 0
    db.upsert_work(year, month, weekday, state, shift_count)
    return {"ok": True}


@app.delete("/api/works/{year}/{month}/{weekday}")
def api_delete_work(year: int, month: int, weekday: str) -> Dict[str, Any]:
    _require_db()
    db.delete_work(year, month, weekday)
    return {"ok": True}


# Static SPA (montato per ultimo così /api/* e /health hanno priorità).
if STATIC_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
