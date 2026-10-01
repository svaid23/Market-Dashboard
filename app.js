const FALLBACK_DATA = {
  as_of: "2026-09-30",
  generated_at: "2026-09-30T17:30:00+00:00",
  methodology_version: "2.4",
  source_status: {
    status: "seed",
    used_cached_data: true,
    message: "Seed values loaded. Run the refresh workflow once after upgrading to populate the new v2 metrics.",
    components: {
      core_index_data: {status: "seed", message: "Legacy seed values"},
      breadth: {status: "unavailable", message: "Awaiting first v2 refresh"}
    }
  },
  indices: [
    {
      key:"large", label:"Large Cap", index_name:"NIFTY 50", pe:19.36, pb:null,
      pe_5y_percentile_pct:null, pb_5y_percentile_pct:null, earnings_yield_pct:5.17,
      implied_roe_pct:null, implied_roe_5y_percentile_pct:null, quality_score:null,
      ttm_earnings_growth_yoy_pct:4.6, earnings_acceleration_3m_pp:null,
      price_return_6m_pct:1.3, price_return_1y_pct:-8.1,
      valuation_premium_vs_large_pct:null, valuation_premium_5y_percentile_pct:null,
      breadth_above_50dma_pct:null, breadth_above_200dma_pct:null,
      breadth_50_coverage_pct:null, breadth_200_coverage_pct:null, breadth_source_status:"unavailable",
      volatility_1y_pct:null, max_drawdown_1y_pct:null, drawdown_from_52w_high_pct:null,
      opportunity_score:null, opportunity_label:"Awaiting v2 refresh", opportunity_coverage_pct:0,
      risk_score:null, risk_label:"Awaiting v2 refresh", risk_coverage_pct:0
    },
    {
      key:"mid", label:"Mid Cap", index_name:"NIFTY MIDCAP 100", pe:27.98, pb:null,
      pe_5y_percentile_pct:null, pb_5y_percentile_pct:null, earnings_yield_pct:3.57,
      implied_roe_pct:null, implied_roe_5y_percentile_pct:null, quality_score:null,
      ttm_earnings_growth_yoy_pct:20.0, earnings_acceleration_3m_pp:null,
      price_return_6m_pct:12.7, price_return_1y_pct:5.0,
      valuation_premium_vs_large_pct:44.5, valuation_premium_5y_percentile_pct:null,
      breadth_above_50dma_pct:null, breadth_above_200dma_pct:null,
      breadth_50_coverage_pct:null, breadth_200_coverage_pct:null, breadth_source_status:"unavailable",
      volatility_1y_pct:null, max_drawdown_1y_pct:null, drawdown_from_52w_high_pct:null,
      opportunity_score:null, opportunity_label:"Awaiting v2 refresh", opportunity_coverage_pct:0,
      risk_score:null, risk_label:"Awaiting v2 refresh", risk_coverage_pct:0
    },
    {
      key:"small", label:"Small Cap", index_name:"NIFTY SMALLCAP 250", pe:34.45, pb:null,
      pe_5y_percentile_pct:null, pb_5y_percentile_pct:null, earnings_yield_pct:2.90,
      implied_roe_pct:null, implied_roe_5y_percentile_pct:null, quality_score:null,
      ttm_earnings_growth_yoy_pct:-2.7, earnings_acceleration_3m_pp:null,
      price_return_6m_pct:24.6, price_return_1y_pct:6.6,
      valuation_premium_vs_large_pct:77.9, valuation_premium_5y_percentile_pct:null,
      breadth_above_50dma_pct:null, breadth_above_200dma_pct:null,
      breadth_50_coverage_pct:null, breadth_200_coverage_pct:null, breadth_source_status:"unavailable",
      volatility_1y_pct:null, max_drawdown_1y_pct:null, drawdown_from_52w_high_pct:null,
      opportunity_score:null, opportunity_label:"Awaiting v2 refresh", opportunity_coverage_pct:0,
      risk_score:null, risk_label:"Awaiting v2 refresh", risk_coverage_pct:0
    }
  ],
  score_weights: {
    opportunity:{valuation:25,relative_valuation:15,earnings:25,breadth:15,quality:10,momentum:10},
    risk:{valuation:20,relative_premium:15,volatility:25,drawdown:20,breadth_fragility:10,earnings_deterioration:10}
  },
  sources: [
    {name:"Screener.in",url:"https://www.screener.in/company/NIFTY/",purpose:"Core index price, P/E, EPS history and current P/B"},
    {name:"IndexPE",url:"https://indexpe.in/",purpose:"Independent valuation cross-check"},
    {name:"Nifty Indices Constituents",url:"https://www.niftyindices.com/indices/equity/broad-based-indices",purpose:"Primary constituent universes for breadth"},
    {name:"Yahoo Finance via yfinance",url:"https://finance.yahoo.com/",purpose:"Constituent prices used only for breadth"}
  ],
  disclaimer:"The scores organize observable market evidence for research. They are not buy/sell recommendations, return forecasts, or guarantees of future performance."
};

const SEGMENT_ORDER = ["large","mid","small"];
const SEGMENT_COLORS = {large:"#1f3a5f", mid:"#177d72", small:"#b97717"};
const SEGMENT_NAMES = {large:"Large",mid:"Mid",small:"Small"};

const fmtPct = (v, signed=true) => {
  if (v == null || Number.isNaN(Number(v))) return "--";
  const n = Number(v);
  const sign = signed && n > 0 ? "+" : "";
  return `${sign}${n.toFixed(1)}%`;
};
const fmtPP = v => v == null || Number.isNaN(Number(v)) ? "--" : `${Number(v) > 0 ? "+" : ""}${Number(v).toFixed(1)} pp`;
const fmtX = v => v == null || Number.isNaN(Number(v)) ? "--" : `${Number(v).toFixed(2)}x`;
const fmtNum = (v, d=1) => v == null || Number.isNaN(Number(v)) ? "--" : Number(v).toFixed(d);
const fmtScore = v => v == null || Number.isNaN(Number(v)) ? "--" : `${Math.round(Number(v))}`;
const fmtDate = v => {
  if (!v) return "--";
  const d = new Date(`${v}T00:00:00`);
  return d.toLocaleDateString("en-IN", {day:"2-digit", month:"short", year:"numeric"});
};
const escapeHtml = s => String(s ?? "").replace(/[&<>'"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[c]));
const clamp = (v, a=0, b=100) => Math.max(a, Math.min(b, Number(v)));

async function loadJson(path, fallback=null) {
  try {
    const r = await fetch(path, {cache:"no-store"});
    if (!r.ok) throw new Error(`${r.status}`);
    return await r.json();
  } catch (_) {
    return fallback;
  }
}

function scoreTone(score, type="opportunity") {
  if (score == null) return "muted";
  if (type === "risk") {
    if (score <= 30) return "good";
    if (score <= 50) return "neutral";
    if (score <= 70) return "warn";
    return "bad";
  }
  if (score >= 75) return "good";
  if (score >= 60) return "positive";
  if (score >= 45) return "neutral";
  return "warn";
}

function valueTone(v, inverse=false) {
  if (v == null) return "muted";
  const n = Number(v);
  if (inverse) return n <= 35 ? "good" : n >= 70 ? "bad" : "neutral";
  return n >= 65 ? "good" : n <= 35 ? "bad" : "neutral";
}

function coverageBadge(v) {
  if (v == null) return "";
  const n = Number(v);
  const tone = n >= 95 ? "good" : n >= 80 ? "neutral" : "warn";
  return `<span class="coverage ${tone}">${n.toFixed(0)}% data</span>`;
}

function renderCards(items) {
  const el = document.getElementById("cards");
  el.innerHTML = items.map(x => `
    <article class="cap-card ${escapeHtml(x.key)}">
      <div class="card-top">
        <div>
          <div class="card-name">${escapeHtml(x.label)}</div>
          <div class="card-index">${escapeHtml(x.index_name)}</div>
        </div>
        <div class="score-pair">
          <div class="score-chip ${scoreTone(x.opportunity_score)}">
            <span>Opportunity</span><strong>${fmtScore(x.opportunity_score)}</strong><small>/100</small>
          </div>
          <div class="score-chip ${scoreTone(x.risk_score,"risk")}">
            <span>Risk</span><strong>${fmtScore(x.risk_score)}</strong><small>/100</small>
          </div>
        </div>
      </div>
      <div class="card-labels">
        <span>${escapeHtml(x.opportunity_label || "Awaiting data")}</span>
        <span>${escapeHtml(x.risk_label || "Awaiting data")} risk</span>
      </div>
      <div class="mini-grid">
        <div><span>P/E</span><strong>${fmtX(x.pe)}</strong></div>
        <div><span>Earnings YoY</span><strong>${fmtPct(x.ttm_earnings_growth_yoy_pct)}</strong></div>
        <div><span>&gt;200DMA</span><strong>${fmtPct(x.breadth_above_200dma_pct,false)}</strong></div>
        <div><span>1Y Vol.</span><strong>${fmtPct(x.volatility_1y_pct,false)}</strong></div>
      </div>
      <div class="card-foot">
        ${coverageBadge(x.opportunity_coverage_pct)}
        <span class="breadth-source ${escapeHtml(x.breadth_source_status || "unavailable")}">Breadth: ${escapeHtml(x.breadth_source_status || "unavailable")}</span>
      </div>
    </article>
  `).join("");
}

function renderInsights(items) {
  if (!items.length) return;
  const validOpp = items.filter(x => x.opportunity_score != null);
  const validRisk = items.filter(x => x.risk_score != null);
  const validGrowth = items.filter(x => x.ttm_earnings_growth_yoy_pct != null);
  const validBreadth = items.filter(x => x.breadth_above_200dma_pct != null);

  const bestOpp = validOpp.length ? [...validOpp].sort((a,b)=>b.opportunity_score-a.opportunity_score)[0] : null;
  const lowRisk = validRisk.length ? [...validRisk].sort((a,b)=>a.risk_score-b.risk_score)[0] : null;
  const bestGrowth = validGrowth.length ? [...validGrowth].sort((a,b)=>b.ttm_earnings_growth_yoy_pct-a.ttm_earnings_growth_yoy_pct)[0] : null;
  const broadest = validBreadth.length ? [...validBreadth].sort((a,b)=>b.breadth_above_200dma_pct-a.breadth_above_200dma_pct)[0] : null;

  const paragraphs = [];
  if (bestOpp) paragraphs.push(`<p><strong>${escapeHtml(bestOpp.label)}</strong> currently has the strongest combined opportunity evidence at <strong>${fmtScore(bestOpp.opportunity_score)}/100</strong>. The score is diagnostic: inspect the components before using it for allocation decisions.</p>`);
  if (lowRisk) paragraphs.push(`<p><strong>${escapeHtml(lowRisk.label)}</strong> has the lowest current risk score at <strong>${fmtScore(lowRisk.risk_score)}/100</strong>, based on valuation stretch, volatility, drawdown, breadth and earnings deterioration.</p>`);
  if (bestGrowth) paragraphs.push(`<p>Trailing earnings growth is strongest in <strong>${escapeHtml(bestGrowth.label)}</strong> at <strong>${fmtPct(bestGrowth.ttm_earnings_growth_yoy_pct)}</strong>${bestGrowth.earnings_acceleration_3m_pp != null ? `, with 3-month growth-rate acceleration of <strong>${fmtPP(bestGrowth.earnings_acceleration_3m_pp)}</strong>` : ""}.</p>`);
  if (broadest) paragraphs.push(`<p>Market participation is broadest in <strong>${escapeHtml(broadest.label)}</strong>: <strong>${fmtPct(broadest.breadth_above_200dma_pct,false)}</strong> of covered constituents are above their 200DMA.</p>`);
  if (!paragraphs.length) paragraphs.push(`<p>The v2 methodology is ready. Run the GitHub refresh workflow once to populate the new valuation percentiles, breadth and risk metrics.</p>`);
  document.getElementById("insightText").innerHTML = paragraphs.join("");
}

function metricRow(label, values, formatter, toneFn=null, subFn=null) {
  return `
    <div class="matrix-row">
      <div class="matrix-label">${escapeHtml(label)}</div>
      ${SEGMENT_ORDER.map(k => {
        const item = values.find(x=>x.key===k) || {};
        const val = item.value;
        const tone = toneFn ? toneFn(val, item.raw) : "";
        const sub = subFn ? subFn(item) : "";
        return `<div class="matrix-value ${tone}"><strong>${formatter(val)}</strong>${sub ? `<small>${sub}</small>` : ""}</div>`;
      }).join("")}
    </div>`;
}

function withItems(items, field) {
  return SEGMENT_ORDER.map(k => {
    const x = items.find(y=>y.key===k) || {};
    return {key:k,value:x[field],raw:x};
  });
}

function renderValuation(items) {
  const html = [
    metricRow("P/E", withItems(items,"pe"), fmtX),
    metricRow("P/E 5Y percentile", withItems(items,"pe_5y_percentile_pct"), v=>fmtPct(v,false), v=>valueTone(v,true)),
    metricRow("P/B", withItems(items,"pb"), fmtX),
    metricRow("P/E 5Y median", withItems(items,"pe_5y_median"), fmtX),
    metricRow("Earnings yield", withItems(items,"earnings_yield_pct"), v=>fmtPct(v,false), v=>valueTone(v,false)),
  ].join("");
  document.getElementById("valuationMatrix").innerHTML = matrixHeader() + html;
}

function renderEarnings(items) {
  const html = [
    metricRow("TTM earnings YoY", withItems(items,"ttm_earnings_growth_yoy_pct"), fmtPct, v=>v == null ? "muted" : v >= 10 ? "good" : v < 0 ? "bad" : "neutral"),
    metricRow("3M-ago YoY growth", withItems(items,"earnings_growth_3m_ago_yoy_pct"), fmtPct),
    metricRow("3M acceleration", withItems(items,"earnings_acceleration_3m_pp"), fmtPP, v=>v == null ? "muted" : v > 2 ? "good" : v < -2 ? "bad" : "neutral"),
    metricRow("6M price return", withItems(items,"price_return_6m_pct"), fmtPct),
    metricRow("1Y price return", withItems(items,"price_return_1y_pct"), fmtPct),
  ].join("");
  document.getElementById("earningsMatrix").innerHTML = matrixHeader() + html;
}

function breadthSub(item, field) {
  const raw = item.raw || {};
  const coverageField = field === "breadth_above_50dma_pct" ? "breadth_50_coverage_pct" : "breadth_200_coverage_pct";
  const coverage = raw[coverageField];
  return coverage == null ? "" : `${Number(coverage).toFixed(0)}% coverage`;
}

function renderBreadth(items) {
  const vals50 = withItems(items,"breadth_above_50dma_pct");
  const vals200 = withItems(items,"breadth_above_200dma_pct");
  const html = [
    metricRow("Above 50DMA", vals50, v=>fmtPct(v,false), v=>valueTone(v,false), item=>breadthSub(item,"breadth_above_50dma_pct")),
    metricRow("Above 200DMA", vals200, v=>fmtPct(v,false), v=>valueTone(v,false), item=>breadthSub(item,"breadth_above_200dma_pct")),
    metricRow("Breadth status", SEGMENT_ORDER.map(k=>{const x=items.find(y=>y.key===k)||{};return {key:k,value:x.breadth_source_status,raw:x};}), v=>v ? String(v) : "--"),
  ].join("");
  document.getElementById("breadthMatrix").innerHTML = matrixHeader() + html;
}

function renderQuality(items) {
  const html = [
    metricRow("Earnings yield", withItems(items,"earnings_yield_pct"), v=>fmtPct(v,false)),
    metricRow("Implied ROE proxy", withItems(items,"implied_roe_pct"), v=>fmtPct(v,false), v=>valueTone(v,false)),
    metricRow("Quality score", withItems(items,"quality_score"), v=>v == null ? "--" : `${Math.round(v)}/100`, v=>scoreTone(v)),
    metricRow("Dividend yield", withItems(items,"dividend_yield_pct"), v=>fmtPct(v,false)),
  ].join("");
  document.getElementById("qualityMatrix").innerHTML = matrixHeader() + html;
}

function renderPremium(items) {
  const html = [
    metricRow("P/E premium vs Large", withItems(items,"valuation_premium_vs_large_pct"), fmtPct),
    metricRow("Premium 5Y percentile", withItems(items,"valuation_premium_5y_percentile_pct"), v=>fmtPct(v,false), v=>valueTone(v,true)),
    metricRow("6M rel. return vs Large", withItems(items,"relative_return_6m_vs_large_pp"), fmtPP),
    metricRow("1Y rel. return vs Large", withItems(items,"relative_return_1y_vs_large_pp"), fmtPP),
  ].join("");
  document.getElementById("premiumMatrix").innerHTML = matrixHeader() + html;
}

function renderRisk(items) {
  const html = [
    metricRow("1Y annualised vol.", withItems(items,"volatility_1y_pct"), v=>fmtPct(v,false), v=>v == null ? "muted" : v >= 30 ? "bad" : v >= 22 ? "warn" : "neutral"),
    metricRow("1Y max drawdown", withItems(items,"max_drawdown_1y_pct"), fmtPct, v=>v == null ? "muted" : v <= -30 ? "bad" : v <= -20 ? "warn" : "neutral"),
    metricRow("From 52W high", withItems(items,"drawdown_from_52w_high_pct"), fmtPct),
    metricRow("Risk score", withItems(items,"risk_score"), v=>v == null ? "--" : `${Math.round(v)}/100`, v=>scoreTone(v,"risk")),
  ].join("");
  document.getElementById("riskMatrix").innerHTML = matrixHeader() + html;
}

function matrixHeader() {
  return `<div class="matrix-row matrix-head"><div></div><div>Large</div><div>Mid</div><div>Small</div></div>`;
}

function renderScoreBars(targetId, items, field, type) {
  const target = document.getElementById(targetId);
  target.innerHTML = items.map(x => {
    const v = x[field];
    const width = v == null ? 0 : clamp(v);
    const label = type === "risk" ? (x.risk_label || "--") : (x.opportunity_label || "--");
    return `<div class="score-bar-row">
      <div class="score-bar-label"><strong>${escapeHtml(x.label)}</strong><span>${escapeHtml(label)}</span></div>
      <div class="score-track"><div class="score-fill ${escapeHtml(x.key)} ${scoreTone(v,type)}" style="width:${width}%"></div></div>
      <div class="score-number">${v == null ? "--" : Math.round(v)}</div>
    </div>`;
  }).join("");
}

function renderTable(items) {
  const tbody = document.getElementById("scoreTable");
  tbody.innerHTML = items.map(x => `
    <tr>
      <td><strong>${escapeHtml(x.label)}</strong><small>${escapeHtml(x.index_name)}</small></td>
      <td><span class="score-cell ${scoreTone(x.opportunity_score)}">${x.opportunity_score == null ? "--" : `${Math.round(x.opportunity_score)}/100`}</span></td>
      <td><span class="score-cell ${scoreTone(x.risk_score,"risk")}">${x.risk_score == null ? "--" : `${Math.round(x.risk_score)}/100`}</span></td>
      <td>${fmtX(x.pe)}</td>
      <td>${fmtPct(x.pe_5y_percentile_pct,false)}</td>
      <td>${fmtX(x.pb)}</td>
      <td>${x.key === "large" ? "Benchmark" : fmtPct(x.valuation_premium_vs_large_pct)}</td>
      <td>${x.key === "large" ? "--" : fmtPct(x.valuation_premium_5y_percentile_pct,false)}</td>
      <td>${fmtPct(x.ttm_earnings_growth_yoy_pct)}</td>
      <td>${fmtPP(x.earnings_acceleration_3m_pp)}</td>
      <td>${fmtPct(x.breadth_above_200dma_pct,false)}</td>
      <td>${fmtPct(x.volatility_1y_pct,false)}</td>
      <td>${fmtPct(x.max_drawdown_1y_pct)}</td>
    </tr>
  `).join("");
}

function renderHistory(targetId, history, field, type) {
  const el = document.getElementById(targetId);
  const points = (history?.snapshots || []).filter(p => p[field] && Object.values(p[field]).some(v=>v!=null)).slice(-24);
  if (points.length < 2) {
    el.innerHTML = `<div class="empty-state">V2 history begins after the first successful upgraded refresh. At least two refreshes are needed for a trend line.</div>`;
    return;
  }
  const w = 760, h = 230, padX = 36, padY = 18;
  const x = i => padX + (i / Math.max(1, points.length - 1)) * (w - padX * 2);
  const y = v => h - padY - (clamp(v) / 100) * (h - padY * 2);
  const guides = [0,25,50,75,100].map(v => `<line x1="${padX}" x2="${w-padX}" y1="${y(v)}" y2="${y(v)}" stroke="#e4e7ec" stroke-width="1"/><text x="4" y="${y(v)+4}" font-size="11" fill="#7a8495">${v}</text>`).join("");
  const lines = SEGMENT_ORDER.map(k => {
    const pts = points.map((p,i) => p[field]?.[k] == null ? null : `${x(i)},${y(p[field][k])}`).filter(Boolean).join(" ");
    return pts ? `<polyline points="${pts}" fill="none" stroke="${SEGMENT_COLORS[k]}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>` : "";
  }).join("");
  const dates = [0, Math.floor((points.length-1)/2), points.length-1].map(i => `<text x="${x(i)}" y="${h-1}" text-anchor="middle" font-size="10" fill="#7a8495">${escapeHtml(points[i].as_of || "")}</text>`).join("");
  el.innerHTML = `<svg class="history-svg" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">${guides}${lines}${dates}</svg>
    <div class="history-legend">${SEGMENT_ORDER.map(k=>`<span><i class="legend-dot ${k}"></i>${SEGMENT_NAMES[k]}</span>`).join("")}</div>`;
}

function prettyWeightName(k) {
  return ({
    valuation:"Valuation vs own history",
    relative_valuation:"Relative valuation premium",
    earnings:"Earnings growth & acceleration",
    breadth:"Market breadth",
    quality:"ROE / quality proxy",
    momentum:"Relative momentum",
    relative_premium:"Relative premium",
    volatility:"Realised volatility",
    drawdown:"Maximum drawdown",
    breadth_fragility:"Breadth fragility",
    earnings_deterioration:"Earnings deterioration"
  })[k] || k.replaceAll("_"," ");
}

function renderWeights(data) {
  const opp = data.score_weights?.opportunity || FALLBACK_DATA.score_weights.opportunity;
  const risk = data.score_weights?.risk || FALLBACK_DATA.score_weights.risk;
  const build = obj => Object.entries(obj).map(([k,v])=>`<div class="weight-row"><span>${escapeHtml(prettyWeightName(k))}</span><strong>${Number(v).toFixed(0)}%</strong></div>`).join("");
  document.getElementById("opportunityWeights").innerHTML = build(opp);
  document.getElementById("riskWeights").innerHTML = build(risk);
}

function renderSources(data) {
  const st = data.source_status || {};
  const componentCards = Object.entries(st.components || {}).map(([k,v]) => `
    <div class="source-card health ${escapeHtml(v.status || "unknown")}">
      <strong>${escapeHtml(k.replaceAll("_"," "))}</strong>
      <span class="health-pill">${escapeHtml(v.status || "unknown")}</span>
      <p>${escapeHtml(v.message || "")}</p>
    </div>`);
  const sourceCards = (data.sources || []).map(s => `
    <div class="source-card">
      <strong>${escapeHtml(s.name)}</strong>
      <p>${escapeHtml(s.purpose)}</p>
      <a href="${escapeHtml(s.url)}" target="_blank" rel="noreferrer">Source</a>
    </div>`);
  document.getElementById("sourceDetails").innerHTML = [
    `<div class="source-card summary"><strong>Refresh status</strong><span class="health-pill">${escapeHtml(st.status || "unknown")}</span><p>${escapeHtml(st.message || "Unknown")}</p><p>Generated: ${escapeHtml(data.generated_at || "--")}</p></div>`,
    ...componentCards,
    ...sourceCards
  ].join("");
}

function renderStatus(data) {
  document.getElementById("asOf").textContent = fmtDate(data.as_of);
  document.getElementById("methodVersion").textContent = `v${data.methodology_version || "--"}`;
  const st = data.source_status?.status || "unknown";
  const dot = document.getElementById("statusDot");
  dot.className = `status-dot ${st}`;
  const label = st === "live" ? "Live refresh successful" : st === "partial" ? "Core live; some data partial" : st === "cached" ? "Showing last good data" : "Awaiting full refresh";
  document.getElementById("sourceStatus").textContent = label;
  document.getElementById("disclaimer").textContent = data.disclaimer || FALLBACK_DATA.disclaimer;
}

function render(data, history) {
  const items = (data.indices || []).sort((a,b)=>SEGMENT_ORDER.indexOf(a.key)-SEGMENT_ORDER.indexOf(b.key));
  renderStatus(data);
  renderCards(items);
  renderInsights(items);
  renderValuation(items);
  renderEarnings(items);
  renderBreadth(items);
  renderQuality(items);
  renderPremium(items);
  renderRisk(items);
  renderScoreBars("opportunityBars", items, "opportunity_score", "opportunity");
  renderScoreBars("riskBars", items, "risk_score", "risk");
  renderTable(items);
  renderHistory("opportunityHistory", history, "opportunity_scores", "opportunity");
  renderHistory("riskHistory", history, "risk_scores", "risk");
  renderWeights(data);
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
