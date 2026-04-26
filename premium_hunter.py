"""
Claude Scorer — second-opinion BUSINESS INTEL for Premium Hunter picks.

Ash already sees all the trade math (strike, premium, IV, earnings dates) on
his card. Claude's job is to tell him about the COMPANY and what's going on
with it right now — like a research-note brief from a friend who reads
everything.
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

What Ash already knows (DON'T repeat it):
- The strike, premium, IV, delta, DTE, OTM% — all visible on his card
- When earnings are and when to pull the trigger on IV crush
- He exits trades at 50% profit, never holds to expiry
- He's flexible on delta — he may pick a different strike

What Ash WANTS from you:
1. What does this company actually do? (one sentence, plain English, like
   you're explaining to a smart friend — NOT yfinance boilerplate)
2. What's happening with the company RIGHT NOW? Recent deals, AI capex,
   product launches, FDA stuff, lawsuits, M&A, leadership changes,
   regulatory pressure, China exposure
3. Real upcoming catalysts (not just earnings — product launches, court
   cases, capacity expansion, capital returns)
4. Risks an outsider might miss — competitor threats, cycle position,
   end-of-life products, structural headwinds
5. Use what you know — your training goes through Jan 2026, you know
   Anthropic's deals, GLP-1 race dynamics, AI capex debate, NBIS spinoff,
   ad market shifts, semi cycle, etc.

DO NOT WRITE:
- Premium math ("$698 for 2 years isn't worth it")
- Trade structure ("strike 41% below spot, huge cushion")
- Earnings timing ("earnings in 2 days, wait for IV crush")
- Generic boilerplate ("quality compounder", "fortress balance sheet",
  "strong buybacks support downside")
- Anything Ash can already see on his card

Return ONLY valid JSON, no preamble:
{
  "score": <float 0-10>,
  "tag": "SAFE BET" | "WATCH" | "SKIP",
  "bullets": [
    {"tone": "good" | "warn" | "bad", "text": "<max 30 words>"}
  ]
}

Aim for 3-4 bullets. ALWAYS lead with one bullet that is a plain-English
"what this company actually does" — written like a smart friend, not a 10-K.

The score reflects whether NOW is a good time to be engaging with this
company (catalysts vs risks vs timing), not whether the company is high
quality in the abstract.

Tag: SAFE BET >= 8 (clear positive catalyst, manageable risks)
     WATCH 6-7.9 (mixed picture, real risks alongside positives)
     SKIP < 6 (real business risks or bad timing)

GOOD examples (what to write):
- "Amazon's three engines: AWS cloud (the profit machine), e-commerce (massive logistics moat), and a fast-growing ads business now ~10% of revenue."
- "AWS growth re-accelerated to 19% on AI demand — the Anthropic capex deal locks multi-year cloud demand. Ads quietly becoming a $50B+ business."
- "Eli Lilly is the GLP-1 leader (Mounjaro, Zepbound) but Novo's CagriSema data and Merck's orforglipron are about to hit. Pricing power eroding."
- "Caterpillar dealer inventories finally normalising after destocking; mining capex cycle turning. Backlog still strong, 2026 setup constructive."
- "Visa faces real disruption: stablecoins on Ethereum + Solana scaling fast, ECB digital euro sidelining card networks. Moat narrowing."

BAD examples (do NOT write):
- "Strike $155 sits 41% below spot — large safety buffer"
- "Premium of $698 over 2 years works out to 2% per year"
- "Earnings in 2 days, wait for IV crush"
- "Amazon is a quality compounder with massive scale advantages"
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

Now write the brief. Lead with what {pick.get('ticker')} actually does in
plain English. Then tell me what's going on with the company right now and
what could move it. Use what you know — don't just regurgitate the data above."""


def _fallback_result(pick):
    algo = pick.get('score', 0)
    return {
        'claude_score': algo,
        'claude_tag': 'SAFE BET' if algo >= 8 else 'WATCH' if algo >= 6 else 'SKIP',
        'claude_bullets': [('warn', 'Claude API unavailable - using algo score only')],
    }


def _score_one(client, pick, retries=2):
    user_prompt = _build_user_prompt(pick)

    for attempt in range(retries + 1):
        try:
            resp = client.messages.create(
                model=MODEL,
                max_tokens=900,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
            )
            text = resp.content[0].text.strip()
            text = text.replace("```json", "").replace("```", "").strip()
            data = json.loads(text)

            return {
                'claude_score': float(data.get('score', pick.get('score', 0))),
                'claude_tag': data.get('tag', 'WATCH'),
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
    print(f"\nScoring {len(picks)} picks with Claude Opus 4.7 (research mode)...", flush=True)

    for i, pick in enumerate(picks, 1):
        result = _score_one(client, pick)
        pick.update(result)
        print(f"  [{i}/{len(picks)}] {pick.get('ticker'):<6} "
              f"Claude: {result['claude_score']:.1f}  "
              f"Algo: {pick.get('score', 0):.1f}  "
              f"{result['claude_tag']}",
              flush=True)

    return picks
