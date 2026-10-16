from local_trader.engine import backtest, new_state, process_bar
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


def test_one_long_per_signal_and_no_second_add():
    history = bars([100.0] * 24 + [102.0])
    state = new_state(10_000, history[0].close_time)
    first = process_bar(state, history, 0.0001, **KW)
    assert len(first) == 1
    assert first[0].kind == "entry"
    assert first[0].side == "long"
    qty = state.position.qty
    nxt = history + bars([102.0], start=history[-1].open_time + HOUR)
    second = process_bar(state, nxt, 0.0001, **KW)
    assert second == []
    assert state.position.qty == qty


def test_stop_closes_and_does_not_flip_on_the_same_bar():
    history = bars([100.0] * 24 + [102.0])
    state = new_state(10_000, history[0].close_time)
    process_bar(state, history, 0.0001, **KW)
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
    history = bars([100.0] * 24 + [102.0])
    state = new_state(10_000, history[0].close_time)
    state.halted = True
    state.halt_reason = "hôm qua"
    state.day_key = "1999-01-01"
    fills = process_bar(state, history, 0.0001, **KW)
    assert state.halted is False
    assert fills[0].kind == "entry"


def test_backtest_charges_fees_on_a_round_trip():
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
