import pytest

from local_trader.config import load_settings
from local_trader.okx_client import DEMO_BASE_URL, OkxDemoClient, assert_demo_base, inst_id


def test_demo_host_is_locked():
    assert assert_demo_base(DEMO_BASE_URL) == "https://openapi.okx.com"
    with pytest.raises(RuntimeError):
        assert_demo_base("https://www.okx.com")
    with pytest.raises(RuntimeError):
        assert_demo_base("https://fapi.binance.com")
    with pytest.raises(RuntimeError):
        assert_demo_base("https://demo-fapi.binance.com")


def test_symbol_maps_to_usdt_swap():
    assert inst_id("BTCUSDT") == "BTC-USDT-SWAP"
    assert inst_id("btc-usdt-swap") == "BTC-USDT-SWAP"


def test_stop_order_goes_to_okx_demo_algo():
    class Response:
        status_code = 200

        def json(self):
            return {"code": "0", "data": [{"algoId": "7", "sCode": "0"}]}

    class Session:
        def __init__(self):
            self.urls = []
            self.bodies = []
            self.headers = []

        def request(self, method, url, data=None, headers=None, timeout=None):
            self.urls.append(url)
            self.bodies.append(data or "")
            self.headers.append(headers or {})
            return Response()

    session = Session()
    client = OkxDemoClient("key", "supersecret", "passphrase-value", session=session)
    client._specs["BTCUSDT"] = {"ctVal": "0.01", "lotSz": "0.01", "minSz": "0.01", "tickSz": "0.1"}
    client.place_stop("BTCUSDT", "SELL", "98000.0", "0.01")
    assert any("/api/v5/trade/order-algo" in url for url in session.urls)
    assert all(url.startswith("https://openapi.okx.com") for url in session.urls)
    assert all("binance" not in url for url in session.urls)
    joined = " ".join(session.urls + session.bodies)
    assert "conditional" in joined
    assert "slTriggerPx" in joined
    assert "supersecret" not in joined
    assert "passphrase-value" not in joined
    assert session.headers[0]["x-simulated-trading"] == "1"


def test_symbol_filters_use_contract_value():
    class Response:
        status_code = 200

        def json(self):
            return {
                "code": "0",
                "data": [
                    {
                        "instId": "BTC-USDT-SWAP",
                        "ctVal": "0.01",
                        "lotSz": "0.01",
                        "minSz": "0.01",
                        "tickSz": "0.1",
                    }
                ],
            }

    class Session:
        def get(self, url, params=None, headers=None, timeout=None):
            return Response()

    filters = OkxDemoClient(session=Session()).symbol_filters("BTCUSDT")
    assert filters.step_size == pytest.approx(0.0001)
    assert filters.min_qty == pytest.approx(0.0001)
    assert filters.tick_size == 0.1
    assert filters.min_notional == 5


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
        "OKX_API_KEY",
        "OKX_API_SECRET",
        "OKX_API_PASSPHRASE",
    ):
        monkeypatch.delenv(key, raising=False)
    env = tmp_path / ".env"
    env.write_text("MODE=live\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_settings(env)
    env.write_text("LEVERAGE=5\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_settings(env)
