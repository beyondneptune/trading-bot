"""Telegram bot — alert cards, approval buttons, commands.

Security (CLAUDE.md rule 16):
  - The bot ONLY responds to the whitelisted TELEGRAM_CHAT_ID.
  - All other senders receive a silent drop (no response that reveals
    the bot exists).

Alert cards expire when the signal expires; expired messages are edited
to show "Expired: [reason]" and buttons are removed.

Commands:
  /status   — connection and account overview
  /positions — open positions
  /ideas    — pending signals
  /pause    — pause new alerts (kill switch lite)
  /resume   — resume after pause or circuit breaker
  /kill     — full kill switch
  /ask <q>  — ask the AI a question (stub in Phase 4a; wired in Phase 5)
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

UTC = timezone.utc

_ALLOWED_CHAT_ID: int | None = None


def _load_chat_id() -> int | None:
    raw = os.getenv("TELEGRAM_CHAT_ID", "")
    try:
        return int(raw) if raw else None
    except ValueError:
        return None


def _is_allowed(chat_id: int) -> bool:
    allowed = _load_chat_id()
    if allowed is None:
        logger.warning("TELEGRAM_CHAT_ID not set — all messages dropped.")
        return False
    return chat_id == allowed


def format_alert_card(signal: dict, equity_gbp: float = 12_500.0) -> str:
    """Format a signal as a Telegram alert card (SPEC section 6)."""
    symbol = signal.get("symbol", "?")
    exchange = signal.get("exchange", "?")
    setup = signal.get("signal_type", "?")
    action = signal.get("action", "BUY")
    entry = signal.get("entry_price", 0)
    stop = signal.get("stop_price", 0)
    target = signal.get("target_price", 0)
    size = signal.get("size", 0)
    risk_gbp = signal.get("risk_gbp", 0)
    cost_r = signal.get("cost_r", 0)
    regime = signal.get("regime", "normal")
    valid_until = signal.get("valid_until", "")
    reward = target - entry if target and entry else 0
    risk_pts = entry - stop if entry and stop else 1
    rr = reward / risk_pts if risk_pts > 0 else 0

    pos_value = (size or 0) * (entry or 0) / 100  # pence to GBP
    pos_pct = pos_value / equity_gbp * 100 if equity_gbp else 0

    return (
        f"*{action} ALERT: {symbol} ({exchange})*\n"
        f"Setup: {setup.replace('_', ' ').title()}  |  Regime: {regime}\n\n"
        f"Entry ≤ {entry:.0f}  |  Stop {stop:.0f}  |  Target {target:.0f}  |  R:R {rr:.1f}\n"
        f"{size} shares (£{pos_value:.0f}, {pos_pct:.1f}% of account)  |  "
        f"Risk £{risk_gbp:.2f}  |  Costs ~{cost_r:.2f}R\n\n"
        f"Valid until: {valid_until}\n\n"
        f"_Tap Approve or Reject below_"
    )


def format_morning_brief(
    positions: list[dict],
    pending_signals: list[dict],
    events_today: list[dict],
    regime: str,
) -> str:
    lines = ["*Morning Brief* 🌅\n"]
    lines.append(f"Market regime: *{regime}*\n")

    if events_today:
        lines.append("*Scheduled events today:*")
        for ev in events_today[:5]:
            lines.append(f"  • {ev.get('name')} ({ev.get('currency')}) — {ev.get('importance')}")
        lines.append("")

    if positions:
        lines.append("*Open positions:*")
        for p in positions[:5]:
            lines.append(f"  • {p.get('symbol')} {p.get('quantity')} @ {p.get('avg_cost'):.0f}")
        lines.append("")

    if pending_signals:
        lines.append("*Plans waiting for trigger:*")
        for s in pending_signals[:3]:
            lines.append(f"  • {s.get('symbol')} {s.get('signal_type')} — entry ≤ {s.get('entry_price'):.0f}")

    return "\n".join(lines)


def format_evening_debrief(
    closed_today: list[dict],
    ai_spend_usd: float,
    pnl_gbp: float,
) -> str:
    lines = ["*Evening Debrief* 🌙\n"]
    lines.append(f"P&L today: *£{pnl_gbp:+.2f}*")
    lines.append(f"AI spend today: *${ai_spend_usd:.2f}*\n")

    if closed_today:
        lines.append("*Trades closed today:*")
        for t in closed_today:
            pnl = t.get("realised_pnl_gbp", 0)
            r = t.get("realised_r", 0)
            lines.append(f"  • {t.get('symbol')}: £{pnl:+.2f} ({r:+.2f}R)")

    return "\n".join(lines)


class TelegramBot:
    """Thin wrapper around python-telegram-bot for sending messages.

    Actual handlers are registered in bot_handlers.py.
    In mock_mode, messages are logged instead of sent.
    """

    def __init__(self) -> None:
        self._token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self._chat_id = _load_chat_id()
        self._app = None

    def _mock_mode(self) -> bool:
        from config.settings import load_settings
        return load_settings().mock_mode

    async def send_message(
        self,
        text: str,
        parse_mode: str = "Markdown",
        reply_markup=None,
    ) -> Optional[int]:
        """Send a message. Returns message_id or None."""
        if not self._chat_id:
            logger.warning("Cannot send Telegram message: no chat ID configured.")
            return None
        if self._mock_mode():
            logger.info("[MOCK Telegram] %s", text[:200])
            return 999

        try:
            from telegram import Bot
            bot = Bot(token=self._token)
            msg = await bot.send_message(
                chat_id=self._chat_id,
                text=text,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
            )
            return msg.message_id
        except Exception as exc:
            logger.error("Telegram send failed: %s", exc)
            return None

    async def edit_message(self, message_id: int, text: str) -> None:
        """Edit an existing message (used for expiry)."""
        if self._mock_mode():
            logger.info("[MOCK Telegram edit msg %d] %s", message_id, text[:200])
            return
        try:
            from telegram import Bot
            bot = Bot(token=self._token)
            await bot.edit_message_text(
                chat_id=self._chat_id,
                message_id=message_id,
                text=text,
            )
        except Exception as exc:
            logger.error("Telegram edit failed: %s", exc)

    async def send_alert_card(self, signal: dict, signal_id: int) -> Optional[int]:
        """Send an alert card with Approve/Reject/Details buttons."""
        text = format_alert_card(signal)
        if self._mock_mode():
            logger.info("[MOCK Telegram alert card] signal_id=%d text=%s", signal_id, text[:100])
            return 999

        try:
            from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Approve", callback_data=f"approve:{signal_id}"),
                    InlineKeyboardButton("❌ Reject", callback_data=f"reject:{signal_id}"),
                ],
                [
                    InlineKeyboardButton("📊 Details", callback_data=f"details:{signal_id}"),
                    InlineKeyboardButton("🤖 Ask AI", callback_data=f"ask:{signal_id}"),
                ],
            ])
            bot = Bot(token=self._token)
            msg = await bot.send_message(
                chat_id=self._chat_id,
                text=text,
                parse_mode="Markdown",
                reply_markup=keyboard,
            )
            return msg.message_id
        except Exception as exc:
            logger.error("Telegram alert card failed: %s", exc)
            return None
