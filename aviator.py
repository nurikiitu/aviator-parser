import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

import pytz
import airportsdata


# ==============================
# Airports DB
# ==============================
AIRPORTS = airportsdata.load("IATA")

TIME_RE = re.compile(r"^\d{4}(\+\d+)?$")
SEGMENT_START_RE = re.compile(r"^\s*\d+\s+")
MONTH_FMT = "%d%b"


RU_MONTH = {
    "JAN": "янв.", "FEB": "февр.", "MAR": "мар.", "APR": "апр.",
    "MAY": "мая",  "JUN": "июн.",  "JUL": "июл.", "AUG": "авг.",
    "SEP": "сент.","OCT": "окт.",  "NOV": "нояб.","DEC": "дек."
}


# ==============================
# Data class
# ==============================
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


# ==============================
# Helpers
# ==============================
def airline_name(iata2: str) -> str:
    known = {
        "KC": "Air Astana",
        "LH": "Lufthansa",
        "TK": "Turkish Airlines",
        "UA": "United Airlines",
    }
    return known.get(iata2.upper(), "")


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


def parse_date(date_str: str, year: int) -> datetime:
    date_str = date_str.strip().upper()
    try:
        d = datetime.strptime(date_str, MONTH_FMT)
        return d.replace(year=year)
    except ValueError:
        mon = date_str[2:]
        day = int(date_str[:2])
        month_map = {
            "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
            "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12
        }
        return datetime(year, month_map[mon], day)


def pick_times(tokens: List[str]) -> Optional[Tuple[str, str, int]]:
    times = []
    for t in tokens:
        if TIME_RE.match(t.strip()):
            times.append(t.strip())

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


def city_for_iata(iata: str) -> str:
    info = AIRPORTS.get(iata.upper())
    return info.get("city") if info else iata


# ==============================
# Core parsing
# ==============================
def parse_segment_line(line: str, year: int) -> Optional[Segment]:
    if not SEGMENT_START_RE.match(line):
        return None

    tokens = line.strip().split()
    if len(tokens) < 6:
        return None

    airline = None
    flight_no = None

    t1 = tokens[1].upper()
    t2 = tokens[2].upper()

    # TK1921
    m = re.match(r"^([A-Z]{2})(\d{1,4})", t1)
    if m:
        airline = m.group(1)
        flight_no = m.group(2)
        cursor = 2
    else:
        if re.match(r"^[A-Z]{2}$", t1) and re.match(r"^\d{1,4}", t2):
            airline = t1
            flight_no = re.match(r"^(\d{1,4})", t2).group(1)
            cursor = 3
        else:
            return None

    date_str = None
    for i in range(cursor, len(tokens)):
        if re.match(r"^\d{2}[A-Z]{3}$", tokens[i].upper()):
            date_str = tokens[i].upper()
            date_idx = i
            break
    if not date_str:
        return None

    origin = dest = None
    route_idx = None

    for i in range(date_idx + 1, len(tokens)):
        tok = tokens[i].upper()

        # ✅ NEW: если встречаем ALAIST*SS1, достаем первые 6 букв
        m_route = re.match(r"^([A-Z]{6})", tok)
        if m_route:
            route6 = m_route.group(1)
            origin, dest = route6[:3], route6[3:]
            route_idx = i
            break

    if not origin or not dest:
        return None

    times = pick_times(tokens)
    if not times:
        return None

    dep_hhmm, arr_hhmm, arr_offset = times

    base_date = parse_date(date_str, year)
    dep_naive = base_date.replace(hour=int(dep_hhmm[:2]), minute=int(dep_hhmm[2:]))
    arr_naive = base_date.replace(hour=int(arr_hhmm[:2]), minute=int(arr_hhmm[2:])) + timedelta(days=arr_offset)

    tz_from = tz_for_iata(origin)
    tz_to = tz_for_iata(dest)

    if not tz_from or not tz_to:
        if arr_offset == 0 and arr_naive < dep_naive:
            arr_naive += timedelta(days=1)
        dep_local = dep_naive
        arr_local = arr_naive
    else:
        dep_local = pytz.timezone(tz_from).localize(dep_naive)
        arr_local = pytz.timezone(tz_to).localize(arr_naive)

        dep_utc = dep_local.astimezone(pytz.UTC)
        arr_utc = arr_local.astimezone(pytz.UTC)

        while arr_utc < dep_utc:
            arr_local += timedelta(days=1)
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


# ==============================
# Build itinerary
# ==============================
def format_date_ru(date_str: str) -> str:
    day = date_str[:2].lstrip("0")
    mon = date_str[2:]
    return f"{day} {RU_MONTH.get(mon, mon)}"


def build_itinerary(text: str, year: Optional[int] = None) -> str:
    if year is None:
        year = datetime.now().year

    lines = [l for l in text.splitlines() if l.strip()]
    segments: List[Segment] = []

    for ln in lines:
        seg = parse_segment_line(ln, year)
        if seg:
            segments.append(seg)

    if not segments:
        return "⚠️ Сегменты не распознаны."

    segments.sort(key=lambda s: s.dep_local)

    out = []
    prev_arr = None

    for s in segments:
        if prev_arr:
            lay = s.dep_local - prev_arr
            if lay.total_seconds() > 0:
                out.append(f"_Пересадка {human_duration(lay)}_")

        dur = human_duration(s.arr_local - s.dep_local)
        carrier = f", {s.airline_name}" if s.airline_name else ""

        out.append(
            f"🗓️{format_date_ru(s.date_str)} {s.dep_local.strftime('%H:%M')} – {s.arr_local.strftime('%H:%M')}, "
            f"{city_for_iata(s.origin)} — {city_for_iata(s.dest)}, "
            f"{s.airline} {s.flight}{carrier}. {dur}"
        )

        prev_arr = s.arr_local

    return "\n".join(out)


# ==============================
# Console input
# ==============================
if __name__ == "__main__":
    print("Вставь PNR текст.")
    print("Нажми Enter на пустой строке для завершения.\n")

    lines = []
    while True:
        line = input()
        if line.strip() == "":
            break
        lines.append(line)

    pnr_text = "\n".join(lines)

    print("\n✈️ Вариант 1 ✈️")
    print(build_itinerary(pnr_text, year=2026))