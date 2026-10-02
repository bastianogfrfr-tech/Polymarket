"""Polymarket market data: temperature events (Gamma API) and order books (CLOB)."""
import json
import re
from dataclasses import dataclass
from datetime import date

import requests

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
SESSION = requests.Session()
SESSION.headers["User-Agent"] = "polybot/0.1"

MONTHS = ["january", "february", "march", "april", "may", "june", "july",
          "august", "september", "october", "november", "december"]


@dataclass
class Bucket:
    market_id: str
    question: str
    title: str          # e.g. "21°C", "16°C or below", "72-73°F"
    lo: float           # inclusive, in market unit (-inf for "or below")
    hi: float           # inclusive, in market unit (+inf for "or higher")
    yes_token: str
    no_token: str
    min_size: float
    tick: float
    taker_fee_bps: float
    yes_ask: float      # Gamma snapshot, only used for pre-filtering
    no_ask: float


@dataclass
class TempEvent:
    slug: str
    city: str
    day: date
    station: str        # ICAO, e.g. EGLC
    unit: str           # "C" or "F"
    buckets: list


def parse_bucket_title(title):
    """'21°C' -> (21, 21, 'C'); '16°C or below' -> (-inf, 16); '72-73°F' -> (72, 73)."""
    m = re.match(r"^\s*(-?\d+)(?:\s*-\s*(-?\d+))?\s*°\s*([CF])\s*(or below|or higher)?\s*$", title)
    if not m:
        return None
    a = float(m.group(1))
    b = float(m.group(2)) if m.group(2) else a
    unit, tail = m.group(3), m.group(4)
    if tail == "or below":
        return float("-inf"), b, unit
    if tail == "or higher":
        return a, float("inf"), unit
    return a, b, unit


def _slug_date(slug):
    m = re.search(r"-on-([a-z]+)-(\d+)-(\d{4})$", slug)
    if not m or m.group(1) not in MONTHS:
        return None
    return date(int(m.group(3)), MONTHS.index(m.group(1)) + 1, int(m.group(2)))


def fetch_temperature_events(days):
    """Open 'highest-temperature-in-<city>-on-<date>' events for the given dates."""
    events, offset = {}, 0
    while True:
        r = SESSION.get(f"{GAMMA}/events", params={
            "active": "true", "closed": "false", "tag_slug": "weather",
            "limit": 100, "offset": offset}, timeout=30)
        r.raise_for_status()
        page = r.json()
        for e in page:
            events[e["slug"]] = e
        if len(page) < 100:
            break
        offset += 100

    out = []
    for e in events.values():
        slug = e["slug"]
        if not slug.startswith("highest-temperature-in-"):
            continue
        d = _slug_date(slug)
        if d not in days:
            continue
        st = re.search(r"timeseries\?site=([A-Za-z0-9]{4})", e.get("description") or "")
        if not st:
            continue  # e.g. Hong Kong uses a different source
        buckets, unit = [], None
        for m in e.get("markets", []):
            if m.get("closed") or not m.get("acceptingOrders", True):
                continue
            parsed = parse_bucket_title(m.get("groupItemTitle") or "")
            if not parsed:
                continue
            lo, hi, unit = parsed
            yes, no = json.loads(m["clobTokenIds"])
            buckets.append(Bucket(
                market_id=m["id"], question=m["question"], title=m["groupItemTitle"],
                lo=lo, hi=hi, yes_token=yes, no_token=no,
                min_size=float(m.get("orderMinSize") or 5),
                tick=float(m.get("orderPriceMinTickSize") or 0.01),
                taker_fee_bps=float(m.get("takerBaseFee") or 0),
                yes_ask=float(m.get("bestAsk") or 1),
                no_ask=1 - float(m.get("bestBid") or 0)))
        if buckets:
            city = slug[len("highest-temperature-in-"):].rsplit("-on-", 1)[0]
            out.append(TempEvent(slug, city, d, st.group(1).upper(), unit, buckets))
    return out


def best_prices(token_id):
    """(best_bid, best_ask, ask_size) from the CLOB order book."""
    r = SESSION.get(f"{CLOB}/book", params={"token_id": token_id}, timeout=20)
    r.raise_for_status()
    book = r.json()
    bids = [(float(x["price"]), float(x["size"])) for x in book.get("bids", [])]
    asks = [(float(x["price"]), float(x["size"])) for x in book.get("asks", [])]
    bid = max(bids)[0] if bids else 0.0
    if asks:
        ask, size = min(asks)
    else:
        ask, size = 1.0, 0.0
    return bid, ask, size
