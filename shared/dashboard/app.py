from __future__ import annotations

import datetime as dt
import os
import sys

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from sqlalchemy import select, text

from engines.prediction_markets.betting.paper_bettor import PaperBettor
from engines.prediction_markets.runtime_state import snapshot
from engines.prediction_markets.storage.db import get_session, init_db
from engines.prediction_markets.storage.models import (
    BacktestResult,
    CalibrationResult,
    Market,
    MomentumResult,
    NewsArticle,
    NewsMarketLink,
    PaperBet,
    ProbabilityReading,
)
from engines.prediction_markets.tracking.knowledge_base import KnowledgeBase

app = FastAPI(title="Prediction Markets Dashboard")

init_db()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SIGNAL_COLORS = {
    "outlier": "#e67e22",
    "velocity": "#2980b9",
    "arbitrage": "#27ae60",
    "news_lag": "#8e44ad",
    "related_divergence": "#c0392b",
    "scheduled_proximity": "#d35400",
    "thin_liquidity": "#7f8c8d",
    "cross_category_momentum": "#16a085",
}

def _score_bar(score: float | None, color: str = "#3498db") -> str:
    pct = int((score or 0) * 100)
    return (
        f'<div style="background:#ecf0f1;border-radius:3px;height:10px;width:120px;display:inline-block;vertical-align:middle">'
        f'<div style="background:{color};height:100%;width:{pct}%;border-radius:3px"></div></div>'
        f' <span style="font-size:0.85em;color:#555">{score:.3f}</span>' if score is not None else "—"
    )


_HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>PM Engine — Prediction Market Dashboard</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0f1117;color:#e0e0e0;font-size:14px}}
  h1{{color:#fff;font-size:1.6em;padding:18px 24px 6px;border-bottom:1px solid #1e2230}}
  h2{{color:#ccc;font-size:1.05em;margin-bottom:10px;text-transform:uppercase;letter-spacing:.05em;font-weight:600}}
  .subtitle{{color:#666;font-size:0.8em;padding:4px 24px 14px}}
  .grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:16px;padding:16px 24px}}
  .card{{background:#1a1d27;border:1px solid #252836;border-radius:8px;padding:16px}}
  .card.wide{{grid-column:1/-1}}
  table{{width:100%;border-collapse:collapse;font-size:0.88em}}
  th{{text-align:left;color:#888;font-weight:500;padding:6px 8px;border-bottom:1px solid #252836;font-size:0.82em;text-transform:uppercase;letter-spacing:.04em}}
  td{{padding:6px 8px;border-bottom:1px solid #1c1f2b;vertical-align:middle}}
  tr:last-child td{{border-bottom:none}}
  tr:hover td{{background:#1f2233}}
  .badge{{display:inline-block;padding:2px 7px;border-radius:10px;font-size:0.78em;font-weight:600;text-transform:uppercase}}
  .badge-outlier{{background:#7d3c0040;color:#e67e22;border:1px solid #e67e2240}}
  .badge-velocity{{background:#1a5276;color:#7fb3d3;border:1px solid #2980b940}}
  .badge-arbitrage{{background:#1d6a3a;color:#52be80;border:1px solid #27ae6040}}
  .badge-news_lag{{background:#4a235a;color:#bb8fce;border:1px solid #8e44ad40}}
  .badge-related_divergence{{background:#5b1212;color:#e98e8e;border:1px solid #c0392b40}}
  .badge-scheduled_proximity{{background:#5c2000;color:#f0a070;border:1px solid #d3540040}}
  .badge-thin_liquidity{{background:#2c2f33;color:#aaa;border:1px solid #7f8c8d40}}
  .badge-cross_category_momentum{{background:#0b3a30;color:#48c9b0;border:1px solid #16a08540}}
  .badge-other{{background:#333;color:#aaa;border:1px solid #555}}
  .stat-row{{display:flex;flex-wrap:wrap;gap:12px;margin-bottom:4px}}
  .stat{{flex:1;min-width:80px;background:#13151e;border:1px solid #252836;border-radius:6px;padding:10px 12px}}
  .stat-val{{font-size:1.4em;font-weight:700;color:#fff}}
  .stat-lbl{{color:#666;font-size:0.75em;margin-top:2px;text-transform:uppercase;letter-spacing:.04em}}
  .status-dot{{width:9px;height:9px;border-radius:50%;display:inline-block;margin-right:5px;vertical-align:middle}}
  .dot-green{{background:#2ecc71}}
  .dot-yellow{{background:#f1c40f}}
  .dot-red{{background:#e74c3c}}
  .dot-grey{{background:#555}}
  .bar-wrap{{display:inline-flex;align-items:center;gap:6px}}
  .bar-bg{{background:#252836;border-radius:3px;height:8px;width:80px;display:inline-block}}
  .bar-fill{{height:100%;border-radius:3px}}
  .score-num{{font-size:0.82em;color:#aaa;min-width:36px}}
  .direction-yes{{color:#2ecc71;font-weight:600}}
  .direction-no{{color:#e74c3c;font-weight:600}}
  .refresh-bar{{text-align:right;padding:8px 24px;color:#444;font-size:0.78em}}
  .section-empty{{color:#555;font-style:italic;padding:12px 0;text-align:center}}
  a{{color:#5dade2;text-decoration:none}}
  a:hover{{text-decoration:underline}}
  .fetch-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:8px;margin-top:4px}}
  .fetch-item{{background:#13151e;border:1px solid #252836;border-radius:4px;padding:8px 10px;font-size:0.8em}}
  .fetch-item .src{{color:#777;margin-bottom:2px;text-transform:uppercase;font-size:0.75em}}
  .fetch-item .time{{color:#ccc}}
</style>
</head>
<body>
<h1>Prediction Market Engine</h1>
<div class="subtitle" id="page-ts">Loading…</div>
<div class="grid" id="root">
  <div class="card wide"><div class="section-empty">Fetching data…</div></div>
</div>
<div class="refresh-bar">Auto-refreshes every 60 seconds &nbsp;|&nbsp; <a href="/docs">API docs</a></div>
<script>
const API = {
  health: '/pm/health',
  overview: '/pm/overview',
  opportunities: '/pm/opportunities',
  signals: '/pm/signals',
  bets: '/pm/bets?days=30',
  markets: '/pm/markets',
  news: '/pm/news',
  stats: '/pm/stats',
  backtest: '/pm/backtest',
  calibration: '/pm/calibration',
  filter_stats: '/pm/filter_stats',
  movers: '/pm/movers',
  newsapi_status: '/pm/newsapi_status',
};

function badge(type) {
  const known = ['outlier','velocity','arbitrage','news_lag','related_divergence','scheduled_proximity','thin_liquidity','cross_category_momentum'];
  const cls = known.includes(type) ? type : 'other';
  const label = (type||'').replace(/_/g,' ');
  return `<span class="badge badge-${cls}">${label}</span>`;
}

function bar(score, color) {
  const pct = Math.round((score||0)*100);
  return `<span class="bar-wrap"><span class="bar-bg"><span class="bar-fill" style="width:${pct}%;background:${color||'#3498db'}"></span></span><span class="score-num">${(score||0).toFixed(3)}</span></span>`;
}

function dot(ok) {
  if (ok === true) return '<span class="status-dot dot-green"></span>';
  if (ok === false) return '<span class="status-dot dot-red"></span>';
  return '<span class="status-dot dot-grey"></span>';
}

function fmtTime(iso) {
  if (!iso) return '—';
  try {
    const d = new Date(iso);
    return d.toLocaleTimeString();
  } catch(e) { return iso; }
}

function fmtNum(n, decimals=2) {
  if (n === null || n === undefined) return '—';
  return Number(n).toFixed(decimals);
}

function pct(n) {
  if (n === null || n === undefined) return '—';
  return (Number(n)*100).toFixed(1)+'%';
}

async function fetchAll() {
  const results = {};
  await Promise.all(Object.entries(API).map(async ([key, url]) => {
    try {
      const r = await fetch(url);
      results[key] = await r.json();
    } catch(e) {
      results[key] = null;
    }
  }));
  return results;
}

function buildHealthCard(h, fs) {
  if (!h) return `<div class="card"><h2>System Status</h2><div class="section-empty">No data yet — waiting for first cycle</div></div>`;
  const ready = h.is_ready;
  const kalshiPassed = fs ? (fs.kalshi_passed||0) : 0;
  const polyPassed = fs ? (fs.polymarket_passed||0) : 0;
  return `<div class="card">
    <h2>${dot(ready)} System Status</h2>
    <div class="stat-row" style="margin-top:10px">
      <div class="stat"><div class="stat-val">${h.markets_tracked||0}</div><div class="stat-lbl">Markets</div></div>
      <div class="stat"><div class="stat-val">${h.ok_markets||0}</div><div class="stat-lbl">OK</div></div>
      <div class="stat"><div class="stat-val">${h.stale_markets||0}</div><div class="stat-lbl">Stale</div></div>
      <div class="stat"><div class="stat-val">${h.news_articles_today||0}</div><div class="stat-lbl">News Today</div></div>
      <div class="stat"><div class="stat-val">${h.open_paper_bets||0}</div><div class="stat-lbl">Open Bets</div></div>
      <div class="stat"><div class="stat-val">${h.alerts_last_24h||0}</div><div class="stat-lbl">Alerts 24h</div></div>
      <div class="stat"><div class="stat-val" style="color:#16a085">${kalshiPassed}</div><div class="stat-lbl">Kalshi Mkts</div></div>
      <div class="stat"><div class="stat-val" style="color:#2980b9">${polyPassed}</div><div class="stat-lbl">Poly Mkts</div></div>
    </div>
    <div style="margin-top:12px;color:#666;font-size:0.82em">
      Last poll: <span style="color:#aaa">${fmtTime(h.last_poll_time)}</span>
      &nbsp;|&nbsp; Status: <span style="color:${ready?'#2ecc71':'#e74c3c'}">${ready?'Ready':'Waiting for data'}</span>
    </div>
  </div>`;
}

function buildFetchTimesCard(h) {
  if (!h || !h.fetch_times) return '';
  const entries = Object.entries(h.fetch_times);
  if (!entries.length) return '';
  const items = entries.map(([src, t]) => `<div class="fetch-item"><div class="src">${src}</div><div class="time">${fmtTime(t)}</div></div>`).join('');
  return `<div class="card"><h2>Data Source Timings</h2><div class="fetch-grid">${items}</div></div>`;
}

function buildOpportunitiesCard(opps) {
  const list = opps?.opportunities || [];
  if (!list.length) return `<div class="card wide"><h2>Top Opportunities</h2><div class="section-empty">None yet — waiting for first cycle to complete</div></div>`;
  const rows = list.map(o => {
    const dir = o.expected_bet === 'YES' ? `<span class="direction-yes">YES</span>` : o.expected_bet === 'NO' ? `<span class="direction-no">NO</span>` : '—';
    const ts = o.time_sensitive ? ' <span style="color:#f39c12;font-size:0.78em">⚡ TIME</span>' : '';
    return `<tr>
      <td style="color:#888;font-size:0.82em">#${o.rank}</td>
      <td>${badge(o.signal_type)}</td>
      <td style="max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${(o.question||'').replace(/"/g,'&quot;')}">${o.question||'—'}</td>
      <td><span style="color:#888;font-size:0.8em">${o.platform||'—'}</span></td>
      <td>${dir}${ts}</td>
      <td>${bar(o.signal_score,'#e67e22')}</td>
      <td>${bar(o.execution_score,'#2980b9')}</td>
      <td>${bar(o.trust_score,'#8e44ad')}</td>
      <td>${bar(o.final_score,'#2ecc71')}</td>
      <td style="color:#f39c12;font-size:0.85em">${o.ev_estimate!=null?pct(o.ev_estimate):'—'}</td>
    </tr>`;
  }).join('');
  return `<div class="card wide">
    <h2>Top Opportunities (${list.length})</h2>
    <table>
      <thead><tr><th>#</th><th>Type</th><th>Question</th><th>Platform</th><th>Bet</th><th>Signal</th><th>Execution</th><th>Trust</th><th>Final</th><th>EV</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
  </div>`;
}

function buildSignalCard(signals) {
  if (!signals) return '';
  const types = ['outlier','velocity','arbitrage','news_lag','related_divergence','scheduled_proximity','thin_liquidity','cross_category_momentum'];
  const colorMap = {outlier:'#e67e22',velocity:'#2980b9',arbitrage:'#27ae60',news_lag:'#8e44ad',related_divergence:'#c0392b',scheduled_proximity:'#d35400',thin_liquidity:'#7f8c8d',cross_category_momentum:'#16a085'};
  const statCells = types.map(t => {
    const list = signals[t] || [];
    if (!list.length && !['outlier','velocity','arbitrage','news_lag'].includes(t)) return '';
    return `<div class="stat"><div class="stat-val" style="color:${colorMap[t]||'#aaa'}">${list.length}</div><div class="stat-lbl">${t.replace(/_/g,' ')}</div></div>`;
  }).join('');

  let arbSection = '';
  if (signals.arbitrage && signals.arbitrage.length) {
    const arbRows = signals.arbitrage.map(o => `<tr>
      <td style="max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${(o.question||'').replace(/"/g,'&quot;')}">${o.question||'—'}</td>
      <td><span style="color:#888;font-size:0.8em">${o.platform||'—'}</span></td>
      <td style="color:#52be80">${o.ev_estimate!=null?pct(o.ev_estimate):'—'}</td>
      <td>${bar(o.final_score,'#27ae60')}</td>
    </tr>`).join('');
    arbSection = `<div style="margin-top:14px"><h2 style="color:#52be80">Arbitrage Alerts (manual review required)</h2>
    <table><thead><tr><th>Question</th><th>Platform</th><th>Edge</th><th>Score</th></tr></thead><tbody>${arbRows}</tbody></table></div>`;
  }

  return `<div class="card wide">
    <h2>Signal Breakdown</h2>
    <div class="stat-row" style="margin-top:8px">${statCells}</div>
    ${arbSection}
  </div>`;
}

function buildBacktestCard(bt) {
  if (!bt || !bt.results || !bt.results.length) {
    return `<div class="card wide"><h2>Backtest Results</h2><div class="section-empty">No backtest data — run: <code>python -m backtest.engine</code></div></div>`;
  }
  const base = bt.results.filter(r => !r.signal_combo || r.signal_type === r.signal_combo).slice(0,10);
  const rows = base.map(r => `<tr>
    <td>${badge(r.signal_type)}</td>
    <td style="color:#aaa;font-size:0.8em">${r.signal_combo||'—'}</td>
    <td>${r.sample_size}</td>
    <td style="color:${(r.hit_rate||0)>0.5?'#2ecc71':'#e74c3c'}">${r.hit_rate!=null?(r.hit_rate*100).toFixed(1)+'%':'—'}</td>
    <td style="color:${(r.ev||0)>0?'#2ecc71':'#e74c3c'}">${r.ev!=null?fmtNum(r.ev,3):'—'}</td>
    <td>${r.roi!=null?fmtNum(r.roi*100,1)+'%':'—'}</td>
    <td>${r.sharpe!=null?fmtNum(r.sharpe,2):'—'}</td>
    <td style="color:#e74c3c">${r.max_drawdown!=null?fmtNum(r.max_drawdown,2):'—'}</td>
    <td style="color:#aaa;font-size:0.8em">${r.kelly_fraction||'—'}</td>
  </tr>`).join('');

  const combos = (bt.top_combos||[]).slice(0,3);
  const comboRows = combos.map(c => `<tr>
    <td style="color:#f39c12">${c.combo||'—'}</td>
    <td>${c.sample_size||0}</td>
    <td style="color:${(c.hit_rate||0)>0.5?'#2ecc71':'#aaa'}">${c.hit_rate!=null?(c.hit_rate*100).toFixed(1)+'%':'—'}</td>
    <td style="color:${(c.ev||0)>0?'#2ecc71':'#e74c3c'}">${c.ev!=null?fmtNum(c.ev,3):'—'}</td>
  </tr>`).join('');

  const kelly = bt.best_kelly || {};
  const window = bt.best_entry_window || {};
  const summary = `<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:10px">
    <div>
      <div style="color:#888;font-size:0.8em;margin-bottom:4px">BEST KELLY SIZING</div>
      <div style="color:#f39c12">${kelly.fraction||'—'} Kelly</div>
      <div style="color:#aaa;font-size:0.82em">ROI: ${kelly.roi!=null?(kelly.roi*100).toFixed(1)+'%':'—'} | Bankroll: ${kelly.final_bankroll!=null?fmtNum(kelly.final_bankroll,1):'—'} | DD: ${kelly.max_drawdown!=null?fmtNum(kelly.max_drawdown,2):'—'}</div>
    </div>
    <div>
      <div style="color:#888;font-size:0.8em;margin-bottom:4px">BEST ENTRY WINDOW</div>
      <div style="color:#2ecc71">${window.window||'—'} days before resolution</div>
      <div style="color:#aaa;font-size:0.82em">Hit rate: ${window.hit_rate!=null?(window.hit_rate*100).toFixed(1)+'%':'—'} | EV: ${window.ev!=null?fmtNum(window.ev,3):'—'}</div>
    </div>
  </div>`;

  return `<div class="card wide">
    <h2>Backtest Results (run date: ${bt.run_date?new Date(bt.run_date).toLocaleDateString():'—'})</h2>
    <div style="color:#888;font-size:0.82em;margin-bottom:8px">Markets analyzed: ${bt.markets_analyzed||0} | Signal fires — outlier: ${(bt.signal_fires||{}).outlier||0}, velocity: ${(bt.signal_fires||{}).velocity||0}, news_lag: ${(bt.signal_fires||{}).news_lag||0}</div>
    <table><thead><tr><th>Signal</th><th>Combo</th><th>N</th><th>Hit%</th><th>EV</th><th>ROI</th><th>Sharpe</th><th>Max DD</th><th>Kelly</th></tr></thead><tbody>${rows}</tbody></table>
    ${comboRows ? `<div style="margin-top:14px"><h2 style="font-size:0.95em">Top Signal Combos</h2><table><thead><tr><th>Combo</th><th>N</th><th>Hit%</th><th>EV</th></tr></thead><tbody>${comboRows}</tbody></table></div>` : ''}
    ${summary}
  </div>`;
}

function buildCalibrationCard(cal) {
  if (!cal || !cal.results || !cal.results.length) {
    return `<div class="card"><h2>Calibration Analysis</h2><div class="section-empty">No calibration data — run: <code>python -m backtest.engine</code></div></div>`;
  }
  const rows = cal.results.slice(0,15).map(r => {
    const biasColor = r.bias > 0.05 ? '#2ecc71' : r.bias < -0.05 ? '#e74c3c' : '#888';
    return `<tr>
      <td style="color:#aaa;font-size:0.8em">${r.source||'—'}</td>
      <td style="color:#aaa;font-size:0.8em">${r.category||'—'}</td>
      <td>${r.prob_bucket||'—'}</td>
      <td>${r.avg_market_prob!=null?(r.avg_market_prob*100).toFixed(1)+'%':'—'}</td>
      <td>${r.actual_resolution_rate!=null?(r.actual_resolution_rate*100).toFixed(1)+'%':'—'}</td>
      <td style="color:${biasColor};font-weight:600">${r.bias!=null?(r.bias>0?'+':'')+fmtNum(r.bias*100,1)+'pp':'—'}</td>
      <td style="color:#666;font-size:0.8em">${r.sample_size||0}</td>
    </tr>`;
  }).join('');
  return `<div class="card wide">
    <h2>Market Calibration (bias = actual − market prob)</h2>
    <div style="color:#666;font-size:0.8em;margin-bottom:8px">Positive bias = market underprices YES (edge for buyers)</div>
    <table><thead><tr><th>Source</th><th>Category</th><th>Prob Bucket</th><th>Mkt Prob</th><th>Actual Rate</th><th>Bias</th><th>N</th></tr></thead><tbody>${rows}</tbody></table>
  </div>`;
}

function buildOverviewCard(ov, stats) {
  if (!ov) return `<div class="card"><h2>Paper Bet Performance</h2><div class="section-empty">No bets placed yet</div></div>`;
  const winPct = ov.win_rate != null ? (ov.win_rate*100).toFixed(1)+'%' : '—';
  const roi = ov.roi_percent != null ? fmtNum(ov.roi_percent,2)+'%' : '—';
  let statsTable = '';
  if (stats && stats.by_signal_type && Object.keys(stats.by_signal_type).length) {
    const rows = Object.entries(stats.by_signal_type).map(([sig, s]) => `<tr>
      <td>${badge(sig)}</td>
      <td>${s.total_bets}</td>
      <td>${s.win_rate!=null?(s.win_rate*100).toFixed(1)+'%':'—'}</td>
      <td style="color:${s.pnl_units>=0?'#2ecc71':'#e74c3c'}">${s.pnl_units!=null?fmtNum(s.pnl_units,2):'—'}</td>
      <td>${s.roi_percent!=null?fmtNum(s.roi_percent,2)+'%':'—'}</td>
    </tr>`).join('');
    statsTable = `<table style="margin-top:12px"><thead><tr><th>Signal</th><th>Bets</th><th>Win%</th><th>PnL</th><th>ROI</th></tr></thead><tbody>${rows}</tbody></table>`;
  }
  return `<div class="card">
    <h2>Paper Bet Performance</h2>
    <div class="stat-row" style="margin-top:8px">
      <div class="stat"><div class="stat-val">${ov.total_bets||0}</div><div class="stat-lbl">Total Bets</div></div>
      <div class="stat"><div class="stat-val">${winPct}</div><div class="stat-lbl">Win Rate</div></div>
      <div class="stat"><div class="stat-val">${fmtNum(ov.total_pnl_units,2)}</div><div class="stat-lbl">PnL Units</div></div>
      <div class="stat"><div class="stat-val">${roi}</div><div class="stat-lbl">ROI</div></div>
    </div>
    <div style="margin-top:8px;font-size:0.82em;color:#666">
      Best category: <span style="color:#aaa">${ov.best_category||'—'}</span> &nbsp;|&nbsp;
      Best signal: <span style="color:#aaa">${ov.best_signal_type||'—'}</span>
    </div>
    ${statsTable}
  </div>`;
}

function buildBetsCard(bets) {
  const list = bets?.bets || [];
  if (!list.length) return `<div class="card"><h2>Recent Paper Bets (30d)</h2><div class="section-empty">No bets placed yet</div></div>`;
  const rows = list.slice(0,15).map(b => {
    const outcomeColor = b.outcome === 'WIN' ? '#2ecc71' : b.outcome === 'LOSS' ? '#e74c3c' : b.outcome === 'PENDING' ? '#f39c12' : '#888';
    const pnlColor = (b.pnl_units||0) >= 0 ? '#2ecc71' : '#e74c3c';
    return `<tr>
      <td>${badge(b.trigger||'other')}</td>
      <td style="max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${(b.question||'').replace(/"/g,'&quot;')}">${b.question||'—'}</td>
      <td><span style="color:#888;font-size:0.8em">${b.platform||'—'}</span></td>
      <td>${b.direction==='YES'?'<span class="direction-yes">YES</span>':'<span class="direction-no">NO</span>'}</td>
      <td style="color:${outcomeColor};font-weight:600">${b.outcome||'—'}</td>
      <td style="color:${pnlColor}">${b.pnl_units!=null?fmtNum(b.pnl_units,2):'—'}</td>
    </tr>`;
  }).join('');
  return `<div class="card">
    <h2>Recent Paper Bets (30d, showing ${Math.min(list.length,15)} of ${list.length})</h2>
    <table><thead><tr><th>Type</th><th>Question</th><th>Platform</th><th>Dir</th><th>Outcome</th><th>PnL</th></tr></thead>
    <tbody>${rows}</tbody></table>
  </div>`;
}

function buildMarketsCard(markets) {
  const list = markets?.markets || [];
  if (!list.length) return `<div class="card"><h2>Active Markets</h2><div class="section-empty">No markets tracked yet</div></div>`;
  // Group by platform+category
  const grid = {};
  const platforms = new Set();
  const categories = new Set();
  for (const m of list) {
    const plt = m.platform || 'unknown';
    const cat = m.theme || 'other';
    platforms.add(plt);
    categories.add(cat);
    const key = plt + '|' + cat;
    grid[key] = (grid[key]||0)+1;
  }
  const sortedPlts = [...platforms].sort();
  const sortedCats = [...categories].sort((a,b) => {
    const totA = sortedPlts.reduce((s,p) => s+(grid[p+'|'+a]||0),0);
    const totB = sortedPlts.reduce((s,p) => s+(grid[p+'|'+b]||0),0);
    return totB - totA;
  }).slice(0,10);
  const headerCols = sortedPlts.map(p => `<th style="color:#aaa">${p}</th>`).join('');
  const gridRows = sortedCats.map(cat => {
    const cells = sortedPlts.map(plt => {
      const n = grid[plt+'|'+cat]||0;
      return `<td style="text-align:center;color:${n>0?'#ccc':'#444'}">${n||'—'}</td>`;
    }).join('');
    return `<tr><td style="color:#888">${cat}</td>${cells}</tr>`;
  }).join('');
  // Sample of 8 markets
  const sample = list.slice(0,8);
  const mktRows = sample.map(m => {
    const prob = m.probability != null ? (m.probability*100).toFixed(1)+'%' : '—';
    const delta = m.delta_24h != null ? ((m.delta_24h>0?'+':'')+((m.delta_24h)*100).toFixed(1)+'%') : '';
    const deltaColor = m.delta_24h > 0.005 ? '#2ecc71' : m.delta_24h < -0.005 ? '#e74c3c' : '#888';
    return `<tr>
      <td>${badge(m.theme||'other')}</td>
      <td style="max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${(m.question||'').replace(/"/g,'&quot;')}">${m.question||'—'}</td>
      <td><span style="color:#888;font-size:0.8em">${m.platform||'—'}</span></td>
      <td style="font-weight:600">${prob}</td>
      <td style="color:${deltaColor}">${delta}</td>
    </tr>`;
  }).join('');
  return `<div class="card">
    <h2>Active Markets (${list.length} total)</h2>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
      <div>
        <table style="margin-bottom:4px"><thead><tr><th>Category</th>${headerCols}</tr></thead><tbody>${gridRows}</tbody></table>
      </div>
      <div>
        <table><thead><tr><th>Theme</th><th>Question</th><th>Plt</th><th>Prob</th><th>Δ24h</th></tr></thead><tbody>${mktRows}</tbody></table>
      </div>
    </div>
  </div>`;
}

function buildNewsCard(news) {
  const list = news?.articles || [];
  if (!list.length) return `<div class="card"><h2>Recent News</h2><div class="section-empty">No news ingested yet</div></div>`;
  const rows = list.slice(0,10).map(a => {
    const matched = (a.matched_markets||[]).slice(0,2).map(m=>`<span style="color:#7fb3d3;font-size:0.78em">${m.question?.substring(0,40)||''}</span>`).join(', ');
    return `<tr>
      <td style="color:#888;font-size:0.78em">${a.source||'—'}</td>
      <td style="max-width:280px">
        ${a.url ? `<a href="${a.url}" target="_blank" style="color:#ddd">${(a.headline||'').substring(0,120)}</a>` : (a.headline||'').substring(0,120)}
        ${matched ? `<div style="margin-top:2px">${matched}</div>` : ''}
      </td>
    </tr>`;
  }).join('');
  return `<div class="card">
    <h2>Recent News (${list.length} articles)</h2>
    <table><thead><tr><th>Source</th><th>Headline + Matched Markets</th></tr></thead><tbody>${rows}</tbody></table>
  </div>`;
}

function buildFilterFunnelCard(fs) {
  if (!fs || !fs.total_fetched) return `<div class="card"><h2>Filter Funnel</h2><div class="section-empty">No filter data yet — waiting for first cycle</div></div>`;
  const total = fs.total_fetched||0;
  const afterLiq = fs.after_liquidity||0;
  const afterMov = fs.after_movement||0;
  const final = fs.final_passed||0;
  const kalshi = fs.kalshi_passed||0;
  const poly = fs.polymarket_passed||0;
  const pct = (n,d) => d>0 ? (n/d*100).toFixed(1)+'%' : '—';
  return `<div class="card">
    <h2>Filter Funnel</h2>
    <div style="margin-top:10px;font-size:0.9em">
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">
        <span style="background:#252836;border-radius:4px;padding:4px 10px;color:#fff;font-weight:700;min-width:70px;text-align:center">${total.toLocaleString()}</span>
        <span style="color:#555">Total fetched</span>
      </div>
      <div style="color:#555;margin-left:35px;margin-bottom:6px">↓ liquidity filter <span style="color:#e74c3c">−${(total-afterLiq).toLocaleString()}</span></div>
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">
        <span style="background:#252836;border-radius:4px;padding:4px 10px;color:#f39c12;font-weight:700;min-width:70px;text-align:center">${afterLiq.toLocaleString()}</span>
        <span style="color:#555">After liquidity <span style="color:#666">(${pct(afterLiq,total)})</span></span>
      </div>
      <div style="color:#555;margin-left:35px;margin-bottom:6px">↓ movement + quality filter <span style="color:#e74c3c">−${(afterLiq-final).toLocaleString()}</span></div>
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:12px">
        <span style="background:#1d6a3a;border-radius:4px;padding:4px 10px;color:#2ecc71;font-weight:700;min-width:70px;text-align:center">${final.toLocaleString()}</span>
        <span style="color:#aaa;font-weight:600">Final passed <span style="color:#666">(${pct(final,total)})</span></span>
      </div>
      <div style="display:flex;gap:16px;margin-top:4px;padding-top:10px;border-top:1px solid #252836">
        <div><span style="color:#16a085;font-weight:700">${kalshi}</span> <span style="color:#666;font-size:0.85em">Kalshi</span></div>
        <div><span style="color:#2980b9;font-weight:700">${poly}</span> <span style="color:#666;font-size:0.85em">Polymarket</span></div>
      </div>
    </div>
  </div>`;
}

function buildMoversCard(movers) {
  const list = movers?.movers || [];
  if (!list.length) return `<div class="card wide"><h2>Price Movers (24h)</h2><div class="section-empty">No significant price movement in last 24h</div></div>`;
  const rows = list.map(m => {
    const range = m.price_range != null ? m.price_range : 0;
    const rangePct = (range*100).toFixed(1)+'%';
    let rangeColor = '#f1c40f';
    if (range >= 0.5) rangeColor = '#e74c3c';
    else if (range >= 0.2) rangeColor = '#e67e22';
    const liq = m.liquidity != null ? '$'+Number(m.liquidity).toLocaleString(undefined,{maximumFractionDigits:0}) : '—';
    const high = m.prob_high != null ? (m.prob_high*100).toFixed(1)+'%' : '—';
    const low = m.prob_low != null ? (m.prob_low*100).toFixed(1)+'%' : '—';
    const q = (m.question||'').substring(0,80);
    return `<tr>
      <td><span style="color:#888;font-size:0.8em">${m.platform||'—'}</span></td>
      <td style="max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${(m.question||'').replace(/"/g,'&quot;')}">${q}</td>
      <td style="color:#888;font-size:0.82em">${liq}</td>
      <td style="color:${rangeColor};font-weight:700">${rangePct}</td>
      <td style="color:#2ecc71">${high}</td>
      <td style="color:#e74c3c">${low}</td>
    </tr>`;
  }).join('');
  return `<div class="card wide">
    <h2>Price Movers — Top ${list.length} by 24h Range</h2>
    <div style="color:#555;font-size:0.8em;margin-bottom:8px"><span style="color:#e74c3c">■</span> &gt;50% &nbsp;<span style="color:#e67e22">■</span> 20–50% &nbsp;<span style="color:#f1c40f">■</span> 5–20%</div>
    <table><thead><tr><th>Platform</th><th>Question</th><th>Liquidity</th><th>Range</th><th>High</th><th>Low</th></tr></thead><tbody>${rows}</tbody></table>
  </div>`;
}

function buildNewsApiCard(ns) {
  if (!ns) return '';
  const used = ns.calls_today||0;
  const limit = ns.daily_limit||80;
  const pctUsed = Math.min(100, Math.round(used/limit*100));
  const barColor = pctUsed > 80 ? '#e74c3c' : pctUsed > 60 ? '#f39c12' : '#2ecc71';
  return `<div class="card">
    <h2>NewsAPI Usage</h2>
    <div style="margin-top:10px">
      <div style="display:flex;justify-content:space-between;margin-bottom:4px">
        <span style="color:#aaa">${used} / ${limit} calls today</span>
        <span style="color:${barColor};font-weight:600">${pctUsed}%</span>
      </div>
      <div style="background:#252836;border-radius:4px;height:10px;width:100%">
        <div style="background:${barColor};height:100%;width:${pctUsed}%;border-radius:4px;transition:width 0.3s"></div>
      </div>
      <div style="margin-top:8px;color:#666;font-size:0.82em">
        Last fetch: <span style="color:#aaa">${fmtTime(ns.last_fetch)}</span>
        &nbsp;|&nbsp; Remaining: <span style="color:#aaa">${limit-used}</span>
      </div>
    </div>
  </div>`;
}

async function render() {
  const data = await fetchAll();

  const now = new Date();
  document.getElementById('page-ts').textContent = `Last refreshed: ${now.toLocaleTimeString()} — Engine cycles every 10 minutes`;

  const h = data.health;
  const cards = [
    buildHealthCard(h, data.filter_stats),
    buildFetchTimesCard(h),
    buildFilterFunnelCard(data.filter_stats),
    buildNewsApiCard(data.newsapi_status),
    buildOpportunitiesCard(data.opportunities),
    buildSignalCard(data.signals),
    buildMoversCard(data.movers),
    buildOverviewCard(data.overview, data.stats),
    buildBetsCard(data.bets),
    buildMarketsCard(data.markets),
    buildNewsCard(data.news),
    buildBacktestCard(data.backtest),
    buildCalibrationCard(data.calibration),
  ].filter(Boolean).join('');

  document.getElementById('root').innerHTML = cards;
}

render();
setInterval(render, 60000);
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Existing endpoints
# ---------------------------------------------------------------------------

@app.get("/pm/overview")
def pm_overview():
    session = get_session()
    try:
        pb = PaperBettor(session)
        stats = pb.get_stats(days=3650)
        return {
            "total_bets": stats["total_bets"],
            "win_rate": round(stats["win_rate"], 4),
            "best_category": stats["best_category"],
            "best_signal_type": stats["best_signal_type"],
            "total_pnl_units": round(stats["total_pnl_units"], 4),
            "roi_percent": round(stats["roi_percent"], 4),
        }
    finally:
        session.close()


@app.get("/pm/opportunities")
def pm_opportunities():
    return {"opportunities": snapshot()["opportunities"]}


@app.get("/pm/bets")
def pm_bets(category: str | None = None, days: int = Query(default=30)):
    since = dt.datetime.now(dt.UTC) - dt.timedelta(days=days)
    session = get_session()
    try:
        q = select(PaperBet).where(PaperBet.placed_at >= since)
        if category:
            q = q.where(PaperBet.category == category)
        rows = list(session.scalars(q.order_by(PaperBet.placed_at.desc())).all())
        out = []
        for r in rows:
            out.append(
                {
                    "id": r.id,
                    "question": r.question[:200],
                    "platform": r.platform,
                    "direction": r.bet_direction,
                    "outcome": r.outcome,
                    "pnl_units": r.pnl_units,
                    "placed_at": r.placed_at.isoformat() if r.placed_at else None,
                    "trigger": r.trigger_type,
                }
            )
        return {"bets": out}
    finally:
        session.close()


@app.get("/pm/markets")
def pm_markets(theme: str | None = None):
    session = get_session()
    try:
        q = select(Market).where(Market.is_active == True)  # noqa: E712
        if theme:
            q = q.where(Market.theme == theme)
        rows = list(session.scalars(q.limit(500)).all())
        kb = KnowledgeBase(session)
        out = []
        now = dt.datetime.now(dt.UTC)
        for m in rows:
            lr = kb.latest_reading(m.id)
            prev = None
            if lr:
                old = session.scalars(
                    select(ProbabilityReading)
                    .where(
                        ProbabilityReading.market_id == m.id,
                        ProbabilityReading.timestamp <= now - dt.timedelta(hours=24),
                    )
                    .order_by(ProbabilityReading.timestamp.desc())
                    .limit(1)
                ).first()
                prev = old.probability if old else None
            cur = lr.probability if lr else None
            delta_24h = None
            if cur is not None and prev is not None:
                delta_24h = cur - prev
            out.append(
                {
                    "id": m.id,
                    "question": m.question[:180],
                    "theme": m.theme,
                    "platform": m.platform,
                    "probability": cur,
                    "delta_24h": delta_24h,
                }
            )
        return {"markets": out}
    finally:
        session.close()


@app.get("/pm/news")
def pm_news():
    session = get_session()
    try:
        arts = list(
            session.scalars(select(NewsArticle).order_by(NewsArticle.fetched_at.desc()).limit(20)).all()
        )
        out = []
        for a in arts:
            links = list(
                session.scalars(select(NewsMarketLink).where(NewsMarketLink.article_id == a.id)).all()
            )
            mkts = []
            for ln in links[:5]:
                m = session.get(Market, ln.market_id)
                mkts.append(
                    {"market_id": ln.market_id, "relevance": ln.relevance_score, "question": m.question[:80] if m else ""}
                )
            out.append(
                {
                    "id": a.id,
                    "headline": a.headline[:200],
                    "source": a.source,
                    "url": a.url,
                    "matched_markets": mkts,
                }
            )
        return {"articles": out}
    finally:
        session.close()


@app.get("/pm/health")
def pm_health():
    h = snapshot()["health"]
    fetch_times = {}
    state = snapshot()
    for src, t in state.get("last_market_fetch", {}).items():
        fetch_times[src] = t.isoformat() if t else None
    for src, t in state.get("last_news_fetch", {}).items():
        fetch_times[src] = t.isoformat() if t else None
    return {
        "markets_tracked": h.get("markets_tracked"),
        "ok_markets": h.get("ok_markets"),
        "stale_markets": h.get("stale_markets"),
        "last_poll_time": h.get("last_poll_time"),
        "is_ready": h.get("is_ready"),
        "news_articles_today": h.get("news_articles_today"),
        "open_paper_bets": h.get("open_paper_bets"),
        "alerts_last_24h": h.get("alerts_last_24h"),
        "fetch_times": fetch_times,
    }


# ---------------------------------------------------------------------------
# New endpoints
# ---------------------------------------------------------------------------

@app.get("/pm/signals")
def pm_signals():
    """Opportunities broken down by signal type. Includes arbitrage separately."""
    opps = snapshot()["opportunities"]
    _KNOWN = {
        "outlier", "velocity", "arbitrage", "news_lag",
        "related_divergence", "scheduled_proximity", "thin_liquidity", "cross_category_momentum",
    }
    grouped: dict[str, list] = {k: [] for k in _KNOWN}
    grouped["other"] = []
    for o in opps:
        sig = o.get("signal_type", "other")
        if sig in grouped:
            grouped[sig].append(o)
        else:
            grouped["other"].append(o)
    return {
        "total": len(opps),
        **grouped,
    }


@app.get("/pm/arb")
def pm_arb():
    """Arbitrage signals only — these are never auto-bet and require manual review."""
    opps = snapshot()["opportunities"]
    arb = [o for o in opps if o.get("signal_type") == "arbitrage"]
    return {
        "count": len(arb),
        "note": "Arbitrage signals require manual execution on two platforms simultaneously. Never auto-bet.",
        "arbitrage_opportunities": arb,
    }


@app.get("/pm/stats")
def pm_stats():
    """Per-signal-type and per-category paper bet performance breakdown."""
    session = get_session()
    try:
        pb = PaperBettor(session)
        signal_types = ["outlier", "velocity", "news_lag", "arbitrage"]
        by_signal: dict[str, dict] = {}
        for sig in signal_types:
            s = pb.get_stats(signal_type=sig, days=3650)
            if s["total_bets"] > 0:
                by_signal[sig] = {
                    "total_bets": s["total_bets"],
                    "win_rate": round(s["win_rate"], 4),
                    "pnl_units": round(s["total_pnl_units"], 4),
                    "roi_percent": round(s["roi_percent"], 4),
                }

        categories = ["fed_macro", "cpi_macro", "elections_us", "elections_intl",
                      "btc_price", "eth_events", "other_crypto", "sports_nfl",
                      "sports_nba", "sports_mlb", "sports_soccer", "geopolitics",
                      "recession", "tech_ai", "entertainment"]
        by_cat: dict[str, dict] = {}
        for cat in categories:
            s = pb.get_stats(category=cat, days=3650)
            if s["total_bets"] > 0:
                by_cat[cat] = {
                    "total_bets": s["total_bets"],
                    "win_rate": round(s["win_rate"], 4),
                    "pnl_units": round(s["total_pnl_units"], 4),
                    "roi_percent": round(s["roi_percent"], 4),
                }

        overall = pb.get_stats(days=3650)
        return {
            "overall": {
                "total_bets": overall["total_bets"],
                "win_rate": round(overall["win_rate"], 4),
                "pnl_units": round(overall["total_pnl_units"], 4),
                "roi_percent": round(overall["roi_percent"], 4),
                "max_drawdown": round(overall.get("max_drawdown", 0), 4),
            },
            "by_signal_type": by_signal,
            "by_category": by_cat,
        }
    finally:
        session.close()


@app.get("/pm/backtest")
def pm_backtest():
    """Return backtest results from last run, plus summary stats."""
    session = get_session()
    try:
        rows = list(
            session.scalars(
                select(BacktestResult).order_by(BacktestResult.run_date.desc())
            ).all()
        )
        if not rows:
            return {"results": [], "top_combos": [], "best_kelly": {}, "best_entry_window": {}, "markets_analyzed": 0, "signal_fires": {}, "run_date": None}

        run_date = rows[0].run_date.isoformat() if rows[0].run_date else None

        # Base signal rows (no combo stacking, no kelly, no time-of-day)
        base_rows = []
        for r in rows:
            if r.signal_type.startswith("hour_") or r.signal_type.startswith("weekday_"):
                continue
            base_rows.append({
                "signal_type": r.signal_type,
                "signal_combo": r.signal_combo,
                "hit_rate": r.hit_rate,
                "avg_edge": r.avg_edge,
                "ev": r.ev,
                "roi": r.roi,
                "sharpe": r.sharpe,
                "max_drawdown": r.max_drawdown,
                "sample_size": r.sample_size,
                "kelly_fraction": r.kelly_fraction,
                "kelly_roi": r.kelly_roi,
                "kelly_final_bankroll": r.kelly_final_bankroll,
                "kelly_max_drawdown": r.kelly_max_drawdown,
                "best_entry_window": r.best_entry_window,
            })

        # Top combos: signal_type == "combo", sorted by ev desc
        combos = sorted(
            [r for r in base_rows if r["signal_type"] == "combo"],
            key=lambda x: float(x.get("ev") or 0),
            reverse=True,
        )[:5]

        # Best Kelly: kelly_fraction rows for all signals combined, pick by roi
        kelly_rows = [r for r in base_rows if r.get("kelly_fraction") and r.get("kelly_roi") is not None]
        best_kelly: dict = {}
        if kelly_rows:
            best_kelly_row = max(kelly_rows, key=lambda x: float(x.get("kelly_roi") or 0))
            best_kelly = {
                "fraction": best_kelly_row["kelly_fraction"],
                "roi": best_kelly_row["kelly_roi"],
                "final_bankroll": best_kelly_row["kelly_final_bankroll"],
                "max_drawdown": best_kelly_row["kelly_max_drawdown"],
            }

        # Best entry window
        window_rows = [r for r in base_rows if r["signal_type"].startswith("entry_window_")]
        best_entry_window: dict = {}
        if window_rows:
            best_w = max(window_rows, key=lambda x: float(x.get("ev") or 0))
            best_entry_window = {
                "window": best_w.get("best_entry_window"),
                "hit_rate": best_w.get("hit_rate"),
                "ev": best_w.get("ev"),
            }

        return {
            "results": base_rows[:50],
            "top_combos": combos,
            "best_kelly": best_kelly,
            "best_entry_window": best_entry_window,
            "markets_analyzed": 0,
            "signal_fires": {},
            "run_date": run_date,
        }
    finally:
        session.close()


@app.get("/pm/calibration")
def pm_calibration():
    """Return calibration analysis results (market prob vs actual resolution rate)."""
    session = get_session()
    try:
        rows = list(
            session.scalars(
                select(CalibrationResult).order_by(CalibrationResult.run_date.desc())
            ).all()
        )
        out = []
        for r in rows:
            out.append({
                "source": r.source,
                "category": r.category,
                "prob_bucket": r.prob_bucket,
                "avg_market_prob": r.avg_market_prob,
                "actual_resolution_rate": r.actual_resolution_rate,
                "sample_size": r.sample_size,
                "bias": r.bias,
                "run_date": r.run_date.isoformat() if r.run_date else None,
            })
        # Find most over/underpriced
        over = sorted([r for r in out if r.get("bias") is not None], key=lambda x: x["bias"])
        under = list(reversed(over))
        return {
            "results": out[:100],
            "most_overpriced": over[:5],
            "most_underpriced": under[:5],
            "total": len(out),
        }
    finally:
        session.close()


# ---------------------------------------------------------------------------
# New endpoints (filter funnel, movers, newsapi status)
# ---------------------------------------------------------------------------

@app.get("/pm/filter_stats")
def pm_filter_stats():
    s = snapshot()
    return s.get("filter_stats", {
        "total_fetched": 0,
        "after_liquidity": 0,
        "after_movement": 0,
        "final_passed": 0,
        "kalshi_passed": 0,
        "polymarket_passed": 0,
    })


@app.get("/pm/movers")
def pm_movers():
    session = get_session()
    try:
        since = dt.datetime.now(dt.UTC) - dt.timedelta(hours=24)
        rows = session.execute(text("""
            SELECT m.question, m.platform, m.category, m.liquidity, m.volume_24h,
                   MAX(pr.probability) - MIN(pr.probability) as price_range,
                   MAX(pr.probability) as prob_high,
                   MIN(pr.probability) as prob_low,
                   MAX(pr.timestamp) as last_update
            FROM probability_readings pr
            JOIN markets m ON m.id = pr.market_id
            WHERE pr.timestamp >= :since
            AND pr.probability > 0 AND pr.probability < 1
            GROUP BY pr.market_id
            HAVING price_range >= 0.05
            ORDER BY price_range DESC
            LIMIT 15
        """), {"since": since.isoformat()}).fetchall()
        return {"movers": [dict(r._mapping) for r in rows]}
    finally:
        session.close()


@app.get("/pm/newsapi_status")
def pm_newsapi_status():
    s = snapshot()
    return s.get("newsapi_status", {"calls_today": 0, "daily_limit": 80, "last_fetch": None})


# ---------------------------------------------------------------------------
# HTML index
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def index():
    return _HTML_PAGE


def run_uvicorn():
    import uvicorn

    uvicorn.run("shared.dashboard.app:app", host="0.0.0.0", port=8090, reload=False)


if __name__ == "__main__":
    run_uvicorn()
