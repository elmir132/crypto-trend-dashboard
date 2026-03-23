"use strict";
// Every value from the server is placed with textContent or setAttribute, never by building HTML strings.

const $ = (id) => document.getElementById(id);
// In the static demo (docs/) there is no server: the page reads a prepared summary.json instead.
const DEMO = document.body.dataset.demo === "true";
const SVG_NS = "http://www.w3.org/2000/svg";
let latest = null;
let timer = null;

function el(tag, attrs, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (key === "text") node.textContent = value;
    else node.setAttribute(key, value);
  }
  for (const child of children) if (child) node.append(child);
  return node;
}

function svg(tag, attrs) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs || {})) node.setAttribute(key, value);
  return node;
}

const fmtPrice = (p) => {
  if (p === null || p === undefined) return "n/a";
  const digits = p >= 1 ? 2 : p >= 0.01 ? 4 : 6;
  return "$" + p.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
};
// Round first, so a change of -0.04% reads "0.0%" and is treated as flat, not as a fall.
const rounded = (c) => Number(c.toFixed(1));
const fmtPct = (c) => (c === null || c === undefined ? "n/a" : (rounded(c) > 0 ? "+" : "") + rounded(c).toFixed(1) + "%");
const fmtBig = (n) => (n === null || n === undefined ? "n/a" : "$" + new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 2 }).format(n));
const dir = (c) => (c === null || c === undefined || rounded(c) === 0 ? "flat" : c > 0 ? "up" : "down");
const arrow = (c) => (dir(c) === "flat" ? "•" : dir(c) === "up" ? "▲" : "▼");

function badge(label, change) {
  return el("span", { class: `badge ${dir(change)}`, text: `${arrow(change)} ${label} ${fmtPct(change)}` });
}

function sparkline(points, change) {
  const box = svg("svg", { class: `spark ${dir(change)}`, viewBox: "0 0 120 36", width: "120", height: "36", role: "img", "aria-label": "7 day price trend" });
  if (points.length < 2) return box;
  const min = Math.min(...points), max = Math.max(...points), span = max - min || 1;
  const d = points.map((p, i) => `${i ? "L" : "M"}${((i / (points.length - 1)) * 120).toFixed(1)} ${(34 - ((p - min) / span) * 32).toFixed(1)}`).join("");
  box.append(svg("path", { d }));
  return box;
}

function renderHeadline(data) {
  const box = $("headline");
  box.replaceChildren();
  if (!data.headline.length) box.append(el("p", { class: "muted", text: "No headline prices available." }));
  for (const coin of data.headline) {
    box.append(el("article", { class: "card" },
      el("h3", { text: `${coin.name} (${coin.symbol})` }),
      el("div", { class: "price", text: fmtPrice(coin.price) }),
      el("div", { class: "badges" }, badge("24h", coin.change_24h), badge("7d", coin.change_7d)),
      sparkline(coin.sparkline, coin.change_7d),
      el("div", { class: "muted small", text: `Market cap ${fmtBig(coin.market_cap)} · 24h volume ${fmtBig(coin.volume)}` })));
  }
}

function renderMood(data) {
  const box = $("mood");
  box.replaceChildren();
  const mood = data.sentiment;
  box.append(el("p", {}, document.createTextNode("Headline sentiment: "),
    el("strong", { class: mood === "bullish" ? "up" : mood === "bearish" ? "down" : "flat", text: mood ? `${mood === "bullish" ? "▲" : "▼"} ${mood}` : "not available" })));
  const b = data.breadth;
  if (b.total) {
    const meter = el("div", { class: "meter", role: "img", "aria-label": `${b.up} of ${b.total} coins up in 24 hours` });
    const fill = el("span", {});
    fill.style.width = `${Math.round(b.share_up * 100)}%`;
    meter.append(fill);
    box.append(meter, el("div", { class: "muted small", text: `${b.up} of ${b.total} tracked coins up in 24h, ${b.down} down${b.flat ? `, ${b.flat} flat` : ""}. Covers only the coins shown on this page.` }));
  } else {
    box.append(el("p", { class: "muted", text: "No coin changes available." }));
  }
}

function renderSectors(data) {
  const box = $("sectors");
  box.replaceChildren();
  const names = Object.keys(data.sector_stats);
  names.sort((a, b) => (data.sector_stats[b].avg_change_24h ?? -Infinity) - (data.sector_stats[a].avg_change_24h ?? -Infinity));
  const widest = Math.max(1, ...names.map((n) => Math.abs(data.sector_stats[n].avg_change_24h ?? 0)));
  const table = el("table", {});
  table.append(el("thead", {}, el("tr", {}, el("th", { text: "Sector" }), el("th", { text: "Avg 24h" }), el("th", { text: "" }), el("th", { text: "Top coins (24h)" }))));
  const body = el("tbody", {});
  for (const name of names) {
    const stat = data.sector_stats[name];
    const avg = stat.avg_change_24h;
    const bar = el("div", { class: `bar ${dir(avg)}` });
    const fill = el("span", {});
    const share = Math.min(50, (Math.abs(avg ?? 0) / widest) * 50);
    fill.style.left = avg >= 0 ? "50%" : `${50 - share}%`;
    fill.style.width = `${share}%`;
    bar.append(fill);
    const chips = el("td", {});
    const coins = data.sector_details[name];
    if (!coins.length) chips.append(el("span", { class: "muted", text: "unavailable" }));
    for (const coin of coins) chips.append(el("span", { class: `chip ${dir(coin.change_24h)}`, text: `${coin.symbol} ${fmtPct(coin.change_24h)}` }));
    body.append(el("tr", {}, el("td", { text: name }), el("td", { class: `num ${dir(avg)}`, text: `${arrow(avg)} ${fmtPct(avg)}` }), el("td", {}, bar), chips));
  }
  table.append(body);
  box.append(table);
}

function renderMovers(data) {
  const box = $("movers");
  box.replaceChildren();
  for (const [title, list] of [["Gainers", data.movers.gainers], ["Losers", data.movers.losers]]) {
    const ul = el("ul", {});
    if (!list.length) ul.append(el("li", { class: "muted", text: "none" }));
    for (const coin of list) ul.append(el("li", {}, el("span", { text: `${coin.symbol}${coin.sector ? " · " + coin.sector : ""}` }), el("span", { class: dir(coin.change_24h), text: `${arrow(coin.change_24h)} ${fmtPct(coin.change_24h)}` })));
    box.append(el("div", { class: "panel" }, el("strong", { text: title }), ul));
  }
}

function renderOverview(data) {
  const box = $("overview");
  box.replaceChildren();
  const o = data.overview;
  if (!o) {
    box.append(el("p", { class: "muted", text: "Whole-market figures are not available right now." }));
    return;
  }
  const cards = [
    ["Total market cap", fmtBig(o.total_market_cap), badge("24h", o.market_cap_change_24h)],
    ["24h volume", fmtBig(o.total_volume), null],
    ["Bitcoin share of the market", o.btc_dominance === null ? "n/a" : `${o.btc_dominance.toFixed(1)}%`, null],
    ["Ethereum share of the market", o.eth_dominance === null ? "n/a" : `${o.eth_dominance.toFixed(1)}%`, null],
    ["Coins tracked by CoinGecko", o.active_coins === null ? "n/a" : o.active_coins.toLocaleString("en-US"), null],
  ];
  for (const [label, value, extra] of cards) {
    box.append(el("article", { class: "card" }, el("h3", { text: label }), el("div", { class: "price", text: value }), extra));
  }
}

function renderAlerts(data) {
  const box = $("alerts");
  box.replaceChildren();
  const { threshold, coins } = data.alerts;
  box.append(el("div", { class: "muted small", text: `Coins that moved ${threshold}% or more in 24h, among the coins on this page:` }));
  if (!coins.length) {
    box.append(el("p", { class: "muted", text: "None right now." }));
    return;
  }
  const row = el("div", {});
  for (const coin of coins) row.append(el("span", { class: `chip ${coin.direction}`, text: `${arrow(coin.change_24h)} ${coin.symbol} ${fmtPct(coin.change_24h)}${coin.sector ? " · " + coin.sector : ""}` }));
  box.append(row);
}

function renderTrending(data) {
  const box = $("trending");
  box.replaceChildren();
  if (!data.trending.length) {
    box.append(el("p", { class: "muted", text: "Trending coins are not available right now." }));
    return;
  }
  const ul = el("ul", { class: "plain" });
  for (const coin of data.trending) {
    ul.append(el("li", {}, el("span", { text: `${coin.symbol} ${coin.name ? "· " + coin.name : ""}${coin.rank ? " · rank #" + coin.rank : ""}` }),
      el("span", { class: dir(coin.change_24h), text: `${arrow(coin.change_24h)} ${fmtPct(coin.change_24h)}` })));
  }
  box.append(ul);
}

const COLORS = ["#0b5cff", "#d97706", "#7c3aed", "#0f766e", "#be185d"];

async function renderHistory() {
  const box = $("history");
  box.replaceChildren();
  if (DEMO) {
    box.append(el("p", { class: "muted", text: "History is not part of the static demo. Run the app yourself and it saves snapshots on your machine." }));
    return;
  }
  let body;
  try {
    const res = await fetch("/api/history?hours=24");
    body = await res.json();
  } catch (e) {
    box.append(el("p", { class: "muted", text: "History could not be loaded." }));
    return;
  }
  if (!body.enabled) { box.append(el("p", { class: "muted", text: "History is switched off (HISTORY=0) or this is sample data." })); return; }
  const points = body.points;
  if (points.length < 2) { box.append(el("p", { class: "muted", text: "History fills in as you use the dashboard: one snapshot is saved at most every 5 minutes. Come back after a few refreshes." })); return; }
  const ids = Object.keys(points[points.length - 1].prices).slice(0, 5);
  const series = ids.map((id, i) => {
    const base = points.find((p) => p.prices[id] !== undefined);
    const values = points.map((p) => (p.prices[id] !== undefined && base ? ((p.prices[id] / base.prices[id]) - 1) * 100 : null));
    return { id, color: COLORS[i % COLORS.length], values };
  });
  const all = series.flatMap((s) => s.values.filter((v) => v !== null));
  const lo = Math.min(0, ...all), hi = Math.max(0, ...all), span = hi - lo || 1;
  const t0 = points[0].ts, t1 = points[points.length - 1].ts, tspan = t1 - t0 || 1;
  const W = 600, H = 180;
  const chart = svg("svg", { viewBox: `0 0 ${W} ${H}`, width: "100%", role: "img", "aria-label": "Price change over the last 24 hours" });
  const zero = H - 10 - ((0 - lo) / span) * (H - 20);
  chart.append(svg("line", { x1: 0, x2: W, y1: zero, y2: zero, stroke: "currentColor", "stroke-opacity": ".25", "stroke-dasharray": "4 4" }));
  for (const s of series) {
    const d = points.map((p, i) => s.values[i] === null ? "" : `${"ML"[+(i > 0 && s.values[i - 1] !== null)]}${(((p.ts - t0) / tspan) * W).toFixed(1)} ${(H - 10 - ((s.values[i] - lo) / span) * (H - 20)).toFixed(1)}`).join("");
    chart.append(svg("path", { d, fill: "none", stroke: s.color, "stroke-width": 2 }));
  }
  const legend = el("div", { class: "legend" });
  for (const s of series) {
    const last = [...s.values].reverse().find((v) => v !== null);
    const item = el("span", { text: `${s.id} ${fmtPct(last)}` });
    item.style.color = s.color;
    legend.append(item);
  }
  box.append(chart, legend, el("div", { class: "muted small", text: `${points.length} snapshots from ${new Date(t0 * 1000).toLocaleString()} to ${new Date(t1 * 1000).toLocaleString()}` }));
}

function setStatus(data) {
  const when = new Date(data.generated_at).toLocaleTimeString();
  let text = `Updated ${when}`;
  if (data.mock) text += " · sample data for the offline demo, not real prices";
  else if (data.stale) text += " · showing cached data because CoinGecko did not answer";
  else text += ` · live data, cached up to ${data.cache_ttl}s`;
  if (DEMO) text += " · static demo";
  $("status").textContent = text;
  const banner = $("banner");
  const notes = [...data.errors];
  if (data.mock) notes.unshift("Sample data: the prices and coins below are made up for the demo.");
  banner.textContent = notes.join(" | ");
  banner.hidden = notes.length === 0;
}

async function load() {
  $("status").textContent = "Loading…";
  try {
    const res = await fetch(DEMO ? "summary.json" : "/api/summary");
    const data = await res.json();
    latest = data;
    setStatus(data);
    renderHeadline(data); renderOverview(data); renderMood(data); renderSectors(data); renderAlerts(data); renderMovers(data); renderTrending(data);
    $("post").textContent = data.post || "No data available.";
    await renderHistory();
  } catch (e) {
    $("status").textContent = "Could not reach the server.";
    $("banner").textContent = "The dashboard could not load data. Check that the server is running.";
    $("banner").hidden = false;
  }
}

async function copy(text, label) {
  const note = $("copied");
  try {
    await navigator.clipboard.writeText(text);
    note.textContent = `${label} copied.`;
  } catch (e) {
    note.textContent = "Copy was blocked by the browser; select the text and copy it by hand.";
  }
  setTimeout(() => { note.textContent = ""; }, 2500);
}

function schedule() {
  clearInterval(timer);
  const seconds = Number($("auto").value);
  if (seconds > 0) timer = setInterval(load, seconds * 1000);
}

function safeStorage(action) { try { return action(); } catch (e) { return null; } }

function applyTheme(theme) {
  if (theme === "light" || theme === "dark") document.documentElement.setAttribute("data-theme", theme);
}

if (DEMO) $("csv").remove();
$("refresh").addEventListener("click", load);
$("auto").addEventListener("change", () => { safeStorage(() => localStorage.setItem("auto", $("auto").value)); schedule(); });
$("copy-text").addEventListener("click", () => latest && copy(latest.post, "Text"));
$("copy-md").addEventListener("click", () => latest && copy(latest.post_markdown, "Markdown"));
$("theme").addEventListener("click", () => {
  const dark = document.documentElement.getAttribute("data-theme") === "dark" || (!document.documentElement.hasAttribute("data-theme") && matchMedia("(prefers-color-scheme: dark)").matches);
  const next = dark ? "light" : "dark";
  applyTheme(next);
  safeStorage(() => localStorage.setItem("theme", next));
});

// ?theme=light or ?theme=dark overrides the saved choice (handy for screenshots and links).
applyTheme(new URLSearchParams(location.search).get("theme") || safeStorage(() => localStorage.getItem("theme")));
const savedAuto = safeStorage(() => localStorage.getItem("auto"));
if (["0", "60", "300"].includes(savedAuto)) $("auto").value = savedAuto;
schedule();
load();
