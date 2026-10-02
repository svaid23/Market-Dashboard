const $=id=>document.getElementById(id);
const segOrder=['large','mid','small'];
const f=(v,d=1)=>v==null||Number.isNaN(Number(v))?'--':Number(v).toFixed(d);
const pct=(v,s=true)=>v==null?'--':`${s&&Number(v)>0?'+':''}${f(v)}%`;
const pp=v=>v==null?'--':`${Number(v)>0?'+':''}${f(v)} pp`;
const x=v=>v==null?'--':`${f(v,2)}x`;
const cr=v=>v==null?'--':`${Number(v)>=0?'+':''}₹${Math.abs(Number(v)).toLocaleString('en-IN',{maximumFractionDigits:0})} Cr`;
function tonePct(v){if(v==null)return'tone-neutral';if(v<=25)return'tone-good';if(v>=75)return'tone-bad';if(v>=60)return'tone-warn';return'tone-neutral'}
function condTone(s){s=(s||'').toLowerCase();if(s.includes('below'))return'cheap';if(s.includes('above')||s.includes('well above'))return'expensive';return'fair'}
function stanceClass(s){s=(s||'').toLowerCase();if(s.includes('constructive')||s==='attractive')return'pos';if(s.includes('cautious')||s==='expensive')return'caut';if(s.includes('mixed'))return'warn';return'neutral'}
function card(k,s){return `<article class="cap-card ${k}"><div class="cap-top"><div><div class="cap-name">${s.label}</div><div class="index-name">${s.index}</div></div><div class="condition ${condTone(s.valuation_condition)}">${s.valuation_condition||'--'}</div></div><div class="kpis"><div><span>P/E</span><strong>${f(s.pe,2)}x</strong></div><div><span>5Y P/E percentile</span><strong class="${tonePct(s.pe_5y_percentile)}">${f(s.pe_5y_percentile,0)}%</strong></div><div><span>5Y P/B percentile</span><strong class="${tonePct(s.pb_5y_percentile)}">${f(s.pb_5y_percentile,0)}%</strong></div><div><span>ROE proxy</span><strong>${pct(s.roe_proxy_pct,false)}</strong></div></div></article>`}
function metricRow(label,a,b){return `<div class="metric-row"><div>${label}</div><div>${a}</div><div>${b}</div></div>`}
function countChip(label,n,kind='neutral'){return `<span class="count-chip ${kind}"><b>${n??0}</b> ${label}</span>`}
function consensusCard(k,s){
  const v=s?.valuation_counts||{}, d=s?.direction_counts||{};
  const cap={large:'Large Cap',mid:'Mid Cap',small:'Small Cap'}[k];
  return `<article class="consensus-card ${k}">
    <div class="consensus-card-head"><div><div class="eyebrow">${cap}</div><h3>External view</h3></div><span class="coverage">${s?.current_sources??0}/${s?.total_sources??10} current docs</span></div>
    <div class="consensus-result"><span>Direction</span><strong class="stance ${stanceClass(s?.direction_label)}">${s?.direction_label||'--'}</strong></div>
    <div class="counts">${countChip('constructive',d['Constructive'],'pos')}${countChip('neutral',d['Neutral/Selective'],'neutral')}${countChip('cautious',d['Cautious'],'caut')}${countChip('no view',d['No explicit view'],'muted')}</div>
    <div class="consensus-result secondary"><span>Valuation view</span><strong class="stance ${stanceClass(s?.valuation_label)}">${s?.valuation_label||'--'}</strong></div>
    <div class="counts">${countChip('attractive',v['Attractive'],'pos')}${countChip('fair',Number(v['Fair']||0)+Number(v['Mixed/Fair']||0),'neutral')}${countChip('expensive',v['Expensive'],'caut')}${countChip('no view',v['No explicit view'],'muted')}</div>
  </article>`;
}
function sourceViewCell(obj){
  const val=obj?.valuation?.view||'No explicit view';
  const dir=obj?.direction?.view||'No explicit view';
  return `<div class="dual-view"><span class="stance ${stanceClass(val)}">V: ${val}</span><span class="stance ${stanceClass(dir)}">D: ${dir}</span></div>`;
}
function sourceEvidence(c){
  for(const k of ['large','mid','small']){
    const x=c?.[k]||{};
    const e=x?.valuation?.evidence||x?.direction?.evidence;
    if(e)return e;
  }
  return c.error||'No explicit current cap-level wording found.';
}
function render(data){
  $('asOf').textContent=data.as_of||'--'; $('version').textContent=data.methodology_version||'--';
  const health=Object.values(data.source_health||{}); const live=health.filter(x=>x.status==='live').length; const bad=health.filter(x=>['cached','unavailable'].includes(x.status)).length;
  const status=bad===0?'live':live>0?'partial':'cached'; $('statusDot').className=`dot ${status}`; $('statusText').textContent=status==='live'?'Sources healthy':status==='partial'?'Some sources unavailable':'Using cached/seed data';
  $('cards').innerHTML=segOrder.map(k=>card(k,data.segments[k])).join('');
  const g=data.gsec_10y?.yield_pct;
  $('coreTable').innerHTML=segOrder.map(k=>{const s=data.segments[k];return `<tr><td><b>${s.label}</b><br><small>${s.index}</small></td><td>${f(s.pe,2)}x</td><td class="${tonePct(s.pe_5y_percentile)}">${f(s.pe_5y_percentile,0)}%</td><td>${f(s.pb,2)}x</td><td class="${tonePct(s.pb_5y_percentile)}">${f(s.pb_5y_percentile,0)}%</td><td>${k==='large'?'—':x(s.relative_pe_vs_large)}</td><td class="${tonePct(s.relative_premium_5y_percentile)}">${k==='large'?'—':f(s.relative_premium_5y_percentile,0)+'%'}</td><td>${pct(s.earnings_yield_pct,false)}</td><td>${g==null?'--':pct(g,false)}</td><td>${pp(s.equity_bond_spread_pp)}</td><td>${pct(s.earnings_growth_1y_pct)}</td><td>${pct(s.earnings_cagr_3y_pct)}</td><td>${pct(s.roe_proxy_pct,false)}</td><td>${pct(s.dividend_yield,false)}</td></tr>`}).join('');
  $('trendGrid').innerHTML=segOrder.map(k=>{const s=data.segments[k],t=s.trend||{};return metricRow(s.label,`<strong>${pct(t.vs_50dma_pct)}</strong><small>vs 50DMA</small>`,`<strong>${pct(t.vs_200dma_pct)}</strong><small>vs 200DMA</small>`)}).join('');
  $('breadthGrid').innerHTML=segOrder.map(k=>{const s=data.segments[k],b=s.breadth||{},proxy=b.proxy_index||'proxy';const label=k==='large'?'Large proxy':s.label;return metricRow(label,`<strong>${pct(b.above_50dma_pct,false)}</strong><small>>50DMA · ${proxy}</small>`,`<strong>${pct(b.above_200dma_pct,false)}</strong><small>>200DMA · ${proxy}</small>`)}).join('');
  $('referenceGrid').innerHTML=Object.entries(data.references||{}).map(([k,r])=>metricRow(k==='nifty50'?'Nifty 50':'Nifty 500',`<strong>${pct(r.vs_50dma_pct)}</strong><small>vs 50DMA · breadth ${pct(r.breadth_50dma_pct,false)}</small>`,`<strong>${pct(r.vs_200dma_pct)}</strong><small>vs 200DMA · breadth ${pct(r.breadth_200dma_pct,false)}</small>`)).join('');
  const fd=data.fii_dii||{}; const rows=fd.rows_available??0; const w20=fd.window_20d_complete!==false;
  $('flowGrid').innerHTML=`<div class="flow-card"><h3>FII / FPI</h3><div class="big ${Number(fd.latest_fii)>=0?'tone-good':'tone-bad'}">${cr(fd.latest_fii)}</div><div class="sub"><div><span>5 trading days</span><b>${cr(fd.fii_5d)}</b></div><div><span>20 trading days</span><b>${w20?cr(fd.fii_20d):'--'}</b>${!w20?`<small>only ${rows} rows available</small>`:''}</div></div></div><div class="flow-card"><h3>DII</h3><div class="big ${Number(fd.latest_dii)>=0?'tone-good':'tone-bad'}">${cr(fd.latest_dii)}</div><div class="sub"><div><span>5 trading days</span><b>${cr(fd.dii_5d)}</b></div><div><span>20 trading days</span><b>${w20?cr(fd.dii_20d):'--'}</b>${!w20?`<small>only ${rows} rows available</small>`:''}</div></div></div>`;

  // v4.2 object; gracefully handle old v4.1 list until first refresh completes.
  const ec=data.external_consensus||{};
  if(Array.isArray(ec)){
    $('consensusCards').innerHTML='<div class="legacy-note">Run the v4.2 refresh to populate the new 10-source consensus engine.</div>';
    $('consensusTable').innerHTML=ec.map(c=>`<tr><td><b>${c.name}</b></td><td colspan="3">Legacy v4.1 view</td><td>${c.note||''}</td><td>${c.reliability||'--'}</td></tr>`).join('');
  }else{
    $('consensusCards').innerHTML=segOrder.map(k=>consensusCard(k,ec.summary?.[k]||{})).join('');
    $('consensusTable').innerHTML=(ec.sources||[]).map(c=>`<tr><td><b>${c.name}</b><br><small>${c.report_date||'undated'} · ${c.age_bucket||'--'}</small></td><td>${sourceViewCell(c.large)}</td><td>${sourceViewCell(c.mid)}</td><td>${sourceViewCell(c.small)}</td><td>${sourceEvidence(c)}</td><td>${c.reliability||'--'}</td></tr>`).join('');
  }
  $('healthGrid').innerHTML=Object.entries(data.source_health||{}).map(([k,h])=>`<div class="health-card"><strong>${k.replaceAll('_',' ')}</strong><span class="health-pill ${h.status||''}">${h.status||'--'}</span></div>`).join('');
  $('disclaimer').textContent=data.disclaimer||'';
}
fetch('data/latest.json',{cache:'no-store'}).then(r=>r.json()).then(render).catch(e=>{$('statusText').textContent='Could not load data';console.error(e)});
