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
    # ===== LARGE CAP ($10B+) =====
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
    'BABA', 'DPZ', 'TGT', 'LOW',
    # Energy
    'XOM', 'CVX', 'KMI', 'OXY',
    # Industrial
    'BA', 'CAT', 'GE', 'HON', 'LMT', 'CLS', 'NUE', 'AMKR',
    'AXON', 'LDOS', 'RCL', 'UAL', 'AA',
    'VZ', 'T',
    'SPY', 'QQQ', 'VOO',
    # Mid-to-large hunt names
    'PYPL', 'TSM', 'MU', 'CCJ', 'UBER',
    
    # ===== MID-CAP ($2-10B) =====
    'BBWI', 'EL',  # Consumer
    'WRBY', 'LMND', 'MARA', 'IREN',  # Premium hunt names
    'HIMS', 'ELF', 'CRBG', 'WBA',
    'HBI', 'XRX', 'MARA', 'GRAB',
    'YELP', 'ZG', 'OPEN', 'RKT',
    'AAP', 'JWN', 'KSS', 'BIRK',
    'BLDR', 'BPT', 'BRZE', 'CALX',
    'CHWY', 'CIEN', 'CLF', 'COLM',
    
    # ===== SMALL-CAP ($300M-$2B) =====
    'HNST', 'OKYO', 'SOFI', 'RIVN',  # From Ash's holdings
    'AGIO', 'AMC', 'BLNK', 'CARG',
    'CDLX', 'CGC', 'CRSR', 'DASH',
    'DOCN', 'DKNG', 'EVRI', 'FUBO',
    'GME', 'INSP', 'IONQ', 'JOBY',
    'KSCP', 'LCID', 'MGNI', 'NRG',
]


# ==============================================================
# TIER CLASSIFICATION
# ==============================================================

def classify_tier(market_cap, ticker):
    """Classify stock into LARGE/MID/SMALL based on market cap.
    Returns None if too small (<$300M)."""
    if not market_cap or market_cap < 300e6:
        return None
    if market_cap >= 10e9:
        return 'LARGE'
    if market_cap >= 2e9:
        return 'MID'
    if market_cap >= 300e6:
        return 'SMALL'
    return None


def get_tag(tier, is_quality_whitelist, is_watch=False):
    """Get tag string and CSS class for a pick.
    Returns (tag_text, css_class)."""
    if is_watch:
        return ('WL', 'wl')
    if tier == 'LARGE':
        return ('QW', 'qw') if is_quality_whitelist else ('PH', 'ph')
    if tier == 'MID':
        return ('MC', 'mc')
    if tier == 'SMALL':
        return ('SC', 'sc')
    return ('WL', 'wl')


# ==============================================================
# ECONOMIC CALENDAR (auto-generated, no API needed)
# ==============================================================

# 2026 FOMC meeting dates (Fed publishes annually)
FOMC_2026 = [
    (datetime(2026, 1, 28), 14, 0),
    (datetime(2026, 3, 18), 14, 0),
    (datetime(2026, 4, 29), 14, 0),
    (datetime(2026, 6, 17), 14, 0),
    (datetime(2026, 7, 29), 14, 0),
    (datetime(2026, 9, 16), 14, 0),
    (datetime(2026, 11, 4), 14, 0),
    (datetime(2026, 12, 16), 14, 0),
]
# 2027 FOMC (estimated - Fed publishes in autumn)
FOMC_2027 = [
    (datetime(2027, 1, 27), 14, 0),
    (datetime(2027, 3, 17), 14, 0),
    (datetime(2027, 4, 28), 14, 0),
    (datetime(2027, 6, 16), 14, 0),
    (datetime(2027, 7, 28), 14, 0),
    (datetime(2027, 9, 15), 14, 0),
    (datetime(2027, 11, 3), 14, 0),
    (datetime(2027, 12, 15), 14, 0),
]

FOMC_DATES = FOMC_2026 + FOMC_2027


def first_friday(year, month):
    """Get the first Friday of a month (NFP date)."""
    d = datetime(year, month, 1)
    while d.weekday() != 4:  # 4 = Friday
        d += timedelta(days=1)
    return d


def last_friday(year, month):
    """Get the last Friday of a month (PCE date)."""
    if month == 12:
        next_first = datetime(year + 1, 1, 1)
    else:
        next_first = datetime(year, month + 1, 1)
    last = next_first - timedelta(days=1)
    while last.weekday() != 4:
        last -= timedelta(days=1)
    return last


def get_upcoming_economic_events(days_ahead=14):
    """Generate upcoming high/medium-impact US economic events."""
    today = datetime.now()
    end_date = today + timedelta(days=days_ahead)
    events = []
    
    # FOMC announcements (HIGH)
    for fomc_date, hour, minute in FOMC_DATES:
        event_dt = fomc_date.replace(hour=hour, minute=minute)
        if today <= event_dt <= end_date:
            events.append({
                'date': event_dt,
                'name': 'FOMC Decision',
                'impact': 'HIGH',
                'icon': '🚨',
            })
    
    # NFP — first Friday of next 1-2 months at 8:30 AM ET
    for month_offset in range(2):
        if today.month + month_offset > 12:
            year = today.year + 1
            month = today.month + month_offset - 12
        else:
            year = today.year
            month = today.month + month_offset
        nfp = first_friday(year, month).replace(hour=8, minute=30)
        if today <= nfp <= end_date:
            events.append({
                'date': nfp,
                'name': 'Non-Farm Payrolls',
                'impact': 'HIGH',
                'icon': '🚨',
            })
    
    # PCE — last Friday at 8:30 AM ET (Fed's preferred inflation gauge)
    for month_offset in range(2):
        if today.month + month_offset > 12:
            year = today.year + 1
            month = today.month + month_offset - 12
        else:
            year = today.year
            month = today.month + month_offset
        pce = last_friday(year, month).replace(hour=8, minute=30)
        if today <= pce <= end_date:
            events.append({
                'date': pce,
                'name': 'Core PCE',
                'impact': 'HIGH',
                'icon': '🚨',
            })
    
    # CPI — typically 2nd Tuesday/Wednesday of month at 8:30 AM ET
    for month_offset in range(2):
        if today.month + month_offset > 12:
            year = today.year + 1
            month = today.month + month_offset - 12
        else:
            year = today.year
            month = today.month + month_offset
        # Approximate CPI date as 12th of month, find nearest Tue/Wed
        cpi_target = datetime(year, month, 12, 8, 30)
        while cpi_target.weekday() not in (1, 2):  # Tue=1, Wed=2
            cpi_target += timedelta(days=1)
        if today <= cpi_target <= end_date:
            events.append({
                'date': cpi_target,
                'name': 'CPI Inflation',
                'impact': 'HIGH',
                'icon': '🚨',
            })
    
    # Initial Jobless Claims — every Thursday at 8:30 AM ET (MEDIUM)
    d = today
    while d <= end_date:
        if d.weekday() == 3:  # Thursday
            event_dt = d.replace(hour=8, minute=30, second=0, microsecond=0)
            if event_dt > today:
                events.append({
                    'date': event_dt,
                    'name': 'Jobless Claims',
                    'impact': 'MEDIUM',
                    'icon': '⚠️',
                })
        d += timedelta(days=1)
    
    # GDP Advance — quarterly, late month of Apr/Jul/Oct/Jan at 8:30 AM ET
    gdp_months = [(2026, 4, 30), (2026, 7, 30), (2026, 10, 29), (2027, 1, 28),
                  (2027, 4, 29), (2027, 7, 29), (2027, 10, 28)]
    for y, m, d_day in gdp_months:
        gdp_dt = datetime(y, m, d_day, 8, 30)
        if today <= gdp_dt <= end_date:
            events.append({
                'date': gdp_dt,
                'name': 'GDP Advance',
                'impact': 'HIGH',
                'icon': '🚨',
            })
    
    # Sort by date
    events.sort(key=lambda e: e['date'])
    return events


def et_to_dubai(et_datetime):
    """Convert ET to Dubai time. ET is UTC-4 (EDT) or UTC-5 (EST). Dubai is UTC+4.
    For simplicity assume EDT (Mar-Nov), so Dubai = ET + 8 hours."""
    # Determine if EDT or EST (rough — DST runs ~Mar to Nov)
    month = et_datetime.month
    if 3 <= month <= 11:
        offset_hours = 8  # EDT to GST
    else:
        offset_hours = 9  # EST to GST
    return et_datetime + timedelta(hours=offset_hours)


# ==============================================================
# VIX CAUTION MODE
# ==============================================================

def get_caution_mode(vix_value):
    """Determine trading mode based on VIX level."""
    if vix_value is None:
        return {
            'mode': 'UNKNOWN',
            'message': 'VIX data unavailable — proceed cautiously.',
            'class': 'warn',
            'fire_recommendation': 'Standard sizing',
        }
    if vix_value < 16:
        return {
            'mode': 'CALM',
            'message': 'VIX low — sell premium aggressively',
            'class': 'good',
            'fire_recommendation': 'Full size, all picks valid',
        }
    if vix_value < 21:
        return {
            'mode': 'NORMAL',
            'message': 'VIX normal — standard sizing',
            'class': 'good',
            'fire_recommendation': 'Standard sizing across all picks',
        }
    if vix_value < 25:
        return {
            'mode': 'CAUTIOUS',
            'message': f'VIX {vix_value:.1f} elevated — reduce size, focus on QW only',
            'class': 'warn',
            'fire_recommendation': 'Half size · skip Premium Hunt · stick to Quality Wheel',
        }
    if vix_value < 30:
        return {
            'mode': 'STAND DOWN',
            'message': f'VIX {vix_value:.1f} HIGH — pause new puts, focus on SPY index plays',
            'class': 'bad',
            'fire_recommendation': 'STOP new picks · Manage existing · Consider SPY puts',
        }
    return {
        'mode': 'CRISIS',
        'message': f'VIX {vix_value:.1f} CRISIS — DO NOT FIRE new positions',
        'class': 'bad',
        'fire_recommendation': 'Halt all new trades · Defensive only',
    }


# ==============================================================
# SMART FIRE-TIME ADJUSTMENT
# ==============================================================

def adjust_fire_window(default_fire_dt, events):
    """Adjust fire time based on nearby high-impact events.
    Returns (adjusted_dt, warning_text)."""
    if not events:
        return default_fire_dt, None
    
    # Look for events within 4 hours of default fire time
    fire_dt_dubai = default_fire_dt
    nearby_events = []
    for ev in events:
        ev_dubai = et_to_dubai(ev['date'])
        if ev['impact'] != 'HIGH':
            continue
        time_diff_hours = (ev_dubai - fire_dt_dubai).total_seconds() / 3600
        if -4 <= time_diff_hours <= 4:
            nearby_events.append({
                'event': ev,
                'dubai_time': ev_dubai,
                'hours_offset': time_diff_hours,
            })
    
    if not nearby_events:
        return default_fire_dt, None
    
    # Find the soonest event after fire window
    upcoming = [e for e in nearby_events if e['hours_offset'] > -1]
    if not upcoming:
        return default_fire_dt, None
    
    soonest = min(upcoming, key=lambda e: e['hours_offset'])
    event = soonest['event']
    ev_dubai = soonest['dubai_time']
    
    # If event is within 2 hours after fire window, fire 1 hour BEFORE event
    if 0 <= soonest['hours_offset'] <= 2:
        adjusted = ev_dubai - timedelta(hours=1)
        warning = f"Fire 1hr before {event['name']} @ {ev_dubai.strftime('%I:%M %p')} Dubai"
        return adjusted, warning
    
    return default_fire_dt, None


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


def find_target_put(t, S, ticker_symbol, market_cap=None):
    try:
        expiries = t.options
        if not expiries:
            return None
        target_dte = get_target_dte(ticker_symbol)
        
        # Tier-specific delta target: MID/SMALL caps want deeper OTM
        cap_tier = classify_tier(market_cap, ticker_symbol) if market_cap else 'LARGE'
        if cap_tier == 'LARGE':
            target_delta = TARGET_DELTA  # -0.07 (~25-30% OTM typical)
        elif cap_tier == 'MID':
            target_delta = -0.05  # ~30-35% OTM typical
        else:  # SMALL
            target_delta = -0.04  # ~40%+ OTM typical
        
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
        puts['delta_diff'] = (puts['delta_calc'] - target_delta).abs()
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
# MID/SMALL CAP STRICTER CHECKS
# ==============================================================

def calc_altman_z_score(t):
    """Altman Z-Score for bankruptcy risk.
    Z > 3.0 = SAFE, 1.8-3.0 = GREY, < 1.8 = DANGER.
    Formula: Z = 1.2(WC/TA) + 1.4(RE/TA) + 3.3(EBIT/TA) + 0.6(MV/TL) + 1.0(S/TA)"""
    try:
        bs = t.balance_sheet
        is_ = t.income_stmt
        info = t.info
        
        if bs is None or bs.empty or is_ is None or is_.empty:
            return None
        
        # Most recent column
        bs_col = bs.columns[0]
        is_col = is_.columns[0]
        
        # Get values (with safe defaults)
        def get_val(df, col, *keys):
            for key in keys:
                if key in df.index:
                    val = df.loc[key, col]
                    if pd.notna(val):
                        return float(val)
            return None
        
        total_assets = get_val(bs, bs_col, 'Total Assets')
        current_assets = get_val(bs, bs_col, 'Current Assets', 'Total Current Assets')
        current_liab = get_val(bs, bs_col, 'Current Liabilities', 'Total Current Liabilities')
        retained_earnings = get_val(bs, bs_col, 'Retained Earnings')
        total_liab = get_val(bs, bs_col, 'Total Liabilities Net Minority Interest', 'Total Liab')
        
        ebit = get_val(is_, is_col, 'EBIT', 'Operating Income')
        revenue = get_val(is_, is_col, 'Total Revenue', 'Revenue')
        
        market_cap = info.get('marketCap')
        
        if not all([total_assets, total_liab, market_cap]):
            return None
        
        wc = (current_assets or 0) - (current_liab or 0)
        re = retained_earnings or 0
        ebit_v = ebit or 0
        rev_v = revenue or 0
        
        z = (1.2 * (wc / total_assets) +
             1.4 * (re / total_assets) +
             3.3 * (ebit_v / total_assets) +
             0.6 * (market_cap / total_liab) +
             1.0 * (rev_v / total_assets))
        
        return round(z, 2)
    except Exception:
        return None


def calc_consecutive_beats(t):
    """Count consecutive EPS beats (most recent first).
    Returns (consecutive_beats, total_in_8q, total_misses_in_8q)."""
    try:
        eh = t.earnings_dates
        if eh is None or eh.empty:
            return None
        if 'Reported EPS' not in eh.columns or 'EPS Estimate' not in eh.columns:
            return None
        
        now = pd.Timestamp.now(tz='UTC') if eh.index.tz else pd.Timestamp.now()
        past = eh[eh.index < now].head(8)
        
        if past.empty:
            return None
        
        consecutive = 0
        total_beats = 0
        total_misses = 0
        streak_broken = False
        
        for _, row in past.iterrows():
            actual = row.get('Reported EPS')
            est = row.get('EPS Estimate')
            if pd.notna(actual) and pd.notna(est):
                if actual > est:
                    total_beats += 1
                    if not streak_broken:
                        consecutive += 1
                else:
                    total_misses += 1
                    streak_broken = True
            else:
                streak_broken = True
        
        total = total_beats + total_misses
        return {
            'consecutive': consecutive,
            'beats': total_beats,
            'total': total,
            'streak_str': f'{total_beats}/{total}' if total > 0 else 'N/A',
            'consec_str': f'{consecutive} in a row' if consecutive > 0 else '0',
        }
    except Exception:
        return None


def calc_revenue_growth_yoy(t):
    """Revenue YoY growth from quarterly_financials (last quarter vs same quarter last year)."""
    try:
        qf = t.quarterly_financials
        if qf is None or qf.empty:
            return None
        
        revenue_keys = ['Total Revenue', 'Revenue', 'Operating Revenue']
        rev_row = None
        for key in revenue_keys:
            if key in qf.index:
                rev_row = qf.loc[key]
                break
        
        if rev_row is None:
            return None
        
        # Need at least 5 quarters (current + 4 quarters back for YoY)
        if len(rev_row) < 5:
            return None
        
        latest = float(rev_row.iloc[0])
        year_ago = float(rev_row.iloc[4])
        
        if year_ago <= 0:
            return None
        
        growth_pct = (latest - year_ago) / year_ago * 100
        return round(growth_pct, 1)
    except Exception:
        return None


def check_fcf_positive(t, n_quarters=4):
    """Check if Free Cash Flow has been positive for last N quarters."""
    try:
        cf = t.quarterly_cashflow
        if cf is None or cf.empty:
            return None
        
        fcf_keys = ['Free Cash Flow']
        fcf_row = None
        for key in fcf_keys:
            if key in cf.index:
                fcf_row = cf.loc[key]
                break
        
        if fcf_row is None:
            # Fallback: Operating CF - CapEx
            op_keys = ['Operating Cash Flow', 'Cash Flow From Continuing Operating Activities']
            capex_keys = ['Capital Expenditure', 'Capital Expenditures']
            op_row = None
            cx_row = None
            for k in op_keys:
                if k in cf.index:
                    op_row = cf.loc[k]
                    break
            for k in capex_keys:
                if k in cf.index:
                    cx_row = cf.loc[k]
                    break
            if op_row is None:
                return None
            if cx_row is not None:
                fcf_row = op_row + cx_row  # capex is already negative
            else:
                fcf_row = op_row
        
        recent = fcf_row.head(n_quarters).fillna(0)
        positive_count = sum(1 for v in recent if float(v) > 0)
        
        return {
            'positive_count': positive_count,
            'total': min(n_quarters, len(recent)),
            'all_positive': positive_count == min(n_quarters, len(recent)),
        }
    except Exception:
        return None


def check_share_dilution(t):
    """Check if share count has grown >5% in last 12 months (dilution)."""
    try:
        shares = t.get_shares_full()
        if shares is None or shares.empty:
            return None
        
        # Last 12 months
        cutoff = pd.Timestamp.now() - pd.Timedelta(days=365)
        if hasattr(shares.index, 'tz') and shares.index.tz:
            cutoff = cutoff.tz_localize('UTC')
        
        recent = shares[shares.index >= cutoff]
        if len(recent) < 2:
            return None
        
        oldest = float(recent.iloc[0])
        newest = float(recent.iloc[-1])
        
        if oldest <= 0:
            return None
        
        change_pct = (newest - oldest) / oldest * 100
        
        return {
            'change_pct': round(change_pct, 1),
            'is_diluted': change_pct > 5,
        }
    except Exception:
        return None


# ==============================================================
# SCORING (tiered: Large vs Mid vs Small)
# ==============================================================

def score(d):
    s = 0.0
    flags = []
    passes = []
    disqualified = False
    is_q = is_quality(d['ticker'])
    
    # Determine tier based on market cap
    cap_tier = classify_tier(d.get('market_cap', 0), d['ticker'])
    if cap_tier is None:
        flags.append('REJECT: <$300M mkt cap')
        return {'score': 0, 'flags': flags, 'passes': [], 'tier': 'NONE', 'cap_tier': None}
    
    # Tier label for display
    if cap_tier == 'LARGE':
        d['tier'] = 'QUALITY' if is_q else 'HUNT'
    elif cap_tier == 'MID':
        d['tier'] = 'MID'
    else:
        d['tier'] = 'SMALL'
    d['cap_tier'] = cap_tier
    
    # Tier-specific config
    if cap_tier == 'LARGE':
        min_analysts = 3
        min_oi = 50
    elif cap_tier == 'MID':
        min_analysts = 5
        min_oi = 200
    else:  # SMALL
        min_analysts = 7
        min_oi = 500
    
    # Hard filters
    if d.get('analyst_count', 0) < min_analysts:
        flags.append(f'REJECT: <{min_analysts} analysts ({cap_tier})')
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
        if cap_tier == 'LARGE':
            edge_threshold = 1.0 if is_q else 1.5
        elif cap_tier == 'MID':
            edge_threshold = 2.0
        else:  # SMALL
            edge_threshold = 2.5
        
        if ratio < edge_threshold:
            flags.append(f'REJECT: Weak edge {ratio:.1f}x ({cap_tier} needs {edge_threshold}x)')
            disqualified = True
        
        if cap_tier == 'LARGE':
            gap_threshold = 5 if is_q else 3
        elif cap_tier == 'MID':
            gap_threshold = 2
        else:  # SMALL
            gap_threshold = 2
        
        if es['red_x_count'] >= gap_threshold:
            flags.append(f'REJECT: {es["red_x_count"]}/8 gap risk ({cap_tier})')
            disqualified = True
    
    # OI minimum check
    if p and p.get('oi', 0) < min_oi:
        flags.append(f'REJECT: OI {p.get("oi",0)} < {min_oi} ({cap_tier})')
        disqualified = True
    
    # ===== MID/SMALL CAP STRICTER FILTERS =====
    if cap_tier in ('MID', 'SMALL') and not disqualified:
        # Altman Z-Score (bankruptcy)
        z = d.get('altman_z')
        z_threshold = 3.0 if cap_tier == 'MID' else 3.5
        if z is None:
            flags.append(f'REJECT: Z-Score unavailable ({cap_tier} requires)')
            disqualified = True
        elif z < z_threshold:
            flags.append(f'REJECT: Z-Score {z} < {z_threshold} ({cap_tier} bankruptcy risk)')
            disqualified = True
        
        # Earnings beats streak
        beats_data = d.get('beats_streak')
        if beats_data:
            if cap_tier == 'MID':
                # 6/8 OR 4 in a row
                ok = beats_data['beats'] >= 6 or beats_data['consecutive'] >= 4
                if not ok:
                    flags.append(f'REJECT: Beats {beats_data["streak_str"]} ({beats_data["consec_str"]}) - MID needs 6/8 or 4 streak')
                    disqualified = True
            else:  # SMALL
                # 7/8 OR 6 in a row
                ok = beats_data['beats'] >= 7 or beats_data['consecutive'] >= 6
                if not ok:
                    flags.append(f'REJECT: Beats {beats_data["streak_str"]} ({beats_data["consec_str"]}) - SMALL needs 7/8 or 6 streak')
                    disqualified = True
        else:
            flags.append(f'REJECT: Beats data unavailable')
            disqualified = True
        
        # Revenue growth YoY
        rev_growth = d.get('revenue_growth')
        rev_threshold = 5 if cap_tier == 'MID' else 10
        if rev_growth is None:
            flags.append(f'REJECT: Revenue growth unavailable')
            disqualified = True
        elif rev_growth < rev_threshold:
            flags.append(f'REJECT: Rev growth {rev_growth}% < {rev_threshold}% ({cap_tier})')
            disqualified = True
        
        # FCF positive
        fcf = d.get('fcf_check')
        if fcf and not fcf.get('all_positive'):
            flags.append(f'REJECT: FCF only {fcf["positive_count"]}/{fcf["total"]} positive')
            disqualified = True
        elif fcf is None:
            flags.append(f'REJECT: FCF data unavailable')
            disqualified = True
        
        # Dilution check
        dil = d.get('dilution_check')
        if dil and dil.get('is_diluted'):
            flags.append(f'REJECT: Diluted {dil["change_pct"]}% in 12mo')
            disqualified = True
        
        # SMALL cap requires insider buying
        if cap_tier == 'SMALL':
            insider = d.get('insider_activity', {})
            if insider.get('signal') != 'bullish':
                flags.append(f'REJECT: SMALL cap requires insider buying signal')
                disqualified = True
    
    # Red alert check — auto-skip
    rf = d.get('red_flags', {})
    if rf.get('signal') == 'red_alert':
        flags.append(f'🚨 RED ALERT: {", ".join(rf["flags"])}')
        disqualified = True
    
    if disqualified:
        return {'score': 0, 'flags': flags, 'passes': [], 'tier': d['tier'], 'cap_tier': cap_tier}
    
    # ===== SOFT SCORING =====
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
    
    # Tier-specific bonus
    if cap_tier == 'MID':
        beats_data = d.get('beats_streak')
        if beats_data and beats_data['beats'] >= 7:
            s += 1; passes.append(f'Mid-cap beats {beats_data["streak_str"]}')
        z = d.get('altman_z')
        if z and z >= 5:
            s += 1; passes.append(f'Z-Score {z} (fortress)')
    elif cap_tier == 'SMALL':
        beats_data = d.get('beats_streak')
        if beats_data and beats_data['consecutive'] >= 8:
            s += 1.5; passes.append('All 8 quarter beat streak')
        z = d.get('altman_z')
        if z and z >= 6:
            s += 1.5; passes.append(f'Z-Score {z} (rock solid)')
    
    peg = d.get('peg')
    if peg and 0 < peg < 2:
        s += 1; passes.append(f'PEG {peg:.1f}')
    elif peg and peg < 3:
        s += 0.5
    
    # OTM bonus (tier-specific thresholds)
    if cap_tier == 'LARGE':
        otm_target = 35
    elif cap_tier == 'MID':
        otm_target = 30
    else:  # SMALL
        otm_target = 40
    
    if p['pct_otm'] >= otm_target and p['oi'] >= min_oi:
        s += 1; passes.append(f'{p["pct_otm"]:.0f}% OTM')
    elif p['pct_otm'] >= otm_target - 10:
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
    
    if sentiment_score >= 2:
        d['sentiment'] = 'BULLISH'
    elif sentiment_score >= 0:
        d['sentiment'] = 'NEUTRAL'
    else:
        d['sentiment'] = 'BEARISH'
    
    return {'score': round(s, 1), 'flags': flags, 'passes': passes, 'tier': d['tier'], 'cap_tier': cap_tier}


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
        d['put_trade'] = find_target_put(t, S, ticker, d['market_cap'])
        
        # Auto-signals
        d['insider_activity'] = get_insider_activity(t)
        d['buybacks'] = get_buybacks(t)
        d['eps_streak'] = get_eps_streak(t)
        d['analyst_revisions'] = get_analyst_revisions(t)
        d['red_flags'] = check_news_red_flags(t)
        d['short_interest'] = get_short_interest(t)
        
        # Mid/Small cap stricter checks (only run if MID or SMALL tier)
        cap_tier_check = classify_tier(d.get('market_cap', 0), ticker)
        if cap_tier_check in ('MID', 'SMALL'):
            d['altman_z'] = calc_altman_z_score(t)
            d['beats_streak'] = calc_consecutive_beats(t)
            d['revenue_growth'] = calc_revenue_growth_yoy(t)
            d['fcf_check'] = check_fcf_positive(t, n_quarters=4)
            d['dilution_check'] = check_share_dilution(t)
        else:
            d['altman_z'] = None
            d['beats_streak'] = None
            d['revenue_growth'] = None
            d['fcf_check'] = None
            d['dilution_check'] = None
        
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


def render_html(results, scan_date, dashboard, economic_events, caution):
    results.sort(key=lambda x: (x['score'], x['edge_ratio']), reverse=True)
    
    top_picks = [r for r in results if r['score'] >= 7]
    watch = [r for r in results if 5 <= r['score'] < 7]
    
    # Group all picks (top + watch) by date for date-first layout
    all_picks = [(r, 'top') for r in top_picks] + [(r, 'watch') for r in watch]
    by_date = {}
    for r, src in all_picks:
        date_key = r.get('next_earnings', 'TBD')
        if date_key not in by_date:
            by_date[date_key] = []
        by_date[date_key].append((r, src))
    
    sorted_dates = sorted([d for d in by_date.keys() if d != 'TBD'])
    
    def get_tag(r, src):
        if src == 'watch':
            return ('WL', 'wl')
        cap_tier = r.get('cap_tier', 'LARGE')
        if cap_tier == 'MID':
            return ('MC', 'mc')
        if cap_tier == 'SMALL':
            return ('SC', 'sc')
        # LARGE cap
        if r.get('tier') == 'QUALITY':
            return ('QW', 'qw')
        return ('PH', 'ph')
    
    def get_default_fire_dt(date_str, timing):
        """Get default fire datetime in Dubai time."""
        try:
            d = datetime.strptime(date_str, '%Y-%m-%d')
            if timing == 'BMO':
                prev = d - timedelta(days=1)
                while prev.weekday() >= 5:
                    prev -= timedelta(days=1)
                return prev.replace(hour=21, minute=0)  # 9 PM Dubai
            elif timing == 'AMC':
                return d.replace(hour=18, minute=0)  # 6 PM Dubai
            else:
                return d.replace(hour=18, minute=0)
        except:
            return None
    
    def fire_time_str(r, default_fire_dt, adjusted_fire_dt, warning):
        """Build fire time display string."""
        if not default_fire_dt:
            return "Fire timing TBD"
        
        if adjusted_fire_dt and adjusted_fire_dt != default_fire_dt:
            return (f"⏰ {adjusted_fire_dt.strftime('%a %b %d %I:%M %p')} Dubai "
                    f"(adjusted from {default_fire_dt.strftime('%I:%M %p')})")
        
        if r.get('earnings_timing') == 'BMO':
            return f"⏰ Fire {default_fire_dt.strftime('%a %b %d')} 8-10 PM Dubai"
        elif r.get('earnings_timing') == 'AMC':
            return f"⏰ Fire {default_fire_dt.strftime('%a %b %d')} 5-7 PM Dubai"
        else:
            return f"⏰ Fire {default_fire_dt.strftime('%a %b %d')} TBD"
    
    def build_pick_row(r, src):
        pt = r.get('put_trade') or {}
        es = r.get('earnings_stats') or {}
        em = r.get('expected_move') or {}
        
        tag, tag_class = get_tag(r, src)
        timing = r.get('earnings_timing', 'TBD')
        timing_class = 'bmo' if timing == 'BMO' else 'amc' if timing == 'AMC' else 'tbd'
        
        # Smart fire time
        default_fire = get_default_fire_dt(r['next_earnings'], timing)
        adjusted_fire, fire_warning = (default_fire, None)
        if default_fire:
            adjusted_fire, fire_warning = adjust_fire_window(default_fire, economic_events)
        fire_str = fire_time_str(r, default_fire, adjusted_fire, fire_warning)
        
        # Trade
        trade_str = '—'
        if pt:
            trade_str = (f'<strong>${pt["strike"]:.0f}P</strong> {pt["expiry"][:7]} · '
                         f'{pt["delta"]*100:.1f}Δ · {pt["pct_otm"]:.0f}% OTM · '
                         f'<span class="credit">${pt["mid"]*100:.0f} credit</span>')
        
        # Fundamentals
        mcap_c, mcap_v = fund_class(r['market_cap'], 'mcap')
        pe_c, pe_v = fund_class(r.get('pe'), 'pe')
        de_c, de_v = fund_class(r.get('debt_to_equity'), 'de')
        peg_c, peg_v = fund_class(r.get('peg'), 'peg')
        
        # Auto-signals (compact inline)
        ins = r.get('insider_activity', {})
        bb = r.get('buybacks', {})
        eps = r.get('eps_streak', {})
        rev = r.get('analyst_revisions', {})
        rf = r.get('red_flags', {})
        si = r.get('short_interest')
        
        sig_parts = []
        if ins.get('signal') == 'bullish':
            sig_parts.append(f'<span class="sig-good">🟢 Insider buys {ins["buys"]}</span>')
        elif ins.get('signal') == 'bearish':
            sig_parts.append(f'<span class="sig-bad">⚠️ Insider sells {ins["sells"]}</span>')
        
        if bb.get('signal') in ('strong', 'moderate') and bb.get('amount'):
            sig_parts.append(f'<span class="sig-good">🟢 Buybacks ${bb["amount"]/1e9:.1f}B</span>')
        
        if eps.get('beats', 0) >= 3:
            sig_parts.append(f'<span class="sig-good">🟢 EPS {eps["streak"]}</span>')
        elif eps.get('misses', 0) >= 2:
            sig_parts.append(f'<span class="sig-bad">⚠️ EPS misses {eps["streak"]}</span>')
        
        if rev.get('signal') == 'bullish':
            sig_parts.append(f'<span class="sig-good">🟢 Upgrades +{rev["upgrades"]}</span>')
        elif rev.get('signal') == 'bearish':
            sig_parts.append(f'<span class="sig-bad">⚠️ Downgrades -{rev["downgrades"]}</span>')
        
        if rf.get('signal') == 'clear':
            sig_parts.append('<span class="sig-good">🟢 No red flags</span>')
        
        signals_html = ' · '.join(sig_parts) if sig_parts else '⚪ Limited data'
        
        # Strict checks row (MID/SMALL caps only)
        strict_checks_html = ''
        cap_tier = r.get('cap_tier')
        if cap_tier in ('MID', 'SMALL'):
            check_parts = []
            z = r.get('altman_z')
            if z is not None:
                z_class = 'sig-good' if z >= 3 else 'sig-bad'
                check_parts.append(f'<span class="{z_class}">Z-Score {z}</span>')
            
            bs = r.get('beats_streak')
            if bs:
                star = ' ⭐' if bs.get('beats', 0) >= 7 else ''
                check_parts.append(f'<span class="sig-good">Beats {bs["streak_str"]}{star}</span>')
            
            rg = r.get('revenue_growth')
            if rg is not None:
                rg_class = 'sig-good' if rg > 0 else 'sig-bad'
                check_parts.append(f'<span class="{rg_class}">Rev YoY {rg:+.1f}%</span>')
            
            fcf = r.get('fcf_check')
            if fcf:
                fcf_class = 'sig-good' if fcf['all_positive'] else 'sig-bad'
                check_parts.append(f'<span class="{fcf_class}">FCF {fcf["positive_count"]}/{fcf["total"]}</span>')
            
            dil = r.get('dilution_check')
            if dil and not dil.get('is_diluted'):
                check_parts.append(f'<span class="sig-good">Shares stable {dil["change_pct"]:+.1f}%</span>')
            
            if check_parts:
                strict_checks_html = f'<div class="strict-checks">⚙️ {" · ".join(check_parts)}</div>'
        
        sentiment = r.get('sentiment', 'NEUTRAL')
        sent_class = 'sent-bull' if sentiment == 'BULLISH' else 'sent-bear' if sentiment == 'BEARISH' else 'sent-neutral'
        
        warning_html = ''
        if fire_warning:
            warning_html = f'<div class="fire-warning">⚠️ {fire_warning}</div>'
        
        return f"""
        <div class="pick">
            <div class="tag {tag_class}">{tag}</div>
            <div class="pick-body">
                <div class="pick-row1">
                    <a href="https://unusualwhales.com/stock/{r['ticker']}/earnings" target="_blank" class="pick-ticker">{r['ticker']}</a>
                    <span class="pick-score">{r['score']}/10</span>
                    <span class="timing-pill {timing_class}">{timing}</span>
                    <span class="pick-rec">{r['recommendation'].replace('_',' ').title()} · {r['company']} · ${r['price']:.2f}</span>
                </div>
                <div class="pick-trade">{trade_str}</div>
                <div class="pick-meta">
                    <span>Edge {r['edge_ratio']}x</span>
                    <span>Exp {em.get('expected_pct',0):.1f}% / Act {es.get('avg_move',0):.1f}%</span>
                    <span>Red X {es.get('red_x_count','—')}/8</span>
                </div>
                <div class="pick-fundamentals">
                    <span>MCap <span class="{mcap_c}">{mcap_v}</span></span>
                    <span>P/E <span class="{pe_c}">{pe_v}</span></span>
                    <span>D/E <span class="{de_c}">{de_v}</span></span>
                    <span>PEG <span class="{peg_c}">{peg_v}</span></span>
                </div>
                {strict_checks_html}
                <div class="signals-inline">{signals_html}</div>
                <div class="pick-bottom">
                    <span class="sentiment {sent_class}">📊 {sentiment}</span>
                    <span class="fire-time">{fire_str}</span>
                </div>
                {warning_html}
                <div class="manual-check">
                    <strong>Verify:</strong> TipRanks · Morningstar · WSJ · Investors.com Pro · Stock Analysis · UW
                </div>
            </div>
        </div>
        """
    
    # Day sections
    day_sections = ''
    for date in sorted_dates:
        try:
            d_obj = datetime.strptime(date, '%Y-%m-%d')
            weekday = d_obj.strftime('%A')
            date_label = d_obj.strftime('%a %b %d')
        except:
            weekday = 'TBD'
            date_label = date
        
        day_picks = by_date[date]
        # Sort within day: QW first, then PH, then WL; by score desc
        def sort_key(item):
            r, src = item
            tier_order = 0 if (src == 'top' and r.get('tier') == 'QUALITY') else 1 if src == 'top' else 2
            return (tier_order, -r['score'])
        day_picks.sort(key=sort_key)
        
        qw_count = sum(1 for r, s in day_picks if s == 'top' and r.get('cap_tier') == 'LARGE' and r.get('tier') == 'QUALITY')
        ph_count = sum(1 for r, s in day_picks if s == 'top' and r.get('cap_tier') == 'LARGE' and r.get('tier') == 'HUNT')
        mc_count = sum(1 for r, s in day_picks if s == 'top' and r.get('cap_tier') == 'MID')
        sc_count = sum(1 for r, s in day_picks if s == 'top' and r.get('cap_tier') == 'SMALL')
        wl_count = sum(1 for r, s in day_picks if s == 'watch')
        
        cards = ''.join(build_pick_row(r, s) for r, s in day_picks)
        
        day_sections += f"""
        <div class="day-section">
            <div class="day-header">
                <div class="day-title">📅 {weekday} — {date_label}</div>
                <div class="day-summary">
                    <span>{qw_count} QW</span><span>{ph_count} PH</span><span>{mc_count} MC</span><span>{sc_count} SC</span><span>{wl_count} WL</span>
                </div>
            </div>
            {cards}
        </div>
        """
    
    if not day_sections:
        day_sections = '<div class="empty">No picks meet criteria today. Quiet markets or all setups failing filters.</div>'
    
    # Economic events strip
    events_html = ''
    if economic_events:
        rows = []
        for ev in economic_events[:8]:
            ev_dubai = et_to_dubai(ev['date'])
            impact_class = 'ev-high' if ev['impact'] == 'HIGH' else 'ev-med'
            rows.append(f"""
                <div class="ev-row {impact_class}">
                    <span class="ev-icon">{ev['icon']}</span>
                    <span class="ev-day">{ev_dubai.strftime('%a %b %d')}</span>
                    <span class="ev-time">{ev_dubai.strftime('%I:%M %p')} Dubai</span>
                    <span class="ev-name">{ev['name']}</span>
                    <span class="ev-impact">{ev['impact']}</span>
                </div>
            """)
        events_html = f"""
        <div class="events-strip">
            <div class="events-title">📅 THIS WEEK — Major Economic Events (Dubai time)</div>
            <div class="events-list">{''.join(rows)}</div>
        </div>
        """
    
    # Dashboard tiles
    def dash_tile(label, key, fmt='{:.2f}', suffix=''):
        d = dashboard.get(key, {})
        val = d.get('value')
        chg = d.get('change')
        if val is None:
            return f'<div class="dash-item"><span class="dash-label">{label}</span><span class="dash-value">—</span></div>'
        chg_class = 'up' if chg and chg > 0 else 'down' if chg and chg < 0 else ''
        chg_arrow = '↑' if chg and chg > 0 else '↓' if chg and chg < 0 else ''
        chg_str = f'{chg_arrow}{abs(chg):.2f}%' if chg is not None else ''
        return f'<div class="dash-item"><span class="dash-label">{label}</span><span class="dash-value">{fmt.format(val)}{suffix}</span><span class="dash-change {chg_class}">{chg_str}</span></div>'
    
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Premium Hunter — {scan_date}</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "Inter", sans-serif; background: #0f172a; color: #e2e8f0; padding: 28px 22px; line-height: 1.45; }}
.container {{ max-width: 920px; margin: 0 auto; }}
header {{ border-bottom: 1px solid #334155; padding-bottom: 14px; margin-bottom: 16px; }}
h1 {{ font-size: 26px; font-weight: 600; color: #f1f5f9; letter-spacing: -0.02em; }}
.subtitle {{ color: #94a3b8; font-size: 13px; margin-top: 4px; }}

/* Caution Banner */
.caution-banner {{ padding: 14px 18px; border-radius: 8px; margin-bottom: 18px; font-weight: 600; font-size: 14px; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px; }}
.caution-banner.good {{ background: #064e3b; color: #6ee7b7; border: 1px solid #10b981; }}
.caution-banner.warn {{ background: #78350f; color: #fbbf24; border: 1px solid #f59e0b; }}
.caution-banner.bad {{ background: #7f1d1d; color: #fca5a5; border: 1px solid #ef4444; animation: pulse 2s ease-in-out infinite; }}
@keyframes pulse {{ 0%, 100% {{ opacity: 1; }} 50% {{ opacity: 0.85; }} }}
.caution-mode {{ font-size: 16px; font-weight: 700; letter-spacing: 0.05em; }}
.caution-rec {{ font-size: 12px; opacity: 0.9; font-weight: 500; }}

/* Compact Dashboard */
.dashboard {{ background: #1e293b; border: 1px solid #334155; border-radius: 8px; padding: 12px 16px; margin-bottom: 14px; display: flex; align-items: center; gap: 16px; flex-wrap: wrap; font-size: 12px; }}
.dash-item {{ display: flex; align-items: baseline; gap: 5px; }}
.dash-label {{ color: #94a3b8; font-size: 10px; text-transform: uppercase; letter-spacing: 0.05em; }}
.dash-value {{ color: #f1f5f9; font-weight: 700; font-size: 13px; }}
.dash-change {{ font-size: 10px; font-weight: 500; }}
.up {{ color: #34d399; }}
.down {{ color: #f87171; }}

/* Economic Events Strip */
.events-strip {{ background: #1e293b; border: 1px solid #334155; border-radius: 8px; padding: 12px 16px; margin-bottom: 22px; }}
.events-title {{ font-size: 11px; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 8px; font-weight: 600; }}
.events-list {{ display: flex; flex-direction: column; gap: 4px; }}
.ev-row {{ display: flex; align-items: center; gap: 10px; padding: 5px 8px; border-radius: 5px; font-size: 12px; }}
.ev-row.ev-high {{ background: #450a0a; }}
.ev-row.ev-med {{ background: #1e293b; }}
.ev-icon {{ font-size: 12px; }}
.ev-day {{ color: #94a3b8; font-weight: 500; min-width: 95px; font-size: 11px; }}
.ev-time {{ color: #cbd5e1; min-width: 100px; font-size: 11px; font-weight: 500; }}
.ev-name {{ color: #f1f5f9; flex: 1; font-size: 12px; }}
.ev-impact {{ font-size: 10px; font-weight: 700; padding: 2px 7px; border-radius: 3px; }}
.ev-high .ev-impact {{ background: #7f1d1d; color: #fecaca; }}
.ev-med .ev-impact {{ background: #78350f; color: #fed7aa; }}

/* Day Section */
.day-section {{ margin-bottom: 18px; background: #1e293b; border: 1px solid #334155; border-radius: 10px; padding: 14px 16px; }}
.day-header {{ display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; padding-bottom: 8px; border-bottom: 1px solid #334155; }}
.day-title {{ font-size: 17px; font-weight: 700; color: #f1f5f9; }}
.day-summary {{ font-size: 11px; color: #94a3b8; }}
.day-summary span {{ margin-left: 8px; }}

/* Pick Row — compact horizontal */
.pick {{ background: #0f172a; border: 1px solid #1e293b; border-radius: 8px; padding: 10px 12px; margin-bottom: 8px; display: flex; gap: 12px; }}
.pick:last-child {{ margin-bottom: 0; }}
.tag {{ flex-shrink: 0; width: 36px; display: flex; flex-direction: column; align-items: center; justify-content: center; border-radius: 6px; padding: 6px 0; font-weight: 700; font-size: 12px; letter-spacing: 0.05em; }}
.tag.qw {{ background: #1e3a8a; color: #dbeafe; border: 1px solid #3b82f6; }}
.tag.ph {{ background: #7c2d12; color: #fed7aa; border: 1px solid #f97316; }}
.tag.mc {{ background: #4c1d95; color: #ddd6fe; border: 1px solid #8b5cf6; }}
.tag.sc {{ background: #713f12; color: #fde68a; border: 1px solid #ca8a04; }}
.tag.wl {{ background: #334155; color: #cbd5e1; border: 1px solid #64748b; }}
.pick-body {{ flex: 1; min-width: 0; }}
.pick-row1 {{ display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 4px; }}
.pick-ticker {{ font-size: 16px; font-weight: 700; color: #60a5fa; text-decoration: none; }}
.pick-ticker:hover {{ text-decoration: underline; }}
.pick-score {{ font-size: 11px; color: #cbd5e1; background: #334155; padding: 2px 7px; border-radius: 4px; font-weight: 600; }}
.timing-pill {{ font-size: 9px; font-weight: 700; padding: 2px 6px; border-radius: 3px; letter-spacing: 0.05em; }}
.bmo {{ background: #fbbf24; color: #422006; }}
.amc {{ background: #8b5cf6; color: #f3e8ff; }}
.tbd {{ background: #475569; color: #cbd5e1; }}
.pick-rec {{ font-size: 10px; color: #94a3b8; }}
.pick-trade {{ font-size: 13px; color: #e2e8f0; background: #1e293b; padding: 6px 10px; border-radius: 5px; margin-bottom: 6px; }}
.pick-trade strong {{ color: #f1f5f9; }}
.credit {{ color: #34d399; font-weight: 600; }}
.pick-meta {{ display: flex; gap: 12px; font-size: 10px; color: #94a3b8; flex-wrap: wrap; margin-bottom: 4px; }}
.pick-fundamentals {{ display: flex; gap: 12px; font-size: 10px; color: #94a3b8; flex-wrap: wrap; margin-bottom: 6px; }}
.strict-checks {{ font-size: 10px; color: #94a3b8; margin-bottom: 6px; padding: 5px 8px; background: rgba(139, 92, 246, 0.1); border-radius: 4px; border-left: 2px solid #8b5cf6; }}
.fund-good {{ color: #34d399; }}
.fund-warn {{ color: #fbbf24; }}
.fund-bad {{ color: #f87171; }}
.signals-inline {{ font-size: 10px; color: #94a3b8; margin-bottom: 6px; line-height: 1.6; }}
.sig-good {{ color: #34d399; }}
.sig-bad {{ color: #f87171; }}
.pick-bottom {{ display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px; }}
.sentiment {{ font-size: 10px; font-weight: 700; padding: 2px 7px; border-radius: 3px; }}
.sent-bull {{ background: #064e3b; color: #6ee7b7; }}
.sent-neutral {{ background: #78350f; color: #fbbf24; }}
.sent-bear {{ background: #7f1d1d; color: #fca5a5; }}
.fire-time {{ color: #f97316; font-weight: 600; font-size: 10px; }}
.fire-warning {{ background: #78350f; border: 1px solid #f59e0b; color: #fbbf24; padding: 4px 8px; border-radius: 4px; margin-top: 4px; font-size: 10px; font-weight: 600; }}
.manual-check {{ font-size: 10px; color: #818cf8; margin-top: 4px; padding-top: 4px; border-top: 1px dashed #1e293b; }}
.manual-check strong {{ color: #a5b4fc; }}
.empty {{ background: #1e293b; border: 1px dashed #475569; border-radius: 8px; padding: 32px; text-align: center; color: #94a3b8; font-size: 13px; }}
.legend {{ background: #1e293b; border: 1px solid #334155; border-radius: 8px; padding: 14px; margin-top: 18px; font-size: 11px; color: #cbd5e1; line-height: 1.6; }}
.legend strong {{ color: #f1f5f9; }}
.legend-tag {{ font-weight: 700; padding: 1px 6px; border-radius: 3px; font-size: 10px; }}
.legend-tag.qw {{ background: #1e3a8a; color: #dbeafe; }}
.legend-tag.ph {{ background: #7c2d12; color: #fed7aa; }}
.legend-tag.mc {{ background: #4c1d95; color: #ddd6fe; }}
.legend-tag.sc {{ background: #713f12; color: #fde68a; }}
.legend-tag.wl {{ background: #334155; color: #cbd5e1; }}
</style>
</head>
<body>
<div class="container">
    <header>
        <h1>Premium Hunter</h1>
        <div class="subtitle">{scan_date}</div>
    </header>
    
    <div class="caution-banner {caution['class']}">
        <span class="caution-mode">🚦 {caution['mode']}</span>
        <span class="caution-rec">{caution['fire_recommendation']}</span>
    </div>
    
    <div class="dashboard">
        {dash_tile('VIX', 'VIX', '{:.1f}')}
        {dash_tile('SPY', 'SPY', '${:.2f}')}
        {dash_tile('10Y', '10Y', '{:.2f}', '%')}
        {dash_tile('GBP/USD', 'GBPUSD', '{:.4f}')}
        {dash_tile('Brent', 'BRENT', '${:.2f}')}
        {dash_tile('Gold', 'GOLD', '${:,.0f}')}
    </div>
    
    {events_html}
    {day_sections}
    
    <div class="legend">
        <strong>Tags:</strong>
        <span class="legend-tag qw">QW</span> Quality Wheel ($10B+, whitelist) · 
        <span class="legend-tag ph">PH</span> Premium Hunt ($10B+) · 
        <span class="legend-tag mc">MC</span> Mid-Cap ($2-10B, strict) · 
        <span class="legend-tag sc">SC</span> Small-Cap ($300M-2B, strictest) · 
        <span class="legend-tag wl">WL</span> Watch List<br><br>
        <strong>VIX bands:</strong> &lt;16 calm · 16-21 normal · 21-25 cautious · 25-30 stand down · &gt;30 crisis<br>
        <strong>BMO</strong> = Before Open. <strong>AMC</strong> = After Close. Smart fire-time auto-shifts to avoid major economic events.<br>
        <strong>Manual checks:</strong> TipRanks · Morningstar · WSJ · Investors.com Pro · Stock Analysis · Unusual Whales
    </div>
</div>
</body>
</html>"""


# ==============================================================
# MAIN
# ==============================================================

def main():
    print(f"Premium Hunter v6 — scanning {len(WATCHLIST)} tickers (Large + Mid + Small caps)...")
    print(f"Looking for earnings in next {MAX_DAYS_TO_EARNINGS} days\n")
    
    print("Pulling market dashboard...")
    dashboard = get_market_dashboard()
    vix_val = dashboard.get('VIX', {}).get('value')
    print(f"  VIX: {vix_val}")
    
    print("Generating economic calendar...")
    economic_events = get_upcoming_economic_events(days_ahead=14)
    print(f"  Found {len(economic_events)} upcoming events")
    for ev in economic_events[:5]:
        print(f"    {ev['date'].strftime('%a %b %d %H:%M ET')} - {ev['name']} [{ev['impact']}]")
    
    caution = get_caution_mode(vix_val)
    print(f"\n  Mode: {caution['mode']} — {caution['fire_recommendation']}\n")
    
    results = []
    for ticker in WATCHLIST:
        d = process_ticker(ticker)
        if d:
            results.append(d)
    
    print(f"\nFound {len(results)} stocks with upcoming earnings.")
    
    scan_date = datetime.now().strftime('%A, %B %d, %Y')
    html = render_html(results, scan_date, dashboard, economic_events, caution)
    
    out_path = Path('report.html')
    out_path.write_text(html)
    print(f"Report saved to: {out_path.absolute()}")
    
    json_path = Path('scan_results.json')
    json_path.write_text(json.dumps(results, default=str, indent=2))
    print(f"Raw data saved to: {json_path.absolute()}")
    
    return results


if __name__ == '__main__':
    main()
