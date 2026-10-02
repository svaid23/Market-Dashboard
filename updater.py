import io
import json
import math
import re
import statistics
from datetime import datetime, timezone, timedelta
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
        'dhan_index_url': 'https://dhan.co/indices/nifty-100-share-price/',
        'dhan_gainers': 'https://dhan.co/stocks/market/nifty-100-gainers/',
        'dhan_losers': 'https://dhan.co/stocks/market/nifty-100-losers/',
        'yf': '^CNX100',
    },
    'mid': {
        'label': 'Mid Cap', 'index': 'Nifty Midcap 150',
        'wealth_url': 'https://wealthticker.in/nifty-pe-ratio/nifty-midcap-150',
        'dhan_index_url': 'https://dhan.co/indices/nifty-midcap-150-share-price/',
        'dhan_gainers': 'https://dhan.co/stocks/market/nifty-midcap-150-gainers/',
        'dhan_losers': 'https://dhan.co/stocks/market/nifty-midcap-150-losers/',
        'yf': 'NIFTYMIDCAP150.NS',
    },
    'small': {
        'label': 'Small Cap', 'index': 'Nifty Smallcap 250',
        'wealth_url': 'https://wealthticker.in/nifty-pe-ratio/nifty-smallcap-250',
        'dhan_index_url': 'https://dhan.co/indices/nifty-smallcap-250-share-price/',
        'dhan_gainers': 'https://dhan.co/stocks/market/nifty-smallcap-250-gainers/',
        'dhan_losers': 'https://dhan.co/stocks/market/nifty-smallcap-250-losers/',
        'yf': 'NIFTYSMLCAP250.NS',
    },
}

REFERENCE = {
    'nifty50': {'name':'Nifty 50','dhan':'https://dhan.co/indices/nifty-50-share-price/'},
    'nifty500': {'name':'Nifty 500','dhan':'https://dhan.co/indices/nifty-500-share-price/'},
}

CONSENSUS_SOURCES = [
    {
        'name':'Motilal Oswal MF',
        'url':'https://www.motilaloswalmf.com/motilal-oswal-edge/articles',
        'reliability':'High',
        'hints':['monthly market outlook','broader market','large cap','mid cap','small cap']
    },
    {
        'name':'Axis MF',
        'url':'https://www.axismf.com/efactsheet/Aug-2026/Innerpage/Hybrid-outlook.html',
        'reliability':'High',
        'hints':['large cap','mid cap','small cap','market cap']
    },
    {
        'name':'DSP NETRA',
        'url':'https://www.dspim.com/dspnetra',
        'reliability':'High',
        'hints':['large cap','mid cap','small cap','smid','valuation']
    },
    {
        'name':'HDFC MF',
        'url':'https://www.hdfcfund.com/learn/macros-markets-more/market-review',
        'reliability':'High',
        'hints':['large cap','mid cap','small cap','market cap']
    },
    {
        'name':'PL Capital',
        'url':'https://www.plindia.com/research/',
        'reliability':'Medium-High',
        'hints':['large cap','mid cap','small cap','mid & small','india strategy']
    },
]


def get(url, *, binary=False):
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r.content if binary else r.text


def safe_float(v):
    if v is None:
        return None
    s = str(v).replace(',', '').replace('₹','').replace('%','').replace('−','-').strip()
    if not s or s.lower() in {'-','--','na','n/a','none'}:
        return None
    try:
        return float(s)
    except Exception:
        return None


def percentile_rank(values, current, invert=False):
    vals = [float(x) for x in values if x is not None and math.isfinite(float(x))]
    if current is None or len(vals) < 12:
        return None
    if invert:
        # higher dividend yield = cheaper, so valuation-expensiveness percentile is reversed
        return 100.0 * sum(v >= current for v in vals) / len(vals)
    return 100.0 * sum(v <= current for v in vals) / len(vals)


def valuation_label(pe_pct, pb_pct, premium_pct=None):
    # Descriptive condition, not a recommendation/ranking.
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
    vals=[]
    for _, row in df.iterrows():
        year = safe_float(row.iloc[0])
        if year is None or year < 2000:
            continue
        for x in row.iloc[1:13]:
            n=safe_float(x)
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
            m=re.search(p,text,re.I)
            if m:
                return safe_float(m.group(1))
        return None

    pe=grab([r'closed at a price-to-earnings ratio of\s*([\d.]+)', r'P/E ratio today.*?([\d.]+)'])
    pb=grab([r'price-to-book ratio is\s*([\d.]+)', r'P/B ratio is\s*([\d.]+)'])
    dy=grab([r'dividend yield is\s*([\d.]+)%'])
    full_pe_pct=grab([r'P/E of [\d.]+ is the\s*([\d.]+)(?:st|nd|rd|th) percentile'])
    relative=grab([r'trades at\s*([\d.]+)×\s*the Nifty 50'])
    relative_median=grab([r'against a median of\s*([\d.]+)×'])
    asof=None
    m=re.search(r'(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4})',text,re.I)
    if m: asof=m.group(1)

    pe_series=[]; pb_series=[]; dy_series=[]
    try:
        tables=pd.read_html(io.StringIO(html))
        month_tables=[]
        for df in tables:
            cols=[str(c).strip().lower() for c in df.columns]
            if len(cols)>=10 and ('year' in cols[0] or str(df.iloc[:,0].name).lower().startswith('year')):
                month_tables.append(df)
        # wealthticker puts P/E, P/B, dividend yield monthly grids in that order
        if len(month_tables)>=1: pe_series=parse_monthly_table(month_tables[0])
        if len(month_tables)>=2: pb_series=parse_monthly_table(month_tables[1])
        if len(month_tables)>=3: dy_series=parse_monthly_table(month_tables[2])
    except Exception:
        pass

    def recent_values(series, months=60):
        nums=[v for _,v in series]
        return nums[-months:] if len(nums)>months else nums

    pe5=recent_values(pe_series); pb5=recent_values(pb_series); dy5=recent_values(dy_series)
    return {
        'pe':pe,'pb':pb,'dividend_yield':dy,'reported_pe_percentile':full_pe_pct,
        'relative_pe_vs_nifty50':relative,'relative_pe_median_vs_nifty50':relative_median,
        'pe_5y_percentile':percentile_rank(pe5,pe),
        'pb_5y_percentile':percentile_rank(pb5,pb),
        'dy_5y_expensiveness_percentile':percentile_rank(dy5,dy,invert=True),
        'pe_monthly_5y':pe5,'pb_monthly_5y':pb5,'dy_monthly_5y':dy5,
        'as_of_text':asof,
    }


def parse_dhan_index(url):
    html=get(url)
    text=' '.join(BeautifulSoup(html,'html.parser').stripped_strings)
    def grab(p):
        m=re.search(p,text,re.I)
        return safe_float(m.group(1)) if m else None
    current=grab(r'Share Price\s*₹?\s*([\d,]+\.\d+)')
    dma50=grab(r'50 DMA:\s*([\d,]+\.\d+)')
    dma200=grab(r'200 DMA:\s*([\d,]+\.\d+)')
    pos=grab(r'Positive Stocks\s*(\d+)')
    neg=grab(r'Negative Stocks\s*(\d+)')
    neutral=grab(r'Neutral Stocks\s*(\d+)')
    return {
        'level':current,'dma50':dma50,'dma200':dma200,
        'vs_50dma_pct': ((current/dma50)-1)*100 if current and dma50 else None,
        'vs_200dma_pct': ((current/dma200)-1)*100 if current and dma200 else None,
        'positive_stocks':pos,'negative_stocks':neg,'neutral_stocks':neutral,
    }


def parse_dhan_stock_table(html):
    import pandas as pd
    rows=[]
    try:
        tables=pd.read_html(io.StringIO(html))
    except Exception:
        return rows
    for df in tables:
        cols=[str(c).lower() for c in df.columns]
        if not any('50 dma' in c for c in cols) or not any('200 dma' in c for c in cols):
            continue
        name_col=df.columns[0]
        ltp_col=next((c for c in df.columns if str(c).strip().lower()=='ltp'),None)
        d50_col=next((c for c in df.columns if '50 dma' in str(c).lower()),None)
        d200_col=next((c for c in df.columns if '200 dma' in str(c).lower()),None)
        if not ltp_col or not d50_col or not d200_col: continue
        for _,r in df.iterrows():
            name=str(r[name_col]).strip()
            ltp=safe_float(r[ltp_col]); d50=safe_float(r[d50_col]); d200=safe_float(r[d200_col])
            if name and ltp:
                rows.append((name,ltp,d50,d200))
    return rows


def calc_breadth(gainers_url, losers_url, expected=None):
    rows={}
    for url in [gainers_url, losers_url]:
        try:
            for name,ltp,d50,d200 in parse_dhan_stock_table(get(url)):
                rows[name]=(ltp,d50,d200)
        except Exception:
            continue
    vals=list(rows.values())
    cov50=[x for x in vals if x[1] is not None]
    cov200=[x for x in vals if x[2] is not None]
    out={
        'covered_stocks':len(vals),
        'coverage_pct': (len(vals)/expected*100) if expected else None,
        'above_50dma_pct': (sum(1 for l,d,_ in cov50 if l>d)/len(cov50)*100) if cov50 else None,
        'above_200dma_pct': (sum(1 for l,_,d in cov200 if l>d)/len(cov200)*100) if cov200 else None,
    }
    if expected and len(vals) < expected*0.70:
        out['status']='low_coverage'
    else:
        out['status']='live' if vals else 'unavailable'
    return out


def fetch_rbi_gsec():
    urls=[
        'https://www.rbi.org.in/Scripts/BS_NSDPDisplay.aspx?param=4',
        'https://www.rbi.org.in/scripts/WSSView.aspx?Id=27337',
    ]
    for url in urls:
        try:
            text=' '.join(BeautifulSoup(get(url),'html.parser').stripped_strings)
            m=re.search(r'10-Year G-Sec Par Yield \(FBIL\).*?((?:\d+\.\d+\s+){1,12})', text, re.I)
            if m:
                nums=[safe_float(x) for x in re.findall(r'\d+\.\d+',m.group(1))]
                nums=[x for x in nums if x is not None and 3<x<12]
                if nums:
                    return {'yield_pct':nums[-1],'source':url,'status':'live'}
        except Exception:
            pass
    return {'yield_pct':None,'source':urls[0],'status':'unavailable'}


def yf_close_near(ticker, target_date):
    try:
        import yfinance as yf
        start=target_date-timedelta(days=12); end=target_date+timedelta(days=8)
        df=yf.download(ticker,start=start.isoformat(),end=end.isoformat(),progress=False,auto_adjust=False,threads=False)
        if df is None or df.empty: return None
        close=df['Close']
        if hasattr(close,'columns'): close=close.iloc[:,0]
        close=close.dropna()
        if close.empty: return None
        dates=[d.date() for d in close.index]
        idx=min(range(len(dates)),key=lambda i:abs((dates[i]-target_date).days))
        return float(close.iloc[idx])
    except Exception:
        return None


def earnings_trend(segment, wealth):
    # Uses index price history + WealthTicker monthly median P/E. This is a directional validation, not a forecast.
    ticker=segment['yf']
    pe_series=wealth.get('pe_monthly_5y',[])
    if len(pe_series)<37 or wealth.get('pe') is None:
        return {'growth_1y_pct':None,'cagr_3y_pct':None,'status':'unavailable'}
    today=datetime.now(timezone.utc).date()
    current_level=yf_close_near(ticker,today)
    p1=yf_close_near(ticker,today-timedelta(days=365))
    p3=yf_close_near(ticker,today-timedelta(days=365*3))
    if not current_level or not p1 or not p3:
        return {'growth_1y_pct':None,'cagr_3y_pct':None,'status':'unavailable'}
    pe_now=wealth['pe']; pe1=pe_series[-13] if len(pe_series)>=13 else None; pe3=pe_series[-37] if len(pe_series)>=37 else None
    if not pe1 or not pe3:
        return {'growth_1y_pct':None,'cagr_3y_pct':None,'status':'unavailable'}
    e0=current_level/pe_now; e1=p1/pe1; e3=p3/pe3
    g1=(e0/e1-1)*100 if e1 else None
    g3=((e0/e3)**(1/3)-1)*100 if e3>0 else None
    return {'growth_1y_pct':g1,'cagr_3y_pct':g3,'status':'live','note':'Approximate index earnings = index level / P/E; historical P/E uses monthly medians.'}


def fetch_fii_dii():
    # Informational only. Prefer a simple public page that republishes NSE provisional data.
    url='https://www.ansaar.in/equities/fii-dii-data'
    try:
        html=get(url); tables=[]
        import pandas as pd
        tables=pd.read_html(io.StringIO(html))
        target=None
        for df in tables:
            cols=' '.join(str(c).lower() for c in df.columns)
            if 'fii' in cols and 'dii' in cols and len(df)>=5:
                target=df; break
        rows=[]
        if target is not None:
            for _,r in target.head(30).iterrows():
                vals=list(r.values)
                text=' | '.join(str(x) for x in vals)
                nums=[safe_float(x) for x in re.findall(r'[+−-]?[₹]?\s*[\d,]+(?:\.\d+)?',text)]
                # Common row order has bought/sold/net/dii net. Take signed values if possible from textual cells.
                signed=[]
                for x in vals:
                    sx=str(x).replace('−','-')
                    n=safe_float(sx)
                    if n is not None: signed.append(n)
                # Robust fallback: extract cells containing + or - for net columns.
                netcells=[]
                for x in vals:
                    sx=str(x).replace('−','-').strip()
                    if sx.startswith(('+','-')):
                        n=safe_float(sx)
                        if n is not None: netcells.append(n)
                if len(netcells)>=2:
                    rows.append({'fii_net':netcells[0],'dii_net':netcells[-1]})
        if rows:
            fii=[r['fii_net'] for r in rows]; dii=[r['dii_net'] for r in rows]
            def s(n,arr): return sum(arr[:min(n,len(arr))])
            return {'latest_fii':fii[0],'latest_dii':dii[0],'fii_5d':s(5,fii),'dii_5d':s(5,dii),'fii_20d':s(20,fii),'dii_20d':s(20,dii),'status':'live','source':url,'note':'Republished from NSE provisional combined cash-market data.'}
    except Exception:
        pass
    return {'status':'unavailable','source':'https://www.nseindia.com/reports/fii-dii'}


def fetch_amfi_flows():
    # Best-effort parser. Failure does not affect the valuation model.
    page='https://www.amfiindia.com/research-information/amfi-monthly'
    try:
        html=get(page); soup=BeautifulSoup(html,'html.parser')
        links=[]
        for a in soup.find_all('a',href=True):
            txt=' '.join(a.stripped_strings).lower()
            href=urljoin(page,a['href'])
            if 'excel' in txt or href.lower().endswith(('.xlsx','.xls')):
                links.append(href)
        for href in links[:6]:
            try:
                content=get(href,binary=True)
                import pandas as pd
                xl=pd.ExcelFile(io.BytesIO(content))
                found={}
                for sheet in xl.sheet_names:
                    df=pd.read_excel(xl,sheet_name=sheet,header=None)
                    for i,row in df.iterrows():
                        rowtxt=' | '.join(str(x) for x in row.values if str(x)!='nan')
                        for key,label in [('large','Large Cap Fund'),('mid','Mid Cap Fund'),('small','Small Cap Fund')]:
                            if key in found: continue
                            if label.lower() in rowtxt.lower():
                                # Find nearest header row with net inflow/outflow keyword.
                                col=None
                                for j in range(max(0,i-12),i):
                                    for c,val in enumerate(df.iloc[j].values):
                                        sv=str(val).lower()
                                        if 'net' in sv and ('inflow' in sv or 'flow' in sv): col=c
                                if col is not None:
                                    n=safe_float(row.iloc[col])
                                    if n is not None: found[key]=n
                    if len(found)==3: break
                if found:
                    return {'status':'live','source':href,'flows_cr':found}
            except Exception:
                continue
    except Exception:
        pass
    return {'status':'unavailable','source':page,'flows_cr':{}}


POS_WORDS={'attractive','balanced risk-reward','opportunity','opportunities','higher growth','improving','supportive','favour','favor','constructive','reasonable valuation','alpha'}
NEG_WORDS={'rich valuation','rich valuations','expensive','stretched','cautious','caution','limited margin of safety','headwind','overvalued','froth'}

def extract_sentences(text):
    return [re.sub(r'\s+',' ',s).strip() for s in re.split(r'(?<=[.!?])\s+',text) if len(s.strip())>25]

def segment_stance(text, segment):
    low=text.lower()
    keys={
        'large':['large cap','large-cap','largecaps','large caps'],
        'mid':['mid cap','mid-cap','midcap','mid caps','smid'],
        'small':['small cap','small-cap','smallcap','small caps','smid'],
    }[segment]
    relevant=[s for s in extract_sentences(text) if any(k in s.lower() for k in keys)]
    if not relevant:
        return {'stance':'No explicit view','evidence':''}
    joined=' '.join(relevant[:5]).lower()
    pos=sum(1 for w in POS_WORDS if w in joined); neg=sum(1 for w in NEG_WORDS if w in joined)
    if pos>neg: stance='Positive/Constructive'
    elif neg>pos: stance='Cautious/Selective'
    else: stance='Neutral/Selective'
    return {'stance':stance,'evidence':relevant[0][:240]}


def fetch_consensus():
    out=[]
    for src in CONSENSUS_SOURCES:
        item={'name':src['name'],'url':src['url'],'reliability':src['reliability'],'status':'unavailable'}
        try:
            html=get(src['url']); soup=BeautifulSoup(html,'html.parser'); text=' '.join(soup.stripped_strings)
            item['large']=segment_stance(text,'large'); item['mid']=segment_stance(text,'mid'); item['small']=segment_stance(text,'small')
            item['status']='live'; item['note']='Rule-based classification of explicit cap-related wording; informational only.'
        except Exception as e:
            item['error']=str(e)[:160]
        out.append(item)
    return out


def main():
    DATA.mkdir(exist_ok=True)
    old={}
    if LATEST.exists():
        try: old=json.loads(LATEST.read_text())
        except Exception: old={}

    source_health={}
    segment_data={}
    wealth={}
    for key,cfg in SEGMENTS.items():
        try:
            wealth[key]=parse_wealth(cfg['wealth_url'])
            source_health[f'valuation_{key}']={'status':'live','url':cfg['wealth_url']}
        except Exception as e:
            wealth[key]=(old.get('segments',{}).get(key,{}).get('_wealth') or {})
            source_health[f'valuation_{key}']={'status':'cached','url':cfg['wealth_url'],'error':str(e)[:180]}

    # Relative premium percentile based on common 5-year monthly P/E medians.
    large_series=wealth.get('large',{}).get('pe_monthly_5y',[])
    for key,cfg in SEGMENTS.items():
        w=wealth[key]
        premium_pct=None
        rel=w.get('relative_pe_vs_nifty50')
        rel_med=w.get('relative_pe_median_vs_nifty50')
        if key!='large' and large_series and w.get('pe_monthly_5y'):
            cur = (w.get('pe')/wealth['large'].get('pe')) if w.get('pe') and wealth['large'].get('pe') else None
            ratios=[a/b for a,b in zip(w['pe_monthly_5y'][-60:],large_series[-60:]) if b and a]
            premium_pct=percentile_rank(ratios,cur) if cur else None
            rel=cur
            rel_med=statistics.median(ratios) if ratios else rel_med

        try: trend=parse_dhan_index(cfg['dhan_index_url']); source_health[f'trend_{key}']={'status':'live','url':cfg['dhan_index_url']}
        except Exception as e: trend={}; source_health[f'trend_{key}']={'status':'unavailable','url':cfg['dhan_index_url'],'error':str(e)[:180]}
        try: breadth=calc_breadth(cfg['dhan_gainers'],cfg['dhan_losers'], {'large':100,'mid':150,'small':250}[key]); source_health[f'breadth_{key}']={'status':breadth.get('status'),'url':cfg['dhan_gainers']}
        except Exception as e: breadth={'status':'unavailable'}; source_health[f'breadth_{key}']={'status':'unavailable','error':str(e)[:180]}
        earn=earnings_trend(cfg,w)
        roe=(w.get('pb')/w.get('pe')*100) if w.get('pb') and w.get('pe') else None
        earnings_yield=(100/w.get('pe')) if w.get('pe') else None
        segment_data[key]={
            'label':cfg['label'],'index':cfg['index'],
            'pe':w.get('pe'),'pb':w.get('pb'),'dividend_yield':w.get('dividend_yield'),
            'pe_5y_percentile':w.get('pe_5y_percentile') or w.get('reported_pe_percentile'),
            'pb_5y_percentile':w.get('pb_5y_percentile'),
            'div_yield_expensiveness_percentile':w.get('dy_5y_expensiveness_percentile'),
            'relative_pe_vs_large':rel,'relative_pe_median':rel_med,'relative_premium_5y_percentile':premium_pct,
            'earnings_yield_pct':earnings_yield,'roe_proxy_pct':roe,
            'earnings_growth_1y_pct':earn.get('growth_1y_pct'),'earnings_cagr_3y_pct':earn.get('cagr_3y_pct'),
            'trend':trend,'breadth':breadth,
            '_wealth':w,
        }

    gsec=fetch_rbi_gsec(); source_health['gsec']={'status':gsec['status'],'url':gsec['source']}
    for key,s in segment_data.items():
        s['equity_bond_spread_pp']=(s['earnings_yield_pct']-gsec['yield_pct']) if s.get('earnings_yield_pct') is not None and gsec.get('yield_pct') is not None else None
        s['valuation_condition']=valuation_label(s.get('pe_5y_percentile'),s.get('pb_5y_percentile'),s.get('relative_premium_5y_percentile') if key!='large' else None)

    refs={}
    for key,cfg in REFERENCE.items():
        try: refs[key]=parse_dhan_index(cfg['dhan']); source_health[f'reference_{key}']={'status':'live','url':cfg['dhan']}
        except Exception as e: refs[key]={}; source_health[f'reference_{key}']={'status':'unavailable','url':cfg['dhan'],'error':str(e)[:180]}

    fii=fetch_fii_dii(); source_health['fii_dii']={'status':fii.get('status'),'url':fii.get('source')}
    amfi=fetch_amfi_flows(); source_health['amfi']={'status':amfi.get('status'),'url':amfi.get('source')}
    consensus=fetch_consensus()
    source_health['consensus']={'status':'partial' if any(x['status']=='live' for x in consensus) else 'unavailable'}

    now=datetime.now(timezone.utc).isoformat()
    out={
        'methodology_version':'4.0',
        'generated_at':now,
        'as_of':datetime.now(timezone.utc).date().isoformat(),
        'model_note':'Core valuation conclusion uses P/E percentile, P/B percentile, relative valuation premium, equity-bond spread, earnings trend and ROE proxy. Technicals, breadth, flows and external consensus are informational only.',
        'segments':segment_data,
        'gsec_10y':gsec,
        'references':refs,
        'fii_dii':fii,
        'amfi_flows':amfi,
        'external_consensus':consensus,
        'source_health':source_health,
        'sources':{
            'valuation':'https://wealthticker.in/nifty-pe-ratio',
            'gsec':'https://www.rbi.org.in/Scripts/BS_NSDPDisplay.aspx?param=4',
            'trend_breadth':'https://dhan.co/indices/',
            'fii_dii_official':'https://www.nseindia.com/reports/fii-dii',
            'amfi':'https://www.amfiindia.com/research-information/amfi-monthly',
        },
        'disclaimer':'Descriptive market research only. The dashboard does not recommend, rank or predict investment outcomes. Market-context indicators do not alter the valuation condition.'
    }
    LATEST.write_text(json.dumps(out,indent=2),encoding='utf-8')

    hist={'snapshots':[]}
    if HISTORY.exists():
        try: hist=json.loads(HISTORY.read_text())
        except Exception: pass
    snap={'date':out['as_of'],'valuation':{k:v['valuation_condition'] for k,v in segment_data.items()},'pe':{k:v['pe'] for k,v in segment_data.items()}}
    snaps=[x for x in hist.get('snapshots',[]) if x.get('date')!=snap['date']]
    snaps.append(snap); snaps=snaps[-120:]
    HISTORY.write_text(json.dumps({'snapshots':snaps},indent=2),encoding='utf-8')
    print(f"Refresh complete: methodology 4.0; {len([x for x in source_health.values() if x.get('status')=='live'])} live sources")

if __name__=='__main__':
    main()
