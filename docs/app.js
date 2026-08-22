const $ = (q, root=document) => root.querySelector(q);
const $$ = (q, root=document) => [...root.querySelectorAll(q)];

const CATEGORY = {
  KR: { short: '국장', dir: 'kr' },
  KR_ETF: { short: '국장 ETF', dir: 'kr-etf' },
  US: { short: '미장', dir: 'us' },
  US_ETF: { short: '미장 ETF', dir: 'us-etf' },
};

const IS_ANDROID_APP = location.hostname === 'localhost' || location.protocol === 'capacitor:';
const CONFIGURED_DATA_ORIGIN = String(window.DTC_DATA_ORIGIN || '').trim().replace(/\/$/, '');
const DATA_BASE = (location.hostname.endsWith('github.io') || IS_ANDROID_APP)
  ? (CONFIGURED_DATA_ORIGIN || '.')
  : '.';
const DEFAULT_TOP_N = 20;
const DEFAULT_EQUITY_SIZE_MIN = 100_000_000_000_000;
const DEFAULT_ETF_SIZE_MIN = 0;
const CAP_FILTER_PRESETS = {
  equity: [
    [10_000_000_000_000, '10조 이상'],
    [50_000_000_000_000, '50조 이상'],
    [100_000_000_000_000, '100조 이상'],
    [500_000_000_000_000, '500조 이상'],
    [1_000_000_000_000_000, '1000조 이상'],
  ],
  etf: [
    [0, '전체'],
    [100_000_000_000, '0.1조 이상'],
    [500_000_000_000, '0.5조 이상'],
    [1_000_000_000_000, '1조 이상'],
    [5_000_000_000_000, '5조 이상'],
  ],
};
const QUIZ_WINDOW_DAYS = 90;
const QUIZ_HIDDEN_DAYS = 30;
const QUIZ_MIN_MARKET_SIZE = 100_000_000_000_000;
const QUIZ_SHARDS = ['kr', 'kr-etf', 'us', 'us-etf'];

const OPINION_ORDER = { BUY_S:0, BUY_A:1, BUY_B:2, BUY_C:3, HOLD:4, SELL:5 };
const OPINION_TEXT = { BUY_S:'매수S', BUY_A:'매수A', BUY_B:'매수B', BUY_C:'매수C', HOLD:'Hold', SELL:'매도' };

const state = {
  mode: 'supertrend',
  category: 'KR',
  data: { KR:null, KR_ETF:null, US:null, US_ETF:null },
  query: '',
  marketSizeMin: { equity: DEFAULT_EQUITY_SIZE_MIN, etf: DEFAULT_ETF_SIZE_MIN },
  filtered: [],
  sellItems: [],
  sortMode: 'default',
  detailCache: new Map(),
  cardObserver: null,
  quiz: { pool:null, detailCache:new Map(), question:null, answered:false, loading:false, number:0 },
};

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>'"]/g, (c) => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', "'":'&#39;', '"':'&quot;' }[c]));
}

function dataUrl(path, force=false) {
  const clean = String(path).replace(/^\.\//, '').replace(/^\//, '');
  const base = DATA_BASE === '.' ? '.' : DATA_BASE.replace(/\/$/, '');
  const url = `${base}/${clean}`;
  return force ? `${url}${url.includes('?') ? '&' : '?'}ts=${Date.now()}` : url;
}

function currentData() { return state.data[state.category]; }

function switchAnalysisMode(mode) {
  const next = ['supertrend', 'quiz'].includes(mode) ? mode : 'supertrend';
  state.mode = next;
  const strategyView = $('#supertrendModeView');
  const quizView = $('#quizModeView');
  if (strategyView) strategyView.hidden = next !== 'supertrend';
  if (quizView) quizView.hidden = next !== 'quiz';
  const select = $('#analysisMode');
  if (select && select.value !== next) select.value = next;
  document.body.dataset.analysisMode = next;
  if (next === 'supertrend') {
    if (currentData()) { renderMeta(); renderList(); }
    requestAnimationFrame(() => activateLazyCards());
  } else if (state.cardObserver) {
    state.cardObserver.disconnect();
  }
}

function isEtfCategory(category=state.category) { return category === 'KR_ETF' || category === 'US_ETF'; }
function sizeFilterMode(category=state.category) { return isEtfCategory(category) ? 'etf' : 'equity'; }
function currentSizeMin() {
  const mode = sizeFilterMode();
  const value = Number(state.marketSizeMin[mode]);
  return Number.isFinite(value) && value >= 0 ? value : (mode === 'etf' ? DEFAULT_ETF_SIZE_MIN : DEFAULT_EQUITY_SIZE_MIN);
}
function renderSizeFilters() {
  const mode = sizeFilterMode();
  const presets = CAP_FILTER_PRESETS[mode];
  const activeValue = currentSizeMin();
  $$('.cap-filter').forEach((button, i) => {
    const [value, label] = presets[i];
    button.dataset.cap = String(value);
    button.textContent = label;
    button.classList.toggle('active', Number(value) === activeValue);
  });
  $('#capFilterTabs')?.setAttribute('aria-label', mode === 'etf' ? 'ETF 규모 필터' : '시가총액 필터');
}

function money(v, currency) {
  if (v == null || !Number.isFinite(Number(v))) return '—';
  return currency === 'KRW'
    ? `₩${Math.round(Number(v)).toLocaleString('ko-KR')}`
    : `$${Number(v).toLocaleString('en-US', { minimumFractionDigits:2, maximumFractionDigits:2 })}`;
}
function numberOrNaN(v) {
  if (v === null || v === undefined || v === '') return Number.NaN;
  const n = Number(v);
  return Number.isFinite(n) ? n : Number.NaN;
}
function marketSize(v) {
  if (v == null || !Number.isFinite(Number(v))) return '—';
  const n = Number(v);
  if (n >= 1e12) return `${(n / 1e12).toFixed(n >= 100e12 ? 0 : 1)}조`;
  if (n >= 1e8) return `${(n / 1e8).toFixed(0)}억`;
  return Math.round(n).toLocaleString('ko-KR');
}
function changeText(v) {
  const n = Number(v);
  if (!Number.isFinite(n)) return '<span class="flat">—</span>';
  if (n > 0) return `<span class="up">▲${Math.abs(n).toFixed(1)}%</span>`;
  if (n < 0) return `<span class="down">▼${Math.abs(n).toFixed(1)}%</span>`;
  return '<span class="flat">0.0%</span>';
}
function marketSizeLabel(stock) { return stock?.market_size_basis === 'total_assets' ? '순자산' : '시총'; }
function opinionKey(stock) { return String(stock?.opinion_code || stock?.supertrend?.opinion_code || 'HOLD'); }
function opinionText(stock) { const k=opinionKey(stock); return OPINION_TEXT[k] || k; }
function opinionClass(stock) { return `opinion-${opinionKey(stock).toLowerCase().replace('_','-')}`; }

async function ensureData(category, force=false) {
  if (state.data[category] && !force) return state.data[category];
  const response = await fetch(dataUrl(`data/${CATEGORY[category].dir}/summary.json`, force), { cache: force ? 'no-store' : 'default' });
  if (!response.ok) throw new Error(`${category} summary ${response.status}`);
  const data = await response.json();
  state.data[category] = data;
  return data;
}
async function ensureDetail(stock, force=false) {
  const key = `${stock.category}:${stock.ticker}`;
  if (state.detailCache.has(key) && !force) return state.detailCache.get(key);
  if (!stock.detail_path) throw new Error('detail_path missing');
  const response = await fetch(dataUrl(stock.detail_path, force), { cache: force ? 'no-store' : 'default' });
  if (!response.ok) throw new Error(`detail ${response.status}`);
  const detail = await response.json();
  state.detailCache.set(key, detail);
  return detail;
}

function numericSortValue(stock, key) {
  const raw = stock?.[key] ?? stock?.supertrend?.[key];
  if (raw === null || raw === undefined || raw === '') return Number.POSITIVE_INFINITY;
  const v = Number(raw);
  return Number.isFinite(v) ? v : Number.POSITIVE_INFINITY;
}
function defaultOpinionCompare(a,b) {
  const la = Number(a.rank_level ?? OPINION_ORDER[opinionKey(a)] ?? 4);
  const lb = Number(b.rank_level ?? OPINION_ORDER[opinionKey(b)] ?? 4);
  if (la !== lb) return la-lb;
  if (la === OPINION_ORDER.SELL) {
    const na = a.new_sell ? 0 : 1, nb = b.new_sell ? 0 : 1;
    if (na !== nb) return na-nb;
  }
  const ca = Number(a.market_size_krw), cb = Number(b.market_size_krw);
  if (Number.isFinite(ca) || Number.isFinite(cb)) return (Number.isFinite(cb)?cb:-1)-(Number.isFinite(ca)?ca:-1);
  return String(a.ticker||'').localeCompare(String(b.ticker||''));
}
function activeSortCompare(a,b) {
  if (state.sortMode === 'r_pct') {
    const d = numericSortValue(a,'r_pct') - numericSortValue(b,'r_pct');
    return d || defaultOpinionCompare(a,b);
  }
  if (state.sortMode === 'stop_pct') {
    const d = numericSortValue(a,'stop_pct') - numericSortValue(b,'stop_pct');
    return d || defaultOpinionCompare(a,b);
  }
  if (state.sortMode === 'bars_since_gate') {
    const d = numericSortValue(a,'bars_since_gate') - numericSortValue(b,'bars_since_gate');
    return d || defaultOpinionCompare(a,b);
  }
  return defaultOpinionCompare(a,b);
}
function filterItems() {
  const items = currentData()?.items || [];
  const q = state.query.trim().toLowerCase();
  const capMin = currentSizeMin();
  const capMatched = capMin <= 0 ? items : items.filter((s) => Number.isFinite(Number(s.market_size_krw)) && Number(s.market_size_krw) >= capMin);
  const candidates = q ? capMatched.filter((s) => `${s.name} ${s.symbol} ${s.ticker} ${s.sector || ''}`.toLowerCase().includes(q)) : capMatched;
  const sorted = [...candidates].sort(activeSortCompare);
  const nonSell = sorted.filter((s) => opinionKey(s) !== 'SELL');
  const sells = sorted.filter((s) => opinionKey(s) === 'SELL').sort((a,b) => {
    const na=a.new_sell?0:1, nb=b.new_sell?0:1;
    if (na !== nb) return na-nb;
    return activeSortCompare(a,b);
  });
  state.filtered = q ? nonSell : nonSell.slice(0, DEFAULT_TOP_N);
  state.sellItems = sells;
  return { totalCapMatched: capMatched.length, nonSellCount: nonSell.length, sellCount: sells.length };
}

function pct(v, digits=2) {
  if (v === null || v === undefined || v === '') return '—';
  const n=Number(v); return Number.isFinite(n) ? `${n>=0?'+':''}${n.toFixed(digits)}%` : '—';
}
function strategyLines(stock) {
  const st = stock?.supertrend || {};
  const p0Raw=st.p0 ?? stock.p0, p1Raw=st.p1 ?? stock.p1;
  const p0=numberOrNaN(p0Raw), p1=numberOrNaN(p1Raw), r=numberOrNaN(st.r_pct ?? stock.r_pct);
  const stop=numberOrNaN(st.stop_pct ?? stock.stop_pct), atr=numberOrNaN(st.atr_pct ?? stock.atr_pct);
  const bars=numberOrNaN(st.bars_since_gate ?? stock.bars_since_gate);
  const dir=st.direction === 'UP' ? '상승' : st.direction === 'DOWN' ? '하락' : '—';
  const reason=String(st.hold_reason ?? stock.hold_reason ?? '');
  const reasonText={BELOW_GATE:'게이트 미통과',OVEREXTENDED:'20% 이상 진행',NO_FLIP:'전환점 없음'}[reason] || '';
  return `<div class="trade-signal-box">
    <div class="trade-signal-row"><span class="signal-title breakout">ST 10·2</span><span>현재 <b>${dir}</b></span><i>·</i><span>P1 <b>${money(p1Raw, stock.currency)}</b></span>${stock.new_sell?'<span class="new-sell-tag">신규매도</span>':''}</div>
    <div class="trade-signal-row"><span class="signal-title pullback">P0</span><span>전환 직전 ST <b>${money(p0Raw, stock.currency)}</b></span><i>·</i><span>r <b>${Number.isFinite(r)?r.toFixed(2)+'%':'—'}</b></span>${reasonText?`<i>·</i><span>${reasonText}</span>`:''}</div>
    <div class="trade-signal-row"><span class="signal-title risk">RISK</span><span>ST 거리 <b>${Number.isFinite(stop)?stop.toFixed(2)+'%':'—'}</b></span><i>·</i><span>ATR <b>${Number.isFinite(atr)?atr.toFixed(2)+'%':'—'}</b></span><i>·</i><span>Gate +<b>${Number.isFinite(bars)?Math.max(0,Math.trunc(bars)):'—'}</b>봉</span></div>
  </div>`;
}
function backtestLine(stock) {
  const bt=stock?.supertrend?.backtest || {};
  const comp=bt.completed || {};
  const avg=numberOrNaN(comp.avg_return_pct), trades=numberOrNaN(comp.trades), win=numberOrNaN(comp.win_rate_pct);
  return `2Y Back test: S→매도 평균 <b>${Number.isFinite(avg)?pct(avg):'—'}</b> <small>· 완료 ${Number.isFinite(trades)?trades:0}회 · 승률 ${Number.isFinite(win)?win.toFixed(1)+'%':'—'}</small>`;
}
function stockCard(stock) {
  const ticker=stock.symbol || stock.ticker || '—';
  return `<article class="stock-card ${opinionClass(stock)}" data-ticker="${escapeHtml(stock.ticker)}">
    <section class="stock-info-pane">
      <div class="stock-headline-row">
        <h2>${escapeHtml(stock.name)} <span>(${escapeHtml(ticker)})</span></h2>
        <button class="opinion-pill ${opinionClass(stock)}" type="button" data-score-detail="${escapeHtml(stock.ticker)}">${escapeHtml(opinionText(stock))}</button>
      </div>
      <div class="stock-meta-line"><span class="sector-name">${escapeHtml(stock.sector || '—')}</span><i>·</i><span class="market-stat">${marketSizeLabel(stock)} ${marketSize(stock.market_size_krw)}</span><i>·</i><span class="market-stat">현재가 ${money(stock.close, stock.currency)} ${changeText(stock.day_change_pct)}</span></div>
      ${strategyLines(stock)}
      <div class="backtest-one-line">${backtestLine(stock)}</div>
    </section>
    <section class="stock-chart-pane"><div class="inline-chart" data-chart-box><div class="chart-loading">6M CANDLE + SUPERTREND</div></div></section>
  </article>`;
}
function renderList() {
  const { totalCapMatched, nonSellCount, sellCount } = filterItems();
  const mainHtml = state.filtered.map(stockCard).join('');
  const sellHtml = sellCount ? `<details class="sell-group" ${state.query?'open':''}><summary><span>매도</span><b>${sellCount.toLocaleString()}개</b><small>최근 5봉 신규매도 우선 · 기본 접힘</small></summary><div class="sell-group-list">${state.sellItems.map(stockCard).join('')}</div></details>` : '';
  $('#stockList').innerHTML = (mainHtml || sellHtml) ? `${mainHtml}${sellHtml}` : '<div class="empty-state">선택한 조건의 검색 결과가 없습니다.</div>';
  $('#resultCount').textContent = state.query
    ? `${(state.filtered.length+sellCount).toLocaleString()}개`
    : `TOP ${Math.min(DEFAULT_TOP_N,nonSellCount).toLocaleString()} / ${nonSellCount.toLocaleString()} · 매도 ${sellCount.toLocaleString()}`;
  activateLazyCards();
}

function renderMeta() {
  const data=currentData();
  if(!data){ $('#marketDate').textContent='—'; $('#coverage').textContent='—'; $('#scanStatus').textContent='—'; return; }
  $('#marketDate').textContent=data.market_date || '—';
  $('#coverage').textContent=`가격수신 ${Number(data.coverage_pct||0).toFixed(1)}%`;
  $('#scanStatus').textContent=`${data.scan_mode==='QUICK'?'장중 QUICK':'종가 확정 FULL'} · Buy S→A→B→C→Hold→매도 · 동일등급 시총순`;
}
function stockByTicker(ticker) { return (currentData()?.items || []).find(x=>x.ticker===ticker) || null; }

function drawSupertrendChart(el, detail) {
  const rows=detail?.supertrend?.chart || [];
  if(!el?.isConnected) return;
  if(rows.length<15){ el.innerHTML='<div class="chart-empty">Supertrend 차트 데이터 부족</div>'; return; }
  const vals=rows.flatMap(r=>[numberOrNaN(r.low),numberOrNaN(r.high),numberOrNaN(r.supertrend),numberOrNaN(r.p0_line)]).filter(Number.isFinite);
  let lo=Math.min(...vals), hi=Math.max(...vals); if(!(hi>lo)){lo*=.99;hi*=1.01;} const ext=(hi-lo)*.07||1;lo-=ext;hi+=ext;
  const W=700,H=250,pad={l:12,r:58,t:14,b:24},plotW=W-pad.l-pad.r,plotH=H-pad.t-pad.b,step=plotW/rows.length;
  const X=i=>pad.l+(i+.5)*step, Y=v=>pad.t+(hi-v)*plotH/(hi-lo);
  let grid=''; for(let g=0;g<4;g++){const y=pad.t+g*plotH/3,v=hi-g*(hi-lo)/3,label=detail.currency==='KRW'?Math.round(v).toLocaleString('ko-KR'):`$${v.toFixed(Math.abs(v)>=100?0:1)}`;grid+=`<line x1="${pad.l}" x2="${W-pad.r}" y1="${y}" y2="${y}" class="chart-grid"/><text x="${W-pad.r+6}" y="${y+4}" class="price-axis">${label}</text>`;}
  const bw=Math.max(1.5,Math.min(4.8,step*.58)); let candles='';
  rows.forEach((r,i)=>{const o=Number(r.open),h=Number(r.high),l=Number(r.low),c=Number(r.close);if(![o,h,l,c].every(Number.isFinite))return;const x=X(i),yo=Y(o),yc=Y(c),yh=Y(h),yl=Y(l),up=c>=o,k=up?'chart-candle-up':'chart-candle-down',top=Math.min(yo,yc),bh=Math.max(1.1,Math.abs(yc-yo));candles+=`<line x1="${x}" x2="${x}" y1="${yh}" y2="${yl}" class="chart-candle-wick ${k}"/><rect x="${x-bw/2}" y="${top}" width="${bw}" height="${bh}" class="${k}" rx=".4"><title>${escapeHtml(r.date)} O ${o} H ${h} L ${l} C ${c}</title></rect>`;});
  let upPath='',downPath='',upDraw=false,downDraw=false;
  rows.forEach((r,i)=>{const v=numberOrNaN(r.supertrend),d=numberOrNaN(r.direction);if(!Number.isFinite(v)){upDraw=downDraw=false;return;}if(d>0){upPath+=`${upDraw?'L':'M'}${X(i).toFixed(1)},${Y(v).toFixed(1)} `;upDraw=true;downDraw=false;}else{downPath+=`${downDraw?'L':'M'}${X(i).toFixed(1)},${Y(v).toFixed(1)} `;downDraw=true;upDraw=false;}});
  const p0Start=rows.findIndex(r=>Number.isFinite(numberOrNaN(r.p0_line)));
  let p0Svg='';
  if(p0Start>=0){const p0=numberOrNaN(rows[p0Start].p0_line),y=Y(p0);p0Svg=`<line x1="${X(p0Start)}" x2="${X(rows.length-1)}" y1="${y}" y2="${y}" class="p0-guide"><title>P0 ${p0}</title></line><text x="${X(p0Start)+4}" y="${y-5}" class="p0-label">P0</text>`;}
  let markerSvg='';
  rows.forEach((r,i)=>{const x=X(i),h=numberOrNaN(r.high),stv=numberOrNaN(r.supertrend);if(r.is_flip&&Number.isFinite(h)){const y=Y(h)-7;markerSvg+=`<path d="M ${x-4} ${y-5} L ${x+4} ${y-5} L ${x} ${y+2} Z" class="flip-marker"><title>상승 전환 ${escapeHtml(r.date)}</title></path>`;}if(r.is_gate&&Number.isFinite(stv)){const y=Y(stv);markerSvg+=`<circle cx="${x}" cy="${y}" r="4.2" class="gate-marker"><title>게이트 통과 ${escapeHtml(r.date)}</title></circle>`;}});
  const dates=`<text x="${X(0)}" y="${H-6}" text-anchor="start" class="date-axis">${escapeHtml(rows[0].date.slice(5))}</text><text x="${X(rows.length-1)}" y="${H-6}" text-anchor="end" class="date-axis">${escapeHtml(rows[rows.length-1].date.slice(5))}</text>`;
  el.innerHTML=`<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-label="6개월 일반 일봉과 Supertrend 10,2">${grid}${candles}${p0Svg}<path d="${upPath}" class="supertrend-up-line"/><path d="${downPath}" class="supertrend-down-line"/>${markerSvg}${dates}</svg><div class="chart-legend"><span>일반 CANDLE · 상승 빨강 / 하락 파랑</span><span>ST 상승 빨강 / 하락 파랑</span><span>P0 점선</span></div>`;
}

function drawChart(el, detail) { drawSupertrendChart(el, detail); }

async function hydrateCard(card) {
  if(!card?.isConnected||card.dataset.hydrated==='1')return; card.dataset.hydrated='1';
  const stock=stockByTicker(card.dataset.ticker); if(!stock)return;
  try{const detail=await ensureDetail(stock);drawSupertrendChart($('[data-chart-box]',card),detail);}catch(err){console.error(err);const box=$('[data-chart-box]',card);if(box)box.innerHTML='<div class="chart-empty">차트를 불러오지 못했습니다.</div>';}
}
function activateLazyCards() {
  if(state.cardObserver)state.cardObserver.disconnect();
  state.cardObserver=new IntersectionObserver(entries=>entries.forEach(entry=>{if(!entry.isIntersecting)return;state.cardObserver.unobserve(entry.target);void hydrateCard(entry.target);}),{rootMargin:'500px 0px'});
  $$('.stock-card').forEach(card=>state.cardObserver.observe(card));
}
async function openScoreDetail(stock) {
  const modal=$('#scoreModal'),body=$('#scoreModalBody'); $('#scoreModalTitle').textContent=`${stock.name} (${stock.symbol||stock.ticker}) · ${opinionText(stock)}`;modal.hidden=false;document.body.classList.add('modal-open');body.innerHTML='<div class="modal-loading">Supertrend 상세를 불러오는 중…</div>';
  let detail=stock;try{detail=await ensureDetail(stock);}catch(_){ }
  const st=detail?.supertrend||stock?.supertrend||{},bt=st.backtest||{},comp=bt.completed||{},inc=bt.including_open||{};
  const avg=numberOrNaN(comp.avg_return_pct),win=numberOrNaN(comp.win_rate_pct),r=numberOrNaN(st.r_pct);
  const trades=(bt.recent_trades||[]).map(t=>`<div class="score-row"><div><b>${escapeHtml(t.buy_date)} → ${escapeHtml(t.sell_date)}</b></div><strong>${pct(t.return_pct)}</strong></div>`).join('');
  body.innerHTML=`<div class="score-total"><span>SUPERTREND 10·2 · WILDER RMA</span><b>${escapeHtml(st.opinion_label||opinionText(stock))}</b></div><div class="score-rows">
    <div class="score-row"><div><b>P0</b><small>하락→상승 전환 직전 봉의 ST = ST[flip−1]</small></div><strong>${money(st.p0,stock.currency)}</strong></div>
    <div class="score-row"><div><b>P1</b><small>현재 Supertrend 가격</small></div><strong>${money(st.p1,stock.currency)}</strong></div>
    <div class="score-row"><div><b>r</b><small>(현재 종가 − P0) / P0 · 등급의 유일한 근거</small></div><strong>${Number.isFinite(r)?r.toFixed(2)+'%':'—'}</strong></div>
    <div class="score-row"><div><b>ST 손절폭</b><small>(Close − P1) / Close</small></div><strong>${pct(st.stop_pct)}</strong></div>
    <div class="score-row"><div><b>ATR% / g_ATR / d_ATR</b><small>진단 전용 · 의견 판정에 미사용</small></div><strong>${pct(st.atr_pct)} · ${Number.isFinite(numberOrNaN(st.g_atr))?numberOrNaN(st.g_atr).toFixed(2):'—'} · ${Number.isFinite(numberOrNaN(st.d_atr))?numberOrNaN(st.d_atr).toFixed(2):'—'}</strong></div>
    <div class="score-row"><div><b>레그 / 게이트 경과</b></div><strong>${st.bars_since_flip??'—'}봉 · ${st.bars_since_gate??'—'}봉</strong></div>
    <div class="score-row"><div><b>2Y 완료거래 평균 / 승률</b><small>S 최초 신호 다음 시가 → 매도전환 다음 시가</small></div><strong>${Number.isFinite(avg)?pct(avg):'—'} · ${Number.isFinite(win)?win.toFixed(1)+'%':'—'}</strong></div>
    <div class="score-row"><div><b>완료 / 미청산 포함</b></div><strong>${Number(comp.trades||0)}회 · ${Number(inc.trades||0)}회</strong></div>${trades}</div>`;
}

function closeModal(){ $('#scoreModal').hidden=true;document.body.classList.remove('modal-open'); }

async function switchCategory(category, force=false) {
  if(!CATEGORY[category])return;state.category=category;state.query='';$('#searchInput').value='';
  if(force){state.data[category]=null;for(const key of [...state.detailCache.keys()])if(key.startsWith(`${category}:`))state.detailCache.delete(key);}
  $$('.market-tab').forEach(b=>b.classList.toggle('active',b.dataset.category===category));renderSizeFilters();$('#status').hidden=false;$('#stockList').hidden=true;$('#status').textContent='데이터를 불러오는 중입니다.';
  try{await ensureData(category,force);renderMeta();renderList();$('#status').hidden=true;$('#stockList').hidden=false;}catch(err){console.error(err);renderMeta();$('#status').hidden=false;$('#status').textContent='데이터를 불러오지 못했습니다.';}
}

// -----------------------------------------------------------------------------
// Quiz mode
// -----------------------------------------------------------------------------
function quizRandomInt(min, max) {
  return Math.floor(Math.random() * (max - min + 1)) + min;
}

function quizPick(arr) {
  return arr?.length ? arr[quizRandomInt(0, arr.length - 1)] : null;
}

function quizPickEntry(pool) {
  if (!pool?.length) return null;
  const groups = new Map();
  pool.forEach((item) => {
    const key = String(item?.category || 'OTHER');
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(item);
  });
  const category = quizPick([...groups.keys()]);
  return quizPick(groups.get(category));
}

async function ensureQuizPool(force=false) {
  if (state.quiz.pool && !force) return state.quiz.pool;
  const settled = await Promise.allSettled(QUIZ_SHARDS.map(async (shard) => {
    const response = await fetch(dataUrl(`data/quiz/${shard}/manifest.json`, force), { cache: force ? 'no-store' : 'default' });
    if (!response.ok) throw new Error(`${shard} quiz ${response.status}`);
    return response.json();
  }));
  const pool = [];
  settled.forEach((result) => {
    if (result.status !== 'fulfilled') return;
    const rows = Array.isArray(result.value?.items) ? result.value.items : [];
    rows.forEach((row) => {
      const n = Number(row?.points || 0);
      const cap = Number(row?.market_size_krw);
      if (n >= QUIZ_WINDOW_DAYS + 20 && Number.isFinite(cap) && cap >= QUIZ_MIN_MARKET_SIZE && row?.detail_path) pool.push(row);
    });
  });
  if (force) state.quiz.detailCache.clear();
  state.quiz.pool = pool;
  return pool;
}

async function ensureQuizStock(entry, force=false) {
  const key = `${entry?.category}:${entry?.ticker}`;
  if (!force && state.quiz.detailCache.has(key)) return state.quiz.detailCache.get(key);
  if (!entry?.detail_path) throw new Error('quiz detail path missing');
  const response = await fetch(dataUrl(entry.detail_path, force), { cache: force ? 'no-store' : 'default' });
  if (!response.ok) throw new Error(`quiz detail ${response.status}`);
  const detail = await response.json();
  state.quiz.detailCache.set(key, detail);
  return detail;
}

function quizRows(stock, start, count=QUIZ_WINDOW_DAYS) {
  const rows = [];
  for (let i = start; i < start + count; i++) {
    const o = Number(stock.o?.[i]), h = Number(stock.h?.[i]), l = Number(stock.l?.[i]);
    const c = Number(stock.c?.[i]), v = Number(stock.v?.[i]);
    if (![o,h,l,c].every(Number.isFinite)) return [];
    rows.push({
      index:i,
      date:String(stock.d?.[i] || ''),
      open:o,
      high:h,
      low:l,
      close:c,
      volume:Number.isFinite(v) ? Math.max(0,v) : 0,
    });
  }
  return rows;
}

function quizBollinger(stock, start, count=QUIZ_WINDOW_DAYS) {
  const closes = (stock.c || []).map(Number);
  return Array.from({length:count}, (_, offset) => {
    const idx = start + offset;
    if (idx < 19) return { mid:NaN, upper:NaN, lower:NaN };
    const w = closes.slice(idx - 19, idx + 1).filter(Number.isFinite);
    if (w.length !== 20) return { mid:NaN, upper:NaN, lower:NaN };
    const mid = w.reduce((a,b) => a+b, 0) / w.length;
    const variance = w.reduce((a,b) => a + (b-mid)*(b-mid), 0) / w.length;
    const sd = Math.sqrt(Math.max(0, variance));
    return { mid, upper:mid + 2*sd, lower:mid - 2*sd };
  });
}

function quizVolumeProfile(rows) {
  if (!rows?.length) return null;
  const pmin = Math.min(...rows.map(r => r.low));
  const pmax = Math.max(...rows.map(r => r.high));
  if (!(pmax > pmin)) return null;
  const edges = Array.from({length:11}, (_, i) => pmin + (pmax-pmin)*i/10);
  const values = Array(10).fill(0);
  rows.forEach((r) => {
    const span = r.high - r.low;
    if (span > 1e-12) {
      for (let b=0;b<10;b++) {
        const overlap = Math.max(0, Math.min(r.high, edges[b+1]) - Math.max(r.low, edges[b]));
        values[b] += r.volume * overlap / span;
      }
    } else {
      const b = Math.max(0, Math.min(9, Math.floor((r.close-pmin)/(pmax-pmin)*10)));
      values[b] += r.volume;
    }
  });
  return { pmin, pmax, edges, values, max:Math.max(...values, 1) };
}

function quizResidualSignature(values) {
  if (!values?.length || values.some(v => !Number.isFinite(v) || v <= 0)) return [];
  const logs = values.map(Math.log);
  const a = logs[0], b = logs[logs.length-1];
  const residual = logs.map((v,i) => v - (a + (b-a)*i/Math.max(1,logs.length-1)));
  const mean = residual.reduce((x,y)=>x+y,0)/residual.length;
  const sd = Math.sqrt(residual.reduce((x,y)=>x+(y-mean)*(y-mean),0)/residual.length) || 1;
  return residual.map(v => (v-mean)/sd);
}

function quizShapeDistance(a, b) {
  const sa = quizResidualSignature(a), sb = quizResidualSignature(b);
  if (!sa.length || sa.length !== sb.length) return Infinity;
  return Math.sqrt(sa.reduce((sum,v,i) => sum + (v-sb[i])*(v-sb[i]), 0) / sa.length);
}

function quizDailyVol(values) {
  if (!values?.length || values.length < 2) return 0;
  const r=[];
  for(let i=1;i<values.length;i++){
    const a=Number(values[i-1]), b=Number(values[i]);
    if(a>0&&b>0) r.push(Math.log(b/a));
  }
  if(!r.length) return 0;
  const mean=r.reduce((x,y)=>x+y,0)/r.length;
  return Math.sqrt(r.reduce((x,y)=>x+(y-mean)*(y-mean),0)/r.length);
}


function quizNormalizeToStart(values, targetStart) {
  if (!values?.length || !Number.isFinite(targetStart) || targetStart <= 0) return [];
  const first = Number(values[0]);
  if (!Number.isFinite(first) || first <= 0) return [];
  const scale = targetStart / first;
  return values.map((v) => Number(v) * scale);
}

function quizEndGapRatio(a, b) {
  const ea = Number(a?.[a.length - 1]);
  const eb = Number(b?.[b.length - 1]);
  if (!(ea > 0) || !(eb > 0)) return 0;
  return Math.abs(ea - eb) / Math.max(1e-9, Math.min(ea, eb));
}

function quizSeriesSimilarity(a, b) {
  return quizShapeDistance(a, b) + Math.min(1.5, quizEndGapRatio(a, b) * 1.6);
}

function quizPrepareOption(raw, displayStart) {
  const rawCloses = Array.isArray(raw?.closes) ? raw.closes.map(Number) : [];
  const rawCandles = Array.isArray(raw?.candles) ? raw.candles : [];
  const displayCloses = quizNormalizeToStart(rawCloses, displayStart);
  if (!displayCloses.length) return null;
  const displayCandles = quizTransformCandles(rawCandles, displayCloses);
  return {
    rawCloses,
    rawCandles,
    closes: displayCloses,
    candles: displayCandles,
    end: displayCloses[displayCloses.length - 1],
  };
}


function quizFastDistractorSeries(correctValues, targetEndMultiplier, variant) {
  if (!correctValues?.length || correctValues.some(v => !Number.isFinite(v) || v <= 0)) return null;
  const n = correctValues.length;
  const start = Number(correctValues[0]);
  const correctEnd = Number(correctValues[n - 1]);
  if (!(start > 0) || !(correctEnd > 0)) return null;
  const targetEnd = correctEnd * targetEndMultiplier;
  if (!(targetEnd > 0)) return null;

  // Anchor every option at exactly the same first close.  The endpoint is chosen
  // separately, while the interior shape is a bounded market-like residual.
  const logStart = Math.log(start);
  const logEnd = Math.log(targetEnd);
  const dailyVol = Math.max(0.008, Math.min(0.045, quizDailyVol(correctValues) || 0.018));
  const amp = Math.max(0.045, Math.min(0.14, dailyVol * Math.sqrt(n) * 0.95));
  const sig = quizResidualSignature(correctValues);
  const out = [];
  for (let i=0;i<n;i++) {
    const x = i / Math.max(1, n - 1);
    const trend = logStart + (logEnd - logStart) * x;
    let shape = 0;
    if (variant === 0) {
      // selloff -> recovery
      shape = -amp * Math.sin(Math.PI * x) + 0.30 * amp * Math.sin(3 * Math.PI * x);
    } else if (variant === 1) {
      // early rally -> fade
      shape = 0.98 * amp * Math.sin(Math.PI * x) - 0.36 * amp * Math.sin(2 * Math.PI * x);
    } else if (variant === 2) {
      // two-leg whipsaw
      shape = 0.80 * amp * Math.sin(2 * Math.PI * x) + 0.34 * amp * Math.sin(5 * Math.PI * x);
    } else if (variant === 3) {
      // late breakout: weak first half, acceleration into the finish
      shape = -0.55 * amp * Math.sin(Math.PI * x) + 0.72 * amp * Math.sin(Math.PI * Math.pow(x, 1.8));
    } else if (variant === 4) {
      // late breakdown: strong first half, sharp fade late
      shape = 0.62 * amp * Math.sin(Math.PI * x) - 0.78 * amp * Math.sin(Math.PI * Math.pow(1 - x, 1.7));
    } else {
      // range / W-shape
      shape = -0.62 * amp * Math.sin(3 * Math.PI * x) + 0.28 * amp * Math.sin(6 * Math.PI * x);
    }
    const src = sig.length === n ? sig[(variant === 0 ? n - 1 - i : (i * (variant + 2)) % n)] : 0;
    const taper = Math.sin(Math.PI * x); // exactly zero at both edges
    const micro = Number.isFinite(src) ? src * dailyVol * 0.20 * taper : 0;
    out.push(Math.exp(trend + shape + micro));
  }
  out[0] = start;
  out[n - 1] = targetEnd;
  return out;
}

function quizBuildFastOptions(correctSeries, correctRawCandles, displayStartValue) {
  const displayStart = Number(displayStartValue);
  if (!(displayStart > 0)) return null;
  const correct = quizPrepareOption({ closes:correctSeries, candles:correctRawCandles }, displayStart);
  if (!correct) return null;

  // Endpoints are intentionally separated by >=10%. For each endpoint, choose
  // the shape template that is farthest from the real path and already-selected
  // distractors. This is CPU-only and normally completes in a few milliseconds.
  const multipliers = [0.76, 0.88, 1.14];
  const distractors = [];
  for (const mult of multipliers) {
    let best = null;
    let bestScore = -Infinity;
    for (let variant=0; variant<6; variant++) {
      const closes = quizFastDistractorSeries(correctSeries, mult, variant);
      if (!closes) continue;
      const candles = quizTransformCandles(correctRawCandles, closes);
      const prepared = quizPrepareOption({ closes, candles }, displayStart);
      if (!prepared) continue;
      const comparisons = [correct, ...distractors];
      const minShapeDistance = Math.min(...comparisons.map(x => quizShapeDistance(prepared.closes, x.closes)));
      if (minShapeDistance > bestScore) {
        bestScore = minShapeDistance;
        best = prepared;
      }
    }
    if (!best) return null;
    distractors.push(best);
  }

  const all = [correct, ...distractors];
  const endsOK = all.every((opt, i) => all.every((other, j) => i === j || quizEndGapRatio(opt.closes, other.closes) >= 0.10));
  if (!endsOK) return null;
  return { correct, distractors };
}

function quizTransformCandles(sourceRows, transformedCloses) {
  if (!sourceRows?.length || sourceRows.length !== transformedCloses?.length) return [];
  return sourceRows.map((r, i) => {
    const targetClose = Number(transformedCloses[i]);
    const srcClose = Number(r.close);
    const scale = srcClose > 0 ? targetClose / srcClose : 1;
    const open = Number(r.open) * scale;
    const high = Number(r.high) * scale;
    const low = Number(r.low) * scale;
    return {
      open,
      high:Math.max(high, open, targetClose),
      low:Math.min(low, open, targetClose),
      close:targetClose,
    };
  });
}

async function buildQuizQuestion(pool) {
  // Fast path: one stock JSON fetch per question.  No repeated donor-stock
  // network search is required to manufacture the three distractors.
  for (let attempt=0; attempt<24; attempt++) {
    const entry = quizPickEntry(pool);
    if (!entry) continue;
    let stock;
    try { stock = await ensureQuizStock(entry); } catch (_) { continue; }
    const n = stock?.c?.length || 0;
    if (n < QUIZ_WINDOW_DAYS + 20) continue;
    const start = quizRandomInt(19, n - QUIZ_WINDOW_DAYS);
    const rows = quizRows(stock, start);
    if (rows.length !== QUIZ_WINDOW_DAYS) continue;

    // Quiz is always "predict the next 30 trading days": show D1-D60 and hide D61-D90.
    const hiddenPart = 2;
    const hiddenStart = 60;
    const hiddenEnd = 90;
    const correctSeries = rows.slice(hiddenStart, hiddenEnd).map(r => r.close);
    if (correctSeries.some(v => !Number.isFinite(v) || v <= 0)) continue;
    const correctRawCandles = rows.slice(hiddenStart, hiddenEnd).map(r => ({open:r.open, high:r.high, low:r.low, close:r.close}));
    const visibleAnchor = Number(rows[hiddenStart - 1]?.close);
    const built = quizBuildFastOptions(correctSeries, correctRawCandles, visibleAnchor);
    if (!built) continue;

    const correctIndex = quizRandomInt(0, 3);
    const options = [];
    let di = 0;
    for (let i=0;i<4;i++) options.push(i === correctIndex ? built.correct : built.distractors[di++]);

    return {
      entry,
      stock,
      rows,
      bb:quizBollinger(stock, start),
      // Volume profile is intentionally calculated ONLY from the visible D1-D60.
      profile:quizVolumeProfile(rows.slice(0, hiddenStart)),
      start,
      hiddenPart,
      hiddenStart,
      hiddenEnd,
      correctIndex,
      options,
      selectedIndex:null,
    };
  }
  return null;
}

function quizPath(values, X, Y) {
  return values.map((v,i) => Number.isFinite(v) ? `${i?'L':'M'}${X(i).toFixed(1)},${Y(v).toFixed(1)}` : '').filter(Boolean).join(' ');
}

function renderQuizOption(option, index, correctIndex, selectedIndex, answered) {
  const values = option?.closes || [];
  const candles = option?.candles || [];
  const W=260, H=126, pad={l:32,r:32,t:12,b:12};
  const extrema = candles.flatMap(c => [Number(c.low), Number(c.high)]).filter(Number.isFinite);
  const fitted = extrema.length ? extrema : values.filter(Number.isFinite);
  let lo=Math.min(...fitted), hi=Math.max(...fitted);
  if (!(hi>lo)) { lo*=.99; hi*=1.01; }
  const extra=(hi-lo)*.08 || Math.abs(hi)*.01 || 1;
  lo-=extra; hi+=extra;
  const plotW=W-pad.l-pad.r;
  const step=plotW/Math.max(1,values.length);
  const X=i=>pad.l+(i+.5)*step;
  const Y=v=>pad.t+(hi-v)*(H-pad.t-pad.b)/(hi-lo);
  const selected = selectedIndex === index;
  const klass = answered ? (index===correctIndex ? ' correct' : selected ? ' wrong' : '') : selected ? ' selected' : '';
  const bodyW=Math.max(2.2,Math.min(5.5,step*.58));
  let candleSvg='';
  candles.forEach((r,i)=>{
    if (![r.open,r.high,r.low,r.close].every(Number.isFinite)) return;
    const x=X(i),yo=Y(r.open),yc=Y(r.close),yh=Y(r.high),yl=Y(r.low);
    const up=r.close>=r.open, ck=up?'quiz-candle-up':'quiz-candle-down';
    const top=Math.min(yo,yc),bh=Math.max(1.2,Math.abs(yc-yo));
    candleSvg+=`<line x1="${x}" x2="${x}" y1="${yh}" y2="${yl}" class="quiz-option-wick ${ck}"/><rect x="${x-bodyW/2}" y="${top}" width="${bodyW}" height="${bh}" class="quiz-option-body ${ck}" rx=".35"/>`;
  });
  return `<button class="quiz-choice${klass}" type="button" data-quiz-choice="${index}" ${answered?'disabled':''}>
    <span class="quiz-choice-key">${index+1}</span>
    <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-label="보기 ${index+1}">
      <line x1="${pad.l}" x2="${W-pad.r}" y1="${pad.t}" y2="${pad.t}" class="quiz-option-grid"/>
      <line x1="${pad.l}" x2="${W-pad.r}" y1="${H/2}" y2="${H/2}" class="quiz-option-grid"/>
      <line x1="${pad.l}" x2="${W-pad.r}" y1="${H-pad.b}" y2="${H-pad.b}" class="quiz-option-grid"/>
      ${candleSvg}
      <path d="${quizPath(values,X,Y)}" class="quiz-option-line"/>
    </svg>
  </button>`;
}

function renderQuizMainChart(question, answered=false) {
  const el = $('#quizMainChart');
  if (!el || !question) return;
  const {rows,bb,profile,hiddenStart,hiddenEnd,selectedIndex,options} = question;
  const preview = (!answered && Number.isInteger(selectedIndex) && selectedIndex >= 0 && selectedIndex < options.length) ? options[selectedIndex] : null;
  const W=1000,H=430,pad={l:18,r:72,t:22,b:32};
  const vals = rows.flatMap((r,i)=>[r.low,r.high,bb[i]?.lower,bb[i]?.upper]).filter(Number.isFinite);
  let lo=Math.min(...vals), hi=Math.max(...vals);
  if (preview?.candles?.length) {
    preview.candles.forEach((r) => { vals.push(r.low, r.high); });
    lo = Math.min(lo, ...preview.candles.map((r) => r.low));
    hi = Math.max(hi, ...preview.candles.map((r) => r.high));
  }
  if (!(hi>lo)) { lo*=.99; hi*=1.01; }
  const extra=(hi-lo)*.055; lo-=extra; hi+=extra;
  const plotW=W-pad.l-pad.r, plotH=H-pad.t-pad.b;
  const X=i=>pad.l+(i+.5)*plotW/rows.length;
  const Y=v=>pad.t+(hi-v)*plotH/(hi-lo);
  const candleStep=plotW/rows.length;
  const bodyW=Math.max(2.1,candleStep*.56);
  const hiddenX1=pad.l+hiddenStart*candleStep;
  const hiddenX2=pad.l+hiddenEnd*candleStep;
  const isHidden=i=>!answered && i>=hiddenStart && i<hiddenEnd;

  let grid='';
  for(let g=0;g<5;g++){
    const y=pad.t+g*plotH/4;
    const v=hi-g*(hi-lo)/4;
    const label=question.stock.currency==='KRW'?Math.round(v).toLocaleString('ko-KR'):`$${v.toFixed(v>=100?0:1)}`;
    grid+=`<line x1="${pad.l}" x2="${W-pad.r}" y1="${y}" y2="${y}" class="quiz-chart-grid"/><text x="${W-pad.r+7}" y="${y+4}" class="quiz-price-axis">${label}</text>`;
  }

  let profileSvg='';
  if(profile){
    const maxWidth=plotW*.23;
    const visibleCurrent=rows[Math.max(0, hiddenStart - 1)]?.close;
    const currentBin=Math.max(0,Math.min(9,Math.floor((visibleCurrent-profile.pmin)/(profile.pmax-profile.pmin)*10)));
    for(let b=0;b<10;b++){
      const y1=Y(profile.edges[b+1]), y2=Y(profile.edges[b]);
      const barH=Math.max(1,Math.abs(y2-y1)-1);
      const bw=maxWidth*(profile.values[b]/profile.max);
      // Left-side volume profile: it stays entirely inside the visible 2/3 zone.
      profileSvg+=`<rect x="${pad.l}" y="${Math.min(y1,y2)+.5}" width="${bw}" height="${barH}" class="quiz-profile-bar${b===currentBin?' current':''}"/>`;
    }
  }

  function bbPath(key){
    let out='',drawing=false;
    for(let i=0;i<rows.length;i++){
      const v=Number(bb[i]?.[key]);
      if(!Number.isFinite(v)||isHidden(i)){drawing=false;continue;}
      out+=`${drawing?'L':'M'}${X(i).toFixed(1)},${Y(v).toFixed(1)} `;
      drawing=true;
    }
    return out.trim();
  }

  let candles='';
  rows.forEach((r,i)=>{
    if(isHidden(i)) return;
    const up=r.close>=r.open;
    const klass=up?'quiz-candle-up':'quiz-candle-down';
    const x=X(i), yo=Y(r.open), yc=Y(r.close), yh=Y(r.high), yl=Y(r.low);
    const top=Math.min(yo,yc), bh=Math.max(1.4,Math.abs(yc-yo));
    candles+=`<line x1="${x}" x2="${x}" y1="${yh}" y2="${yl}" class="quiz-candle-wick ${klass}"/><rect x="${x-bodyW/2}" y="${top}" width="${bodyW}" height="${bh}" class="${klass}" rx=".5"/>`;
  });

  let previewSvg='';
  if (preview?.candles?.length) {
    const previewCloses = preview.closes || [];
    previewSvg += `<rect x="${hiddenX1}" y="${pad.t}" width="${hiddenX2-hiddenX1}" height="${plotH}" class="quiz-preview-zone"/>`;
    preview.candles.forEach((r, localIndex) => {
      if (![r.open,r.high,r.low,r.close].every(Number.isFinite)) return;
      const i = hiddenStart + localIndex;
      const x = X(i), yo = Y(r.open), yc = Y(r.close), yh = Y(r.high), yl = Y(r.low);
      const up = r.close >= r.open;
      const klass = up ? 'quiz-candle-up' : 'quiz-candle-down';
      const top = Math.min(yo, yc), bh = Math.max(1.4, Math.abs(yc - yo));
      previewSvg += `<line x1="${x}" x2="${x}" y1="${yh}" y2="${yl}" class="quiz-preview-wick ${klass}"/><rect x="${x-bodyW/2}" y="${top}" width="${bodyW}" height="${bh}" class="quiz-preview-body ${klass}" rx=".5"/>`;
    });
    const previewPath = previewCloses.length ? quizPath(previewCloses, (idx) => X(hiddenStart + idx), Y) : '';
    if (previewPath) previewSvg += `<path d="${previewPath}" class="quiz-preview-line"/>`;
  }

  const dateIndices=[0,44,89];
  const dates=dateIndices.map((i)=>{
    const label=answered?(rows[i]?.date?.slice(2)||''):`D${i+1}`;
    const anchor=i===0?'start':i===89?'end':'middle';
    return `<text x="${i===0?pad.l:i===89?W-pad.r:X(i)}" y="${H-9}" text-anchor="${anchor}" class="quiz-date-axis">${escapeHtml(label)}</text>`;
  }).join('');

  const hiddenOverlay = answered
    ? `<rect x="${hiddenX1}" y="${pad.t}" width="${hiddenX2-hiddenX1}" height="${plotH}" class="quiz-reveal-zone"/>`
    : (!preview ? `<rect x="${hiddenX1}" y="${pad.t}" width="${hiddenX2-hiddenX1}" height="${plotH}" class="quiz-hidden-block"/><text x="${(hiddenX1+hiddenX2)/2}" y="${pad.t+plotH/2}" text-anchor="middle" class="quiz-hidden-label">HIDDEN</text>` : '');

  el.innerHTML=`<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
    ${grid}
    <path d="${bbPath('upper')}" class="quiz-bb-line"/><path d="${bbPath('lower')}" class="quiz-bb-line"/><path d="${bbPath('mid')}" class="quiz-bb-mid"/>
    ${candles}${hiddenOverlay}${previewSvg}${profileSvg}${dates}
  </svg><div class="quiz-chart-legend"><span>CANDLE</span><span>BB 20,2</span><span>VISIBLE 60D · 10-ZONE PROFILE</span></div>`;
}

function renderQuizQuestion() {
  const q=state.quiz.question;
  if(!q) return;
  $('#quizStatus').hidden=true;
  $('#quizGame').hidden=false;
  $('#quizNumber').textContent=`QUIZ #${String(state.quiz.number).padStart(3,'0')}`;
  $('#quizIdentity').textContent=state.quiz.answered
    ? `${q.stock.name} (${q.stock.symbol || q.stock.ticker}) · ${q.rows[0].date} ~ ${q.rows[q.rows.length-1].date}`
    : '종목 · 기간 비공개';
  $('#quizSegmentLabel').textContent='마지막 1/3 · 다음 30거래일 가림';
  const instruction=$('#quizInstructionSub');
  if(instruction){
    instruction.textContent=state.quiz.answered
      ? '제출 후 실제 다음 30거래일 캔들과 정답 여부를 공개합니다.'
      : '앞 60거래일을 보고 다음 30거래일을 예측하세요. 보기를 누르면 후보가 빈 구간에 먼저 채워집니다.';
  }
  renderQuizMainChart(q,state.quiz.answered);
  $('#quizChoices').innerHTML=q.options.map((option,i)=>renderQuizOption(option,i,q.correctIndex,q.selectedIndex,state.quiz.answered)).join('');
  const submitBtn = $('#quizSubmitBtn');
  if (submitBtn) submitBtn.disabled = state.quiz.answered || !Number.isInteger(q.selectedIndex);
  const selectedHint = $('#quizSelectedHint');
  if (selectedHint) {
    selectedHint.textContent = state.quiz.answered
      ? `제출 완료 · 선택한 보기 ${Number.isInteger(q.selectedIndex) ? q.selectedIndex + 1 : '—'}번`
      : Number.isInteger(q.selectedIndex)
        ? `${q.selectedIndex + 1}번 보기를 차트에 적용했습니다. 마음에 들면 정답 제출을 누르세요.`
        : '보기를 선택하면 가려진 구간에 해당 차트가 먼저 채워집니다.';
  }
  const feedback=$('#quizFeedback');
  if(state.quiz.answered){
    feedback.hidden=false;
    const ok=q.selectedIndex===q.correctIndex;
    feedback.innerHTML=`<b>${ok?'정답입니다.':'오답입니다.'}</b> 정답은 ${q.correctIndex+1}번입니다. 실제 종목은 ${escapeHtml(q.stock.name)} (${escapeHtml(q.stock.symbol || q.stock.ticker)})이며, 가려진 30거래일 캔들을 차트에 공개했습니다.`;
  }else{
    feedback.hidden=true;
    feedback.textContent='';
  }
}

async function newQuizQuestion(forcePool=false) {
  if(state.quiz.loading) return;
  state.quiz.loading=true;
  const btn=$('#newQuizBtn');
  if(btn){btn.disabled=true;btn.textContent='문제 생성 중';}
  $('#quizStatus').hidden=false;
  $('#quizStatus').textContent='100조 이상 종목의 90거래일 차트를 불러오는 중입니다.';
  $('#quizGame').hidden=true;
  try{
    const pool=await ensureQuizPool(forcePool);
    if(!pool.length) throw new Error('quiz pool empty');
    const q=await buildQuizQuestion(pool);
    if(!q) throw new Error('could not build plausible distractors');
    q.selectedIndex = null;
    state.quiz.question=q;
    state.quiz.answered=false;
    state.quiz.number+=1;
    renderQuizQuestion();
  }catch(err){
    console.error('quiz',err);
    $('#quizStatus').hidden=false;
    $('#quizStatus').textContent='퀴즈 데이터가 아직 없습니다. 최신 코드 배포 후 ALL · FULL 스캔을 한 번 실행해 주세요.';
    $('#quizGame').hidden=true;
  }finally{
    state.quiz.loading=false;
    if(btn){btn.disabled=false;btn.textContent='문제 내기';}
  }
}

$('#analysisMode').addEventListener('change', (e) => {
  switchAnalysisMode(e.target.value);
});

$('#marketTabs').addEventListener('click', (e) => {
  const b = e.target.closest('.market-tab');
  if (b) void switchCategory(b.dataset.category);
});

$('#capFilterTabs').addEventListener('click', (e) => {
  const b = e.target.closest('.cap-filter');
  if (!b) return;
  const cap = Number(b.dataset.cap);
  if (!Number.isFinite(cap) || cap < 0) return;
  state.marketSizeMin[sizeFilterMode()] = cap;
  $$('.cap-filter').forEach((button) => button.classList.toggle('active', button === b));
  renderList();
});

$('#sortMode')?.addEventListener('change', (e) => {
  state.sortMode = ['default','r_pct','stop_pct','bars_since_gate'].includes(e.target.value) ? e.target.value : 'default';
  renderList();
});

let searchTimer;
$('#searchInput').addEventListener('input', (e) => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    state.query = e.target.value;
    renderList();
  }, 100);
});

$('#reloadBtn').addEventListener('click', () => {
  if (state.mode === 'quiz') {
    void newQuizQuestion(true);
    return;
  }
  void switchCategory(state.category, true);
});

$('#newQuizBtn')?.addEventListener('click', () => void newQuizQuestion(false));

$('#quizChoices')?.addEventListener('click', (e) => {
  const button = e.target.closest('[data-quiz-choice]');
  if (!button || state.quiz.answered || !state.quiz.question) return;
  const index = Number(button.dataset.quizChoice);
  if (!Number.isInteger(index) || index < 0 || index > 3) return;
  state.quiz.question.selectedIndex = index;
  renderQuizQuestion();
});

$('#quizSubmitBtn')?.addEventListener('click', () => {
  if (!state.quiz.question || state.quiz.answered) return;
  if (!Number.isInteger(state.quiz.question.selectedIndex)) return;
  state.quiz.answered = true;
  renderQuizQuestion();
});

document.addEventListener('click', (e) => {
  const score = e.target.closest('[data-score-detail]');
  if (score) {
    const stock = stockByTicker(score.dataset.scoreDetail);
    if (stock) void openScoreDetail(stock);
    return;
  }
  if (e.target.closest('[data-close-modal]')) closeModal();
});

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && !$('#scoreModal').hidden) closeModal();
});

document.addEventListener('toggle', (e) => {
  if (e.target?.classList?.contains('sell-group') && e.target.open) requestAnimationFrame(() => activateLazyCards());
}, true);

window.addEventListener('resize', () => {
  $$('.stock-card[data-hydrated="1"]').forEach(async (card) => {
    const stock = stockByTicker(card.dataset.ticker);
    if (!stock) return;
    const key = `${stock.category}:${stock.ticker}`;
    const detail = state.detailCache.get(key);
    if (detail) drawChart($('[data-chart-box]', card), detail);
  });
});

switchAnalysisMode('supertrend');
void switchCategory('KR');
