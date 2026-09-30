const FALLBACK_DATA = {
  as_of: "2026-09-30",
  generated_at: "2026-09-30T17:30:00+00:00",
  source_status: {status: "seed", used_cached_data: true, message: "Seed values loaded."},
  indices: [
    {key:"large",label:"Large Cap",index_name:"NIFTY 50",pe:19.36,pe_5y_median:21.97,pe_vs_5y_median_pct:-11.9,ttm_earnings_growth_yoy_pct:4.6,price_return_6m_pct:1.3,price_return_1y_pct:-8.1,valuation_score:3,earnings_score:2,momentum_score:1,total_score:6,valuation_premium_vs_large_pct:0,status:"Worth deeper research"},
    {key:"mid",label:"Mid Cap",index_name:"NIFTY MIDCAP 100",pe:27.98,pe_5y_median:29.89,pe_vs_5y_median_pct:-6.4,ttm_earnings_growth_yoy_pct:20.0,price_return_6m_pct:12.7,price_return_1y_pct:5.0,valuation_score:2,earnings_score:4,momentum_score:2,total_score:8,valuation_premium_vs_large_pct:44.5,status:"High research priority"},
    {key:"small",label:"Small Cap",index_name:"NIFTY SMALLCAP 250",pe:34.45,pe_5y_median:28.31,pe_vs_5y_median_pct:21.7,ttm_earnings_growth_yoy_pct:-2.7,price_return_6m_pct:24.6,price_return_1y_pct:6.6,valuation_score:0,earnings_score:1,momentum_score:2,total_score:3,valuation_premium_vs_large_pct:77.9,status:"Demanding setup"}
  ],
  sources: [
    {name:"Nifty Indices Historical Data",url:"https://www.niftyindices.com/reports/historical-data",purpose:"Index prices, P/E, P/B and dividend yield"},
    {name:"Nifty Indices P/E methodology",url:"https://www.niftyindices.com/resources/index-concepts/price-earnings-ratio",purpose:"Index P/E and trailing earnings methodology"}
  ],
  disclaimer: "The score is a research-priority indicator, not a buy/sell recommendation or return forecast."
};

const fmtPct = v => v == null ? "--" : `${v > 0 ? "+" : ""}${Number(v).toFixed(1)}%`;
const fmtX = v => v == null ? "--" : `${Number(v).toFixed(2)}x`;
const fmtDate = v => {
  if (!v) return "--";
  const d = new Date(`${v}T00:00:00`);
  return d.toLocaleDateString("en-IN", {day:"2-digit", month:"short", year:"numeric"});
};
const toneClass = v => v > 0 ? "good" : v < 0 ? "bad" : "neutral";
const escapeHtml = s => String(s ?? "").replace(/[&<>'"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[c]));

async function loadJson(path, fallback=null) {
  try {
    const r = await fetch(path, {cache:"no-store"});
    if (!r.ok) throw new Error(`${r.status}`);
    return await r.json();
  } catch (_) {
    return fallback;
  }
}

function renderCards(items) {
  const el = document.getElementById("cards");
  el.innerHTML = items.map(x => `
    <article class="cap-card ${x.key}">
      <div class="card-top">
        <div>
          <div class="card-name">${escapeHtml(x.label)}</div>
          <div class="card-index">${escapeHtml(x.index_name)}</div>
          <span class="status-pill">${escapeHtml(x.status)}</span>
        </div>
        <div class="score-ring" style="--score:${x.total_score}">
          <div><strong>${x.total_score}</strong><small>/10</small></div>
        </div>
      </div>
      <div class="card-metrics">
        <div class="metric">
          <div class="metric-label">Valuation</div>
          <div class="metric-value">${fmtX(x.pe)}</div>
          <div class="metric-score">${x.valuation_score}/4 | ${fmtPct(x.pe_vs_5y_median_pct)} vs 5Y median</div>
        </div>
        <div class="metric">
          <div class="metric-label">Earnings</div>
          <div class="metric-value ${toneClass(x.ttm_earnings_growth_yoy_pct)}">${fmtPct(x.ttm_earnings_growth_yoy_pct)}</div>
          <div class="metric-score">${x.earnings_score}/4 | TTM YoY</div>
        </div>
        <div class="metric">
          <div class="metric-label">Momentum</div>
          <div class="metric-value">${x.momentum_score}/2</div>
          <div class="metric-score">6M ${fmtPct(x.price_return_6m_pct)}</div>
        </div>
      </div>
    </article>`).join("");
}

function renderInsights(items) {
  const ranked = [...items].sort((a,b) => b.total_score - a.total_score);
  const top = ranked[0];
  const large = items.find(x => x.key === "large");
  const mid = items.find(x => x.key === "mid");
  const small = items.find(x => x.key === "small");
  document.getElementById("insightText").innerHTML = `
    <div class="insight-box">
      <strong>${escapeHtml(top.label)} has the highest evidence score (${top.total_score}/10)</strong>
      Its score reflects the combination of today's valuation, actual trailing earnings growth and relative price confirmation. The dashboard deliberately does not forecast a future P/E or EPS.
    </div>
    <div class="insight-box">
      <strong>Mid premium vs Large: ${fmtPct(mid.valuation_premium_vs_large_pct)}</strong>
      Mid caps still require stronger earnings to justify the premium, even when their own valuation is close to historical norms.
    </div>
    <div class="insight-box">
      <strong>Small premium vs Large: ${fmtPct(small.valuation_premium_vs_large_pct)}</strong>
      Compare that premium with Small Cap earnings growth of ${fmtPct(small.ttm_earnings_growth_yoy_pct)}. That gap is a useful risk flag.
    </div>`;
}

function renderCenteredBars(id, items, field, maxAbs, type) {
  const el = document.getElementById(id);
  el.innerHTML = items.map(x => {
    const val = Number(x[field] ?? 0);
    const width = Math.min(Math.abs(val) / maxAbs * 50, 50);
    let cls = val >= 0 ? "positive" : "negative";
    if (type === "earn") cls = val >= 0 ? "earn-positive" : "earn-negative";
    return `<div class="bar-row">
      <div class="bar-label">${escapeHtml(x.label)}</div>
      <div class="bar-track"><div class="bar-zero"></div><div class="bar-fill ${cls}" style="width:${width}%"></div></div>
      <div class="bar-value ${type === "earn" ? toneClass(val) : ""}">${fmtPct(val)}</div>
    </div>`;
  }).join("");
}

function renderMomentum(items) {
  document.getElementById("momentumChart").innerHTML = items.map(x => `
    <div class="momentum-row">
      <div class="bar-label">${escapeHtml(x.label)}</div>
      <div class="return-pair">
        <div class="return-cell"><span>6M</span><strong class="${toneClass(x.price_return_6m_pct)}">${fmtPct(x.price_return_6m_pct)}</strong></div>
        <div class="return-cell"><span>1Y</span><strong class="${toneClass(x.price_return_1y_pct)}">${fmtPct(x.price_return_1y_pct)}</strong></div>
      </div>
    </div>`).join("");
}

function renderPremium(items) {
  const max = Math.max(...items.map(x => Number(x.valuation_premium_vs_large_pct || 0)), 1);
  document.getElementById("premiumChart").innerHTML = items.map(x => {
    const v = Number(x.valuation_premium_vs_large_pct || 0);
    const h = v <= 0 ? 4 : 16 + (v / max) * 66;
    return `<div class="premium-col">
      <div class="premium-bar-wrap"><div class="premium-bar" style="height:${h}px"></div></div>
      <div class="premium-value">${v === 0 ? "Base" : fmtPct(v)}</div>
      <div class="premium-label">${escapeHtml(x.label)}</div>
    </div>`;
  }).join("");
}

function renderTable(items) {
  document.getElementById("scoreTable").innerHTML = items.map(x => `
    <tr>
      <td>${escapeHtml(x.label)}</td>
      <td>${fmtX(x.pe)}</td>
      <td>${fmtX(x.pe_5y_median)}</td>
      <td class="${x.pe_vs_5y_median_pct <= 0 ? "good" : "bad"}">${fmtPct(x.pe_vs_5y_median_pct)}</td>
      <td>${x.valuation_score}/4</td>
      <td class="${toneClass(x.ttm_earnings_growth_yoy_pct)}">${fmtPct(x.ttm_earnings_growth_yoy_pct)}</td>
      <td>${x.earnings_score}/4</td>
      <td class="${toneClass(x.price_return_6m_pct)}">${fmtPct(x.price_return_6m_pct)}</td>
      <td class="${toneClass(x.price_return_1y_pct)}">${fmtPct(x.price_return_1y_pct)}</td>
      <td>${x.momentum_score}/2</td>
      <td class="score-cell">${x.total_score}/10</td>
    </tr>`).join("");
}

function renderHistory(history) {
  const el = document.getElementById("historyChart");
  const points = history?.snapshots || [];
  if (points.length < 2) {
    el.innerHTML = `<div class="history-empty">History will appear here after the next scheduled refresh.</div>`;
    return;
  }
  const w = 1000, h = 180, padX = 32, padY = 18;
  const x = i => padX + (i / Math.max(points.length - 1, 1)) * (w - padX * 2);
  const y = v => h - padY - (Number(v) / 10) * (h - padY * 2);
  const keys = ["large","mid","small"];
  const classes = {large:"#14213d",mid:"#147d73",small:"#b7791f"};
  const lines = keys.map(k => {
    const pts = points.map((p,i) => `${x(i)},${y(p.scores?.[k] ?? 0)}`).join(" ");
    return `<polyline points="${pts}" fill="none" stroke="${classes[k]}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>`;
  }).join("");
  const guides = [0,2,4,6,8,10].map(v => `<line x1="${padX}" x2="${w-padX}" y1="${y(v)}" y2="${y(v)}" stroke="#e6e1d7" stroke-width="1"/><text x="4" y="${y(v)+4}" font-size="11" fill="#7a8495">${v}</text>`).join("");
  el.innerHTML = `<svg class="history-svg" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">${guides}${lines}</svg>
    <div class="history-legend"><span><i class="legend-dot large"></i>Large</span><span><i class="legend-dot mid"></i>Mid</span><span><i class="legend-dot small"></i>Small</span></div>`;
}

function renderSources(data) {
  const status = data.source_status || {};
  const cards = [
    `<div class="source-card"><strong>Refresh status</strong><p>${escapeHtml(status.message || "Unknown")}</p><p>Generated: ${escapeHtml(data.generated_at || "--")}</p></div>`,
    ...(data.sources || []).map(s => `<div class="source-card"><strong>${escapeHtml(s.name)}</strong><p>${escapeHtml(s.purpose)}</p><a href="${escapeHtml(s.url)}" target="_blank" rel="noreferrer">${escapeHtml(s.url)}</a></div>`)
  ];
  document.getElementById("sourceDetails").innerHTML = cards.join("");
}

function renderStatus(data) {
  document.getElementById("asOf").textContent = fmtDate(data.as_of);
  const st = data.source_status?.status || "unknown";
  const dot = document.getElementById("statusDot");
  dot.classList.remove("live","cached");
  if (st === "live") dot.classList.add("live"); else dot.classList.add("cached");
  document.getElementById("sourceStatus").textContent = st === "live" ? "Live data refresh successful" : "Showing last available data";
  document.getElementById("disclaimer").textContent = data.disclaimer || FALLBACK_DATA.disclaimer;
}

function render(data, history) {
  const items = data.indices || [];
  renderStatus(data);
  renderCards(items);
  renderInsights(items);
  renderCenteredBars("valuationChart", items, "pe_vs_5y_median_pct", 30, "valuation");
  renderCenteredBars("earningsChart", items, "ttm_earnings_growth_yoy_pct", 25, "earn");
  renderMomentum(items);
  renderPremium(items);
  renderTable(items);
  renderHistory(history);
  renderSources(data);
}

document.getElementById("methodBtn").addEventListener("click", () => {
  const el = document.getElementById("methodology");
  el.classList.toggle("hidden");
  if (!el.classList.contains("hidden")) el.scrollIntoView({behavior:"smooth", block:"start"});
});

(async function init() {
  const data = await loadJson("data/latest.json", FALLBACK_DATA);
  const history = await loadJson("data/history.json", {snapshots:[]});
  render(data, history);
})();
