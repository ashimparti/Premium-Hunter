# Premium Hunter

Daily earnings IV crush scanner. Scores stocks against your 5-section checklist and outputs a ranked HTML report.

## What it does

- Scans ~80 quality optionable stocks
- Filters to those with earnings in the next 14 days
- For each stock, pulls:
  - Quality: market cap, sector, analyst recommendation, target price
  - Valuation: PEG, P/E, upside %
  - Earnings edge: expected move (from straddle), avg actual move (last 8 quarters), gap risk count
  - Suggested trade: ~5-7 delta put around 8 months out (Dec/Jan LEAP)
- Scores against checklist, sorts by score + edge ratio
- Outputs ranked HTML report

## Setup (one-time)

```bash
# Install dependencies
pip install yfinance pandas numpy scipy

# Or with break-system-packages on newer macOS / Linux
pip install yfinance pandas numpy scipy --break-system-packages
```

## Run it

```bash
python premium_hunter.py
```

This generates:
- `report.html` — open in browser
- `scan_results.json` — raw data for downstream use

## Daily automation (Mac/Linux)

Add to crontab to run every weekday at 7 AM Dubai time:

```bash
crontab -e
```

Add this line (adjust path):
```
0 7 * * 1-5 cd /path/to/premium_hunter && python premium_hunter.py && open report.html
```

## What it CAN'T do (manual check required)

These data points need a human eye on UW/TipRanks/Morningstar:

- **TipRanks Smart Score** (proprietary)
- **Morningstar fair value rating** (proprietary)
- **UW dark pool floor** (UW-exclusive)
- **UW net put/call premium flow** (UW-exclusive)
- **Whisper numbers** (Earnings Whispers)

The script gets you 90% of the way. Click the ticker in the report — it opens UW's earnings page directly for the final 10%.

## Customization

Edit `premium_hunter.py`:

- `WATCHLIST` — add/remove stocks
- `TARGET_DELTA` — default -0.07 (5-8 delta range). Set to -0.10 for slightly more premium.
- `TARGET_DTE` — default 240 days. Set to 360 for Jan'27 LEAPs.
- `MAX_DAYS_TO_EARNINGS` — default 14. Increase to 30 for wider window.
- `score()` function — tweak scoring weights to match your priorities.

## Limitations

- yfinance is free but rate-limited. Full scan takes ~3-5 min.
- IV from yfinance is sometimes stale during off-hours.
- "Avg earnings move" uses next-day close vs prior close. Real intraday moves can differ.
- Delta is calculated via Black-Scholes (not broker-supplied). Close to real but not exact.

## Workflow

1. **Morning** — Run script, open report.html
2. **Top picks (score ≥3.5)** — Click ticker → UW earnings tab → verify avg move + red X count
3. **Cross-check** — TipRanks for Smart Score, Morningstar for fair value
4. **Final check** — UW Dark Pool floor + Net Premium flow
5. **Fire** — Sell put, set 70% PT GTC

That's the system.
