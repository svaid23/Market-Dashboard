import csv
import io
import json
import math
import re
import statistics
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
LATEST_FILE = DATA_DIR / "latest.json"
HISTORY_FILE = DATA_DIR / "history.json"

SCREENER = "https://www.screener.in"
INDEXPE = "https://indexpe.in"

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
        "expected_constituents": 50,
        "max_pages": 2,
    },
    {
        "key": "mid",
        "label": "Mid Cap",
        "name": "NIFTY MIDCAP 100",
        "screener_slug": "CNXMIDCAP",
        "indexpe_slug": "nifty-midcap-100",
        "expected_constituents": 100,
        "max_pages": 4,
    },
    {
        "key": "small",
        "label": "Small Cap",
        "name": "NIFTY SMALLCAP 250",
        "screener_slug": "SMALLCA250",
        "indexpe_slug": "nifty-smallcap-250",
        "expected_constituents": 250,
        "max_pages": 10,
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
    "beta": 15,
    "breadth_fragility": 10,
    "earnings_deterioration": 15,
}

# Slow-moving reference anchors. These are used only if IndexPE is temporarily
# unavailable. They were verified from the public IndexPE pages on 10 Aug 2026.
# The live Screener page remains the primary source for current market data.
REFERENCE_ANCHORS = {
    "large": {"as_of": "2026-08-10", "pe_5y_median": 22.07, "volatility_1y_pct": 13.34, "beta_1y": 1.00},
    "mid": {"as_of": "2026-08-10", "pe_5y_median": 29.52, "volatility_1y_pct": 16.76, "beta_1y": 1.10},
    "small": {"as_of": "2026-08-10", "pe_5y_median": 28.30, "volatility_1y_pct": 17.35, "beta_1y": 1.07},
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
    return linear_score(growth_pct, [(-15, 0), (-5, 20), (0, 35), (8, 55), (15, 75), (25, 100)])


def quality_roe_score(roe_pct):
    return linear_score(roe_pct, [(6, 10), (10, 30), (14, 50), (18, 70), (24, 90), (30, 100)])


def volatility_risk_score(vol_pct):
    return linear_score(vol_pct, [(10, 0), (15, 20), (20, 45), (25, 70), (30, 88), (35, 100)])


def beta_risk_score(beta):
    return linear_score(beta, [(0.75, 5), (0.9, 20), (1.0, 35), (1.1, 55), (1.2, 75), (1.4, 100)])


def valuation_opportunity_score(vs_median_pct):
    return linear_score(vs_median_pct, [(-30, 100), (-20, 95), (-10, 75), (0, 50), (10, 30), (20, 10), (30, 0)])


def relative_value_score(excess_premium_pp):
    return linear_score(excess_premium_pp, [(-30, 100), (-15, 80), (0, 50), (15, 20), (30, 0)])


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
            if "text/html" not in (r.headers.get("content-type") or "").lower() and not r.text.lstrip().startswith("<"):
                # Both core sources are normal public HTML pages. Refuse surprise binary/JSON pages.
                raise RuntimeError(f"Unexpected content type from {r.url}: {r.headers.get('content-type')}")
            return r
        except Exception as exc:
            last = exc
            time.sleep(1.0 * (attempt + 1))
    raise RuntimeError(f"GET failed for {url}: {last}")


def text_from_html(html):
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


def parse_date_text(value):
    value = re.sub(r"\s+", " ", value.strip())
    for fmt in ("%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except Exception:
            pass
    return None


def parse_screener_as_of(text):
    m = re.search(r"(\d{1,2}\s+[A-Za-z]{3})\s*-\s*close price", text, flags=re.I)
    if not m:
        return None
    partial = m.group(1)
    today = date.today()
    try:
        d = datetime.strptime(f"{partial} {today.year}", "%d %b %Y").date()
    except Exception:
        return None
    if d > today + timedelta(days=3):
        d = d.replace(year=today.year - 1)
    return d


def parse_screener_summary(html):
    _, text = text_from_html(html)
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
    if current is None or pe is None:
        raise RuntimeError("Screener summary missing Current Price or P/E")
    return {
        "as_of": parse_screener_as_of(text),
        "current": current,
        "pe": pe,
        "pb": pb,
        "dividend_yield": dy,
        "cagr_1y": cagr_1y,
        "cagr_5y": cagr_5y,
        "high_52w": high,
        "low_52w": low,
    }


def parse_indexpe(html):
    _, text = text_from_html(html)
    current_pe = regex_num(text, [r"Current PE Ratio\s*([\d,.]+)"])
    median_5y = regex_num(text, [r"5Y Median PE\s*([\d,.]+)"])
    vol = regex_num(text, [r"Volatility \(1Y std dev\)\s*([+\-]?[\d,.]+)\s*%"])
    beta = regex_num(text, [r"Beta vs Nifty 50 \(1Y\)\s*([\d,.]+)"])
    total_return_1y = regex_num(text, [r"1Y total return\s*([+\-]?[\d,.]+)\s*%"])
    total_return_5y = regex_num(text, [r"5Y total return \(p\.a\.\)\s*([+\-]?[\d,.]+)\s*%"])
    earnings_long_cagr = regex_num(text, [r"earnings grew\s*([+\-]?[\d,.]+)%\s*a year over the last\s*(?:5|3|1)\s*years?"])
    updated = None
    m = re.search(r"Last updated:\s*(\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4})", text, flags=re.I)
    if m:
        updated = parse_date_text(m.group(1))

    pe_percentile = None
    # Only accept a sentence explicitly about PE. Some pages render PB as the default percentile metric.
    m = re.search(r"(?:'s|’s)\s+PE\s+is\s+(cheaper|more expensive)\s+than\s+([\d.]+)%\s+of the last\s+5 years", text, flags=re.I)
    if m:
        pct = safe_float(m.group(2))
        if pct is not None:
            pe_percentile = 100 - pct if m.group(1).lower().startswith("cheaper") else pct

    if current_pe is None:
        raise RuntimeError("IndexPE page missing Current PE Ratio")
    return {
        "as_of": updated,
        "pe": current_pe,
        "pe_5y_median": median_5y,
        "pe_5y_percentile_pct": pe_percentile,
        "volatility_1y_pct": vol,
        "beta_1y": beta,
        "total_return_1y_pct": total_return_1y,
        "total_return_5y_cagr_pct": total_return_5y,
        "long_term_earnings_cagr_pct": earnings_long_cagr,
    }


def normalize_header(s):
    return re.sub(r"\s+", " ", s.replace("%", "%").strip()).lower()


def parse_constituent_table(html):
    soup = BeautifulSoup(html, "html.parser")
    best = None
    for table in soup.find_all("table"):
        headers = [normalize_header(th.get_text(" ", strip=True)) for th in table.find_all("th")]
        joined = " | ".join(headers)
        if "mar cap" in joined and "qtr profit var" in joined:
            best = (table, headers)
            break
    if not best:
        # Screener sometimes uses td in the header row.
        for table in soup.find_all("table"):
            first = table.find("tr")
            if not first:
                continue
            headers = [normalize_header(c.get_text(" ", strip=True)) for c in first.find_all(["th", "td"])]
            joined = " | ".join(headers)
            if "mar cap" in joined and "qtr profit var" in joined:
                best = (table, headers)
                break
    if not best:
        raise RuntimeError("Screener constituent table not found")

    table, headers = best
    def idx_contains(needle):
        for i, h in enumerate(headers):
            if needle in h:
                return i
        return None

    i_name = idx_contains("name") or idx_contains("company")
    i_mcap = idx_contains("mar cap")
    i_np = idx_contains("np qtr")
    i_growth = idx_contains("qtr profit var")
    i_roce = idx_contains("roce")
    if i_mcap is None or i_growth is None:
        raise RuntimeError("Required Screener constituent columns not found")

    rows = []
    for tr in table.find_all("tr"):
        cells = tr.find_all("td")
        if not cells or len(cells) < len(headers):
            continue
        symbol = None
        link = tr.find("a", href=re.compile(r"^/company/[^/]+/"))
        if link:
            m = re.match(r"/company/([^/]+)/", link.get("href", ""))
            if m:
                symbol = m.group(1).strip().upper()
        name = cells[i_name].get_text(" ", strip=True) if i_name is not None and i_name < len(cells) else (link.get_text(" ", strip=True) if link else None)
        mcap = safe_float(cells[i_mcap].get_text(" ", strip=True)) if i_mcap < len(cells) else None
        np_qtr = safe_float(cells[i_np].get_text(" ", strip=True)) if i_np is not None and i_np < len(cells) else None
        growth = safe_float(cells[i_growth].get_text(" ", strip=True)) if i_growth < len(cells) else None
        roce = safe_float(cells[i_roce].get_text(" ", strip=True)) if i_roce is not None and i_roce < len(cells) else None
        if symbol or name:
            rows.append({"symbol": symbol, "name": name, "mcap": mcap, "np_qtr": np_qtr, "profit_growth": growth, "roce": roce})
    return rows


def fetch_screener_bundle(session, cfg):
    """Fetch the public index page plus constituent table.

    The summary is core. Constituents are optional: a pagination/table change must
    not take down current P/E/P/B/return data for the entire dashboard.
    """
    all_rows = []
    seen = set()
    summary = None
    constituent_error = None
    for page in range(1, cfg["max_pages"] + 1):
        url = f"{SCREENER}/company/{cfg['screener_slug']}/"
        try:
            r = http_get(session, url, params={"page": page, "sort": "name", "order": "asc"}, timeout=35)
        except Exception as exc:
            if page == 1:
                raise
            constituent_error = f"page {page} fetch failed: {exc}"
            break
        if page == 1:
            summary = parse_screener_summary(r.text)
        try:
            rows = parse_constituent_table(r.text)
        except Exception as exc:
            constituent_error = f"page {page} table parse failed: {exc}"
            break
        added = 0
        for row in rows:
            key = row.get("symbol") or row.get("name")
            if key and key not in seen:
                seen.add(key)
                all_rows.append(row)
                added += 1
        if added == 0 or len(all_rows) >= cfg["expected_constituents"]:
            break
        time.sleep(0.2)
    if summary is None:
        raise RuntimeError("Screener summary unavailable")
    minimum = max(20, int(cfg["expected_constituents"] * 0.70))
    if len(all_rows) < minimum:
        constituent_error = constituent_error or f"constituent coverage too low: {len(all_rows)}/{cfg['expected_constituents']}"
        # Do not use a thin/non-representative subset for the profit pulse or breadth.
        usable_rows = []
    else:
        usable_rows = all_rows
    return {
        "summary": summary,
        "rows": usable_rows,
        "raw_row_count": len(all_rows),
        "constituent_error": constituent_error,
    }


def fetch_indexpe(session, cfg):
    url = f"{INDEXPE}/{cfg['indexpe_slug']}"
    r = http_get(session, url, timeout=30)
    return parse_indexpe(r.text)


def winsorized_weighted_growth(rows):
    valid = []
    for r in rows:
        mcap = r.get("mcap")
        g = r.get("profit_growth")
        if mcap is None or mcap <= 0 or g is None or not math.isfinite(g):
            continue
        # Percentage changes from tiny/negative bases can explode. Cap their influence.
        clipped = max(-100.0, min(200.0, float(g)))
        valid.append((float(mcap), clipped, float(g)))
    if not valid:
        return None, None, None, 0.0
    total_w = sum(w for w, _, _ in valid)
    weighted = sum(w * g for w, g, _ in valid) / total_w
    positive_mcap = sum(w for w, _, raw in valid if raw > 0) / total_w * 100
    positive_count = sum(1 for _, _, raw in valid if raw > 0) / len(valid) * 100
    return weighted, positive_mcap, positive_count, len(valid)


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

    # Yahoo uses .NS for NSE cash-market securities. Invalid/special tickers are tolerated via coverage checks.
    yf_tickers = [f"{s}.NS" for s in symbols if s]
    series_map = {}
    for i in range(0, len(yf_tickers), 50):
        batch = yf_tickers[i : i + 50]
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

    total = len(yf_tickers)
    if eligible50 < max(10, int(total * 0.45)):
        raise RuntimeError(f"Yahoo breadth coverage too low ({eligible50}/{total})")
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


def copy_prior_fields(item, prior_item, fields):
    if not prior_item:
        return
    for f in fields:
        if item.get(f) is None and prior_item.get(f) is not None:
            item[f] = prior_item.get(f)


def build_snapshot(prior=None):
    session = requests.Session()
    session.headers.update(HEADERS)
    results = []
    component_notes = []
    fresh_dates = []
    source_live_counts = {"indexpe": 0, "screener": 0, "breadth": 0}

    for cfg in INDICES:
        old = prior_index(prior, cfg["key"])
        indexpe = None
        screener = None
        indexpe_error = None
        screener_error = None
        try:
            indexpe = fetch_indexpe(session, cfg)
            source_live_counts["indexpe"] += 1
            if indexpe.get("as_of"):
                fresh_dates.append(indexpe["as_of"])
        except Exception as exc:
            indexpe_error = str(exc)

        try:
            screener = fetch_screener_bundle(session, cfg)
            source_live_counts["screener"] += 1
            if screener["summary"].get("as_of"):
                fresh_dates.append(screener["summary"]["as_of"])
        except Exception as exc:
            screener_error = str(exc)

        if not indexpe and not screener:
            if old:
                cached = dict(old)
                cached["source_segment_status"] = "cached"
                cached["source_segment_message"] = f"IndexPE failed: {indexpe_error}; Screener failed: {screener_error}"
                results.append(cached)
                component_notes.append(f"{cfg['label']}: cached; both core sources unavailable")
                continue
            raise RuntimeError(f"{cfg['label']} has no live core source. IndexPE: {indexpe_error}; Screener: {screener_error}")

        summary = screener["summary"] if screener else {}
        rows = screener["rows"] if screener else []
        anchor = REFERENCE_ANCHORS[cfg["key"]]
        # Use Screener for *current* P/E because its public index page is the
        # live market page. IndexPE is a slower-moving historical/risk anchor.
        pe = summary.get("pe") if summary else (indexpe.get("pe") if indexpe else None)
        pe5 = indexpe.get("pe_5y_median") if indexpe and indexpe.get("pe_5y_median") is not None else (old.get("pe_5y_median") if old and old.get("pe_5y_median") is not None else anchor["pe_5y_median"])
        pb = summary.get("pb") if summary else (old.get("pb") if old else None)
        dy = summary.get("dividend_yield") if summary else (old.get("dividend_yield_pct") if old else None)
        close = summary.get("current") if summary else (old.get("close") if old else None)
        if pe is None:
            pe = old.get("pe") if old else None
        if pe is None:
            if old and old.get("pe") is not None:
                pe = old.get("pe")
            else:
                raise RuntimeError(f"{cfg['label']} missing current P/E")

        pe_vs = (pe / pe5 - 1.0) * 100 if pe5 else None
        earnings_yield = 100 / pe if pe else None
        implied_roe = pb / pe * 100 if pb and pe else None
        quality_score = quality_roe_score(implied_roe)

        weighted_growth = positive_mcap = positive_count = None
        earnings_n = 0
        symbols = []
        if rows:
            weighted_growth, positive_mcap, positive_count, earnings_n = winsorized_weighted_growth(rows)
            symbols = [r.get("symbol") for r in rows if r.get("symbol")]

        item = {
            "key": cfg["key"],
            "label": cfg["label"],
            "index_name": cfg["name"],
            "as_of": (summary.get("as_of").isoformat() if summary and summary.get("as_of") else (indexpe.get("as_of").isoformat() if indexpe and indexpe.get("as_of") else (old.get("as_of") if old else None))),
            "close": round(close, 2) if close is not None else None,
            "pe": round(pe, 2) if pe is not None else None,
            "pb": round(pb, 2) if pb is not None else None,
            "dividend_yield_pct": round(dy, 2) if dy is not None else None,
            "earnings_yield_pct": round(earnings_yield, 2) if earnings_yield is not None else None,
            "pe_5y_median": round(pe5, 2) if pe5 is not None else None,
            "pe_vs_5y_median_pct": round(pe_vs, 1) if pe_vs is not None else None,
            "pe_5y_percentile_pct": round(indexpe.get("pe_5y_percentile_pct"), 1) if indexpe and indexpe.get("pe_5y_percentile_pct") is not None else None,
            "implied_roe_pct": round(implied_roe, 1) if implied_roe is not None else None,
            "quality_score": round(quality_score, 1) if quality_score is not None else None,
            "profit_growth_weighted_pct": round(weighted_growth, 1) if weighted_growth is not None else None,
            "profit_positive_mcap_breadth_pct": round(positive_mcap, 1) if positive_mcap is not None else None,
            "profit_positive_constituent_pct": round(positive_count, 1) if positive_count is not None else None,
            "earnings_constituent_coverage_pct": round(earnings_n / max(1, cfg["expected_constituents"]) * 100, 1) if earnings_n else None,
            "long_term_earnings_cagr_pct": round(indexpe.get("long_term_earnings_cagr_pct"), 1) if indexpe and indexpe.get("long_term_earnings_cagr_pct") is not None else None,
            "price_return_1y_pct": round(summary.get("cagr_1y"), 1) if summary and summary.get("cagr_1y") is not None else (round(indexpe.get("total_return_1y_pct"), 1) if indexpe and indexpe.get("total_return_1y_pct") is not None else None),
            "price_return_5y_cagr_pct": round(summary.get("cagr_5y"), 1) if summary and summary.get("cagr_5y") is not None else None,
            "relative_return_1y_vs_large_pp": None,
            "valuation_premium_vs_large_pct": None,
            "normal_5y_premium_vs_large_pct": None,
            "excess_premium_vs_normal_pp": None,
            "volatility_1y_pct": round(indexpe.get("volatility_1y_pct"), 1) if indexpe and indexpe.get("volatility_1y_pct") is not None else (old.get("volatility_1y_pct") if old and old.get("volatility_1y_pct") is not None else anchor["volatility_1y_pct"]),
            "beta_1y": round(indexpe.get("beta_1y"), 2) if indexpe and indexpe.get("beta_1y") is not None else (old.get("beta_1y") if old and old.get("beta_1y") is not None else anchor["beta_1y"]),
            "drawdown_from_52w_high_pct": round((close / summary.get("high_52w") - 1) * 100, 1) if close and summary and summary.get("high_52w") else (old.get("drawdown_from_52w_high_pct") if old else None),
            "breadth_above_50dma_pct": None,
            "breadth_above_200dma_pct": None,
            "breadth_50_coverage_pct": None,
            "breadth_200_coverage_pct": None,
            "breadth_constituents": None,
            "breadth_source_status": "unavailable",
            "breadth_as_of": None,
            "source_segment_status": "live" if indexpe and screener else "partial",
            "source_segment_message": "IndexPE + Screener live" if indexpe and screener else (f"IndexPE live; Screener unavailable: {screener_error}" if indexpe else f"Screener live; IndexPE unavailable: {indexpe_error}"),
            "historical_anchor_status": "live" if indexpe and indexpe.get("pe_5y_median") is not None else ("cached" if old and old.get("pe_5y_median") is not None else "reference"),
            "historical_anchor_as_of": indexpe.get("as_of").isoformat() if indexpe and indexpe.get("as_of") else (old.get("historical_anchor_as_of") if old and old.get("historical_anchor_as_of") else anchor["as_of"]),
            "constituent_table_status": ("live" if screener and screener.get("rows") else ("partial" if screener else "unavailable")),
            "constituent_table_message": (screener.get("constituent_error") if screener else screener_error),
            "constituent_rows_seen": (screener.get("raw_row_count") if screener else 0),
            "screener_pe_crosscheck": round(summary.get("pe"), 2) if summary and summary.get("pe") is not None else None,
            "indexpe_total_return_1y_pct": round(indexpe.get("total_return_1y_pct"), 1) if indexpe and indexpe.get("total_return_1y_pct") is not None else None,
            "indexpe_total_return_5y_cagr_pct": round(indexpe.get("total_return_5y_cagr_pct"), 1) if indexpe and indexpe.get("total_return_5y_cagr_pct") is not None else None,
            "opportunity_components": {},
            "opportunity_score": None,
            "opportunity_coverage_pct": None,
            "opportunity_label": None,
            "risk_components": {},
            "risk_score": None,
            "risk_coverage_pct": None,
            "risk_label": None,
            "_symbols": symbols,
        }

        # Fill optional metrics from prior snapshot if one live source is missing.
        copy_prior_fields(item, old, [
            "pb", "dividend_yield_pct", "implied_roe_pct", "quality_score",
            "profit_growth_weighted_pct", "profit_positive_mcap_breadth_pct",
            "profit_positive_constituent_pct", "earnings_constituent_coverage_pct",
            "volatility_1y_pct", "beta_1y", "drawdown_from_52w_high_pct",
        ])
        results.append(item)
        if item["source_segment_status"] == "partial":
            component_notes.append(f"{cfg['label']}: {item['source_segment_message']}")
        if item.get("constituent_table_status") == "partial":
            component_notes.append(f"{cfg['label']} constituent table partial: {item.get('constituent_table_message')}")

    result_by_key = {x["key"]: x for x in results}
    large = result_by_key["large"]

    # Relative valuation and momentum use current P/E and each segment's own 5Y median P/E.
    for item in results:
        if item["key"] == "large":
            item["valuation_premium_vs_large_pct"] = 0.0
            item["normal_5y_premium_vs_large_pct"] = 0.0
            item["excess_premium_vs_normal_pp"] = 0.0
            item["relative_return_1y_vs_large_pp"] = 0.0
            continue
        if item.get("pe") and large.get("pe"):
            item["valuation_premium_vs_large_pct"] = round((item["pe"] / large["pe"] - 1) * 100, 1)
        if item.get("pe_5y_median") and large.get("pe_5y_median"):
            item["normal_5y_premium_vs_large_pct"] = round((item["pe_5y_median"] / large["pe_5y_median"] - 1) * 100, 1)
        if item.get("valuation_premium_vs_large_pct") is not None and item.get("normal_5y_premium_vs_large_pct") is not None:
            item["excess_premium_vs_normal_pp"] = round(item["valuation_premium_vs_large_pct"] - item["normal_5y_premium_vs_large_pct"], 1)
        if item.get("price_return_1y_pct") is not None and large.get("price_return_1y_pct") is not None:
            item["relative_return_1y_vs_large_pp"] = round(item["price_return_1y_pct"] - large["price_return_1y_pct"], 1)

    # Breadth is optional. It uses only the public constituent symbols scraped from Screener and Yahoo daily prices.
    for item in results:
        old = prior_index(prior, item["key"])
        symbols = item.pop("_symbols", [])
        if symbols:
            try:
                breadth = compute_breadth(symbols)
                item.update(breadth)
                item["breadth_source_status"] = "live"
                item["breadth_as_of"] = item.get("as_of")
                source_live_counts["breadth"] += 1
            except Exception as exc:
                if old and old.get("breadth_above_50dma_pct") is not None:
                    for field in ["breadth_above_50dma_pct", "breadth_above_200dma_pct", "breadth_50_coverage_pct", "breadth_200_coverage_pct", "breadth_constituents"]:
                        item[field] = old.get(field)
                    item["breadth_source_status"] = "cached"
                    item["breadth_as_of"] = old.get("breadth_as_of") or old.get("as_of")
                    component_notes.append(f"{item['label']} breadth cached: {exc}")
                else:
                    component_notes.append(f"{item['label']} breadth unavailable: {exc}")

    # Scores.
    for item in results:
        val_opp = valuation_opportunity_score(item.get("pe_vs_5y_median_pct"))
        rel_opp = 50.0 if item["key"] == "large" else relative_value_score(item.get("excess_premium_vs_normal_pp"))
        g = growth_score(item.get("profit_growth_weighted_pct"))
        e_breadth = item.get("profit_positive_mcap_breadth_pct")
        earnings_component, _ = weighted_average({"growth": g, "profit_breadth": e_breadth}, {"growth": 70, "profit_breadth": 30})
        breadth_component, _ = weighted_average(
            {"dma50": item.get("breadth_above_50dma_pct"), "dma200": item.get("breadth_above_200dma_pct")},
            {"dma50": 40, "dma200": 60},
        )
        quality_component = item.get("quality_score")
        if item["key"] == "large":
            momentum_component = 50.0
        else:
            rr = item.get("relative_return_1y_vs_large_pp")
            momentum_component = clamp(50 + 2.5 * rr) if rr is not None else None

        opp_parts = {
            "valuation": val_opp,
            "relative_valuation": rel_opp,
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

        valuation_risk = None if val_opp is None else 100 - val_opp
        relative_risk = None if item["key"] == "large" else (None if rel_opp is None else 100 - rel_opp)
        vol_risk = volatility_risk_score(item.get("volatility_1y_pct"))
        beta_risk = beta_risk_score(item.get("beta_1y"))
        breadth_fragility = None if breadth_component is None else 100 - breadth_component
        earnings_deterioration = None if earnings_component is None else 100 - earnings_component
        risk_parts = {
            "valuation": valuation_risk,
            "relative_premium": relative_risk,
            "volatility": vol_risk,
            "beta": beta_risk,
            "breadth_fragility": breadth_fragility,
            "earnings_deterioration": earnings_deterioration,
        }
        risk, risk_cov = weighted_average(risk_parts, RISK_WEIGHTS)
        item["risk_components"] = {k: round(v, 1) if v is not None else None for k, v in risk_parts.items()}
        item["risk_score"] = round(risk, 1) if risk is not None else None
        item["risk_coverage_pct"] = round(risk_cov, 1)
        item["risk_label"] = risk_label(risk)

    # Overall health. Screener public HTML is the primary *current* market source.
    # IndexPE is intentionally treated as a slow-moving historical/risk anchor, so
    # its older update date does not make otherwise-current market data stale.
    fresh_core_segments = sum(1 for x in results if x.get("source_segment_status") in {"live", "partial"})
    if fresh_core_segments == 0:
        status = "cached"
    elif source_live_counts["screener"] == 3 and source_live_counts["breadth"] == 3:
        status = "live" if source_live_counts["indexpe"] == 3 else "partial"
    else:
        status = "partial"

    screener_dates = []
    for x in results:
        if x.get("source_segment_status") in {"live", "partial"} and x.get("as_of"):
            try:
                screener_dates.append(datetime.strptime(x["as_of"], "%Y-%m-%d").date())
            except Exception:
                pass
    as_of = min(screener_dates).isoformat() if screener_dates else (prior.get("as_of") if prior else None)
    if screener_dates and min(screener_dates) < date.today() - timedelta(days=10):
        status = "partial"
        component_notes.append(f"Oldest current-market source date is {min(screener_dates).isoformat()}")

    ranked = sorted(
        [x for x in results if x.get("opportunity_score") is not None],
        key=lambda x: (-x["opportunity_score"], x.get("risk_score") if x.get("risk_score") is not None else 999),
    )

    msg = f"IndexPE live for {source_live_counts['indexpe']}/3; Screener live for {source_live_counts['screener']}/3; breadth live for {source_live_counts['breadth']}/3."
    if component_notes:
        msg += " " + " | ".join(component_notes[:8])

    return {
        "as_of": as_of,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "methodology_version": "3.0",
        "source_status": {
            "status": status,
            "used_cached_data": status != "live",
            "message": msg,
            "components": {
                "historical_risk_anchor": {"status": "live" if source_live_counts["indexpe"] == 3 else "reference", "message": f"IndexPE public HTML live for {source_live_counts['indexpe']}/3 segments; vetted Aug-2026 reference anchors are used if unavailable."},
                "market_fundamentals": {"status": "live" if source_live_counts["screener"] == 3 else "partial", "message": f"Screener public index/constituent HTML pages live for {source_live_counts['screener']}/3 segments."},
                "breadth": {"status": "live" if source_live_counts["breadth"] == 3 else "partial", "message": f"Yahoo breadth live for {source_live_counts['breadth']}/3 segments; cached/unavailable values are labelled."},
            },
        },
        "sources": [
            {"name": "Screener.in", "url": "https://www.screener.in/company/NIFTY/", "purpose": "Primary live source: current index P/E/P/B, dividend yield, 52W range, returns and public constituent profit-growth tables"},
            {"name": "IndexPE", "url": "https://indexpe.in/", "purpose": "Slow-moving 5Y median P/E and 1Y volatility/beta anchor; vetted reference values are used if the site is temporarily unavailable"},
            {"name": "Yahoo Finance via yfinance", "url": "https://finance.yahoo.com/", "purpose": "Optional constituent daily prices for 50DMA/200DMA market breadth only"},
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
        "profit_growth": {x["key"]: x.get("profit_growth_weighted_pct") for x in snapshot.get("indices", [])},
        "breadth_200dma": {x["key"]: x.get("breadth_above_200dma_pct") for x in snapshot.get("indices", [])},
    }
    snapshots = [x for x in snapshots if x.get("as_of") != summary["as_of"] or x.get("methodology_version") != summary["methodology_version"]]
    snapshots.append(summary)
    snapshots = sorted(snapshots, key=lambda x: (x.get("as_of") or "", x.get("generated_at") or ""))[-72:]
    save_json(HISTORY_FILE, {"snapshots": snapshots})


def self_test():
    screener_html = """
    <html><body><div>01 Oct - close price</div><ul>
    <li>Current Price ₹ 22,422</li><li>P/E 19.2</li><li>Price to Book value 2.75</li>
    <li>Dividend Yield 1.23 %</li><li>High / Low ₹ 26,373 / 22,183</li>
    <li>CAGR 1Yr -8.87 %</li><li>CAGR 5Yr 5.04 %</li></ul>
    <table><tr><th>S.No.</th><th>Name</th><th>CMP Rs.</th><th>P/E</th><th>Mar Cap Rs.Cr.</th><th>Div Yld %</th><th>NP Qtr Rs.Cr.</th><th>Qtr Profit Var %</th><th>Sales Qtr Rs.Cr.</th><th>Qtr Sales Var %</th><th>ROCE %</th></tr>
    <tr><td>1</td><td><a href='/company/AAA/'>AAA Ltd</a></td><td>100</td><td>20</td><td>1000</td><td>1</td><td>100</td><td>10</td><td>500</td><td>5</td><td>15</td></tr>
    <tr><td>2</td><td><a href='/company/BBB/'>BBB Ltd</a></td><td>200</td><td>25</td><td>500</td><td>0</td><td>50</td><td>-5</td><td>300</td><td>2</td><td>12</td></tr></table>
    </body></html>
    """
    indexpe_html = """
    <html><body>Last updated: 1 Oct 2026 Current PE Ratio 19.19 5Y Median PE 21.97
    Nifty 50's PE is cheaper than 100% of the last 5 years.
    1Y total return -8.20% 5Y total return (p.a.) +7.10% Volatility (1Y std dev) 13.34% Beta vs Nifty 50 (1Y) 1.00
    Nifty 50 earnings grew +12.34% a year over the last 5 years.</body></html>
    """
    s = parse_screener_summary(screener_html)
    assert s["current"] == 22422 and abs(s["pe"] - 19.2) < 1e-9 and abs(s["pb"] - 2.75) < 1e-9
    rows = parse_constituent_table(screener_html)
    assert len(rows) == 2 and rows[0]["symbol"] == "AAA" and rows[1]["profit_growth"] == -5
    w, pm, pc, n = winsorized_weighted_growth(rows)
    assert n == 2 and round(w, 2) == 5.00 and round(pm, 2) == 66.67 and round(pc, 2) == 50.00
    p = parse_indexpe(indexpe_html)
    assert abs(p["pe"] - 19.19) < 1e-9 and abs(p["pe_5y_median"] - 21.97) < 1e-9
    assert p["pe_5y_percentile_pct"] == 0 and abs(p["volatility_1y_pct"] - 13.34) < 1e-9
    assert valuation_opportunity_score(-12.65) > valuation_opportunity_score(12.65)
    print("Self-test passed: public-HTML parsers and scoring helpers are healthy.")


def main():
    if "--self-test" in sys.argv:
        self_test()
        return
    prior = load_json(LATEST_FILE, None)
    try:
        snapshot = build_snapshot(prior=prior)
        save_json(LATEST_FILE, snapshot)
        update_history(snapshot)
        print("Refresh succeeded for", snapshot["as_of"], "status", snapshot["source_status"]["status"])
    except Exception as exc:
        if prior:
            prior["generated_at"] = datetime.now(timezone.utc).isoformat()
            prior["source_status"] = {
                "status": "cached",
                "used_cached_data": True,
                "message": "All core refresh paths failed; showing last good data. Error: " + str(exc),
                "components": {
                    "valuation_risk": {"status": "cached", "message": str(exc)},
                    "market_fundamentals": {"status": "cached", "message": "Preserved with prior snapshot."},
                    "breadth": {"status": "cached", "message": "Preserved with prior snapshot."},
                },
            }
            save_json(LATEST_FILE, prior)
            print("::warning::Refresh failed; preserved last good data: " + str(exc))
        else:
            raise


if __name__ == "__main__":
    main()
