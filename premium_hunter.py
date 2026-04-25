"""
Premium Hunter v3 — Daily earnings IV crush scanner
Day-organized · Auto-signals · Market Dashboard · Quality + Hunt tiers

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
import re
from pathlib import Path

# ==============================================================
# CONFIG
# ==============================================================

RISK_FREE = 0.045
TARGET_DELTA = -0.07
MIN_MARKET_CAP = 10e9  # $10B minimum (raised from $5B)
MAX_DAYS_TO_EARNINGS = 14

# Stocks Ash would happily own at the strike — relaxed filters, 18-month LEAPs
QUALITY_WHITELIST = {
    'META', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'AAPL', 'AVGO',
    'V', 'MA', 'JPM', 'LLY', 'UNH', 'COST', 'WMT', 'HD',
    'NKE', 'BABA', 'BX', 'NFLX', 'CRM', 'ABBV', 'BAC',
    'ORCL', 'XOM', 'CVX', 'KO', 'PEP', 'JNJ', 'MRK',
    'SPY', 'QQQ', 'VOO',
}

# News red flag keywords (lowercase)
RED_FLAG_KEYWORDS = [
    'sec investigation', 'sec charges', 'doj', 'department of justice',
    'class action', 'lawsuit', 'subpoena', 'fraud', 'fraudulent',
    'guidance cut', 'lowered guidance', 'revenue warning',
    'ceo resignation', 'cfo resignation', 'ceo steps down', 'cfo steps down',
    'accounting irregularit', 'restatement', 'investigation',
]


def is_quality(ticker):
    return ticker.upper() in QUALITY_WHITELIST


def get_target_dte(ticker=None):
    """Quality stocks → 18-24mo LEAPs. Hunt → 9mo LEAPs."""
    today = datetime.now()
    if ticker and is_quality(ticker):
        target = datetime(today.year + 2, 1, 17)
    else:
        if today.month <= 6:
            target = datetime(today.year + 1, 1, 17)
        else:
            target = datetime(today.year + 2, 1, 17)
    return (target - today).days


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
    'BBWI', 'EL', 'BABA', 'DPZ',
    # Energy
    'XOM', 'CVX', 'KMI',
    # Industrial / Other
    'BA', 'CAT', 'GE', 'HON', 'LMT', 'CLS', 'NUE', 'AMKR',
    'AXON', 'LDOS', 'RCL', 'UAL', 'AA',
    'VZ', 'T',
    'SPY', 'QQQ', 'VOO',
    'MARA', 'GRAB', 'SOFI', 'UBER', 'RIVN', 'PYPL',
    'TSM', 'MU', 'ELF', 'HIMS', 'CCJ', 'IREN',
]


# ==============================================================
# MARKET DASHBOARD
# ==============================================================

def get_market_dashboard():
    """Pull VIX, SPY, GBP/USD, Brent, 10Y, Gold for the dashboard."""
    tickers = {
        'VIX': '^VIX',
        'SPY': 'SPY',
        '10Y': '^TNX',
        'GBPUSD': 'GBPUSD=X',
        'BRENT': 'BZ=F',
        'GOLD': 'GC=F',
    }
    
    dashboard = {}
    for label, sym in tickers.items():
        try:
            t = yf.Ticker(sym)
            hist = t.history(period='5d')
            if hist.empty:
                dashboard[label] = {'value': None, 'change': None}
                continue
            current = float(hist['Close'].iloc[-1])
            prev = float(hist['Close'].iloc[-2]) if len(hist) > 1 else current
            change_pct = (current - prev) / prev * 100 if prev > 0 else 0
            dashboard[label] = {'value': current, 'change': change_pct}
        except Exception:
            dashboard[label] = {'value': None, 'change': None}
    
    # Determine market regime from VIX
    vix = dashboard.get('VIX', {}).get('value')
    if vix is None:
        regime = 'UNKNOWN'
    elif vix < 16:
        regime = 'CALM — sell premium with confidence'
    elif vix < 22:
        regime = 'NORMAL — standard sizing'
    elif vix < 30:
        regime = 'ELEVATED — reduce size'
    else:
        regime = 'STRESS — reconsider firing'
    
    dashboard['regime'] = regime
    return dashboard


# ==============================================================
# OPTIONS MATH
# ==============================================================

def black_scholes_delta_put(S, K, T, r, sigma):
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 0
    try:
        d1 = (np.log(S/K) + (r + sigma**2/2) * T) / (sigma * np.sqrt(T))
        return float(norm.cdf(d1) - 1)
    except:
        return 0


def get_next_earnings(t):
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


def get_earnings_timing(t, earnings_date):
    """Return 'BMO' (before market open), 'AMC' (after market close), or 'TBD'."""
    try:
        eh = t.earnings_dates
        if eh is not None and not eh.empty and earnings_date is not None:
            target = earnings_date.tz_localize(None) if earnings_date.tz else earnings_date
            for idx in eh.index:
                idx_naive = idx.tz_localize(None) if idx.tz else idx
                if abs((idx_naive.normalize() - target.normalize()).days) <= 1:
                    hour = idx_naive.hour
                    if hour < 9:
                        return 'BMO'
                    elif hour > 16:
                        return 'AMC'
    except Exception:
        pass
    
    try:
        if earnings_date is not None:
            ed_naive = earnings_date.tz_localize(None) if earnings_date.tz else earnings_date
            hour = ed_naive.hour
            if hour < 9:
                return 'BMO'
            elif hour > 16:
                return 'AMC'
    except Exception:
        pass
    
    return 'TBD'


def calc_avg_earnings_move(t, current_earnings_date=None, n=8):
    try:
        earnings_dates = []
        try:
            eh = t.earnings_dates
            if eh is not None and not eh.empty:
                now = pd.Timestamp.now(tz='UTC') if eh.index.tz else pd.Timestamp.now()
                past = eh[eh.index < now].head(n)
                earnings_dates = [d.tz_localize(None) if d.tz else d for d in past.index]
        except Exception:
            pass
        
        if len(earnings_dates) < 4 and current_earnings_date is not None:
            cur = pd.Timestamp(current_earnings_date)
            cur = cur.tz_localize(None) if cur.tz else cur
            existing = set(d.date() for d in earnings_dates)
            for i in range(1, n+1):
                est = cur - pd.Timedelta(days=91 * i)
                if est.date() not in existing:
                    earnings_dates.append(est)
        
        if not earnings_dates:
            return None
        
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
                if (aidx - bidx).days > 10:
                    continue
                pb_pos = hist_idx.get_loc(bidx)
                pa_pos = hist_idx.get_loc(aidx)
                pb = float(hist['Close'].iloc[pb_pos])
                pa = float(hist['Close'].iloc[pa_pos])
                if pb <= 0:
                    continue
                pct = abs((pa - pb) / pb * 100)
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
        }
    except Exception:
        return None


def calc_expected_move(t, S):
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
    try:
        expiries = t.options
        if not expiries:
            return None
        target_dte = get_target_dte(ticker_symbol)
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
# AUTO-SIGNALS (the v3 additions)
# ==============================================================

def get_insider_activity(t):
    """Buys vs sells last 30 days from yfinance insider_transactions."""
    try:
        ins = t.insider_transactions
        if ins is None or ins.empty:
            return {'buys': 0, 'sells': 0, 'signal': 'unknown'}
        
        # Filter to last 30 days
        if 'Start Date' in ins.columns:
            date_col = 'Start Date'
        elif 'Date' in ins.columns:
            date_col = 'Date'
        else:
            return {'buys': 0, 'sells': 0, 'signal': 'unknown'}
        
        ins[date_col] = pd.to_datetime(ins[date_col], errors='coerce')
        cutoff = pd.Timestamp.now() - pd.Timedelta(days=30)
        recent = ins[ins[date_col] >= cutoff]
        
        if recent.empty:
            return {'buys': 0, 'sells': 0, 'signal': 'neutral'}
        
        # Detect buys vs sells from Transaction column or text
        buys = 0
        sells = 0
        if 'Text' in recent.columns:
            for txt in recent['Text'].fillna(''):
                t_lower = str(txt).lower()
                if 'buy' in t_lower or 'purchase' in t_lower:
                    buys += 1
                elif 'sale' in t_lower or 'sell' in t_lower or 'sold' in t_lower:
                    sells += 1
        
        if buys > sells:
            signal = 'bullish'
        elif sells > buys * 2:
            signal = 'bearish'
        else:
            signal = 'neutral'
        
        return {'buys': buys, 'sells': sells, 'signal': signal}
    except Exception:
        return {'buys': 0, 'sells': 0, 'signal': 'unknown'}


def get_buybacks(t):
    """Detect share buybacks from cashflow last 4 quarters."""
    try:
        cf = t.quarterly_cashflow
        if cf is None or cf.empty:
            return {'amount': 0, 'signal': 'unknown'}
        
        # Look for "Repurchase Of Capital Stock" or similar
        buyback_rows = [r for r in cf.index if 'repurchase' in str(r).lower() or 'buyback' in str(r).lower()]
        if not buyback_rows:
            return {'amount': 0, 'signal': 'none'}
        
        row = buyback_rows[0]
        last_4q = cf.loc[row].head(4).fillna(0)
        # Buybacks are negative cashflow (cash leaving company)
        total = abs(float(last_4q.sum()))
        
        if total > 1e9:
            signal = 'strong'
        elif total > 1e8:
            signal = 'moderate'
        else:
            signal = 'minimal'
        
        return {'amount': total, 'signal': signal}
    except Exception:
        return {'amount': 0, 'signal': 'unknown'}


def get_eps_streak(t):
    """EPS beat/miss streak from earnings_dates."""
    try:
        eh = t.earnings_dates
        if eh is None or eh.empty:
            return {'beats': 0, 'misses': 0, 'streak': 'unknown'}
        
        # Get last 4 quarters with actual data
        now = pd.Timestamp.now(tz='UTC') if eh.index.tz else pd.Timestamp.now()
        past = eh[eh.index < now].head(4)
        
        if past.empty or 'Reported EPS' not in past.columns or 'EPS Estimate' not in past.columns:
            return {'beats': 0, 'misses': 0, 'streak': 'unknown'}
        
        beats = 0
        misses = 0
        for _, row in past.iterrows():
            actual = row.get('Reported EPS')
            est = row.get('EPS Estimate')
            if pd.notna(actual) and pd.notna(est):
                if actual > est:
                    beats += 1
                else:
                    misses += 1
        
        return {
            'beats': beats,
            'misses': misses,
            'streak': f'{beats}/{beats + misses}',
        }
    except Exception:
        return {'beats': 0, 'misses': 0, 'streak': 'unknown'}


def get_analyst_revisions(t):
    """Recent analyst upgrades/downgrades last 30d."""
    try:
        rec = t.recommendations
        if rec is None or rec.empty:
            return {'upgrades': 0, 'downgrades': 0, 'signal': 'unknown'}
        
        cutoff = pd.Timestamp.now() - pd.Timedelta(days=30)
        if hasattr(rec.index, 'tz') and rec.index.tz:
            cutoff = cutoff.tz_localize('UTC')
        
        recent = rec[rec.index >= cutoff] if hasattr(rec.index, 'tz') else rec.tail(20)
        
        ups = 0
        downs = 0
        if 'Action' in recent.columns:
            for action in recent['Action'].fillna(''):
                a = str(action).lower()
                if 'up' in a or 'positive' in a:
                    ups += 1
                elif 'down' in a or 'negative' in a:
                    downs += 1
        
        if ups > downs:
            signal = 'bullish'
        elif downs > ups:
            signal = 'bearish'
        else:
            signal = 'neutral'
        
        return {'upgrades': ups, 'downgrades': downs, 'signal': signal}
    except Exception:
        return {'upgrades': 0, 'downgrades': 0, 'signal': 'unknown'}


def check_news_red_flags(t):
    """Scan recent news headlines for red flag keywords."""
    try:
        news = t.news
        if not news:
            return {'flags': [], 'signal': 'clear'}
        
        flags_found = set()
        for article in news[:15]:
            title = article.get('title', '') if isinstance(article, dict) else ''
            content = article.get('content', {}) if isinstance(article, dict) else {}
            if isinstance(content, dict):
                title = content.get('title', title)
            
            title_lower = str(title).lower()
            for kw in RED_FLAG_KEYWORDS:
                if kw in title_lower:
                    flags_found.add(kw)
        
        return {
            'flags': list(flags_found),
            'signal': 'red_alert' if flags_found else 'clear',
        }
    except Exception:
        return {'flags': [], 'signal': 'unknown'}


def get_short_interest(t):
    """Short interest as % of float."""
    try:
        info = t.info
        si = info.get('shortPercentOfFloat')
        if si is None:
            return None
        return float(si) * 100
    except Exception:
        return None


# ==============================================================
# SCORING
# ==============================================================

def score(d):
    s = 0.0
    flags = []
    passes = []
    disqualified = False
    is_q = is_quality(d['ticker'])
    d['tier'] = 'QUALITY' if is_q else 'HUNT'
    
    # Hard filters
    if d['market_cap'] < MIN_MARKET_CAP:
        flags.append('REJECT: <$10B mkt cap')
        disqualified = True
    
    if d.get('analyst_count', 0) < 3:
        flags.append('REJECT: No analyst coverage')
        disqualified = True
    
    if d.get('days_to_earnings', 99) > 7:
        flags.append(f'REJECT: Earnings {d["days_to_earnings"]}d away')
        disqualified = True
    
    es = d.get('earnings_stats')
    em = d.get('expected_move')
    p = d.get('put_trade')
    
    if not es or not em or not p:
        flags.append('REJECT: Missing data')
        disqualified = True
    elif es['avg_move'] <= 0:
        flags.append('REJECT: No history')
        disqualified = True
    else:
        ratio = em['expected_pct'] / es['avg_move']
        edge_threshold = 1.0 if is_q else 1.5
        if ratio < edge_threshold:
            flags.append(f'REJECT: Weak edge {ratio:.1f}x')
            disqualified = True
        gap_threshold = 5 if is_q else 3
        if es['red_x_count'] >= gap_threshold:
            flags.append(f'REJECT: {es["red_x_count"]}/8 gap risk')
            disqualified = True
    
    # Red alert check — auto-skip
    rf = d.get('red_flags', {})
    if rf.get('signal') == 'red_alert':
        flags.append(f'🚨 RED ALERT: {", ".join(rf["flags"])}')
        disqualified = True
    
    if disqualified:
        return {'score': 0, 'flags': flags, 'passes': [], 'tier': d['tier']}
    
    # Soft scoring
    ratio = em['expected_pct'] / es['avg_move']
    if ratio >= 4:
        s += 4; passes.append(f'Massive edge {ratio:.1f}x')
    elif ratio >= 3:
        s += 3.5; passes.append(f'Big edge {ratio:.1f}x')
    elif ratio >= 2:
        s += 2.5; passes.append(f'Solid edge {ratio:.1f}x')
    elif ratio >= 1.5:
        s += 1.5; passes.append(f'Edge {ratio:.1f}x')
    elif ratio >= 1.0 and is_q:
        s += 1; passes.append(f'Edge {ratio:.1f}x (quality)')
    
    rx = es['red_x_count']
    if rx == 0:
        s += 2; passes.append('Zero gaps')
    elif rx == 1:
        s += 1
    elif rx == 2:
        s += 0.5
    
    if is_q:
        s += 2; passes.append('Quality whitelist')
    else:
        rec = d.get('recommendation', '').lower()
        if rec == 'strong_buy':
            s += 2; passes.append('Strong Buy')
        elif rec in ('buy', 'moderate_buy'):
            s += 1
    
    peg = d.get('peg')
    if peg and 0 < peg < 2:
        s += 1; passes.append(f'PEG {peg:.1f}')
    elif peg and peg < 3:
        s += 0.5
    
    if p['pct_otm'] >= 35 and p['oi'] >= 100:
        s += 1; passes.append(f'{p["pct_otm"]:.0f}% OTM')
    elif p['pct_otm'] >= 25:
        s += 0.5
    
    dte = d.get('days_to_earnings', 99)
    if dte <= 2:
        s += 0.5
    elif dte <= 4:
        s += 0.25
    
    # Auto-signal sentiment bonus (max 1 point)
    sentiment_score = 0
    insider = d.get('insider_activity', {})
    if insider.get('signal') == 'bullish':
        sentiment_score += 1
    elif insider.get('signal') == 'bearish':
        sentiment_score -= 1
    
    buybacks = d.get('buybacks', {})
    if buybacks.get('signal') in ('strong', 'moderate'):
        sentiment_score += 1
    
    eps = d.get('eps_streak', {})
    if eps.get('beats', 0) >= 3:
        sentiment_score += 1
    elif eps.get('misses', 0) >= 2:
        sentiment_score -= 1
    
    revisions = d.get('analyst_revisions', {})
    if revisions.get('signal') == 'bullish':
        sentiment_score += 0.5
    elif revisions.get('signal') == 'bearish':
        sentiment_score -= 0.5
    
    s += max(-1, min(1, sentiment_score * 0.3))
    
    # Sentiment label
    if sentiment_score >= 2:
        d['sentiment'] = 'BULLISH'
    elif sentiment_score >= 0:
        d['sentiment'] = 'NEUTRAL'
    else:
        d['sentiment'] = 'BEARISH'
    
    return {'score': round(s, 1), 'flags': flags, 'passes': passes, 'tier': d['tier']}


# ==============================================================
# DATA PIPELINE
# ==============================================================

def process_ticker(ticker):
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
            'debt_to_equity': info.get('debtToEquity'),
            'dividend_yield': info.get('dividendYield'),
            'recommendation': info.get('recommendationKey', 'none'),
            'target_mean': info.get('targetMeanPrice'),
            'analyst_count': info.get('numberOfAnalystOpinions', 0),
            'days_to_earnings': int(dte),
            'next_earnings': ne.strftime('%Y-%m-%d') if ne is not None else None,
            'earnings_weekday': ne.strftime('%A') if ne is not None else None,
            'earnings_timing': get_earnings_timing(t, ne),
        }
        
        if d['target_mean']:
            d['target_upside_pct'] = (d['target_mean'] - S) / S * 100
        
        print(f"  {ticker}... earnings {dte}d", flush=True)
        
        d['earnings_stats'] = calc_avg_earnings_move(t, current_earnings_date=ne)
        d['expected_move'] = calc_expected_move(t, S)
        d['put_trade'] = find_target_put(t, S, ticker)
        
        # Auto-signals
        d['insider_activity'] = get_insider_activity(t)
        d['buybacks'] = get_buybacks(t)
        d['eps_streak'] = get_eps_streak(t)
        d['analyst_revisions'] = get_analyst_revisions(t)
        d['red_flags'] = check_news_red_flags(t)
        d['short_interest'] = get_short_interest(t)
        
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
# HTML RENDERING
# ==============================================================

def fire_time_label(date_str, timing):
    """Calculate Dubai fire time. Mon BMO = Fri PM (markets closed weekend)."""
    try:
        d = datetime.strptime(date_str, '%Y-%m-%d')
        if timing == 'BMO':
            prev = d - timedelta(days=1)
            # If prev day is weekend, go back to Friday
            while prev.weekday() >= 5:  # Sat=5, Sun=6
                prev -= timedelta(days=1)
            return f"Fire: {prev.strftime('%a %b %d')} · 8-10 PM Dubai"
        elif timing == 'AMC':
            return f"Fire: {d.strftime('%a %b %d')} · 5-7 PM Dubai"
        else:
            return f"Fire: {d.strftime('%a %b %d')} · TBD timing"
    except:
        return 'Unknown'


def fund_class(value, kind):
    """Return CSS class for fundamental coloring."""
    if value is None:
        return 'fund-bad', 'N/A'
    
    if kind == 'mcap':
        if value >= 50e9: return 'fund-good', f'${value/1e9:.0f}B'
        elif value >= 10e9: return 'fund-good', f'${value/1e9:.1f}B'
        else: return 'fund-warn', f'${value/1e9:.1f}B'
    
    if kind == 'pe':
        if value < 25: return 'fund-good', f'{value:.1f}'
        elif value < 50: return 'fund-warn', f'{value:.1f}'
        else: return 'fund-bad', f'{value:.1f}'
    
    if kind == 'de':
        if value < 100: return 'fund-good', f'{value/100:.2f}'
        elif value < 300: return 'fund-warn', f'{value/100:.2f}'
        else: return 'fund-bad', f'{value/100:.2f}'
    
    if kind == 'peg':
        if value > 0 and value < 2: return 'fund-good', f'{value:.2f}'
        elif value < 3: return 'fund-warn', f'{value:.2f}'
        else: return 'fund-bad', f'{value:.2f}'
    
    return 'fund-warn', str(value)


def render_html(results, scan_date, dashboard):
    results.sort(key=lambda x: (x['score'], x['edge_ratio']), reverse=True)
    
    top_picks = [r for r in results if r['score'] >= 7]
    watch = [r for r in results if 5 <= r['score'] < 7]
    
    # Group by date
    by_date = {}
    for r in top_picks:
        date_key = r.get('next_earnings', 'TBD')
        timing = r.get('earnings_timing', 'TBD')
        if date_key not in by_date:
            by_date[date_key] = {'BMO': [], 'AMC': [], 'TBD': []}
        by_date[date_key][timing].append(r)
    
    sorted_dates = sorted([d for d in by_date.keys() if d != 'TBD'])
    
    def build_signals_box(r):
        ins = r.get('insider_activity', {})
        bb = r.get('buybacks', {})
        eps = r.get('eps_streak', {})
        rev = r.get('analyst_revisions', {})
        rf = r.get('red_flags', {})
        si = r.get('short_interest')
        
        lines = []
        # Insider
        if ins.get('signal') == 'bullish':
            lines.append(f'<div class="signal-line">🟢 Insider buying: {ins["buys"]} buys, {ins["sells"]} sells last 30d</div>')
        elif ins.get('signal') == 'bearish':
            lines.append(f'<div class="signal-line bad">⚠️ Insider selling: {ins["buys"]} buys, {ins["sells"]} sells last 30d</div>')
        elif ins.get('signal') == 'neutral':
            lines.append(f'<div class="signal-line">⚪ Insider activity: balanced</div>')
        
        # Buybacks
        if bb.get('signal') == 'strong':
            lines.append(f'<div class="signal-line">🟢 Strong buybacks: ${bb["amount"]/1e9:.1f}B last 4Q</div>')
        elif bb.get('signal') == 'moderate':
            lines.append(f'<div class="signal-line">🟢 Buybacks: ${bb["amount"]/1e9:.1f}B last 4Q</div>')
        
        # EPS streak
        if eps.get('streak') and eps['streak'] != 'unknown':
            beats = eps['beats']
            total = beats + eps['misses']
            if beats >= 3:
                lines.append(f'<div class="signal-line">🟢 EPS beat streak: {eps["streak"]} last quarters</div>')
            elif eps['misses'] >= 2:
                lines.append(f'<div class="signal-line bad">⚠️ EPS miss streak: {eps["streak"]} last quarters</div>')
            else:
                lines.append(f'<div class="signal-line">⚪ EPS mixed: {eps["streak"]} beats</div>')
        
        # Revisions
        if rev.get('signal') == 'bullish':
            lines.append(f'<div class="signal-line">🟢 Analyst upgrades: +{rev["upgrades"]}, -{rev["downgrades"]} (last 30d)</div>')
        elif rev.get('signal') == 'bearish':
            lines.append(f'<div class="signal-line bad">⚠️ Analyst downgrades: +{rev["upgrades"]}, -{rev["downgrades"]}</div>')
        
        # Red flags
        if rf.get('signal') == 'clear':
            lines.append(f'<div class="signal-line">🟢 No red flags in recent news</div>')
        
        # Short interest
        if si is not None:
            if si < 5:
                lines.append(f'<div class="signal-line">🟢 Short interest: {si:.1f}% (low)</div>')
            elif si < 15:
                lines.append(f'<div class="signal-line">⚪ Short interest: {si:.1f}%</div>')
            else:
                lines.append(f'<div class="signal-line bad">⚠️ Short interest: {si:.1f}% (high)</div>')
        
        sentiment = r.get('sentiment', 'NEUTRAL')
        sent_class = 'sent-bull' if sentiment == 'BULLISH' else 'sent-bear' if sentiment == 'BEARISH' else 'sent-neutral'
        lines.append(f'<div class="sentiment-line {sent_class}">📊 Sentiment: {sentiment}</div>')
        
        return f'<div class="signals-box"><div class="signals-header">🟢 Auto-Signals</div>{"".join(lines)}</div>'
    
    def build_pick_card(r, rank):
        pt = r.get('put_trade') or {}
        es = r.get('earnings_stats') or {}
        em = r.get('expected_move') or {}
        is_q = r.get('tier') == 'QUALITY'
        
        rank_class = '' if rank == 1 else 'r2' if rank == 2 else 'r3'
        timing = r.get('earnings_timing', 'TBD')
        timing_class = 'bmo' if timing == 'BMO' else 'amc' if timing == 'AMC' else 'tbd'
        
        # Trade str
        trade_str = '—'
        if pt:
            trade_str = f'<strong>${pt["strike"]:.0f}P</strong> {pt["expiry"][:7]} · {pt["delta"]*100:.1f}Δ · {pt["pct_otm"]:.0f}% OTM · <span class="credit">${pt["mid"]*100:.0f} credit</span>'
        
        # Fundamentals
        mcap_c, mcap_v = fund_class(r['market_cap'], 'mcap')
        pe_c, pe_v = fund_class(r.get('pe'), 'pe')
        de_c, de_v = fund_class(r.get('debt_to_equity'), 'de')
        peg_c, peg_v = fund_class(r.get('peg'), 'peg')
        
        signals_html = build_signals_box(r)
        fire_str = fire_time_label(r['next_earnings'], timing)
        
        return f"""
        <div class="pick {'quality' if is_q else 'hunt'}">
            <div class="pick-row1">
                <span class="rank {rank_class}">#{rank}</span>
                <a href="https://unusualwhales.com/stock/{r['ticker']}/earnings" target="_blank" class="pick-ticker">{r['ticker']}</a>
                <span class="pick-score">{r['score']}/10</span>
                <span class="timing-pill {timing_class}">{timing}</span>
                <span class="pick-rec">{r['recommendation'].replace('_',' ').title()}</span>
            </div>
            <div class="pick-company">{r['company']} · {r['sector']} · ${r['price']:.2f}</div>
            
            {signals_html}
            
            <div class="pick-trade">{trade_str}</div>
            <div class="pick-meta">
                <span>Edge {r['edge_ratio']}x</span>
                <span>Exp {em.get('expected_pct',0):.1f}% / Act {es.get('avg_move',0):.1f}%</span>
                <span>Red X {es.get('red_x_count','—')}/8</span>
                <span class="fire-time">⏰ {fire_str}</span>
            </div>
            <div class="pick-fundamentals">
                <span>MCap: <span class="{mcap_c}">{mcap_v}</span></span>
                <span>P/E: <span class="{pe_c}">{pe_v}</span></span>
                <span>D/E: <span class="{de_c}">{de_v}</span></span>
                <span>PEG: <span class="{peg_c}">{peg_v}</span></span>
            </div>
            <div class="manual-check">
                <strong>Manual checks:</strong> TipRanks · Morningstar · WSJ · Investors.com Pro · Stock Analysis · Unusual Whales
            </div>
        </div>
        """
    
    def build_tier_section(picks, tier_label, emoji, tier_class):
        if not picks:
            return ''
        cards = ''.join(build_pick_card(p, i+1) for i, p in enumerate(picks))
        return f"""
        <div class="tier-section">
            <div class="tier-header">
                <span class="tier-emoji">{emoji}</span>
                <span class="tier-name">{tier_label}</span>
                <span class="tier-count">{len(picks)}</span>
            </div>
            {cards}
        </div>
        """
    
    # Build day sections
    day_sections = ''
    for date in sorted_dates:
        try:
            d_obj = datetime.strptime(date, '%Y-%m-%d')
            weekday = d_obj.strftime('%A')
            date_label = d_obj.strftime('%a %b %d')
        except:
            weekday = 'TBD'
            date_label = date
        
        all_day = by_date[date]['BMO'] + by_date[date]['AMC'] + by_date[date]['TBD']
        quality_picks = [r for r in all_day if r.get('tier') == 'QUALITY']
        hunt_picks = [r for r in all_day if r.get('tier') == 'HUNT']
        
        q_section = build_tier_section(quality_picks, 'QUALITY WHEEL', '🏰', 'quality')
        h_section = build_tier_section(hunt_picks, 'PREMIUM HUNT', '🔥', 'hunt')
        
        day_sections += f"""
        <div class="day-section">
            <h2 class="day-title">📅 {weekday} — {date_label}</h2>
            <div class="day-subtitle">{len(quality_picks)} quality · {len(hunt_picks)} hunt</div>
            {q_section}
            {h_section}
        </div>
        """
    
    # Watch list
    watch_html = ''
    if watch:
        watch_cards = ''.join(build_pick_card(p, i+1) for i, p in enumerate(watch[:10]))
        watch_html = f"""
        <div class="day-section">
            <h2 class="day-title">👀 Watch List — partial fits</h2>
            <div class="day-subtitle">Score 5.0-6.9 · Manual judgment call</div>
            <div class="tier-section">{watch_cards}</div>
        </div>
        """
    
    # Dashboard
    def dash_tile(label, key, fmt='{:.2f}', suffix=''):
        d = dashboard.get(key, {})
        val = d.get('value')
        chg = d.get('change')
        if val is None:
            return f'<div class="dash-tile"><div class="dash-label">{label}</div><div class="dash-value">—</div></div>'
        chg_class = 'up' if chg and chg > 0 else 'down' if chg and chg < 0 else ''
        chg_arrow = '↑' if chg and chg > 0 else '↓' if chg and chg < 0 else ''
        chg_str = f'{chg_arrow} {abs(chg):.2f}%' if chg is not None else ''
        return f'<div class="dash-tile"><div class="dash-label">{label}</div><div class="dash-value">{fmt.format(val)}{suffix}</div><div class="dash-change {chg_class}">{chg_str}</div></div>'
    
    regime = dashboard.get('regime', 'UNKNOWN')
    regime_class = 'good' if 'CALM' in regime else 'warn' if 'NORMAL' in regime else 'bad'
    
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Premium Hunter — {scan_date}</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "Inter", sans-serif; background: #0f172a; color: #e2e8f0; padding: 32px 24px; line-height: 1.5; }}
.container {{ max-width: 920px; margin: 0 auto; }}
header {{ border-bottom: 1px solid #334155; padding-bottom: 20px; margin-bottom: 28px; }}
h1 {{ font-size: 30px; font-weight: 600; color: #f1f5f9; letter-spacing: -0.02em; }}
.subtitle {{ color: #94a3b8; font-size: 14px; margin-top: 4px; }}
.dashboard {{ background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%); border: 1px solid #334155; border-radius: 12px; padding: 20px; margin-bottom: 24px; }}
.dashboard-title {{ font-size: 12px; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 14px; display: flex; justify-content: space-between; align-items: center; }}
.regime-badge {{ padding: 3px 10px; border-radius: 6px; font-size: 11px; font-weight: 600; }}
.regime-badge.good {{ background: #064e3b; color: #6ee7b7; }}
.regime-badge.warn {{ background: #78350f; color: #fbbf24; }}
.regime-badge.bad {{ background: #7f1d1d; color: #fca5a5; }}
.dashboard-grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }}
.dash-tile {{ background: #0f172a; padding: 12px; border-radius: 8px; border: 1px solid #1e293b; }}
.dash-label {{ font-size: 10px; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 4px; }}
.dash-value {{ font-size: 18px; font-weight: 700; color: #f1f5f9; }}
.dash-change {{ font-size: 11px; margin-top: 2px; font-weight: 500; }}
.up {{ color: #34d399; }}
.down {{ color: #f87171; }}
.day-section {{ margin-bottom: 36px; background: #1e293b; border: 1px solid #334155; border-radius: 12px; padding: 22px; }}
.day-title {{ font-size: 22px; font-weight: 700; color: #f1f5f9; margin-bottom: 4px; }}
.day-subtitle {{ font-size: 12px; color: #94a3b8; margin-bottom: 18px; padding-bottom: 14px; border-bottom: 1px solid #334155; }}
.tier-section {{ margin-bottom: 22px; }}
.tier-section:last-child {{ margin-bottom: 0; }}
.tier-header {{ display: flex; align-items: center; gap: 8px; margin-bottom: 12px; }}
.tier-emoji {{ font-size: 18px; }}
.tier-name {{ font-size: 13px; font-weight: 600; color: #f1f5f9; letter-spacing: 0.02em; }}
.tier-count {{ font-size: 11px; color: #94a3b8; background: #334155; padding: 2px 8px; border-radius: 10px; }}
.pick {{ background: #0f172a; border: 1px solid #334155; border-radius: 10px; padding: 14px 16px; margin-bottom: 12px; }}
.pick.quality {{ border-left: 3px solid #3b82f6; }}
.pick.hunt {{ border-left: 3px solid #f97316; }}
.pick-row1 {{ display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 6px; }}
.rank {{ color: #fff; font-weight: 700; font-size: 11px; padding: 3px 8px; border-radius: 4px; background: #16a34a; }}
.rank.r2 {{ background: #3b82f6; }}
.rank.r3 {{ background: #a78bfa; }}
.pick-ticker {{ font-size: 18px; font-weight: 700; color: #60a5fa; text-decoration: none; }}
.pick-ticker:hover {{ text-decoration: underline; }}
.pick-score {{ font-size: 12px; color: #cbd5e1; background: #334155; padding: 2px 8px; border-radius: 4px; font-weight: 600; }}
.timing-pill {{ font-size: 10px; font-weight: 700; padding: 2px 8px; border-radius: 4px; letter-spacing: 0.05em; }}
.bmo {{ background: #fbbf24; color: #422006; }}
.amc {{ background: #8b5cf6; color: #f3e8ff; }}
.tbd {{ background: #475569; color: #cbd5e1; }}
.pick-rec {{ font-size: 11px; color: #94a3b8; margin-left: auto; }}
.pick-company {{ font-size: 12px; color: #94a3b8; margin-bottom: 12px; }}
.signals-box {{ background: #022c22; border: 1px solid #064e3b; border-radius: 8px; padding: 10px 12px; margin: 10px 0; }}
.signals-header {{ font-size: 10px; font-weight: 700; color: #34d399; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 6px; }}
.signal-line {{ font-size: 12px; color: #d1fae5; margin: 3px 0; line-height: 1.5; }}
.signal-line.bad {{ color: #fecaca; }}
.sentiment-line {{ font-size: 13px; margin-top: 8px; padding-top: 8px; border-top: 1px solid #064e3b; font-weight: 600; }}
.sent-bull {{ color: #34d399; }}
.sent-bear {{ color: #f87171; }}
.sent-neutral {{ color: #fbbf24; }}
.pick-trade {{ font-size: 14px; color: #e2e8f0; margin-bottom: 6px; background: #1e293b; padding: 10px 12px; border-radius: 6px; }}
.pick-trade strong {{ color: #f1f5f9; }}
.credit {{ color: #34d399; font-weight: 600; }}
.pick-meta {{ display: flex; gap: 12px; font-size: 11px; color: #94a3b8; flex-wrap: wrap; margin-bottom: 6px; }}
.pick-fundamentals {{ display: flex; gap: 14px; font-size: 11px; color: #94a3b8; flex-wrap: wrap; margin-bottom: 8px; padding-bottom: 8px; border-bottom: 1px dashed #1e293b; }}
.fund-good {{ color: #34d399; }}
.fund-warn {{ color: #fbbf24; }}
.fund-bad {{ color: #f87171; }}
.fire-time {{ color: #f97316; font-weight: 600; font-size: 11px; margin-left: auto; }}
.manual-check {{ background: #1e1b3b; border: 1px dashed #6366f1; border-radius: 6px; padding: 8px 12px; margin-top: 8px; font-size: 11px; color: #c7d2fe; }}
.manual-check strong {{ color: #a5b4fc; font-weight: 700; }}
.legend {{ background: #1e293b; border: 1px solid #334155; border-radius: 8px; padding: 16px; margin-top: 28px; font-size: 11px; color: #cbd5e1; line-height: 1.7; }}
.legend strong {{ color: #f1f5f9; }}
.empty {{ background: #1e293b; border: 1px dashed #475569; border-radius: 8px; padding: 32px; text-align: center; color: #94a3b8; font-size: 13px; }}
</style>
</head>
<body>
<div class="container">
    <header>
        <h1>Premium Hunter</h1>
        <div class="subtitle">{scan_date}</div>
    </header>
    
    <div class="dashboard">
        <div class="dashboard-title">
            <span>📊 MARKET DASHBOARD</span>
            <span class="regime-badge {regime_class}">{regime}</span>
        </div>
        <div class="dashboard-grid">
            {dash_tile('VIX (Fear)', 'VIX', '{:.1f}')}
            {dash_tile('SPY', 'SPY', '${:.2f}')}
            {dash_tile('10Y Yield', '10Y', '{:.2f}', '%')}
            {dash_tile('GBP/USD', 'GBPUSD', '{:.4f}')}
            {dash_tile('Brent Crude', 'BRENT', '${:.2f}')}
            {dash_tile('Gold', 'GOLD', '${:,.0f}')}
        </div>
    </div>
    
    {day_sections if day_sections else '<div class="empty">No top picks today.</div>'}
    {watch_html}
    
    <div class="legend">
        <strong>🏰 QUALITY WHEEL</strong> = stocks you'd happily own at the strike (META, MSFT, GOOGL, AMZN, V, MA, LLY...). 18-month LEAPs. Relaxed filters.<br>
        <strong>🔥 PREMIUM HUNT</strong> = pure premium plays. 9-month LEAPs (Jan'27). Strict filters.<br><br>
        <strong>BMO</strong> = Before Market Open. Fire previous US trading day Dubai 8-10 PM (Mon BMO = Fri eve fire).<br>
        <strong>AMC</strong> = After Market Close. Fire same day Dubai 5-7 PM.<br><br>
        <strong>Auto-signals</strong>: Insider activity · Buybacks · EPS streak · Analyst revisions · News red flags · Short interest. <strong>Sentiment proxy</strong> derived from these.<br>
        <strong>🚨 RED ALERT triggers</strong>: SEC investigation · fraud · lawsuit · subpoena · CEO/CFO resignation · guidance cut.<br><br>
        <strong>Manual checks (paid)</strong>: TipRanks · Morningstar · WSJ · Investors.com Pro · Stock Analysis · Unusual Whales.
    </div>
</div>
</body>
</html>"""


# ==============================================================
# MAIN
# ==============================================================

def main():
    print(f"Premium Hunter v3 — scanning {len(WATCHLIST)} tickers...")
    print(f"Looking for earnings in next {MAX_DAYS_TO_EARNINGS} days\n")
    
    print("Pulling market dashboard...")
    dashboard = get_market_dashboard()
    print(f"  VIX: {dashboard.get('VIX',{}).get('value','—')}")
    print(f"  Regime: {dashboard.get('regime','—')}\n")
    
    results = []
    for ticker in WATCHLIST:
        d = process_ticker(ticker)
        if d:
            results.append(d)
    
    print(f"\nFound {len(results)} stocks with upcoming earnings.")
    
    scan_date = datetime.now().strftime('%A, %B %d, %Y')
    html = render_html(results, scan_date, dashboard)
    
    out_path = Path('report.html')
    out_path.write_text(html)
    print(f"Report saved to: {out_path.absolute()}")
    
    json_path = Path('scan_results.json')
    json_path.write_text(json.dumps(results, default=str, indent=2))
    print(f"Raw data saved to: {json_path.absolute()}")
    
    return results


if __name__ == '__main__':
    main()
