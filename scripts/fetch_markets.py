#!/usr/bin/env python3
"""B5 — zero-token markets snapshot.

Pulls the locked lineup (Nifty 50, Sensex, USD/INR, Brent crude, BTC, Gold,
Silver) with 1-day % change and writes feeds/markets.json. Runs on GitHub
Actions (free compute); assemble_edition.py injects the result verbatim as
edition.markets. The model never sees this file.

Source: Yahoo Finance's public (unauthenticated, no API key) chart endpoint —
the same one the `yfinance` package wraps internally. We hit it directly with
stdlib `urllib` to avoid an extra dependency. stooq.com's CSV endpoint (the
plan's other suggested source) returned "page does not exist" for every symbol
tried during implementation (index/FX/futures tickers not resolving there in
this environment) — Yahoo's endpoint worked for all seven instruments and is
used instead.

Gold/Silver: reported in the Indian convention — ₹ per 10g (gold) and ₹ per kg
(silver) — derived from Yahoo's GC=F/SI=F USD/troy-oz COMEX spot and the same
run's live USD/INR rate. The unit conversion (troy oz = 31.1034768 g) is exact;
this is the SPOT/wholesale price in Indian units, NOT a retail jewellery price
(which would add a local premium + GST/duty we have no clean free source for) —
labelled "spot" so that's unambiguous. The 1-day % change shown is the metal's
own USD move (the dominant driver); if USD/INR is unavailable that run, we fall
back to clearly-labelled USD/oz spot.
"""
import datetime
import json
import pathlib
import ssl
import urllib.request
import urllib.parse
from zoneinfo import ZoneInfo

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "feeds" / "markets.json"

UA = "Mozilla/5.0 (compatible; DailyCactusBot/1.0; +https://amanbeni.github.io/daily-cactus/)"
TIMEOUT = 10

try:
    import certifi
    _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _SSL_CTX = None   # fall back to the interpreter's default CA store

# label, yahoo symbol, unit label, USD-denominated flag (for the "clear label" rule)
INSTRUMENTS = [
    ("Nifty 50",  "^NSEI",   "",      False),
    ("Sensex",    "^BSESN",  "",      False),
    ("USD/INR",   "INR=X",   "₹",     False),
    ("Brent Crude", "BZ=F",  "$/bbl", True),
    ("Bitcoin",   "BTC-USD", "$",     True),
    ("Gold",      "GC=F",    "$/oz",  True),
    ("Silver",    "SI=F",    "$/oz",  True),
]

TROY_OZ_G = 31.1034768   # exact grams per troy ounce

# Metals to convert from USD/oz spot into the Indian convention, given USD/INR.
# label -> (unit label, grams basis). Gold quoted per 10g, silver per kg.
METAL_INR = {
    "Gold":   ("₹/10g", 10),
    "Silver": ("₹/kg",  1000),
}


def to_inr_metal(usd_per_oz: float, usd_inr: float, grams: float) -> float:
    """USD/troy-oz spot -> ₹ for the given grams basis. Exact unit math."""
    return usd_per_oz * usd_inr / TROY_OZ_G * grams


def quote_from_chart(result, now):
    """Compare the quote with the close before its own exchange trading date."""
    meta = result["meta"]
    price = meta.get("regularMarketPrice")
    market_time = meta.get("regularMarketTime")
    if price is None or market_time is None:
        raise ValueError("missing regularMarketPrice or regularMarketTime")
    tz = ZoneInfo(meta.get("exchangeTimezoneName") or "UTC")
    as_of = datetime.datetime.fromtimestamp(market_time, datetime.timezone.utc)
    trading_date = as_of.astimezone(tz).date()
    previous = []
    closes = result.get("indicators", {}).get("quote", [{}])[0].get("close", [])
    for stamp, close in zip(result.get("timestamp", []), closes):
        if close is not None and datetime.datetime.fromtimestamp(stamp, tz).date() < trading_date:
            previous.append((stamp, close))
    prev = max(previous)[1] if previous else meta.get("previousClose")
    # chartPreviousClose precedes the FIRST candle in the requested range.
    # It is only the right comparison when that first candle is today's.
    stamps = result.get("timestamp") or []
    if prev is None and stamps and datetime.datetime.fromtimestamp(stamps[0], tz).date() == trading_date:
        prev = meta.get("chartPreviousClose")
    closed = trading_date != now.astimezone(tz).date()
    pct = round((price / prev - 1) * 100, 2) if prev and not closed else None
    yahoo_pct = meta.get("regularMarketChangePercent")
    # Daily candles can have a null row even for an open 24/7 market (BTC).
    # Yahoo's quote-level daily change then has the missing prior close.
    if not closed and yahoo_pct is not None and (pct is None or abs(pct - yahoo_pct) > 0.005):
        pct = round(yahoo_pct, 2)
    return {"price": price, "pct": pct, "as_of": as_of.isoformat(), "closed": closed}


def fetch_quote(symbol: str, now=None):
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/"
           f"{urllib.parse.quote(symbol)}?range=5d&interval=1d")
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=TIMEOUT, context=_SSL_CTX) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    result = data["chart"]["result"][0]
    return quote_from_chart(result, now or datetime.datetime.now(datetime.timezone.utc))


def main():
    now = datetime.datetime.now(datetime.timezone.utc)
    quotes = []
    ok = failed = 0
    # Pass 1: fetch every instrument's raw USD/native price + 1-day %.
    raw = {}
    for label, symbol, unit, is_usd in INSTRUMENTS:
        try:
            q = fetch_quote(symbol, now)
            price, pct = q["price"], q["pct"]
            if price is None:
                raise ValueError("no regularMarketPrice")
            raw[label] = {"price": price, "pct": pct, "unit": unit, "usd": is_usd,
                          "as_of": q["as_of"], "closed": q["closed"]}
            ok += 1
        except Exception as ex:                      # noqa: BLE001
            print(f"markets: {label} ({symbol}) failed: {ex!r}")
            failed += 1

    usd_inr = raw.get("USD/INR", {}).get("price")

    # Pass 2: build output, converting the metals into the Indian convention
    # when USD/INR is available; otherwise keep clearly-labelled USD/oz spot.
    for label, symbol, unit, is_usd in INSTRUMENTS:
        r = raw.get(label)
        if not r:
            continue
        if label in METAL_INR and usd_inr:
            inr_unit, grams = METAL_INR[label]
            quotes.append({
                "label": label,
                "value": round(to_inr_metal(r["price"], usd_inr, grams)),
                "unit": inr_unit,          # "₹/10g" / "₹/kg" — spot, not retail
                "change_pct": r["pct"],    # metal's own USD 1-day move (dominant)
                "usd": False,
                "as_of": r["as_of"],
                "closed": r["closed"],
            })
        else:
            quotes.append({
                "label": label,
                "value": round(r["price"], 2),
                "unit": r["unit"],
                "change_pct": r["pct"],
                "usd": r["usd"],
                "as_of": r["as_of"],
                "closed": r["closed"],
            })

    payload = {
        "generated_at": now.isoformat(),
        "as_of": max((q["as_of"] for q in quotes), default=None),
        "quotes": quotes,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    print(f"Wrote {OUT.relative_to(ROOT)}: {ok} ok, {failed} failed")


if __name__ == "__main__":
    main()
