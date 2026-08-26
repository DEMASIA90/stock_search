const $ = (q, root=document) => root.querySelector(q);
const $$ = (q, root=document) => [...root.querySelectorAll(q)];

const CATEGORY = {
  KR: { short: '국장', dir: 'kr' },
  KR_ETF: { short: '국장ETF', dir: 'kr-etf' },
  US: { short: '미장', dir: 'us' },
  US_ETF: { short: '미장ETF', dir: 'us-etf' },
};
const CATEGORY_KEYS = Object.keys(CATEGORY);
const CONFIGURED_DATA_ORIGIN = String(window.DTC_DATA_ORIGIN || '').trim().replace(/\/$/, '');
const IS_ANDROID_APP = location.hostname === 'localhost' || location.protocol === 'capacitor:';
const DATA_BASE = (location.hostname.endsWith('github.io') || IS_ANDROID_APP) ? (CONFIGURED_DATA_ORIGIN || '.') : '.';
const CAP_FILTER_PRESETS = {
  KR: [
    [1_000_000_000_000,'1조 이상'],[10_000_000_000_000,'10조 이상'],[50_000_000_000_000,'50조 이상'],
    [100_000_000_000_000,'100조 이상'],[500_000_000_000_000,'500조 이상'],[1_000_000_000_000_000,'1000조 이상'],
  ],
  US: [
    [10_000_000_000_000,'10조 이상'],[50_000_000_000_000,'50조 이상'],[100_000_000_000_000,'100조 이상'],
    [500_000_000_000_000,'500조 이상'],[1_000_000_000_000_000,'1000조 이상'],
  ],
  KR_ETF: [[0,'전체'],[100_000_000_000,'0.1조 이상'],[500_000_000_000,'0.5조 이상'],[1_000_000_000_000,'1조 이상'],[5_000_000_000_000,'5조 이상']],
  US_ETF: [[0,'전체'],[100_000_000_000,'0.1조 이상'],[500_000_000_000,'0.5조 이상'],[1_000_000_000_000,'1조 이상'],[5_000_000_000_000,'5조 이상']],
};
const QUIZ_WINDOW_DAYS = 90;
const QUIZ_HIDDEN_DAYS = 30;
const QUIZ_MIN_MARKET_SIZE = 100_000_000_000_000;
const QUIZ_SHARDS = ['kr','kr-etf','us','us-etf'];
const OPINION_ORDER = {BUY:0,HOLD:1,SHORT_BUY:1,LONG_BUY:1,SELL_CONSIDER:2,SELL:3};
const OPINION_TEXT = {BUY:'BUY',SHORT_BUY:'BUY',LONG_BUY:'HOLD',HOLD:'HOLD',SELL_CONSIDER:'Consider Sell',SELL:'Sell'};
const SORT_KEYS = new Set(['opinion','name','sector','close','day_change_amount','day_change_pct','market_size_krw','st_d_direction','st_w_direction','adx','backtest']);
const AUTO_REFRESH_INTERVAL_MS = 60_000;
const AUTO_REFRESH_MIN_GAP_MS = 20_000;
const DISPLAY_TIME_ZONE = 'Asia/Seoul';

const state = {
  sheet:'KR', category:'KR', data:{KR:null,KR_ETF:null,US:null,US_ETF:null},
  capMin:{KR:1_000_000_000_000,KR_ETF:0,US:10_000_000_000_000,US_ETF:0}, selectedTicker:null, detailCache:new Map(),
  sort:{key:'opinion',dir:'asc'}, searchOverrideTicker:null,
  quiz:{pool:null,detailCache:new Map(),question:null,answered:false,loading:false},
};

function escapeHtml(value){return String(value??'').replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));}
function dataUrl(path,force=false){const clean=String(path).replace(/^\.\//,'').replace(/^\//,'');const base=DATA_BASE==='.'?'.':DATA_BASE.replace(/\/$/,'');const url=`${base}/${clean}`;return force?`${url}${url.includes('?')?'&':'?'}ts=${Date.now()}`:url;}
function numberOrNaN(v){if(v===null||v===undefined||v==='')return Number.NaN;const n=Number(v);return Number.isFinite(n)?n:Number.NaN;}
function isEtfCategory(c=state.category){return c==='KR_ETF'||c==='US_ETF';}
function currentCapMin(){return Number(state.capMin[state.category]||0);}
function currentData(){return state.data[state.category];}
function stockByTicker(ticker){return (currentData()?.items||[]).find(x=>String(x.ticker)===String(ticker))||null;}

function summaryGeneratedMs(data){const ms=Date.parse(String(data?.generated_at_utc||''));return Number.isFinite(ms)?ms:0;}
function formatGeneratedAtKst(data){
  const ms=summaryGeneratedMs(data);if(!ms)return String(data?.market_date||'—');
  const parts=new Intl.DateTimeFormat('en-CA',{timeZone:DISPLAY_TIME_ZONE,year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false,hourCycle:'h23'}).formatToParts(new Date(ms));
  const pick=t=>parts.find(x=>x.type===t)?.value||'';
  return `${pick('year')}-${pick('month')}-${pick('day')} ${pick('hour')}:${pick('minute')}`;
}
async function fetchSummary(category){
  const r=await fetch(dataUrl(`data/${CATEGORY[category].dir}/summary.json`,true),{cache:'no-store',headers:{'Cache-Control':'no-cache'}});
  if(!r.ok)throw new Error(`${category} summary ${r.status}`);
  return r.json();
}
async function ensureData(category,force=false){if(state.data[category]&&!force)return state.data[category];const d=await fetchSummary(category);state.data[category]=d;return d;}
function clearDetailCacheForCategory(category){for(const key of [...state.detailCache.keys()])if(String(key).startsWith(`${category}:`))state.detailCache.delete(key);}
async function refreshCategoryData(category){
  if(!CATEGORY[category])return false;
  const previous=state.data[category];
  const next=await fetchSummary(category);
  const previousMs=summaryGeneratedMs(previous),nextMs=summaryGeneratedMs(next);
  const changed=!previous || (nextMs>0&&nextMs>previousMs) || (!nextMs&&JSON.stringify(next)!==JSON.stringify(previous));
  state.data[category]=next;
  if(!changed)return false;
  clearDetailCacheForCategory(category);
  if(state.category===category&&state.sheet!=='QUIZ'){
    if(state.selectedTicker&&!stockByTicker(state.selectedTicker)){state.selectedTicker=null;state.searchOverrideTicker=null;hideChartOverlay();}
    renderCapSelect();renderSheet();
  }
  return true;
}
async function ensureAllData(){await Promise.all(CATEGORY_KEYS.map(c=>ensureData(c,false).catch(()=>null)));}
async function ensureDetail(stock,force=false){const key=`${stock.category}:${stock.ticker}`;if(state.detailCache.has(key)&&!force)return state.detailCache.get(key);const r=await fetch(dataUrl(stock.detail_path,force),{cache:force?'no-store':'default'});if(!r.ok)throw new Error(`detail ${r.status}`);const d=await r.json();state.detailCache.set(key,d);return d;}

function money(v,currency){const n=numberOrNaN(v);if(!Number.isFinite(n))return '—';return currency==='KRW'?`${Math.round(n).toLocaleString('ko-KR')}원`:`$${n.toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2})}`;}
function changeAmount(v,currency){const n=numberOrNaN(v);if(!Number.isFinite(n))return '—';const sign=n>0?'+':'';return currency==='KRW'?`${sign}${Math.round(n).toLocaleString('ko-KR')}`:`${sign}$${n.toFixed(2)}`;}
function pct(v,d=2){const n=numberOrNaN(v);if(!Number.isFinite(n))return '—';return `${n>0?'+':''}${n.toFixed(d)}%`;}
function marketSize(v){const n=numberOrNaN(v);if(!Number.isFinite(n))return '—';if(n>=1e12)return `${(n/1e12).toFixed(n>=100e12?0:1)}조`;if(n>=1e8)return `${(n/1e8).toFixed(0)}억`;return Math.round(n).toLocaleString('ko-KR');}
function changeClass(v){const n=numberOrNaN(v);return n>0?'change-up':n<0?'change-down':'';}
function stClass(v){return String(v)==='상승'?'st-up':String(v)==='하락'?'st-down':'';}
function opinionClass(text){const s=String(text||'').trim().toUpperCase();if(s.startsWith('BUY'))return 'opinion-buy';if(s.includes('SELL'))return 'opinion-sell';return 'opinion-hold';}
function backtestValue(stock){return numberOrNaN(stock?.supertrend?.backtest?.median_max_return_pct);}
function backtestText(stock){const bt=stock?.supertrend?.backtest||{};const med=backtestValue(stock);const n=Number(bt.completed_events||0);return Number.isFinite(med)?`최고수익 중위 ${pct(med,1)} · ${n}회`:'—';}
function opinionCode(stock){return String(stock?.opinion_code||'HOLD').toUpperCase();}
function opinionRank(stock){return Number(stock?.rank_level??OPINION_ORDER[opinionCode(stock)]??2);}
function stRank(v){return String(v)==='상승'?0:String(v)==='하락'?2:1;}
function localeCompare(a,b){return String(a??'').localeCompare(String(b??''),'ko',{numeric:true,sensitivity:'base'});}

function renderCapSelect(){const select=$('#capSelect');if(!select)return;const presets=CAP_FILTER_PRESETS[state.category]||[];const active=currentCapMin();select.innerHTML=presets.map(([v,label])=>`<option value="${v}" ${Number(v)===active?'selected':''}>${escapeHtml(label)}</option>`).join('');}
function sortDirectionForNewKey(key){return ['name','sector','opinion','st_d_direction','st_w_direction'].includes(key)?'asc':'desc';}
function compareStocks(a,b,key){
  if(key==='opinion') {
    const rankDiff=opinionRank(a)-opinionRank(b);if(rankDiff)return rankDiff;
    if(opinionCode(a)==='BUY'&&opinionCode(b)==='BUY'){
      const aa=numberOrNaN(a.buy_age_days),bb=numberOrNaN(b.buy_age_days);
      if(Number.isFinite(aa)&&Number.isFinite(bb)&&aa!==bb)return aa-bb;
      if(Number.isFinite(aa)&&!Number.isFinite(bb))return -1;
      if(!Number.isFinite(aa)&&Number.isFinite(bb))return 1;
    }
    return (Number(b.market_size_krw)||-1)-(Number(a.market_size_krw)||-1);
  }
  if(key==='name') return localeCompare(a.name,b.name);
  if(key==='sector') return localeCompare(a.sector,b.sector) || localeCompare(a.name,b.name);
  if(key==='st_d_direction') return stRank(a.st_d_direction)-stRank(b.st_d_direction) || (Number(b.market_size_krw)||-1)-(Number(a.market_size_krw)||-1);
  if(key==='st_w_direction') return stRank(a.st_w_direction)-stRank(b.st_w_direction) || (Number(b.market_size_krw)||-1)-(Number(a.market_size_krw)||-1);
  const av=key==='backtest'?backtestValue(a):numberOrNaN(a[key]);
  const bv=key==='backtest'?backtestValue(b):numberOrNaN(b[key]);
  if(Number.isFinite(av)&&Number.isFinite(bv))return av-bv;
  if(Number.isFinite(av))return -1;
  if(Number.isFinite(bv))return 1;
  return localeCompare(a.name,b.name);
}
function filteredItems(){
  const min=currentCapMin();
  const override=state.searchOverrideTicker;
  const raw=(currentData()?.items||[]).filter(s=>String(s.ticker)===String(override)||min<=0||(Number.isFinite(Number(s.market_size_krw))&&Number(s.market_size_krw)>=min));
  const dir=state.sort.dir==='desc'?-1:1;
  return [...raw].sort((a,b)=>dir*compareStocks(a,b,state.sort.key));
}
function updateSortHeaders(){
  $$('.header-row [data-sort]').forEach(th=>{
    const active=th.dataset.sort===state.sort.key;
    th.classList.toggle('sort-active',active);
    const mark=$('.sort-indicator',th);
    if(mark)mark.textContent=active?(state.sort.dir==='asc'?'▲':'▼'):'⌄';
    if(active)th.title=state.sort.key==='opinion'?(state.sort.dir==='asc'?'매수 우선 → 매도 우선으로 전환':'매도 우선 → 매수 우선으로 전환'):(state.sort.dir==='asc'?'오름차순':'내림차순');
  });
}

function renderSheet(){
  const body=$('#sheetBody');if(!body)return;
  const items=filteredItems(),data=currentData(),selected=state.selectedTicker;
  body.innerHTML=items.map((s,i)=>{
    const row=i+3, opinion=s.opinion_label||s.opinion||OPINION_TEXT[s.opinion_code]||'HOLD', selectedClass=String(s.ticker)===String(selected)?' selected':'';
    return `<tr class="stock-row${selectedClass}" data-ticker="${escapeHtml(s.ticker)}"><th class="row-number">${row}</th>
<td class="center ${opinionClass(opinion)}">${escapeHtml(opinion)}</td>
<td class="stock-name-cell">${escapeHtml(s.name)} <small>${escapeHtml(s.symbol||s.ticker)}</small></td>
<td data-chart-start>${escapeHtml(s.sector||'—')}</td>
<td class="num">${money(s.close,s.currency)}</td>
<td class="num ${changeClass(s.day_change_amount)}">${changeAmount(s.day_change_amount,s.currency)}</td>
<td class="num ${changeClass(s.day_change_pct)}">${pct(s.day_change_pct,2)}</td>
<td class="num">${marketSize(s.market_size_krw)}</td>
<td class="center ${stClass(s.st_d_direction)}">${escapeHtml(s.st_d_direction||'—')}</td>
<td class="center ${stClass(s.st_w_direction)}">${escapeHtml(s.st_w_direction||'—')}</td>
<td class="num">${Number.isFinite(numberOrNaN(s.adx))?Number(s.adx).toFixed(1):'—'}</td>
<td class="backtest-cell">${escapeHtml(backtestText(s))}</td>
<td class="chart-anchor-cell"> </td></tr>`;
  }).join('');
  const updatedAt=formatGeneratedAtKst(data),mode=String(data?.scan_mode||'').toUpperCase();
  const b1=`${updatedAt}${mode?` · ${mode}`:''}`;
  const status=`${CATEGORY[state.category].short} · 업데이트 ${updatedAt} KST${mode?` · ${mode}`:''} · ${items.length.toLocaleString()}개 · 가격수신 ${Number(data?.coverage_pct||0).toFixed(1)}%`;
  $('#sheetStatusCell').textContent=b1;$('#sheetStatusCell').title=status;$('#bottomStatus').textContent=status;updateSortHeaders();
  if(selected){requestAnimationFrame(()=>void hydrateSelectedChart(selected));}else hideChartOverlay();
}

function hideChartOverlay(){const overlay=$('#sheetChartOverlay');if(overlay){overlay.hidden=true;overlay.innerHTML='';}}
function positionChartOverlay(row){
  const overlay=$('#sheetChartOverlay'),scroll=$('#sheetScroll'),cell=$('[data-chart-start]',row);if(!overlay||!scroll||!cell)return false;
  const sr=scroll.getBoundingClientRect(),cr=cell.getBoundingClientRect();
  const top=cr.top-sr.top+scroll.scrollTop, left=cr.left-sr.left+scroll.scrollLeft;
  const visibleWidth=Math.max(760,Math.round(sr.right-Math.max(sr.left,cr.left)-8));
  overlay.style.top=`${Math.round(top)}px`;overlay.style.left=`${Math.round(left)}px`;overlay.style.width=`${visibleWidth}px`;
  overlay.style.height='324px';overlay.hidden=false;return true;
}
async function hydrateSelectedChart(ticker){
  const stock=stockByTicker(ticker),body=$('#sheetBody'),overlay=$('#sheetChartOverlay');if(!stock||!body||!overlay)return;
  const row=body.querySelector(`tr[data-ticker="${CSS.escape(String(ticker))}"]`);if(!row||!positionChartOverlay(row)){hideChartOverlay();return;}
  overlay.innerHTML='<div class="row-chart"><div class="chart-loading">차트를 불러오는 중입니다.</div></div>';
  try{const detail=await ensureDetail(stock);if(state.selectedTicker!==ticker)return;const currentRow=body.querySelector(`tr[data-ticker="${CSS.escape(String(ticker))}"]`);if(!currentRow||!positionChartOverlay(currentRow))return;overlay.innerHTML='<div class="row-chart" data-overlay-chart></div>';drawSheetChart($('[data-overlay-chart]',overlay),detail,stock);}catch(err){console.error(err);overlay.innerHTML='<div class="row-chart"><div class="chart-empty">차트를 불러오지 못했습니다.</div></div>';}
}
async function selectRow(ticker,{scroll=false}={}){
  state.selectedTicker=ticker;state.searchOverrideTicker=ticker;const stock=stockByTicker(ticker);if(!stock)return;
  const idx=filteredItems().findIndex(x=>String(x.ticker)===String(ticker));$('#nameBox').textContent=`B${idx+3}`;$('#formulaInput').value=String(stock.name||stock.symbol||stock.ticker);renderSheet();
  if(scroll)requestAnimationFrame(()=>$('#sheetBody')?.querySelector(`tr[data-ticker="${CSS.escape(String(ticker))}"]`)?.scrollIntoView({block:'center',inline:'nearest',behavior:'smooth'}));
}

function drawSheetChart(el,detail,stock){
  const st=detail?.supertrend||{},rows=st.chart||[];if(rows.length<15){el.innerHTML='<div class="chart-empty">차트 데이터 부족</div>';return;}
  const vals=rows.flatMap(r=>[numberOrNaN(r.low),numberOrNaN(r.high),numberOrNaN(r.supertrend),numberOrNaN(r.weekly_supertrend)]).filter(Number.isFinite);let lo=Math.min(...vals),hi=Math.max(...vals);if(!(hi>lo)){lo*=.99;hi*=1.01;}const ext=(hi-lo)*.06||1;lo-=ext;hi+=ext;
  const W=760,H=316,pad={l:9,r:58,t:13,b:22},plotW=W-pad.l-pad.r,plotH=H-pad.t-pad.b,step=plotW/rows.length,X=i=>pad.l+(i+.5)*step,Y=v=>pad.t+(hi-v)*plotH/(hi-lo);let grid='';
  for(let g=0;g<4;g++){const y=pad.t+g*plotH/3,v=hi-g*(hi-lo)/3,label=stock.currency==='KRW'?Math.round(v).toLocaleString('ko-KR'):`$${v.toFixed(Math.abs(v)>=100?0:1)}`;grid+=`<line x1="${pad.l}" x2="${W-pad.r}" y1="${y}" y2="${y}" class="chart-grid"/><text x="${W-pad.r+5}" y="${y+3}" class="price-axis">${label}</text>`;}
  const bw=Math.max(1.2,Math.min(4.8,step*.58));let candles='';rows.forEach((r,i)=>{const o=Number(r.open),h=Number(r.high),l=Number(r.low),c=Number(r.close);if(![o,h,l,c].every(Number.isFinite))return;const x=X(i),yo=Y(o),yc=Y(c),yh=Y(h),yl=Y(l),up=c>=o,cls=up?'chart-candle-up':'chart-candle-down',top=Math.min(yo,yc),bh=Math.max(1.1,Math.abs(yc-yo));candles+=`<line x1="${x}" x2="${x}" y1="${yh}" y2="${yl}" class="chart-candle-wick ${cls}"/><rect x="${x-bw/2}" y="${top}" width="${bw}" height="${bh}" class="${cls}"><title>${escapeHtml(r.date)} O ${o} H ${h} L ${l} C ${c}</title></rect>`;});
  function paths(valueKey,dirKey,upClass,downClass){let up='',down='',u=false,d=false;rows.forEach((r,i)=>{const v=numberOrNaN(r[valueKey]),dir=numberOrNaN(r[dirKey]);if(!Number.isFinite(v)){u=d=false;return;}if(dir>0){up+=`${u?'L':'M'}${X(i).toFixed(1)},${Y(v).toFixed(1)} `;u=true;d=false;}else if(dir<0){down+=`${d?'L':'M'}${X(i).toFixed(1)},${Y(v).toFixed(1)} `;d=true;u=false;}else{u=d=false;}});return `<path d="${up}" class="${upClass}"/><path d="${down}" class="${downClass}"/>`;}
  const dates=`<text x="${X(0)}" y="${H-4}" text-anchor="start" class="date-axis">${escapeHtml(rows[0].date.slice(5))}</text><text x="${X(rows.length-1)}" y="${H-4}" text-anchor="end" class="date-axis">${escapeHtml(rows.at(-1).date.slice(5))}</text>`;
  el.innerHTML=`<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">${grid}${candles}${paths('supertrend','direction','st-d-up','st-d-down')}${paths('weekly_supertrend','weekly_direction','st-w-up','st-w-down')}${dates}</svg><div class="chart-legend"><span>ST_D 실선</span><span>ST_W 점선</span><span>양봉 빨강 · 음봉 파랑</span></div><div class="chart-actions"><button class="chart-action-btn toss" type="button" data-open-external="${escapeHtml(stock.ticker)}">토스증권 열기 ↗</button><button class="chart-action-btn close" type="button" data-close-chart>닫기</button></div>`;
}

function tossProductCode(stock){const existing=String(stock?.toss_product_code||'').trim().toUpperCase();if(existing)return existing;const raw=String(stock?.symbol||stock?.ticker||'').trim().toUpperCase().replace(/\.(KS|KQ)$/i,'');if(String(stock?.category||'').startsWith('KR')){if(/^[0-9A-Z]{6}$/.test(raw))return `A${raw}`;if(/^A[0-9A-Z]{6}$/.test(raw))return raw;}return '';}
function tossDisplaySymbol(stock){return String(stock?.symbol||stock?.ticker||'').trim().toUpperCase().replace(/\.(KS|KQ)$/i,'').replace(/-/g,'.');}
function tossSearchHits(payload){if(Array.isArray(payload))return payload.filter(x=>x&&typeof x==='object');if(!payload||typeof payload!=='object')return [];for(const key of ['result','data'])if(Array.isArray(payload[key]))return payload[key].filter(x=>x&&typeof x==='object');return [];}
function tossInfoResult(payload){if(!payload||typeof payload!=='object')return null;const value=payload.result&&typeof payload.result==='object'&&!Array.isArray(payload.result)?payload.result:payload;return value&&typeof value==='object'?value:null;}
async function resolveTossProductCode(stock){
  const direct=tossProductCode(stock);if(direct)return direct;
  const ticker=tossDisplaySymbol(stock);if(!ticker)return '';
  // Current Toss WTS exposes a symbol-to-product-code resolver. Prefer it for US stocks/ETFs.
  try{
    const response=await fetch(`https://wts-info-api.tossinvest.com/api/v2/stock-infos/code-or-symbol/${encodeURIComponent(ticker)}`,{headers:{'Accept':'application/json'}});
    if(response.ok){
      const info=tossInfoResult(await response.json());
      const code=String(info?.code||info?.stockCode||'').trim().toUpperCase();
      if(code){stock.toss_product_code=code;return code;}
    }
  }catch(_){/* fall through to search resolver */}
  try{
    const response=await fetch('https://wts-info-api.tossinvest.com/api/v1/search-all/wts-auto-complete',{method:'POST',headers:{'Accept':'application/json','Content-Type':'application/json'},body:JSON.stringify({query:ticker})});
    if(!response.ok)return '';
    const hits=tossSearchHits(await response.json());
    const tickerNorm=ticker.replace(/\./g,'-');
    const chosen=hits.find(h=>String(h.symbol||'').trim().toUpperCase().replace(/\./g,'-')===tickerNorm&&h.stockCode)||hits.find(h=>h.stockCode);
    const code=String(chosen?.stockCode||'').trim().toUpperCase();if(code)stock.toss_product_code=code;return code;
  }catch(_){return '';}
}
async function openTossChart(stock){
  const popup=window.open('about:blank','_blank');if(!popup)return;
  try{popup.document.title='토스증권 여는 중';popup.document.body.innerHTML='<p style="font-family:sans-serif;padding:20px">토스증권 종목 페이지를 여는 중입니다...</p>';}catch(_){/* ignore */}
  const code=await resolveTossProductCode(stock);
  const symbol=tossDisplaySymbol(stock);
  if(!code&&navigator.clipboard?.writeText&&symbol){navigator.clipboard.writeText(symbol).catch(()=>{});$('#bottomStatus').textContent=`토스 종목코드를 찾지 못해 ${symbol} 티커를 복사했습니다.`;}
  const url=code?`https://www.tossinvest.com/stocks/${encodeURIComponent(code)}/order`:'https://www.tossinvest.com/';
  try{popup.location.replace(url);}catch(_){popup.location.href=url;}
}
function openExternal(stock){return openTossChart(stock);}

function normalizeSearch(v){return String(v||'').trim().toLowerCase().replace(/\s+/g,'');}
function searchScore(stock,q){
  const name=normalizeSearch(stock.name),ticker=normalizeSearch(stock.ticker),symbol=normalizeSearch(stock.symbol),needle=normalizeSearch(q);if(!needle)return -Infinity;
  if(name===needle||ticker===needle||symbol===needle)return 1000;
  if(name.startsWith(needle))return 900-Math.min(100,name.length-needle.length);
  if(symbol.startsWith(needle)||ticker.startsWith(needle))return 880;
  if(name.includes(needle))return 760-Math.min(100,name.indexOf(needle));
  if(symbol.includes(needle)||ticker.includes(needle))return 720;
  let j=0;for(const ch of name)if(ch===needle[j])j++;return j===needle.length?500-String(name).length:-Infinity;
}
async function findBestStock(query){
  await ensureAllData();let best=null,bestScore=-Infinity;
  CATEGORY_KEYS.forEach(category=>(state.data[category]?.items||[]).forEach(stock=>{const score=searchScore(stock,query);if(score>bestScore||(score===bestScore&&Number(stock.market_size_krw||0)>Number(best?.market_size_krw||0))){best={...stock,category:stock.category||category};bestScore=score;}}));
  return bestScore>0?best:null;
}
async function jumpToSearch(query){
  const input=$('#formulaInput'),q=String(query||'').trim();if(!q)return;const previous=input.value;input.value='검색 중...';input.disabled=true;
  try{const best=await findBestStock(q);if(!best){input.value=previous;$('#bottomStatus').textContent=`'${q}' 검색 결과 없음`;return;}
    const cat=best.category;state.capMin[cat]=Number(CAP_FILTER_PRESETS[cat]?.[0]?.[0]||0);await switchSheet(cat,false);state.searchOverrideTicker=best.ticker;state.selectedTicker=best.ticker;renderCapSelect();renderSheet();await selectRow(best.ticker,{scroll:true});$('#bottomStatus').textContent=`검색: ${best.name} (${best.symbol||best.ticker})`;
  }catch(err){console.error(err);input.value=previous;$('#bottomStatus').textContent='종목 검색 실패';}finally{input.disabled=false;input.focus();input.select();}
}

async function switchSheet(sheet,force=false){
  state.sheet=sheet;$$('.sheet-tab').forEach(b=>b.classList.toggle('active',b.dataset.sheet===sheet));
  if(sheet==='QUIZ'){$('#marketSheetView').hidden=true;$('#quizSheetView').hidden=false;hideChartOverlay();$('#nameBox').textContent='A1';$('#formulaInput').value='Quiz · SuperTrend(14,2) · 1문제 / 보기 5개';return;}
  $('#quizSheetView').hidden=true;$('#marketSheetView').hidden=false;state.category=sheet;state.selectedTicker=null;state.searchOverrideTicker=null;renderCapSelect();hideChartOverlay();$('#sheetBody').innerHTML='<tr><th class="row-number">3</th><td colspan="12">데이터를 불러오는 중입니다.</td></tr>';
  try{await ensureData(sheet,force||Boolean(state.data[sheet]));renderCapSelect();renderSheet();$('#formulaInput').value='';$('#nameBox').textContent='A1';}catch(err){console.error(err);$('#sheetBody').innerHTML='<tr><th class="row-number">3</th><td colspan="12">데이터를 불러오지 못했습니다.</td></tr>';$('#sheetStatusCell').textContent='데이터 로드 실패';}
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
  const multipliers = [0.72, 0.84, 1.16, 1.32];
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
  // network search is required to manufacture the four distractors (5 choices total).
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

    const correctIndex = quizRandomInt(0, 4);
    const options = [];
    let di = 0;
    for (let i=0;i<5;i++) options.push(i === correctIndex ? built.correct : built.distractors[di++]);

    return {
      entry,
      stock,
      rows,
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

function quizSupertrend(candles, length=14, multiplier=2.0) {
  const n=candles?.length||0, atr=Array(n).fill(NaN), upper=Array(n).fill(NaN), lower=Array(n).fill(NaN), st=Array(n).fill(NaN), dir=Array(n).fill(0), tr=Array(n).fill(NaN);
  if(!n)return {atr,upper,lower,st,dir};
  for(let i=0;i<n;i++){
    const h=Number(candles[i].high),l=Number(candles[i].low),c=Number(candles[i].close),pc=i?Number(candles[i-1].close):NaN;
    if(![h,l,c].every(Number.isFinite))continue;
    tr[i]=i===0||!Number.isFinite(pc)?h-l:Math.max(h-l,Math.abs(h-pc),Math.abs(l-pc));
  }
  if(n<length)return {atr,upper,lower,st,dir};
  const seed=tr.slice(0,length);if(seed.some(v=>!Number.isFinite(v)))return {atr,upper,lower,st,dir};
  atr[length-1]=seed.reduce((a,b)=>a+b,0)/length;
  for(let i=length;i<n;i++)if(Number.isFinite(tr[i]))atr[i]=(atr[i-1]*(length-1)+tr[i])/length;
  const first=length-1;
  for(let i=first;i<n;i++){
    const h=Number(candles[i].high),l=Number(candles[i].low),c=Number(candles[i].close),pc=i?Number(candles[i-1].close):NaN;
    if(![h,l,c,atr[i]].every(Number.isFinite))continue;
    const hl2=(h+l)/2,bu=hl2+multiplier*atr[i],bl=hl2-multiplier*atr[i];
    if(i===first){upper[i]=bu;lower[i]=bl;dir[i]=-1;st[i]=upper[i];continue;}
    upper[i]=(bu<upper[i-1]||pc>upper[i-1])?bu:upper[i-1];
    lower[i]=(bl>lower[i-1]||pc<lower[i-1])?bl:lower[i-1];
    if(Math.abs(st[i-1]-upper[i-1])<=1e-10*Math.max(1,Math.abs(st[i-1]))){
      if(c>upper[i]){dir[i]=1;st[i]=lower[i];}else{dir[i]=-1;st[i]=upper[i];}
    }else{
      if(c<lower[i]){dir[i]=-1;st[i]=upper[i];}else{dir[i]=1;st[i]=lower[i];}
    }
  }
  return {atr,upper,lower,st,dir};
}
function quizSTPaths(stData,startIndex,count,X,Y,upClass,downClass){
  let up='',down='',u=false,d=false;
  for(let j=0;j<count;j++){
    const i=startIndex+j,v=Number(stData.st[i]),direction=Number(stData.dir[i]);
    if(!Number.isFinite(v)){u=d=false;continue;}
    if(direction>0){up+=`${u?'L':'M'}${X(j).toFixed(1)},${Y(v).toFixed(1)} `;u=true;d=false;}
    else if(direction<0){down+=`${d?'L':'M'}${X(j).toFixed(1)},${Y(v).toFixed(1)} `;d=true;u=false;}else{u=d=false;}
  }
  return `<path d="${up}" class="${upClass}"/><path d="${down}" class="${downClass}"/>`;
}

function renderQuizOption(option, index, correctIndex, selectedIndex, answered, question) {
  const candles=option?.candles||[], visible=question?.rows?.slice(0,question.hiddenStart)||[];
  const combined=[...visible.map(r=>({open:Number(r.open),high:Number(r.high),low:Number(r.low),close:Number(r.close)})),...candles];
  const stData=quizSupertrend(combined,14,2.0),startIndex=visible.length;
  const W=260,H=126,pad={l:18,r:18,t:11,b:10};
  const extrema=candles.flatMap((c,j)=>[Number(c.low),Number(c.high),Number(stData.st[startIndex+j])]).filter(Number.isFinite);
  let lo=Math.min(...extrema),hi=Math.max(...extrema);if(!(hi>lo)){lo*=.99;hi*=1.01;}const extra=(hi-lo)*.08||1;lo-=extra;hi+=extra;
  const step=(W-pad.l-pad.r)/Math.max(1,candles.length),X=i=>pad.l+(i+.5)*step,Y=v=>pad.t+(hi-v)*(H-pad.t-pad.b)/(hi-lo),bodyW=Math.max(2.2,Math.min(5.5,step*.58));
  const selected=selectedIndex===index,klass=answered?(index===correctIndex?' correct':selected?' wrong':''):selected?' selected':'';let candleSvg='';
  candles.forEach((r,i)=>{if(![r.open,r.high,r.low,r.close].every(Number.isFinite))return;const x=X(i),yo=Y(r.open),yc=Y(r.close),yh=Y(r.high),yl=Y(r.low),up=r.close>=r.open,ck=up?'quiz-candle-up':'quiz-candle-down',top=Math.min(yo,yc),bh=Math.max(1.2,Math.abs(yc-yo));candleSvg+=`<line x1="${x}" x2="${x}" y1="${yh}" y2="${yl}" class="quiz-option-wick ${ck}"/><rect x="${x-bodyW/2}" y="${top}" width="${bodyW}" height="${bh}" class="quiz-option-body ${ck}" rx=".35"/>`;});
  return `<button class="quiz-choice${klass}" type="button" data-quiz-choice="${index}" ${answered?'disabled':''}><span class="quiz-choice-key">${index+1}</span><svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-label="보기 ${index+1}"><line x1="${pad.l}" x2="${W-pad.r}" y1="${pad.t}" y2="${pad.t}" class="quiz-option-grid"/><line x1="${pad.l}" x2="${W-pad.r}" y1="${H/2}" y2="${H/2}" class="quiz-option-grid"/><line x1="${pad.l}" x2="${W-pad.r}" y1="${H-pad.b}" y2="${H-pad.b}" class="quiz-option-grid"/>${candleSvg}${quizSTPaths(stData,startIndex,candles.length,X,Y,'quiz-option-st-up','quiz-option-st-down')}</svg></button>`;
}

function renderQuizMainChart(question, answered=false) {
  const el=$('#quizMainChart');if(!el||!question)return;
  const {rows,hiddenStart,hiddenEnd,selectedIndex,options}=question;
  const preview=(!answered&&Number.isInteger(selectedIndex)&&selectedIndex>=0&&selectedIndex<options.length)?options[selectedIndex]:null;
  const shownCandles=answered?rows.map(r=>({open:Number(r.open),high:Number(r.high),low:Number(r.low),close:Number(r.close)})):[...rows.slice(0,hiddenStart).map(r=>({open:Number(r.open),high:Number(r.high),low:Number(r.low),close:Number(r.close)})),...(preview?.candles||[])];
  const stData=quizSupertrend(shownCandles,14,2.0);
  const W=1000,H=430,pad={l:18,r:72,t:22,b:32},plotW=W-pad.l-pad.r,plotH=H-pad.t-pad.b,step=plotW/rows.length,X=i=>pad.l+(i+.5)*step;
  const vals=[];shownCandles.forEach((r,i)=>{vals.push(r.low,r.high,stData.st[i]);});let lo=Math.min(...vals.filter(Number.isFinite)),hi=Math.max(...vals.filter(Number.isFinite));if(!(hi>lo)){lo*=.99;hi*=1.01;}const extra=(hi-lo)*.055||1;lo-=extra;hi+=extra;const Y=v=>pad.t+(hi-v)*plotH/(hi-lo),bodyW=Math.max(2.1,step*.56),hiddenX1=pad.l+hiddenStart*step,hiddenX2=pad.l+hiddenEnd*step;
  let grid='';for(let g=0;g<5;g++){const y=pad.t+g*plotH/4,v=hi-g*(hi-lo)/4,label=question.stock.currency==='KRW'?Math.round(v).toLocaleString('ko-KR'):`$${v.toFixed(v>=100?0:1)}`;grid+=`<line x1="${pad.l}" x2="${W-pad.r}" y1="${y}" y2="${y}" class="quiz-chart-grid"/><text x="${W-pad.r+7}" y="${y+4}" class="quiz-price-axis">${label}</text>`;}
  let candles='';shownCandles.forEach((r,i)=>{const x=X(i),yo=Y(r.open),yc=Y(r.close),yh=Y(r.high),yl=Y(r.low),up=r.close>=r.open,klass=up?'quiz-candle-up':'quiz-candle-down',top=Math.min(yo,yc),bh=Math.max(1.4,Math.abs(yc-yo));candles+=`<line x1="${x}" x2="${x}" y1="${yh}" y2="${yl}" class="quiz-candle-wick ${klass}"/><rect x="${x-bodyW/2}" y="${top}" width="${bodyW}" height="${bh}" class="${klass}" rx=".5"/>`;});
  function mainSTPaths(){let up='',down='',u=false,d=false;for(let i=0;i<shownCandles.length;i++){const v=Number(stData.st[i]),direction=Number(stData.dir[i]);if(!Number.isFinite(v)){u=d=false;continue;}if(direction>0){up+=`${u?'L':'M'}${X(i).toFixed(1)},${Y(v).toFixed(1)} `;u=true;d=false;}else if(direction<0){down+=`${d?'L':'M'}${X(i).toFixed(1)},${Y(v).toFixed(1)} `;d=true;u=false;}}return `<path d="${up}" class="quiz-st-up"/><path d="${down}" class="quiz-st-down"/>`;}
  const dateIndices=[0,44,89],dates=dateIndices.map(i=>{const label=answered?(rows[i]?.date?.slice(2)||''):`D${i+1}`,anchor=i===0?'start':i===89?'end':'middle';return `<text x="${i===0?pad.l:i===89?W-pad.r:X(i)}" y="${H-9}" text-anchor="${anchor}" class="quiz-date-axis">${escapeHtml(label)}</text>`;}).join('');
  const hiddenOverlay=answered?`<rect x="${hiddenX1}" y="${pad.t}" width="${hiddenX2-hiddenX1}" height="${plotH}" class="quiz-reveal-zone"/>`:(!preview?`<rect x="${hiddenX1}" y="${pad.t}" width="${hiddenX2-hiddenX1}" height="${plotH}" class="quiz-hidden-block"/><text x="${(hiddenX1+hiddenX2)/2}" y="${pad.t+plotH/2}" text-anchor="middle" class="quiz-hidden-label">HIDDEN</text>`:`<rect x="${hiddenX1}" y="${pad.t}" width="${hiddenX2-hiddenX1}" height="${plotH}" class="quiz-preview-zone"/>`);
  el.innerHTML=`<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">${grid}${candles}${mainSTPaths()}${hiddenOverlay}${dates}</svg><div class="quiz-chart-legend"><span>CANDLE</span><span>SuperTrend 14,2</span><span>상승 ST 빨강 · 하락 ST 파랑</span></div>`;
}

function renderQuizQuestion() {
  const q=state.quiz.question;
  if(!q) return;
  $('#quizStatus').hidden=true;
  $('#quizGame').hidden=false;
  $('#quizNumber').textContent='QUIZ';
  $('#quizIdentity').textContent=state.quiz.answered
    ? `${q.stock.name} (${q.stock.symbol || q.stock.ticker}) · ${q.rows[0].date} ~ ${q.rows[q.rows.length-1].date}`
    : '종목 · 기간 비공개';
  $('#quizSegmentLabel').textContent='1문제 · 보기 5개 · 다음 30거래일 가림';
  const instruction=$('#quizInstructionSub');
  if(instruction){
    instruction.textContent=state.quiz.answered
      ? '제출 후 실제 다음 30거래일 캔들과 SuperTrend를 공개합니다.'
      : '앞 60거래일의 캔들과 SuperTrend(14,2)를 보고 5개 보기 중 다음 30거래일을 고르세요.';
  }
  renderQuizMainChart(q,state.quiz.answered);
  $('#quizChoices').innerHTML=q.options.map((option,i)=>renderQuizOption(option,i,q.correctIndex,q.selectedIndex,state.quiz.answered,q)).join('');
  const submitBtn = $('#quizSubmitBtn');
  if (submitBtn) submitBtn.disabled = state.quiz.answered || !Number.isInteger(q.selectedIndex);
  const selectedHint = $('#quizSelectedHint');
  if (selectedHint) {
    selectedHint.textContent = state.quiz.answered
      ? `제출 완료 · 선택한 보기 ${Number.isInteger(q.selectedIndex) ? q.selectedIndex + 1 : '—'}번`
      : Number.isInteger(q.selectedIndex)
        ? `${q.selectedIndex + 1}번 보기를 차트에 적용했습니다. 마음에 들면 정답 제출을 누르세요.`
        : '보기를 선택하면 캔들과 SuperTrend가 가려진 구간에 함께 적용됩니다.';
  }
  const feedback=$('#quizFeedback');
  if(state.quiz.answered){
    feedback.hidden=false;
    const ok=q.selectedIndex===q.correctIndex;
    feedback.innerHTML=`<b>${ok?'정답입니다.':'오답입니다.'}</b> 정답은 ${q.correctIndex+1}번입니다. 실제 종목은 ${escapeHtml(q.stock.name)} (${escapeHtml(q.stock.symbol || q.stock.ticker)})이며, 가려진 30거래일 캔들과 SuperTrend(14,2)를 공개했습니다.`;
  }else{
    feedback.hidden=true;
    feedback.textContent='';
  }
  const newBtn=$('#newQuizBtn');
  if(newBtn && !state.quiz.loading){
    newBtn.disabled = !state.quiz.answered && Boolean(state.quiz.question);
    newBtn.textContent = state.quiz.answered ? '새 문제' : '문제 시작';
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
    renderQuizQuestion();
  }catch(err){
    console.error('quiz',err);
    $('#quizStatus').hidden=false;
    $('#quizStatus').textContent='퀴즈 데이터가 아직 없습니다. 최신 코드 배포 후 ALL · FULL 스캔을 한 번 실행해 주세요.';
    $('#quizGame').hidden=true;
  }finally{
    state.quiz.loading=false;
    if(btn){
      btn.disabled = !state.quiz.answered && Boolean(state.quiz.question);
      btn.textContent = state.quiz.answered ? '새 문제' : (state.quiz.question ? '현재 문제' : '문제 시작');
    }
  }
}



$('#capSelect')?.addEventListener('change',e=>{state.capMin[state.category]=Number(e.target.value)||0;state.selectedTicker=null;state.searchOverrideTicker=null;hideChartOverlay();renderSheet();});
$('.sheet-nav')?.addEventListener('click',e=>{const b=e.target.closest('[data-sheet]');if(b)void switchSheet(b.dataset.sheet);});
$('.header-row')?.addEventListener('click',e=>{const th=e.target.closest('[data-sort]');if(!th)return;const key=th.dataset.sort;if(!SORT_KEYS.has(key))return;if(state.sort.key===key)state.sort.dir=state.sort.dir==='asc'?'desc':'asc';else{state.sort.key=key;state.sort.dir=sortDirectionForNewKey(key);}renderSheet();});
$('#sheetBody')?.addEventListener('click',e=>{const row=e.target.closest('tr[data-ticker]');if(row)void selectRow(row.dataset.ticker);});
$('#sheetChartOverlay')?.addEventListener('click',e=>{
  const close=e.target.closest('[data-close-chart]');if(close){e.preventDefault();e.stopPropagation();state.selectedTicker=null;hideChartOverlay();renderSheet();return;}
  const external=e.target.closest('[data-open-external]');if(!external)return;e.preventDefault();e.stopPropagation();const stock=stockByTicker(external.dataset.openExternal);if(stock)void openExternal(stock);
});
$('#formulaInput')?.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();void jumpToSearch(e.currentTarget.value);}});
$('#formulaInput')?.addEventListener('focus',e=>{e.currentTarget.select();});
$('#newQuizBtn')?.addEventListener('click',()=>void newQuizQuestion(false));
$('#quizChoices')?.addEventListener('click',e=>{const b=e.target.closest('[data-quiz-choice]');if(!b||state.quiz.answered||!state.quiz.question)return;const i=Number(b.dataset.quizChoice);if(!Number.isInteger(i)||i<0||i>4)return;state.quiz.question.selectedIndex=i;renderQuizQuestion();});
$('#quizSubmitBtn')?.addEventListener('click',()=>{if(!state.quiz.question||state.quiz.answered||!Number.isInteger(state.quiz.question.selectedIndex))return;state.quiz.answered=true;renderQuizQuestion();});

window.addEventListener('resize',()=>{if(state.selectedTicker)requestAnimationFrame(()=>void hydrateSelectedChart(state.selectedTicker));});

let autoRefreshBusy=false,lastAutoRefreshAt=0;
async function autoRefreshCurrentCategory(force=false){
  if(autoRefreshBusy||state.sheet==='QUIZ'||document.visibilityState==='hidden')return;
  const now=Date.now();if(!force&&now-lastAutoRefreshAt<AUTO_REFRESH_MIN_GAP_MS)return;
  autoRefreshBusy=true;lastAutoRefreshAt=now;
  try{
    const changed=await refreshCategoryData(state.category);
    if(changed)console.info(`[DTC] ${state.category} summary auto-refreshed at ${formatGeneratedAtKst(currentData())} KST`);
  }catch(err){console.warn('[DTC] automatic summary refresh failed',err);}
  finally{autoRefreshBusy=false;}
}
setInterval(()=>void autoRefreshCurrentCategory(false),AUTO_REFRESH_INTERVAL_MS);
document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible')void autoRefreshCurrentCategory(true);});
window.addEventListener('focus',()=>void autoRefreshCurrentCategory(true));

void switchSheet('KR');
