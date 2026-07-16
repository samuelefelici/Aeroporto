"""
flight_parser — parsing del PDF con orario voli (qualsiasi mese: le colonne dei
giorni vengono rilevate dinamicamente).

Estratto dall'app Streamlit originale e reso indipendente da pandas: restituisce
una lista di dict JSON-serializzabili, così il backend FastAPI può inviarli al
frontend senza dipendenze pesanti.

Logica di parsing (invariata rispetto all'originale):
- rileva dinamicamente i centri delle 7 colonne "giorno" dalle tabelle con
  intestazione di data (robusto a passo/larghezza diversi tra i file);
- per ogni tabella la assegna alla colonna con bordo sinistro più vicino,
  scartando i "blob" spurî generati da pdfplumber ai bordi pagina;
- gestisce le tabelle di *continuazione* a cavallo tra due pagine;
- legge per ogni riga volo: Flight, Route, A/D, Type, ETA, ETD;
- tiene solo i voli PAX (CARGO esclusi).
"""

import io
import re
from datetime import date
from typing import List, Optional, Dict, Any

import pdfplumber


# =========================
# Costanti
# =========================

WEEKDAY_ORDER = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

# pattern generico: Sun 1 Mar 2026, Mon 2 Feb 2026, ecc.
DAY_PATTERN = re.compile(
    r"^(Sun|Mon|Tue|Wed|Thu|Fri|Sat)\s+(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})$"
)

MONTH_MAP = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}

# Colonna "Flight" dell'intestazione tabella (es. "Flight RouteA/D Type ETA ETD")
HEADER_TOKENS = {"flight", "route", "a/d", "ad", "type", "eta", "etd"}


def _median(values: List[float]) -> float:
    """Mediana semplice (evita dipendenze esterne)."""
    vals = sorted(values)
    n = len(vals)
    if n == 0:
        return 0.0
    mid = n // 2
    if n % 2:
        return float(vals[mid])
    return 0.5 * (vals[mid - 1] + vals[mid])


def _extract_tables_by_page(pdf) -> List[List[dict]]:
    """
    Estrae UNA SOLA VOLTA le tabelle di ogni pagina (l'operazione più costosa di
    pdfplumber). Restituisce, per pagina, una lista di dict con bbox e righe.
    """
    pages_tables: List[List[dict]] = []
    for page in pdf.pages:
        page_tables: List[dict] = []
        for t in page.find_tables():
            rows = t.extract()
            if not rows:
                continue
            x0, y0, x1, y1 = t.bbox
            page_tables.append({"x0": x0, "y0": y0, "x1": x1, "y1": y1, "rows": rows})
        pages_tables.append(page_tables)
    return pages_tables


def _detect_day_columns(pages_tables: List[List[dict]], tol: float = 45.0) -> List[float]:
    """
    Rileva i centri (x) delle 7 colonne "giorno" a partire dalle tabelle che
    iniziano con un'intestazione di data (es. "Wed 15 Apr 2026").
    """
    candidates: List[tuple] = []
    for page_tables in pages_tables:
        for tbl in page_tables:
            rows = tbl["rows"]
            first_cell = (rows[0][0] or "").strip() if rows and rows[0] else ""
            if DAY_PATTERN.match(first_cell):
                candidates.append((tbl["x0"], tbl["x1"] - tbl["x0"]))

    if not candidates:
        return []

    # scarta header "blob" anomali (larghi più colonne): teniamo le larghezze ~mediane
    med_w = _median([w for _, w in candidates])
    lefts = sorted(x0 for x0, w in candidates if med_w == 0 or w <= med_w * 1.8)
    if not lefts:
        return []

    # clustering per prossimità sui bordi sinistri (ordinati)
    clusters: List[List[float]] = [[lefts[0]]]
    for x in lefts[1:]:
        if x - clusters[-1][-1] <= tol:
            clusters[-1].append(x)
        else:
            clusters.append([x])

    return [_median(c) for c in clusters]


def _resolve_column(x0: float, centers: List[float], pitch: float) -> Optional[int]:
    """Indice della colonna più vicina al bordo sinistro `x0`, o None se troppo lontana."""
    if not centers:
        return None
    best = min(range(len(centers)), key=lambda i: abs(centers[i] - x0))
    if abs(centers[best] - x0) > pitch * 0.6:
        return None
    return best


def _is_flight_row(flight_cell: str) -> bool:
    """True se la cella sembra un codice volo reale (non header, non data, non vuota)."""
    if not flight_cell:
        return False
    low = flight_cell.lower()
    if low in HEADER_TOKENS:
        return False
    if DAY_PATTERN.match(flight_cell):
        return False
    return True


def parse_pdf_to_flights(file_obj: io.BytesIO) -> List[Dict[str, Any]]:
    """
    Parser per il PDF con orario voli. Restituisce una lista di dict:

        {
            "date": "2026-04-15",   # ISO
            "weekday": "Wed",
            "flight": "FR1234",
            "route": "STN",
            "ad": "P",               # A = arrivo, P = partenza
            "type": "PAX",
            "eta": "10:25",
            "etd": "11:05",
        }

    Solo voli PAX (CARGO esclusi), deduplicati.
    """
    records: List[dict] = []

    with pdfplumber.open(file_obj) as pdf:
        pages_tables = _extract_tables_by_page(pdf)

        centers = _detect_day_columns(pages_tables)
        if not centers:
            return []

        # passo tipico tra colonne adiacenti (per tolleranze e filtro blob)
        if len(centers) >= 2:
            gaps = [centers[i + 1] - centers[i] for i in range(len(centers) - 1)]
            pitch = _median(gaps)
        else:
            pitch = pdf.pages[0].width / 7.0

        n_cols = len(centers)

        current_date_by_col = {i: None for i in range(n_cols)}
        current_weekday_by_col = {i: None for i in range(n_cols)}

        for page_tables in pages_tables:
            tables = sorted(page_tables, key=lambda tb: (tb["y0"], tb["x0"]))

            for tbl in tables:
                rows = tbl["rows"]
                if not rows:
                    continue

                x0, x1 = tbl["x0"], tbl["x1"]

                # scarta tabelle spurie che coprono più colonne (blob ai bordi)
                if (x1 - x0) > pitch * 1.5:
                    continue

                col = _resolve_column(x0, centers, pitch)
                if col is None:
                    continue

                first_cell = (rows[0][0] or "").strip() if rows and rows[0] else ""
                m = DAY_PATTERN.match(first_cell)

                if m:
                    month_num = MONTH_MAP.get(m.group(3))
                    if month_num is None:
                        current_date_by_col[col] = None
                        current_weekday_by_col[col] = None
                        continue

                    current_weekday_by_col[col] = m.group(1)
                    current_date_by_col[col] = date(
                        int(m.group(4)), month_num, int(m.group(2))
                    )
                    data_rows = rows[1:]
                else:
                    data_rows = rows

                cur_date = current_date_by_col[col]
                cur_weekday = current_weekday_by_col[col]
                if cur_date is None or cur_weekday is None:
                    continue

                for row in data_rows:
                    if not row:
                        continue

                    flight = (row[0] or "").strip()
                    if not _is_flight_row(flight):
                        continue

                    route = (row[1] or "").strip() if len(row) > 1 else ""
                    ad = (row[2] or "").strip() if len(row) > 2 else ""
                    typ = (row[3] or "").strip() if len(row) > 3 else ""
                    eta = (row[4] or "").strip() if len(row) > 4 else ""
                    etd = (row[5] or "").strip() if len(row) > 5 else ""

                    records.append({
                        "date": cur_date.isoformat(),
                        "weekday": cur_weekday,
                        "flight": flight,
                        "route": route,
                        "ad": ad.upper().strip(),
                        "type": typ.upper().strip(),
                        "eta": eta,
                        "etd": etd,
                    })

    if not records:
        return []

    # dedup (doppio conteggio a cavallo pagina) preservando l'ordine
    seen = set()
    deduped: List[dict] = []
    for r in records:
        key = (r["date"], r["weekday"], r["flight"], r["route"], r["ad"], r["type"], r["eta"], r["etd"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(r)

    # solo PAX
    pax = [r for r in deduped if r["type"] == "PAX"]

    # normalizza celle vuote a None
    for r in pax:
        for k in ("route", "eta", "etd"):
            if r[k] == "":
                r[k] = None

    return pax
