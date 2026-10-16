from local_trader.config import Settings
from local_trader.models import Candle, SymbolFilters
from local_trader.service import run_paper_once
from local_trader.storage import Store

HOUR = 3_600_000


def _settings(path):
    return Settings(
        mode="paper",
        symbol="BTCUSDT",
        interval="1h",
        lookback=24,
        entry_threshold=0.015,
        exit_threshold=0.0,
        position_pct=0.10,
        stop_pct=0.02,
        daily_loss_pct=0.03,
        max_abs_funding=0.0003,
        fee_rate=0.0005,
        paper_cash=10_000,
        leverage=1,
        loop_seconds=60,
        db_path=path,
        api_key="",
        api_secret="",
    )


def test_storage_and_paper_cycle_wait_for_the_next_closed_bar(tmp_path):
    db = tmp_path / "trader.db"
    store = Store(db)
    start = 1_700_000_000_000
    candles = []
    for index, close in enumerate([100.0] * 24 + [102.0]):
        open_time = start + index * HOUR
        candles.append(Candle(open_time, close, close, close, close, 1, open_time + HOUR - 1))
    store.upsert_candles("BTCUSDT", "1h", candles)
    store.upsert_candles("BTCUSDT", "1h", candles)
    store.upsert_funding("BTCUSDT", [(start, 0.0001)])
    store.save_filters("BTCUSDT", SymbolFilters(0.001, 0.001, 0.1, 5))
    assert store.counts("BTCUSDT", "1h") == (25, 1)

    settings = _settings(db)
    now = candles[-1].close_time + 1
    first = run_paper_once(settings, store, now)
    assert "vào long khi phá đỉnh/đáy" in first
    second = run_paper_once(settings, store, now)
    assert "đã xử lý nến này" in second
    store.close()
