from local_trader.service import format_session_log
from local_trader.day_guard import (
    LOSS_PAUSE_MS,
    LOSS_TEXT,
    TARGET_TEXT,
    apply_day_guards,
    entries_allowed,
    start_new_session,
)
from local_trader.engine import new_state

NOW = 1_705_276_800_000


def test_profit_target_starts_a_new_session_and_allows_trading():
    state = new_state(10_000, NOW)
    logs, take_profit = apply_day_guards(state, 10_050, NOW)
    assert take_profit is True
    assert logs == [TARGET_TEXT]
    assert entries_allowed(state, NOW) is True
    start_new_session(state, 10_050)
    assert state.day_start_equity == 10_050
    again, take_again = apply_day_guards(state, 10_050, NOW + 1_000)
    assert again == []
    assert take_again is False


def test_session_log_shows_remaining_target_loss_and_balance():
    text = format_session_log(
        now_ms=NOW,
        equity=5012,
        day_pnl=12,
        open_count=1,
        open_text="long 0.01 lãi/lỗ chưa chốt +3.00 USDT",
        wins=2,
        losses=1,
        entries_blocked_until=NOW + 90 * 60 * 1000,
        halted=False,
    )
    assert "lệnh đang mở 1" in text
    assert "2 lệnh lãi, 1 lệnh lỗ" in text
    assert "còn 38.00 USDT nữa thì đủ target ngày 50 USDT" in text
    assert "còn 32.00 USDT nữa thì chạm lỗ ngày 20 USDT" in text
    assert "tổng tiền còn lại 5012.00 USDT" in text
    assert "còn 1 giờ 30 phút" in text


def test_loss_pause_lasts_four_hours_then_allows_entries():
    state = new_state(10_000, NOW)
    state.day_start_equity = 10_020
    logs, take_profit = apply_day_guards(state, 10_000, NOW)
    assert take_profit is False
    assert logs == [LOSS_TEXT]
    assert state.entries_blocked_until == NOW + LOSS_PAUSE_MS
    assert entries_allowed(state, NOW + LOSS_PAUSE_MS - 1) is False
    assert entries_allowed(state, NOW + LOSS_PAUSE_MS) is True
    later, _take = apply_day_guards(state, 10_000, NOW + 1_000)
    assert later == []
    assert state.entries_blocked_until == NOW + LOSS_PAUSE_MS
