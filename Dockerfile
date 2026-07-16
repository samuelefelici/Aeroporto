# =========================================================================
# Flight Matrix — immagine Docker per il deploy su Coolify (o qualsiasi host)
# =========================================================================
# Build:  docker build -t flight-matrix .
# Run:    docker run -p 8501:8501 flight-matrix
# Coolify: rileva automaticamente questo Dockerfile (build pack "Dockerfile").
# =========================================================================

FROM python:3.12-slim

# Impostazioni Python pulite per container
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    # Streamlit: headless + ascolta su tutte le interfacce dentro il container
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_SERVER_ADDRESS=0.0.0.0 \
    STREAMLIT_SERVER_PORT=8501 \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

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

# Health check nativo Streamlit (usato anche da Coolify/Traefik).
# Usa urllib (già in stdlib) per non aggiungere curl all'immagine slim.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://localhost:8501/_stcore/health', timeout=4).read().strip()==b'ok' else 1)"

# Avvio dell'app. Le opzioni server sono già fornite via env STREAMLIT_*.
ENTRYPOINT ["streamlit", "run", "streamlit_app.py"]
