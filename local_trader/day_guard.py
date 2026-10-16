"""Chốt lãi ngày và nghỉ sau lỗ ngày. Cắt lỗ cứng và thoát phá biên vẫn chạy."""

from datetime import datetime, timezone

from local_trader.models import BotState

PROFIT_TARGET_USD = 50.0
LOSS_PAUSE_USD = 40.0
LOSS_PAUSE_MS = 4 * 60 * 60 * 1000

TARGET_TEXT = "lãi ngày đạt 50 USD, chốt lãi và bắt đầu tính ngày mới từ lúc này"
LOSS_TEXT = "cảnh báo: lỗ ngày đạt 40 USD. Tạm dừng mở lệnh 4 giờ."


def reset_day_guards(state: BotState) -> None:
    state.profit_day_done = False
    state.loss_pause_latched = False
    state.logs = []


def start_new_session(state: BotState, equity: float) -> None:
    """Mốc lãi/lỗ ngày tính lại từ vốn ngay sau lúc chốt. Được vào lệnh tiếp."""
    state.day_start_equity = equity
    state.profit_day_done = False
    state.loss_pause_latched = False
    state.entries_blocked_until = None


def entries_allowed(state: BotState, now_ms: int) -> bool:
    if state.halted:
        return False
    if state.entries_blocked_until is not None and now_ms < state.entries_blocked_until:
        return False
    return True


def activity_label(state: BotState, now_ms: int) -> str:
    if state.halted:
        return "đang dừng mở lệnh mới"
    if state.entries_blocked_until is not None and now_ms < state.entries_blocked_until:
        return f"tạm dừng mở lệnh đến {_fmt(state.entries_blocked_until)}"
    return "đang chạy"


def entry_block_text(state: BotState, now_ms: int) -> str | None:
    if entries_allowed(state, now_ms):
        return None
    if state.halted and state.halt_reason:
        return state.halt_reason
    if state.entries_blocked_until is not None and now_ms < state.entries_blocked_until:
        return f"tạm dừng mở lệnh đến {_fmt(state.entries_blocked_until)}"
    return "không mở lệnh mới"


def apply_day_guards(state: BotState, equity: float, now_ms: int) -> tuple[list[str], bool]:
    """Trả về dòng log mới và cờ cần đóng vị thế để chốt lãi ngày."""
    day_pnl = equity - state.day_start_equity
    logs: list[str] = []
    take_profit = False
    if day_pnl >= PROFIT_TARGET_USD:
        take_profit = True
        logs.append(TARGET_TEXT)
    if day_pnl > -LOSS_PAUSE_USD:
        state.loss_pause_latched = False
    elif not state.loss_pause_latched and not take_profit:
        state.loss_pause_latched = True
        state.entries_blocked_until = now_ms + LOSS_PAUSE_MS
        logs.append(LOSS_TEXT)
    state.logs = logs
    return logs, take_profit


def _fmt(time_ms: int) -> str:
    return datetime.fromtimestamp(time_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
