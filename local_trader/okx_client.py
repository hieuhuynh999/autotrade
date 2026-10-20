import base64
import hashlib
import hmac
import json
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from urllib.parse import urlencode

import requests

from local_trader.models import Candle, SymbolFilters

DEMO_BASE_URL = "https://openapi.okx.com"
_USER_AGENT = "local-trader/1.0"


class OkxError(RuntimeError):
    def __init__(self, code: str | None, message: str, http_status: int | None = None) -> None:
        super().__init__(message if code is None else f"{code}: {message}")
        self.code = code
        self.http_status = http_status


def assert_demo_base(url: str) -> str:
    cleaned = url.strip().rstrip("/")
    if cleaned != DEMO_BASE_URL:
        raise RuntimeError(
            "Tool này chỉ gọi https://openapi.okx.com với header demo. "
            "Không gọi Binance và không đặt lệnh OKX thật."
        )
    return cleaned


def inst_id(symbol: str) -> str:
    text = symbol.strip().upper()
    if text.endswith("-SWAP"):
        return text
    if text.endswith("USDT") and "-" not in text:
        base = text[: -len("USDT")]
        if base:
            return f"{base}-USDT-SWAP"
    raise OkxError(None, f"SYMBOL không map được sang hợp đồng OKX: {symbol}")


def bar_name(interval: str) -> str:
    if interval == "15m":
        return "15m"
    if interval == "1h":
        return "1H"
    raise OkxError(None, f"INTERVAL không hỗ trợ trên OKX: {interval}")


def bar_ms(interval: str) -> int:
    if interval == "15m":
        return 15 * 60 * 1000
    if interval == "1h":
        return 60 * 60 * 1000
    raise OkxError(None, f"INTERVAL không hỗ trợ trên OKX: {interval}")


class OkxDemoClient:
    def __init__(
        self,
        api_key: str = "",
        api_secret: str = "",
        api_passphrase: str = "",
        timeout: float = 15.0,
        session: requests.Session | None = None,
    ) -> None:
        self.base_url = assert_demo_base(DEMO_BASE_URL)
        self.api_key = api_key
        self.api_secret = api_secret
        self.api_passphrase = api_passphrase
        self.timeout = timeout
        self.session = session or requests.Session()
        self._specs: dict[str, dict] = {}

    def public_get(self, path: str, params: dict | None = None) -> dict | list:
        response = self.session.get(
            f"{self.base_url}{path}",
            params=params or {},
            headers=self._public_headers(),
            timeout=self.timeout,
        )
        return self._parse(response)

    def klines(self, symbol: str, interval: str, limit: int = 100, end_time: int | None = None) -> list[Candle]:
        params: dict = {
            "instId": inst_id(symbol),
            "bar": bar_name(interval),
            "limit": str(min(limit, 300)),
        }
        path = "/api/v5/market/candles"
        if end_time is not None:
            params["after"] = str(end_time)
            params["limit"] = str(min(limit, 100))
            path = "/api/v5/market/history-candles"
        payload = self.public_get(path, params)
        if not isinstance(payload, list):
            raise OkxError(None, "nến OKX trả về sai định dạng")
        candles = [_parse_candle(row, bar_ms(interval)) for row in payload]
        candles.sort(key=lambda candle: candle.open_time)
        return candles

    def funding_history(self, symbol: str, limit: int = 100) -> list[tuple[int, float]]:
        collected: list[tuple[int, float]] = []
        after: int | None = None
        while len(collected) < limit:
            params: dict = {"instId": inst_id(symbol), "limit": "100"}
            if after is not None:
                params["after"] = str(after)
            payload = self.public_get("/api/v5/public/funding-rate-history", params)
            if not isinstance(payload, list) or not payload:
                break
            batch = [
                (int(item["fundingTime"]), float(item["fundingRate"]))
                for item in payload
            ]
            before = len(collected)
            collected = _merge_points(collected, batch)
            oldest = min(item[0] for item in batch)
            if len(collected) == before or oldest == after or len(payload) < 100:
                break
            after = oldest
        collected.sort(key=lambda item: item[0])
        if len(collected) > limit:
            collected = collected[-limit:]
        return collected

    def symbol_filters(self, symbol: str) -> SymbolFilters:
        spec = self._spec(symbol)
        ct_val = Decimal(spec["ctVal"])
        lot = Decimal(spec["lotSz"])
        min_sz = Decimal(spec["minSz"])
        return SymbolFilters(
            step_size=float(lot * ct_val),
            min_qty=float(min_sz * ct_val),
            tick_size=float(spec["tickSz"]),
            min_notional=5.0,
        )

    def mark_price(self, symbol: str) -> float:
        payload = self.public_get(
            "/api/v5/public/mark-price",
            {"instType": "SWAP", "instId": inst_id(symbol)},
        )
        if not isinstance(payload, list) or not payload or "markPx" not in payload[0]:
            raise OkxError(None, "mark price OKX trả về sai định dạng")
        return float(payload[0]["markPx"])

    def download_history(
        self,
        symbol: str,
        interval: str,
        candles_target: int = 2160,
    ) -> tuple[list[Candle], list[tuple[int, float]]]:
        collected: list[Candle] = []
        after: int | None = None
        while len(collected) < candles_target:
            batch = self.klines(symbol, interval, limit=300 if after is None else 100, end_time=after)
            if not batch:
                break
            before = len(collected)
            collected = _merge_candles(batch, collected)
            oldest = batch[0].open_time
            if len(collected) == before or oldest == after:
                break
            after = oldest
        if len(collected) > candles_target:
            collected = collected[-candles_target:]
        funding = self.funding_history(symbol, limit=300)
        return collected, funding

    def account(self) -> dict:
        balance = self._private("GET", "/api/v5/account/balance", None, None)
        if not isinstance(balance, list) or not balance:
            raise OkxError(None, "số dư OKX trả về sai định dạng")
        total = float(balance[0].get("totalEq") or 0)
        available = 0.0
        for detail in balance[0].get("details") or []:
            if detail.get("ccy") == "USDT":
                available = float(detail.get("availEq") or detail.get("availBal") or 0)
                break
        positions = self._private(
            "GET",
            "/api/v5/account/positions",
            {"instType": "SWAP"},
            None,
        )
        if not isinstance(positions, list):
            raise OkxError(None, "vị thế OKX trả về sai định dạng")
        rows = []
        for row in positions:
            symbol = _symbol_from_inst(str(row.get("instId") or ""))
            if not symbol:
                continue
            spec = self._spec(symbol)
            contracts = Decimal(str(row.get("pos") or "0"))
            qty = float(contracts * Decimal(spec["ctVal"]))
            pos_side = row.get("posSide") or "net"
            rows.append(
                {
                    "symbol": symbol,
                    "positionSide": "BOTH" if pos_side == "net" else pos_side,
                    "positionAmt": qty,
                    "entryPrice": row.get("avgPx") or "0",
                }
            )
        return {
            "totalMarginBalance": total,
            "availableBalance": available,
            "canWithdraw": False,
            "positions": rows,
        }

    def prepare_symbol(self, symbol: str, leverage: int) -> None:
        try:
            self._private("POST", "/api/v5/account/set-position-mode", None, {"posMode": "net_mode"})
        except OkxError:
            pass
        self._private(
            "POST",
            "/api/v5/account/set-leverage",
            None,
            {"instId": inst_id(symbol), "lever": str(leverage), "mgnMode": "isolated"},
        )

    def market_order(self, symbol: str, side: str, quantity: str, reduce_only: bool = False) -> dict:
        body = {
            "instId": inst_id(symbol),
            "tdMode": "isolated",
            "side": "buy" if side.upper() == "BUY" else "sell",
            "ordType": "market",
            "sz": self._contracts(symbol, quantity),
        }
        if reduce_only:
            body["reduceOnly"] = True
        payload = self._private("POST", "/api/v5/trade/order", None, body)
        order = _first_order(payload)
        order_id = str(order.get("ordId") or "")
        avg = order.get("avgPx") or ""
        if order_id and not avg:
            detail = self._private(
                "GET",
                "/api/v5/trade/order",
                {"instId": inst_id(symbol), "ordId": order_id},
                None,
            )
            if isinstance(detail, list) and detail:
                avg = detail[0].get("avgPx") or ""
        return {"avgPrice": avg, "orderId": order_id}

    def place_stop(self, symbol: str, side: str, trigger_price: str, quantity: str) -> dict:
        body = {
            "instId": inst_id(symbol),
            "tdMode": "isolated",
            "side": "buy" if side.upper() == "BUY" else "sell",
            "ordType": "conditional",
            "sz": self._contracts(symbol, quantity),
            "slTriggerPx": trigger_price,
            "slOrdPx": "-1",
            "slTriggerPxType": "mark",
            "reduceOnly": True,
            "cxlOnClosePos": True,
        }
        payload = self._private("POST", "/api/v5/trade/order-algo", None, body)
        return _first_order(payload)

    def cancel_stops(self, symbol: str) -> None:
        pending = self._private(
            "GET",
            "/api/v5/trade/orders-algo-pending",
            {"ordType": "conditional", "instId": inst_id(symbol)},
            None,
        )
        if not isinstance(pending, list) or not pending:
            return
        body = [
            {"instId": inst_id(symbol), "algoId": str(item["algoId"])}
            for item in pending
            if item.get("algoId")
        ]
        if body:
            self._private("POST", "/api/v5/trade/cancel-algos", None, body)

    def _spec(self, symbol: str) -> dict:
        cached = self._specs.get(symbol)
        if cached is not None:
            return cached
        payload = self.public_get(
            "/api/v5/public/instruments",
            {"instType": "SWAP", "instId": inst_id(symbol)},
        )
        if not isinstance(payload, list):
            raise OkxError(None, "instruments OKX trả về sai định dạng")
        for info in payload:
            if info.get("instId") == inst_id(symbol):
                for key in ("ctVal", "lotSz", "minSz", "tickSz"):
                    if not info.get(key):
                        raise OkxError(None, f"thiếu {key} của {symbol} trên OKX")
                self._specs[symbol] = info
                return info
        raise OkxError(None, f"không thấy {symbol} trên OKX demo")

    def _contracts(self, symbol: str, quantity: str) -> str:
        spec = self._spec(symbol)
        ct_val = Decimal(spec["ctVal"])
        lot = Decimal(spec["lotSz"])
        contracts = (Decimal(quantity) / ct_val).quantize(lot, rounding=ROUND_DOWN)
        if contracts <= 0:
            raise OkxError(None, "khối lượng quy ra số hợp đồng OKX bằng 0")
        return format(contracts, "f")

    def _private(self, method: str, path: str, params: dict | None, body: dict | list | None) -> dict | list:
        if not self.api_key or not self.api_secret or not self.api_passphrase:
            raise OkxError(None, "thiếu OKX_API_KEY, OKX_API_SECRET hoặc OKX_API_PASSPHRASE của demo")
        query = urlencode(params) if params else ""
        request_path = path if not query else f"{path}?{query}"
        body_text = json.dumps(body, separators=(",", ":")) if body is not None else ""
        timestamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        signature = base64.b64encode(
            hmac.new(
                self.api_secret.encode("utf-8"),
                f"{timestamp}{method.upper()}{request_path}{body_text}".encode("utf-8"),
                hashlib.sha256,
            ).digest()
        ).decode("utf-8")
        headers = self._public_headers()
        headers.update(
            {
                "OK-ACCESS-KEY": self.api_key,
                "OK-ACCESS-SIGN": signature,
                "OK-ACCESS-TIMESTAMP": timestamp,
                "OK-ACCESS-PASSPHRASE": self.api_passphrase,
                "Content-Type": "application/json",
            }
        )
        response = self.session.request(
            method,
            f"{self.base_url}{request_path}",
            data=body_text if body is not None else None,
            headers=headers,
            timeout=self.timeout,
        )
        return self._parse(response)

    @staticmethod
    def _public_headers() -> dict:
        return {"User-Agent": _USER_AGENT, "x-simulated-trading": "1"}

    @staticmethod
    def _parse(response: requests.Response) -> dict | list:
        try:
            payload = response.json()
        except ValueError as exc:
            raise OkxError(None, f"phản hồi không phải JSON ({response.status_code})", response.status_code) from exc
        if not isinstance(payload, dict):
            raise OkxError(None, "phản hồi OKX sai định dạng", response.status_code)
        code = str(payload.get("code", ""))
        if response.status_code >= 400 or code not in {"0", ""}:
            raise OkxError(code or None, str(payload.get("msg") or payload), response.status_code)
        data = payload.get("data", payload)
        if isinstance(data, list) and data and isinstance(data[0], dict) and data[0].get("sCode") not in (None, "0"):
            item = data[0]
            raise OkxError(str(item.get("sCode")), str(item.get("sMsg") or payload.get("msg")), response.status_code)
        return data


def _parse_candle(row: list, width_ms: int) -> Candle:
    open_time = int(row[0])
    return Candle(
        open_time=open_time,
        open=float(row[1]),
        high=float(row[2]),
        low=float(row[3]),
        close=float(row[4]),
        volume=float(row[6] if len(row) > 6 else row[5]),
        close_time=open_time + width_ms - 1,
    )


def _merge_candles(older: list[Candle], newer: list[Candle]) -> list[Candle]:
    by_open = {candle.open_time: candle for candle in older}
    for candle in newer:
        by_open[candle.open_time] = candle
    return [by_open[key] for key in sorted(by_open)]


def _merge_points(left: list[tuple[int, float]], right: list[tuple[int, float]]) -> list[tuple[int, float]]:
    by_time = {item[0]: item[1] for item in left}
    for funding_time, rate in right:
        by_time[funding_time] = rate
    return [(key, by_time[key]) for key in sorted(by_time)]


def _symbol_from_inst(inst: str) -> str:
    if not inst.endswith("-SWAP"):
        return ""
    parts = inst.split("-")
    if len(parts) < 3:
        return ""
    return f"{parts[0]}{parts[1]}"


def _first_order(payload: dict | list) -> dict:
    if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
        raise OkxError(None, "lệnh OKX trả về sai định dạng")
    return payload[0]
