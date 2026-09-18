import { decryptEnvelope } from "./crypto.mjs";

const $ = (selector) => document.querySelector(selector);
let encryptedEnvelope = null;
let portfolio = null;
let watchlist = null;

const won = new Intl.NumberFormat("ko-KR", { style: "currency", currency: "KRW", maximumFractionDigits: 0 });
const usd = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 2 });
const qty = new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 6 });
const pct = new Intl.NumberFormat("ko-KR", { minimumFractionDigits: 2, maximumFractionDigits: 2, signDisplay: "exceptZero" });

function number(value) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function money(value, currency = "KRW") {
  return currency === "USD" ? usd.format(number(value)) : won.format(number(value));
}

function percent(value, digits = 2) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return "—";
  return `${parsed > 0 ? "+" : ""}${parsed.toFixed(digits)}%`;
}

function marketCap(value) {
  const amount = number(value);
  if (amount >= 1e12) return `${(amount / 1e12).toFixed(amount >= 1e14 ? 0 : 1)}조`;
  if (amount >= 1e8) return `${(amount / 1e8).toFixed(0)}억`;
  return amount ? won.format(amount) : "—";
}

function setText(selector, value) {
  const target = $(selector);
  if (target) target.textContent = value;
}

function compactDate(value) {
  return String(value || "").replace(/[^0-9]/g, "").slice(0, 8);
}

function dateText(value, includeTime = false) {
  if (!value) return "—";
  const compact = String(value).replace(/[^0-9]/g, "");
  if (/^\d{8}/.test(compact)) {
    const base = `${compact.slice(0, 4)}.${compact.slice(4, 6)}.${compact.slice(6, 8)}`;
    if (includeTime && compact.length >= 12) return `${base} ${compact.slice(8, 10)}:${compact.slice(10, 12)}`;
    return base;
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat("ko-KR", {
    year: "numeric", month: "2-digit", day: "2-digit",
    ...(includeTime ? { hour: "2-digit", minute: "2-digit" } : {}),
  }).format(date);
}

function pnlClass(value) {
  return number(value) > 0 ? "positive" : number(value) < 0 ? "negative" : "";
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
  })[character]);
}

function activateTab(name) {
  const isWatchlist = name === "watchlist";
  $("#watchlistTab").classList.toggle("is-active", isWatchlist);
  $("#portfolioTab").classList.toggle("is-active", !isWatchlist);
  $("#watchlistTab").setAttribute("aria-selected", String(isWatchlist));
  $("#portfolioTab").setAttribute("aria-selected", String(!isWatchlist));
  $("#watchlistView").classList.toggle("is-hidden", !isWatchlist);
  $("#portfolioView").classList.toggle("is-hidden", isWatchlist);
  history.replaceState(null, "", isWatchlist ? "#watchlist" : "#portfolio");
}

function trendBadge(direction, label = "") {
  const known = direction === "UP" || direction === "DOWN";
  const text = direction === "UP" ? "상승" : direction === "DOWN" ? "하락" : "미확인";
  return `<span class="trend ${known ? direction.toLowerCase() : "unknown"}">${escapeHtml(label)}${text}</span>`;
}

function slopeBadge(value) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return '<span class="trend unknown">미확인</span>';
  return `<span class="trend ${number(value) > 0 ? "up" : number(value) < 0 ? "down" : "flat"}">${number(value) > 0 ? "↗" : number(value) < 0 ? "↘" : "→"} ${percent(value)}</span>`;
}

function bandBadge(label) {
  const classes = { "최하단": "bottom-most", "하단": "bottom", "중단": "middle", "상단": "top", "최상단": "top-most" };
  return `<span class="band ${classes[label] || "unknown"}">${escapeHtml(label || "미확인")}</span>`;
}

function renderWatchlistRows() {
  const allRows = Array.isArray(watchlist?.items) ? watchlist.items : [];
  const market = $("#watchMarketFilter").value;
  const band = $("#watchBandFilter").value;
  const rows = allRows.filter((item) => {
    const marketMatch = market === "ALL" || item.market === market || (market === "ETF" && item.kind === "leveraged_etf");
    return marketMatch && (band === "ALL" || item.band === band);
  });
  $("#watchlistEmpty").classList.toggle("is-hidden", rows.length > 0);
  $("#watchlistBody").innerHTML = rows.map((item, index) => {
    const currency = item.currency || (item.market === "US" ? "USD" : "KRW");
    return `<tr class="${item.band === "최하단" ? "priority-row" : ""}">
      <td><span class="rank">${index + 1}</span></td>
      <td><div class="instrument"><strong>${escapeHtml(item.name || item.code)}</strong><span>${escapeHtml(item.code)} · ${item.kind === "leveraged_etf" ? "3X ETF" : escapeHtml(item.market)}${item.stale ? " · 이전 데이터" : ""}</span></div></td>
      <td>${bandBadge(item.band)}</td>
      <td class="numeric"><span class="score">${number(item.score).toFixed(0)}</span><small>/30</small></td>
      <td>${trendBadge(item.st_10_3)}</td><td>${trendBadge(item.st_20_4)}</td>
      <td>${slopeBadge(item.ma60_slope_pct)}</td><td>${slopeBadge(item.ma200_slope_pct)}</td>
      <td class="numeric"><strong>${money(item.price, currency)}</strong><small class="${pnlClass(item.change_pct)}">${percent(item.change_pct)}</small></td>
      <td class="numeric ${pnlClass(item.vs_sma60_pct)}"><strong>${percent(item.vs_sma60_pct)}</strong></td>
      <td><span class="sector-chip">${escapeHtml(item.sector || "기타")}</span></td>
      <td class="numeric">${item.kind === "leveraged_etf" ? "3X ETF" : marketCap(item.market_cap_krw)}</td>
    </tr>`;
  }).join("");
}

function renderWatchlist(data) {
  watchlist = data;
  const rows = Array.isArray(data.items) ? data.items : [];
  setText("#watchUpdatedAt", dateText(data.updated_at, true));
  setText("#watchSource", data.source || data.message || "NHPLUG 시세");
  setText("#watchEligible", `${rows.length}개`);
  setText("#watchScanned", `후보 ${data.scanned_count || 0}개 분석`);
  setText("#watchBottomCount", `${rows.filter((item) => item.band === "최하단").length}개`);
  setText("#watchTopScore", rows.length ? `${Math.max(...rows.map((item) => number(item.score)))}점` : "—");
  const warnings = Array.isArray(data.warnings) ? data.warnings : [];
  $("#watchWarningPanel").classList.toggle("is-hidden", warnings.length === 0);
  $("#watchWarnings").innerHTML = warnings.map((warning) => `<li>${escapeHtml(warning)}</li>`).join("");
  renderWatchlistRows();
}

async function loadWatchlist() {
  try {
    const response = await fetch(`./data/watchlist.json?v=${Date.now()}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`관심종목 데이터를 불러오지 못했습니다 (${response.status}).`);
    renderWatchlist(await response.json());
  } catch (error) {
    renderWatchlist({ items: [], warnings: [error.message], message: error.message });
  }
}

function openPinModal() {
  const message = $("#pinMessage");
  const button = $("#unlockButton");
  $("#pin").value = "";
  message.textContent = "";
  button.disabled = false;
  if (!encryptedEnvelope) {
    message.textContent = "암호화 데이터를 불러오는 중입니다…";
    button.disabled = true;
  } else if (encryptedEnvelope.empty) {
    message.textContent = encryptedEnvelope.message || "먼저 자산 업데이트 배치를 실행하세요.";
    button.disabled = true;
  }
  $("#pinModal").classList.remove("is-hidden");
  document.body.classList.add("modal-open");
  requestAnimationFrame(() => $("#pin").focus());
}

function closePinModal({ returnToWatchlist = false } = {}) {
  $("#pinModal").classList.add("is-hidden");
  document.body.classList.remove("modal-open");
  $("#pin").value = "";
  if (returnToWatchlist && !portfolio) activateTab("watchlist");
}

function renderHoldings(holdings) {
  const rows = Array.isArray(holdings) ? holdings : [];
  setText("#positionsCount", `${rows.length}개`);
  $("#holdingsEmpty").classList.toggle("is-hidden", rows.length > 0);
  $("#holdingsBody").innerHTML = rows.map((item) => `<tr>
    <td><div class="instrument"><strong>${escapeHtml(item.name || item.code)}</strong><span>${escapeHtml(item.code)} · ${escapeHtml(item.market)}</span></div></td>
    <td><span class="sector-chip">${escapeHtml(item.sector || "기타")}</span></td>
    <td class="numeric strong">${money(item.eval_amount_krw)}</td>
    <td class="numeric ${pnlClass(item.pnl_amount_krw)}"><strong>${money(item.pnl_amount_krw)}</strong></td>
    <td class="numeric ${pnlClass(item.pnl_pct)}"><strong>${percent(item.pnl_pct)}</strong></td>
    <td>${bandBadge(item.band)}</td><td>${trendBadge(item.st_14_3)}</td>
    <td>${slopeBadge(item.ma60_slope_pct)}</td><td>${slopeBadge(item.ma200_slope_pct)}</td>
  </tr>`).join("");
}

function eventDate(value) {
  return compactDate(value);
}

function renderSelectedPoint(point) {
  if (!point) return;
  const day = eventDate(point.at);
  setText("#eventDetailTitle", dateText(point.at, true));
  setText("#selectedAsset", money(point.total_asset_krw));
  const selectedRealized = $("#selectedRealized");
  selectedRealized.textContent = money(point.cumulative_realized_krw);
  selectedRealized.className = pnlClass(point.cumulative_realized_krw);
  const realized = (portfolio.realized_events || []).filter((item) => eventDate(item.date) === day);
  const flows = (portfolio.cash_flows || []).filter((item) => eventDate(item.date) === day);
  const parts = [
    ...realized.map((item) => `<article class="event-item"><span class="event-icon realized">R</span><div><strong>${escapeHtml(item.name || item.code)}</strong><small>${qty.format(number(item.qty))}주 · ${money(item.sell_price, item.currency === "USD" ? "USD" : "KRW")}</small></div><b class="${pnlClass(item.realized_pnl_krw)}">${money(item.realized_pnl_krw)}</b></article>`),
    ...flows.map((item) => `<article class="event-item"><span class="event-icon flow">${item.side === "DEPOSIT" ? "+" : "−"}</span><div><strong>${item.side === "DEPOSIT" ? "입금" : "출금"}</strong><small>${escapeHtml(item.label || "계좌 현금흐름")}</small></div><b class="${item.side === "DEPOSIT" ? "positive" : "negative"}">${item.side === "DEPOSIT" ? "+" : "−"}${money(item.amount_krw)}</b></article>`),
  ];
  $("#selectedEvents").innerHTML = parts.length ? parts.join("") : '<p class="muted">해당 날짜의 실현 종목 또는 입출금이 없습니다.</p>';
}

function samplePoints(points, maximum = 240) {
  if (points.length <= maximum) return points;
  const sampled = [];
  const step = (points.length - 1) / (maximum - 1);
  for (let index = 0; index < maximum; index += 1) sampled.push(points[Math.round(index * step)]);
  return sampled;
}

function renderYieldChart(historyRows) {
  const host = $("#yieldChart");
  const points = samplePoints(Array.isArray(historyRows) ? historyRows : []);
  if (!points.length) {
    host.innerHTML = '<div class="chart-empty">시간당 업데이트가 시작되면 자산과 실현수익 추이가 누적됩니다.</div>';
    return;
  }
  const assets = points.map((point) => number(point.total_asset_krw));
  const realized = points.map((point) => number(point.cumulative_realized_krw));
  const width = 820; const height = 260; const pad = 24;
  const domain = (values) => {
    let low = Math.min(...values); let high = Math.max(...values);
    if (low === high) { const spread = Math.max(1, Math.abs(high) * 0.02); low -= spread; high += spread; }
    return [low, high];
  };
  const [assetLow, assetHigh] = domain(assets);
  const [realizedLow, realizedHigh] = domain(realized);
  const x = (index) => pad + (index / Math.max(1, points.length - 1)) * (width - pad * 2);
  const y = (value, low, high) => height - pad - ((value - low) / Math.max(1, high - low)) * (height - pad * 2);
  const assetLine = points.map((point, index) => `${x(index)},${y(number(point.total_asset_krw), assetLow, assetHigh)}`).join(" ");
  const realizedLine = points.map((point, index) => `${x(index)},${y(number(point.cumulative_realized_krw), realizedLow, realizedHigh)}`).join(" ");
  const eventDays = new Set([...(portfolio.realized_events || []), ...(portfolio.cash_flows || [])].map((item) => eventDate(item.date)));
  host.innerHTML = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="총자산과 누적 실현손익 추이">
    <defs><linearGradient id="assetFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#51e4ba" stop-opacity=".2"/><stop offset="1" stop-color="#51e4ba" stop-opacity="0"/></linearGradient></defs>
    <polygon points="${pad},${height - pad} ${assetLine} ${width - pad},${height - pad}" fill="url(#assetFill)"/>
    <polyline points="${assetLine}" fill="none" stroke="#51e4ba" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>
    <polyline points="${realizedLine}" fill="none" stroke="#9d8cff" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" stroke-dasharray="7 5"/>
    ${points.map((point, index) => `<circle class="chart-point ${eventDays.has(eventDate(point.at)) ? "has-event" : ""}" data-index="${index}" cx="${x(index)}" cy="${y(number(point.total_asset_krw), assetLow, assetHigh)}" r="${eventDays.has(eventDate(point.at)) ? 5 : 3}"/>`).join("")}
  </svg><div class="chart-axis"><span>${dateText(points[0].at)}</span><strong>${money(assets.at(-1))}</strong><span>${dateText(points.at(-1).at)}</span></div>`;
  host.querySelectorAll(".chart-point").forEach((node) => node.addEventListener("click", () => {
    host.querySelectorAll(".chart-point").forEach((point) => point.classList.remove("selected"));
    node.classList.add("selected");
    renderSelectedPoint(points[Number(node.dataset.index)]);
  }));
  renderSelectedPoint(points.at(-1));
}

const sectorColors = ["#51e4ba", "#7fa8ff", "#9d8cff", "#f6bd60", "#ef7185", "#66c7d8", "#b5d86b", "#d88dd2"];

function renderSectorAnalytics(holdings) {
  const grouped = new Map();
  (holdings || []).forEach((item) => {
    const sector = item.sector || "기타";
    const current = grouped.get(sector) || { evaluation: 0, pnl: 0, principal: 0 };
    current.evaluation += number(item.eval_amount_krw);
    current.pnl += number(item.pnl_amount_krw);
    current.principal += number(item.eval_amount_krw) - number(item.pnl_amount_krw);
    grouped.set(sector, current);
  });
  const rows = [...grouped.entries()].sort((a, b) => Math.abs(b[1].pnl) - Math.abs(a[1].pnl));
  setText("#sectorCount", rows.length);
  const totalWeight = rows.reduce((sum, [, value]) => sum + (Math.abs(value.pnl) || value.evaluation), 0) || 1;
  let cursor = 0;
  const stops = rows.map(([, value], index) => {
    const start = cursor; cursor += ((Math.abs(value.pnl) || value.evaluation) / totalWeight) * 360;
    return `${sectorColors[index % sectorColors.length]} ${start}deg ${cursor}deg`;
  });
  $("#sectorDonut").style.background = rows.length ? `conic-gradient(${stops.join(",")})` : "#182439";
  $("#sectorLegend").innerHTML = rows.map(([sector, value], index) => {
    const returnPct = value.principal ? value.pnl / value.principal * 100 : 0;
    return `<div><i style="background:${sectorColors[index % sectorColors.length]}"></i><span>${escapeHtml(sector)}</span><b class="${pnlClass(value.pnl)}">${percent(returnPct)}</b></div>`;
  }).join("") || '<p class="muted">보유종목 데이터가 없습니다.</p>';
}

function renderMonthly(rows) {
  const data = (Array.isArray(rows) ? rows : []).slice(-12);
  if (!data.length) {
    $("#monthlyChart").innerHTML = '<div class="chart-empty">실현손익 기록이 누적되면 월별 수익률이 표시됩니다.</div>';
    return;
  }
  const values = data.map((item) => Number.isFinite(Number(item.return_pct)) ? number(item.return_pct) : 0);
  const max = Math.max(1, ...values.map(Math.abs));
  $("#monthlyChart").innerHTML = `<div class="monthly-zero"></div>${data.map((item, index) => {
    const value = values[index]; const height = Math.max(4, Math.abs(value) / max * 45);
    return `<div class="month-column"><span class="month-value ${pnlClass(value)}">${Number.isFinite(Number(item.return_pct)) ? percent(value, 1) : "—"}</span><i class="${value >= 0 ? "gain" : "loss"}" style="height:${height}%;${value >= 0 ? "bottom:50%" : "top:50%"}"></i><small>${escapeHtml(String(item.month || "").slice(2).replace("-", "."))}</small></div>`;
  }).join("")}`;
}

function renderRealizedEvents(events) {
  const rows = (Array.isArray(events) ? events : []).slice().reverse();
  setText("#realizedEventsCount", `${rows.length}건`);
  $("#realizedEmpty").classList.toggle("is-hidden", rows.length > 0);
  $("#realizedBody").innerHTML = rows.slice(0, 100).map((item) => `<tr>
    <td>${dateText(item.date)}</td><td><div class="instrument"><strong>${escapeHtml(item.name || item.code)}</strong><span>${escapeHtml(item.code)} · ${escapeHtml(item.market)}</span></div></td>
    <td><span class="sector-chip">${escapeHtml(item.sector || "기타")}</span></td><td class="numeric">${qty.format(number(item.qty))}</td><td class="numeric">${money(item.sell_price, item.currency === "USD" ? "USD" : "KRW")}</td>
    <td class="numeric ${pnlClass(item.realized_pnl_krw)}"><strong>${money(item.realized_pnl_krw)}</strong></td><td class="numeric ${pnlClass(item.return_pct)}"><strong>${percent(item.return_pct)}</strong></td>
  </tr>`).join("");
}

function render(data) {
  portfolio = data;
  const totals = data.totals || {};
  const realized = data.realized_summary || {};
  const netFlow = (data.cash_flows || []).reduce((sum, item) => sum + (item.side === "DEPOSIT" ? number(item.amount_krw) : -number(item.amount_krw)), 0);
  setText("#environment", data.account?.environment === "mock" ? "모의투자" : "실계좌");
  setText("#account", data.account?.masked || "—");
  setText("#recordingStart", dateText(data.recording_started_at));
  setText("#updatedAt", dateText(data.updated_at, true));
  setText("#totalAsset", money(totals.total_asset_krw || totals.evaluation_krw));
  setText("#realizedPnl", money(realized.cumulative_realized_krw));
  $("#realizedPnl").className = pnlClass(realized.cumulative_realized_krw);
  setText("#realizedCount", `${realized.trade_count || 0}건 실현`);
  setText("#unrealizedPnl", money(totals.unrealized_pnl_krw ?? totals.pnl_krw));
  $("#unrealizedPnl").className = pnlClass(totals.unrealized_pnl_krw ?? totals.pnl_krw);
  setText("#unrealizedRate", percent(totals.unrealized_pnl_pct ?? totals.pnl_pct));
  setText("#netCashFlow", money(netFlow));
  $("#netCashFlow").className = pnlClass(netFlow);
  setText("#winRate", percent(realized.win_rate_pct, 1));
  setText("#avgTakeProfit", percent(realized.average_take_profit_pct));
  setText("#avgStopLoss", percent(realized.average_stop_loss_pct));
  setText("#exitCount", `${realized.trade_count || 0}건`);
  renderYieldChart(data.yield_history || []);
  renderHoldings(data.holdings || []);
  renderSectorAnalytics(data.holdings || []);
  renderMonthly(data.monthly_performance || []);
  renderRealizedEvents(data.realized_events || []);
  const warnings = Array.isArray(data.warnings) ? data.warnings : [];
  $("#warningPanel").classList.toggle("is-hidden", warnings.length === 0);
  $("#warnings").innerHTML = warnings.map((warning) => `<li>${escapeHtml(warning)}</li>`).join("");
  setText("#source", data.source || "조회 전용");
}

async function loadEnvelope() {
  const response = await fetch(`./data/portfolio.enc.json?v=${Date.now()}`, { cache: "no-store" });
  if (!response.ok) throw new Error(`데이터를 불러오지 못했습니다 (${response.status}).`);
  encryptedEnvelope = await response.json();
  if (!$("#pinModal").classList.contains("is-hidden")) openPinModal();
}

$("#watchlistTab").addEventListener("click", () => { closePinModal(); activateTab("watchlist"); });
$("#portfolioTab").addEventListener("click", () => { activateTab("portfolio"); if (!portfolio) openPinModal(); });
$("#watchMarketFilter").addEventListener("change", renderWatchlistRows);
$("#watchBandFilter").addEventListener("change", renderWatchlistRows);
$("#openPinButton").addEventListener("click", openPinModal);
$("#closePinButton").addEventListener("click", () => closePinModal({ returnToWatchlist: true }));
$("#cancelPinButton").addEventListener("click", () => closePinModal({ returnToWatchlist: true }));
$("#pin").addEventListener("input", (event) => { event.target.value = event.target.value.replace(/\D/g, "").slice(0, 4); $("#pinMessage").textContent = ""; });

$("#pinForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const pin = $("#pin").value; const button = $("#unlockButton"); const message = $("#pinMessage");
  if (!/^\d{4}$/.test(pin)) { message.textContent = "숫자 4자리를 입력해 주세요."; $("#pin").focus(); return; }
  button.disabled = true; message.textContent = "Yield Monitor를 여는 중…";
  try {
    render(await decryptEnvelope(encryptedEnvelope, pin));
    $("#portfolioLocked").classList.add("is-hidden"); $("#dashboardContent").classList.remove("is-hidden"); closePinModal();
  } catch (error) {
    portfolio = null; message.textContent = error.code === "EMPTY" ? error.message : "PIN이 맞지 않거나 데이터가 손상되었습니다."; $("#pin").value = ""; $("#pin").focus();
  } finally { button.disabled = Boolean(encryptedEnvelope?.empty); }
});

$("#lockButton").addEventListener("click", () => {
  portfolio = null; $("#dashboardContent").classList.add("is-hidden"); $("#portfolioLocked").classList.remove("is-hidden"); activateTab("watchlist");
});
document.addEventListener("keydown", (event) => { if (event.key === "Escape" && !$("#pinModal").classList.contains("is-hidden")) closePinModal({ returnToWatchlist: true }); });

activateTab(location.hash === "#portfolio" ? "portfolio" : "watchlist");
if (location.hash === "#portfolio") openPinModal();
loadWatchlist();
loadEnvelope().catch((error) => {
  encryptedEnvelope = { empty: true, message: `${error.message} 웹 서버 상태를 확인하세요.` };
  if (!$("#pinModal").classList.contains("is-hidden")) openPinModal();
});
