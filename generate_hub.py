"""   
Generate the website hub page (docs/index.html).
Shows the latest report + archive of past 14 days.
"""

import re
from datetime import datetime
from pathlib import Path

DOCS_DIR = Path('docs')


def get_report_meta(report_path):
    if not report_path.exists():
        return None
    content = report_path.read_text()
    
    mode_match = re.search(r'caution-mode">[^<]*🚦\s*([^<]+)</span>', content)
    mode = mode_match.group(1).strip() if mode_match else 'UNKNOWN'
    
    qw = len(re.findall(r'<div class="tag qw">', content))
    ph = len(re.findall(r'<div class="tag ph">', content))
    wl = len(re.findall(r'<div class="tag wl">', content))
    
    vix_match = re.search(r'>VIX</span><span class="dash-value">([^<]+)</span>', content)
    vix = vix_match.group(1).strip() if vix_match else None
    
    spy_match = re.search(r'>SPY</span><span class="dash-value">([^<]+)</span>', content)
    spy = spy_match.group(1).strip() if spy_match else None
    
    updated = datetime.fromtimestamp(report_path.stat().st_mtime)
    
    return {
        'mode': mode,
        'qw': qw, 'ph': ph, 'wl': wl,
        'total_top': qw + ph,
        'vix': vix, 'spy': spy,
        'updated_str': updated.strftime('%a %b %d · %I:%M %p UTC'),
    }


def list_archives(archive_dir, n=14):
    if not archive_dir.exists():
        return []
    files = sorted(archive_dir.glob('*.html'), reverse=True)
    archives = []
    for f in files[:n]:
        try:
            date_obj = datetime.strptime(f.stem, '%Y-%m-%d')
            pretty = date_obj.strftime('%a %b %d, %Y')
        except:
            pretty = f.stem
        archives.append({'name': f.name, 'pretty': pretty})
    return archives


def mode_class(mode):
    m = mode.upper()
    if 'CALM' in m or 'NORMAL' in m: return 'mode-good'
    if 'CAUTIOUS' in m: return 'mode-warn'
    return 'mode-bad'


def main():
    latest_meta = get_report_meta(DOCS_DIR / 'latest.html')
    archives = list_archives(DOCS_DIR / 'archive')
    
    if latest_meta:
        bp = []
        if latest_meta['qw']: bp.append(f'<span class="b-qw">{latest_meta["qw"]} QW</span>')
        if latest_meta['ph']: bp.append(f'<span class="b-ph">{latest_meta["ph"]} PH</span>')
        if latest_meta['wl']: bp.append(f'<span class="b-wl">{latest_meta["wl"]} WL</span>')
        breakdown = ' · '.join(bp) if bp else 'No picks'
        
        market_strip = ''
        if latest_meta['vix'] or latest_meta['spy']:
            parts = []
            if latest_meta['vix']: parts.append(f'VIX <strong>{latest_meta["vix"]}</strong>')
            if latest_meta['spy']: parts.append(f'SPY <strong>{latest_meta["spy"]}</strong>')
            market_strip = f'<div class="market-strip">{" · ".join(parts)}</div>'
        
        hero_html = f"""
        <a href="latest.html" class="hero-card">
            <div class="hero-header">
                <div><div class="hero-emoji">📊</div><h2>Latest Report</h2></div>
                <div class="hero-mode {mode_class(latest_meta['mode'])}">🚦 {latest_meta['mode']}</div>
            </div>
            <div class="hero-stats">
                <div class="stat-big">{latest_meta['total_top']}</div>
                <div class="stat-label">Top Picks</div>
            </div>
            <div class="stat-breakdown">{breakdown}</div>
            {market_strip}
            <div class="hero-footer">
                <span class="hero-updated">{latest_meta['updated_str']}</span>
                <span class="hero-cta">Open Report →</span>
            </div>
        </a>
        """
    else:
        hero_html = '<div class="hero-card hero-empty"><div class="hero-emoji">⏳</div><h2>Waiting for first run</h2><p>Report will appear here once scanner runs.</p></div>'
    
    archive_html = ''
    if archives:
        items = ''.join(f'<a href="archive/{a["name"]}" class="archive-item">{a["pretty"]}</a>' for a in archives)
        archive_html = f'<section class="archives"><h3>📁 Archive — Last {len(archives)} reports</h3><div class="archive-list">{items}</div></section>'
    
    now_str = datetime.now().strftime('%a %b %d, %Y · %I:%M %p')
    
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="theme-color" content="#0f172a">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="Premium Hunter">
<title>Premium Hunter</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22><text y=%2280%22 font-size=%2280%22>🎯</text></svg>">
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "Inter", sans-serif; background: #0f172a; color: #e2e8f0; line-height: 1.5; min-height: 100vh; padding: 24px 16px; -webkit-font-smoothing: antialiased; }}
.container {{ max-width: 760px; margin: 0 auto; }}
header {{ text-align: center; margin-bottom: 28px; padding-bottom: 20px; border-bottom: 1px solid #334155; }}
h1 {{ font-size: 32px; font-weight: 700; color: #f1f5f9; letter-spacing: -0.02em; margin-bottom: 6px; }}
.subtitle {{ color: #94a3b8; font-size: 13px; }}
.hero-card {{ display: block; background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%); border: 1px solid #334155; border-left: 4px solid #3b82f6; border-radius: 14px; padding: 26px; text-decoration: none; color: inherit; transition: transform 0.15s, border-color 0.15s, box-shadow 0.15s; margin-bottom: 28px; }}
.hero-card:hover {{ transform: translateY(-3px); border-color: #3b82f6; box-shadow: 0 12px 32px rgba(59, 130, 246, 0.15); }}
.hero-empty {{ border-left-color: #64748b; text-align: center; }}
.hero-empty .hero-emoji {{ font-size: 48px; margin-bottom: 12px; }}
.hero-empty p {{ color: #94a3b8; font-size: 13px; margin-top: 8px; }}
.hero-header {{ display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 22px; flex-wrap: wrap; }}
.hero-emoji {{ font-size: 32px; margin-bottom: 4px; }}
.hero-card h2 {{ font-size: 18px; font-weight: 700; color: #f1f5f9; }}
.hero-mode {{ font-size: 12px; font-weight: 700; padding: 6px 12px; border-radius: 6px; letter-spacing: 0.04em; white-space: nowrap; }}
.mode-good {{ background: #064e3b; color: #6ee7b7; }}
.mode-warn {{ background: #78350f; color: #fbbf24; }}
.mode-bad {{ background: #7f1d1d; color: #fca5a5; }}
.hero-stats {{ display: flex; align-items: baseline; gap: 12px; margin-bottom: 8px; }}
.stat-big {{ font-size: 56px; font-weight: 700; color: #f1f5f9; line-height: 1; letter-spacing: -0.02em; }}
.stat-label {{ color: #94a3b8; font-size: 14px; }}
.stat-breakdown {{ font-size: 12px; color: #94a3b8; margin-bottom: 16px; }}
.stat-breakdown span {{ margin-right: 6px; padding: 2px 7px; border-radius: 4px; font-weight: 600; }}
.b-qw {{ background: #1e3a8a; color: #dbeafe; }}
.b-ph {{ background: #7c2d12; color: #fed7aa; }}
.b-wl {{ background: #334155; color: #cbd5e1; }}
.market-strip {{ font-size: 12px; color: #cbd5e1; padding: 10px 14px; background: #0f172a; border-radius: 8px; margin-bottom: 16px; }}
.market-strip strong {{ color: #f1f5f9; }}
.hero-footer {{ display: flex; justify-content: space-between; align-items: center; border-top: 1px solid #334155; padding-top: 14px; flex-wrap: wrap; gap: 8px; }}
.hero-updated {{ font-size: 11px; color: #64748b; }}
.hero-cta {{ font-size: 14px; color: #60a5fa; font-weight: 600; }}
.archives {{ background: #1e293b; border: 1px solid #334155; border-radius: 12px; padding: 20px; }}
.archives h3 {{ font-size: 14px; font-weight: 600; color: #f1f5f9; margin-bottom: 14px; }}
.archive-list {{ display: flex; flex-direction: column; gap: 4px; }}
.archive-item {{ display: block; padding: 10px 12px; background: #0f172a; border-radius: 6px; color: #cbd5e1; text-decoration: none; font-size: 13px; transition: background 0.1s, color 0.1s; border: 1px solid transparent; }}
.archive-item:hover {{ background: #334155; color: #f1f5f9; border-color: #475569; }}
footer {{ text-align: center; margin-top: 32px; color: #64748b; font-size: 11px; }}
@media (max-width: 600px) {{ body {{ padding: 16px 12px; }} h1 {{ font-size: 26px; }} .hero-card {{ padding: 20px; }} .stat-big {{ font-size: 44px; }} }}
</style>
</head>
<body>
<div class="container">
    <header>
        <h1>🎯 Premium Hunter</h1>
        <div class="subtitle">Earnings IV-crush scanner · Updated {now_str}</div>
    </header>
    {hero_html}
    {archive_html}
    <footer>Auto-updates after each scan · Add to home screen for app-like access</footer>
</div>
</body>
</html>"""
    
    output_path = DOCS_DIR / 'index.html'
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html)
    print(f"✅ Generated {output_path}")
    print(f"   Latest: {'✓' if latest_meta else '✗'}, Archive: {len(archives)} files")


if __name__ == '__main__':
    main()
