import io
import json
import math
import re
import statistics
from datetime import datetime, timezone, timedelta, date
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'data'
LATEST = DATA / 'latest.json'
HISTORY = DATA / 'history.json'

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/134 Safari/537.36',
    'Accept-Language': 'en-IN,en;q=0.9',
}
TIMEOUT = 35

SEGMENTS = {
    'large': {
        'label': 'Large Cap', 'index': 'Nifty 100',
        'wealth_url': 'https://wealthticker.in/nifty-pe-ratio/nifty-100',
        'indexpe_url': 'https://indexpe.in/nifty-100',
        'dhan_index_url': 'https://dhan.co/indices/nifty-100-share-price/',
        'yf': '^CNX100',
    },
    'mid': {
        'label': 'Mid Cap', 'index': 'Nifty Midcap 150',
        'wealth_url': 'https://wealthticker.in/nifty-pe-ratio/nifty-midcap-150',
        'indexpe_url': 'https://indexpe.in/nifty-midcap-150',
        'dhan_index_url': 'https://dhan.co/indices/nifty-midcap-150-share-price/',
        'yf': 'NIFTYMIDCAP150.NS',
    },
    'small': {
        'label': 'Small Cap', 'index': 'Nifty Smallcap 250',
        'wealth_url': 'https://wealthticker.in/nifty-pe-ratio/nifty-smallcap-250',
        'indexpe_url': 'https://indexpe.in/nifty-smallcap-250',
        'dhan_index_url': 'https://dhan.co/indices/nifty-smallcap-250-share-price/',
        'yf': 'NIFTYSMLCAP250.NS',
    },
}

REFERENCE = {
    'nifty50': {'name': 'Nifty 50', 'dhan': 'https://dhan.co/indices/nifty-50-share-price/'},
    'nifty500': {'name': 'Nifty 500', 'dhan': 'https://dhan.co/indices/nifty-500-share-price/'},
}

TRADINGVIEW_BREADTH_URL = 'https://in.tradingview.com/markets/indices/'
GROWW_FII_DII_URL = 'https://groww.in/fii-dii-data'

# Ten independent external sources. These are context only and never alter the core model.
# direct=True means use the page as the report; otherwise the crawler may follow a few
# relevant links from the landing page to find the most recent market/outlook document.
CONSENSUS_SOURCES = [
    {
        'name': 'Franklin Templeton India', 'reliability': 'High', 'direct': True,
        'url': 'https://www.franklintempletonindia.com/static/factsheet/Innerpage/Equity-Market-Snap-Shot.html'
    },
    {
        'name': 'Axis MF', 'reliability': 'High', 'direct': True,
        'url': 'https://www.axismf.com/efactsheet/Aug-2026/Innerpage/Hybrid-outlook.html'
    },
    {
        'name': 'Motilal Oswal MF', 'reliability': 'High', 'direct': False,
        'url': 'https://www.motilaloswalmf.com/motilal-oswal-edge/articles'
    },
    {
        'name': 'DSP NETRA', 'reliability': 'High', 'direct': False,
        'url': 'https://www.dspim.com/dspnetra'
    },
    {
        'name': 'SBI Mutual Fund', 'reliability': 'High', 'direct': True,
        'url': "https://www.sbimf.com/learn-about-mutual-funds/nifty-midcap-150-investing-in-india%27s-emerging-growth-leaders-sbi-mutual-fund"
    },
    {
        'name': 'Aditya Birla Sun Life MF', 'reliability': 'High', 'direct': False,
        'url': 'https://mutualfund.adityabirlacapital.com/abslamc-knowledge-centre/monthly-outlook'
    },
    {
        'name': 'ICICI Prudential MF', 'reliability': 'High', 'direct': True,
        'url': 'https://www.icicipruamc.com/blob/sebi-repo/Advertisements/2026/August/Filing%20date%2012-08-2026/Release%20date%2011-08-2026/Advertisements/Annexure%206%20-%20Mailer%20on%20Monthly%20Market%20Outlook.html'
    },
    {
        'name': 'PL Capital', 'reliability': 'Medium-High', 'direct': False,
        'url': 'https://www.plindia.com/research/'
    },
    {
        'name': 'Kotak Mutual Fund', 'reliability': 'High', 'direct': False,
        'url': 'https://www.kotakmf.com/monthly-market-update'
    },
    {
        'name': 'Nippon India Mutual Fund', 'reliability': 'High', 'direct': False,
        'url': 'https://mf.nipponindiaim.com/knowledge-center/updates-insights/market-outlook'
    },
]

MONTHS = {
    'jan': 1, 'january': 1, 'feb': 2, 'february': 2, 'mar': 3, 'march': 3,
    'apr': 4, 'april': 4, 'may': 5, 'jun': 6, 'june': 6, 'jul': 7, 'july': 7,
    'aug': 8, 'august': 8, 'sep': 9, 'sept': 9, 'september': 9, 'oct': 10,
    'october': 10, 'nov': 11, 'november': 11, 'dec': 12, 'december': 12,
}


def get(url, *, binary=False):
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r.content if binary else r.text


def safe_float(v):
    if v is None:
        return None
    s = str(v).replace(',', '').replace('₹', '').replace('%', '').replace('−', '-').strip()
    if not s or s.lower() in {'-', '--', 'na', 'n/a', 'none'}:
        return None
    try:
        return float(s)
    except Exception:
        return None


def percentile_rank(values, current, invert=False, drop_current=False):
    vals = [float(x) for x in values if x is not None and math.isfinite(float(x))]
    if current is None or len(vals) < 12:
        return None
    # Monthly source tables usually contain the current month as the final observation.
    # Do not let today's observation vote on its own historical percentile.
    if drop_current and vals and abs(vals[-1] - float(current)) <= max(0.01, abs(float(current)) * 0.001):
        vals = vals[:-1]
    if not vals:
        return None
    if invert:
        # Higher dividend yield = cheaper, so valuation-expensiveness percentile is reversed.
        return 100.0 * sum(v >= current for v in vals) / len(vals)
    return 100.0 * sum(v <= current for v in vals) / len(vals)


def valuation_label(pe_pct, pb_pct, premium_pct=None):
    # Descriptive condition only. Earnings/ROE/bond spread are validation context,
    # not additional hidden weights in this label.
    vals = [x for x in [pe_pct, pb_pct] if x is not None]
    if not vals:
        return 'Insufficient data'
    core = statistics.mean(vals)
    if premium_pct is not None:
        core = 0.8 * core + 0.2 * premium_pct
    if core <= 25:
        return 'Below-normal valuation'
    if core <= 40:
        return 'Slightly below normal'
    if core < 60:
        return 'Broadly fair'
    if core < 75:
        return 'Above normal'
    return 'Well above normal'


def parse_monthly_table(df):
    if df is None or df.empty:
        return []
    vals = []
    for _, row in df.iterrows():
        year = safe_float(row.iloc[0])
        if year is None or year < 2000:
            continue
        for x in row.iloc[1:13]:
            n = safe_float(x)
            if n is not None and n > 0:
                vals.append((int(year), n))
    return vals


def parse_wealth(url):
    import pandas as pd
    html = get(url)
    soup = BeautifulSoup(html, 'html.parser')
    text = ' '.join(soup.stripped_strings)

    def grab(patterns):
        for p in patterns:
            m = re.search(p, text, re.I)
            if m:
                return safe_float(m.group(1))
        return None

    pe = grab([r'closed at a price-to-earnings ratio of\s*([\d.]+)', r'P/E ratio today.*?([\d.]+)'])
    pb = grab([r'price-to-book ratio is\s*([\d.]+)', r'P/B ratio is\s*([\d.]+)'])
    dy = grab([r'dividend yield is\s*([\d.]+)%'])
    full_pe_pct = grab([r'P/E of [\d.]+ is the\s*([\d.]+)(?:st|nd|rd|th) percentile'])
    relative = grab([r'trades at\s*([\d.]+)×\s*the Nifty 50'])
    relative_median = grab([r'against a median of\s*([\d.]+)×'])
    asof = None
    m = re.search(r'(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4})', text, re.I)
    if m:
        asof = m.group(1)

    pe_series, pb_series, dy_series = [], [], []
    try:
        tables = pd.read_html(io.StringIO(html))
        month_tables = []
        for df in tables:
            cols = [str(c).strip().lower() for c in df.columns]
            if len(cols) >= 10 and ('year' in cols[0] or str(df.iloc[:, 0].name).lower().startswith('year')):
                month_tables.append(df)
        if len(month_tables) >= 1:
            pe_series = parse_monthly_table(month_tables[0])
        if len(month_tables) >= 2:
            pb_series = parse_monthly_table(month_tables[1])
        if len(month_tables) >= 3:
            dy_series = parse_monthly_table(month_tables[2])
    except Exception:
        pass

    def recent_values(series, months=60):
        nums = [v for _, v in series]
        return nums[-months:] if len(nums) > months else nums

    pe5, pb5, dy5 = recent_values(pe_series), recent_values(pb_series), recent_values(dy_series)
    return {
        'pe': pe, 'pb': pb, 'dividend_yield': dy, 'reported_pe_percentile': full_pe_pct,
        'relative_pe_vs_nifty50': relative, 'relative_pe_median_vs_nifty50': relative_median,
        'pe_5y_percentile_monthly': percentile_rank(pe5, pe, drop_current=True),
        'pb_5y_percentile': percentile_rank(pb5, pb, drop_current=True),
        'pb_percentile_basis': '5Y monthly medians from WealthTicker/NSE-derived tables, current observation excluded',
        'dy_5y_expensiveness_percentile': percentile_rank(dy5, dy, invert=True, drop_current=True),
        'pe_monthly_5y': pe5, 'pb_monthly_5y': pb5, 'dy_monthly_5y': dy5,
        'as_of_text': asof,
    }


def parse_indexpe(url, index_name):
    """Best-effort extraction of IndexPE's 5Y daily P/E percentile and 5Y earnings CAGR.
    If the page is serving a different metric state (e.g. PB), PE percentile is rejected
    unless the explanatory sentence explicitly says '<index>'s PE is ...'.
    """
    html = get(url)
    text = ' '.join(BeautifulSoup(html, 'html.parser').stripped_strings)
    out = {'source': url, 'status': 'live'}
    m = re.search(r'Current PE Ratio\s*([\d.]+)', text, re.I)
    out['pe'] = safe_float(m.group(1)) if m else None
    m = re.search(r'5Y Median PE\s*([\d.]+)', text, re.I)
    out['pe_5y_median'] = safe_float(m.group(1)) if m else None
    # Explicit PE wording is required; this avoids accidentally reading a PB toggle state.
    idx = re.escape(index_name)
    m = re.search(r'(\d{1,3})(?:st|nd|rd|th) percentile\s+' + idx + r"'s\s+PE\s+is\s+", text, re.I)
    if not m:
        m = re.search(r'(\d{1,3})(?:st|nd|rd|th) percentile.*?' + idx + r"'s\s+PE\s+is\s+", text, re.I)
    out['pe_5y_percentile_daily'] = safe_float(m.group(1)) if m else None
    m = re.search(r'5Y daily data\s*\(([\d,]+)\s*obs', text, re.I)
    out['pe_daily_observations'] = int(m.group(1).replace(',', '')) if m else None
    m = re.search(r'earnings grew\s*([+−-]?[\d.]+)%\s*a year over the last 5 years', text, re.I)
    out['earnings_cagr_5y_pct'] = safe_float(m.group(1)) if m else None
    m = re.search(r'Last updated:\s*(\d{1,2}\s+[A-Za-z]+\s+\d{4})', text, re.I)
    out['as_of_text'] = m.group(1) if m else None
    return out


def parse_dhan_index(url):
    html = get(url)
    text = ' '.join(BeautifulSoup(html, 'html.parser').stripped_strings)

    def grab(p):
        m = re.search(p, text, re.I)
        return safe_float(m.group(1)) if m else None

    current = grab(r'Share Price\s*₹?\s*([\d,]+\.\d+)')
    dma50 = grab(r'50 DMA:\s*([\d,]+\.\d+)')
    dma200 = grab(r'200 DMA:\s*([\d,]+\.\d+)')
    pos = grab(r'Positive Stocks\s*(\d+)')
    neg = grab(r'Negative Stocks\s*(\d+)')
    neutral = grab(r'Neutral Stocks\s*(\d+)')
    return {
        'level': current, 'dma50': dma50, 'dma200': dma200,
        'vs_50dma_pct': ((current / dma50) - 1) * 100 if current and dma50 else None,
        'vs_200dma_pct': ((current / dma200) - 1) * 100 if current and dma200 else None,
        'positive_stocks': pos, 'negative_stocks': neg, 'neutral_stocks': neutral,
    }


def fetch_tradingview_breadth():
    html = get(TRADINGVIEW_BREADTH_URL)
    text = ' '.join(BeautifulSoup(html, 'html.parser').stripped_strings)
    specs = {
        'large': ('NIFTY', 'Nifty 50'),
        'mid': ('CNXMIDCAP', 'Nifty MidCap'),
        'small': ('CNXSMALLCAP', 'Nifty SmallCap'),
        'nifty50': ('NIFTY', 'Nifty 50'),
        'nifty500': ('CNX500', 'Nifty 500'),
    }
    out = {}
    for key, (sym, name) in specs.items():
        pat = rf'{re.escape(sym)}\s*{re.escape(name)}.*?(\d{{1,3}})%\s+(\d{{1,3}})%'
        m = re.search(pat, text, re.I)
        if not m:
            m = re.search(rf'{re.escape(name)}.*?(\d{{1,3}})%\s+(\d{{1,3}})%', text, re.I)
        if m:
            out[key] = {
                'above_50dma_pct': safe_float(m.group(1)),
                'above_200dma_pct': safe_float(m.group(2)),
                'status': 'live', 'proxy_index': name, 'source': TRADINGVIEW_BREADTH_URL,
            }
        else:
            out[key] = {'status': 'unavailable', 'proxy_index': name, 'source': TRADINGVIEW_BREADTH_URL}
    return out


def fetch_rbi_gsec():
    urls = [
        'https://www.rbi.org.in/Scripts/BS_NSDPDisplay.aspx?param=4',
        'https://www.rbi.org.in/scripts/WSSView.aspx?Id=27337',
    ]
    for url in urls:
        try:
            text = ' '.join(BeautifulSoup(get(url), 'html.parser').stripped_strings)
            m = re.search(r'10-Year G-Sec Par Yield \(FBIL\).*?((?:\d+\.\d+\s+){1,12})', text, re.I)
            if m:
                nums = [safe_float(x) for x in re.findall(r'\d+\.\d+', m.group(1))]
                nums = [x for x in nums if x is not None and 3 < x < 12]
                if nums:
                    return {'yield_pct': nums[-1], 'source': url, 'status': 'live'}
        except Exception:
            pass
    return {'yield_pct': None, 'source': urls[0], 'status': 'unavailable'}


def yf_close_near(ticker, target_date):
    try:
        import yfinance as yf
        start = target_date - timedelta(days=12)
        end = target_date + timedelta(days=8)
        df = yf.download(ticker, start=start.isoformat(), end=end.isoformat(), progress=False, auto_adjust=False, threads=False)
        if df is None or df.empty:
            return None
        close = df['Close']
        if hasattr(close, 'columns'):
            close = close.iloc[:, 0]
        close = close.dropna()
        if close.empty:
            return None
        dates = [d.date() for d in close.index]
        idx = min(range(len(dates)), key=lambda i: abs((dates[i] - target_date).days))
        return float(close.iloc[idx])
    except Exception:
        return None


def earnings_trend(segment, wealth, dhan_trend=None, indexpe=None):
    # Directional historical validation only; never a forecast.
    ticker = segment['yf']
    pe_series = wealth.get('pe_monthly_5y', [])
    if len(pe_series) < 37 or wealth.get('pe') is None:
        return {'growth_1y_pct': None, 'cagr_3y_pct': None, 'status': 'unavailable'}
    today = datetime.now(timezone.utc).date()
    yf_current = yf_close_near(ticker, today)
    current_level = (dhan_trend or {}).get('level') or yf_current
    p1 = yf_close_near(ticker, today - timedelta(days=365))
    p3 = yf_close_near(ticker, today - timedelta(days=365 * 3))
    if not current_level or not p1 or not p3:
        return {'growth_1y_pct': None, 'cagr_3y_pct': None, 'status': 'unavailable'}
    pe_now = wealth['pe']
    pe1 = pe_series[-13] if len(pe_series) >= 13 else None
    pe3 = pe_series[-37] if len(pe_series) >= 37 else None
    if not pe1 or not pe3:
        return {'growth_1y_pct': None, 'cagr_3y_pct': None, 'status': 'unavailable'}
    e0 = current_level / pe_now
    e1 = p1 / pe1
    e3 = p3 / pe3
    g1 = (e0 / e1 - 1) * 100 if e1 else None
    g3 = ((e0 / e3) ** (1 / 3) - 1) * 100 if e3 > 0 else None
    checks = []
    if yf_current and (dhan_trend or {}).get('level'):
        diff = abs(yf_current / dhan_trend['level'] - 1) * 100
        checks.append({'check': 'current_index_level_crosscheck', 'difference_pct': diff, 'pass': diff <= 3.0})
    if g1 is not None:
        checks.append({'check': '1y_growth_sanity', 'pass': -60 <= g1 <= 120})
    if g3 is not None:
        checks.append({'check': '3y_cagr_sanity', 'pass': -30 <= g3 <= 60})
    ref5 = (indexpe or {}).get('earnings_cagr_5y_pct')
    if ref5 is not None:
        checks.append({'check': 'indexpe_5y_reference_available', 'reference_5y_cagr_pct': ref5, 'pass': True})
    ok = all(c.get('pass', True) for c in checks)
    return {
        'growth_1y_pct': g1 if ok else None,
        'cagr_3y_pct': g3 if ok else None,
        'status': 'live' if ok else 'warning',
        'validation': checks,
        'note': 'Approximate index earnings = index level / P/E; historical P/E uses monthly medians. Cross-checked against Dhan/Yahoo and IndexPE where available.'
    }


def _parse_row_date(s):
    s = str(s).strip()
    for fmt in ('%d-%b-%Y', '%d %b %Y', '%d/%m/%Y', '%d-%m-%Y', '%d %B %Y'):
        try:
            return datetime.strptime(s, fmt).date()
        except Exception:
            pass
    return None


def fetch_fii_dii():
    url = GROWW_FII_DII_URL
    try:
        import pandas as pd
        html = get(url)
        tables = pd.read_html(io.StringIO(html))
        rows = []
        for df in tables:
            if len(df) < 1:
                continue
            cols = []
            for c in df.columns:
                if isinstance(c, tuple):
                    cols.append(' '.join(str(x) for x in c if str(x) != 'nan').lower())
                else:
                    cols.append(str(c).lower())
            if not any('fii' in c for c in cols) or not any('dii' in c for c in cols):
                continue
            for _, r in df.head(45).iterrows():
                vals = list(r.values)
                date_txt = str(vals[0])
                d = _parse_row_date(date_txt)
                nums = [safe_float(x) for x in vals[1:]]
                nums = [x for x in nums if x is not None]
                if len(nums) >= 6:
                    rows.append({'date': date_txt, 'parsed_date': d, 'fii_net': nums[2], 'dii_net': nums[5]})
            if rows:
                break
        if rows:
            fii = [r['fii_net'] for r in rows if r.get('fii_net') is not None]
            dii = [r['dii_net'] for r in rows if r.get('dii_net') is not None]
            nrows = min(len(fii), len(dii))
            def exact_sum(arr, n):
                return sum(arr[:n]) if len(arr) >= n else None
            latest_date = next((r['parsed_date'] for r in rows if r.get('parsed_date')), None)
            mtd_rows = [r for r in rows if latest_date and r.get('parsed_date') and r['parsed_date'].year == latest_date.year and r['parsed_date'].month == latest_date.month]
            # MTD is shown only if the table appears to cover the month from day 1 or the first trading day.
            mtd_complete = bool(mtd_rows) and min(r['parsed_date'].day for r in mtd_rows) <= 4
            return {
                'latest_fii': fii[0] if fii else None, 'latest_dii': dii[0] if dii else None,
                'fii_5d': exact_sum(fii, 5), 'dii_5d': exact_sum(dii, 5),
                'fii_20d': exact_sum(fii, 20), 'dii_20d': exact_sum(dii, 20),
                'fii_mtd': sum(r['fii_net'] for r in mtd_rows) if mtd_complete else None,
                'dii_mtd': sum(r['dii_net'] for r in mtd_rows) if mtd_complete else None,
                'rows_available': nrows,
                'window_20d_complete': nrows >= 20,
                'mtd_complete': mtd_complete,
                'status': 'live', 'source': url,
                'note': 'Groww republishes exchange cash-market institutional activity. 20D is blank unless 20 actual sessions are available.'
            }
    except Exception as e:
        return {'status': 'unavailable', 'source': url, 'error': str(e)[:180]}
    return {'status': 'unavailable', 'source': url}


def extract_sentences(text):
    # Also split on bullets/newlines after BeautifulSoup text flattening.
    text = re.sub(r'\s+', ' ', text)
    return [s.strip() for s in re.split(r'(?<=[.!?])\s+', text) if len(s.strip()) > 25]


def extract_report_date(text, url=''):
    candidates = []
    # 1 Oct 2026 / 01 October 2026
    for m in re.finditer(r'\b(\d{1,2})\s+(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+(20\d{2})\b', text, re.I):
        mon = MONTHS.get(m.group(2).lower())
        if mon:
            try: candidates.append(date(int(m.group(3)), mon, int(m.group(1))))
            except Exception: pass
    # Month 2026 in title/path. Use the 15th when day is absent; age buckets tolerate this.
    combined = text[:1200] + ' ' + url
    for m in re.finditer(r'\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)[\s\-_/,%]*(20\d{2})\b', combined, re.I):
        mon = MONTHS.get(m.group(1).lower())
        if mon:
            try: candidates.append(date(int(m.group(2)), mon, 15))
            except Exception: pass
    candidates = [d for d in candidates if date(2020,1,1) <= d <= datetime.now(timezone.utc).date() + timedelta(days=7)]
    return max(candidates) if candidates else None


CAP_KEYS = {
    'large': ['large cap', 'large-cap', 'large caps', 'largecap'],
    'mid': ['mid cap', 'mid-cap', 'mid caps', 'midcap', 'mid/small', 'smid'],
    'small': ['small cap', 'small-cap', 'small caps', 'smallcap', 'mid/small', 'smid'],
}

VAL_POS = [
    'attractive valuation', 'attractive valuations', 'relatively attractive', 'more attractive',
    'reasonable valuation', 'valuations have become more reasonable', 'favourably priced', 'favorably priced',
    'undervalued', 'cheap', 'cheaper', 'discount to', 'better valuation'
]
VAL_NEG = [
    'rich valuation', 'rich valuations', 'valuations remain rich', 'expensive', 'overvalued', 'stretched',
    'sizeable premium', 'sizable premium', 'high premium', 'limited margin of safety', 'froth'
]
VAL_NEU = ['fairly valued', 'fair value', 'around long-term average', 'around long term average', 'balanced valuation']
DIR_POS = [
    'prefer ', 'preferred', 'favour ', 'favor ', 'overweight', 'constructive on', 'better placed',
    'balanced risk-reward', 'favourable risk-reward', 'favorable risk-reward', 'focus on ', 'opportunities in '
]
DIR_NEG = [
    'underweight', 'avoid ', 'cautious', 'caution', 'staggered manner', 'staggered approach',
    'unfavourable risk-reward', 'unfavorable risk-reward', 'vulnerable', 'correct more sharply'
]
DIR_NEU = ['neutral', 'selective', 'flexibility across', 'balanced approach', 'across market caps']


def _relevant_sentences(text, segment):
    keys = CAP_KEYS[segment]
    relevant = []
    for sent in extract_sentences(text):
        # Split contrast clauses so a sentence such as 'large caps are better placed while
        # mid/small should be staggered' does not give both signals to every cap.
        clauses = [c.strip(' ,') for c in re.split(r'\bwhile\b|\bwhereas\b|\bbut\b|;', sent, flags=re.I) if c.strip()]
        matched = [c for c in clauses if any(k in c.lower() for k in keys)]
        relevant.extend(matched)
    # 'broader market' commonly refers to mid/small caps. It may inform direction only;
    # valuation still requires explicit valuation wording in that clause.
    if segment in {'mid', 'small'}:
        for sent in extract_sentences(text):
            if 'broader market' in sent.lower() and sent not in relevant:
                relevant.append(sent)
    return relevant[:12]


def _classify_explicit(text, segment):
    rel = _relevant_sentences(text, segment)
    valuation = {'view': 'No explicit view', 'evidence': ''}
    direction = {'view': 'No explicit view', 'evidence': ''}
    if not rel:
        return {'valuation': valuation, 'direction': direction}

    # Score only explicit evaluative/preference phrases in sentences naming the cap.
    vp, vn, vf = [], [], []
    dp, dn, dz = [], [], []
    for s in rel:
        low = s.lower()
        if any(p in low for p in VAL_POS): vp.append(s)
        if any(p in low for p in VAL_NEG): vn.append(s)
        if any(p in low for p in VAL_NEU): vf.append(s)
        if any(p in low for p in DIR_POS): dp.append(s)
        if any(p in low for p in DIR_NEG): dn.append(s)
        if any(p in low for p in DIR_NEU): dz.append(s)

    if vp and not vn:
        valuation = {'view': 'Attractive', 'evidence': vp[0][:320]}
    elif vn and not vp:
        valuation = {'view': 'Expensive', 'evidence': vn[0][:320]}
    elif vp and vn:
        valuation = {'view': 'Mixed/Fair', 'evidence': (vp[0] + ' ' + vn[0])[:320]}
    elif vf:
        valuation = {'view': 'Fair', 'evidence': vf[0][:320]}

    if dp and not dn:
        direction = {'view': 'Constructive', 'evidence': dp[0][:320]}
    elif dn and not dp:
        direction = {'view': 'Cautious', 'evidence': dn[0][:320]}
    elif dp and dn:
        direction = {'view': 'Mixed', 'evidence': (dp[0] + ' ' + dn[0])[:320]}
    elif dz:
        direction = {'view': 'Neutral/Selective', 'evidence': dz[0][:320]}

    return {'valuation': valuation, 'direction': direction}


def _candidate_links(base_url, html):
    soup = BeautifulSoup(html, 'html.parser')
    scored = []
    for a in soup.find_all('a', href=True):
        label = ' '.join(a.stripped_strings)
        href = urljoin(base_url, a['href'])
        blob = (label + ' ' + href).lower()
        score = 0
        for kw in ['market outlook', 'equity market outlook', 'monthly outlook', 'india strategy', 'netra', 'market update']:
            if kw in blob: score += 6
        for kw in ['large cap', 'mid cap', 'small cap', 'valuation', 'equity']:
            if kw in blob: score += 2
        if re.search(r'2026|sep|oct|september|october', blob): score += 2
        if href.startswith('http') and score > 0:
            scored.append((score, href))
    seen, out = set(), []
    for _, href in sorted(scored, reverse=True):
        if href not in seen:
            seen.add(href); out.append(href)
    return out[:4]


def fetch_source_report(src):
    landing_html = get(src['url'])
    candidates = [(src['url'], landing_html)] if src.get('direct') else []
    if not src.get('direct'):
        for link in _candidate_links(src['url'], landing_html):
            try:
                candidates.append((link, get(link)))
            except Exception:
                continue
        if not candidates:
            candidates = [(src['url'], landing_html)]
    evaluated = []
    for url, html in candidates:
        text = ' '.join(BeautifulSoup(html, 'html.parser').stripped_strings)
        d = extract_report_date(text, url)
        cap_mentions = sum(text.lower().count(k) for arr in CAP_KEYS.values() for k in arr)
        eval_mentions = sum(text.lower().count(k) for k in (VAL_POS + VAL_NEG + DIR_POS + DIR_NEG))
        # Date dominates selection, then content relevance.
        date_ord = d.toordinal() if d else 0
        evaluated.append((date_ord, cap_mentions + eval_mentions * 2, url, text, d))
    evaluated.sort(reverse=True)
    _, _, url, text, d = evaluated[0]
    return url, text, d


def age_bucket(report_date):
    if not report_date:
        return 'undated', None
    age = (datetime.now(timezone.utc).date() - report_date).days
    if age <= 45:
        return 'current', age
    if age <= 90:
        return 'stale', age
    return 'archive', age


def fetch_consensus_sources():
    out = []
    for src in CONSENSUS_SOURCES:
        item = {
            'name': src['name'], 'landing_url': src['url'], 'reliability': src['reliability'],
            'status': 'unavailable', 'report_url': None, 'report_date': None, 'age_bucket': 'unavailable',
        }
        try:
            report_url, text, report_date = fetch_source_report(src)
            bucket, age = age_bucket(report_date)
            item.update({
                'status': 'live', 'report_url': report_url,
                'report_date': report_date.isoformat() if report_date else None,
                'age_days': age, 'age_bucket': bucket,
                'large': _classify_explicit(text, 'large'),
                'mid': _classify_explicit(text, 'mid'),
                'small': _classify_explicit(text, 'small'),
            })
        except Exception as e:
            item['error'] = str(e)[:180]
        out.append(item)
    return out


def summarize_consensus(sources):
    summary = {}
    for cap in ('large', 'mid', 'small'):
        val_counts = {'Attractive': 0, 'Fair': 0, 'Mixed/Fair': 0, 'Expensive': 0, 'No explicit view': 0}
        dir_counts = {'Constructive': 0, 'Neutral/Selective': 0, 'Mixed': 0, 'Cautious': 0, 'No explicit view': 0}
        current_sources = [s for s in sources if s.get('status') == 'live' and s.get('age_bucket') == 'current']
        stale_sources = [s for s in sources if s.get('status') == 'live' and s.get('age_bucket') in {'stale', 'archive'}]
        for s in current_sources:
            vv = ((s.get(cap) or {}).get('valuation') or {}).get('view', 'No explicit view')
            dv = ((s.get(cap) or {}).get('direction') or {}).get('view', 'No explicit view')
            val_counts[vv if vv in val_counts else 'No explicit view'] += 1
            dir_counts[dv if dv in dir_counts else 'No explicit view'] += 1

        vdir = val_counts['Attractive'] + val_counts['Expensive']
        if vdir >= 3:
            ratio = val_counts['Attractive'] / vdir
            if ratio >= 0.65: vlabel = 'Attractive'
            elif ratio <= 0.35: vlabel = 'Expensive'
            else: vlabel = 'Mixed'
        else:
            vlabel = 'Insufficient consensus'

        ddir = dir_counts['Constructive'] + dir_counts['Cautious']
        if ddir >= 3:
            ratio = dir_counts['Constructive'] / ddir
            if ratio >= 0.65: dlabel = 'Constructive'
            elif ratio <= 0.35: dlabel = 'Cautious'
            else: dlabel = 'Mixed'
        else:
            dlabel = 'Insufficient consensus'

        summary[cap] = {
            'valuation_label': vlabel,
            'direction_label': dlabel,
            'valuation_counts': val_counts,
            'direction_counts': dir_counts,
            'current_sources': len(current_sources),
            'stale_or_archive_sources': len(stale_sources),
            'total_sources': len(sources),
            'valuation_directional_votes': vdir,
            'direction_directional_votes': ddir,
            'rule': 'Only <=45-day explicit views vote. Silence does not vote. At least 3 directional votes are required; 65% agreement sets the directional label.'
        }
    return summary


def main():
    DATA.mkdir(exist_ok=True)
    old = {}
    if LATEST.exists():
        try: old = json.loads(LATEST.read_text())
        except Exception: old = {}

    source_health, segment_data, wealth, indexpe = {}, {}, {}, {}
    for key, cfg in SEGMENTS.items():
        try:
            wealth[key] = parse_wealth(cfg['wealth_url'])
            source_health[f'valuation_{key}'] = {'status': 'live', 'url': cfg['wealth_url']}
        except Exception as e:
            wealth[key] = (old.get('segments', {}).get(key, {}).get('_wealth') or {})
            source_health[f'valuation_{key}'] = {'status': 'cached', 'url': cfg['wealth_url'], 'error': str(e)[:180]}
        try:
            indexpe[key] = parse_indexpe(cfg['indexpe_url'], cfg['index'])
            source_health[f'pe_percentile_daily_{key}'] = {
                'status': 'live' if indexpe[key].get('pe_5y_percentile_daily') is not None else 'partial',
                'url': cfg['indexpe_url']
            }
        except Exception as e:
            indexpe[key] = {}
            source_health[f'pe_percentile_daily_{key}'] = {'status': 'unavailable', 'url': cfg['indexpe_url'], 'error': str(e)[:180]}

    large_series = wealth.get('large', {}).get('pe_monthly_5y', [])
    for key, cfg in SEGMENTS.items():
        w = wealth[key]; ip = indexpe.get(key, {})
        premium_pct = None
        rel = w.get('relative_pe_vs_nifty50'); rel_med = w.get('relative_pe_median_vs_nifty50')
        if key != 'large' and large_series and w.get('pe_monthly_5y'):
            cur = (w.get('pe') / wealth['large'].get('pe')) if w.get('pe') and wealth['large'].get('pe') else None
            ratios = [a / b for a, b in zip(w['pe_monthly_5y'][-60:], large_series[-60:]) if b and a]
            premium_pct = percentile_rank(ratios, cur, drop_current=True) if cur else None
            rel = cur
            rel_med = statistics.median(ratios[:-1] if len(ratios) > 1 else ratios) if ratios else rel_med

        try:
            trend = parse_dhan_index(cfg['dhan_index_url'])
            source_health[f'trend_{key}'] = {'status': 'live', 'url': cfg['dhan_index_url']}
        except Exception as e:
            trend = {}
            source_health[f'trend_{key}'] = {'status': 'unavailable', 'url': cfg['dhan_index_url'], 'error': str(e)[:180]}

        earn = earnings_trend(cfg, w, trend, ip)
        roe = (w.get('pb') / w.get('pe') * 100) if w.get('pb') and w.get('pe') else None
        earnings_yield = (100 / w.get('pe')) if w.get('pe') else None
        pe_pct_daily = ip.get('pe_5y_percentile_daily')
        pe_pct = pe_pct_daily if pe_pct_daily is not None else w.get('pe_5y_percentile_monthly')
        pe_basis = ('5Y daily data from IndexPE' if pe_pct_daily is not None
                    else '5Y monthly medians from WealthTicker/NSE-derived tables; current observation excluded')
        segment_data[key] = {
            'label': cfg['label'], 'index': cfg['index'],
            'pe': w.get('pe'), 'pb': w.get('pb'), 'dividend_yield': w.get('dividend_yield'),
            'pe_5y_percentile': pe_pct, 'pe_percentile_basis': pe_basis,
            'pb_5y_percentile': w.get('pb_5y_percentile'), 'pb_percentile_basis': w.get('pb_percentile_basis'),
            'div_yield_expensiveness_percentile': w.get('dy_5y_expensiveness_percentile'),
            'relative_pe_vs_large': rel, 'relative_pe_median': rel_med,
            'relative_premium_5y_percentile': premium_pct,
            'earnings_yield_pct': earnings_yield, 'roe_proxy_pct': roe,
            'earnings_growth_1y_pct': earn.get('growth_1y_pct'),
            'earnings_cagr_3y_pct': earn.get('cagr_3y_pct'),
            'earnings_validation': earn,
            'trend': trend, 'breadth': {'status': 'pending'},
            '_wealth': w, '_indexpe': ip,
        }

    try:
        tvb = fetch_tradingview_breadth()
    except Exception as e:
        tvb = {}
        source_health['breadth_market'] = {'status': 'unavailable', 'url': TRADINGVIEW_BREADTH_URL, 'error': str(e)[:180]}
    else:
        live_count = sum(1 for x in tvb.values() if x.get('status') == 'live')
        source_health['breadth_market'] = {'status': 'live' if live_count >= 3 else 'partial', 'url': TRADINGVIEW_BREADTH_URL}
    for key in SEGMENTS:
        segment_data[key]['breadth'] = tvb.get(key, {'status': 'unavailable'})

    gsec = fetch_rbi_gsec(); source_health['gsec'] = {'status': gsec['status'], 'url': gsec['source']}
    for key, s in segment_data.items():
        s['equity_bond_spread_pp'] = (s['earnings_yield_pct'] - gsec['yield_pct']) if s.get('earnings_yield_pct') is not None and gsec.get('yield_pct') is not None else None
        s['valuation_condition'] = valuation_label(s.get('pe_5y_percentile'), s.get('pb_5y_percentile'), s.get('relative_premium_5y_percentile') if key != 'large' else None)

    refs = {}
    for key, cfg in REFERENCE.items():
        try:
            refs[key] = parse_dhan_index(cfg['dhan'])
            source_health[f'reference_{key}'] = {'status': 'live', 'url': cfg['dhan']}
        except Exception as e:
            refs[key] = {}
            source_health[f'reference_{key}'] = {'status': 'unavailable', 'url': cfg['dhan'], 'error': str(e)[:180]}
        tb = tvb.get(key, {}) if 'tvb' in locals() else {}
        if tb.get('above_50dma_pct') is not None:
            refs[key]['breadth_50dma_pct'] = tb.get('above_50dma_pct')
            refs[key]['breadth_200dma_pct'] = tb.get('above_200dma_pct')

    fii = fetch_fii_dii(); source_health['fii_dii'] = {'status': fii.get('status'), 'url': fii.get('source')}
    consensus_sources = fetch_consensus_sources()
    consensus_summary = summarize_consensus(consensus_sources)
    current_docs = sum(1 for s in consensus_sources if s.get('status') == 'live' and s.get('age_bucket') == 'current')
    source_health['consensus'] = {'status': 'live' if current_docs >= 5 else ('partial' if current_docs else 'unavailable'), 'current_documents': current_docs, 'total_sources': len(consensus_sources)}

    now = datetime.now(timezone.utc).isoformat()
    out = {
        'methodology_version': '4.2',
        'generated_at': now,
        'as_of': datetime.now(timezone.utc).date().isoformat(),
        'model_note': 'Valuation condition is driven by historical P/E and P/B positioning plus relative P/E premium for Mid/Small. Equity-bond spread, earnings trend and ROE proxy validate the read. Technicals, breadth, flows and external consensus are informational only.',
        'segments': segment_data,
        'gsec_10y': gsec,
        'references': refs,
        'fii_dii': fii,
        'external_consensus': {
            'summary': consensus_summary,
            'sources': consensus_sources,
            'rules': {
                'current_days': 45, 'stale_days': 90, 'minimum_directional_votes': 3,
                'agreement_threshold_pct': 65,
                'note': 'Silence does not vote. Only explicit cap-level evaluative/preference wording from current documents is counted.'
            }
        },
        'source_health': source_health,
        'sources': {
            'valuation': 'https://wealthticker.in/nifty-pe-ratio',
            'daily_pe_percentile': 'https://indexpe.in/',
            'gsec': 'https://www.rbi.org.in/Scripts/BS_NSDPDisplay.aspx?param=4',
            'trend': 'https://dhan.co/indices/',
            'breadth': 'https://in.tradingview.com/markets/indices/',
            'fii_dii': 'https://groww.in/fii-dii-data',
        },
        'disclaimer': 'Descriptive market research only. The dashboard does not recommend, rank or predict investment outcomes. Market-context indicators and external views do not alter the valuation condition.'
    }
    LATEST.write_text(json.dumps(out, indent=2), encoding='utf-8')

    hist = {'snapshots': []}
    if HISTORY.exists():
        try: hist = json.loads(HISTORY.read_text())
        except Exception: pass
    snap = {
        'date': out['as_of'],
        'valuation': {k: v['valuation_condition'] for k, v in segment_data.items()},
        'pe': {k: v['pe'] for k, v in segment_data.items()},
        'pe_5y_percentile': {k: v['pe_5y_percentile'] for k, v in segment_data.items()},
    }
    snaps = [x for x in hist.get('snapshots', []) if x.get('date') != snap['date']]
    snaps.append(snap); snaps = snaps[-120:]
    HISTORY.write_text(json.dumps({'snapshots': snaps}, indent=2), encoding='utf-8')
    print(f"Refresh complete: methodology 4.2; {len([x for x in source_health.values() if x.get('status') == 'live'])} live sources; consensus current docs {current_docs}/10")


if __name__ == '__main__':
    main()
