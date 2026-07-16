# =========================================================================
# Flight Matrix — Shuttle Aeroporto → immagine Docker (Coolify o qualsiasi host)
# =========================================================================
# Build:   docker build -t flight-matrix .
# Run:     docker run -p 8501:8501 flight-matrix
# Coolify: rileva automaticamente questo Dockerfile (build pack "Dockerfile").
# =========================================================================

FROM python:3.12-slim

# Impostazioni Python pulite per container
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8501

WORKDIR /app

# 1) Prima le dipendenze (layer cache): cambia solo se cambia requirements.txt
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 2) Poi il codice dell'app
COPY . .

# Utente non-root per sicurezza
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8501

# Health check: endpoint applicativo GET /health (risponde "ok").
# Usa urllib (stdlib) per non aggiungere curl all'immagine slim.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request,sys,os; \
sys.exit(0 if urllib.request.urlopen('http://localhost:%s/health' % os.environ.get('PORT','8501'), timeout=4).read().strip()==b'ok' else 1)"

# Avvio: uvicorn serve sia l'API (/api/parse) sia la SPA statica.
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-8501}"]
