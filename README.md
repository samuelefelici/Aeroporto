# ✈️ Flight Matrix — Shuttle Aeroporto → Turni Guida

Web app che importa il PDF con l'orario voli di un aeroporto e permette di:

1. **Parsare i voli PAX** (i CARGO sono esclusi), robusto anche quando un giorno è
   spezzato a cavallo tra due pagine del PDF;
2. costruire la **matrice voli × date** raggruppata per giorno della settimana, con
   filtri (codice volo, aeroporto, arrivi/partenze) e un **filtro per fasce orarie**;
3. **generare le corse** del bus a partire dai voli selezionati;
4. aprire una **Finestra di Lavoro** in cui muovere, ordinare, selezionare e
   **rimpacchettare** le corse in **turni guida**, con verifica della **normativa
   extraurbano** (Accordo Quadro 18/05/2012).

> Non è più un'app Streamlit: è una web app "normale" — un backend **FastAPI** che
> fa solo il parsing del PDF ed espone `/api/parse`, e una **SPA statica** (HTML/CSS/JS
> vanilla) che contiene tutta la UI. Deploy invariato: singola immagine Docker Python.

---

## 🚌 Generazione delle corse

- **Partenze** (voli in partenza): il bus deve **arrivare in aeroporto 1 ora prima**
  della partenza effettiva del volo; il tragitto **Piazza Cavour → Aeroporto** dura
  **40′**. Quindi il bus parte da Piazza Cavour a `ETD − 1h40′` e arriva a `ETD − 1h00′`.
- **Arrivi** (voli in arrivo): il bus **parte dall'aeroporto 25′ dopo l'atterraggio
  se Schengen**, **35′ se extra-Schengen**, e impiega **35′** per Piazza Cavour.
  La classificazione Schengen è automatica (best-effort per rotta) ma **modificabile**
  dall'operatore prima di generare le corse.

## 🪟 Finestra di Lavoro (turni guida)

Ispirata alla "Finestra di Lavoro" dei turni guida di TransitIntel:

- ogni **corsa** è una riga libera che si **trascina** e si **ordina** sul canvas
  (selezione a rettangolo con il mouse o `Ctrl`+click, `Ctrl+A`, `Esc`);
- **Rimpacchetta** chiude un **turno guida** con le corse selezionate; **Spacchetta**
  lo scioglie di nuovo in corse libere;
- si può trascinare una corsa **dentro/fuori** da un turno;
- ogni turno è verificato **live** secondo la **normativa extraurbano**:
  - **Intero (unico)**: nastro/lavoro ≤ 8h00, nessuna interruzione ≥ 40′;
  - **Semiunico**: interruzione 40′–2h59′, nastro/lavoro ≤ 9h00;
  - **Spezzato**: interruzione ≥ 3h00, nastro/lavoro ≤ 10h30;
  - **Sosta inoperosa**: se l'autista resta fermo in **aeroporto > 30′** il gap è
    *sosta inoperosa* (standby retribuito al 12% con strutture), nastro ≤ 9h15;
  - **RD 131**: guida continuativa ≤ 4h30 (reset con sosta ≥ 15′);
  - **Fuorilinea**: deposito Ancona ↔ **Piazza Cavour** = **10′**, deposito ↔
    **Aeroporto** = **30′** (aggiunti a inizio/fine turno).
- per ogni sosta inoperosa lunga si può scegliere, con un clic sul gap, il
  **rientro in deposito** (che aggiunge il fuorilinea A/R e trasforma la sosta in
  una vera interruzione);
- l'operatore può **forzare la tipologia** del turno dal menu, se la
  classificazione automatica non va bene.

---

## 🚀 Avvio in locale

```bash
pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 8501
```

App su <http://localhost:8501>.

In alternativa, con Docker:

```bash
docker build -t flight-matrix . && docker run -p 8501:8501 flight-matrix
```

> Con `docker compose` in locale: scommenta il blocco `ports` in
> `docker-compose.yml` prima di lanciare `docker compose up --build`.

---

## 🐳 Deploy su Coolify

Il repository è pronto per Coolify: contiene un `Dockerfile`, un `docker-compose.yml`
e serve tutto (API + SPA) sulla porta **8501**.

### Opzione A — Dockerfile (consigliata)

1. In Coolify: **+ New Resource → Application → Public/Private Repository** e seleziona
   questo repo/branch.
2. **Build Pack**: `Dockerfile`.
3. **Port**: **`8501`**.
4. Assegna il dominio e attiva HTTPS.
5. **Deploy**.

### Opzione B — Docker Compose

1. **+ New Resource → Docker Compose** e punta a `docker-compose.yml`.
2. Coolify usa il servizio `flight-matrix` (porta interna `8501`).

### Health check

Il container espone `GET /health` (risponde `ok`), configurato come `HEALTHCHECK`
nel `Dockerfile` e nel compose.

---

## 📄 Formato PDF supportato

Il parser si aspetta il layout "settimanale" dell'orario voli: 7 colonne (Lun–Dom),
con tabelle intestate da una data tipo `Wed 15 Apr 2026` e colonne
`Flight / Route / A/D / Type / ETA / ETD`. Le colonne dei giorni vengono **rilevate
dinamicamente** dalle intestazioni di data, quindi il parser si adatta a mesi/anni e
a passi/larghezze diversi tra i file.

---

## 🧩 Struttura del progetto

| File | Descrizione |
|------|-------------|
| `app.py` | Backend FastAPI (endpoint `/api/parse`, `/health`, serve la SPA) |
| `flight_parser.py` | Parser PDF (pdfplumber, restituisce dict — niente pandas) |
| `static/index.html` | SPA: matrice, filtri, generazione corse, Finestra di Lavoro |
| `static/app.js` | Logica frontend (matrice, corse, Finestra di Lavoro, normativa) |
| `static/styles.css` | Tema scuro |
| `requirements.txt` | Dipendenze (pinnate) |
| `Dockerfile` | Immagine per Coolify / Docker |
| `docker-compose.yml` | Deploy compose / test locale |

---

## 🔧 Aggiornare le dipendenze

Le versioni in `requirements.txt` sono pinnate per build riproducibili. Per aggiornarle,
modifica i pin e ricostruisci l'immagine (`docker compose build` oppure redeploy su Coolify).
