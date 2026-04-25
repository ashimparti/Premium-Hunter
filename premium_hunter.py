"""
Premium Hunter - Daily earnings IV crush opportunity scanner
Built for Ash's checklist: Quality + Valuation + Edge + Flow + Trade structure

Run: python premium_hunter.py
Output: report.html (open in browser)
"""

import yfinance as yf
import pandas as pd
import numpy as np
from scipy.stats import norm
from datetime import datetime, timedelta
import json
import sys
import os
from pathlib import Path

# ==============================================================
# CONFIG
# ==============================================================

RISK_FREE = 0.045  # ~current 10Y treasury
TARGET_DELTA = -0.07  # 5-8 delta sweet spot
MAX_DAYS_TO_EARNINGS = 14


def get_target_dte(ticker=None):
    """Auto-pick next January LEAP — always 6-12 months out.
    Rotates as months pass, no manual updates needed."""
    today = datetime.now()
    # If before July, target next January (6-12 months out)
    # If July or later, target the January after next
    if today.month <= 6:
        target = datetime(today.year + 1, 1, 17)
    else:
        target = datetime(today.year + 2, 1, 17)
    return (target - today).days


# Watchlist - quality optionable stocks + Ash's known names
WATCHLIST = [
    # Mag 7 + tech
    'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'META', 'NVDA', 'TSLA',
    'AVGO', 'AMD', 'CRM', 'ORCL', 'ADBE', 'NFLX', 'NBIS',
    'PANW', 'ZS', 'CRWD', 'PLTR', 'SHOP',
    # Financials
    'JPM', 'V', 'MA', 'BAC', 'WFC', 'GS', 'MS', 'AXP',
    'CINF', 'TRV', 'MKL', 'ALL', 'BX', 'COF', 'HOOD',
    # Healthcare
    'JNJ', 'LLY', 'UNH', 'MRK', 'ABBV', 'NVO', 'ARGX',
    # Consumer
    'KO', 'PEP', 'WMT', 'COST', 'HD', 'NKE', 'MCD', 'SBUX',
    'BBWI', 'EL', 'BABA', 'DPZ', 'AMZN',
    # Energy
    'XOM', 'CVX', 'KMI',
    # Industrial
    'BA', 'CAT', 'GE', 'HON', 'LMT', 'CLS', 'NUE', 'AMKR',
    'AXON', 'LDOS', 'RCL', 'UAL', 'AA',
    # Telecom/Defensive
    'VZ', 'T',
    # ETFs
    'SPY', 'QQQ', 'VOO',
    # Premium hunt names
    'MARA', 'GRAB', 'SOFI', 'UBER', 'RIVN', 'PYPL',
    'TSM', 'MU', 'ELF', 'HIMS', 'CCJ', 'IREN',
]

# ==============================================================
# CALCULATIONS
# ==============================================================

def black_scholes_delta_put(S, K, T, r, sigma):
    """Calculate put delta via Black-Scholes."""
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 0
    try:
        d1 = (np.log(S/K) + (r + sigma**2/2) * T) / (sigma * np.sqrt(T))
        return float(norm.cdf(d1) - 1)
    except:
        return 0


def get_next_earnings(t):
    """Get next earnings date."""
    try:
        cal = t.calendar
        if isinstance(cal, dict):
            ed = cal.get('Earnings Date')
            if ed:
                if isinstance(ed, list) and ed:
                    return pd.Timestamp(ed[0])
                return pd.Timestamp(ed)
        return None
    except Exception:
        return None


def calc_avg_earnings_move(t, current_earnings_date=None, n=8):
    """Average abs % move on past earnings days. Multiple fallback methods."""
    try:
        earnings_dates = []
        
        # Method 1: yfinance earnings_dates (preferred but unreliable)
        try:
            eh = t.earnings_dates
            if eh is not None and not eh.empty:
                now = pd.Timestamp.now(tz='UTC') if eh.index.tz else pd.Timestamp.now()
                past = eh[eh.index < now].head(n)
                earnings_dates = [
                    d.tz_localize(None) if d.tz else d for d in past.index
                ]
        except Exception:
            pass
        
        # Method 2: Estimate by going back in 91-day quarterly increments
        if len(earnings_dates) < 4 and current_earnings_date is not None:
            cur = pd.Timestamp(current_earnings_date)
            cur = cur.tz_localize(None) if cur.tz else cur
            existing = set(d.date() for d in earnings_dates)
            for i in range(1, n+1):
                est = cur - pd.Timedelta(days=91 * i)
                if est.date() not in existing:
                    earnings_dates.append(est)
        
        # Method 3: Use quarterly_financials column dates as last resort
        if len(earnings_dates) == 0:
            try:
                qf = t.quarterly_financials
                if qf is not None and not qf.empty:
                    for c in list(qf.columns)[:n]:
                        # Earnings reported ~35 days after quarter end
                        est = pd.Timestamp(c) + pd.Timedelta(days=35)
                        earnings_dates.append(est)
            except Exception:
                pass
        
        if not earnings_dates:
            return None
        
        # Get price history (3 years to cover 8 quarters)
        hist = t.history(period='3y')
        if hist.empty:
            return None
        
        hist_idx = hist.index.tz_localize(None) if hist.index.tz else hist.index
        
        moves = []
        for date in earnings_dates[:n]:
            try:
                before = hist_idx[hist_idx <= date]
                after = hist_idx[hist_idx > date]
                if len(before) == 0 or len(after) == 0:
                    continue
                bidx = before.max()
                aidx = after.min()
                # Skip if the gap between before/after is too large (>10 days = market closure)
                if (aidx - bidx).days > 10:
                    continue
                pb_pos = hist_idx.get_loc(bidx)
                pa_pos = hist_idx.get_loc(aidx)
                pb = float(hist['Close'].iloc[pb_pos])
                pa = float(hist['Close'].iloc[pa_pos])
                if pb <= 0:
                    continue
                pct = abs((pa - pb) / pb * 100)
                # Sanity check: reject moves >50% (likely data error)
                if pct > 50:
                    continue
                moves.append(pct)
            except Exception:
                continue
        
        if not moves:
            return None
        
        return {
            'avg_move': float(np.mean(moves)),
            'max_move': float(max(moves)),
            'red_x_count': int(sum(1 for m in moves if m > 5)),
            'sample': len(moves),
            'all_moves': [round(m, 2) for m in moves]
        }
    except Exception:
        return None


def calc_expected_move(t, S):
    """Expected move from nearest weekly ATM straddle."""
    try:
        expiries = t.options
        if not expiries:
            return None
        
        today = datetime.now()
        for exp in expiries:
            try:
                dte = (datetime.strptime(exp, '%Y-%m-%d') - today).days
                if 5 <= dte <= 21:
                    chain = t.option_chain(exp)
                    calls = chain.calls.copy()
                    puts = chain.puts.copy()
                    if calls.empty or puts.empty:
                        continue
                    calls['diff'] = (calls['strike'] - S).abs()
                    puts['diff'] = (puts['strike'] - S).abs()
                    ac = calls.loc[calls['diff'].idxmin()]
                    ap = puts.loc[puts['diff'].idxmin()]
                    straddle = float(ac['lastPrice']) + float(ap['lastPrice'])
                    if straddle <= 0:
                        continue
                    return {
                        'expected_pct': straddle / S * 100,
                        'expected_dollar': straddle,
                        'expiry': exp,
                        'dte': dte
                    }
            except Exception:
                continue
        return None
    except Exception:
        return None


def find_target_put(t, S, ticker_symbol):
    """Find ~5-7 delta put around target DTE (auto-adjusted for ticker tier)."""
    try:
        target_dte = get_target_dte(ticker_symbol)
        expiries = t.options
        if not expiries:
            return None
        
        today = datetime.now()
        best_exp = None
        best_diff = 9999
        for exp in expiries:
            try:
                dte = (datetime.strptime(exp, '%Y-%m-%d') - today).days
                if dte < 90:
                    continue
                d = abs(dte - target_dte)
                if d < best_diff:
                    best_diff = d
                    best_exp = exp
            except:
                continue
        
        if not best_exp:
            return None
        
        chain = t.option_chain(best_exp)
        puts = chain.puts.copy()
        if puts.empty:
            return None
        
        T = (datetime.strptime(best_exp, '%Y-%m-%d') - today).days / 365
        
        puts['delta_calc'] = puts.apply(
            lambda r: black_scholes_delta_put(
                S, r['strike'], T, RISK_FREE,
                r['impliedVolatility'] if r['impliedVolatility'] > 0 else 0.3
            ), axis=1
        )
        
        # Filter to reasonable strikes (OTM, has bid, has OI)
        puts = puts[(puts['strike'] < S) & (puts['bid'] > 0)]
        if puts.empty:
            return None
        
        puts['delta_diff'] = (puts['delta_calc'] - TARGET_DELTA).abs()
        best = puts.loc[puts['delta_diff'].idxmin()]
        
        return {
            'expiry': best_exp,
            'dte': (datetime.strptime(best_exp, '%Y-%m-%d') - today).days,
            'strike': float(best['strike']),
            'delta': float(best['delta_calc']),
            'iv': float(best['impliedVolatility']),
            'bid': float(best['bid']),
            'ask': float(best['ask']),
            'mid': float((best['bid'] + best['ask']) / 2),
            'oi': int(best['openInterest']) if not pd.isna(best['openInterest']) else 0,
            'pct_otm': (S - float(best['strike'])) / S * 100
        }
    except Exception:
        return None


# ==============================================================
# SCORING
# ==============================================================

def score(d):
    """5-section checklist scoring."""
    s = 0.0
    flags = []
    passes = []
    
    # 1. Quality
    if d['market_cap'] >= 5e9:
        s += 1; passes.append('Quality')
    else:
        flags.append('Low mkt cap')
    
    rec = d.get('recommendation', '').lower()
    if rec in ('strong_buy', 'buy', 'moderate_buy'):
        s += 0.5
    
    # 2. Valuation
    peg = d.get('peg')
    if peg and 0 < peg < 2:
        s += 1; passes.append('PEG')
    elif peg and peg < 3:
        s += 0.5
    elif peg and peg >= 3:
        flags.append(f'High PEG {peg:.1f}')
    
    upside = d.get('target_upside_pct')
    if upside and upside > 0:
        s += 0.5
    
    # 3. Earnings edge
    es = d.get('earnings_stats')
    em = d.get('expected_move')
    if es and em and es['avg_move'] > 0:
        ratio = em['expected_pct'] / es['avg_move']
        if ratio >= 2:
            s += 1.5; passes.append(f'Edge {ratio:.1f}x')
        elif ratio >= 1.3:
            s += 0.5
        else:
            flags.append(f'Weak edge {ratio:.1f}x')
        
        if es['red_x_count'] <= 1:
            s += 0.5; passes.append('Low gap risk')
        elif es['red_x_count'] >= 4:
            flags.append(f'{es["red_x_count"]} gaps in 8Q')
    
    # 4. Trade structure
    p = d.get('put_trade')
    if p and p['pct_otm'] >= 25 and p['oi'] >= 50:
        s += 1; passes.append('Safe strike')
    elif p and p['oi'] < 20:
        flags.append('Low OI')
    
    return {
        'score': round(s, 1),
        'flags': flags,
        'passes': passes
    }


# ==============================================================
# DATA PIPELINE
# ==============================================================

def process_ticker(ticker):
    """Pull and score a ticker. Return None if not eligible."""
    try:
        t = yf.Ticker(ticker)
        info = t.info
        
        if not info or len(info) < 5:
            return None
        
        S = info.get('currentPrice') or info.get('regularMarketPrice')
        if not S:
            return None
        
        ne = get_next_earnings(t)
        dte = None
        if ne is not None:
            try:
                ne_naive = ne.tz_localize(None) if ne.tz else ne
                dte = (ne_naive - pd.Timestamp.now()).days
            except:
                pass
        
        # Must have earnings in window
        if dte is None or dte < 0 or dte > MAX_DAYS_TO_EARNINGS:
            return None
        
        d = {
            'ticker': ticker,
            'company': info.get('longName', ticker)[:40],
            'sector': info.get('sector', '—'),
            'price': float(S),
            'market_cap': info.get('marketCap') or 0,
            'peg': info.get('trailingPegRatio') or info.get('pegRatio'),
            'pe': info.get('trailingPE'),
            'recommendation': info.get('recommendationKey', 'none'),
            'target_mean': info.get('targetMeanPrice'),
            'analyst_count': info.get('numberOfAnalystOpinions', 0),
            'days_to_earnings': int(dte),
            'next_earnings': ne.strftime('%Y-%m-%d') if ne is not None else None,
        }
        
        if d['target_mean']:
            d['target_upside_pct'] = (d['target_mean'] - S) / S * 100
        else:
            d['target_upside_pct'] = None
        
        print(f"  Processing {ticker}... earnings in {dte}d")
        
        d['earnings_stats'] = calc_avg_earnings_move(t, current_earnings_date=ne)
        d['expected_move'] = calc_expected_move(t, S)
        d['put_trade'] = find_target_put(t, S, ticker)
        
        if d['earnings_stats'] and d['expected_move']:
            avg = d['earnings_stats']['avg_move']
            exp = d['expected_move']['expected_pct']
            d['edge_ratio'] = round(exp / avg, 2) if avg > 0 else 0
        else:
            d['edge_ratio'] = 0
        
        sc = score(d)
        d['score'] = sc['score']
        d['flags'] = sc['flags']
        d['passes'] = sc['passes']
        
        return d
    except Exception as e:
        print(f"  ✗ {ticker}: {e}", file=sys.stderr)
        return None


# ==============================================================
# HTML REPORT
# ==============================================================

def render_html(results, scan_date):
    """Generate the HTML report."""
    
    # Sort by score, then edge ratio
    results.sort(key=lambda x: (x['score'], x['edge_ratio']), reverse=True)
    
    top_picks = [r for r in results if r['score'] >= 3.5]
    watch = [r for r in results if 2 <= r['score'] < 3.5]
    skip = [r for r in results if r['score'] < 2]
    
    # Build rows
    def build_row(r, rank=None):
        es = r.get('earnings_stats') or {}
        em = r.get('expected_move') or {}
        pt = r.get('put_trade') or {}
        
        edge_color = '#16a34a' if r['edge_ratio'] >= 2 else '#ca8a04' if r['edge_ratio'] >= 1.3 else '#dc2626'
        score_color = '#16a34a' if r['score'] >= 3.5 else '#ca8a04' if r['score'] >= 2 else '#94a3b8'
        
        flags_html = ''
        if r.get('flags'):
            flags_html = ''.join(f'<span class="flag warn">{f}</span>' for f in r['flags'])
        if r.get('passes'):
            flags_html += ''.join(f'<span class="flag pass">{p}</span>' for p in r['passes'])
        
        trade_html = '—'
        if pt:
            trade_html = f"""
                <div class="trade">
                    <div class="trade-strike">${pt['strike']:.0f}P</div>
                    <div class="trade-meta">{pt['expiry'][:7]} · {pt['dte']}d · {pt['delta']*100:.1f}Δ</div>
                    <div class="trade-credit">${pt['mid']*100:.0f} mid · {pt['pct_otm']:.0f}% OTM</div>
                </div>
            """
        
        return f"""
        <tr>
            <td><span class="score" style="background:{score_color}">{r['score']}</span></td>
            <td>
                <div class="ticker"><a href="https://unusualwhales.com/stock/{r['ticker']}/earnings" target="_blank">{r['ticker']}</a></div>
                <div class="company">{r['company']}</div>
                <div class="sector">{r['sector']}</div>
            </td>
            <td>
                <div class="price">${r['price']:.2f}</div>
                <div class="muted">${r['market_cap']/1e9:.1f}B cap</div>
                <div class="muted">PEG {('%.1f' % r['peg']) if r['peg'] else 'n/a'}</div>
            </td>
            <td>
                <div class="earn-date">{r['next_earnings']}</div>
                <div class="earn-dte">{r['days_to_earnings']}d away</div>
                <div class="muted">{r['recommendation'].replace('_',' ').title()}</div>
            </td>
            <td>
                <div class="edge" style="color:{edge_color}">{r['edge_ratio']}x edge</div>
                <div class="muted">Expected: {em.get('expected_pct',0):.1f}%</div>
                <div class="muted">Avg actual: {es.get('avg_move',0):.1f}%</div>
                <div class="muted">Red X: {es.get('red_x_count','—')}/{es.get('sample','—')}</div>
            </td>
            <td>{trade_html}</td>
            <td><div class="flags">{flags_html}</div></td>
        </tr>
        """
    
    table_top = ''.join(build_row(r, i+1) for i, r in enumerate(top_picks))
    table_watch = ''.join(build_row(r) for r in watch)
    
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Premium Hunter — {scan_date}</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
    font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "Inter", sans-serif;
    background: #0f172a;
    color: #e2e8f0;
    padding: 32px 24px;
    line-height: 1.5;
}}
.container {{ max-width: 1400px; margin: 0 auto; }}
header {{
    border-bottom: 1px solid #334155;
    padding-bottom: 20px;
    margin-bottom: 28px;
}}
h1 {{
    font-size: 28px;
    font-weight: 600;
    color: #f1f5f9;
    letter-spacing: -0.02em;
}}
.subtitle {{ color: #94a3b8; font-size: 14px; margin-top: 4px; }}
.stats {{
    display: flex;
    gap: 24px;
    margin: 24px 0;
}}
.stat {{
    background: #1e293b;
    padding: 14px 20px;
    border-radius: 8px;
    border: 1px solid #334155;
}}
.stat-num {{ font-size: 22px; font-weight: 600; color: #f1f5f9; }}
.stat-label {{ font-size: 12px; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.05em; }}
.section {{ margin-bottom: 40px; }}
.section-title {{
    font-size: 16px;
    font-weight: 600;
    color: #f1f5f9;
    margin-bottom: 12px;
    display: flex;
    align-items: center;
    gap: 8px;
}}
.section-title.fire::before {{ content: "🔥"; }}
.section-title.watch::before {{ content: "👀"; }}
table {{
    width: 100%;
    border-collapse: collapse;
    background: #1e293b;
    border-radius: 8px;
    overflow: hidden;
    border: 1px solid #334155;
}}
th {{
    background: #0f172a;
    color: #94a3b8;
    text-align: left;
    padding: 12px 16px;
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    border-bottom: 1px solid #334155;
}}
td {{
    padding: 14px 16px;
    border-bottom: 1px solid #334155;
    vertical-align: top;
    font-size: 13px;
}}
tr:last-child td {{ border-bottom: none; }}
tr:hover {{ background: #283548; }}
.score {{
    display: inline-block;
    color: #fff;
    font-weight: 600;
    padding: 4px 10px;
    border-radius: 6px;
    font-size: 14px;
    min-width: 32px;
    text-align: center;
}}
.ticker a {{
    font-size: 16px;
    font-weight: 600;
    color: #60a5fa;
    text-decoration: none;
}}
.ticker a:hover {{ text-decoration: underline; }}
.company {{ font-size: 12px; color: #cbd5e1; margin-top: 2px; }}
.sector {{ font-size: 11px; color: #94a3b8; }}
.price {{ font-size: 15px; font-weight: 600; color: #f1f5f9; }}
.muted {{ font-size: 11px; color: #94a3b8; margin-top: 2px; }}
.earn-date {{ font-size: 13px; font-weight: 500; color: #f1f5f9; }}
.earn-dte {{ font-size: 11px; color: #fbbf24; margin-top: 2px; font-weight: 500; }}
.edge {{ font-size: 15px; font-weight: 600; }}
.trade-strike {{ font-size: 14px; font-weight: 600; color: #f1f5f9; }}
.trade-meta {{ font-size: 11px; color: #94a3b8; margin-top: 2px; }}
.trade-credit {{ font-size: 12px; color: #34d399; margin-top: 2px; font-weight: 500; }}
.flags {{ display: flex; flex-direction: column; gap: 4px; max-width: 140px; }}
.flag {{
    font-size: 10px;
    padding: 3px 8px;
    border-radius: 4px;
    font-weight: 500;
    display: inline-block;
}}
.flag.pass {{ background: #064e3b; color: #6ee7b7; }}
.flag.warn {{ background: #7c2d12; color: #fdba74; }}
.empty {{
    background: #1e293b;
    border: 1px dashed #475569;
    border-radius: 8px;
    padding: 32px;
    text-align: center;
    color: #94a3b8;
    font-size: 13px;
}}
.legend {{
    background: #1e293b;
    border: 1px solid #334155;
    border-radius: 8px;
    padding: 16px 20px;
    margin-top: 32px;
    font-size: 12px;
    color: #cbd5e1;
}}
.legend strong {{ color: #f1f5f9; }}
</style>
</head>
<body>
<div class="container">
    <header>
        <h1>Premium Hunter</h1>
        <div class="subtitle">Daily IV crush opportunity scan · {scan_date}</div>
    </header>
    
    <div class="stats">
        <div class="stat">
            <div class="stat-num">{len(results)}</div>
            <div class="stat-label">Stocks scanned</div>
        </div>
        <div class="stat">
            <div class="stat-num" style="color:#34d399">{len(top_picks)}</div>
            <div class="stat-label">Top picks (≥3.5)</div>
        </div>
        <div class="stat">
            <div class="stat-num" style="color:#fbbf24">{len(watch)}</div>
            <div class="stat-label">Watch (2.0-3.4)</div>
        </div>
        <div class="stat">
            <div class="stat-num" style="color:#94a3b8">{len(skip)}</div>
            <div class="stat-label">Skip (&lt;2.0)</div>
        </div>
    </div>
    
    <div class="section">
        <div class="section-title fire">Top picks — fire candidates</div>
        {table_top and f'<table><thead><tr><th>Score</th><th>Ticker</th><th>Stock</th><th>Earnings</th><th>IV Edge</th><th>Suggested Trade (~5Δ)</th><th>Flags</th></tr></thead><tbody>{table_top}</tbody></table>' or '<div class="empty">No top-tier picks today. Check the watch list below or come back tomorrow.</div>'}
    </div>
    
    <div class="section">
        <div class="section-title watch">Watch list — partial fits</div>
        {table_watch and f'<table><thead><tr><th>Score</th><th>Ticker</th><th>Stock</th><th>Earnings</th><th>IV Edge</th><th>Suggested Trade (~5Δ)</th><th>Flags</th></tr></thead><tbody>{table_watch}</tbody></table>' or '<div class="empty">No watch-list candidates today.</div>'}
    </div>
    
    <div class="legend">
        <strong>Scoring breakdown (max 5.5):</strong> Quality (1.5) · Valuation (1.5) · Earnings edge (2.0) · Trade structure (1.0) ·
        <strong>Edge ratio</strong> = expected move ÷ avg actual move. ≥2x is the sweet spot. ·
        <strong>Red X</strong> = past quarters where actual exceeded expected (gap risk). ·
        <strong>Click ticker</strong> to open Unusual Whales for manual flow + dark pool check before firing.
    </div>
</div>
</body>
</html>"""


# ==============================================================
# MAIN
# ==============================================================

def main():
    print(f"Premium Hunter — scanning {len(WATCHLIST)} tickers...")
    print(f"Looking for earnings in next {MAX_DAYS_TO_EARNINGS} days\n")
    
    results = []
    for ticker in WATCHLIST:
        d = process_ticker(ticker)
        if d:
            results.append(d)
    
    print(f"\nFound {len(results)} stocks with upcoming earnings.")
    
    scan_date = datetime.now().strftime('%A, %B %d, %Y')
    html = render_html(results, scan_date)
    
    out_path = Path('report.html')
    out_path.write_text(html)
    print(f"Report saved to: {out_path.absolute()}")
    
    # Also save raw JSON for downstream use
    json_path = Path('scan_results.json')
    json_path.write_text(json.dumps(results, default=str, indent=2))
    print(f"Raw data saved to: {json_path.absolute()}")
    
    return results


if __name__ == '__main__':
    main()
