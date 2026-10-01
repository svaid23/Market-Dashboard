const FALLBACK_DATA = {
  as_of: "2026-09-30",
  generated_at: "2026-09-30T17:30:00+00:00",
  methodology_version: "3.0",
  source_status: {status:"seed", used_cached_data:true, message:"Run the refresh workflow once to populate v3 data.", components:{}},
  indices: [
    {key:"large",label:"Large Cap",index_name:"NIFTY 50",pe:19.36,pb:null,pe_5y_median:22.07,pe_vs_5y_median_pct:-12.3,profit_growth_weighted_pct:null,breadth_above_200dma_pct:null,volatility_1y_pct:13.34},
    {key:"mid",label:"Mid Cap",index_name:"NIFTY MIDCAP 100",pe:27.98,pb:null,pe_5y_median:29.52,pe_vs_5y_median_pct:-5.2,profit_growth_weighted_pct:null,breadth_above_200dma_pct:null,volatility_1y_pct:16.76},
    {key:"small",label:"Small Cap",index_name:"NIFTY SMALLCAP 250",pe:34.45,pb:null,pe_5y_median:28.30,pe_vs_5y_median_pct:21.7,profit_growth_weighted_pct:null,breadth_above_200dma_pct:null,volatility_1y_pct:17.35}
  ],
  score_weights:{
    opportunity:{valuation:25,relative_valuation:15,earnings:25,breadth:15,quality:10,momentum:10},
    risk:{valuation:20,relative_premium:15,volatility:25,beta:15,breadth_fragility:10,earnings_deterioration:15}
  },
  sources:[],
  disclaimer:"The scores organize observable market evidence for research. They are not buy/sell recommendations, return forecasts, or guarantees of future performance."
};

const SEGMENT_ORDER=["large","mid","small"];
const SEGMENT_COLORS={large:"#1f3a5f",mid:"#177d72",small:"#b97717"};
const SEGMENT_NAMES={large:"Large",mid:"Mid",small:"Small"};
const num=v=>v==null||Number.isNaN(Number(v))?null:Number(v);
const fmtPct=(v,signed=true)=>{const n=num(v);if(n==null)return"--";return`${signed&&n>0?"+":""}${n.toFixed(1)}%`;};
const fmtPP=v=>{const n=num(v);if(n==null)return"--";return`${n>0?"+":""}${n.toFixed(1)} pp`;};
const fmtX=v=>{const n=num(v);return n==null?"--":`${n.toFixed(2)}x`;};
const fmtScore=v=>{const n=num(v);return n==null?"--":`${Math.round(n)}`;};
const fmtDate=v=>{if(!v)return"--";const d=new Date(`${v}T00:00:00`);return d.toLocaleDateString("en-IN",{day:"2-digit",month:"short",year:"numeric"});};
const esc=s=>String(s??"").replace(/[&<>'"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[c]));
const clamp=(v,a=0,b=100)=>Math.max(a,Math.min(b,Number(v)));

async function loadJson(path,fallback=null){try{const r=await fetch(path,{cache:"no-store"});if(!r.ok)throw new Error(String(r.status));return await r.json();}catch(_){return fallback;}}
function scoreTone(score,type="opportunity"){const n=num(score);if(n==null)return"muted";if(type==="risk"){if(n<=30)return"good";if(n<=50)return"neutral";if(n<=70)return"warn";return"bad";}if(n>=75)return"good";if(n>=60)return"positive";if(n>=45)return"neutral";return"warn";}
function valueTone(v,inverse=false){const n=num(v);if(n==null)return"muted";if(inverse)return n<=35?"good":n>=70?"bad":"neutral";return n>=65?"good":n<=35?"bad":"neutral";}
function coverageBadge(v){const n=num(v);if(n==null)return"";const tone=n>=95?"good":n>=80?"neutral":"warn";return`<span class="coverage ${tone}">${n.toFixed(0)}% score data</span>`;}
function matrixHeader(){return`<div class="matrix-row matrix-head"><div></div><div>Large</div><div>Mid</div><div>Small</div></div>`;}
function withItems(items,field){return SEGMENT_ORDER.map(k=>{const x=items.find(y=>y.key===k)||{};return{key:k,value:x[field],raw:x};});}
function metricRow(label,values,formatter,toneFn=null,subFn=null){return`<div class="matrix-row"><div class="matrix-label">${esc(label)}</div>${SEGMENT_ORDER.map(k=>{const it=values.find(x=>x.key===k)||{};const tone=toneFn?toneFn(it.value,it.raw):"";const sub=subFn?subFn(it):"";return`<div class="matrix-value ${tone}"><strong>${formatter(it.value)}</strong>${sub?`<small>${sub}</small>`:""}</div>`;}).join("")}</div>`;}

function renderCards(items){document.getElementById("cards").innerHTML=items.map(x=>`<article class="cap-card ${esc(x.key)}">
  <div class="card-top"><div><div class="card-name">${esc(x.label)}</div><div class="card-index">${esc(x.index_name)}</div></div>
  <div class="score-pair"><div class="score-chip ${scoreTone(x.opportunity_score)}"><span>Opportunity</span><strong>${fmtScore(x.opportunity_score)}</strong><small>/100</small></div><div class="score-chip ${scoreTone(x.risk_score,"risk")}"><span>Risk</span><strong>${fmtScore(x.risk_score)}</strong><small>/100</small></div></div></div>
  <div class="card-labels"><span>${esc(x.opportunity_label||"Awaiting data")}</span><span>${esc(x.risk_label||"Awaiting data")} risk</span></div>
  <div class="mini-grid"><div><span>P/E</span><strong>${fmtX(x.pe)}</strong></div><div><span>Qtr profit YoY</span><strong>${fmtPct(x.profit_growth_weighted_pct)}</strong></div><div><span>&gt;200DMA</span><strong>${fmtPct(x.breadth_above_200dma_pct,false)}</strong></div><div><span>1Y Vol.</span><strong>${fmtPct(x.volatility_1y_pct,false)}</strong></div></div>
  <div class="card-foot">${coverageBadge(x.opportunity_coverage_pct)}<span class="breadth-source ${esc(x.breadth_source_status||"unavailable")}">Breadth: ${esc(x.breadth_source_status||"unavailable")}</span></div>
</article>`).join("");}

function renderInsights(items,data){
  const validOpp=items.filter(x=>num(x.opportunity_score)!=null), validRisk=items.filter(x=>num(x.risk_score)!=null), validGrowth=items.filter(x=>num(x.profit_growth_weighted_pct)!=null), validProfitBreadth=items.filter(x=>num(x.profit_positive_mcap_breadth_pct)!=null);
  const bestOpp=validOpp.length?[...validOpp].sort((a,b)=>b.opportunity_score-a.opportunity_score)[0]:null;
  const lowRisk=validRisk.length?[...validRisk].sort((a,b)=>a.risk_score-b.risk_score)[0]:null;
  const bestGrowth=validGrowth.length?[...validGrowth].sort((a,b)=>b.profit_growth_weighted_pct-a.profit_growth_weighted_pct)[0]:null;
  const bestProfitBreadth=validProfitBreadth.length?[...validProfitBreadth].sort((a,b)=>b.profit_positive_mcap_breadth_pct-a.profit_positive_mcap_breadth_pct)[0]:null;
  const p=[];
  if(bestOpp)p.push(`<p><strong>${esc(bestOpp.label)}</strong> has the strongest current opportunity evidence at <strong>${fmtScore(bestOpp.opportunity_score)}/100</strong>. Use the components below to see why; the score is not a buy signal.</p>`);
  if(lowRisk)p.push(`<p><strong>${esc(lowRisk.label)}</strong> has the lowest risk score at <strong>${fmtScore(lowRisk.risk_score)}/100</strong>.</p>`);
  if(bestGrowth)p.push(`<p>The strongest current constituent-profit pulse is in <strong>${esc(bestGrowth.label)}</strong> at <strong>${fmtPct(bestGrowth.profit_growth_weighted_pct)}</strong> market-cap-weighted YoY growth.</p>`);
  if(bestProfitBreadth)p.push(`<p>Profit participation is broadest in <strong>${esc(bestProfitBreadth.label)}</strong>: companies representing <strong>${fmtPct(bestProfitBreadth.profit_positive_mcap_breadth_pct,false)}</strong> of covered market cap reported positive YoY quarterly profit growth.</p>`);
  if(data.source_status?.status==="partial")p.push(`<p><strong>Data health:</strong> the core dashboard refreshed, but at least one optional/slow-moving source used a reference or cached value. See Data Health below.</p>`);
  if(!p.length)p.push(`<p>Run the refresh workflow once to populate the v3 framework.</p>`);
  document.getElementById("insightText").innerHTML=p.join("");
}

function renderValuation(items){document.getElementById("valuationMatrix").innerHTML=matrixHeader()+[
  metricRow("P/E",withItems(items,"pe"),fmtX),
  metricRow("5Y median P/E",withItems(items,"pe_5y_median"),fmtX),
  metricRow("Vs 5Y median",withItems(items,"pe_vs_5y_median_pct"),fmtPct,v=>{const n=num(v);return n==null?"muted":n<=-10?"good":n>=15?"bad":"neutral";}),
  metricRow("P/B",withItems(items,"pb"),fmtX),
  metricRow("Earnings yield",withItems(items,"earnings_yield_pct"),v=>fmtPct(v,false))
].join("");}

function renderEarnings(items){document.getElementById("earningsMatrix").innerHTML=matrixHeader()+[
  metricRow("Mcap-wtd Qtr profit YoY",withItems(items,"profit_growth_weighted_pct"),fmtPct,v=>{const n=num(v);return n==null?"muted":n>=15?"good":n<0?"bad":"neutral";}),
  metricRow("Positive-profit mcap",withItems(items,"profit_positive_mcap_breadth_pct"),v=>fmtPct(v,false),v=>valueTone(v,false)),
  metricRow("Positive constituents",withItems(items,"profit_positive_constituent_pct"),v=>fmtPct(v,false),v=>valueTone(v,false)),
  metricRow("Constituent coverage",withItems(items,"earnings_constituent_coverage_pct"),v=>fmtPct(v,false)),
  metricRow("Long-term earnings CAGR",withItems(items,"long_term_earnings_cagr_pct"),fmtPct)
].join("");}

function breadthSub(it,field){const r=it.raw||{};const c=field==="breadth_above_50dma_pct"?r.breadth_50_coverage_pct:r.breadth_200_coverage_pct;return c==null?"":`${Number(c).toFixed(0)}% coverage`;}
function renderBreadth(items){const a=withItems(items,"breadth_above_50dma_pct"),b=withItems(items,"breadth_above_200dma_pct");document.getElementById("breadthMatrix").innerHTML=matrixHeader()+[
  metricRow("Above 50DMA",a,v=>fmtPct(v,false),v=>valueTone(v,false),it=>breadthSub(it,"breadth_above_50dma_pct")),
  metricRow("Above 200DMA",b,v=>fmtPct(v,false),v=>valueTone(v,false),it=>breadthSub(it,"breadth_above_200dma_pct")),
  metricRow("Breadth status",SEGMENT_ORDER.map(k=>{const x=items.find(y=>y.key===k)||{};return{key:k,value:x.breadth_source_status,raw:x};}),v=>v?String(v):"--")
].join("");}
function renderQuality(items){document.getElementById("qualityMatrix").innerHTML=matrixHeader()+[
  metricRow("Earnings yield",withItems(items,"earnings_yield_pct"),v=>fmtPct(v,false)),
  metricRow("Implied ROE proxy",withItems(items,"implied_roe_pct"),v=>fmtPct(v,false),v=>valueTone(v,false)),
  metricRow("Quality score",withItems(items,"quality_score"),v=>num(v)==null?"--":`${Math.round(v)}/100`,v=>scoreTone(v)),
  metricRow("Dividend yield",withItems(items,"dividend_yield_pct"),v=>fmtPct(v,false))
].join("");}
function renderPremium(items){document.getElementById("premiumMatrix").innerHTML=matrixHeader()+[
  metricRow("Current P/E premium",withItems(items,"valuation_premium_vs_large_pct"),fmtPct),
  metricRow("Normal 5Y premium",withItems(items,"normal_5y_premium_vs_large_pct"),fmtPct),
  metricRow("Excess vs normal",withItems(items,"excess_premium_vs_normal_pp"),fmtPP,v=>{const n=num(v);return n==null?"muted":n<=0?"good":n>=15?"bad":"neutral";}),
  metricRow("1Y rel. return vs Large",withItems(items,"relative_return_1y_vs_large_pp"),fmtPP)
].join("");}
function renderRisk(items){document.getElementById("riskMatrix").innerHTML=matrixHeader()+[
  metricRow("1Y annualised vol.",withItems(items,"volatility_1y_pct"),v=>fmtPct(v,false),v=>{const n=num(v);return n==null?"muted":n>=25?"bad":n>=18?"warn":"neutral";}),
  metricRow("Beta vs Nifty 50",withItems(items,"beta_1y"),v=>{const n=num(v);return n==null?"--":n.toFixed(2);}),
  metricRow("From 52W high",withItems(items,"drawdown_from_52w_high_pct"),fmtPct),
  metricRow("Risk score",withItems(items,"risk_score"),v=>num(v)==null?"--":`${Math.round(v)}/100`,v=>scoreTone(v,"risk"))
].join("");}

function renderScoreBars(targetId,items,field,type){document.getElementById(targetId).innerHTML=items.map(x=>{const v=num(x[field]);const width=v==null?0:clamp(v);const label=type==="risk"?(x.risk_label||"--"):(x.opportunity_label||"--");return`<div class="score-bar-row"><div class="score-bar-label"><strong>${esc(x.label)}</strong><span>${esc(label)}</span></div><div class="score-track"><div class="score-fill ${esc(x.key)} ${scoreTone(v,type)}" style="width:${width}%"></div></div><div class="score-number">${v==null?"--":Math.round(v)}</div></div>`;}).join("");}
function renderTable(items){document.getElementById("scoreTable").innerHTML=items.map(x=>`<tr>
  <td><strong>${esc(x.label)}</strong><small>${esc(x.index_name)}</small></td><td><span class="score-cell ${scoreTone(x.opportunity_score)}">${num(x.opportunity_score)==null?"--":`${Math.round(x.opportunity_score)}/100`}</span></td><td><span class="score-cell ${scoreTone(x.risk_score,"risk")}">${num(x.risk_score)==null?"--":`${Math.round(x.risk_score)}/100`}</span></td>
  <td>${fmtX(x.pe)}</td><td>${fmtPct(x.pe_vs_5y_median_pct)}</td><td>${fmtX(x.pb)}</td><td>${x.key==="large"?"Benchmark":fmtPct(x.valuation_premium_vs_large_pct)}</td><td>${x.key==="large"?"--":fmtPP(x.excess_premium_vs_normal_pp)}</td><td>${fmtPct(x.profit_growth_weighted_pct)}</td><td>${fmtPct(x.profit_positive_mcap_breadth_pct,false)}</td><td>${fmtPct(x.breadth_above_200dma_pct,false)}</td><td>${fmtPct(x.volatility_1y_pct,false)}</td><td>${num(x.beta_1y)==null?"--":Number(x.beta_1y).toFixed(2)}</td>
</tr>`).join("");}

function renderHistory(targetId,history,field){const el=document.getElementById(targetId);const pts=(history?.snapshots||[]).filter(p=>p[field]&&Object.values(p[field]).some(v=>v!=null)).slice(-24);if(pts.length<2){el.innerHTML=`<div class="empty-state">V3 history will appear after at least two successful refreshes.</div>`;return;}const w=760,h=230,padX=36,padY=18,x=i=>padX+(i/Math.max(1,pts.length-1))*(w-padX*2),y=v=>h-padY-(clamp(v)/100)*(h-padY*2);const guides=[0,25,50,75,100].map(v=>`<line x1="${padX}" x2="${w-padX}" y1="${y(v)}" y2="${y(v)}" stroke="#e4e7ec"/><text x="4" y="${y(v)+4}" font-size="11" fill="#7a8495">${v}</text>`).join("");const lines=SEGMENT_ORDER.map(k=>{const p=pts.map((d,i)=>d[field]?.[k]==null?null:`${x(i)},${y(d[field][k])}`).filter(Boolean).join(" ");return p?`<polyline points="${p}" fill="none" stroke="${SEGMENT_COLORS[k]}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>`:"";}).join("");const dates=[0,Math.floor((pts.length-1)/2),pts.length-1].map(i=>`<text x="${x(i)}" y="${h-1}" text-anchor="middle" font-size="10" fill="#7a8495">${esc(pts[i].as_of||"")}</text>`).join("");el.innerHTML=`<svg class="history-svg" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">${guides}${lines}${dates}</svg><div class="history-legend">${SEGMENT_ORDER.map(k=>`<span><i class="legend-dot ${k}"></i>${SEGMENT_NAMES[k]}</span>`).join("")}</div>`;}
function prettyWeightName(k){return({valuation:"Valuation vs own history",relative_valuation:"Relative valuation premium",earnings:"Current profit pulse",breadth:"Market breadth",quality:"ROE / quality proxy",momentum:"Relative momentum",relative_premium:"Relative premium",volatility:"Realised volatility",beta:"Beta",breadth_fragility:"Breadth fragility",earnings_deterioration:"Profit deterioration"})[k]||k.replaceAll("_"," ");}
function renderWeights(data){const build=o=>Object.entries(o||{}).map(([k,v])=>`<div class="weight-row"><span>${esc(prettyWeightName(k))}</span><strong>${Number(v).toFixed(0)}%</strong></div>`).join("");document.getElementById("opportunityWeights").innerHTML=build(data.score_weights?.opportunity||FALLBACK_DATA.score_weights.opportunity);document.getElementById("riskWeights").innerHTML=build(data.score_weights?.risk||FALLBACK_DATA.score_weights.risk);}
function renderSources(data){const st=data.source_status||{};const comps=Object.entries(st.components||{}).map(([k,v])=>`<div class="source-card health ${esc(v.status||"unknown")}"><strong>${esc(k.replaceAll("_"," "))}</strong><span class="health-pill">${esc(v.status||"unknown")}</span><p>${esc(v.message||"")}</p></div>`);const sources=(data.sources||[]).map(s=>`<div class="source-card"><strong>${esc(s.name)}</strong><p>${esc(s.purpose)}</p><a href="${esc(s.url)}" target="_blank" rel="noreferrer">Source</a></div>`);document.getElementById("sourceDetails").innerHTML=[`<div class="source-card summary"><strong>Refresh status</strong><span class="health-pill">${esc(st.status||"unknown")}</span><p>${esc(st.message||"Unknown")}</p><p>Generated: ${esc(data.generated_at||"--")}</p></div>`,...comps,...sources].join("");}
function renderStatus(data){document.getElementById("asOf").textContent=fmtDate(data.as_of);document.getElementById("methodVersion").textContent=`v${data.methodology_version||"--"}`;const st=data.source_status?.status||"unknown";document.getElementById("statusDot").className=`status-dot ${st}`;document.getElementById("sourceStatus").textContent=st==="live"?"Live refresh successful":st==="partial"?"Core live; optional data partial":st==="cached"?"Showing last good data":"Awaiting full refresh";document.getElementById("disclaimer").textContent=data.disclaimer||FALLBACK_DATA.disclaimer;}
function render(data,history){const items=(data.indices||[]).sort((a,b)=>SEGMENT_ORDER.indexOf(a.key)-SEGMENT_ORDER.indexOf(b.key));renderStatus(data);renderCards(items);renderInsights(items,data);renderValuation(items);renderEarnings(items);renderBreadth(items);renderQuality(items);renderPremium(items);renderRisk(items);renderScoreBars("opportunityBars",items,"opportunity_score","opportunity");renderScoreBars("riskBars",items,"risk_score","risk");renderTable(items);renderHistory("opportunityHistory",history,"opportunity_scores");renderHistory("riskHistory",history,"risk_scores");renderWeights(data);renderSources(data);}

document.getElementById("methodBtn").addEventListener("click",()=>{const el=document.getElementById("methodology");el.classList.toggle("hidden");if(!el.classList.contains("hidden"))el.scrollIntoView({behavior:"smooth",block:"start"});});
(async function(){const data=await loadJson("data/latest.json",FALLBACK_DATA);const history=await loadJson("data/history.json",{snapshots:[]});render(data,history);})();
