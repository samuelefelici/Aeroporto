"""
flight_parser — parsing del PDF con orario voli (file MENSILE).

Estratto dall'app Streamlit originale e reso indipendente da pandas: restituisce
una lista di dict JSON-serializzabili.

Ogni file è mensile: l'operatore sceglie MESE e ANNO all'import. Le intestazioni
di giorno nel PDF possono essere:
  - complete   "Wed 15 Apr 2026"  → si usa la data esatta del PDF;
  - senza anno "Wed 15 Apr"       → si usa l'anno scelto dall'operatore;
  - brevi      "Wed 15"           → si usa mese+anno scelti, con rilevamento del
                                     cambio mese (bande settimanali a cavallo).

Logica di parsing (invariata nella sostanza):
- rileva dinamicamente i centri delle 7 colonne "giorno" dalle tabelle con
  intestazione di data (robusto a passo/larghezza diversi tra i file);
- assegna ogni tabella alla colonna con bordo sinistro più vicino, scartando i
  "blob" spurî ai bordi pagina; gestisce le continuazioni a cavallo pagina;
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

# Intestazione giorno: weekday + numero, con mese e anno OPZIONALI.
#   "Wed 15 Apr 2026" | "Wed 15 Apr" | "Wed 15"
DAY_HEADER = re.compile(
    r"^(Sun|Mon|Tue|Wed|Thu|Fri|Sat)\s+(\d{1,2})(?:\s+([A-Za-z]{3})(?:\s+(\d{4}))?)?$"
)

MONTH_MAP = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}

# Colonna "Flight" dell'intestazione tabella (es. "Flight RouteA/D Type ETA ETD")
HEADER_TOKENS = {"flight", "route", "a/d", "ad", "type", "eta", "etd"}


def _match_day(cell: str):
    """
    Se `cell` è un'intestazione di giorno restituisce
    (weekday, day, month_num|None, year|None), altrimenti None.
    """
    m = DAY_HEADER.match((cell or "").strip())
    if not m:
        return None
    wd = m.group(1)
    day = int(m.group(2))
    month = MONTH_MAP.get(m.group(3)) if m.group(3) else None
    year = int(m.group(4)) if m.group(4) else None
    return (wd, day, month, year)


def _add_months(year: int, month: int, offset: int):
    """Somma `offset` mesi a (year, month) → (year, month)."""
    idx = (month - 1) + offset
    return year + idx // 12, idx % 12 + 1


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
    """Estrae UNA SOLA VOLTA le tabelle di ogni pagina (operazione costosa)."""
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
    """Rileva i centri (x) delle 7 colonne "giorno" dalle tabelle con intestazione di data."""
    candidates: List[tuple] = []
    for page_tables in pages_tables:
        for tbl in page_tables:
            rows = tbl["rows"]
            first_cell = (rows[0][0] or "").strip() if rows and rows[0] else ""
            if _match_day(first_cell):
                candidates.append((tbl["x0"], tbl["x1"] - tbl["x0"]))

    if not candidates:
        return []

    med_w = _median([w for _, w in candidates])
    lefts = sorted(x0 for x0, w in candidates if med_w == 0 or w <= med_w * 1.8)
    if not lefts:
        return []

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
    if _match_day(flight_cell):
        return False
    return True


def parse_pdf_to_flights(
    file_obj: io.BytesIO,
    ref_month: Optional[int] = None,
    ref_year: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """
    Parser per il PDF con orario voli mensile.

    ref_month / ref_year: mese e anno scelti dall'operatore, usati per le
    intestazioni di giorno prive di mese/anno (con rilevamento del cambio mese
    nelle bande settimanali a cavallo). Se l'intestazione è completa si usa la
    data esatta del PDF.

    Restituisce una lista di dict:
        {"date": "2026-04-15", "weekday": "Wed", "flight": "FR1234",
         "route": "STN", "ad": "P", "type": "PAX", "eta": "10:25", "etd": "11:05"}
    Solo voli PAX (CARGO esclusi), deduplicati.
    """
    if ref_month is None or not (1 <= int(ref_month) <= 12):
        ref_month = 1
    if ref_year is None:
        ref_year = 2026
    ref_month = int(ref_month)
    ref_year = int(ref_year)

    records: List[dict] = []

    # stato per il rollover dei mesi negli header brevi (per colonna)
    col_month_off: Dict[int, int] = {}
    col_last_day: Dict[int, int] = {}

    def short_date(col: int, day: int) -> Optional[date]:
        """Data per un header breve, gestendo il cambio mese nella colonna."""
        if col not in col_month_off:
            # una banda iniziale con giorni "alti" appartiene al mese precedente
            col_month_off[col] = -1 if day >= 24 else 0
        elif day < col_last_day[col]:
            col_month_off[col] += 1   # la colonna è passata al mese successivo
        col_last_day[col] = day
        y, m = _add_months(ref_year, ref_month, col_month_off[col])
        try:
            return date(y, m, day)
        except ValueError:
            return None

    with pdfplumber.open(file_obj) as pdf:
        pages_tables = _extract_tables_by_page(pdf)

        centers = _detect_day_columns(pages_tables)
        if not centers:
            return []

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
                if (x1 - x0) > pitch * 1.5:
                    continue

                col = _resolve_column(x0, centers, pitch)
                if col is None:
                    continue

                first_cell = (rows[0][0] or "").strip() if rows and rows[0] else ""
                hdr = _match_day(first_cell)

                if hdr:
                    wd, day, month, year = hdr
                    if month is not None:
                        # header con mese esplicito (anno esplicito o = ref_year)
                        try:
                            cur = date(year if year is not None else ref_year, month, day)
                        except ValueError:
                            cur = None
                    else:
                        cur = short_date(col, day)

                    current_weekday_by_col[col] = wd
                    current_date_by_col[col] = cur
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

    pax = [r for r in deduped if r["type"] == "PAX"]
    for r in pax:
        for k in ("route", "eta", "etd"):
            if r[k] == "":
                r[k] = None

    return pax
