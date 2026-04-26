"""
Claude Scorer — second-opinion BUSINESS INTEL for Premium Hunter picks.

Returns per pick:
  - claude_blurb: 2-3 sentence plain-English description of what the company does
  - claude_bullets: 3-4 business catalyst observations
  - claude_score, claude_tag

Replaces the yfinance company description box with Claude's version.
"""

import os
import json
import time
import sys

try:
    from anthropic import Anthropic
except ImportError:
    Anthropic = None

MODEL = "claude-opus-4-7"

SYSTEM_PROMPT = """You are giving Ash a quick research-note brief on a stock.
Ash is a sophisticated options trader running the Wheel strategy from Dubai
on a $2.4M portfolio.

CRITICAL — WRITING LEVEL:
Write at a 15-year-old reading level. If a smart 15-year-old wouldn't
understand a phrase, REWRITE IT. No jargon. No finance-speak. Plain English.

BANNED phrases (translate them to plain English):
- "multi-year compression" → "stock been falling for years"
- "valuation re-rating" → "investors paying more for the stock"
- "cash flow basis" → just say "cash" or "free cash"
- "pricing power eroding" → "harder to keep prices high"
- "binary risk" → "could go either way fast"
- "secular tailwind" → "long-term boost"
- "dealer destocking" → "shops working through old inventory"
- "capex cycle" → "company spending big on new stuff"
- "TAM expansion" → "bigger market to sell to"
- "annualized return" → "per year"
- "backlog" → "orders piling up"
- "guidance" → "what the company tells investors to expect"
- "GLP-1 LOE overhang" → just explain the patent issue plainly
- "moat" → "competitive edge" or describe what makes it hard to compete
- ALL Greek letters, "delta", "DTE", "OTM"
- "fortress balance sheet" → "tons of cash, low debt"
- "compounder" → "keeps growing year after year"

What Ash already knows (DON'T repeat):
- All the trade math (strike, premium, IV, dates) — visible on his card
- He exits at 50% profit, never holds to expiry
- He's flexible on strike

What Ash WANTS:

1. A 2-3 sentence "what this company actually does" — explain it like to a
   smart kid. NOT yfinance boilerplate. Mention real products, segments,
   what makes them money.

2. 3-4 catalyst bullets:
   - What's happening NOW (deals, products, lawsuits, leadership)
   - Real upcoming catalysts (launches, court cases, expansion)
   - Risks the average person might miss
   - All in plain language a 15-year-old would understand

Return ONLY valid JSON, no preamble:
{
  "score": <float 0-10>,
  "tag": "SAFE BET" | "WATCH" | "SKIP",
  "blurb": "<2-3 sentences, plain English>",
  "bullets": [
    {"tone": "good" | "warn" | "bad", "text": "<plain English, max 30 words>"}
  ]
}

Score: SAFE BET >= 8, WATCH 6-7.9, SKIP < 6
Score reflects "is now a good time to deal with this company"

GOOD blurb examples:
- AMZN: "Three businesses in one: AWS (rents out cloud computers, where most of the profit comes from), online shopping (the Amazon site you know), and a fast-growing ads business now around 10% of sales. AWS just sped back up to 19% growth thanks to AI demand."
- LLY: "Drug company whose biggest moneymakers are Mounjaro and Zepbound — weight-loss and diabetes shots that have exploded in popularity. They also make cancer and immune-system drugs, but the next 2 years are mostly about defending those weight-loss drugs from copycats."
- DPZ: "World's biggest pizza chain — over 21,000 stores, most owned by local franchise owners. Tech-heavy: 75% of US orders come through their app. Now growing fast overseas and using AI to cut labor costs."

GOOD bullet examples (plain English):
- "AWS just sped back up to 19% growth — the deal with Anthropic locks in big cloud orders for years"
- "Novo Nordisk's new shot is about to launch and Merck has a pill version coming — Lilly's lead is shrinking"
- "Stock has shot up 174% this year, looks overheated — buying now after that run is risky"
- "Pizza dealers in the US slowing down as people choose McDonald's $5 meals — but UK and India growing strong"

BAD bullet examples (too jargon-y, do NOT write):
- "Stock down 23% YTD — finally cheap on cash flow basis after multi-year compression"
- "GLP-1 LOE overhang creating valuation overhang"
- "Dealer destocking cycle complete, capex turning constructive"
- "AWS reaccelerated; secular AI tailwind durable"
"""


def _build_user_prompt(pick):
    pt = pick.get('put_trade') or {}
    es = pick.get('earnings_stats') or {}
    em = pick.get('expected_move') or {}
    hc = pick.get('hist_chart') or {}

    news_titles = []
    for n in (pick.get('news_items') or [])[:4]:
        if isinstance(n, dict):
            t = n.get('title') or n.get('headline') or ''
        else:
            t = str(n)
        if t:
            news_titles.append(t)

    return f"""Stock: {pick.get('ticker')} ({pick.get('company')})

Sector: {pick.get('sector')}
Market cap: ${(pick.get('market_cap') or 0)/1e9:.1f}B
Current price: ${pick.get('price', 0):.2f}
1y price action: {hc.get('pct_1y', 0)}% (range: ${hc.get('low_1y', 0):.0f}-${hc.get('high_1y', 0):.0f})
Earnings: in {pick.get('days_to_earnings')} days

Recent news headlines:
{chr(10).join(f'  - {t}' for t in news_titles) if news_titles else '  (none)'}

Business signals:
- Insider activity: {pick.get('insider_activity')}
- Buybacks: {pick.get('buybacks')}
- EPS streak: {pick.get('eps_streak')}
- Analyst revisions: {pick.get('analyst_revisions')}
- Short interest: {pick.get('short_interest')}
- Red flags: {pick.get('red_flags')}

Now write the brief.
- "blurb": 2-3 sentences explaining what {pick.get('ticker')} actually does (NO yfinance boilerplate)
- "bullets": 3-4 catalysts — recent deals, business shifts, real risks. Use what you know."""


def _fallback_result(pick):
    algo = pick.get('score', 0)
    return {
        'claude_score': algo,
        'claude_tag': 'SAFE BET' if algo >= 8 else 'WATCH' if algo >= 6 else 'SKIP',
        'claude_blurb': '',
        'claude_bullets': [('warn', 'Claude API unavailable - using algo score only')],
    }


def _score_one(client, pick, retries=2):
    user_prompt = _build_user_prompt(pick)

    for attempt in range(retries + 1):
        try:
            resp = client.messages.create(
                model=MODEL,
                max_tokens=1000,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
            )
            text = resp.content[0].text.strip()
            text = text.replace("```json", "").replace("```", "").strip()
            data = json.loads(text)

            return {
                'claude_score': float(data.get('score', pick.get('score', 0))),
                'claude_tag': data.get('tag', 'WATCH'),
                'claude_blurb': data.get('blurb', '').strip(),
                'claude_bullets': [
                    (b.get('tone', 'warn'), b.get('text', ''))
                    for b in data.get('bullets', [])
                    if b.get('text')
                ][:4],
            }
        except Exception as e:
            if attempt < retries:
                time.sleep(1.5 ** attempt)
                continue
            print(f"  x Claude API error for {pick.get('ticker')}: {e}",
                  file=sys.stderr, flush=True)
            return _fallback_result(pick)


def score_picks(picks):
    api_key = os.environ.get('ANTHROPIC_API_KEY')

    if Anthropic is None:
        print("WARN: anthropic package not installed - skipping Claude scoring", flush=True)
        for pick in picks:
            pick.update(_fallback_result(pick))
        return picks

    if not api_key:
        print("WARN: ANTHROPIC_API_KEY not set - skipping Claude scoring", flush=True)
        for pick in picks:
            pick.update(_fallback_result(pick))
        return picks

    client = Anthropic(api_key=api_key)
    print(f"\nScoring {len(picks)} picks with Claude Opus 4.7 (research mode + blurb)...", flush=True)

    for i, pick in enumerate(picks, 1):
        result = _score_one(client, pick)
        pick.update(result)
        print(f"  [{i}/{len(picks)}] {pick.get('ticker'):<6} "
              f"Claude: {result['claude_score']:.1f}  "
              f"Algo: {pick.get('score', 0):.1f}  "
              f"{result['claude_tag']}",
              flush=True)

    return picks
