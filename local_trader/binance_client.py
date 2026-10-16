import hashlib
import hmac
import time
from urllib.parse import urlencode

import requests

from local_trader.models import Candle, SymbolFilters

DEMO_BASE_URL = "https://demo-fapi.binance.com"


class BinanceError(RuntimeError):
    def __init__(self, code: int | None, message: str, http_status: int | None = None) -> None:
        super().__init__(message if code is None else f"{code}: {message}")
        self.code = code
        self.http_status = http_status


def assert_demo_base(url: str) -> str:
    cleaned = url.strip().rstrip("/")
    if cleaned != DEMO_BASE_URL:
        raise RuntimeError(
            "Tool này chỉ gọi https://demo-fapi.binance.com. "
            "Không dùng key và không gọi API của Binance thật."
        )
    return cleaned


class BinanceDemoClient:
    def __init__(
        self,
        api_key: str = "",
        api_secret: str = "",
        timeout: float = 15.0,
        session: requests.Session | None = None,
    ) -> None:
        self.base_url = assert_demo_base(DEMO_BASE_URL)
        self.api_key = api_key
        self.api_secret = api_secret
        self.timeout = timeout
        self.session = session or requests.Session()
        self._time_offset_ms = 0

    def sync_time(self) -> None:
        server = int(self.public_get("/fapi/v1/time")["serverTime"])
        self._time_offset_ms = server - int(time.time() * 1000)

    def public_get(self, path: str, params: dict | None = None) -> dict | list:
        response = self.session.get(
            f"{self.base_url}{path}",
            params=params or {},
            timeout=self.timeout,
        )
        return self._parse(response)

    def klines(self, symbol: str, interval: str, limit: int = 500, end_time: int | None = None) -> list[Candle]:
        params: dict = {"symbol": symbol, "interval": interval, "limit": limit}
        if end_time is not None:
            params["endTime"] = end_time
        payload = self.public_get("/fapi/v1/klines", params)
        if not isinstance(payload, list):
            raise BinanceError(None, "klines trả về sai định dạng")
        return [_parse_kline(row) for row in payload]

    def funding_history(self, symbol: str, limit: int = 1000) -> list[tuple[int, float]]:
        payload = self.public_get("/fapi/v1/fundingRate", {"symbol": symbol, "limit": limit})
        if not isinstance(payload, list):
            raise BinanceError(None, "fundingRate trả về sai định dạng")
        points = [
            (int(item["fundingTime"]), float(item["fundingRate"]))
            for item in payload
        ]
        points.sort(key=lambda item: item[0])
        return points

    def symbol_filters(self, symbol: str) -> SymbolFilters:
        payload = self.public_get("/fapi/v1/exchangeInfo", {"symbol": symbol})
        if not isinstance(payload, dict):
            raise BinanceError(None, "exchangeInfo trả về sai định dạng")
        for info in payload.get("symbols", []):
            if info.get("symbol") == symbol:
                return _filters_from_symbol(info)
        raise BinanceError(None, f"không thấy {symbol} trên demo")

    def mark_price(self, symbol: str) -> float:
        payload = self.public_get("/fapi/v1/premiumIndex", {"symbol": symbol})
        if not isinstance(payload, dict) or "markPrice" not in payload:
            raise BinanceError(None, "premiumIndex trả về sai định dạng")
        return float(payload["markPrice"])

    def download_history(self, symbol: str, interval: str, candles_target: int = 2160) -> tuple[list[Candle], list[tuple[int, float]]]:
        collected: list[Candle] = []
        end_time: int | None = None
        while len(collected) < candles_target:
            batch = self.klines(symbol, interval, limit=1500, end_time=end_time)
            if not batch:
                break
            before = len(collected)
            collected = _merge_candles(batch, collected)
            if len(collected) == before or len(batch) < 1500:
                break
            end_time = batch[0].open_time - 1
        if len(collected) > candles_target:
            collected = collected[-candles_target:]
        funding = self.funding_history(symbol, limit=1000)
        return collected, funding

    def account(self) -> dict:
        payload = self._signed("GET", "/fapi/v2/account", {})
        if not isinstance(payload, dict):
            raise BinanceError(None, "account trả về sai định dạng")
        return payload

    def prepare_symbol(self, symbol: str, leverage: int) -> None:
        try:
            self._signed("POST", "/fapi/v1/marginType", {"symbol": symbol, "marginType": "ISOLATED"})
        except BinanceError:
            pass
        self._signed("POST", "/fapi/v1/leverage", {"symbol": symbol, "leverage": leverage})

    def market_order(self, symbol: str, side: str, quantity: str, reduce_only: bool = False) -> dict:
        params = {
            "symbol": symbol,
            "side": side,
            "type": "MARKET",
            "quantity": quantity,
            "newClientOrderId": f"lt{int(time.time() * 1000)}",
        }
        if reduce_only:
            params["reduceOnly"] = "true"
        payload = self._signed("POST", "/fapi/v1/order", params)
        if not isinstance(payload, dict):
            raise BinanceError(None, "order trả về sai định dạng")
        return payload

    def place_stop(self, symbol: str, side: str, trigger_price: str) -> dict:
        payload = self._signed(
            "POST",
            "/fapi/v1/algoOrder",
            {
                "algoType": "CONDITIONAL",
                "symbol": symbol,
                "side": side,
                "type": "STOP_MARKET",
                "triggerPrice": trigger_price,
                "closePosition": "true",
                "workingType": "MARK_PRICE",
            },
        )
        if not isinstance(payload, dict):
            raise BinanceError(None, "algoOrder trả về sai định dạng")
        return payload

    def cancel_stops(self, symbol: str) -> None:
        try:
            self._signed("DELETE", "/fapi/v1/algoOpenOrders", {"symbol": symbol})
        except BinanceError as exc:
            if exc.http_status == 404 or exc.code in {-2011, -4130}:
                return
            raise

    def _signed(self, method: str, path: str, params: dict) -> dict | list:
        if not self.api_key or not self.api_secret:
            raise BinanceError(None, "thiếu BINANCE_API_KEY hoặc BINANCE_API_SECRET của demo")
        if self._time_offset_ms == 0:
            self.sync_time()
        try:
            return self._signed_once(method, path, params)
        except BinanceError as exc:
            if exc.code != -1021:
                raise
            self.sync_time()
            return self._signed_once(method, path, params)

    def _signed_once(self, method: str, path: str, params: dict) -> dict | list:
        query = dict(params)
        query["timestamp"] = int(time.time() * 1000) + self._time_offset_ms
        query["recvWindow"] = 5000
        encoded = urlencode(query)
        signature = hmac.new(
            self.api_secret.encode("utf-8"),
            encoded.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        response = self.session.request(
            method,
            f"{self.base_url}{path}?{encoded}&signature={signature}",
            headers={"X-MBX-APIKEY": self.api_key},
            timeout=self.timeout,
        )
        return self._parse(response)

    @staticmethod
    def _parse(response: requests.Response) -> dict | list:
        try:
            payload = response.json()
        except ValueError as exc:
            raise BinanceError(None, f"phản hồi không phải JSON ({response.status_code})", response.status_code) from exc
        if response.status_code >= 400 or (isinstance(payload, dict) and payload.get("code", 0) < 0):
            code = payload.get("code") if isinstance(payload, dict) else None
            message = payload.get("msg") if isinstance(payload, dict) else str(payload)
            raise BinanceError(code, str(message), response.status_code)
        return payload


def _parse_kline(row: list) -> Candle:
    return Candle(
        open_time=int(row[0]),
        open=float(row[1]),
        high=float(row[2]),
        low=float(row[3]),
        close=float(row[4]),
        volume=float(row[5]),
        close_time=int(row[6]),
    )


def _merge_candles(older: list[Candle], newer: list[Candle]) -> list[Candle]:
    by_open = {candle.open_time: candle for candle in older}
    for candle in newer:
        by_open[candle.open_time] = candle
    return [by_open[key] for key in sorted(by_open)]


def _filters_from_symbol(info: dict) -> SymbolFilters:
    by_type = {item["filterType"]: item for item in info.get("filters", [])}
    lot = by_type.get("MARKET_LOT_SIZE") or by_type.get("LOT_SIZE")
    price = by_type.get("PRICE_FILTER")
    notional = by_type.get("MIN_NOTIONAL") or by_type.get("NOTIONAL")
    if lot is None or price is None or notional is None:
        raise BinanceError(None, f"thiếu bộ lọc khối lượng của {info.get('symbol')}")
    min_notional = float(notional.get("notional") or notional.get("minNotional"))
    return SymbolFilters(
        step_size=float(lot["stepSize"]),
        min_qty=float(lot["minQty"]),
        tick_size=float(price["tickSize"]),
        min_notional=min_notional,
    )
