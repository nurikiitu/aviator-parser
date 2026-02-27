import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from aviator import build_itinerary
def test_kc_lh_itinerary_has_layover():
    text = """
1 KC 921Y 15FEB 1 NQZFRA SS1  1125  1530  /DCKC /E
2 LH 116Y 15FEB 1 FRAMUC SS1  1715  1810  /DCLH /E
"""
    out = build_itinerary(text, year=2026)
    assert "KC 921" in out
    assert "LH 116" in out
    assert "Пересадка 1 час, 45 минут" in out

def test_tk_formats_parse():
    text = """
1 TK 351 C 15MAR 7 ALAIST HK1 0635 1035 333 E 0 M SEE RTSVC
2 TK1921 C 15MAR 7 ISTGVA HK1 1225 1340 32Q E 0 M SEE RTSVC
"""
    out = build_itinerary(text, year=2026)
    assert "TK 351" in out
    assert "TK 1921" in out

def test_arrival_next_day():
    text = """
1 TK 350 C 25MAR 3 ISTALA HK1 2110 0435+1 333 E 0 M SEE RTSVC
"""
    out = build_itinerary(text, year=2026)
    assert "TK 350" in out
    # длительность не должна быть отрицательной
    assert "минут" in out or "час" in out

def test_garbage_lines_ignored():
    text = """
PLS ADD PAX MOBILE CTC FOR IRREG COMMUNICATION
H1DM.77E8*ANZ 0155/27FEB26
1 KC 921Y 15FEB 1 NQZFRA SS1  1125  1530  /DCKC /E
SOME RANDOM TEXT
"""
    out = build_itinerary(text, year=2026)
    assert "KC 921" in out