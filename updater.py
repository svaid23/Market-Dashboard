import json
import math
import statistics
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

BASE = "https://www.niftyindices.com"
HIST_PAGE = BASE + "/reports/historical-data"
PRICE_URL = BASE + "/Backpage.aspx/getHistoricaldatatabletoString"
VALUATION_URL = BASE + "/Backpage.aspx/getpepbHistoricaldataDBtoString"
ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
LATEST_FILE = DATA_DIR / "latest.json"
HISTORY_FILE = DATA_DIR / "history.json"

INDICES = [
    {"key": "large", "name": "NIFTY 50", "label": "Large Cap", "benchmark": True},
    {"key": "mid", "name": "NIFTY MIDCAP 100", "label": "Mid Cap", "benchmark": False},
    {"key": "small", "name": "NIFTY SMALLCAP 250", "label": "Small Cap", "benchmark": False},
]

HEADERS = {
    "Content-Type": "application/json; charset=UTF-8",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": HIST_PAGE,
    "User-Agent": "Mozilla/5.0 (compatible; CapAllocationDashboard/1.0)",
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
    payload = resp.json()
    rows = payload.get("d", [])
    if isinstance(rows, str):
        rows = rows.strip()
        if not rows:
            return []
        rows = json.loads(rows)
    if not isinstance(rows, list):
        return []
    return rows


def request_rows(session, url, payload, retries=3):
    last_error = None
    for attempt in range(retries):
        try:
            resp = session.post(url, headers=HEADERS, json=payload, timeout=45)
            resp.raise_for_status()
            return decode_rows(resp)
        except Exception as exc:
            last_error = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"Data request failed: {last_error}")


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
        rows = request_rows(session, PRICE_URL, {"cinfo": cinfo})
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
                chunk_start.strftime("%d %b %Y"),
                chunk_end.strftime("%d %b %Y"),
                index_name,
            )
        )
        rows = request_rows(session, VALUATION_URL, {"cinfo": cinfo})
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


def nearest_on_or_before(mapping, target):
    candidates = [d for d in mapping if d <= target]
    if not candidates:
        return None, None
    dt = max(candidates)
    return dt, mapping[dt]


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


def valuation_score(delta):
    if delta is None:
        return 0
    if delta <= -0.20:
        return 4
    if delta <= -0.10:
        return 3
    if delta <= 0.10:
        return 2
    if delta <= 0.20:
        return 1
    return 0


def earnings_score(growth):
    if growth is None:
        return 0
    if growth > 0.15:
        return 4
    if growth >= 0.08:
        return 3
    if growth >= 0:
        return 2
    if growth >= -0.10:
        return 1
    return 0


def status_label(total):
    if total >= 8:
        return "High research priority"
    if total >= 6:
        return "Worth deeper research"
    if total >= 4:
        return "Mixed setup"
    return "Demanding setup"


def direction(change):
    if change is None:
        return "n/a"
    if change >= 0.02:
        return "up"
    if change <= -0.02:
        return "down"
    return "flat"


def percentile(values, x):
    clean = sorted(v for v in values if v is not None and math.isfinite(v))
    if not clean or x is None:
        return None
    count = sum(1 for v in clean if v <= x)
    return count / len(clean)


def build_live_snapshot():
    today = date.today()
    start_price = today - timedelta(days=430)
    start_pe = today - timedelta(days=5 * 365 + 45)

    session = requests.Session()
    try:
        session.get(HIST_PAGE, headers={"User-Agent": HEADERS["User-Agent"]}, timeout=8)
    except Exception:
        pass

    prepared = []
    common_as_of = None

    for cfg in INDICES:
        price_map = fetch_price_history(session, cfg["name"], start_price, today)
        valuation_map = fetch_valuation_history(session, cfg["name"], start_pe, today)
        as_of = last_common_date(price_map, valuation_map)
        if common_as_of is None or as_of < common_as_of:
            common_as_of = as_of
        prepared.append((cfg, price_map, valuation_map))

    results = []
    for cfg, price_map, valuation_map in prepared:
        current = pair_on_or_before(price_map, valuation_map, common_as_of)
        prior_1y = pair_on_or_before(price_map, valuation_map, common_as_of - timedelta(days=365))
        prior_6m = pair_on_or_before(price_map, valuation_map, common_as_of - timedelta(days=183))
        prior_3m = pair_on_or_before(price_map, valuation_map, common_as_of - timedelta(days=91))
        if not all([current, prior_1y, prior_6m, prior_3m]):
            raise RuntimeError(f"Insufficient history for {cfg['name']}")

        five_year_start = common_as_of - timedelta(days=5 * 365)
        pe_values = [
            item["pe"] for dt, item in valuation_map.items()
            if five_year_start <= dt <= common_as_of and item.get("pe")
        ]
        pe_median = statistics.median(pe_values) if pe_values else None
        pe_delta = pct_change(current["pe"], pe_median)

        earn_now = earnings_value(current["close"], current["pe"])
        earn_1y = earnings_value(prior_1y["close"], prior_1y["pe"])
        earn_3m = earnings_value(prior_3m["close"], prior_3m["pe"])
        earn_growth = pct_change(earn_now, earn_1y)
        earn_3m_change = pct_change(earn_now, earn_3m)

        ret_6m = pct_change(current["close"], prior_6m["close"])
        ret_1y = pct_change(current["close"], prior_1y["close"])

        item = {
            "key": cfg["key"],
            "label": cfg["label"],
            "index_name": cfg["name"],
            "as_of": common_as_of.isoformat(),
            "close": round(current["close"], 2),
            "pe": round(current["pe"], 2),
            "pb": round(current["pb"], 2) if current.get("pb") is not None else None,
            "dividend_yield_pct": round(current["div_yield"], 2) if current.get("div_yield") is not None else None,
            "pe_5y_median": round(pe_median, 2) if pe_median is not None else None,
            "pe_vs_5y_median_pct": round(pe_delta * 100, 1) if pe_delta is not None else None,
            "pe_5y_percentile_pct": round(percentile(pe_values, current["pe"]) * 100, 1) if pe_values else None,
            "ttm_earnings_index": round(earn_now, 4),
            "ttm_earnings_growth_yoy_pct": round(earn_growth * 100, 1) if earn_growth is not None else None,
            "earnings_3m_change_pct": round(earn_3m_change * 100, 1) if earn_3m_change is not None else None,
            "earnings_direction_3m": direction(earn_3m_change),
            "price_return_6m_pct": round(ret_6m * 100, 1) if ret_6m is not None else None,
            "price_return_1y_pct": round(ret_1y * 100, 1) if ret_1y is not None else None,
            "valuation_score": valuation_score(pe_delta),
            "earnings_score": earnings_score(earn_growth),
            "momentum_score": None,
            "total_score": None,
            "valuation_premium_vs_large_pct": None,
            "status": None,
        }
        results.append(item)

    large = next(x for x in results if x["key"] == "large")
    for item in results:
        if item["key"] == "large":
            item["momentum_score"] = 1
            item["valuation_premium_vs_large_pct"] = 0.0
        else:
            beats_6m = item["price_return_6m_pct"] > large["price_return_6m_pct"]
            beats_1y = item["price_return_1y_pct"] > large["price_return_1y_pct"]
            item["momentum_score"] = int(beats_6m) + int(beats_1y)
            item["valuation_premium_vs_large_pct"] = round((item["pe"] / large["pe"] - 1) * 100, 1)
        item["total_score"] = item["valuation_score"] + item["earnings_score"] + item["momentum_score"]
        item["status"] = status_label(item["total_score"])

    results = sorted(results, key=lambda x: ["large", "mid", "small"].index(x["key"]))
    ranked = sorted(results, key=lambda x: (-x["total_score"], ["large", "mid", "small"].index(x["key"])))

    return {
        "as_of": common_as_of.isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "methodology_version": "1.0",
        "source_status": {
            "status": "live",
            "used_cached_data": False,
            "message": "Official Nifty Indices historical price and valuation endpoints refreshed successfully.",
        },
        "sources": [
            {
                "name": "Nifty Indices Historical Data",
                "url": "https://www.niftyindices.com/reports/historical-data",
                "purpose": "Index prices, P/E, P/B and dividend yield",
            },
            {
                "name": "Nifty Indices P/E methodology",
                "url": "https://www.niftyindices.com/resources/index-concepts/price-earnings-ratio",
                "purpose": "Index P/E and trailing earnings methodology",
            },
        ],
        "indices": results,
        "ranked_research_priority": [x["key"] for x in ranked],
        "disclaimer": "The score is a research-priority indicator, not a buy/sell recommendation or return forecast.",
    }


def update_history(snapshot):
    history = load_json(HISTORY_FILE, {"snapshots": []})
    snapshots = history.get("snapshots", [])
    summary = {
        "as_of": snapshot.get("as_of"),
        "generated_at": snapshot.get("generated_at"),
        "scores": {x["key"]: x["total_score"] for x in snapshot.get("indices", [])},
        "pe": {x["key"]: x["pe"] for x in snapshot.get("indices", [])},
        "earnings_growth": {x["key"]: x["ttm_earnings_growth_yoy_pct"] for x in snapshot.get("indices", [])},
    }
    snapshots = [x for x in snapshots if x.get("as_of") != summary["as_of"]]
    snapshots.append(summary)
    snapshots = sorted(snapshots, key=lambda x: x.get("as_of") or "")[-72:]
    save_json(HISTORY_FILE, {"snapshots": snapshots})


def main():
    prior = load_json(LATEST_FILE, None)
    try:
        snapshot = build_live_snapshot()
        save_json(LATEST_FILE, snapshot)
        update_history(snapshot)
        print("Refresh succeeded for", snapshot["as_of"])
    except Exception as exc:
        if prior:
            prior["generated_at"] = datetime.now(timezone.utc).isoformat()
            prior["source_status"] = {
                "status": "cached",
                "used_cached_data": True,
                "message": "Live refresh failed; showing last good data. Error: " + str(exc),
            }
            save_json(LATEST_FILE, prior)
            print("Refresh failed; preserved last good data:", exc)
        else:
            raise


if __name__ == "__main__":
    main()
