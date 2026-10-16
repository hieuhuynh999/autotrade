import pytest

from local_trader.binance_client import (
    DEMO_BASE_URL,
    BinanceDemoClient,
    _filters_from_symbol,
    assert_demo_base,
)
from local_trader.config import load_settings


def test_demo_host_is_locked():
    assert assert_demo_base(DEMO_BASE_URL) == "https://demo-fapi.binance.com"
    with pytest.raises(RuntimeError):
        assert_demo_base("https://fapi.binance.com")
    with pytest.raises(RuntimeError):
        assert_demo_base("https://testnet.binancefuture.com")


def test_stop_order_goes_to_algo_endpoint_on_demo():
    class Response:
        status_code = 200

        def json(self):
            return {"serverTime": 1_700_000_000_000, "algoId": 7}

    class Session:
        def __init__(self):
            self.urls = []

        def get(self, url, params=None, timeout=None):
            self.urls.append(url)
            return Response()

        def request(self, method, url, headers=None, timeout=None):
            self.urls.append(url)
            return Response()

    session = Session()
    client = BinanceDemoClient("key", "secret", session=session)
    client.place_stop("BTCUSDT", "SELL", "98000.0")
    assert any("/fapi/v1/algoOrder" in url for url in session.urls)
    assert all(url.startswith("https://demo-fapi.binance.com") for url in session.urls)
    assert all("https://fapi.binance.com" not in url for url in session.urls)
    joined = " ".join(session.urls)
    assert "STOP_MARKET" in joined
    assert "secret" not in joined


def test_symbol_filters_prefer_market_lot_size():
    info = {
        "symbol": "BTCUSDT",
        "filters": [
            {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
            {"filterType": "MARKET_LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
            {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
            {"filterType": "MIN_NOTIONAL", "notional": "50"},
        ],
    }
    filters = _filters_from_symbol(info)
    assert filters.step_size == 0.001
    assert filters.tick_size == 0.1
    assert filters.min_notional == 50


def test_settings_reject_leverage_and_bad_mode(tmp_path, monkeypatch):
    for key in (
        "MODE",
        "LEVERAGE",
        "SYMBOL",
        "INTERVAL",
        "LOOKBACK",
        "ENTRY_THRESHOLD",
        "EXIT_THRESHOLD",
        "POSITION_PCT",
        "STOP_PCT",
        "DAILY_LOSS_PCT",
        "MAX_ABS_FUNDING",
        "FEE_RATE",
        "PAPER_CASH",
        "LOOP_SECONDS",
        "DB_PATH",
        "BINANCE_API_KEY",
        "BINANCE_API_SECRET",
    ):
        monkeypatch.delenv(key, raising=False)
    env = tmp_path / ".env"
    env.write_text("MODE=live\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_settings(env)
    env.write_text("LEVERAGE=5\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_settings(env)
