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


def test_loss_pause_lasts_four_hours_then_allows_entries():
    state = new_state(10_000, NOW)
    state.day_start_equity = 10_040
    logs, take_profit = apply_day_guards(state, 10_000, NOW)
    assert take_profit is False
    assert logs == [LOSS_TEXT]
    assert state.entries_blocked_until == NOW + LOSS_PAUSE_MS
    assert entries_allowed(state, NOW + LOSS_PAUSE_MS - 1) is False
    assert entries_allowed(state, NOW + LOSS_PAUSE_MS) is True
    later, _take = apply_day_guards(state, 10_000, NOW + 1_000)
    assert later == []
    assert state.entries_blocked_until == NOW + LOSS_PAUSE_MS
