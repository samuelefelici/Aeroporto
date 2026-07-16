"""
app.py — Backend FastAPI per Flight Matrix / Shuttle Aeroporto.

Sostituisce la vecchia app Streamlit con una web app "normale":
- il backend fa SOLO il parsing del PDF (riusa flight_parser, robusto);
- tutta la UI (matrice, filtri, generazione corse, Finestra di Lavoro) è nel
  frontend statico (static/index.html + app.js + styles.css).

Avvio locale:
    uvicorn app:app --host 0.0.0.0 --port 8501

Endpoint:
    GET  /               → SPA
    GET  /health         → healthcheck ("ok")
    POST /api/parse      → multipart PDF → { flights: [...], meta: {...} }
"""

from pathlib import Path

from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from flight_parser import parse_pdf_to_flights

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

# Limite upload lato applicazione (i PDF orari sono piccoli): 25 MB.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024

app = FastAPI(title="Flight Matrix — Shuttle Aeroporto", version="2.0.0")


@app.get("/health", response_class=PlainTextResponse)
def health() -> str:
    """Healthcheck usato da Docker/Coolify."""
    return "ok"


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


# Static SPA (montato per ultimo così /api/* e /health hanno priorità).
if STATIC_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
