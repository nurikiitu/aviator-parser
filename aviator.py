import csv
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Tuple
from urllib.request import Request, urlopen

import pytz
import airportsdata


# ============================================================
# CONFIG
# ============================================================

# ✅ Your Google Sheet -> CSV export (must be publicly viewable: Anyone with link -> Viewer)
RU_OVERRIDES_URL = (
    "https://docs.google.com/spreadsheets/d/"
    "1pXSvaJT76g-ScBu7GxiduHM54UM8zjda3gHJYdjh_Is"
    "/export?format=csv"
)

# Local cache file
RU_OVERRIDES_CACHE_PATH = "data/ru_overrides.csv"

# Refresh interval (hours)
RU_OVERRIDES_MAX_AGE_HOURS = 24

# Default year when PNR has only 15FEB (no year)
DEFAULT_YEAR = 2026


# ============================================================
# AIRPORTS DB (IATA -> tz/city/name etc.)
# ============================================================
AIRPORTS = airportsdata.load("IATA")

TIME_RE = re.compile(r"^\d{4}(\+\d+)?$")        # 0435 or 0435+1
SEGMENT_START_RE = re.compile(r"^\s*\d+\s+")    # segment line begins with number
MONTH_FMT = "%d%b"                               # 15FEB

RU_MONTH = {
    "JAN": "янв.", "FEB": "февр.", "MAR": "мар.", "APR": "апр.",
    "MAY": "мая",  "JUN": "июн.",  "JUL": "июл.", "AUG": "авг.",
    "SEP": "сент.","OCT": "окт.",  "NOV": "нояб.","DEC": "дек."
}


# ============================================================
# DATA CLASSES
# ============================================================
@dataclass
class Segment:
    airline: str
    flight: str
    date_str: str
    origin: str
    dest: str
    dep_local: datetime
    arr_local: datetime
    airline_name: str = ""


# ============================================================
# DOWNLOAD + CACHE RU OVERRIDES
# ============================================================
def download_text(url: str, timeout: int = 4) -> str:
    req = Request(url, headers={"User-Agent": "aviator-parser/1.0"})
    with urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8")


def ensure_ru_overrides(
    url: str,
    local_path: str = RU_OVERRIDES_CACHE_PATH,
    max_age_hours: int = RU_OVERRIDES_MAX_AGE_HOURS,
) -> str:
    """
    Ensures local cached CSV exists and is fresh enough.
    Returns local file path, or "" if no file available.
    """
    p = Path(local_path)
    p.parent.mkdir(parents=True, exist_ok=True)

    if p.exists():
        age_seconds = time.time() - p.stat().st_mtime
        if age_seconds < max_age_hours * 3600:
            return str(p)

    try:
        text = download_text(url, timeout=4)
        p.write_text(text, encoding="utf-8")
        return str(p)
    except Exception:
        # fallback to existing cache if present
        if p.exists():
            return str(p)
        return ""


def load_ru_overrides_csv(path: str) -> dict:
    """
    CSV structure (2 columns):
    iata,airport_ru

    Example:
    ALA,Алматы
    MXP,Мальпенса
    LGW,Гатвик
    """
    data = {}
    if not path:
        return data

    try:
        with open(path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            # expected headers: iata, airport_ru
            for row in reader:
                iata = (row.get("iata") or "").strip().upper()
                if not iata:
                    continue
                data[iata] = (row.get("airport_ru") or "").strip()
    except Exception:
        return {}

    return data


# Load RU overrides on startup (cached)
_RU_OVERRIDES_LOCAL = ensure_ru_overrides(RU_OVERRIDES_URL)
RU_OVERRIDES = load_ru_overrides_csv(_RU_OVERRIDES_LOCAL)


# ============================================================
# AIRLINE NAME (optional)
# ============================================================
def airline_name(iata2: str) -> str:
    known = {
        "KC": "Air Astana",
        "LH": "Lufthansa",
        "TK": "Turkish Airlines",
        "UA": "United Airlines",
    }
    return known.get(iata2.upper(), "")


# ============================================================
# RUSSIAN PLURALS
# ============================================================
def plural_ru(n: int, form1: str, form2: str, form5: str) -> str:
    n = abs(int(n))
    if 11 <= (n % 100) <= 14:
        return form5
    last = n % 10
    if last == 1:
        return form1
    if 2 <= last <= 4:
        return form2
    return form5


def human_duration(td: timedelta) -> str:
    total = int(td.total_seconds())
    if total < 0:
        total = abs(total)

    hours = total // 3600
    minutes = (total % 3600) // 60

    parts = []
    if hours > 0:
        parts.append(f"{hours} {plural_ru(hours, 'час', 'часа', 'часов')}")
    if minutes > 0 or hours == 0:
        parts.append(f"{minutes} {plural_ru(minutes, 'минуту', 'минуты', 'минут')}")
    return ", ".join(parts)


# ============================================================
# DATE/TIME PARSING
# ============================================================
def parse_date(date_str: str, year: int) -> datetime:
    date_str = date_str.strip().upper()
    try:
        d = datetime.strptime(date_str, MONTH_FMT)
        return d.replace(year=year)
    except ValueError:
        mon = date_str[2:].upper()
        day = int(date_str[:2])
        month_map = {
            "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
            "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12
        }
        if mon not in month_map:
            raise ValueError(f"Unknown month: {mon}")
        return datetime(year, month_map[mon], day)


def pick_times(tokens: List[str]) -> Optional[Tuple[str, str, int]]:
    """
    Finds first two time tokens anywhere in line.
    dep: HHMM
    arr: HHMM or HHMM+1
    """
    times = []
    for t in tokens:
        t = t.strip()
        if TIME_RE.match(t):
            times.append(t)

    if len(times) < 2:
        return None

    dep = times[0]
    arr_raw = times[1]

    if "+" in arr_raw:
        arr, plus = arr_raw.split("+", 1)
        offset = int(plus)
    else:
        arr = arr_raw
        offset = 0

    return dep[:4], arr[:4], offset


def tz_for_iata(iata: str) -> Optional[str]:
    info = AIRPORTS.get(iata.upper())
    return info.get("tz") if info else None


def place_for_iata(iata: str) -> str:
    """
    What we display in itinerary:
    1) airport_ru overrides (e.g. "Мальпенса", "Гатвик")
    2) fallback to airportsdata city (English)
    3) fallback to IATA
    """
    iata = iata.upper()

    ov = RU_OVERRIDES.get(iata)
    if ov:
        return ov

    info = AIRPORTS.get(iata)
    if info and info.get("city"):
        return info["city"]

    return iata


# ============================================================
# SEGMENT PARSER
# ============================================================
def parse_segment_line(line: str, year: int) -> Optional[Segment]:
    """
    Supports:
    - 1 KC 921Y 15FEB 1 NQZFRA SS1  1125  1530  /DCKC /E
    - 1 TK 351 C 15MAR 7 ALAIST HK1 0635 1035 ... SEE RTSVC
    - 2 TK1921 C 15MAR 7 ISTGVA HK1 1225 1340 ...
    - 1 TK 353Y 15MAR 7 ALAIST*SS1 0950 1410 ...
    """
    if not SEGMENT_START_RE.match(line):
        return None

    tokens = line.strip().split()
    if len(tokens) < 6:
        return None

    # ---- airline + flight detection (supports J2, KC, TK, etc.) ----
    airline = None
    flight_no = None

    t1 = tokens[1].upper()
    t2 = tokens[2].upper() if len(tokens) > 2 else ""

    # Case 1: merged like TK1921Y / UA5405D / J254Y (rare)
    m1 = re.match(r"^([A-Z0-9]{2,3})(\d{1,4})", t1)
    if m1 and len(t1) > len(m1.group(1)):  # means digits really exist
        airline = m1.group(1)
        flight_no = m1.group(2)
        cursor = 2
    else:
        # Case 2: split like J2 54Y / KC 909D / TK 351
        if re.match(r"^[A-Z0-9]{2,3}$", t1):
            m2 = re.match(r"^(\d{1,4})", t2)  # take only digits from 54Y / 909D
            if m2:
                airline = t1
                flight_no = m2.group(1)
                cursor = 3
            else:
                return None
        else:
            return None

    # Find date token: 2 digits + 3 letters (15MAR)
    date_str = None
    date_idx = None
    for i in range(cursor, len(tokens)):
        cand = tokens[i].upper()
        if re.match(r"^\d{2}[A-Z]{3}$", cand):
            date_str = cand
            date_idx = i
            break
    if not date_str or date_idx is None:
        return None

    # Find route token: first 6 letters in a token (supports ALAIST*SS1)
    origin = dest = None
    for i in range(date_idx + 1, len(tokens)):
        tok = tokens[i].upper()
        m_route = re.match(r"^([A-Z]{6})", tok)
        if m_route:
            route6 = m_route.group(1)
            origin, dest = route6[:3], route6[3:]
            break

    if not origin or not dest:
        return None

    # Find times (first two HHMM tokens)
    times = pick_times(tokens)
    if not times:
        return None
    dep_hhmm, arr_hhmm, arr_offset = times

    # Build naive times for dep/arr on base date
    base_date = parse_date(date_str, year)
    dep_naive = base_date.replace(hour=int(dep_hhmm[:2]), minute=int(dep_hhmm[2:]))
    arr_naive = base_date.replace(hour=int(arr_hhmm[:2]), minute=int(arr_hhmm[2:])) + timedelta(days=arr_offset)

    tz_from = tz_for_iata(origin)
    tz_to = tz_for_iata(dest)

    # Fallback if TZ missing (best effort)
    if not tz_from or not tz_to:
        # naive fix only if no TZ data
        if arr_offset == 0 and arr_naive < dep_naive:
            arr_naive += timedelta(days=1)
        dep_local = dep_naive
        arr_local = arr_naive
    else:
        dep_tz = pytz.timezone(tz_from)
        arr_tz = pytz.timezone(tz_to)

        dep_local = dep_tz.localize(dep_naive)
        arr_local = arr_tz.localize(arr_naive)

        # ✅ Key fix: adjust arrival by comparing UTC (handles "arrival earlier" by TZ)
        dep_utc = dep_local.astimezone(pytz.UTC)
        arr_utc = arr_local.astimezone(pytz.UTC)

        while arr_utc < dep_utc:
            arr_local = arr_local + timedelta(days=1)
            arr_utc = arr_local.astimezone(pytz.UTC)

    return Segment(
        airline=airline,
        flight=flight_no,
        date_str=date_str,
        origin=origin,
        dest=dest,
        dep_local=dep_local,
        arr_local=arr_local,
        airline_name=airline_name(airline),
    )


# ============================================================
# ITINERARY BUILDING
# ============================================================
def format_date_ru(date_str: str) -> str:
    date_str = date_str.strip().upper()
    day = date_str[:2].lstrip("0")
    mon = date_str[2:].upper()
    return f"{day} {RU_MONTH.get(mon, mon)}"


def build_itinerary(text: str, year: Optional[int] = None) -> str:
    if year is None:
        year = datetime.now().year

    lines = [ln.rstrip("\n") for ln in text.splitlines() if ln.strip()]
    segments: List[Segment] = []

    for ln in lines:
        seg = parse_segment_line(ln, year)
        if seg:
            segments.append(seg)

    if not segments:
        return "⚠️ Сегменты не распознаны."

    segments.sort(key=lambda s: s.dep_local)

    out: List[str] = []
    prev_arr: Optional[datetime] = None

    for s in segments:
        if prev_arr is not None:
            lay = s.dep_local - prev_arr
            if lay.total_seconds() > 0:
                out.append(f"_Пересадка {human_duration(lay)}_")

        dur = human_duration(s.arr_local - s.dep_local)
        carrier = f", {s.airline_name}" if s.airline_name else ""

        out.append(
            f"🗓️{format_date_ru(s.date_str)} {s.dep_local.strftime('%H:%M')} – {s.arr_local.strftime('%H:%M')}, "
            f"{place_for_iata(s.origin)} — {place_for_iata(s.dest)}, "
            f"{s.airline} {s.flight}{carrier}. {dur}"
        )

        prev_arr = s.arr_local

    return "\n".join(out)


# ============================================================
# MAIN (PyCharm-friendly input)
# ============================================================
if __name__ == "__main__":
    print("Вставь PNR текст.")
    print("Нажми Enter на пустой строке для завершения.\n")

    lines = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line.strip() == "":
            break
        lines.append(line)

    pnr_text = "\n".join(lines)

    print("\n✈️ Вариант 1 ✈️")
    print(build_itinerary(pnr_text, year=DEFAULT_YEAR))