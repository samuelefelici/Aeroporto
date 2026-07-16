# streamlit_app.py

"""
App Streamlit per:
1. Caricare un PDF con orari voli (qualsiasi mese, tipo Feb/Mar 2026).
2. Parsare i voli PAX, anche quando un giorno è spezzato su più tabelle / pagine.
3. Raggruppare per giorno della settimana.
4. Visualizzare una matrice voli × date con interfaccia curata e filtri.
5. Esportare la matrice in CSV.
6. Visualizzare un grafico a linee Arrivi/Partenze per giorno.
"""

import io
import re
from datetime import date
from typing import List, Optional

import pandas as pd
import pdfplumber
import streamlit as st


# =========================
# Costanti
# =========================

WEEKDAY_ORDER = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

WEEKDAY_LABELS_IT = {
    "Mon": "Lunedì",
    "Tue": "Martedì",
    "Wed": "Mercoledì",
    "Thu": "Giovedì",
    "Fri": "Venerdì",
    "Sat": "Sabato",
    "Sun": "Domenica",
}

# pattern generico: Sun 1 Mar 2026, Mon 2 Feb 2026, ecc.
DAY_PATTERN = re.compile(
    r"^(Sun|Mon|Tue|Wed|Thu|Fri|Sat)\s+(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})$"
)

MONTH_MAP = {
    "Jan": 1,
    "Feb": 2,
    "Mar": 3,
    "Apr": 4,
    "May": 5,
    "Jun": 6,
    "Jul": 7,
    "Aug": 8,
    "Sep": 9,
    "Oct": 10,
    "Nov": 11,
    "Dec": 12,
}


# =========================
# PARSING PDF
# =========================

# Colonna "Flight" dell'intestazione tabella (es. "Flight RouteA/D Type ETA ETD")
HEADER_TOKENS = {"flight", "route", "a/d", "ad", "type", "eta", "etd"}

DF_COLUMNS = ["Date", "Weekday", "Flight", "Route", "AD", "Type", "ETA", "ETD"]


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


def _detect_day_columns(pdf, tol: float = 45.0) -> List[float]:
    """
    Rileva i centri (x) delle 7 colonne "giorno" a partire dalle tabelle
    che iniziano con un'intestazione di data (es. "Wed 15 Apr 2026").

    Perché non usare `page_width / 7`: nei PDF reali i 7 giorni occupano solo
    la parte sinistra della pagina (~75%), con passo variabile da file a file
    (~89px in un layout, ~105px in un altro). Dividere la pagina in 7 fette
    uguali sbaglia l'assegnazione delle colonne (Gio/Ven/Sab/Dom finiscono
    nella colonna sbagliata) e questo corrompe l'attribuzione delle tabelle
    di *continuazione* a cavallo tra una pagina e l'altra.

    Restituisce la lista ordinata dei centri-x delle colonne (bordo sinistro
    delle tabelle-giorno, molto stabile tra le pagine).
    """
    lefts: List[float] = []
    for page in pdf.pages:
        for t in page.find_tables():
            rows = t.extract()
            first_cell = (rows[0][0] or "").strip() if rows and rows[0] else ""
            if DAY_PATTERN.match(first_cell):
                lefts.append(t.bbox[0])

    if not lefts:
        return []

    # clustering per prossimità sui bordi sinistri (ordinati)
    lefts.sort()
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


def parse_pdf_to_flights_df(file_obj: io.BytesIO) -> pd.DataFrame:
    """
    Parser per il PDF con orario voli (qualsiasi mese, es. Apr/Giu/Ago 2026).

    Logica:
    - rileva dinamicamente i centri delle 7 colonne "giorno" dalle tabelle con
      intestazione data (robusto a passo/larghezza diversi tra i file);
    - per ogni tabella:
        * la assegna alla colonna con bordo sinistro più vicino;
        * scarta le tabelle "spurie" larghe più colonne (blob generati da
          pdfplumber ai bordi pagina);
        * se la prima cella è tipo "Wed 15 Apr 2026" → nuova data/weekday per
          quella colonna;
        * altrimenti è una *continuazione* del giorno corrente di quella colonna
          (tipico a cavallo tra due pagine);
    - per ogni riga volo valida legge: Flight, Route, A/D, Type, ETA, ETD.

    Restituisce un DataFrame con colonne:
        ['Date', 'Weekday', 'Flight', 'Route', 'AD', 'Type', 'ETA', 'ETD']
        (poi filtrato a PAX).
    """
    records: List[dict] = []

    with pdfplumber.open(file_obj) as pdf:
        # centri delle colonne-giorno rilevati dalle intestazioni di data
        centers = _detect_day_columns(pdf)
        if not centers:
            return pd.DataFrame(columns=DF_COLUMNS)

        # passo tipico tra colonne adiacenti (per tolleranze e filtro blob)
        if len(centers) >= 2:
            gaps = [centers[i + 1] - centers[i] for i in range(len(centers) - 1)]
            pitch = _median(gaps)
        else:
            pitch = pdf.pages[0].width / 7.0

        n_cols = len(centers)

        # stato corrente per ogni colonna: data e weekday
        current_date_by_col = {i: None for i in range(n_cols)}
        current_weekday_by_col = {i: None for i in range(n_cols)}

        # scorri tutte le pagine, dall'alto in basso (le continuazioni in cima
        # alla pagina vengono processate prima delle nuove intestazioni sotto)
        for page in pdf.pages:
            tables = sorted(page.find_tables(), key=lambda t: (t.bbox[1], t.bbox[0]))

            for t in tables:
                rows = t.extract()
                if not rows:
                    continue

                x0, _, x1, _ = t.bbox

                # scarta tabelle spurie che coprono più colonne (blob ai bordi pagina)
                if (x1 - x0) > pitch * 1.5:
                    continue

                col = _resolve_column(x0, centers, pitch)
                if col is None:
                    continue

                first_cell = (rows[0][0] or "").strip() if rows and rows[0] else ""
                m = DAY_PATTERN.match(first_cell)

                if m:
                    # intestazione del giorno (es. "Wed 15 Apr 2026")
                    month_num = MONTH_MAP.get(m.group(3))
                    if month_num is None:
                        current_date_by_col[col] = None
                        current_weekday_by_col[col] = None
                        continue

                    current_weekday_by_col[col] = m.group(1)
                    current_date_by_col[col] = date(
                        int(m.group(4)), month_num, int(m.group(2))
                    )
                    data_rows = rows[1:]  # salta la riga data (l'header colonne è filtrato sotto)
                else:
                    # continuazione: usa la data corrente della colonna
                    data_rows = rows

                cur_date = current_date_by_col[col]
                cur_weekday = current_weekday_by_col[col]
                if cur_date is None or cur_weekday is None:
                    continue

                # estrai righe voli (con guardie robuste riga-per-riga)
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

                    records.append(
                        {
                            "Date": cur_date,
                            "Weekday": cur_weekday,
                            "Flight": flight,
                            "Route": route,
                            "AD": ad,
                            "Type": typ,
                            "ETA": eta,
                            "ETD": etd,
                        }
                    )

    if not records:
        return pd.DataFrame(columns=DF_COLUMNS)

    df = pd.DataFrame(records)

    # normalizzazione
    df["Type"] = df["Type"].str.upper().str.strip()
    df["AD"] = df["AD"].str.upper().str.strip()
    df["ETA"] = df["ETA"].str.strip()
    df["ETD"] = df["ETD"].str.strip()

    # rete di sicurezza: rimuove eventuali righe identiche (doppio conteggio a
    # cavallo pagina). Preserva voli distinti con orari diversi.
    df = df.drop_duplicates(
        subset=["Date", "Weekday", "Flight", "Route", "AD", "Type", "ETA", "ETD"]
    ).reset_index(drop=True)

    # solo PAX (CARGO esclusi automaticamente)
    df = df[df["Type"] == "PAX"].copy()
    df = df.replace({"": None})

    return df


# =========================
# COSTRUZIONE MATRICE
# =========================

def compute_time_value(row: pd.Series) -> Optional[str]:
    """
    Valore da mettere nella matrice:
    - ETA se AD = A (arrivo)
    - ETD se AD in {P, D, DEP, DEPT} (partenza)
    """
    ad = str(row.get("AD", "")).upper()

    if ad in ("A", "ARR", "ARRIVAL"):
        return row.get("ETA") or None

    if ad in ("P", "D", "DEP", "DEPT", "DEPARTURE"):
        return row.get("ETD") or None

    return None


def build_matrix_for_weekday(flights: pd.DataFrame, weekday: str) -> pd.DataFrame:
    """
    Matrice per un dato weekday:

    - Righe = 3 campi:
        Flight, Route, A/D
    - Colonne = date del periodo (dd-mm)
    - Celle = ETA (se arrivo) o ETD (se partenza)
    """
    if flights.empty:
        return pd.DataFrame()

    subset = flights[flights["Weekday"] == weekday].copy()
    if subset.empty:
        return pd.DataFrame()

    subset["TimeValue"] = subset.apply(compute_time_value, axis=1)
    subset = subset.dropna(subset=["TimeValue"])

    if subset.empty:
        return pd.DataFrame()

    matrix = subset.pivot_table(
        index=["Flight", "Route", "AD"],
        columns="Date",
        values="TimeValue",
        aggfunc="first",
    )

    matrix = matrix.reindex(sorted(matrix.columns), axis=1)
    matrix = matrix.reset_index()

    new_cols = []
    for c in matrix.columns:
        if isinstance(c, date):
            new_cols.append(c.strftime("%d-%m"))
        else:
            new_cols.append(c)
    matrix.columns = new_cols

    matrix = matrix.sort_values(by=["Flight", "Route", "AD"]).reset_index(drop=True)
    return matrix


# =========================
# STYLING PER LA VIEW
# =========================

def style_ad(val: str) -> str:
    """
    Colore per la colonna AD:
    - P → rosso
    - A → verde
    """
    if val == "P":
        return "color: #f97373;"  # rosso soft
    if val == "A":
        return "color: #4ade80;"  # verde soft
    return ""


def style_time(row: pd.Series):
    """
    Colora gli orari (colonne data) in base al valore di AD nella riga:
    - se AD = P → orari rossi
    - se AD = A → orari verdi
    """
    ad = row.get("AD", None)
    color = None
    if ad == "P":
        color = "#f97373"
    elif ad == "A":
        color = "#4ade80"

    styles = []
    for col in row.index:
        if col in ("Codice Volo", "Aeroporto", "AD"):
            styles.append("")
            continue

        if pd.notna(row[col]) and row[col] != "" and color is not None:
            styles.append(f"color: {color};")
        else:
            styles.append("")
    return styles


# =========================
# UI STREAMLIT
# =========================

def main():
    st.set_page_config(
        page_title="Flight Matrix",
        page_icon="✈️",
        layout="wide",
    )

    # ---- CSS custom ----
    st.markdown(
        """
        <style>
        .block-container {
            padding-top: 1.5rem;
            padding-bottom: 2rem;
            padding-left: 2rem;
            padding-right: 2rem;
        }

        h1 {
            text-align: center;
        }

        .info-card {
            background: rgba(15,23,42,0.9);
            padding: 1rem 1.2rem;
            border-radius: 0.9rem;
            border: 1px solid rgba(148,163,184,0.35);
        }

        .info-card p {
            margin-bottom: 0.2rem;
        }

        .day-badge {
            display: inline-flex;
            align-items: center;
            padding: 0.25rem 0.75rem;
            border-radius: 999px;
            border: 1px solid rgba(148,163,184,0.8);
            background: rgba(15,23,42,0.9);
            font-size: 0.9rem;
            gap: 0.4rem;
        }

        .day-dot {
            width: 0.6rem;
            height: 0.6rem;
            border-radius: 999px;
            background: #38bdf8;
        }

        .legend-pill {
            display: inline-flex;
            align-items: center;
            gap: 0.4rem;
            padding: 0.25rem 0.6rem;
            border-radius: 999px;
            border: 1px solid rgba(148,163,184,0.4);
            font-size: 0.8rem;
            margin-right: 0.4rem;
        }

        .legend-color-arr {
            width: 0.9rem;
            height: 0.35rem;
            border-radius: 999px;
            background: #4ade80;
        }
        .legend-color-dep {
            width: 0.9rem;
            height: 0.35rem;
            border-radius: 999px;
            background: #f97373;
        }

        .uploadedFile { font-size: 0.9rem !important; }
        </style>
        """,
        unsafe_allow_html=True,
    )

    # Titolo
    st.title("✈️ Flight Matrix")

    # Intro card
    with st.container():
        st.markdown(
            """
            <div class="info-card">
                <p>🛫🛬 <strong>Carica il PDF con gli orari dei voli</strong>.</p>
                <p style="margin-top:0.35rem;">L'app:</p>
                <ul style="margin-top:0.15rem;">
                    <li>considera <strong>solo voli passeggeri (PAX)</strong></li>
                    <li>esclude i voli <strong>CARGO</strong></li>
                    <li>raggruppa per <strong>giorno della settimana</strong></li>
                    <li>mostra una <strong>matrice</strong> con i voli per tipologia giorno</li>
                </ul>
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.write("")

    uploaded_file = st.file_uploader("Carica il PDF con gli orari dei voli", type=["pdf"])

    if uploaded_file is None:
        st.info("Carica il PDF per procedere.")
        return

    # Parsing
    with st.spinner("Parsing del PDF in corso..."):
        flights_df = parse_pdf_to_flights_df(uploaded_file)

    if flights_df.empty:
        st.error("Non sono stati trovati voli PAX o la struttura del PDF non è riconosciuta.")
        return

    # Metriche globali
    unique_days = sorted(flights_df["Date"].unique())
    num_days = len(unique_days)
    num_flights = len(flights_df)

    col1, col2, col3 = st.columns([1, 1, 2])
    with col1:
        st.metric("Voli PAX estratti", num_flights)
    with col2:
        st.metric("Giorni coperti", num_days)
    with col3:
        if num_days > 0:
            start = unique_days[0]
            end = unique_days[-1]
            st.write(
                f"📆 Periodo: **{start.strftime('%d/%m/%Y')} – {end.strftime('%d/%m/%Y')}**"
            )

    st.success("Parsing completato.")

    # Giorni effettivamente presenti
    weekdays_present = sorted(
        flights_df["Weekday"].unique(),
        key=lambda x: WEEKDAY_ORDER.index(x),
    )

    # Sidebar
    st.sidebar.header("Filtro giorno")
    selected_weekday = st.sidebar.selectbox(
        "Seleziona giorno della settimana",
        options=weekdays_present,
        format_func=lambda x: WEEKDAY_LABELS_IT.get(x, x),
    )

    matrix_df = build_matrix_for_weekday(flights_df, selected_weekday)

    if matrix_df.empty:
        st.warning("Per il giorno selezionato non sono stati trovati voli PAX con orari validi.")
        return

    # Dati per la tipologia di giorno selezionata
    label_it = WEEKDAY_LABELS_IT.get(selected_weekday, selected_weekday)
    weekday_ops = flights_df[flights_df["Weekday"] == selected_weekday]
    weekday_flights_count = len(weekday_ops)
    weekday_dates_count = weekday_ops["Date"].nunique()

    # Badge giorno
    st.markdown(
        f"""
        <div style="margin-top: 1.2rem; margin-bottom: 0.3rem;">
            <span class="day-badge">
                <span class="day-dot"></span>
                <span>{label_it}</span>
            </span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Numero voli per tipologia di giorno
    st.markdown(
        f"**Voli PAX per questo tipo di giorno:** {weekday_flights_count} "
        f"(su {weekday_dates_count} {label_it.lower()} nel periodo caricato)"
    )

    # Legend arrivi/partenze
    st.markdown(
        """
        <div style="margin-bottom: 0.6rem; margin-top: 0.2rem;">
            <span class="legend-pill">
                <span class="legend-color-arr"></span>
                <span>Arrivi (A)</span>
            </span>
            <span class="legend-pill">
                <span class="legend-color-dep"></span>
                <span>Partenze (P)</span>
            </span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # --------- FILTRI MATRICE ---------
    with st.expander("Filtri matrice", expanded=False):
        flight_filter = st.text_input(
            "Filtra per Codice Volo (contiene)",
            key="flt_flight",
            placeholder="Es. FR, EN8, DX1702..."
        )

        airport_options = sorted(matrix_df["Route"].unique())
        selected_airports = st.multiselect(
            "Filtra per Aeroporto",
            options=airport_options,
            key="flt_airport",
        )

        ad_choice = st.radio(
            "Tipo di movimento",
            ["Arrivi e partenze", "Solo arrivi (A)", "Solo partenze (P)"],
            horizontal=True,
            key="flt_ad",
        )

    # Applica i filtri alla matrice
    matrix_filtered = matrix_df.copy()

    if flight_filter:
        matrix_filtered = matrix_filtered[
            matrix_filtered["Flight"].str.contains(flight_filter, case=False, na=False)
        ]

    if selected_airports:
        matrix_filtered = matrix_filtered[
            matrix_filtered["Route"].isin(selected_airports)
        ]

    if ad_choice == "Solo arrivi (A)":
        matrix_filtered = matrix_filtered[matrix_filtered["AD"] == "A"]
    elif ad_choice == "Solo partenze (P)":
        matrix_filtered = matrix_filtered[matrix_filtered["AD"] == "P"]

    if matrix_filtered.empty:
        st.warning("Nessun volo corrisponde ai filtri impostati.")
    else:
        # Rinomina colonne per la visualizzazione
        display_df = matrix_filtered.rename(
            columns={"Flight": "Codice Volo", "Route": "Aeroporto"}
        )

        # Stile AD + orari
        if "AD" in display_df.columns:
            styled_df = (
                display_df
                .style
                .apply(style_time, axis=1)
                .map(style_ad, subset=["AD"])
            )
        else:
            styled_df = display_df.style

        st.dataframe(styled_df, use_container_width=True, height=650)

        # Export CSV
        csv_buffer = display_df.to_csv(index=False).encode("utf-8")
        st.download_button(
            label="⬇️ Scarica matrice in CSV",
            data=csv_buffer,
            file_name=f"flight_matrix_{label_it.lower()}.csv",
            mime="text/csv",
        )

    # --------- GRAFICO ARRIVI/PARTENZE PER GIORNO ---------
    st.markdown("### Andamento giornaliero Arrivi / Partenze")

    chart_df = flights_df.copy()

    def map_dir(ad: str) -> Optional[str]:
        if ad == "A":
            return "Arrivi"
        if ad in ("P", "D", "DEP", "DEPT", "DEPARTURE"):
            return "Partenze"
        return None

    chart_df["Dir"] = chart_df["AD"].map(map_dir)
    chart_df = chart_df.dropna(subset=["Dir"])

    if not chart_df.empty:
        daily_counts = (
            chart_df.groupby(["Date", "Dir"])["Flight"]
            .count()
            .unstack("Dir")
            .fillna(0)
            .sort_index()
        )

        # garantiamo sempre le due colonne, anche se una categoria manca
        for col in ["Arrivi", "Partenze"]:
            if col not in daily_counts.columns:
                daily_counts[col] = 0

        st.line_chart(daily_counts[["Arrivi", "Partenze"]])
    else:
        st.info("Nessun dato disponibile per costruire il grafico Arrivi/Partenze.")


if __name__ == "__main__":
    main()
