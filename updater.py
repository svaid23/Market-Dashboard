import csv
import io
import json
import math
import statistics
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

BASE = "https://niftyindices.com"
HIST_PAGE = BASE + "/reports/historical-data"
# Nifty Indices changed these public historical endpoints in Jul-2026.
PRICE_URLS = [
    BASE + "/BackPage/getHistoricaldatatabletoString",
    BASE + "/Backpage.aspx/getHistoricaldatatabletoString",
]
VALUATION_URLS = [
    BASE + "/BackPage/getpepbHistoricaldataDBtoString",
    BASE + "/Backpage.aspx/getpepbHistoricaldataDBtoString",
]
ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
LATEST_FILE = DATA_DIR / "latest.json"
HISTORY_FILE = DATA_DIR / "history.json"

INDICES = [
    {
        "key": "large",
        "name": "NIFTY 50",
        "label": "Large Cap",
        "benchmark": True,
        "constituents": BASE + "/IndexConstituent/ind_nifty50list.csv",
    },
    {
        "key": "mid",
        "name": "NIFTY MIDCAP 100",
        "label": "Mid Cap",
        "benchmark": False,
        "constituents": BASE + "/IndexConstituent/ind_niftymidcap100list.csv",
    },
    {
        "key": "small",
        "name": "NIFTY SMALLCAP 250",
        "label": "Small Cap",
        "benchmark": False,
        "constituents": BASE + "/IndexConstituent/ind_niftysmallcap250list.csv",
    },
]

HEADERS = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "en-US,en;q=0.9",
    "Content-Type": "application/json; charset=UTF-8",
    "Origin": BASE,
    "Referer": HIST_PAGE,
    "X-Requested-With": "XMLHttpRequest",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
}
GET_HEADERS = {
    "Referer": BASE + "/",
    "User-Agent": HEADERS["User-Agent"],
    "Accept": "text/csv,text/plain,*/*",
}

OPPORTUNITY_WEIGHTS = {
    "valuation": 25,
    "relative_valuation": 15,
    "earnings": 25,
    "breadth": 15,
    "quality": 10,
    "momentum": 10,
}

RISK_WEIGHTS = {
    "valuation": 20,
    "relative_premium": 15,
    "volatility": 25,
    "drawdown": 20,
    "breadth_fragility": 10,
    "earnings_deterioration": 10,
}


def safe_float(value):
    if value is None:
        return None
    try:
        text = str(value).replace(",", "").strip()
        if not text or text.lower() in {"na", "nan", "null", "-"}:
            return None
        return float(text)
    except Exception:
        return None


def parse_any_date(value):
    if not value:
        return None
    text = str(value).strip()
    fmts = ["%d %b %Y", "%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y"]
    for fmt in fmts:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    return None


def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=False), encoding="utf-8")


def chunks(start, end, days=350):
    cursor = start
    while cursor <= end:
        chunk_end = min(cursor + timedelta(days=days - 1), end)
        yield cursor, chunk_end
        cursor = chunk_end + timedelta(days=1)


def decode_rows(resp):
    """Decode both the current Nifty Indices response and the legacy ASP.NET wrapper."""
    raw = (resp.text or "").lstrip("\ufeff").strip()
    if not raw:
        raise RuntimeError(f"Empty response from {resp.url}")
    try:
        payload = resp.json()
    except Exception as exc:
        preview = raw[:180].replace("\n", " ")
        raise RuntimeError(
            f"Non-JSON response from {resp.url} (HTTP {resp.status_code}): {preview!r}"
        ) from exc

    # Current API (Jul-2026 onward): direct JSON array.
    if isinstance(payload, list):
        return payload

    # Legacy API: {"d": "[...]"} or occasionally {"d": [...]}
    if isinstance(payload, dict):
        rows = payload.get("d", payload.get("data", []))
        if isinstance(rows, str):
            rows = rows.strip()
            if not rows:
                return []
            rows = json.loads(rows)
        if isinstance(rows, list):
            return rows

    raise RuntimeError(f"Unexpected JSON shape from {resp.url}: {type(payload).__name__}")


def request_rows(session, urls, payload, retries=2):
    """Try the current Nifty Indices endpoint first, then the legacy endpoint.

    This protects the scheduled dashboard from upstream path/response migrations.
    Both direct-array and legacy {"d": "[...]"} response shapes are supported.
    """
    if isinstance(urls, str):
        urls = [urls]
    errors = []
    for url in urls:
        last_error = None
        for attempt in range(retries):
            try:
                resp = session.post(url, headers=HEADERS, json=payload, timeout=45)
                resp.raise_for_status()
                rows = decode_rows(resp)
                if not rows:
                    raise RuntimeError(f"Empty data array from {url}")
                return rows
            except Exception as exc:
                last_error = exc
                try:
                    session.get(HIST_PAGE, headers={"User-Agent": HEADERS["User-Agent"]}, timeout=10)
                except Exception:
                    pass
                time.sleep(1.5 * (attempt + 1))
        errors.append(f"{url}: {last_error}")
    raise RuntimeError("All Nifty Indices endpoints failed: " + " | ".join(errors))


def fetch_price_history(session, index_name, start, end):
    output = {}
    for chunk_start, chunk_end in chunks(start, end):
        cinfo = (
            "{'name':'%s','startDate':'%s','endDate':'%s','indexName':'%s'}"
            % (
                index_name,
                chunk_start.strftime("%d-%b-%Y"),
                chunk_end.strftime("%d-%b-%Y"),
                index_name,
            )
        )
        rows = request_rows(session, PRICE_URLS, {"cinfo": cinfo})
        for row in rows:
            dt = parse_any_date(row.get("HistoricalDate") or row.get("DATE") or row.get("Date"))
            close = safe_float(row.get("CLOSE") or row.get("Close") or row.get("close"))
            if dt and close and close > 0:
                output[dt] = close
    return output


def fetch_valuation_history(session, index_name, start, end):
    output = {}
    for chunk_start, chunk_end in chunks(start, end):
        cinfo = (
            "{'name':'%s','startDate':'%s','endDate':'%s','indexName':'%s'}"
            % (
                index_name,
                chunk_start.strftime("%d-%b-%Y"),
                chunk_end.strftime("%d-%b-%Y"),
                index_name,
            )
        )
        rows = request_rows(session, VALUATION_URLS, {"cinfo": cinfo})
        for row in rows:
            dt = parse_any_date(row.get("DATE") or row.get("Date") or row.get("HistoricalDate"))
            pe = safe_float(row.get("pe") or row.get("P/E") or row.get("PE"))
            pb = safe_float(row.get("pb") or row.get("P/B") or row.get("PB"))
            dy = safe_float(row.get("divYield") or row.get("Div Yield") or row.get("Dividend Yield"))
            if dt and pe and pe > 0:
                output[dt] = {"pe": pe, "pb": pb, "div_yield": dy}
    return output


def last_common_date(price_map, valuation_map):
    common = sorted(set(price_map) & set(valuation_map))
    if not common:
        raise RuntimeError("No common price and valuation date")
    return common[-1]


def pair_on_or_before(price_map, valuation_map, target):
    common = {d for d in price_map if d in valuation_map and d <= target}
    if not common:
        return None
    dt = max(common)
    return {
        "date": dt,
        "close": price_map[dt],
        "pe": valuation_map[dt]["pe"],
        "pb": valuation_map[dt].get("pb"),
        "div_yield": valuation_map[dt].get("div_yield"),
    }


def pct_change(new, old):
    if new is None or old is None or old == 0:
        return None
    return new / old - 1.0


def earnings_value(close, pe):
    if not close or not pe:
        return None
    return close / pe


def clamp(value, lo=0.0, hi=100.0):
    if value is None:
        return None
    return max(lo, min(hi, float(value)))


def percentile(values, x):
    clean = sorted(v for v in values if v is not None and math.isfinite(v))
    if not clean or x is None or not math.isfinite(x):
        return None
    count = sum(1 for v in clean if v <= x)
    return count / len(clean)


def weighted_average(parts, weights):
    available = [(k, v) for k, v in parts.items() if v is not None and k in weights]
    if not available:
        return None, 0.0
    used_weight = sum(weights[k] for k, _ in available)
    value = sum(v * weights[k] for k, v in available) / used_weight
    total_weight = sum(weights.values())
    coverage = used_weight / total_weight * 100 if total_weight else 0
    return value, coverage


def linear_score(value, anchors):
    """Piecewise linear score. anchors = [(input, score), ...], sorted by input."""
    if value is None:
        return None
    anchors = sorted(anchors)
    if value <= anchors[0][0]:
        return anchors[0][1]
    if value >= anchors[-1][0]:
        return anchors[-1][1]
    for (x0, y0), (x1, y1) in zip(anchors, anchors[1:]):
        if x0 <= value <= x1:
            if x1 == x0:
                return y1
            return y0 + (value - x0) / (x1 - x0) * (y1 - y0)
    return None


def daily_returns(price_map, start, end):
    points = [(d, p) for d, p in price_map.items() if start <= d <= end and p and p > 0]
    points.sort()
    rets = []
    for (_, p0), (_, p1) in zip(points, points[1:]):
        if p0 > 0 and p1 > 0:
            rets.append(math.log(p1 / p0))
    return rets


def annualized_volatility(price_map, start, end):
    rets = daily_returns(price_map, start, end)
    if len(rets) < 40:
        return None
    return statistics.stdev(rets) * math.sqrt(252)


def max_drawdown(price_map, start, end):
    points = [(d, p) for d, p in price_map.items() if start <= d <= end and p and p > 0]
    points.sort()
    if not points:
        return None
    peak = points[0][1]
    worst = 0.0
    for _, p in points:
        peak = max(peak, p)
        dd = p / peak - 1.0
        worst = min(worst, dd)
    return worst


def drawdown_from_high(price_map, start, end):
    points = [p for d, p in price_map.items() if start <= d <= end and p and p > 0]
    if not points:
        return None
    high = max(points)
    current = price_map[max(d for d in price_map if d <= end)]
    return current / high - 1.0 if high else None


def fetch_constituents(session, url):
    resp = session.get(url, headers=GET_HEADERS, timeout=45)
    resp.raise_for_status()
    text = resp.content.decode("utf-8-sig", errors="replace")
    rows = list(csv.DictReader(io.StringIO(text)))
    symbols = []
    for row in rows:
        symbol = (row.get("Symbol") or row.get("SYMBOL") or "").strip()
        if symbol:
            symbols.append(symbol)
    if not symbols:
        raise RuntimeError("No symbols found in constituent file")
    return symbols


def extract_yf_close(frame, tickers):
    """Return dict[ticker] -> pandas Series without importing pandas at module import time."""
    if frame is None or getattr(frame, "empty", True):
        return {}
    output = {}
    cols = getattr(frame, "columns", None)
    if cols is None:
        return output

    try:
        # Normal multi-ticker yfinance layout: first level contains OHLC field names.
        if getattr(cols, "nlevels", 1) > 1:
            level0 = list(cols.get_level_values(0))
            if "Close" in level0:
                closes = frame["Close"]
                for ticker in tickers:
                    if ticker in closes.columns:
                        output[ticker] = closes[ticker]
                return output
            # Alternate ticker-first layout.
            level0_unique = set(cols.get_level_values(0))
            for ticker in tickers:
                if ticker in level0_unique:
                    sub = frame[ticker]
                    if "Close" in sub.columns:
                        output[ticker] = sub["Close"]
                elif (ticker, "Close") in cols:
                    output[ticker] = frame[(ticker, "Close")]
            return output

        # Single ticker layout.
        if "Close" in cols and tickers:
            output[tickers[0]] = frame["Close"]
    except Exception:
        return {}
    return output


def compute_breadth(symbols):
    import yfinance as yf

    yf_tickers = [f"{s}.NS" for s in symbols]
    series_map = {}
    batch_size = 60
    for i in range(0, len(yf_tickers), batch_size):
        batch = yf_tickers[i : i + batch_size]
        frame = yf.download(
            tickers=batch,
            period="1y",
            interval="1d",
            auto_adjust=True,
            progress=False,
            threads=True,
            timeout=30,
        )
        series_map.update(extract_yf_close(frame, batch))

    eligible50 = above50 = eligible200 = above200 = 0
    for ticker in yf_tickers:
        series = series_map.get(ticker)
        if series is None:
            continue
        try:
            s = series.dropna()
            n = len(s)
            if n >= 50:
                eligible50 += 1
                if float(s.iloc[-1]) > float(s.iloc[-50:].mean()):
                    above50 += 1
            if n >= 200:
                eligible200 += 1
                if float(s.iloc[-1]) > float(s.iloc[-200:].mean()):
                    above200 += 1
        except Exception:
            continue

    total = len(symbols)
    if eligible50 < max(10, int(total * 0.45)):
        raise RuntimeError(f"Breadth coverage too low ({eligible50}/{total} with 50DMA history)")

    return {
        "breadth_above_50dma_pct": round(above50 / eligible50 * 100, 1) if eligible50 else None,
        "breadth_above_200dma_pct": round(above200 / eligible200 * 100, 1) if eligible200 else None,
        "breadth_50_coverage_pct": round(eligible50 / total * 100, 1) if total else None,
        "breadth_200_coverage_pct": round(eligible200 / total * 100, 1) if total else None,
        "breadth_constituents": total,
    }


def growth_score(growth_pct):
    return linear_score(growth_pct, [(-10, 0), (0, 35), (8, 55), (15, 75), (25, 100)])


def acceleration_score(accel_pp):
    return linear_score(accel_pp, [(-10, 0), (-5, 20), (0, 50), (5, 75), (10, 100)])


def volatility_risk_score(vol_pct):
    return linear_score(vol_pct, [(10, 0), (15, 20), (20, 40), (25, 60), (30, 80), (35, 100)])


def drawdown_risk_score(max_dd_pct):
    if max_dd_pct is None:
        return None
    magnitude = abs(max_dd_pct)
    return linear_score(magnitude, [(8, 0), (12, 15), (18, 35), (25, 60), (32, 80), (42, 100)])


def opportunity_label(score):
    if score is None:
        return "Awaiting data"
    if score >= 75:
        return "Strong evidence"
    if score >= 60:
        return "Constructive"
    if score >= 45:
        return "Balanced"
    return "Demanding"


def risk_label(score):
    if score is None:
        return "Awaiting data"
    if score <= 30:
        return "Lower"
    if score <= 50:
        return "Moderate"
    if score <= 70:
        return "Elevated"
    return "High"


def prior_index(prior, key):
    if not prior:
        return None
    return next((x for x in prior.get("indices", []) if x.get("key") == key), None)


def build_live_snapshot(prior=None):
    today = date.today()
    # 500 calendar days gives enough room for current and 3-month-ago YoY earnings growth.
    start_price = today - timedelta(days=500)
    start_valuation = today - timedelta(days=5 * 365 + 60)

    session = requests.Session()
    session.headers.update(HEADERS)
    try:
        session.get(HIST_PAGE, timeout=8)
    except Exception:
        pass

    prepared = []
    common_as_of = None

    for cfg in INDICES:
        price_map = fetch_price_history(session, cfg["name"], start_price, today)
        valuation_map = fetch_valuation_history(session, cfg["name"], start_valuation, today)
        as_of = last_common_date(price_map, valuation_map)
        common_as_of = as_of if common_as_of is None else min(common_as_of, as_of)
        prepared.append({"cfg": cfg, "price": price_map, "valuation": valuation_map})

    if common_as_of < today - timedelta(days=10):
        raise RuntimeError(f"Core index data is stale: latest common date is {common_as_of.isoformat()}")

    results = []
    prepared_by_key = {x["cfg"]["key"]: x for x in prepared}
    five_year_start = common_as_of - timedelta(days=5 * 365)
    one_year_start = common_as_of - timedelta(days=365)

    for pack in prepared:
        cfg = pack["cfg"]
        price_map = pack["price"]
        valuation_map = pack["valuation"]

        current = pair_on_or_before(price_map, valuation_map, common_as_of)
        prior_1y = pair_on_or_before(price_map, valuation_map, common_as_of - timedelta(days=365))
        prior_6m = pair_on_or_before(price_map, valuation_map, common_as_of - timedelta(days=183))
        prior_3m = pair_on_or_before(price_map, valuation_map, common_as_of - timedelta(days=91))
        prior_15m = pair_on_or_before(price_map, valuation_map, common_as_of - timedelta(days=456))
        if not all([current, prior_1y, prior_6m, prior_3m, prior_15m]):
            raise RuntimeError(f"Insufficient history for {cfg['name']}")

        pe_values = [
            item["pe"]
            for dt, item in valuation_map.items()
            if five_year_start <= dt <= common_as_of and item.get("pe")
        ]
        pb_values = [
            item.get("pb")
            for dt, item in valuation_map.items()
            if five_year_start <= dt <= common_as_of and item.get("pb") and item.get("pb") > 0
        ]
        roe_values = [
            item.get("pb") / item.get("pe") * 100
            for dt, item in valuation_map.items()
            if five_year_start <= dt <= common_as_of
            and item.get("pe")
            and item.get("pb")
            and item.get("pe") > 0
            and item.get("pb") > 0
        ]

        pe_median = statistics.median(pe_values) if pe_values else None
        pb_median = statistics.median(pb_values) if pb_values else None
        pe_pctile = percentile(pe_values, current["pe"])
        pb_pctile = percentile(pb_values, current.get("pb")) if current.get("pb") else None

        earn_now = earnings_value(current["close"], current["pe"])
        earn_1y = earnings_value(prior_1y["close"], prior_1y["pe"])
        earn_3m = earnings_value(prior_3m["close"], prior_3m["pe"])
        earn_15m = earnings_value(prior_15m["close"], prior_15m["pe"])
        earn_growth = pct_change(earn_now, earn_1y)
        growth_3m_ago = pct_change(earn_3m, earn_15m)
        acceleration = None if earn_growth is None or growth_3m_ago is None else earn_growth - growth_3m_ago

        ret_6m = pct_change(current["close"], prior_6m["close"])
        ret_1y = pct_change(current["close"], prior_1y["close"])
        vol_1y = annualized_volatility(price_map, one_year_start, common_as_of)
        max_dd = max_drawdown(price_map, one_year_start, common_as_of)
        dd_high = drawdown_from_high(price_map, one_year_start, common_as_of)

        implied_roe = None
        if current.get("pb") and current.get("pe"):
            implied_roe = current["pb"] / current["pe"] * 100
        roe_pctile = percentile(roe_values, implied_roe) if implied_roe is not None else None

        item = {
            "key": cfg["key"],
            "label": cfg["label"],
            "index_name": cfg["name"],
            "as_of": common_as_of.isoformat(),
            "close": round(current["close"], 2),
            "pe": round(current["pe"], 2),
            "pb": round(current["pb"], 2) if current.get("pb") is not None else None,
            "dividend_yield_pct": round(current["div_yield"], 2) if current.get("div_yield") is not None else None,
            "earnings_yield_pct": round(100 / current["pe"], 2) if current.get("pe") else None,
            "pe_5y_median": round(pe_median, 2) if pe_median is not None else None,
            "pe_5y_percentile_pct": round(pe_pctile * 100, 1) if pe_pctile is not None else None,
            "pb_5y_median": round(pb_median, 2) if pb_median is not None else None,
            "pb_5y_percentile_pct": round(pb_pctile * 100, 1) if pb_pctile is not None else None,
            "implied_roe_pct": round(implied_roe, 1) if implied_roe is not None else None,
            "implied_roe_5y_percentile_pct": round(roe_pctile * 100, 1) if roe_pctile is not None else None,
            "ttm_earnings_index": round(earn_now, 4) if earn_now is not None else None,
            "ttm_earnings_growth_yoy_pct": round(earn_growth * 100, 1) if earn_growth is not None else None,
            "earnings_growth_3m_ago_yoy_pct": round(growth_3m_ago * 100, 1) if growth_3m_ago is not None else None,
            "earnings_acceleration_3m_pp": round(acceleration * 100, 1) if acceleration is not None else None,
            "price_return_6m_pct": round(ret_6m * 100, 1) if ret_6m is not None else None,
            "price_return_1y_pct": round(ret_1y * 100, 1) if ret_1y is not None else None,
            "relative_return_6m_vs_large_pp": None,
            "relative_return_1y_vs_large_pp": None,
            "valuation_premium_vs_large_pct": None,
            "valuation_premium_5y_percentile_pct": None,
            "volatility_1y_pct": round(vol_1y * 100, 1) if vol_1y is not None else None,
            "max_drawdown_1y_pct": round(max_dd * 100, 1) if max_dd is not None else None,
            "drawdown_from_52w_high_pct": round(dd_high * 100, 1) if dd_high is not None else None,
            "breadth_above_50dma_pct": None,
            "breadth_above_200dma_pct": None,
            "breadth_50_coverage_pct": None,
            "breadth_200_coverage_pct": None,
            "breadth_constituents": None,
            "breadth_source_status": "unavailable",
            "breadth_as_of": None,
            "opportunity_components": {},
            "opportunity_score": None,
            "opportunity_coverage_pct": None,
            "opportunity_label": None,
            "risk_components": {},
            "risk_score": None,
            "risk_coverage_pct": None,
            "risk_label": None,
        }
        results.append(item)

    # Relative valuation and relative momentum use Large Cap as the benchmark.
    large_result = next(x for x in results if x["key"] == "large")
    large_val_map = prepared_by_key["large"]["valuation"]
    for item in results:
        if item["key"] == "large":
            item["relative_return_6m_vs_large_pp"] = 0.0
            item["relative_return_1y_vs_large_pp"] = 0.0
            continue

        item["relative_return_6m_vs_large_pp"] = round(item["price_return_6m_pct"] - large_result["price_return_6m_pct"], 1)
        item["relative_return_1y_vs_large_pp"] = round(item["price_return_1y_pct"] - large_result["price_return_1y_pct"], 1)
        item["valuation_premium_vs_large_pct"] = round((item["pe"] / large_result["pe"] - 1) * 100, 1)

        item_val_map = prepared_by_key[item["key"]]["valuation"]
        premiums = []
        for dt in sorted(set(item_val_map) & set(large_val_map)):
            if not (five_year_start <= dt <= common_as_of):
                continue
            a = item_val_map[dt].get("pe")
            b = large_val_map[dt].get("pe")
            if a and b and a > 0 and b > 0:
                premiums.append(a / b - 1)
        current_premium = item["pe"] / large_result["pe"] - 1
        premium_pctile = percentile(premiums, current_premium)
        item["valuation_premium_5y_percentile_pct"] = round(premium_pctile * 100, 1) if premium_pctile is not None else None

    # Breadth is deliberately isolated from the core refresh. If Yahoo fails, preserve prior breadth if available.
    breadth_messages = []
    breadth_live_count = 0
    for cfg in INDICES:
        item = next(x for x in results if x["key"] == cfg["key"])
        try:
            symbols = fetch_constituents(session, cfg["constituents"])
            breadth = compute_breadth(symbols)
            item.update(breadth)
            item["breadth_source_status"] = "live"
            item["breadth_as_of"] = common_as_of.isoformat()
            breadth_live_count += 1
        except Exception as exc:
            old = prior_index(prior, cfg["key"])
            copied = False
            if old and old.get("breadth_above_50dma_pct") is not None:
                for field in [
                    "breadth_above_50dma_pct",
                    "breadth_above_200dma_pct",
                    "breadth_50_coverage_pct",
                    "breadth_200_coverage_pct",
                    "breadth_constituents",
                ]:
                    item[field] = old.get(field)
                item["breadth_source_status"] = "cached"
                item["breadth_as_of"] = old.get("breadth_as_of") or prior.get("as_of")
                copied = True
            breadth_messages.append(f"{cfg['label']}: {'cached' if copied else 'unavailable'} ({exc})")

    # Build Opportunity and Risk scores after every observable is ready.
    for item in results:
        pe_value = None if item["pe_5y_percentile_pct"] is None else 100 - item["pe_5y_percentile_pct"]
        pb_value = None if item["pb_5y_percentile_pct"] is None else 100 - item["pb_5y_percentile_pct"]
        valuation_component, _ = weighted_average({"pe": pe_value, "pb": pb_value}, {"pe": 60, "pb": 40})

        if item["key"] == "large":
            relative_valuation_component = 50.0
        elif item["valuation_premium_5y_percentile_pct"] is not None:
            relative_valuation_component = 100 - item["valuation_premium_5y_percentile_pct"]
        else:
            relative_valuation_component = None

        g_score = growth_score(item["ttm_earnings_growth_yoy_pct"])
        a_score = acceleration_score(item["earnings_acceleration_3m_pp"])
        earnings_component, _ = weighted_average({"growth": g_score, "acceleration": a_score}, {"growth": 65, "acceleration": 35})

        breadth_component, _ = weighted_average(
            {"dma50": item["breadth_above_50dma_pct"], "dma200": item["breadth_above_200dma_pct"]},
            {"dma50": 40, "dma200": 60},
        )

        quality_component = item["implied_roe_5y_percentile_pct"]

        if item["key"] == "large":
            momentum_component = 50.0
        else:
            m6 = clamp(50 + 2.5 * item["relative_return_6m_vs_large_pp"]) if item["relative_return_6m_vs_large_pp"] is not None else None
            m12 = clamp(50 + 2.5 * item["relative_return_1y_vs_large_pp"]) if item["relative_return_1y_vs_large_pp"] is not None else None
            momentum_component, _ = weighted_average({"m6": m6, "m12": m12}, {"m6": 50, "m12": 50})

        opportunity_parts = {
            "valuation": valuation_component,
            "relative_valuation": relative_valuation_component,
            "earnings": earnings_component,
            "breadth": breadth_component,
            "quality": quality_component,
            "momentum": momentum_component,
        }
        opp_score, opp_coverage = weighted_average(opportunity_parts, OPPORTUNITY_WEIGHTS)
        item["opportunity_components"] = {k: round(v, 1) if v is not None else None for k, v in opportunity_parts.items()}
        item["opportunity_score"] = round(opp_score, 1) if opp_score is not None else None
        item["opportunity_coverage_pct"] = round(opp_coverage, 1)
        item["opportunity_label"] = opportunity_label(opp_score)

        valuation_risk, _ = weighted_average(
            {"pe": item["pe_5y_percentile_pct"], "pb": item["pb_5y_percentile_pct"]},
            {"pe": 60, "pb": 40},
        )
        relative_premium_risk = None if item["key"] == "large" else item["valuation_premium_5y_percentile_pct"]
        vol_risk = volatility_risk_score(item["volatility_1y_pct"])
        dd_risk = drawdown_risk_score(item["max_drawdown_1y_pct"])
        breadth_fragility = None if breadth_component is None else 100 - breadth_component
        earnings_deterioration = None if earnings_component is None else 100 - earnings_component

        risk_parts = {
            "valuation": valuation_risk,
            "relative_premium": relative_premium_risk,
            "volatility": vol_risk,
            "drawdown": dd_risk,
            "breadth_fragility": breadth_fragility,
            "earnings_deterioration": earnings_deterioration,
        }
        risk_score, risk_coverage = weighted_average(risk_parts, RISK_WEIGHTS)
        item["risk_components"] = {k: round(v, 1) if v is not None else None for k, v in risk_parts.items()}
        item["risk_score"] = round(risk_score, 1) if risk_score is not None else None
        item["risk_coverage_pct"] = round(risk_coverage, 1)
        item["risk_label"] = risk_label(risk_score)

    results = sorted(results, key=lambda x: ["large", "mid", "small"].index(x["key"]))
    ranked = sorted(
        [x for x in results if x.get("opportunity_score") is not None],
        key=lambda x: (-x["opportunity_score"], x.get("risk_score") if x.get("risk_score") is not None else 999),
    )

    breadth_status = "live" if breadth_live_count == len(INDICES) else ("partial" if breadth_live_count else "cached_or_unavailable")
    source_status_value = "live" if breadth_live_count == len(INDICES) else "partial"
    source_message = "Official Nifty Indices price/valuation refresh succeeded."
    if breadth_live_count == len(INDICES):
        source_message += " Constituent breadth refreshed successfully."
    else:
        source_message += " Breadth was not fully live; cached or unavailable breadth is clearly marked."

    return {
        "as_of": common_as_of.isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "methodology_version": "2.2",
        "source_status": {
            "status": source_status_value,
            "used_cached_data": breadth_live_count != len(INDICES),
            "message": source_message,
            "components": {
                "core_index_data": {"status": "live", "message": "Nifty Indices price, P/E, P/B and dividend yield refreshed."},
                "breadth": {"status": breadth_status, "message": "; ".join(breadth_messages) if breadth_messages else "All three breadth universes refreshed."},
            },
        },
        "sources": [
            {
                "name": "Nifty Indices Historical Data",
                "url": "https://www.niftyindices.com/reports/historical-data",
                "purpose": "Index prices, P/E, P/B and dividend yield",
            },
            {
                "name": "Nifty Indices Constituents",
                "url": "https://www.niftyindices.com/indices/equity/broad-based-indices/nifty--50",
                "purpose": "Official index constituent lists used for breadth universes",
            },
            {
                "name": "Yahoo Finance via yfinance",
                "url": "https://finance.yahoo.com/",
                "purpose": "Constituent daily prices used only for 50DMA/200DMA breadth",
            },
            {
                "name": "Nifty Indices P/E methodology",
                "url": "https://www.niftyindices.com/resources/index-concepts/price-earnings-ratio",
                "purpose": "Index P/E and trailing earnings methodology",
            },
        ],
        "indices": results,
        "ranked_research_priority": [x["key"] for x in ranked],
        "score_weights": {"opportunity": OPPORTUNITY_WEIGHTS, "risk": RISK_WEIGHTS},
        "disclaimer": "The scores organize observable market evidence for research. They are not buy/sell recommendations, return forecasts, or guarantees of future performance.",
    }


def update_history(snapshot):
    history = load_json(HISTORY_FILE, {"snapshots": []})
    snapshots = history.get("snapshots", [])
    summary = {
        "as_of": snapshot.get("as_of"),
        "generated_at": snapshot.get("generated_at"),
        "methodology_version": snapshot.get("methodology_version"),
        "opportunity_scores": {x["key"]: x.get("opportunity_score") for x in snapshot.get("indices", [])},
        "risk_scores": {x["key"]: x.get("risk_score") for x in snapshot.get("indices", [])},
        "pe": {x["key"]: x.get("pe") for x in snapshot.get("indices", [])},
        "earnings_growth": {x["key"]: x.get("ttm_earnings_growth_yoy_pct") for x in snapshot.get("indices", [])},
        "breadth_200dma": {x["key"]: x.get("breadth_above_200dma_pct") for x in snapshot.get("indices", [])},
    }
    snapshots = [x for x in snapshots if x.get("as_of") != summary["as_of"]]
    snapshots.append(summary)
    snapshots = sorted(snapshots, key=lambda x: x.get("as_of") or "")[-72:]
    save_json(HISTORY_FILE, {"snapshots": snapshots})


def main():
    prior = load_json(LATEST_FILE, None)
    try:
        snapshot = build_live_snapshot(prior=prior)
        save_json(LATEST_FILE, snapshot)
        update_history(snapshot)
        print("Refresh succeeded for", snapshot["as_of"], "status", snapshot["source_status"]["status"])
    except Exception as exc:
        if prior:
            prior["generated_at"] = datetime.now(timezone.utc).isoformat()
            prior["source_status"] = {
                "status": "cached",
                "used_cached_data": True,
                "message": "Core live refresh failed; showing the last good data. Error: " + str(exc),
                "components": {
                    "core_index_data": {"status": "cached", "message": str(exc)},
                    "breadth": {"status": "cached", "message": "Preserved with prior snapshot."},
                },
            }
            save_json(LATEST_FILE, prior)
            print("::warning::Core refresh failed; preserved last good data: " + str(exc))
            print("Refresh failed; preserved last good data:", exc)
        else:
            raise


if __name__ == "__main__":
    main()
