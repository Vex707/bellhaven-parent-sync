"""Normalization helpers so 'NW Sylvania Ave' and 'Northwest Sylvania Avenue' compare equal."""
import re
from difflib import SequenceMatcher

STREET_WORDS = {
    "st": "street", "str": "street", "ave": "avenue", "av": "avenue", "rd": "road", "dr": "drive",
    "ln": "lane", "blvd": "boulevard", "pk": "pike", "pke": "pike", "ct": "court", "pl": "place",
    "pkwy": "parkway", "hwy": "highway", "cir": "circle", "ter": "terrace", "trl": "trail", "sq": "square",
    "n": "north", "s": "south", "e": "east", "w": "west",
    "nw": "northwest", "ne": "northeast", "sw": "southwest", "se": "southeast",
}

# Website care offerings -> CRM care_type picklist.
CARE_TYPE_MAP = {
    "assisted living": "Assisted Living",
    "memory support": "Memory Care",
    "memory care": "Memory Care",
    "short-term rehabilitation & nursing": "Skilled Nursing",
    "skilled nursing": "Skilled Nursing",
    "independent living": "Independent Living",
}

NAME_WORDS = {"rehab": "rehabilitation", "and": "&", "centre": "center", "healthcare": "health care"}
NAME_STOPWORDS = {"the", "of", "at", "-", "bellhaven"}


def street_key(street: str) -> str:
    tokens = re.sub(r"[^\w\s]", " ", (street or "").lower()).split()
    return " ".join(STREET_WORDS.get(t, t) for t in tokens)


def street_number(street: str) -> str | None:
    m = re.match(r"\s*(\d+)", street or "")
    return m.group(1) if m else None


def is_po_box(street: str) -> bool:
    return bool(re.match(r"\s*p\.?\s*o\.?\s*box\b", street or "", re.I))


def phone_digits(phone: str) -> str:
    return re.sub(r"\D", "", phone or "")[-10:]


def name_key(name: str) -> str:
    text = (name or "").lower().replace("&", " & ")
    tokens = [NAME_WORDS.get(t, t) for t in re.sub(r"[^\w&\s-]", " ", text).split()]
    return " ".join(tokens)


def name_similarity(a: str, b: str) -> float:
    """0..1 similarity on the distinctive part of a facility name (brand words removed)."""
    def core(n):
        return " ".join(t for t in name_key(n).split() if t not in NAME_STOPWORDS)
    ca, cb = core(a), core(b)
    if not ca or not cb:
        return 0.0
    return SequenceMatcher(None, ca, cb).ratio()


def care_types(offerings: list[str]) -> list[str]:
    out = []
    for o in offerings:
        mapped = CARE_TYPE_MAP.get(o.strip().lower())
        if mapped and mapped not in out:
            out.append(mapped)
    return out
