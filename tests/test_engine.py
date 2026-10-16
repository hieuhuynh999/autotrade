from local_trader.engine import apply_entry, backtest, new_state, process_bar
from local_trader.models import Candle, SymbolFilters

FILTERS = SymbolFilters(0.001, 0.001, 0.1, 5)
HOUR = 3_600_000
KW = dict(
    lookback=24,
    entry_threshold=0.015,
    exit_threshold=0.0,
    max_abs_funding=0.0003,
    position_pct=0.10,
    stop_pct=0.02,
    daily_loss_pct=0.03,
    fee_rate=0.0005,
    filters=FILTERS,
)


def bars(closes, start=1_700_000_000_000):
    candles = []
    for index, close in enumerate(closes):
        open_time = start + index * HOUR
        candles.append(
            Candle(open_time, close, close + 0.1, close - 0.1, close, 1, open_time + HOUR - 1)
        )
    return candles


def test_flat_market_does_not_break_out():
    history = bars([100.0] * 30)
    state = new_state(10_000, history[0].close_time)
    fills = process_bar(state, history, 0.0001, **KW)
    assert fills == []
    assert state.position is None


def test_breakout_opens_one_long_and_does_not_add():
    history = bars([100.0] * 24 + [102.0])
    state = new_state(10_000, history[0].close_time)
    first = process_bar(state, history, 0.0001, **KW)
    assert len(first) == 1
    assert first[0].side == "long"
    qty = state.position.qty
    nxt = history + bars([102.0], start=history[-1].open_time + HOUR)
    second = process_bar(state, nxt, 0.0001, **KW)
    assert second == []
    assert state.position.qty == qty


def test_stop_closes_and_does_not_flip_on_the_same_bar():
    history = bars([100.0] * 24 + [102.0])
    state = new_state(10_000, history[0].close_time)
    apply_entry(state, "long", 10, 100, history[-1].close_time, 98, 0.0005, "mở sẵn", 0.02)
    stop = state.position.stop_price
    crash_open = history[-1].open_time + HOUR
    crash = Candle(crash_open, 102, 103, stop - 5, 102, 1, crash_open + HOUR - 1)
    params = dict(KW)
    params["daily_loss_pct"] = 0.001
    fills = process_bar(state, history + [crash], 0.0001, **params)
    assert [fill.reason for fill in fills] == ["cắt lỗ cứng"]
    assert state.position is None
    assert state.halted is True


def test_new_utc_day_clears_halt():
    history = bars([100.0] * 30)
    state = new_state(10_000, history[0].close_time)
    state.halted = True
    state.halt_reason = "hôm qua"
    state.day_key = "1999-01-01"
    state.profit_day_done = True
    state.entries_blocked_until = history[-1].close_time + 4 * HOUR
    fills = process_bar(state, history, 0.0001, **KW)
    assert state.halted is False
    assert state.profit_day_done is False
    assert state.entries_blocked_until == history[-1].close_time + 4 * HOUR
    assert fills == []


def test_daily_profit_target_closes_then_can_enter_on_the_next_bar():
    start = 1_705_276_800_000
    step = 15 * 60 * 1000
    candles = []
    for index, close in enumerate([100.0] * 24):
        open_time = start + index * step
        candles.append(Candle(open_time, close, close + 0.1, close - 0.1, close, 1, open_time + step - 1))
    state = new_state(10_000, candles[0].close_time)
    apply_entry(state, "long", 10, 100, candles[-1].close_time, 50, 0.0, "mở sẵn", None)
    open_time = start + 24 * step
    winner = Candle(open_time, 105, 105.2, 104.8, 105, 1, open_time + step - 1)
    fills = process_bar(state, candles + [winner], 0.0001, **KW)
    assert [fill.reason for fill in fills] == ["chốt lãi vì lãi ngày đạt 50 USD"]
    assert state.position is None
    assert state.profit_day_done is False
    assert state.day_start_equity == state.cash
    later = open_time + step
    nxt = Candle(later, 120, 120.2, 119.8, 120, 1, later + step - 1)
    nxt_fills = process_bar(state, candles + [winner, nxt], 0.0001, **KW)
    assert len(nxt_fills) == 1
    assert nxt_fills[0].kind == "entry"


def test_daily_loss_pauses_entries_for_four_hours():
    start = 1_705_276_800_000
    step = 15 * 60 * 1000
    candles = []
    for index, close in enumerate([100.0] * 24 + [130.0]):
        open_time = start + index * step
        candles.append(Candle(open_time, close, close + 0.1, close - 0.1, close, 1, open_time + step - 1))
    state = new_state(10_000, candles[0].close_time)
    state.day_start_equity = 10_020
    assert process_bar(state, candles, 0.0001, **KW) == []
    assert state.position is None
    until = candles[-1].close_time + 4 * HOUR
    assert state.entries_blocked_until == until
    paused_open = start + 25 * step
    paused = Candle(paused_open, 140, 140.1, 139.9, 140, 1, paused_open + step - 1)
    assert process_bar(state, candles + [paused], 0.0001, **KW) == []
    resumed = Candle(until, 150, 150.1, 149.9, 150, 1, until + step - 1)
    fills = process_bar(state, candles + [paused, resumed], 0.0001, **KW)
    assert len(fills) == 1
    assert fills[0].kind == "entry"


def test_backtest_breakout_round_trip_pays_fees():
    closes = [100.0] * 24 + [102.0] * 30
    candles = bars(closes)
    result = backtest(
        candles,
        [(0, 0.0001)],
        lookback=KW["lookback"],
        entry_threshold=KW["entry_threshold"],
        exit_threshold=KW["exit_threshold"],
        max_abs_funding=KW["max_abs_funding"],
        position_pct=KW["position_pct"],
        stop_pct=KW["stop_pct"],
        daily_loss_pct=KW["daily_loss_pct"],
        fee_rate=KW["fee_rate"],
        filters=FILTERS,
        cash=10_000,
    )
    assert result.trades
    assert result.fees > 0
    assert result.ending_equity < result.start_equity
