"""End-of-day analyst pipeline.

Steps (SPEC section 5):
  1. Build a shared research brief (one AI call per instrument).
  2. Run the debate: bull, bear, technical, news, macro analysts.
  3. Run the judge: returns TradeIdea or NO_TRADE.
  4. Grounding check: verify price levels.
  5. Risk sizing + cost calculation.
  6. Record everything in the database.

Cost control:
  - Analysts use claude-haiku (cheaper model).
  - Judge uses claude-sonnet (stronger model).
  - Budget gate checked before any call.
  - Prompt caching used via system prompt.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional

from analyst.grounding import verify_price_levels
from analyst.schemas import Action, AnalystDebateEntry, DebateResult, TradeIdea
from analyst.spend import check_budget, record_spend, tokens_to_usd
from analyst.tools import (
    ANTHROPIC_TOOLS,
    get_bars,
    get_earnings_info,
    get_indicators,
    get_news_summary,
    get_regime_info,
    get_support_resistance,
)
from data.models import Bar, NewsItem
from data.fundamentals import is_earnings_blackout

logger = logging.getLogger(__name__)

UTC = timezone.utc

ANALYST_MODEL = "claude-haiku-4-5-20251001"
JUDGE_MODEL = "claude-sonnet-4-6"
PROMPT_VERSION = "v1"

ANALYST_ROLES = ["bull", "bear", "technical", "news", "macro"]

_SYSTEM_PROMPT = """\
You are an analyst for a systematic trading system. You receive structured \
market data through tool calls and write concise analysis.

IMPORTANT rules:
- Never do arithmetic on prices yourself — read levels from the tool outputs.
- Do not state price levels that are not present in the tool data.
- Be concise: 3-5 sentences per section.
- Always acknowledge uncertainty.
- "No trade today" is a valid and sometimes correct answer.\
"""

_JUDGE_SYSTEM = """\
You are the judge analyst. Your job is to weigh the bull and bear cases and \
return a single structured JSON trade idea.

CRITICAL: Your entry, stop, and target levels must all come directly from the \
tool outputs (support/resistance, current price, or SMA levels). Do not invent \
price levels.

Return ONLY valid JSON matching this schema:
{
  "action": "BUY" | "SELL" | "HOLD" | "NO_TRADE",
  "instrument": "<SYMBOL.EXCHANGE>",
  "setup_type": "planned_entry" | "breakout" | "dip_to_support" | "eod_fundamental",
  "expected_holding_days": <int 1-90>,
  "thesis": "<3-5 sentences>",
  "technical_picture": "<description>",
  "catalysts": "<description>",
  "key_risks": "<what would make me wrong>",
  "stop_level": <float>,
  "target_level": <float>,
  "stop_reasoning": "<description>",
  "target_reasoning": "<description>",
  "confidence": "low" | "medium" | "high",
  "sources": [{"description": "...", "timestamp": "..."}],
  "strongest_dissent": "<best argument against this trade>"
}\
"""


class BudgetExceededError(RuntimeError):
    """Raised when the monthly AI budget is reached."""


def _call_anthropic(
    messages: list[dict],
    system: str,
    tools: list[dict],
    tool_data: dict[str, Any],
    model: str,
    conn: sqlite3.Connection,
) -> tuple[str, int, int]:
    """Call Anthropic API, handling tool use in an agentic loop.

    Returns (final_text_response, total_input_tokens, total_output_tokens).
    In mock_mode, returns a stub response.
    """
    from config.settings import load_settings
    settings = load_settings()

    if settings.mock_mode:
        # Return a stub that passes schema validation for testing
        stub = {
            "action": "NO_TRADE",
            "instrument": "AZN.LSE",
            "setup_type": "eod_fundamental",
            "expected_holding_days": 5,
            "thesis": "Mock mode active. No real analysis performed. This is a test stub. Returning NO_TRADE.",
            "technical_picture": "Mock technical picture.",
            "catalysts": "Mock catalysts.",
            "key_risks": "Mock mode — no real analysis.",
            "stop_level": 100.0,
            "target_level": 110.0,
            "stop_reasoning": "Mock stop.",
            "target_reasoning": "Mock target.",
            "confidence": "low",
            "sources": [],
            "strongest_dissent": "Mock dissent.",
        }
        return json.dumps(stub), 100, 50

    budget_ok, _ = check_budget(conn)
    if not budget_ok:
        raise BudgetExceededError("Monthly AI budget reached.")

    import anthropic  # type: ignore[import]
    client = anthropic.Anthropic()

    total_input = 0
    total_output = 0
    current_messages = list(messages)

    for _ in range(10):  # max tool use rounds
        response = client.messages.create(
            model=model,
            max_tokens=2048,
            system=system,
            tools=tools,
            messages=current_messages,
        )
        total_input += response.usage.input_tokens
        total_output += response.usage.output_tokens

        if response.stop_reason == "end_turn":
            text = next(
                (b.text for b in response.content if b.type == "text"), ""
            )
            return text, total_input, total_output

        if response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                result = _dispatch_tool(block.name, block.input, tool_data)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result),
                })
            current_messages.append({"role": "assistant", "content": response.content})
            current_messages.append({"role": "user", "content": tool_results})
        else:
            break

    return "", total_input, total_output


def _dispatch_tool(name: str, inputs: dict, tool_data: dict) -> Any:
    """Route a tool call to the correct function using pre-loaded data."""
    symbol = inputs.get("symbol", "")
    exchange = inputs.get("exchange", "")
    key = f"{symbol}.{exchange}"

    if name == "get_bars":
        bars = tool_data.get("bars", {}).get(key, [])
        return get_bars(bars, inputs.get("bar_type", "1d"), inputs.get("limit", 60))

    if name == "get_indicators":
        bars = tool_data.get("bars", {}).get(key, [])
        return get_indicators(bars)

    if name == "get_support_resistance":
        bars = tool_data.get("bars", {}).get(key, [])
        return get_support_resistance(bars)

    if name == "get_news_summary":
        news = tool_data.get("news", {}).get(key, [])
        return get_news_summary(news, symbol)

    if name == "get_earnings_info":
        earnings = tool_data.get("earnings", {}).get(key)
        is_etf = tool_data.get("is_etf", {}).get(key, False)
        return get_earnings_info(symbol, exchange, earnings, is_etf)

    if name == "get_regime_info":
        regime = tool_data.get("regime")
        if regime:
            return get_regime_info(regime)
        return {"label": "normal", "multiplier": 1.0}

    return {"error": f"Unknown tool: {name}"}


async def run_debate(
    symbol: str,
    exchange: str,
    bars: list[Bar],
    news: list[NewsItem],
    earnings,
    regime,
    is_etf: bool,
    conn: sqlite3.Connection,
    analyst_roles: list[str] | None = None,
) -> DebateResult:
    """Run the full analyst debate and return a DebateResult."""
    if analyst_roles is None:
        analyst_roles = ANALYST_ROLES

    key = f"{symbol}.{exchange}"
    tool_data = {
        "bars": {key: bars},
        "news": {key: news},
        "earnings": {key: earnings},
        "is_etf": {key: is_etf},
        "regime": regime,
    }

    analyst_config = f"roles={','.join(analyst_roles)};judge={JUDGE_MODEL}"
    debate_entries: list[AnalystDebateEntry] = []
    total_in = total_out = 0

    # Pre-compute indicators for grounding (shared across all analysts)
    bars_data = get_bars(bars)
    indicators = get_indicators(bars)
    sr = get_support_resistance(bars)

    # Step 1: Run each analyst
    for role in analyst_roles:
        prompt = (
            f"You are the {role} analyst. Review {symbol} on {exchange}. "
            f"Use the available tools to gather data, then write your {role} case "
            f"in 3-5 sentences. Be direct about your conclusion."
        )
        messages = [{"role": "user", "content": prompt}]
        try:
            text, n_in, n_out = _call_anthropic(
                messages, _SYSTEM_PROMPT, ANTHROPIC_TOOLS, tool_data,
                ANALYST_MODEL, conn,
            )
            record_spend(conn, ANALYST_MODEL, n_in, n_out)
            total_in += n_in
            total_out += n_out
        except BudgetExceededError:
            raise
        except Exception as exc:
            logger.warning("Analyst %s failed for %s: %s", role, symbol, exc)
            text = f"Analysis unavailable ({exc})"
            n_in = n_out = 0

        debate_entries.append(AnalystDebateEntry(
            role=role, content=text or f"No analysis from {role}.",
            model=ANALYST_MODEL, input_tokens=n_in, output_tokens=n_out,
        ))

    # Step 2: Judge
    debate_text = "\n\n".join(
        f"=== {e.role.upper()} ===\n{e.content}" for e in debate_entries
    )
    judge_prompt = (
        f"Here are the analyst views on {symbol} ({exchange}):\n\n"
        f"{debate_text}\n\n"
        "Now use the tools to verify any price levels you want to cite, "
        "then return your structured JSON trade idea."
    )
    messages = [{"role": "user", "content": judge_prompt}]
    try:
        judge_text, j_in, j_out = _call_anthropic(
            messages, _JUDGE_SYSTEM, ANTHROPIC_TOOLS, tool_data,
            JUDGE_MODEL, conn,
        )
        record_spend(conn, JUDGE_MODEL, j_in, j_out)
        total_in += j_in
        total_out += j_out
    except BudgetExceededError:
        raise
    except Exception as exc:
        logger.warning("Judge failed for %s: %s", symbol, exc)
        return DebateResult(
            symbol=symbol, exchange=exchange,
            debate=debate_entries, idea=None,
            total_input_tokens=total_in, total_output_tokens=total_out,
            analyst_config=analyst_config,
        )

    debate_entries.append(AnalystDebateEntry(
        role="judge", content=judge_text or "",
        model=JUDGE_MODEL, input_tokens=j_in, output_tokens=j_out,
    ))

    # Step 3: Parse and validate judge output
    idea: Optional[TradeIdea] = None
    grounding_passed = False
    grounding_details = []

    try:
        raw = judge_text.strip()
        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        idea_dict = json.loads(raw)
        idea = TradeIdea(**idea_dict)

        # Step 4: Grounding check
        if idea.action == Action.NO_TRADE or idea.action == Action.HOLD:
            grounding_passed = True
        else:
            g = verify_price_levels(
                idea.stop_level, idea.stop_level, idea.target_level,
                sr, indicators, bars_data,
            )
            grounding_passed = g.passed
            grounding_details = g.checked_levels
            if not g.passed:
                logger.warning(
                    "Grounding failed for %s: %s — rejecting idea.", symbol, g.reason
                )
                idea = None  # reject the idea

    except Exception as exc:
        logger.warning("Failed to parse judge output for %s: %s", symbol, exc)
        idea = None

    total_cost = tokens_to_usd(ANALYST_MODEL, total_in, total_out)

    return DebateResult(
        symbol=symbol,
        exchange=exchange,
        debate=debate_entries,
        idea=idea,
        grounding_passed=grounding_passed,
        grounding_details=grounding_details,
        total_input_tokens=total_in,
        total_output_tokens=total_out,
        cost_usd=total_cost,
        prompt_version=PROMPT_VERSION,
        analyst_config=analyst_config,
    )
