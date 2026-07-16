# ✈️ Flight Matrix

App [Streamlit](https://streamlit.io/) che importa il PDF con l'orario voli di un
aeroporto e produce:

- il **parsing dei voli PAX** (i CARGO vengono esclusi automaticamente), robusto
  anche quando un giorno è spezzato a cavallo tra due pagine del PDF;
- una **matrice voli × date** raggruppata per giorno della settimana, con filtri
  (codice volo, aeroporto, arrivi/partenze) ed export in CSV;
- un **grafico giornaliero** arrivi / partenze.

---

## 🚀 Avvio in locale

```bash
pip install -r requirements.txt
streamlit run streamlit_app.py
```

App su <http://localhost:8501>.

In alternativa, con Docker:

```bash
docker compose up --build
# oppure
docker build -t flight-matrix . && docker run -p 8501:8501 flight-matrix
```

---

## 🐳 Deploy su Coolify

Il repository è pronto per Coolify: contiene un `Dockerfile`, un
`docker-compose.yml` e la configurazione Streamlit per il reverse proxy.

### Opzione A — Dockerfile (consigliata)

1. In Coolify: **+ New Resource → Application → Public/Private Repository** e
   seleziona questo repo/branch.
2. **Build Pack**: `Dockerfile` (Coolify rileva automaticamente il `Dockerfile`
   in root).
3. **Port**: imposta la porta esposta a **`8501`** (è quella su cui ascolta
   Streamlit nel container).
4. Assegna il dominio e attiva HTTPS: al deploy Coolify (Traefik) instrada il
   traffico verso la porta 8501.
5. **Deploy**.

### Opzione B — Docker Compose

1. **+ New Resource → Docker Compose** e punta a `docker-compose.yml`.
2. Coolify usa il servizio `flight-matrix` (porta interna `8501`) e ne gestisce
   dominio/HTTPS.

### Health check

Il container espone l'endpoint nativo di Streamlit
`GET /_stcore/health` (risponde `ok`). È già configurato come `HEALTHCHECK` nel
`Dockerfile` e nel compose, così Coolify sa quando l'app è pronta/viva.

### Note sul reverse proxy

Dietro Traefik (Coolify) l'app gira con `enableCORS = false` e
`enableXsrfProtection = false` (vedi `.streamlit/config.toml` e le variabili
`STREAMLIT_*` nel `Dockerfile`): questo evita errori di upload dei PDF e problemi
con i websocket. L'app non ha login né operazioni sensibili, quindi il
trade-off di sicurezza è trascurabile.

---

## 📄 Formato PDF supportato

Il parser si aspetta il layout "settimanale" dell'orario voli: 7 colonne
(Lun–Dom), più bande settimanali per pagina, con tabelle intestate da una data
tipo `Wed 15 Apr 2026` e colonne `Flight / Route / A/D / Type / ETA / ETD`.

Funziona con mesi e anni diversi: le colonne dei giorni vengono **rilevate
dinamicamente** dalle intestazioni di data, quindi il parser si adatta a passi e
larghezze differenti tra i vari file.

---

## 🧩 Struttura del progetto

| File | Descrizione |
|------|-------------|
| `streamlit_app.py` | App Streamlit + parser PDF |
| `requirements.txt` | Dipendenze (pinnate) |
| `Dockerfile` | Immagine per Coolify / Docker |
| `docker-compose.yml` | Deploy compose / test locale |
| `.streamlit/config.toml` | Config server Streamlit (reverse proxy) |
| `.dockerignore` | Esclusioni dal contesto di build |

---

## 🔧 Aggiornare le dipendenze

Le versioni in `requirements.txt` sono pinnate per build riproducibili. Per
aggiornarle, modifica i pin e ricostruisci l'immagine (`docker compose build`
oppure redeploy su Coolify).
