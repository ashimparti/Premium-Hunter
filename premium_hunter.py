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

from claude_scorer import score_picks

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
# TECHNICAL ANALYSIS (1Y chart, RSI, DMA, Bollinger, ATR, IV Rank, Support, Tariff anchor)
# ==============================================================

# Tariff crash benchmark date - used as stress floor for all stocks
TARIFF_CRASH_START = pd.Timestamp('2025-04-01')
TARIFF_CRASH_END = pd.Timestamp('2025-05-15')


def fetch_1y_history(ticker_obj, ticker_str):
    """Get 1y daily price history. Returns DataFrame or None."""
    try:
        hist = ticker_obj.history(period='1y', auto_adjust=True)
        if hist.empty or len(hist) < 50:
            return None
        return hist
    except Exception as e:
        print(f"    ⚠️ {ticker_str} 1y history fetch failed: {e}")
        return None


def calc_rsi(prices, period=14):
    """14-day RSI. Returns single float."""
    try:
        delta = prices.diff()
        gain = delta.where(delta > 0, 0).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        val = float(rsi.iloc[-1])
        return val if not pd.isna(val) else None
    except Exception:
        return None


def calc_dma(prices, period):
    """Moving average. Returns single float (last value)."""
    try:
        ma = prices.rolling(window=period).mean()
        val = float(ma.iloc[-1])
        return val if not pd.isna(val) else None
    except Exception:
        return None


def calc_bollinger_position(prices, period=20, num_std=2):
    """Where current price sits in Bollinger bands. Returns 0-1 (0=lower, 0.5=mid, 1=upper) or None."""
    try:
        ma = prices.rolling(window=period).mean()
        std = prices.rolling(window=period).std()
        upper = ma + num_std * std
        lower = ma - num_std * std
        current = float(prices.iloc[-1])
        u = float(upper.iloc[-1])
        l = float(lower.iloc[-1])
        if u <= l:
            return None
        pos = (current - l) / (u - l)
        return max(0, min(1, pos))
    except Exception:
        return None


def calc_atr(hist, period=14):
    """Average True Range. Returns dollar value."""
    try:
        high = hist['High']
        low = hist['Low']
        close = hist['Close']
        prev_close = close.shift(1)
        tr1 = high - low
        tr2 = (high - prev_close).abs()
        tr3 = (low - prev_close).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(window=period).mean()
        val = float(atr.iloc[-1])
        return val if not pd.isna(val) else None
    except Exception:
        return None


def calc_iv_rank(ticker_obj, current_iv):
    """IV Rank vs past year. Approximate using ATR % as a proxy.
    Returns 0-100 or None."""
    try:
        hist = ticker_obj.history(period='1y', auto_adjust=True)
        if hist.empty or current_iv is None:
            return None
        # Use rolling 30-day realized vol as proxy
        returns = hist['Close'].pct_change()
        rv = returns.rolling(window=30).std() * np.sqrt(252) * 100  # annualized %
        rv = rv.dropna()
        if len(rv) < 30:
            return None
        rv_min = float(rv.min())
        rv_max = float(rv.max())
        if rv_max <= rv_min:
            return None
        # current_iv expected as percentage already
        rank = (current_iv - rv_min) / (rv_max - rv_min) * 100
        return max(0, min(100, rank))
    except Exception:
        return None


def find_support_floors(hist, n_levels=2):
    """Find recent support levels (local lows in last 6 months).
    Returns list of price levels, sorted descending (closest below price first)."""
    try:
        # Last 6 months
        recent = hist.tail(120) if len(hist) > 120 else hist
        lows = recent['Low']
        current = float(hist['Close'].iloc[-1])
        # Find local minima: low lower than 5 days before and after
        levels = []
        for i in range(5, len(lows) - 5):
            window = lows.iloc[i-5:i+6]
            if lows.iloc[i] == window.min() and lows.iloc[i] < current * 0.97:
                levels.append(float(lows.iloc[i]))
        if not levels:
            return []
        # Cluster nearby levels (within 2%)
        levels.sort(reverse=True)
        clustered = []
        for lv in levels:
            if not clustered or abs(lv - clustered[-1]) / clustered[-1] > 0.02:
                clustered.append(lv)
            if len(clustered) >= n_levels:
                break
        return clustered
    except Exception:
        return []


def find_tariff_floor(hist):
    """Find the lowest closing price during the April 2025 tariff crash window.
    Returns float price or None."""
    try:
        if hist is None or hist.empty:
            return None
        idx = hist.index
        # Normalize timezone for comparison
        if hasattr(idx, 'tz') and idx.tz is not None:
            tariff_start = TARIFF_CRASH_START.tz_localize(idx.tz)
            tariff_end = TARIFF_CRASH_END.tz_localize(idx.tz)
        else:
            tariff_start = TARIFF_CRASH_START
            tariff_end = TARIFF_CRASH_END
        mask = (hist.index >= tariff_start) & (hist.index <= tariff_end)
        if not mask.any():
            return None
        floor = float(hist.loc[mask, 'Low'].min())
        return floor if not pd.isna(floor) else None
    except Exception:
        return None


def calc_trend_state(price, dma50, dma200):
    """Returns trend tag: 'Bull · Gold', 'Bull', 'Bear · Death', 'Bear', or None."""
    if not all([price, dma50, dma200]):
        return None
    price_above_200 = price > dma200
    fifty_above_200 = dma50 > dma200
    if price_above_200 and fifty_above_200:
        return 'Bull · Gold'
    if price_above_200:
        return 'Bull'
    if not fifty_above_200:
        return 'Bear · Death'
    return 'Bear'


def detect_chart_dips(hist, threshold=0.04, max_dips=2):
    """Find largest single-day drops in 1y history.
    Returns list of {date, pct_drop, price_after} sorted by severity."""
    try:
        if hist is None or hist.empty:
            return []
        returns = hist['Close'].pct_change()
        # Find days with significant drops
        drops = returns[returns < -threshold].sort_values()
        results = []
        seen_dates = set()
        for date, pct in drops.items():
            # Skip if within 7 days of an already-recorded drop
            if any(abs((date - d).days) < 7 for d in seen_dates):
                continue
            results.append({
                'date': date,
                'pct': float(pct),
                'price_after': float(hist.loc[date, 'Close']),
            })
            seen_dates.add(date)
            if len(results) >= max_dips:
                break
        # Sort by date for chart positioning
        results.sort(key=lambda x: x['date'])
        return results
    except Exception:
        return []


def fetch_news_headlines(ticker_obj, ticker_str, n=4):
    """Get recent news from yfinance. Returns list of {title, date, sentiment, url}."""
    try:
        news = ticker_obj.news
        if not news:
            return []
        items = []
        for n_item in news[:n*2]:
            try:
                content = n_item.get('content', n_item)
                title = content.get('title') or n_item.get('title')
                if not title:
                    continue
                # URL extraction (yfinance has multiple possible fields)
                url = ''
                if isinstance(content, dict):
                    cu = content.get('canonicalUrl') or {}
                    ct = content.get('clickThroughUrl') or {}
                    url = (cu.get('url') if isinstance(cu, dict) else cu) or \
                          (ct.get('url') if isinstance(ct, dict) else ct) or \
                          content.get('link') or ''
                if not url:
                    url = n_item.get('link', '')
                # Date extraction
                pub = content.get('pubDate') or content.get('providerPublishTime')
                if isinstance(pub, str):
                    try:
                        date = pd.Timestamp(pub).strftime('%b %d')
                    except:
                        date = ''
                elif isinstance(pub, (int, float)):
                    date = pd.Timestamp(pub, unit='s').strftime('%b %d')
                else:
                    date = ''
                # Simple sentiment (keyword-based)
                title_lower = title.lower()
                pos_kw = ['beat', 'upgrade', 'rise', 'surge', 'growth', 'expand', 'launch', 'record', 'strong']
                neg_kw = ['miss', 'downgrade', 'fall', 'plunge', 'cut', 'lawsuit', 'recall', 'weak', 'decline']
                if any(k in title_lower for k in pos_kw):
                    sentiment = 'positive'
                elif any(k in title_lower for k in neg_kw):
                    sentiment = 'negative'
                else:
                    sentiment = 'neutral'
                items.append({
                    'title': title[:90] + ('...' if len(title) > 90 else ''),
                    'date': date,
                    'sentiment': sentiment,
                    'url': url,
                })
                if len(items) >= n:
                    break
            except Exception:
                continue
        return items
    except Exception:
        return []


def get_company_narrative(info):
    """Build ELI5 company description from yfinance info."""
    try:
        name = info.get('longName') or info.get('shortName', 'this company')
        summary = info.get('longBusinessSummary', '')
        sector = info.get('sector', '')
        industry = info.get('industry', '')
        # Try to extract first 2-3 sentences max
        sentences = summary.replace('. ', '.|').split('|')
        short = '. '.join(s.strip() for s in sentences[:2] if s.strip())
        if len(short) > 280:
            short = short[:280] + '...'
        return short or f"{name} operates in {industry or sector}"
    except Exception:
        return ''


def get_fundamentals_checklist(info, ticker_obj):
    """4-point checklist: Revenue 5y, Profits 5y, Cash flow, Debt."""
    checks = {
        'revenue': None,
        'profits': None,
        'cashflow': None,
        'debt': None,
    }
    try:
        # Revenue growth 5y
        fin = ticker_obj.financials
        if fin is not None and not fin.empty and 'Total Revenue' in fin.index:
            revs = fin.loc['Total Revenue'].dropna()
            if len(revs) >= 3:
                # newer columns first - check trend
                r_list = revs.tolist()
                # Check if generally growing
                growing = sum(1 for i in range(len(r_list) - 1) if r_list[i] > r_list[i+1])
                checks['revenue'] = growing >= len(r_list) - 2  # allow 1 down year
        
        # Profit growth
        if fin is not None and not fin.empty and 'Net Income' in fin.index:
            profs = fin.loc['Net Income'].dropna()
            if len(profs) >= 3:
                p_list = profs.tolist()
                growing = sum(1 for i in range(len(p_list) - 1) if p_list[i] > p_list[i+1])
                checks['profits'] = growing >= len(p_list) - 2
        
        # Cash flow (positive recent)
        cf = ticker_obj.cashflow
        if cf is not None and not cf.empty:
            cf_keys = ['Free Cash Flow', 'Operating Cash Flow', 'Total Cash From Operating Activities']
            for k in cf_keys:
                if k in cf.index:
                    vals = cf.loc[k].dropna()
                    if len(vals) >= 2:
                        checks['cashflow'] = (vals.iloc[0] > 0)
                        break
        
        # Debt - use D/E ratio
        de = info.get('debtToEquity')
        if de is not None:
            # <100 = low/moderate, 100-200 = some, >200 = high
            if de < 100:
                checks['debt'] = 'good'
            elif de < 200:
                checks['debt'] = 'okay'
            else:
                checks['debt'] = 'bad'
    except Exception as e:
        pass
    return checks


def find_alternative_strike(ticker_obj, current_price, target_dte=35, target_delta=-0.16):
    """Find a short-dated alternative put strike.
    Returns dict with strike, expiry, delta, otm_pct, mid_credit, contracts or None."""
    try:
        expiries = ticker_obj.options
        if not expiries:
            return None
        today = pd.Timestamp.now().normalize()
        # Find expiry closest to target_dte
        best_exp = None
        best_diff = 999
        for exp in expiries:
            try:
                exp_date = pd.Timestamp(exp)
                dte = (exp_date - today).days
                if 20 <= dte <= 60:
                    diff = abs(dte - target_dte)
                    if diff < best_diff:
                        best_diff = diff
                        best_exp = exp
            except Exception:
                continue
        if not best_exp:
            return None
        chain = ticker_obj.option_chain(best_exp)
        puts = chain.puts
        if puts.empty:
            return None
        # Calculate delta for each strike
        T = (pd.Timestamp(best_exp) - today).days / 365.0
        if T <= 0:
            return None
        S = current_price
        candidates = []
        for _, row in puts.iterrows():
            K = float(row['strike'])
            iv = float(row.get('impliedVolatility', 0))
            if iv <= 0 or K >= S:
                continue
            d = black_scholes_delta_put(S, K, T, RISK_FREE, iv)
            bid = float(row.get('bid', 0)) or 0
            ask = float(row.get('ask', 0)) or 0
            if bid <= 0 and ask <= 0:
                continue
            mid = (bid + ask) / 2 if (bid > 0 and ask > 0) else max(bid, ask)
            candidates.append({
                'strike': K,
                'delta': d,
                'iv': iv,
                'mid': mid,
                'expiry': best_exp,
                'dte': int(T * 365),
            })
        if not candidates:
            return None
        # Pick closest to target delta
        best = min(candidates, key=lambda c: abs(c['delta'] - target_delta))
        otm_pct = (S - best['strike']) / S * 100
        return {
            'strike': best['strike'],
            'expiry': best_exp,
            'delta': round(best['delta'], 3),
            'otm_pct': round(otm_pct, 1),
            'mid': round(best['mid'], 2),
            'dte': best['dte'],
        }
    except Exception:
        return None


def get_bargain_price(hist, current_price):
    """Bargain price = stress test floor.
    Use lower of: tariff crash low OR 4y low (approximated by 1y low * adjustment)."""
    try:
        if hist is None or hist.empty:
            return None
        one_y_low = float(hist['Low'].min())
        tariff_floor = find_tariff_floor(hist)
        # Bargain = lower of these = "if it goes here, it's a steal"
        if tariff_floor:
            bargain = min(one_y_low, tariff_floor)
        else:
            bargain = one_y_low
        # Sanity: shouldn't be above current price
        if bargain >= current_price:
            return None
        return round(bargain, 2)
    except Exception:
        return None




def fetch_fear_greed():
    """CNN Fear & Greed Index. Returns 0-100 score + label."""
    try:
        url = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        score = int(data.get('fear_and_greed', {}).get('score', 0))
        rating = data.get('fear_and_greed', {}).get('rating', 'unknown')
        return {
            'score': score,
            'label': rating.replace('_', ' ').title(),
            'icon': '😱' if score < 25 else '😟' if score < 45 else '😐' if score < 55 else '😊' if score < 75 else '🤑',
        }
    except Exception as e:
        print(f"  ⚠️ Fear&Greed fetch failed: {e}")
        return None


def fetch_put_call_ratio():
    """CBOE Total Put/Call Ratio. Returns ratio + interpretation."""
    try:
        url = "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_history.json"
        # Approximate using VIX-related signals; CBOE direct is paywalled
        # Fallback: derive from SPY put/call open interest
        spy = yf.Ticker('SPY')
        chains = spy.options
        if not chains:
            return None
        # Use nearest expiry
        chain = spy.option_chain(chains[0])
        put_oi = chain.puts['openInterest'].fillna(0).sum()
        call_oi = chain.calls['openInterest'].fillna(0).sum()
        if call_oi == 0:
            return None
        ratio = put_oi / call_oi
        if ratio > 1.2:
            label = 'Defensive'
            icon = '🛡️'
        elif ratio > 0.9:
            label = 'Balanced'
            icon = '⚖️'
        elif ratio > 0.7:
            label = 'Bullish'
            icon = '📈'
        else:
            label = 'Complacent'
            icon = '⚠️'
        return {'ratio': round(ratio, 2), 'label': label, 'icon': icon}
    except Exception as e:
        print(f"  ⚠️ Put/Call fetch failed: {e}")
        return None


def fetch_aaii_sentiment():
    """AAII Weekly Sentiment Survey via simple RSS scrape."""
    try:
        # AAII publishes weekly. We use a stable scrape approach
        url = "https://www.aaii.com/sentimentsurvey"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode('utf-8', errors='ignore')
        # Find bullish/bearish/neutral percentages via regex
        bull_match = re.search(r'Bullish[^0-9]*([0-9]+\.?[0-9]*)%', html)
        bear_match = re.search(r'Bearish[^0-9]*([0-9]+\.?[0-9]*)%', html)
        if not bull_match or not bear_match:
            return None
        bull = float(bull_match.group(1))
        bear = float(bear_match.group(1))
        spread = bull - bear
        if spread > 20:
            label = 'Crowded Bull'
            icon = '🐂'
        elif spread > 5:
            label = 'Bullish'
            icon = '📈'
        elif spread > -5:
            label = 'Neutral'
            icon = '😐'
        elif spread > -20:
            label = 'Bearish'
            icon = '📉'
        else:
            label = 'Capitulation'
            icon = '🐻'
        return {
            'bull': round(bull, 1),
            'bear': round(bear, 1),
            'spread': round(spread, 1),
            'label': label,
            'icon': icon,
        }
    except Exception as e:
        print(f"  ⚠️ AAII fetch failed: {e}")
        return None


def fetch_sector_performance():
    """Sector ETFs daily performance."""
    sectors = {
        'XLK': 'Technology',
        'XLF': 'Financials',
        'XLV': 'Healthcare',
        'XLE': 'Energy',
        'XLI': 'Industrials',
        'XLY': 'Consumer Disc',
        'XLP': 'Consumer Staples',
        'XLU': 'Utilities',
        'XLRE': 'Real Estate',
        'XLB': 'Materials',
        'XLC': 'Comm Services',
    }
    results = []
    for ticker, name in sectors.items():
        try:
            t = yf.Ticker(ticker)
            hist = t.history(period='5d')
            if hist.empty or len(hist) < 2:
                continue
            today = float(hist['Close'].iloc[-1])
            prev = float(hist['Close'].iloc[-2])
            chg = (today - prev) / prev * 100
            results.append({
                'ticker': ticker,
                'name': name,
                'change': round(chg, 2),
            })
        except Exception:
            continue
    results.sort(key=lambda x: x['change'], reverse=True)
    return results


def get_sentiment_pack():
    """Pull all sentiment signals."""
    print("Pulling sentiment signals...")
    pack = {
        'fear_greed': fetch_fear_greed(),
        'put_call': fetch_put_call_ratio(),
        'aaii': fetch_aaii_sentiment(),
        'sectors': fetch_sector_performance(),
    }
    if pack['fear_greed']:
        print(f"  Fear&Greed: {pack['fear_greed']['score']} ({pack['fear_greed']['label']})")
    if pack['put_call']:
        print(f"  Put/Call: {pack['put_call']['ratio']} ({pack['put_call']['label']})")
    if pack['aaii']:
        print(f"  AAII: bull {pack['aaii']['bull']}% / bear {pack['aaii']['bear']}%")
    if pack['sectors']:
        print(f"  Sectors loaded: {len(pack['sectors'])}")
    return pack


# ==============================================================
# POSITION SIZING
# ==============================================================

PORTFOLIO_NLV = 2_400_000  # Ash's net liq baseline
MAX_PER_STOCK_PCT = 5  # max 5% of NLV per single name

def suggest_position_size(score, red_x, vix_mode, put_strike):
    """Suggest contract count based on score, gap risk, VIX mode."""
    # Base size from score
    if score >= 9:
        base = 5
    elif score >= 7:
        base = 3
    elif score >= 5:
        base = 1
    else:
        base = 0
    
    # Adjust for gap risk (Red X)
    if red_x is not None:
        if red_x == 0:
            pass  # full size
        elif red_x == 1:
            base = max(1, base - 1)
        elif red_x == 2:
            base = max(1, base // 2)
        else:
            base = 0  # skip
    
    # Adjust for VIX mode
    mode = (vix_mode or '').upper()
    if 'CAUTIOUS' in mode:
        base = max(1, base // 2)
    elif 'STAND DOWN' in mode or 'CRISIS' in mode:
        base = 0
    
    # Cap by 5% NLV exposure (rough check)
    if put_strike and put_strike > 0:
        contract_bp = put_strike * 100  # cash-secured BP
        max_contracts_by_size = int((PORTFOLIO_NLV * MAX_PER_STOCK_PCT / 100) / contract_bp)
        base = min(base, max(1, max_contracts_by_size))
    
    return base




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
        flags.append(f'🚨 RED ALERT: {", ".join(rf.get("flags") or [])}')
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
        
        # ==============================================================
        # v18: Technical analysis + visual data for the new card
        # ==============================================================
        hist = fetch_1y_history(t, ticker)
        if hist is not None and len(hist) >= 50:
            close = hist['Close']
            d['hist_chart'] = {
                'dates': [str(idx)[:10] for idx in hist.index],
                'prices': [float(p) for p in close.tolist()],
                'pct_1y': round((float(close.iloc[-1]) / float(close.iloc[0]) - 1) * 100, 1),
                'low_1y': float(hist['Low'].min()),
                'high_1y': float(hist['High'].max()),
            }
            d['rsi_14'] = round(calc_rsi(close, 14), 1) if calc_rsi(close, 14) else None
            d['dma_50'] = round(calc_dma(close, 50), 2) if calc_dma(close, 50) else None
            d['dma_200'] = round(calc_dma(close, 200), 2) if calc_dma(close, 200) else None
            d['bollinger_pos'] = calc_bollinger_position(close)
            d['atr_14'] = round(calc_atr(hist, 14), 2) if calc_atr(hist, 14) else None
            d['support_floors'] = find_support_floors(hist, n_levels=2)
            d['tariff_floor'] = find_tariff_floor(hist)
            d['trend_state'] = calc_trend_state(d['price'], d['dma_50'], d['dma_200'])
            d['chart_dips'] = detect_chart_dips(hist)
            d['bargain_price'] = get_bargain_price(hist, d['price'])
            # IV rank using current option's IV
            current_iv = d['put_trade']['iv'] * 100 if d['put_trade'] else None
            d['iv_rank'] = round(calc_iv_rank(t, current_iv), 0) if current_iv else None
            # ATR distance to strike
            if d['atr_14'] and d['put_trade']:
                d['atrs_to_strike'] = round((d['price'] - d['put_trade']['strike']) / d['atr_14'], 1)
            else:
                d['atrs_to_strike'] = None
        else:
            d['hist_chart'] = None
            d['rsi_14'] = None
            d['dma_50'] = None
            d['dma_200'] = None
            d['bollinger_pos'] = None
            d['atr_14'] = None
            d['support_floors'] = []
            d['tariff_floor'] = None
            d['trend_state'] = None
            d['chart_dips'] = []
            d['bargain_price'] = None
            d['iv_rank'] = None
            d['atrs_to_strike'] = None
        
        # News + narrative
        d['news_items'] = fetch_news_headlines(t, ticker, n=4)
        d['company_narrative'] = get_company_narrative(info)
        d['fundamentals'] = get_fundamentals_checklist(info, t)
        # Beta from info
        d['beta'] = round(info.get('beta', 0), 2) if info.get('beta') else None
        # Alternative short-dated put
        d['alt_put'] = find_alternative_strike(t, d['price'])
        
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


def build_chart_svg(r):
    """Build 1Y SVG price chart with dip annotations and tariff anchor."""
    hist = r.get('hist_chart')
    if not hist or not hist.get('prices'):
        return '<div class="chart-empty">No chart data</div>'
    
    prices = hist['prices']
    if len(prices) < 30:
        return '<div class="chart-empty">Insufficient history</div>'
    
    # Chart dims
    W, H = 400, 240
    PAD_L, PAD_R, PAD_T, PAD_B = 8, 8, 30, 28
    plot_w = W - PAD_L - PAD_R
    plot_h = H - PAD_T - PAD_B
    
    p_min = min(prices)
    p_max = max(prices)
    p_range = p_max - p_min if p_max > p_min else 1
    
    # Build polyline points
    n = len(prices)
    points = []
    for i, p in enumerate(prices):
        x = PAD_L + (i / (n - 1)) * plot_w
        y = PAD_T + (1 - (p - p_min) / p_range) * plot_h
        points.append(f"{x:.1f},{y:.1f}")
    
    polyline_pts = ' '.join(points)
    polygon_pts = f"{polyline_pts} {PAD_L + plot_w:.1f},{PAD_T + plot_h:.1f} {PAD_L:.1f},{PAD_T + plot_h:.1f}"
    
    # 1y change badge
    pct_1y = hist.get('pct_1y', 0)
    pct_color = '#34d399' if pct_1y >= 0 else '#f87171'
    pct_bg = '#064e3b' if pct_1y >= 0 else '#7f1d1d'
    pct_sign = '+' if pct_1y >= 0 else ''
    
    # Tariff floor line
    tariff_line = ''
    tariff_floor = r.get('tariff_floor')
    if tariff_floor and p_min <= tariff_floor <= p_max:
        ty = PAD_T + (1 - (tariff_floor - p_min) / p_range) * plot_h
        tariff_line = f'''
            <line x1="{PAD_L}" y1="{ty:.1f}" x2="{PAD_L + plot_w}" y2="{ty:.1f}" 
                  stroke="#a855f7" stroke-width="1" stroke-dasharray="4,3" opacity="0.7"/>
            <rect x="{PAD_L}" y="{ty - 7:.1f}" width="100" height="14" rx="3" fill="#3b0764"/>
            <text x="{PAD_L + 6}" y="{ty + 3:.1f}" fill="#e9d5ff" font-size="10" font-weight="600">⚠ TARIFF ${tariff_floor:.0f}</text>
        '''
    
    # Dip annotations (max 2)
    dip_html = ''
    dips = r.get('chart_dips') or []
    dip_dates = hist.get('dates', [])
    for idx_dip, dip in enumerate(dips[:2]):
        try:
            dip_date_str = str(dip['date'])[:10]
            if dip_date_str not in dip_dates:
                continue
            i = dip_dates.index(dip_date_str)
            x = PAD_L + (i / (n - 1)) * plot_w
            price_after = dip['price_after']
            y = PAD_T + (1 - (price_after - p_min) / p_range) * plot_h
            
            # Place label box above or below depending on position
            if y < H / 2:
                box_y = y + 20
                line_y2 = y + 18
            else:
                box_y = y - 50
                line_y2 = y - 12
            
            box_x = max(PAD_L, min(W - 160, x - 75))
            
            try:
                date_obj = pd.Timestamp(dip['date'])
                date_label = date_obj.strftime('%b %Y').upper()
            except:
                date_label = dip_date_str
            
            dip_html += f'''
                <line x1="{x:.1f}" y1="{y:.1f}" x2="{x:.1f}" y2="{line_y2:.1f}" 
                      stroke="#f87171" stroke-width="1" stroke-dasharray="2,2" opacity="0.6"/>
                <circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="#f87171" stroke="#0f172a" stroke-width="2"/>
                <rect x="{box_x:.1f}" y="{box_y:.1f}" width="150" height="32" rx="4" 
                      fill="#1e293b" stroke="#475569" stroke-width="1"/>
                <text x="{box_x + 8:.1f}" y="{box_y + 13:.1f}" fill="#fca5a5" font-size="9" font-weight="600">{date_label}  {dip['pct']*100:.1f}%</text>
                <text x="{box_x + 8:.1f}" y="{box_y + 26:.1f}" fill="#cbd5e1" font-size="9">News-driven dip</text>
            '''
        except Exception:
            continue
    
    return f'''
    <svg viewBox="0 0 {W} {H}" preserveAspectRatio="xMidYMid meet" class="chart-svg">
        <defs>
            <linearGradient id="pg-{r['ticker']}" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stop-color="#34d399" stop-opacity="0.32"/>
                <stop offset="100%" stop-color="#34d399" stop-opacity="0"/>
            </linearGradient>
        </defs>
        <polyline points="{polyline_pts}" fill="none" stroke="#34d399" stroke-width="2.2"/>
        <polygon points="{polygon_pts}" fill="url(#pg-{r['ticker']})"/>
        <line x1="{PAD_L}" y1="{PAD_T + plot_h:.1f}" x2="{PAD_L + plot_w}" y2="{PAD_T + plot_h:.1f}" stroke="#1e293b" stroke-width="0.5"/>
        <text x="{PAD_L + 2}" y="{PAD_T - 10}" fill="#64748b" font-size="13" font-weight="600" letter-spacing="0.5">1Y PRICE</text>
        <rect x="{W - 76}" y="{H - PAD_B - 7}" width="64" height="16" rx="3" fill="{pct_bg}"/>
        <text x="{W - 44}" y="{H - PAD_B + 5}" fill="{pct_color}" font-size="13" font-weight="600" text-anchor="middle">{pct_sign}{pct_1y:.1f}%</text>
        {tariff_line}
        {dip_html}
    </svg>
    '''


def build_indicators_panel(r):
    """Build the right-column indicator panel."""
    price = r['price']
    hist = r.get('hist_chart') or {}
    
    parts = []
    
    # 52w range
    low_1y = hist.get('low_1y')
    high_1y = hist.get('high_1y')
    if low_1y and high_1y and high_1y > low_1y:
        pos_pct = (price - low_1y) / (high_1y - low_1y) * 100
        parts.append(f'''<div class="ind-block">
            <div class="ind-label">52w range</div>
            <div class="bar-52w">
                <div class="bar-fill"></div>
                <div class="bar-marker" style="left: calc({pos_pct:.0f}% - 4px);"></div>
            </div>
            <div class="bar-labels">
                <span>${low_1y:.0f}</span>
                <span class="bar-current">${price:.2f}</span>
                <span>${high_1y:.0f}</span>
            </div>
        </div>''')
    
    # RSI
    rsi = r.get('rsi_14')
    if rsi is not None:
        rsi_pos = max(0, min(100, rsi))
        parts.append(f'''<div class="ind-block">
            <div class="ind-label-row">
                <span class="ind-label">RSI 14</span>
                <span class="ind-val">{rsi:.0f}</span>
            </div>
            <div class="bar-rsi">
                <div class="bar-rsi-low"></div>
                <div class="bar-rsi-mid"></div>
                <div class="bar-rsi-high"></div>
                <div class="bar-marker" style="left: calc({rsi_pos:.0f}% - 4px);"></div>
            </div>
        </div>''')
    
    # 50 DMA
    dma50 = r.get('dma_50')
    if dma50:
        pct_diff = (price - dma50) / dma50 * 100
        # Mapping: clamp to ±20%
        clamped = max(-20, min(20, pct_diff))
        if clamped >= 0:
            fill_left = 50
            fill_width = (clamped / 20) * 50
            marker_left = 50 + (clamped / 20) * 50
        else:
            fill_left = 50 + (clamped / 20) * 50  # negative
            fill_width = -(clamped / 20) * 50
            marker_left = 50 + (clamped / 20) * 50
        color = '#34d399' if pct_diff >= 0 else '#f87171'
        sign = '+' if pct_diff >= 0 else ''
        pct_color = '#34d399' if pct_diff >= 0 else '#f87171'
        parts.append(f'''<div class="ind-block">
            <div class="ind-label-row">
                <span class="ind-label">50 dma</span>
                <span class="ind-val" style="color: {pct_color};">{sign}{pct_diff:.1f}%</span>
            </div>
            <div class="bar-dma">
                <div class="bar-dma-fill" style="left: {fill_left:.0f}%; width: {fill_width:.0f}%; background: {color};"></div>
                <div class="bar-dma-center"></div>
                <div class="bar-marker" style="left: calc({marker_left:.0f}% - 4px);"></div>
            </div>
        </div>''')
    
    # 200 DMA
    dma200 = r.get('dma_200')
    if dma200:
        pct_diff = (price - dma200) / dma200 * 100
        clamped = max(-20, min(20, pct_diff))
        if clamped >= 0:
            fill_left = 50
            fill_width = (clamped / 20) * 50
            marker_left = 50 + (clamped / 20) * 50
        else:
            fill_left = 50 + (clamped / 20) * 50
            fill_width = -(clamped / 20) * 50
            marker_left = 50 + (clamped / 20) * 50
        color = '#34d399' if pct_diff >= 0 else '#f87171'
        sign = '+' if pct_diff >= 0 else ''
        pct_color = '#34d399' if pct_diff >= 0 else '#f87171'
        parts.append(f'''<div class="ind-block">
            <div class="ind-label-row">
                <span class="ind-label">200 dma</span>
                <span class="ind-val" style="color: {pct_color};">{sign}{pct_diff:.1f}%</span>
            </div>
            <div class="bar-dma">
                <div class="bar-dma-fill" style="left: {fill_left:.0f}%; width: {fill_width:.0f}%; background: {color};"></div>
                <div class="bar-dma-center"></div>
                <div class="bar-marker" style="left: calc({marker_left:.0f}% - 4px);"></div>
            </div>
        </div>''')
    
    # Bollinger
    boll = r.get('bollinger_pos')
    if boll is not None:
        pos_pct = boll * 100
        parts.append(f'''<div class="ind-block">
            <div class="ind-label">Bollinger</div>
            <div class="bar-bollinger">
                <div class="bar-bb-cheap"></div>
                <div class="bar-bb-normal"></div>
                <div class="bar-bb-expensive"></div>
                <div class="bar-bb-center-l"></div>
                <div class="bar-bb-center-r"></div>
                <div class="bar-marker bar-marker-tall" style="left: calc({pos_pct:.0f}% - 4px);"></div>
            </div>
            <div class="bar-bb-labels">
                <span class="bb-cheap">CHEAP</span>
                <span class="bb-normal">NORMAL</span>
                <span class="bb-expensive">EXPENSIVE</span>
            </div>
        </div>''')
    
    # Support floors ladder (with tariff anchor)
    pt = r.get('put_trade') or {}
    strike = pt.get('strike')
    supports = r.get('support_floors') or []
    tariff_floor = r.get('tariff_floor')
    
    ladder_rows = []
    # Now (top - white)
    ladder_rows.append(('price', '#f1f5f9', '#f1f5f9', f'${price:.0f} now', '', 0))
    # Supports
    for sup in supports[:2]:
        pct = (price - sup) / price * 100
        if pct < 4:
            color = '#fbbf24'
            label_color = '#fde68a'
        else:
            color = '#f87171'
            label_color = '#fca5a5'
        ladder_rows.append(('support', color, label_color, f'${sup:.0f}', f'−{pct:.0f}%', pct))
    # Tariff floor
    if tariff_floor and tariff_floor < price:
        pct = (price - tariff_floor) / price * 100
        ladder_rows.append(('⚠ tariff', '#a855f7', '#e9d5ff', f'${tariff_floor:.0f}', f'−{pct:.0f}%', pct))
    # Strike
    if strike and strike < price:
        pct = (price - strike) / price * 100
        ladder_rows.append(('strike', '#34d399', '#6ee7b7', f'${strike:.0f}', f'−{pct:.0f}% ✓', pct))
    
    ladder_html = ''
    for label, dot_color, label_color, value, pct_str, _ in ladder_rows:
        is_tariff = '⚠' in label
        bg = 'background: linear-gradient(90deg, rgba(168,85,247,0.08), transparent); border-top: 1px dashed #4c1d95; border-bottom: 1px dashed #4c1d95; padding: 4px 0;' if is_tariff else ''
        ladder_html += f'''<div class="ladder-row" style="{bg}">
            <div class="ladder-label" style="color: {label_color};">{label}</div>
            <div class="ladder-dot" style="background: {dot_color};"></div>
            <div class="ladder-value" style="color: {label_color};">{value} <span class="ladder-pct">{pct_str}</span></div>
        </div>'''
    
    if ladder_html:
        stress_note = ''
        if tariff_floor and strike and strike < tariff_floor:
            stress_pct = (tariff_floor - strike) / tariff_floor * 100
            stress_note = f'<div class="stress-test"><strong>Stress test:</strong> Even at the Apr 2025 tariff panic floor of ${tariff_floor:.0f}, your strike sits another <strong style="color:#6ee7b7;">{stress_pct:.0f}% below</strong>. Stock has never traded at ${strike:.0f} in 4 years.</div>'
        parts.append(f'''<div class="ind-block">
            <div class="ind-label">Support floors</div>
            <div class="ladder">{ladder_html}</div>
            {stress_note}
        </div>''')
    
    # ATR
    atr = r.get('atr_14')
    atrs_to = r.get('atrs_to_strike')
    if atr and atrs_to is not None:
        # Visual marker: clamp to range 0-25 ATRs  
        atr_pos = min(25, max(0, atrs_to)) / 25 * 100
        atr_color = '#34d399' if atrs_to >= 10 else '#fbbf24' if atrs_to >= 5 else '#f87171'
        parts.append(f'''<div class="ind-block">
            <div class="ind-label-row">
                <span class="ind-label">ATR · daily move</span>
                <span class="ind-val" style="color: {atr_color};">{atrs_to:.0f} ATRs to strike</span>
            </div>
            <div class="bar-atr">
                <div class="bar-atr-bad"></div>
                <div class="bar-atr-warn"></div>
                <div class="bar-atr-good"></div>
                <div class="bar-marker" style="left: calc({atr_pos:.0f}% - 4px);"></div>
            </div>
            <div class="bar-atr-labels">
                <span class="bb-cheap">&lt;5</span>
                <span class="bb-normal">5-10</span>
                <span class="bb-expensive">10+ SAFE</span>
            </div>
            <div class="atr-note">${atr:.2f}/day</div>
        </div>''')
    
    # 2x2 grid: IV rank, Beta, Trend, Short int
    iv_rank = r.get('iv_rank')
    beta = r.get('beta')
    trend = r.get('trend_state')
    si = r.get('short_interest')
    
    iv_color = '#34d399' if iv_rank and iv_rank >= 70 else '#fbbf24' if iv_rank and iv_rank >= 50 else '#f87171' if iv_rank and iv_rank < 30 else '#cbd5e1'
    iv_label = 'great' if iv_rank and iv_rank >= 70 else 'good' if iv_rank and iv_rank >= 50 else 'low' if iv_rank and iv_rank < 30 else 'mid'
    
    trend_color = '#34d399' if trend and 'Bull' in trend else '#f87171' if trend and 'Bear' in trend else '#cbd5e1'
    
    si_text = '—'
    si_color = '#cbd5e1'
    if si is not None:
        si_pct = si if isinstance(si, (int, float)) else (si.get('pct_short') or si.get('percent') if isinstance(si, dict) else None)
        if isinstance(si_pct, (int, float)):
            si_text = f'{si_pct:.1f}%'
            if si_pct < 5:
                si_text += ' ✓'
                si_color = '#34d399'
            elif si_pct > 15:
                si_color = '#f87171'
    
    parts.append(f'''<div class="ind-grid">
        <div class="ind-mini"><div class="mini-label">IV rank</div><div class="mini-val" style="color: {iv_color};">{iv_rank:.0f}% {iv_label}</div></div>
        <div class="ind-mini"><div class="mini-label">Beta</div><div class="mini-val">{beta if beta else '—'}</div></div>
        <div class="ind-mini"><div class="mini-label">Trend</div><div class="mini-val" style="color: {trend_color};">{trend or '—'}</div></div>
        <div class="ind-mini"><div class="mini-label">Short int</div><div class="mini-val" style="color: {si_color};">{si_text}</div></div>
    </div>''' if (iv_rank or beta or trend or si) else '')
    
    return ''.join(parts)


def render_html(results, scan_date, dashboard, economic_events, caution, sentiment=None):
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
        alt = r.get('alt_put') or {}
        
        tag, tag_class = get_tag(r, src)
        timing = r.get('earnings_timing', 'TBD')
        timing_class = 'bmo' if timing == 'BMO' else 'amc' if timing == 'AMC' else 'tbd'
        timing_icon = '☀️' if timing == 'BMO' else '🌙' if timing == 'AMC' else '⏱'
        
        # Smart fire time (in ET, not Dubai - per Ash's request)
        default_fire = get_default_fire_dt(r['next_earnings'], timing)
        adjusted_fire, fire_warning = (default_fire, None)
        if default_fire:
            adjusted_fire, fire_warning = adjust_fire_window(default_fire, economic_events)
        
        # Build ET fire string (markets in NY)
        fire_str = "Fire timing TBD"
        if default_fire:
            try:
                date_obj = datetime.strptime(r['next_earnings'], '%Y-%m-%d')
                if timing == 'BMO':
                    prev = date_obj - timedelta(days=1)
                    while prev.weekday() >= 5:
                        prev -= timedelta(days=1)
                    fire_str = f"{prev.strftime('%a %b %d')} · 11:00 AM - 1:00 PM ET"
                elif timing == 'AMC':
                    fire_str = f"{date_obj.strftime('%a %b %d')} · 8:00 AM - 10:00 AM ET"
                else:
                    fire_str = f"{date_obj.strftime('%a %b %d')} · TBD"
            except:
                pass
        
        # ============================================
        # SCORES + BARGAIN BADGE
        # ============================================
        my_score = r['score']
        # Claude score from API (falls back to algo score if API unavailable)
        claude_score = round(r.get('claude_score', my_score), 1)
        bargain = r.get('bargain_price')
        bargain_html = ''
        if bargain:
            bargain_html = f'''<div class="score-block">
                <div class="score-label bargain-label">Bargain</div>
                <div class="score-badge bargain-badge">🎯 ${bargain:.0f}</div>
            </div>'''
        
        # ============================================
        # COMPANY NARRATIVE + FUNDAMENTALS CHECKLIST
        # ============================================
        narrative = r.get('company_narrative', '') or f"{r['company']} · {r.get('sector', 'sector unknown')}"
        # Cap length
        if len(narrative) > 320:
            narrative = narrative[:317] + '...'
        
        funds = r.get('fundamentals') or {}
        def fund_check(key, label):
            v = funds.get(key)
            if v is True or v == 'good':
                return f'<div class="fund-check"><span class="fund-tick">✓</span><span>{label}</span></div>'
            elif v == 'okay':
                return f'<div class="fund-check fund-warn"><span class="fund-tilde">~</span><span>{label}</span></div>'
            elif v is False or v == 'bad':
                return f'<div class="fund-check fund-bad"><span class="fund-cross">✗</span><span>{label}</span></div>'
            return f'<div class="fund-check fund-na"><span class="fund-tilde">·</span><span>{label}</span></div>'
        
        fund_html = (
            fund_check('revenue', 'Revenue 5y') +
            fund_check('profits', 'Profits 5y') +
            fund_check('cashflow', 'Cash flow') +
            fund_check('debt', 'Debt')
        )
        
        # ============================================
        # CLAUDE COMMENTARY — uses real API output when available
        # ============================================
        if r.get('claude_bullets'):
            # Real Claude API response
            claude_tag = r.get('claude_tag', 'WATCH')
            claude_bullets = list(r['claude_bullets'])
        else:
            # Fallback: algo-derived placeholders
            claude_tag = 'SAFE BET' if my_score >= 8 else 'WATCH' if my_score >= 6 else 'SKIP'
            claude_bullets = []
            edge = r.get('edge_ratio', 0)
            if edge >= 4:
                claude_bullets.append(('good', f'Edge {edge}x — premium way overpriced vs actual moves'))
            elif edge >= 2:
                claude_bullets.append(('good', f'Solid edge {edge}x — options pricing extra fear'))
            else:
                claude_bullets.append(('warn', f'Edge only {edge}x — premium not generous'))
            
            red_x = es.get('red_x_count', 0)
            if red_x == 0:
                claude_bullets.append(('good', 'Zero gap risk in last 8 quarters — boring is good'))
            elif red_x <= 1:
                claude_bullets.append(('good', f'Only {red_x}/8 quarters had big moves — low gap risk'))
            else:
                claude_bullets.append(('warn', f'{red_x}/8 quarters moved big — half-size or skip'))
            
            if r.get('trend_state') == 'Bull · Gold':
                claude_bullets.append(('good', 'Strong uptrend (Bull · Gold) — confirmed institutional bid'))
            elif r.get('trend_state') and 'Bear' in r['trend_state']:
                claude_bullets.append(('bad', f'In {r["trend_state"]} — assignment risk elevated'))
            
            if pt and bargain and pt.get('strike', 0) <= bargain:
                claude_bullets.append(('good', f'Strike below bargain price — happy if assigned'))
            
            claude_bullets = claude_bullets[:4]
        
        claude_tag_class = 'tag-safe' if claude_tag == 'SAFE BET' else 'tag-watch' if claude_tag == 'WATCH' else 'tag-skip'
        bullets_html = ''.join(
            f'<div class="claude-bullet bullet-{tone}"><span>●</span><span>{text}</span></div>'
            for tone, text in claude_bullets
        )
        
        # ============================================
        # PRIMARY + ALT PUT ROWS
        # ============================================
        if pt:
            primary_dte = pt.get('dte', 0)
            primary_html = f'''<div class="put-row">
                <span class="put-tag">PRIMARY · {primary_dte}d</span>
                <span class="put-strike">${pt["strike"]:.0f}P</span>
                <span class="put-meta">{pt["expiry"][:7]} · {pt["delta"]*100:.1f}Δ · {pt["pct_otm"]:.0f}% OTM</span>
                <span class="put-qty">×{r.get('suggested_size', 1)}</span>
                <span class="put-credit">${pt["mid"]*100:.0f}</span>
            </div>'''
        else:
            primary_html = '<div class="put-row put-row-empty">No primary put available</div>'
        
        alt_html = ''
        if alt:
            alt_html = f'''<div class="put-row">
                <span class="put-tag">ALT · {alt.get("dte", 35)}d</span>
                <span class="put-strike">${alt["strike"]:.0f}P</span>
                <span class="put-meta">{alt["expiry"][:10]} · {alt["delta"]*100:.0f}Δ · {alt["otm_pct"]:.0f}% OTM</span>
                <span class="put-qty">×3</span>
                <span class="put-credit">${alt["mid"]*100:.0f}</span>
            </div>'''
        
        # ============================================
        # HERO STATS (Edge + Gap)
        # ============================================
        edge_class = 'hero-edge-great' if edge >= 4 else 'hero-edge-good' if edge >= 2 else 'hero-edge-ok'
        if red_x is None:
            gap_label = '—/8'
            gap_class = 'hero-gap-na'
        elif red_x == 0:
            gap_label = '0 / 8'
            gap_class = 'hero-gap-perfect'
        else:
            gap_label = f'{red_x} / 8'
            gap_class = 'hero-gap-good' if red_x <= 1 else 'hero-gap-warn'
        
        # ============================================
        # 1Y CHART (with dip annotations + tariff anchor)
        # ============================================
        chart_html = build_chart_svg(r)
        
        # ============================================
        # NEWS BOX
        # ============================================
        news_items = r.get('news_items') or []
        news_html = ''
        for item in news_items:
            sent = item.get('sentiment', 'neutral')
            icon = '▲' if sent == 'positive' else '▼' if sent == 'negative' else '●'
            icon_class = 'news-pos' if sent == 'positive' else 'news-neg' if sent == 'negative' else 'news-neu'
            url = item.get('url', '')
            title = item.get('title', '')
            title_html = f'<a href="{url}" target="_blank" rel="noopener" class="news-title-link">{title}</a>' if url else f'<span class="news-title">{title}</span>'
            news_html += f'''<div class="news-item">
                <span class="news-icon {icon_class}">{icon}</span>
                {title_html}
                <span class="news-date">{item.get("date", "")}</span>
            </div>'''
        if not news_html:
            news_html = '<div class="news-empty">No recent headlines</div>'
        
        # ============================================
        # INDICATOR PANEL (right column)
        # ============================================
        indicators_html = build_indicators_panel(r)
        
        # ============================================
        # SIGNALS ROW (restored buybacks/insider/EPS/no-red-flags)
        # ============================================
        ins = r.get('insider_activity') or {}
        bb = r.get('buybacks') or {}
        eps = r.get('eps_streak') or {}
        rf = r.get('red_flags') or {}
        
        sig_chips = []
        if bb.get('signal') in ('strong', 'moderate') and bb.get('amount'):
            sig_chips.append(f'<span class="sig-chip"><span class="sig-dot">●</span>Buybacks ${bb["amount"]/1e9:.1f}B</span>')
        if rf.get('signal') == 'clear':
            sig_chips.append(f'<span class="sig-chip"><span class="sig-dot">●</span>No red flags</span>')
        if ins.get('signal') == 'bullish' and ins.get('buys', 0) > 0:
            sig_chips.append(f'<span class="sig-chip"><span class="sig-dot">●</span>Insider buys ({ins["buys"]})</span>')
        if eps.get('beats', 0) >= 3:
            sig_chips.append(f'<span class="sig-chip"><span class="sig-dot">●</span>EPS streak {eps.get("streak", "")}</span>')
        
        signals_row_html = ''
        if sig_chips:
            signals_row_html = f'<div class="signals-row">{"".join(sig_chips)}</div>'
        
        warning_html = ''
        if fire_warning:
            warning_html = f'<div class="fire-warning">⚠️ {fire_warning}</div>'
        
        # ============================================
        # FINAL CARD HTML
        # ============================================
        return f"""
        <div class="pick-v18">
            <div class="tag-side">{tag}</div>
            <div class="pick-body">
                <div class="card-header">
                    <div class="header-left">
                        <a href="https://unusualwhales.com/stock/{r['ticker']}/earnings" target="_blank" class="card-ticker">{r['ticker']}</a>
                        <span class="timing-icon {timing_class}" title="{timing}">{timing_icon}</span>
                        <div class="score-block">
                            <div class="score-label">My Score</div>
                            <div class="score-badge score-mine">{my_score}</div>
                        </div>
                        <span class="score-sep">·</span>
                        <div class="score-block">
                            <div class="score-label score-claude-label">Claude</div>
                            <div class="score-badge score-claude">{claude_score}</div>
                        </div>
                        {('<span class="score-sep">·</span>' + bargain_html) if bargain_html else ''}
                    </div>
                    <div class="header-right">
                        <div class="company-name">{r['company']}</div>
                        <div class="company-price">${r['price']:.2f}</div>
                    </div>
                </div>

                <div class="company-claude-row">
                    <div class="company-box">
                        <div class="box-label">🍕 The company</div>
                        <div class="box-text">{narrative}</div>
                        <div class="fund-grid">{fund_html}</div>
                    </div>
                    <div class="claude-box">
                        <div class="box-label-row">
                            <span class="claude-c">C</span>
                            <span class="box-label">Claude says</span>
                            <span class="claude-tag {claude_tag_class}">{claude_tag}</span>
                        </div>
                        <div class="claude-bullets">{bullets_html}</div>
                    </div>
                </div>

                {primary_html}
                {alt_html}

                <div class="hero-row">
                    <div class="hero-stat {edge_class}">
                        <div class="hero-icon">⚡</div>
                        <div>
                            <div class="hero-label">Edge</div>
                            <div class="hero-value">{edge}x</div>
                        </div>
                    </div>
                    <div class="hero-stat {gap_class}">
                        <div class="hero-icon">🛡️</div>
                        <div>
                            <div class="hero-label">Gap risk</div>
                            <div class="hero-value">{gap_label}</div>
                        </div>
                    </div>
                </div>

                <div class="chart-indicators-row">
                    <div class="chart-news-col">
                        {chart_html}
                        <div class="news-box">
                            <div class="box-label">📰 Latest news</div>
                            <div class="news-list">{news_html}</div>
                        </div>
                    </div>
                    <div class="indicators-col">
                        {indicators_html}
                    </div>
                </div>

                {signals_row_html}

                {warning_html}

                <div class="card-footer">
                    <div class="fire-time">{fire_str}</div>
                    <div class="verify-links">
                        <a href="https://www.tipranks.com/stocks/{r['ticker'].lower()}/forecast" target="_blank">TipRanks</a> · 
                        <a href="https://research.investors.com/stock-quotes/nasdaq-{r['ticker'].lower()}.htm" target="_blank">Investors.com</a> · 
                        <a href="https://unusualwhales.com/stock/{r['ticker']}" target="_blank">UW</a>
                    </div>
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
        
        qw_count = sum(1 for r, s in day_picks if s == 'top' and r.get('tier') == 'QUALITY')
        ph_count = sum(1 for r, s in day_picks if s == 'top' and r.get('tier') == 'HUNT')
        wl_count = sum(1 for r, s in day_picks if s == 'watch')
        
        cards = ''.join(build_pick_row(r, s) for r, s in day_picks)
        
        day_sections += f"""
        <div class="day-section">
            <div class="day-header">
                <div class="day-title">📅 {weekday} — {date_label}</div>
                <div class="day-summary">
                    <span>{qw_count} QW</span><span>{ph_count} PH</span><span>{wl_count} WL</span>
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
    
    # Sentiment strip
    sentiment_html = ''
    if sentiment:
        items = []
        fg = sentiment.get('fear_greed')
        if fg:
            items.append(f"""
                <div class="sent-item">
                    <span class="sent-icon">{fg['icon']}</span>
                    <div>
                        <div class="sent-name">Fear & Greed</div>
                        <div><span class="sent-value">{fg['score']}</span> <span class="sent-label">{fg['label']}</span></div>
                    </div>
                </div>
            """)
        pc = sentiment.get('put_call')
        if pc:
            items.append(f"""
                <div class="sent-item">
                    <span class="sent-icon">{pc['icon']}</span>
                    <div>
                        <div class="sent-name">Put/Call SPY</div>
                        <div><span class="sent-value">{pc['ratio']}</span> <span class="sent-label">{pc['label']}</span></div>
                    </div>
                </div>
            """)
        aaii = sentiment.get('aaii')
        if aaii:
            items.append(f"""
                <div class="sent-item">
                    <span class="sent-icon">{aaii['icon']}</span>
                    <div>
                        <div class="sent-name">AAII Bull/Bear</div>
                        <div><span class="sent-value">{aaii['bull']}/{aaii['bear']}%</span> <span class="sent-label">{aaii['label']}</span></div>
                    </div>
                </div>
            """)
        
        sectors = sentiment.get('sectors') or []
        sectors_html = ''
        if sectors:
            top3 = sectors[:3]
            bot3 = sectors[-3:][::-1]
            chips = []
            for s in top3:
                chips.append(f'<span class="sector-chip"><span class="sector-up">{s["ticker"]} ↑{s["change"]:.1f}%</span></span>')
            chips.append('<span class="sector-chip sector-flat">·</span>')
            for s in bot3:
                cls = 'sector-down' if s['change'] < 0 else 'sector-flat'
                chips.append(f'<span class="sector-chip"><span class="{cls}">{s["ticker"]} {"↓" if s["change"]<0 else "↑"}{abs(s["change"]):.1f}%</span></span>')
            sectors_html = f'<div class="sectors-row">{"".join(chips)}</div>'
        
        if items or sectors_html:
            sentiment_html = f"""
            <div class="sentiment-strip">
                <div class="sentiment-title">🌡️ MARKET SENTIMENT</div>
                <div class="sentiment-grid">{''.join(items)}</div>
                {sectors_html}
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

/* HERO ROW — the two signals that matter */
.hero-row {{ display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-bottom: 8px; }}
.hero-stat {{ background: #1e293b; border-radius: 8px; padding: 10px 12px; display: flex; align-items: center; gap: 10px; border: 1px solid #334155; }}
.hero-icon {{ font-size: 20px; }}
.hero-label {{ font-size: 9px; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.05em; font-weight: 600; }}
.hero-value {{ font-size: 16px; font-weight: 700; color: #f1f5f9; line-height: 1; margin-top: 2px; }}
.hero-stat > div:not(.hero-icon) {{ display: flex; flex-direction: column; }}
.hero-edge-great {{ border-left: 3px solid #34d399; }}
.hero-edge-great .hero-value {{ color: #34d399; }}
.hero-edge-good {{ border-left: 3px solid #6ee7b7; }}
.hero-edge-good .hero-value {{ color: #6ee7b7; }}
.hero-edge-ok {{ border-left: 3px solid #fbbf24; }}
.hero-edge-ok .hero-value {{ color: #fbbf24; }}
.hero-gap-perfect {{ border-left: 3px solid #34d399; }}
.hero-gap-perfect .hero-value {{ color: #34d399; }}
.hero-gap-good {{ border-left: 3px solid #6ee7b7; }}
.hero-gap-warn {{ border-left: 3px solid #fbbf24; }}
.hero-gap-warn .hero-value {{ color: #fbbf24; }}
.hero-gap-bad {{ border-left: 3px solid #f87171; }}
.hero-gap-bad .hero-value {{ color: #f87171; }}
.hero-gap-na {{ border-left: 3px solid #64748b; }}

/* Position sizing */
.position-size {{ background: #064e3b; border: 1px solid #10b981; border-radius: 8px; padding: 10px 12px; margin-bottom: 8px; }}
.size-row {{ display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; font-size: 12px; }}
.size-label {{ color: #6ee7b7; font-weight: 600; }}
.size-value {{ color: #f1f5f9; font-weight: 700; font-size: 14px; }}
.size-credit {{ color: #34d399; font-weight: 600; }}
.size-bp {{ font-size: 10px; color: #6ee7b7; margin-top: 4px; opacity: 0.8; }}
.size-skip {{ background: #7f1d1d; border-color: #ef4444; color: #fca5a5; text-align: center; font-size: 12px; font-weight: 600; padding: 8px; }}

/* Compact signals */
.signals-compact {{ font-size: 11px; color: #94a3b8; margin-bottom: 6px; line-height: 1.5; }}

/* Sentiment strip */
.sentiment-strip {{ background: #1e293b; border: 1px solid #334155; border-radius: 8px; padding: 14px 18px; margin-bottom: 14px; }}
.sentiment-title {{ font-size: 11px; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 10px; font-weight: 600; }}
.sentiment-grid {{ display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; margin-bottom: 10px; }}
.sent-item {{ display: flex; align-items: center; gap: 8px; padding: 6px 10px; background: #0f172a; border-radius: 6px; font-size: 12px; }}
.sent-icon {{ font-size: 16px; }}
.sent-name {{ color: #94a3b8; font-size: 10px; text-transform: uppercase; letter-spacing: 0.04em; }}
.sent-value {{ color: #f1f5f9; font-weight: 700; font-size: 13px; }}
.sent-label {{ color: #cbd5e1; font-size: 11px; }}
.sectors-row {{ display: flex; flex-wrap: wrap; gap: 6px; padding-top: 8px; border-top: 1px solid #334155; }}
.sector-chip {{ font-size: 10px; padding: 3px 8px; border-radius: 4px; background: #0f172a; color: #cbd5e1; }}
.sector-up {{ color: #34d399; }}
.sector-down {{ color: #f87171; }}
.sector-flat {{ color: #94a3b8; }}
.pick-fundamentals {{ display: flex; gap: 12px; font-size: 10px; color: #94a3b8; flex-wrap: wrap; margin-bottom: 6px; }}
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
.legend-tag.wl {{ background: #334155; color: #cbd5e1; }}

/* ============================================================
   v18 CARD STYLES
   ============================================================ */
.pick-v18 {{ position: relative; background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%); border: 1px solid #334155; border-radius: 12px; overflow: hidden; display: flex; margin-bottom: 14px; }}
.pick-v18 .tag-side {{ background: #c2410c; color: #fff7ed; writing-mode: vertical-rl; transform: rotate(180deg); padding: 14px 6px; font-size: 12px; font-weight: 500; letter-spacing: 0.15em; display: flex; align-items: center; justify-content: center; min-width: 22px; }}
.pick-v18 .pick-body {{ flex: 1; padding: 16px 18px; min-width: 0; }}

.card-header {{ display: flex; align-items: center; justify-content: space-between; margin-bottom: 14px; gap: 18px; flex-wrap: wrap; }}
.header-left {{ display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }}
.card-ticker {{ color: #c4b5fd; font-size: 26px; font-weight: 500; text-decoration: none; letter-spacing: -0.02em; line-height: 1; }}
.card-ticker:hover {{ color: #ddd6fe; }}
.timing-icon {{ background: #fbbf24; color: #422006; padding: 6px 10px; border-radius: 50%; font-size: 14px; }}
.timing-icon.amc {{ background: #1e3a8a; color: #dbeafe; }}
.timing-icon.tbd {{ background: #475569; color: #cbd5e1; }}

.score-block {{ display: flex; flex-direction: column; align-items: center; gap: 3px; }}
.score-label {{ color: #64748b; font-size: 9px; text-transform: uppercase; font-weight: 500; letter-spacing: 0.04em; }}
.score-claude-label {{ color: #c4b5fd; }}
.bargain-label {{ color: #f9a8d4; }}
.score-badge {{ font-size: 19px; font-weight: 500; padding: 5px 14px; border-radius: 6px; line-height: 1; }}
.score-mine {{ background: #1e293b; border: 1px solid #475569; color: #f1f5f9; }}
.score-claude {{ background: #2e1065; border: 1px solid #7c3aed; color: #ddd6fe; }}
.bargain-badge {{ background: #500724; border: 1px solid #be185d; color: #fce7f3; font-size: 17px; padding: 5px 12px; display: inline-flex; align-items: center; gap: 5px; }}
.score-sep {{ color: #475569; font-size: 16px; }}

.header-right {{ display: flex; flex-direction: column; align-items: flex-end; gap: 2px; }}
.company-name {{ color: #f1f5f9; font-size: 16px; font-weight: 500; }}
.company-price {{ color: #cbd5e1; font-size: 15px; font-weight: 500; }}

.company-claude-row {{ display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 14px; }}
.company-box {{ background: #0c2d1a; border: 1px solid #166534; border-radius: 8px; padding: 12px 14px; }}
.claude-box {{ background: #1e1b4b; border: 1px solid #4c1d95; border-radius: 8px; padding: 12px 14px; }}
.box-label {{ color: #6ee7b7; font-size: 10px; text-transform: uppercase; letter-spacing: 0.06em; font-weight: 600; }}
.claude-box .box-label {{ color: #c4b5fd; }}
.box-label-row {{ display: flex; align-items: center; gap: 6px; margin-bottom: 8px; }}
.claude-c {{ width: 18px; height: 18px; background: #6d28d9; border-radius: 50%; display: flex; align-items: center; justify-content: center; color: #f5f3ff; font-size: 9px; font-weight: 600; }}
.claude-tag {{ font-size: 9px; padding: 1px 5px; border-radius: 3px; font-weight: 500; margin-left: auto; }}
.tag-safe {{ background: #064e3b; color: #6ee7b7; }}
.tag-watch {{ background: #422006; color: #fde68a; }}
.tag-skip {{ background: #7f1d1d; color: #fca5a5; }}
.box-text {{ color: #d1fae5; font-size: 12px; line-height: 1.55; margin: 8px 0 10px 0; }}
.fund-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 6px; padding-top: 8px; border-top: 1px dashed #166534; }}
.fund-check {{ display: flex; align-items: center; gap: 5px; color: #d1fae5; font-size: 10px; }}
.fund-check.fund-warn {{ color: #fde68a; }}
.fund-check.fund-bad {{ color: #fca5a5; }}
.fund-check.fund-na {{ color: #94a3b8; }}
.fund-tick {{ color: #34d399; font-size: 11px; }}
.fund-warn .fund-tilde {{ color: #fbbf24; font-size: 11px; }}
.fund-bad .fund-cross {{ color: #f87171; font-size: 11px; }}
.fund-na .fund-tilde {{ color: #64748b; font-size: 11px; }}
.claude-bullets {{ display: flex; flex-direction: column; gap: 4px; }}
.claude-bullet {{ display: flex; gap: 7px; align-items: flex-start; font-size: 12px; line-height: 1.45; }}
.claude-bullet > span:first-child {{ font-size: 13px; line-height: 1; margin-top: 2px; }}
.bullet-good > span:first-child {{ color: #34d399; }} .bullet-good > span:last-child {{ color: #d1fae5; }}
.bullet-warn > span:first-child {{ color: #fbbf24; }} .bullet-warn > span:last-child {{ color: #fde68a; }}
.bullet-bad > span:first-child {{ color: #f87171; }} .bullet-bad > span:last-child {{ color: #fecaca; }}

.put-row {{ display: grid; grid-template-columns: 110px 80px 1fr 50px 70px; gap: 10px; align-items: center; background: #0f172a; border: 1px solid #4c1d95; border-radius: 8px; padding: 14px 16px; margin-bottom: 8px; }}
.put-row-empty {{ display: block; color: #94a3b8; font-size: 12px; text-align: center; padding: 12px; }}
.put-tag {{ background: #2e1065; color: #ddd6fe; font-size: 10px; font-weight: 500; padding: 4px 8px; border-radius: 4px; letter-spacing: 0.06em; text-align: center; }}
.put-strike {{ color: #60a5fa; font-size: 22px; font-weight: 500; letter-spacing: -0.015em; }}
.put-meta {{ color: #cbd5e1; font-size: 13px; font-weight: 400; }}
.put-qty {{ color: #fbbf24; font-size: 15px; font-weight: 500; text-align: center; }}
.put-credit {{ color: #34d399; font-size: 16px; font-weight: 500; text-align: right; }}

.pick-v18 .hero-row {{ display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin: 14px 0; }}
.pick-v18 .hero-stat {{ background: #0f172a; border: 1px solid #34d399; border-radius: 8px; padding: 12px 14px; display: flex; align-items: center; gap: 12px; }}
.pick-v18 .hero-stat .hero-icon {{ width: 30px; height: 30px; background: #064e3b; border-radius: 6px; display: flex; align-items: center; justify-content: center; font-size: 16px; }}
.pick-v18 .hero-stat .hero-label {{ color: #6ee7b7; font-size: 9px; text-transform: uppercase; font-weight: 500; }}
.pick-v18 .hero-stat .hero-value {{ color: #34d399; font-size: 22px; font-weight: 500; }}
.pick-v18 .hero-edge-good {{ border-color: #34d399; }}
.pick-v18 .hero-edge-ok {{ border-color: #fbbf24; }} .hero-edge-ok .hero-value {{ color: #fbbf24; }}
.pick-v18 .hero-gap-warn {{ border-color: #fbbf24; }} .hero-gap-warn .hero-value {{ color: #fbbf24; }}
.pick-v18 .hero-gap-na {{ border-color: #475569; }} .hero-gap-na .hero-value {{ color: #94a3b8; }}

.chart-indicators-row {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 12px; }}
.chart-news-col {{ display: flex; flex-direction: column; gap: 12px; }}
.chart-svg {{ width: 100%; height: auto; max-height: 260px; background: #0f172a; border: 1px solid #1e293b; border-radius: 6px; display: block; }}
.chart-empty {{ background: #0f172a; border: 1px solid #1e293b; border-radius: 6px; padding: 60px; text-align: center; color: #64748b; font-size: 12px; }}
.news-box {{ background: #082f49; border: 1px solid #1e40af; border-radius: 8px; padding: 12px 14px; }}
.news-box .box-label {{ color: #93c5fd; }}
.news-list {{ display: flex; flex-direction: column; gap: 6px; margin-top: 8px; }}
.news-item {{ display: flex; gap: 8px; align-items: baseline; }}
.news-icon {{ font-size: 11px; }}
.news-pos {{ color: #34d399; }}
.news-neg {{ color: #f87171; }}
.news-neu {{ color: #94a3b8; }}
.news-title {{ color: #cbd5e1; font-size: 12px; line-height: 1.4; flex: 1; }}
.news-date {{ color: #64748b; font-size: 9px; white-space: nowrap; }}
.news-empty {{ color: #64748b; font-size: 11px; padding: 8px; text-align: center; }}

.indicators-col {{ background: #0f172a; border: 1px solid #1e293b; border-radius: 8px; padding: 12px 14px; display: flex; flex-direction: column; gap: 10px; }}
.ind-block {{ }}
.ind-label {{ color: #64748b; font-size: 9px; text-transform: uppercase; font-weight: 500; }}
.ind-label-row {{ display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 5px; }}
.ind-val {{ color: #cbd5e1; font-size: 10px; font-weight: 500; }}

.bar-52w {{ position: relative; height: 6px; background: #1e293b; border-radius: 3px; margin-top: 5px; }}
.bar-52w .bar-fill {{ position: absolute; left: 0; top: 0; height: 100%; width: 100%; background: linear-gradient(90deg, #f87171 0%, #fbbf24 50%, #34d399 100%); opacity: 0.35; border-radius: 3px; }}
.bar-marker {{ position: absolute; top: -2px; width: 8px; height: 10px; background: #f1f5f9; border-radius: 2px; }}
.bar-marker-tall {{ top: 4px; bottom: 4px; height: auto; }}
.bar-labels {{ display: flex; justify-content: space-between; margin-top: 4px; font-size: 10px; color: #94a3b8; }}
.bar-labels .bar-current {{ color: #f1f5f9; font-weight: 500; }}

.bar-rsi {{ position: relative; height: 6px; background: #1e293b; border-radius: 3px; margin-top: 5px; }}
.bar-rsi-low {{ position: absolute; left: 0; top: 0; height: 100%; width: 30%; background: #f87171; opacity: 0.4; border-radius: 3px 0 0 3px; }}
.bar-rsi-mid {{ position: absolute; left: 30%; top: 0; height: 100%; width: 40%; background: #34d399; opacity: 0.3; }}
.bar-rsi-high {{ position: absolute; left: 70%; top: 0; height: 100%; width: 30%; background: #f87171; opacity: 0.4; border-radius: 0 3px 3px 0; }}

.bar-dma {{ position: relative; height: 6px; background: #1e293b; border-radius: 3px; }}
.bar-dma-fill {{ position: absolute; top: 0; height: 100%; opacity: 0.5; }}
.bar-dma-center {{ position: absolute; left: 50%; top: -3px; width: 1px; height: 12px; background: #475569; }}

.bar-bollinger {{ position: relative; height: 22px; background: #0c1929; border-radius: 3px; overflow: hidden; margin-top: 5px; }}
.bar-bb-cheap {{ position: absolute; left: 0; top: 0; bottom: 0; width: 25%; background: #f87171; opacity: 0.18; }}
.bar-bb-normal {{ position: absolute; left: 25%; top: 0; bottom: 0; width: 50%; background: #34d399; opacity: 0.15; }}
.bar-bb-expensive {{ position: absolute; right: 0; top: 0; bottom: 0; width: 25%; background: #f87171; opacity: 0.18; }}
.bar-bb-center-l {{ position: absolute; left: 25%; top: 2px; bottom: 2px; width: 1px; background: #475569; }}
.bar-bb-center-r {{ position: absolute; right: 25%; top: 2px; bottom: 2px; width: 1px; background: #475569; }}
.bar-bb-labels {{ display: flex; justify-content: space-between; margin-top: 3px; font-size: 8px; }}
.bb-cheap {{ color: #fca5a5; }} .bb-normal {{ color: #6ee7b7; }} .bb-expensive {{ color: #fca5a5; }}

.ladder {{ display: flex; flex-direction: column; gap: 7px; padding: 8px 0; margin-top: 4px; }}
.ladder-row {{ display: grid; grid-template-columns: 70px 14px 1fr; gap: 10px; align-items: center; }}
.ladder-label {{ text-align: right; font-size: 9px; text-transform: uppercase; }}
.ladder-dot {{ width: 10px; height: 10px; border-radius: 50%; }}
.ladder-value {{ font-size: 11px; font-weight: 500; }}
.ladder-pct {{ color: #94a3b8; font-size: 10px; }}
.stress-test {{ margin-top: 12px; padding: 8px 10px; background: #2e1065; border-left: 2px solid #a855f7; border-radius: 4px; color: #e9d5ff; font-size: 10px; line-height: 1.5; }}
.stress-test strong {{ color: #f5f3ff; }}

.bar-atr {{ position: relative; height: 16px; background: #0c1929; border-radius: 3px; }}
.bar-atr-bad {{ position: absolute; left: 0; top: 0; bottom: 0; width: 28%; background: #f87171; opacity: 0.2; }}
.bar-atr-warn {{ position: absolute; left: 28%; top: 0; bottom: 0; width: 28%; background: #fbbf24; opacity: 0.18; }}
.bar-atr-good {{ position: absolute; left: 56%; top: 0; bottom: 0; width: 44%; background: #34d399; opacity: 0.18; }}
.bar-atr-labels {{ display: flex; justify-content: space-between; margin-top: 3px; font-size: 8px; }}
.atr-note {{ color: #94a3b8; font-size: 9px; margin-top: 3px; }}

.ind-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 6px; padding-top: 8px; border-top: 1px dashed #1e293b; }}
.ind-mini {{ background: #0c1929; border-radius: 5px; padding: 6px 8px; }}
.mini-label {{ color: #64748b; font-size: 8px; text-transform: uppercase; font-weight: 500; margin-bottom: 2px; }}
.mini-val {{ color: #cbd5e1; font-size: 12px; font-weight: 500; }}

.signals-row {{ display: flex; align-items: center; gap: 16px; padding: 8px 14px; background: #0c1929; border: 1px dashed #1e293b; border-radius: 6px; font-size: 11px; color: #cbd5e1; margin-bottom: 10px; flex-wrap: wrap; }}
.sig-chip {{ display: flex; align-items: center; gap: 5px; }}
.sig-dot {{ color: #34d399; }}

.card-footer {{ display: flex; align-items: center; justify-content: space-between; padding-top: 12px; border-top: 1px solid #1e293b; flex-wrap: wrap; gap: 8px; }}
.card-footer .fire-time {{ color: #fb923c; font-size: 13px; font-weight: 500; background: none; padding: 0; }}
.verify-links {{ color: #64748b; font-size: 11px; }}
.verify-links a {{ color: #60a5fa; text-decoration: none; }}
.verify-links a:hover {{ color: #93c5fd; text-decoration: underline; }}

@media (max-width: 720px) {{
  .company-claude-row {{ grid-template-columns: 1fr; }}
  .chart-indicators-row {{ grid-template-columns: 1fr; }}
  .put-row {{ grid-template-columns: 1fr; gap: 6px; text-align: left; }}
  .put-row > * {{ text-align: left !important; }}
}}
/* ============================================================
   v18 CARD STYLES — comprehensive overrides
   ============================================================ */
.pick-v18 {{ position: relative; background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%); border: 1px solid #334155; border-radius: 12px; overflow: hidden; display: flex; margin-bottom: 12px; }}
.pick-v18 .tag-side {{ background: #c2410c; color: #fff7ed; writing-mode: vertical-rl; transform: rotate(180deg); padding: 14px 6px; font-size: 12px; font-weight: 500; letter-spacing: 0.15em; display: flex; align-items: center; justify-content: center; flex-shrink: 0; }}
.pick-v18 .pick-body {{ flex: 1; padding: 16px 18px; min-width: 0; }}

/* Card header */
.pick-v18 .card-header {{ display: flex; align-items: center; justify-content: space-between; margin-bottom: 14px; gap: 18px; flex-wrap: wrap; }}
.pick-v18 .header-left {{ display: flex; align-items: center; gap: 18px; flex-wrap: wrap; }}
.pick-v18 .header-right {{ display: flex; flex-direction: column; align-items: flex-end; gap: 2px; }}
.pick-v18 .card-ticker {{ color: #c4b5fd; font-size: 26px; font-weight: 500; text-decoration: none; letter-spacing: -0.02em; line-height: 1; }}
.pick-v18 .card-ticker:hover {{ text-decoration: underline; }}
.pick-v18 .timing-icon {{ background: #fbbf24; color: #422006; padding: 4px 8px; border-radius: 50%; font-size: 14px; display: inline-flex; align-items: center; justify-content: center; width: 28px; height: 28px; }}
.pick-v18 .timing-icon.amc {{ background: #6d28d9; color: #ddd6fe; }}
.pick-v18 .timing-icon.tbd {{ background: #475569; color: #cbd5e1; }}
.pick-v18 .score-block {{ display: flex; flex-direction: column; align-items: center; gap: 3px; }}
.pick-v18 .score-label {{ font-size: 9px; text-transform: uppercase; font-weight: 500; letter-spacing: 0.04em; color: #64748b; }}
.pick-v18 .score-claude-label {{ color: #c4b5fd; }}
.pick-v18 .bargain-label {{ color: #f9a8d4; }}
.pick-v18 .score-badge {{ font-size: 19px; font-weight: 500; padding: 5px 14px; border-radius: 6px; line-height: 1; display: inline-flex; align-items: center; }}
.pick-v18 .score-mine {{ background: #1e293b; border: 1px solid #475569; color: #f1f5f9; }}
.pick-v18 .score-claude {{ background: #2e1065; border: 1px solid #7c3aed; color: #ddd6fe; }}
.pick-v18 .bargain-badge {{ background: #500724; border: 1px solid #be185d; color: #fce7f3; font-size: 17px; padding: 5px 12px; gap: 5px; }}
.pick-v18 .score-sep {{ color: #475569; font-size: 16px; }}
.pick-v18 .company-name {{ color: #f1f5f9; font-size: 16px; font-weight: 500; }}
.pick-v18 .company-price {{ color: #cbd5e1; font-size: 15px; font-weight: 500; }}

/* Company + Claude row */
.pick-v18 .company-claude-row {{ display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 14px; }}
.pick-v18 .company-box {{ background: #0c2d1a; border: 1px solid #166534; border-radius: 8px; padding: 12px 14px; }}
.pick-v18 .company-box .box-label {{ color: #6ee7b7; }}
.pick-v18 .claude-box {{ background: #1e1b4b; border: 1px solid #4c1d95; border-radius: 8px; padding: 12px 14px; }}
.pick-v18 .claude-box .box-label {{ color: #c4b5fd; }}
.pick-v18 .box-label {{ font-size: 10px; text-transform: uppercase; letter-spacing: 0.06em; font-weight: 600; }}
.pick-v18 .box-label-row {{ display: flex; align-items: center; gap: 6px; margin-bottom: 8px; }}
.pick-v18 .box-text {{ color: #d1fae5; font-size: 12px; line-height: 1.55; margin: 8px 0 10px 0; }}
.pick-v18 .claude-c {{ width: 18px; height: 18px; background: #6d28d9; border-radius: 50%; display: inline-flex; align-items: center; justify-content: center; color: #f5f3ff; font-size: 9px; font-weight: 600; flex-shrink: 0; }}
.pick-v18 .claude-tag {{ font-size: 9px; padding: 2px 6px; border-radius: 3px; font-weight: 500; margin-left: auto; }}
.pick-v18 .tag-safe {{ background: #064e3b; color: #6ee7b7; }}
.pick-v18 .tag-watch {{ background: #78350f; color: #fbbf24; }}
.pick-v18 .tag-skip {{ background: #7f1d1d; color: #fca5a5; }}
.pick-v18 .claude-bullets {{ display: flex; flex-direction: column; gap: 4px; }}
.pick-v18 .claude-bullet {{ display: flex; gap: 7px; align-items: flex-start; font-size: 12px; line-height: 1.45; }}
.pick-v18 .bullet-good > span:first-child {{ color: #34d399; }}
.pick-v18 .bullet-good > span:last-child {{ color: #d1fae5; }}
.pick-v18 .bullet-warn > span:first-child {{ color: #fbbf24; }}
.pick-v18 .bullet-warn > span:last-child {{ color: #fde68a; }}
.pick-v18 .bullet-bad > span:first-child {{ color: #f87171; }}
.pick-v18 .bullet-bad > span:last-child {{ color: #fecaca; }}

/* Fundamentals */
.pick-v18 .fund-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 6px; padding-top: 8px; border-top: 1px dashed #166534; margin-top: 8px; }}
.pick-v18 .fund-check {{ display: flex; align-items: center; gap: 5px; font-size: 10px; color: #d1fae5; }}
.pick-v18 .fund-tick {{ color: #34d399; font-size: 11px; }}
.pick-v18 .fund-tilde {{ color: #fbbf24; font-size: 11px; }}
.pick-v18 .fund-cross {{ color: #f87171; font-size: 11px; }}
.pick-v18 .fund-warn {{ color: #fde68a; }}
.pick-v18 .fund-bad {{ color: #fca5a5; }}
.pick-v18 .fund-na {{ color: #94a3b8; }}

/* Put rows — INLINE GRID */
.pick-v18 .put-row {{ display: grid; grid-template-columns: 110px 80px 1fr 50px 70px; gap: 10px; align-items: center; background: #0f172a; border: 1px solid #4c1d95; border-radius: 8px; padding: 14px 16px; margin-bottom: 8px; }}
.pick-v18 .put-row-empty {{ display: block; text-align: center; color: #64748b; font-size: 12px; padding: 10px; }}
.pick-v18 .put-tag {{ background: #2e1065; color: #ddd6fe; font-size: 10px; font-weight: 500; padding: 4px 8px; border-radius: 4px; letter-spacing: 0.06em; text-align: center; line-height: 1.2; }}
.pick-v18 .put-strike {{ color: #60a5fa; font-size: 22px; font-weight: 500; letter-spacing: -0.015em; }}
.pick-v18 .put-meta {{ color: #cbd5e1; font-size: 13px; font-weight: 400; }}
.pick-v18 .put-qty {{ color: #fbbf24; font-size: 15px; font-weight: 500; text-align: center; }}
.pick-v18 .put-credit {{ color: #34d399; font-size: 16px; font-weight: 500; text-align: right; }}

/* Hero stats */
.pick-v18 .hero-row {{ display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin: 14px 0; }}
.pick-v18 .hero-stat {{ background: #0f172a; border: 1px solid #34d399; border-radius: 8px; padding: 12px 14px; display: flex; align-items: center; gap: 12px; }}
.pick-v18 .hero-icon {{ width: 30px; height: 30px; background: #064e3b; border-radius: 6px; display: flex; align-items: center; justify-content: center; font-size: 16px; flex-shrink: 0; }}
.pick-v18 .hero-stat > div:not(.hero-icon) {{ display: flex; flex-direction: column; gap: 0; }}
.pick-v18 .hero-stat .hero-label {{ font-size: 9px; color: #6ee7b7; text-transform: uppercase; letter-spacing: 0.04em; font-weight: 500; }}
.pick-v18 .hero-stat .hero-value {{ font-size: 22px; font-weight: 500; color: #34d399; line-height: 1.1; }}
.pick-v18 .hero-edge-good {{ border-color: #6ee7b7; }}
.pick-v18 .hero-edge-ok {{ border-color: #fbbf24; }}
.pick-v18 .hero-edge-ok .hero-value {{ color: #fbbf24; }}
.pick-v18 .hero-gap-warn {{ border-color: #fbbf24; }}
.pick-v18 .hero-gap-warn .hero-value {{ color: #fbbf24; }}
.pick-v18 .hero-gap-bad {{ border-color: #f87171; }}
.pick-v18 .hero-gap-bad .hero-value {{ color: #f87171; }}
.pick-v18 .hero-gap-na {{ border-color: #475569; }}
.pick-v18 .hero-gap-na .hero-value {{ color: #94a3b8; }}

/* Chart + indicators row */
.pick-v18 .chart-indicators-row {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 12px; }}
.pick-v18 .chart-news-col {{ display: flex; flex-direction: column; gap: 12px; min-width: 0; }}
.pick-v18 svg.chart-svg {{ width: 100%; height: auto; max-height: 260px; background: #0f172a; border: 1px solid #1e293b; border-radius: 6px; display: block; }}
.pick-v18 .chart-empty {{ background: #0f172a; border: 1px solid #1e293b; border-radius: 6px; padding: 60px; text-align: center; color: #64748b; font-size: 12px; }}

/* News */
.pick-v18 .news-box {{ background: #082f49; border: 1px solid #1e40af; border-radius: 8px; padding: 12px 14px; }}
.pick-v18 .news-box .box-label {{ color: #93c5fd; margin-bottom: 8px; display: inline-block; }}
.pick-v18 .news-list {{ display: flex; flex-direction: column; gap: 6px; }}
.pick-v18 .news-item {{ display: flex; gap: 8px; align-items: baseline; font-size: 11px; line-height: 1.4; }}
.pick-v18 .news-icon {{ font-size: 11px; flex-shrink: 0; }}
.pick-v18 .news-pos {{ color: #34d399; }}
.pick-v18 .news-neg {{ color: #f87171; }}
.pick-v18 .news-neu {{ color: #94a3b8; }}
.pick-v18 .news-title {{ color: #cbd5e1; flex: 1; }}
.pick-v18 .news-title-link {{ color: #cbd5e1; flex: 1; text-decoration: none; }}
.pick-v18 .news-title-link:hover {{ color: #f1f5f9; text-decoration: underline; }}
.pick-v18 .news-date {{ color: #64748b; font-size: 9px; white-space: nowrap; flex-shrink: 0; }}
.pick-v18 .news-empty {{ color: #64748b; font-size: 11px; font-style: italic; }}

/* Indicator panel (right column) */
.pick-v18 .indicators-col {{ background: #0f172a; border: 1px solid #1e293b; border-radius: 8px; padding: 12px 14px; display: flex; flex-direction: column; gap: 10px; min-width: 0; }}
.pick-v18 .ind-block {{ }}
.pick-v18 .ind-label {{ color: #64748b; font-size: 9px; text-transform: uppercase; font-weight: 500; letter-spacing: 0.04em; }}
.pick-v18 .ind-label-row {{ display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 5px; }}
.pick-v18 .ind-val {{ color: #cbd5e1; font-size: 10px; font-weight: 500; }}

/* 52w bar */
.pick-v18 .bar-52w {{ position: relative; height: 6px; background: #1e293b; border-radius: 3px; margin-top: 5px; }}
.pick-v18 .bar-fill {{ position: absolute; left: 0; top: 0; height: 100%; width: 100%; background: linear-gradient(90deg, #f87171 0%, #fbbf24 50%, #34d399 100%); opacity: 0.35; border-radius: 3px; }}
.pick-v18 .bar-labels {{ display: flex; justify-content: space-between; margin-top: 4px; font-size: 10px; color: #94a3b8; }}
.pick-v18 .bar-current {{ color: #f1f5f9; font-weight: 500; }}

/* Markers (universal) */
.pick-v18 .bar-marker {{ position: absolute; top: -2px; width: 8px; height: 10px; background: #f1f5f9; border-radius: 2px; z-index: 2; }}
.pick-v18 .bar-marker-tall {{ top: 4px; bottom: 4px; height: auto; }}

/* Support floors LADDER (clean, no lines through numbers) */
.pick-v18 .ladder {{ display: flex; flex-direction: column; gap: 7px; padding: 8px 0; margin-top: 4px; }}
.pick-v18 .ladder-row {{ display: grid; grid-template-columns: 60px 14px 1fr; gap: 10px; align-items: center; }}
.pick-v18 .ladder-label {{ text-align: right; font-size: 9px; text-transform: uppercase; font-weight: 500; }}
.pick-v18 .ladder-dot {{ width: 10px; height: 10px; border-radius: 50%; }}
.pick-v18 .ladder-value {{ font-size: 11px; font-weight: 500; }}
.pick-v18 .ladder-pct {{ color: #94a3b8; font-size: 10px; font-weight: 400; }}
.pick-v18 .stress-test {{ margin-top: 10px; padding: 8px 10px; background: #2e1065; border-left: 2px solid #a855f7; border-radius: 4px; color: #e9d5ff; font-size: 10px; line-height: 1.5; }}
.pick-v18 .stress-test strong {{ color: #f5f3ff; }}

/* 2x2 grid */
.pick-v18 .ind-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 6px; padding-top: 8px; border-top: 1px dashed #1e293b; }}
.pick-v18 .ind-mini {{ background: #0c1929; border-radius: 5px; padding: 6px 8px; }}
.pick-v18 .mini-label {{ color: #64748b; font-size: 8px; text-transform: uppercase; font-weight: 500; margin-bottom: 2px; }}
.pick-v18 .mini-val {{ color: #cbd5e1; font-size: 12px; font-weight: 500; }}

/* Signals row */
.pick-v18 .signals-row {{ display: flex; align-items: center; gap: 16px; padding: 8px 14px; background: #0c1929; border: 1px dashed #1e293b; border-radius: 6px; font-size: 11px; color: #cbd5e1; margin-bottom: 10px; flex-wrap: wrap; }}
.pick-v18 .sig-chip {{ display: inline-flex; align-items: center; gap: 5px; }}
.pick-v18 .sig-dot {{ color: #34d399; }}

/* Card footer */
.pick-v18 .card-footer {{ display: flex; align-items: center; justify-content: space-between; padding-top: 12px; border-top: 1px solid #1e293b; flex-wrap: wrap; gap: 8px; }}
.pick-v18 .card-footer .fire-time {{ color: #fb923c; font-size: 13px; font-weight: 500; padding: 0; background: none; }}
.pick-v18 .verify-links {{ color: #64748b; font-size: 11px; }}
.pick-v18 .verify-links a {{ color: #60a5fa; text-decoration: none; }}
.pick-v18 .verify-links a:hover {{ text-decoration: underline; }}
.pick-v18 .fire-warning {{ background: #78350f; border: 1px solid #f59e0b; color: #fbbf24; padding: 6px 10px; border-radius: 4px; margin-bottom: 8px; font-size: 11px; font-weight: 500; }}

/* Mobile responsive */
@media (max-width: 720px) {{
  .pick-v18 .company-claude-row,
  .pick-v18 .hero-row,
  .pick-v18 .chart-indicators-row {{ grid-template-columns: 1fr; }}
  .pick-v18 .put-row {{ grid-template-columns: 1fr; gap: 6px; }}
  .pick-v18 .put-credit {{ text-align: left; }}
  .pick-v18 .put-qty {{ text-align: left; }}
}}
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
    {sentiment_html}
    {day_sections}
    
    <div class="legend">
        <strong>Tags:</strong>
        <span class="legend-tag qw">QW</span> Quality Wheel · 
        <span class="legend-tag ph">PH</span> Premium Hunt · 
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
    print(f"Premium Hunter v6 — scanning {len(WATCHLIST)} tickers...")
    print(f"Looking for earnings in next {MAX_DAYS_TO_EARNINGS} days\n")
    
    print("Pulling market dashboard...")
    dashboard = get_market_dashboard()
    vix_val = dashboard.get('VIX', {}).get('value')
    print(f"  VIX: {vix_val}")
    
    sentiment = get_sentiment_pack()
    
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
            # Add suggested position size
            es = d.get('earnings_stats') or {}
            pt = d.get('put_trade') or {}
            d['suggested_size'] = suggest_position_size(
                d.get('score', 0),
                es.get('red_x_count'),
                caution.get('mode'),
                pt.get('strike'),
            )
            results.append(d)
    
    print(f"\nFound {len(results)} stocks with upcoming earnings.")
    
    # Claude second-opinion scoring
    score_picks(results)
    
    scan_date = datetime.now().strftime('%A, %B %d, %Y')
    html = render_html(results, scan_date, dashboard, economic_events, caution, sentiment)
    
    out_path = Path('report.html')
    out_path.write_text(html)
    print(f"Report saved to: {out_path.absolute()}")
    
    json_path = Path('scan_results.json')
    json_path.write_text(json.dumps(results, default=str, indent=2))
    print(f"Raw data saved to: {json_path.absolute()}")
    
    return results


if __name__ == '__main__':
    main()
