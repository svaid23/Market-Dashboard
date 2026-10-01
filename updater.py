import csv
import io
import json
import math
import re
import statistics
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
LATEST_FILE = DATA_DIR / "latest.json"
HISTORY_FILE = DATA_DIR / "history.json"

SCREENER = "https://www.screener.in"
INDEXPE = "https://indexpe.in"
NIFTY = "https://www.niftyindices.com"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

INDICES = [
    {
        "key": "large",
        "label": "Large Cap",
        "name": "NIFTY 50",
        "screener_slug": "NIFTY",
        "indexpe_slug": "nifty-50",
        "constituent_csv": NIFTY + "/IndexConstituent/ind_nifty50list.csv",
        "expected_constituents": 50,
        "max_screener_pages": 3,
    },
    {
        "key": "mid",
        "label": "Mid Cap",
        "name": "NIFTY MIDCAP 100",
        "screener_slug": "CNXMIDCAP",
        "indexpe_slug": "nifty-midcap-100",
        "constituent_csv": NIFTY + "/IndexConstituent/ind_niftymidcap100list.csv",
        "expected_constituents": 100,
        "max_screener_pages": 6,
    },
    {
        "key": "small",
        "label": "Small Cap",
        "name": "NIFTY SMALLCAP 250",
        "screener_slug": "SMALLCA250",
        "indexpe_slug": "nifty-smallcap-250",
        "constituent_csv": NIFTY + "/IndexConstituent/ind_niftysmallcap250list.csv",
        "expected_constituents": 250,
        "max_screener_pages": 14,
    },
]

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
        text = str(value).replace(",", "").replace("%", "").replace("₹", "").strip()
        if not text or text.lower() in {"na", "nan", "null", "-", "--"}:
            return None
        return float(text)
    except Exception:
        return None


def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=False), encoding="utf-8")


def clamp(value, lo=0.0, hi=100.0):
    if value is None:
        return None
    return max(lo, min(hi, float(value)))


def pct_change(new, old):
    if new is None or old is None or old == 0:
        return None
    return new / old - 1.0


def percentile(values, x):
    clean = sorted(v for v in values if v is not None and math.isfinite(v))
    if not clean or x is None or not math.isfinite(x):
        return None
    return sum(1 for v in clean if v <= x) / len(clean)


def weighted_average(parts, weights):
    available = [(k, v) for k, v in parts.items() if v is not None and k in weights]
    if not available:
        return None, 0.0
    used_weight = sum(weights[k] for k, _ in available)
    value = sum(v * weights[k] for k, v in available) / used_weight
    coverage = used_weight / sum(weights.values()) * 100
    return value, coverage


def linear_score(value, anchors):
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


def growth_score(growth_pct):
    return linear_score(growth_pct, [(-10, 0), (0, 35), (8, 55), (15, 75), (25, 100)])


def acceleration_score(accel_pp):
    return linear_score(accel_pp, [(-10, 0), (-5, 20), (0, 50), (5, 75), (10, 100)])


def quality_roe_score(roe_pct):
    return linear_score(roe_pct, [(6, 10), (10, 30), (14, 50), (18, 70), (24, 90), (30, 100)])


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


def http_get(session, url, *, timeout=35, params=None):
    last = None
    for attempt in range(3):
        try:
            r = session.get(url, headers=HEADERS, timeout=timeout, params=params)
            if r.status_code == 429:
                wait = int(r.headers.get("Retry-After", "3") or "3")
                time.sleep(min(wait, 15))
                last = RuntimeError(f"HTTP 429 from {r.url}")
                continue
            r.raise_for_status()
            return r
        except Exception as exc:
            last = exc
            time.sleep(1.2 * (attempt + 1))
    raise RuntimeError(f"GET failed for {url}: {last}")


def normalize_text(html):
    soup = BeautifulSoup(html, "html.parser")
    return soup, " ".join(soup.stripped_strings)


def regex_num(text, patterns):
    for pattern in patterns:
        m = re.search(pattern, text, flags=re.I)
        if m:
            value = safe_float(m.group(1))
            if value is not None:
                return value
    return None


def parse_screener_summary(html):
    soup, text = normalize_text(html)
    company_id = None
    for tag in soup.find_all(attrs={"data-company-id": True}):
        company_id = str(tag.get("data-company-id") or "").strip()
        if company_id:
            break
    if not company_id:
        m = re.search(r'data-company-id=["\'](\d+)["\']', html)
        if m:
            company_id = m.group(1)

    current = regex_num(text, [r"Current Price\s*₹?\s*([\d,.]+)"])
    pe = regex_num(text, [r"(?:^|\s)P/E\s*([\d,.]+)"])
    pb = regex_num(text, [r"Price to Book value\s*([\d,.]+)"])
    dy = regex_num(text, [r"Dividend Yield\s*([\d,.]+)\s*%"])
    cagr_1y = regex_num(text, [r"CAGR 1Yr\s*([+\-]?[\d,.]+)\s*%"])
    cagr_5y = regex_num(text, [r"CAGR 5Yr\s*([+\-]?[\d,.]+)\s*%"])

    high = low = None
    m = re.search(r"High / Low\s*₹?\s*([\d,.]+)\s*/\s*([\d,.]+)", text, flags=re.I)
    if m:
        high, low = safe_float(m.group(1)), safe_float(m.group(2))

    if not company_id:
        raise RuntimeError("Screener page did not expose data-company-id")
    if pe is None or current is None:
        raise RuntimeError("Screener summary missing current price or P/E")

    return {
        "company_id": company_id,
        "current": current,
        "pe": pe,
        "pb": pb,
        "dividend_yield": dy,
        "cagr_1y": cagr_1y,
        "cagr_5y": cagr_5y,
        "high_52w": high,
        "low_52w": low,
    }


def parse_chart_series(payload):
    datasets = payload.get("datasets") if isinstance(payload, dict) else None
    if not isinstance(datasets, list):
        raise RuntimeError("Unexpected Screener chart JSON shape")
    out = {}
    for ds in datasets:
        metric = str(ds.get("metric") or ds.get("label") or "").strip()
        values = ds.get("values") or []
        series = {}
        for row in values:
            if not isinstance(row, (list, tuple)) or len(row) < 2:
                continue
            try:
                dt = datetime.strptime(str(row[0])[:10], "%Y-%m-%d").date()
            except Exception:
                continue
            val = safe_float(row[1])
            if val is not None and math.isfinite(val):
                series[dt] = val
        if series:
            out[metric] = series
    return out


def choose_series(series_map, keywords, exclude=()):
    for name, series in series_map.items():
        lower = name.lower()
        if any(k in lower for k in keywords) and not any(x in lower for x in exclude):
            return series
    return {}


def fetch_screener_core(session, cfg):
    page_url = f"{SCREENER}/company/{cfg['screener_slug']}/"
    html = http_get(session, page_url).text
    summary = parse_screener_summary(html)
    company_id = summary["company_id"]

    price_url = f"{SCREENER}/api/company/{company_id}/chart/"
    price_long_resp = http_get(
        session,
        price_url,
        params={"q": "Price-DMA50-DMA200-Volume", "days": "1825", "consolidated": "true"},
    )
    # Screener can aggregate long windows to weekly points. Pull a separate 1Y
    # daily window for realised volatility, drawdown and 6M/1Y momentum.
    price_daily_resp = http_get(
        session,
        price_url,
        params={"q": "Price-DMA50-DMA200-Volume", "days": "365", "consolidated": "true"},
    )
    pe_resp = http_get(
        session,
        price_url,
        params={"q": "PE-EPS", "days": "1825", "consolidated": "true"},
    )

    try:
        price_long_series_map = parse_chart_series(price_long_resp.json())
        price_daily_series_map = parse_chart_series(price_daily_resp.json())
        pe_series_map = parse_chart_series(pe_resp.json())
    except Exception as exc:
        raise RuntimeError(f"Screener chart JSON parse failed for {cfg['name']}: {exc}") from exc

    price_map = choose_series(price_long_series_map, ["price"], exclude=["sales", "book", "market cap"])
    price_daily_map = choose_series(price_daily_series_map, ["price"], exclude=["sales", "book", "market cap"])
    pe_map = choose_series(pe_series_map, ["pe", "p/e", "price to earning"])
    eps_map = choose_series(pe_series_map, ["eps", "earning per share"])

    if not price_map:
        raise RuntimeError(f"No Price series from Screener chart for {cfg['name']}")
    if not price_daily_map:
        raise RuntimeError(f"No 1Y daily Price series from Screener chart for {cfg['name']}")
    if not pe_map:
        raise RuntimeError(f"No P/E series from Screener chart for {cfg['name']}")

    return {
        "page_url": page_url,
        "summary": summary,
        "price_map": price_map,
        "price_daily_map": price_daily_map,
        "pe_map": pe_map,
        "eps_map": eps_map,
    }


def fetch_indexpe_check(session, cfg):
    url = f"{INDEXPE}/{cfg['indexpe_slug']}"
    try:
        html = http_get(session, url, timeout=25).text
        _, text = normalize_text(html)
        current_pe = regex_num(text, [r"Current PE Ratio\s*([\d,.]+)"])
        median_5y = regex_num(text, [r"5Y Median PE\s*([\d,.]+)"])
        updated = None
        m = re.search(r"Last updated:\s*(\d{1,2}\s+[A-Za-z]{3}\s+\d{4})", text, flags=re.I)
        if m:
            try:
                updated = datetime.strptime(m.group(1), "%d %b %Y").date().isoformat()
            except Exception:
                pass
        return {"status": "live", "url": url, "current_pe": current_pe, "median_5y": median_5y, "as_of": updated}
    except Exception as exc:
        return {"status": "unavailable", "url": url, "error": str(exc), "current_pe": None, "median_5y": None, "as_of": None}


def on_or_before(series, target):
    eligible = [d for d in series if d <= target]
    if not eligible:
        return None, None
    dt = max(eligible)
    return dt, series[dt]


def paired_on_or_before(a, b, target):
    common = [d for d in set(a) & set(b) if d <= target]
    if not common:
        return None
    dt = max(common)
    return dt, a[dt], b[dt]


def annualized_volatility(price_map, start, end):
    pts = sorted((d, p) for d, p in price_map.items() if start <= d <= end and p and p > 0)
    if len(pts) < 40:
        return None
    rets = [math.log(p1 / p0) for (_, p0), (_, p1) in zip(pts, pts[1:]) if p0 > 0 and p1 > 0]
    if len(rets) < 40:
        return None
    return statistics.stdev(rets) * math.sqrt(252)


def max_drawdown(price_map, start, end):
    pts = sorted((d, p) for d, p in price_map.items() if start <= d <= end and p and p > 0)
    if not pts:
        return None
    peak = pts[0][1]
    worst = 0.0
    for _, p in pts:
        peak = max(peak, p)
        worst = min(worst, p / peak - 1.0)
    return worst


def fetch_nifty_constituents(session, cfg):
    r = http_get(session, cfg["constituent_csv"], timeout=30)
    text = r.content.decode("utf-8-sig", errors="replace")
    rows = list(csv.DictReader(io.StringIO(text)))
    syms = []
    for row in rows:
        sym = (row.get("Symbol") or row.get("SYMBOL") or "").strip()
        if sym:
            syms.append(sym)
    if len(syms) < max(10, int(cfg["expected_constituents"] * 0.75)):
        raise RuntimeError(f"Official constituent CSV returned only {len(syms)} symbols")
    return syms


def fetch_screener_constituents(session, cfg):
    symbols = []
    seen = set()
    for page in range(1, cfg["max_screener_pages"] + 1):
        url = f"{SCREENER}/company/{cfg['screener_slug']}/"
        r = http_get(session, url, params={"page": page, "sort": "name", "order": "asc"}, timeout=30)
        soup = BeautifulSoup(r.text, "html.parser")
        found = 0
        for table in soup.find_all("table"):
            for a in table.find_all("a", href=True):
                href = a.get("href", "")
                m = re.match(r"/company/([^/]+)/", href)
                if not m:
                    continue
                sym = m.group(1).strip().upper()
                if sym in {cfg["screener_slug"].upper(), "ID"}:
                    continue
                if sym and sym not in seen:
                    seen.add(sym)
                    symbols.append(sym)
                    found += 1
        if found == 0:
            break
        if len(symbols) >= cfg["expected_constituents"]:
            break
        time.sleep(0.25)
    if len(symbols) < max(10, int(cfg["expected_constituents"] * 0.7)):
        raise RuntimeError(f"Screener fallback found only {len(symbols)} symbols")
    return symbols[: cfg["expected_constituents"] + 5]


def fetch_constituents(session, cfg):
    try:
        return fetch_nifty_constituents(session, cfg), "nifty_csv"
    except Exception as official_exc:
        try:
            return fetch_screener_constituents(session, cfg), "screener_pages"
        except Exception as fallback_exc:
            raise RuntimeError(f"Constituent sources failed. Nifty: {official_exc}; Screener: {fallback_exc}")


def extract_yf_close(frame, tickers):
    if frame is None or getattr(frame, "empty", True):
        return {}
    out = {}
    cols = getattr(frame, "columns", None)
    if cols is None:
        return out
    try:
        if getattr(cols, "nlevels", 1) > 1:
            level0 = list(cols.get_level_values(0))
            if "Close" in level0:
                closes = frame["Close"]
                for t in tickers:
                    if t in closes.columns:
                        out[t] = closes[t]
                return out
            top = set(cols.get_level_values(0))
            for t in tickers:
                if t in top:
                    sub = frame[t]
                    if "Close" in sub.columns:
                        out[t] = sub["Close"]
                elif (t, "Close") in cols:
                    out[t] = frame[(t, "Close")]
            return out
        if "Close" in cols and tickers:
            out[tickers[0]] = frame["Close"]
    except Exception:
        return {}
    return out


def compute_breadth(symbols):
    import yfinance as yf

    yf_tickers = [f"{s}.NS" for s in symbols]
    series_map = {}
    for i in range(0, len(yf_tickers), 60):
        batch = yf_tickers[i : i + 60]
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
            if len(s) >= 50:
                eligible50 += 1
                if float(s.iloc[-1]) > float(s.iloc[-50:].mean()):
                    above50 += 1
            if len(s) >= 200:
                eligible200 += 1
                if float(s.iloc[-1]) > float(s.iloc[-200:].mean()):
                    above200 += 1
        except Exception:
            continue

    total = len(symbols)
    if eligible50 < max(10, int(total * 0.45)):
        raise RuntimeError(f"Breadth coverage too low ({eligible50}/{total})")
    return {
        "breadth_above_50dma_pct": round(above50 / eligible50 * 100, 1) if eligible50 else None,
        "breadth_above_200dma_pct": round(above200 / eligible200 * 100, 1) if eligible200 else None,
        "breadth_50_coverage_pct": round(eligible50 / total * 100, 1) if total else None,
        "breadth_200_coverage_pct": round(eligible200 / total * 100, 1) if total else None,
        "breadth_constituents": total,
    }


def prior_index(prior, key):
    if not prior:
        return None
    return next((x for x in prior.get("indices", []) if x.get("key") == key), None)


def build_live_snapshot(prior=None):
    session = requests.Session()
    session.headers.update(HEADERS)
    packs = []
    crosschecks = []

    for cfg in INDICES:
        core = fetch_screener_core(session, cfg)
        check = fetch_indexpe_check(session, cfg)
        crosschecks.append((cfg, check))
        packs.append({"cfg": cfg, "core": core, "check": check})
        time.sleep(0.4)

    latest_dates = []
    for pack in packs:
        price_map = pack["core"]["price_map"]
        price_daily_map = pack["core"]["price_daily_map"]
        pe_map = pack["core"]["pe_map"]
        # Long price and P/E history can be weekly-aggregated by Screener, while
        # the 365-day price window is daily. Use the long history to establish
        # the valuation date, then consume the daily series on-or-before it.
        common = sorted(set(price_map) & set(pe_map))
        if not common:
            raise RuntimeError(f"No common Screener price/P-E date for {pack['cfg']['name']}")
        daily_latest = max(price_daily_map) if price_daily_map else None
        if daily_latest is None or daily_latest < common[-1] - timedelta(days=7):
            raise RuntimeError(f"Screener daily price series is stale for {pack['cfg']['name']}")
        latest_dates.append(common[-1])
    common_as_of = min(latest_dates)

    if common_as_of < date.today() - timedelta(days=10):
        raise RuntimeError(f"Screener core data is stale: latest common date {common_as_of.isoformat()}")

    five_year_start = common_as_of - timedelta(days=5 * 365)
    one_year_start = common_as_of - timedelta(days=365)
    results = []

    for pack in packs:
        cfg = pack["cfg"]
        core = pack["core"]
        summary = core["summary"]
        price_map = core["price_map"]
        price_daily_map = core["price_daily_map"]
        pe_map = core["pe_map"]
        eps_map = core["eps_map"]

        cur_pair = paired_on_or_before(price_map, pe_map, common_as_of)
        if not cur_pair:
            raise RuntimeError(f"Missing current paired price/P-E for {cfg['name']}")
        cur_date, cur_price, cur_pe = cur_pair

        # Use the long series for point-to-point 6M/1Y returns so the 365-day
        # daily API window does not fail at its exact boundary. The dedicated
        # daily series remains the source for volatility and drawdown.
        def price_at(delta_days):
            _, v = on_or_before(price_map, common_as_of - timedelta(days=delta_days))
            return v

        p6m = price_at(183)
        p1y = price_at(365)
        ret_6m = pct_change(cur_price, p6m)
        ret_1y = pct_change(cur_price, p1y)

        pe_values = [v for d, v in pe_map.items() if five_year_start <= d <= common_as_of and v and v > 0]
        if len(pe_values) < 100:
            raise RuntimeError(f"Too little 5Y P/E history for {cfg['name']}: {len(pe_values)} points")
        pe_median = statistics.median(pe_values)
        pe_pct = percentile(pe_values, cur_pe)

        # Prefer Screener's EPS series; if missing, derive EPS = Price / P-E on common dates.
        if eps_map:
            _, eps_now = on_or_before(eps_map, common_as_of)
            _, eps_1y = on_or_before(eps_map, common_as_of - timedelta(days=365))
            _, eps_3m = on_or_before(eps_map, common_as_of - timedelta(days=91))
            _, eps_15m = on_or_before(eps_map, common_as_of - timedelta(days=456))
        else:
            def derived_eps(target):
                pair = paired_on_or_before(price_map, pe_map, target)
                if not pair:
                    return None
                _, p, pe = pair
                return p / pe if pe else None
            eps_now = derived_eps(common_as_of)
            eps_1y = derived_eps(common_as_of - timedelta(days=365))
            eps_3m = derived_eps(common_as_of - timedelta(days=91))
            eps_15m = derived_eps(common_as_of - timedelta(days=456))

        earnings_growth = pct_change(eps_now, eps_1y)
        growth_3m_ago = pct_change(eps_3m, eps_15m)
        acceleration = None if earnings_growth is None or growth_3m_ago is None else earnings_growth - growth_3m_ago

        vol = annualized_volatility(price_daily_map, one_year_start, common_as_of)
        mdd = max_drawdown(price_daily_map, one_year_start, common_as_of)
        high_52w = max((p for d, p in price_daily_map.items() if one_year_start <= d <= common_as_of), default=summary.get("high_52w"))
        dd_high = cur_price / high_52w - 1.0 if high_52w else None

        current_pb = summary.get("pb")
        implied_roe = current_pb / cur_pe * 100 if current_pb and cur_pe else None
        quality_score = quality_roe_score(implied_roe)

        check = pack["check"]
        check_gap_pct = None
        if check.get("current_pe") and cur_pe:
            check_gap_pct = (check["current_pe"] / cur_pe - 1.0) * 100

        item = {
            "key": cfg["key"],
            "label": cfg["label"],
            "index_name": cfg["name"],
            "as_of": common_as_of.isoformat(),
            "close": round(cur_price, 2),
            "pe": round(cur_pe, 2),
            "pb": round(current_pb, 2) if current_pb is not None else None,
            "dividend_yield_pct": round(summary.get("dividend_yield"), 2) if summary.get("dividend_yield") is not None else None,
            "earnings_yield_pct": round(100 / cur_pe, 2) if cur_pe else None,
            "pe_5y_median": round(pe_median, 2),
            "pe_5y_percentile_pct": round(pe_pct * 100, 1) if pe_pct is not None else None,
            "pb_5y_median": None,
            "pb_5y_percentile_pct": None,
            "implied_roe_pct": round(implied_roe, 1) if implied_roe is not None else None,
            "implied_roe_5y_percentile_pct": None,
            "quality_score": round(quality_score, 1) if quality_score is not None else None,
            "ttm_earnings_index": round(eps_now, 4) if eps_now is not None else None,
            "ttm_earnings_growth_yoy_pct": round(earnings_growth * 100, 1) if earnings_growth is not None else None,
            "earnings_growth_3m_ago_yoy_pct": round(growth_3m_ago * 100, 1) if growth_3m_ago is not None else None,
            "earnings_acceleration_3m_pp": round(acceleration * 100, 1) if acceleration is not None else None,
            "price_return_6m_pct": round(ret_6m * 100, 1) if ret_6m is not None else None,
            "price_return_1y_pct": round(ret_1y * 100, 1) if ret_1y is not None else None,
            "relative_return_6m_vs_large_pp": None,
            "relative_return_1y_vs_large_pp": None,
            "valuation_premium_vs_large_pct": None,
            "valuation_premium_5y_percentile_pct": None,
            "volatility_1y_pct": round(vol * 100, 1) if vol is not None else None,
            "max_drawdown_1y_pct": round(mdd * 100, 1) if mdd is not None else None,
            "drawdown_from_52w_high_pct": round(dd_high * 100, 1) if dd_high is not None else None,
            "breadth_above_50dma_pct": None,
            "breadth_above_200dma_pct": None,
            "breadth_50_coverage_pct": None,
            "breadth_200_coverage_pct": None,
            "breadth_constituents": None,
            "breadth_source_status": "unavailable",
            "breadth_as_of": None,
            "valuation_crosscheck": {
                "status": check.get("status"),
                "source": "IndexPE",
                "current_pe": check.get("current_pe"),
                "median_5y": check.get("median_5y"),
                "as_of": check.get("as_of"),
                "gap_vs_screener_pct": round(check_gap_pct, 1) if check_gap_pct is not None else None,
            },
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

    # Relative valuation and momentum.
    result_by_key = {x["key"]: x for x in results}
    pack_by_key = {x["cfg"]["key"]: x for x in packs}
    large = result_by_key["large"]
    large_pe_map = pack_by_key["large"]["core"]["pe_map"]

    for item in results:
        if item["key"] == "large":
            item["relative_return_6m_vs_large_pp"] = 0.0
            item["relative_return_1y_vs_large_pp"] = 0.0
            continue
        item["relative_return_6m_vs_large_pp"] = round(item["price_return_6m_pct"] - large["price_return_6m_pct"], 1)
        item["relative_return_1y_vs_large_pp"] = round(item["price_return_1y_pct"] - large["price_return_1y_pct"], 1)
        item["valuation_premium_vs_large_pct"] = round((item["pe"] / large["pe"] - 1) * 100, 1)

        this_pe_map = pack_by_key[item["key"]]["core"]["pe_map"]
        premiums = []
        for dt in sorted(set(this_pe_map) & set(large_pe_map)):
            if dt < five_year_start or dt > common_as_of:
                continue
            a, b = this_pe_map.get(dt), large_pe_map.get(dt)
            if a and b and a > 0 and b > 0:
                premiums.append(a / b - 1)
        current_premium = item["pe"] / large["pe"] - 1
        p = percentile(premiums, current_premium)
        item["valuation_premium_5y_percentile_pct"] = round(p * 100, 1) if p is not None else None

    # Optional constituent breadth. Core snapshot stays usable if breadth fails.
    breadth_messages = []
    breadth_live = 0
    for cfg in INDICES:
        item = result_by_key[cfg["key"]]
        try:
            symbols, universe_source = fetch_constituents(session, cfg)
            breadth = compute_breadth(symbols)
            item.update(breadth)
            item["breadth_source_status"] = "live"
            item["breadth_as_of"] = common_as_of.isoformat()
            item["breadth_universe_source"] = universe_source
            breadth_live += 1
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

    # Scores.
    for item in results:
        valuation_component = None if item["pe_5y_percentile_pct"] is None else 100 - item["pe_5y_percentile_pct"]
        if item["key"] == "large":
            relative_valuation_component = 50.0
        elif item["valuation_premium_5y_percentile_pct"] is not None:
            relative_valuation_component = 100 - item["valuation_premium_5y_percentile_pct"]
        else:
            relative_valuation_component = None

        g = growth_score(item["ttm_earnings_growth_yoy_pct"])
        a = acceleration_score(item["earnings_acceleration_3m_pp"])
        earnings_component, _ = weighted_average({"growth": g, "acceleration": a}, {"growth": 65, "acceleration": 35})
        breadth_component, _ = weighted_average(
            {"dma50": item["breadth_above_50dma_pct"], "dma200": item["breadth_above_200dma_pct"]},
            {"dma50": 40, "dma200": 60},
        )
        quality_component = item.get("quality_score")

        if item["key"] == "large":
            momentum_component = 50.0
        else:
            m6 = clamp(50 + 2.5 * item["relative_return_6m_vs_large_pp"]) if item["relative_return_6m_vs_large_pp"] is not None else None
            m12 = clamp(50 + 2.5 * item["relative_return_1y_vs_large_pp"]) if item["relative_return_1y_vs_large_pp"] is not None else None
            momentum_component, _ = weighted_average({"m6": m6, "m12": m12}, {"m6": 50, "m12": 50})

        opp_parts = {
            "valuation": valuation_component,
            "relative_valuation": relative_valuation_component,
            "earnings": earnings_component,
            "breadth": breadth_component,
            "quality": quality_component,
            "momentum": momentum_component,
        }
        opp, opp_cov = weighted_average(opp_parts, OPPORTUNITY_WEIGHTS)
        item["opportunity_components"] = {k: round(v, 1) if v is not None else None for k, v in opp_parts.items()}
        item["opportunity_score"] = round(opp, 1) if opp is not None else None
        item["opportunity_coverage_pct"] = round(opp_cov, 1)
        item["opportunity_label"] = opportunity_label(opp)

        valuation_risk = item["pe_5y_percentile_pct"]
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
        risk, risk_cov = weighted_average(risk_parts, RISK_WEIGHTS)
        item["risk_components"] = {k: round(v, 1) if v is not None else None for k, v in risk_parts.items()}
        item["risk_score"] = round(risk, 1) if risk is not None else None
        item["risk_coverage_pct"] = round(risk_cov, 1)
        item["risk_label"] = risk_label(risk)

    crosscheck_warnings = []
    check_live = 0
    for item in results:
        c = item.get("valuation_crosscheck") or {}
        if c.get("status") == "live":
            check_live += 1
            gap = c.get("gap_vs_screener_pct")
            if gap is not None and abs(gap) > 7:
                crosscheck_warnings.append(f"{item['label']} P/E differs {gap:+.1f}% between Screener and IndexPE")

    status = "live" if breadth_live == len(INDICES) and check_live == len(INDICES) else "partial"
    source_message = "Core valuation, earnings and price-history refresh succeeded from Screener."
    if check_live:
        source_message += f" IndexPE cross-check live for {check_live}/3 segments."
    if breadth_live == len(INDICES):
        source_message += " Breadth refreshed for all segments."
    else:
        source_message += f" Breadth live for {breadth_live}/3 segments; cached/unavailable values are labelled."
    if crosscheck_warnings:
        source_message += " Cross-check warning: " + "; ".join(crosscheck_warnings) + "."

    results = sorted(results, key=lambda x: ["large", "mid", "small"].index(x["key"]))
    ranked = sorted(
        [x for x in results if x.get("opportunity_score") is not None],
        key=lambda x: (-x["opportunity_score"], x.get("risk_score") if x.get("risk_score") is not None else 999),
    )

    return {
        "as_of": common_as_of.isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "methodology_version": "2.4",
        "source_status": {
            "status": status,
            "used_cached_data": breadth_live != len(INDICES),
            "message": source_message,
            "components": {
                "core_index_data": {"status": "live", "message": "Screener index pages + historical chart API refreshed price, P/E and EPS series."},
                "valuation_crosscheck": {"status": "live" if check_live == len(INDICES) else "partial", "message": f"IndexPE cross-check live for {check_live}/3 segments."},
                "breadth": {"status": "live" if breadth_live == len(INDICES) else ("partial" if breadth_live else "cached_or_unavailable"), "message": "; ".join(breadth_messages) if breadth_messages else "All three breadth universes refreshed."},
            },
        },
        "sources": [
            {"name": "Screener.in index pages", "url": "https://www.screener.in/company/NIFTY/", "purpose": "Current P/E, P/B, dividend yield and index page identifiers"},
            {"name": "Screener.in chart API", "url": "https://www.screener.in/", "purpose": "Historical index price, P/E and EPS series used for percentiles, earnings, momentum, volatility and drawdown"},
            {"name": "IndexPE", "url": "https://indexpe.in/", "purpose": "Independent current P/E and 5-year median valuation cross-check"},
            {"name": "Nifty Indices constituent files", "url": "https://www.niftyindices.com/indices/equity/broad-based-indices", "purpose": "Primary breadth universe; Screener constituent pages are the fallback"},
            {"name": "Yahoo Finance via yfinance", "url": "https://finance.yahoo.com/", "purpose": "Constituent daily prices used only for breadth"},
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
                "message": "Core live refresh failed; showing last good data. Error: " + str(exc),
                "components": {
                    "core_index_data": {"status": "cached", "message": str(exc)},
                    "valuation_crosscheck": {"status": "cached", "message": "Preserved with prior snapshot."},
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
