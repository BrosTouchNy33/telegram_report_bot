from __future__ import annotations
import re

# Khmer digits → Latin
_KHMER_DIGITS = str.maketrans("០១២៣៤៥៦៧៨៩", "0123456789")

def normalize_digits(text: str) -> str:
    return (text or "").translate(_KHMER_DIGITS)

# Numbers like 2,000 or 150000 or 12.50
_AMOUNT_RE = re.compile(r"(?<!\w)([+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)(?!\w)")

# Simple word-hints to decide sign & default category
POSITIVE_HINTS = {
    "deposit","income","revenue","sale","sales","add","topup","top-up",
    "ឈ្នះ","បញ្ចូល","ដាក់","ចូល"
}
NEGATIVE_HINTS = {
    "withdraw","expense","cost","bet","pay","paid","payout","minus",
    "ដក","ចេញ","ចំណាយ","ភ្នាល់","បង់"
}

# Explicit category patterns
# e.g. "category: food", "cat: salary", or a simple tag "#lunch"
_CAT_DIRECT_RE = re.compile(r"(?:^|\s)(?:category|cat)\s*:\s*([A-Za-z0-9_\-\s\u1780-\u17FF]{2,40})", re.IGNORECASE)
_HASH_TAG_RE   = re.compile(r"#([A-Za-z0-9_\-]{2,40})")

def looks_like_money(raw: str) -> bool:
    no_commas = raw.replace(",", "")
    return ("," in raw) or (len(no_commas.split(".")[0]) >= 4)

def extract_signed_amounts(note: str) -> list[float]:
    """
    Extract amounts and assign a sign from hints:
      - if only NEGATIVE hints -> negative
      - if only POSITIVE hints -> positive
      - if both or none -> positive (default)
    Also ignores tiny values (<1000) unless they look like money with comma.
    """
    text = normalize_digits(note)
    lowered = text.lower()
    neg = any(k in lowered for k in NEGATIVE_HINTS)
    pos = any(k in lowered for k in POSITIVE_HINTS)
    sign = -1.0 if (neg and not pos) else 1.0

    signed = []
    for m in _AMOUNT_RE.finditer(text):
        raw = m.group(1)
        try:
            val = float(raw.replace(",", ""))
        except ValueError:
            continue
        if not looks_like_money(raw) and abs(val) < 1000:
            continue
        signed.append(sign * val)
    return signed

def infer_category(text: str) -> str | None:
    """
    Priority:
      1) explicit "category: XXX" / "cat: XXX"
      2) hashtag like "#food"
      3) hints → "income" or "expense"
      4) None
    """
    if not text:
        return None
    t = text.strip()

    m = _CAT_DIRECT_RE.search(t)
    if m:
        return m.group(1).strip().lower()

    mh = _HASH_TAG_RE.search(t)
    if mh:
        return mh.group(1).strip().lower()

    lowered = t.lower()
    pos = any(k in lowered for k in POSITIVE_HINTS)
    neg = any(k in lowered for k in NEGATIVE_HINTS)
    if pos and not neg:
        return "income"
    if neg and not pos:
        return "expense"
    return None

def human_amount(a: float) -> str:
    return f"{a:,.0f}" if float(a).is_integer() else f"{a:,.2f}"
