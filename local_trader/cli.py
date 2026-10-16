import argparse
import sys
import time
from pathlib import Path

from local_trader.binance_client import DEMO_BASE_URL, BinanceDemoClient, BinanceError
from local_trader.config import load_settings
import requests

from local_trader.engine import backtest
from local_trader.service import (
    describe_signal,
    run_demo_once,
    run_paper_once,
    session_log,
    status_report,
    sync_market,
)
from local_trader.storage import Store
from local_trader.strategy import closed_candles


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        prog="local_trader",
        description="Bot phá đỉnh/đáy 15 phút, lưu dữ liệu local, chỉ nối Binance Futures Demo.",
    )
    parser.add_argument("--env", default=".env", help="đường dẫn file env")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("sync", help="kéo nến và funding về máy")
    sub.add_parser("signal", help="in tín hiệu của nến đã đóng, không đặt lệnh")
    sub.add_parser("once", help="xử lý một vòng")
    sub.add_parser("run", help="lặp vòng cho đến khi dừng bằng Ctrl+C")
    sub.add_parser("status", help="xem vốn, vị thế, ngưỡng")
    sub.add_parser("backtest", help="chạy lại luật trên nến đã lưu")
    sub.add_parser("reset-paper", help="xóa vị thế paper và trả vốn về PAPER_CASH")
    args = parser.parse_args(argv)
    try:
        settings = load_settings(Path(args.env))
    except ValueError as exc:
        print(f"cấu hình lỗi: {exc}")
        return 2
    store = Store(settings.db_path)
    client = BinanceDemoClient(settings.api_key, settings.api_secret)
    try:
        if args.command == "sync":
            print(sync_market(settings, store, client, full=True))
        elif args.command == "signal":
            print(describe_signal(settings, store, _now()))
        elif args.command == "once":
            print(_once(settings, store, client))
        elif args.command == "run":
            print(f"đang chạy {settings.mode}. Dừng bằng Ctrl+C. Host: {DEMO_BASE_URL}")
            print("log lãi/lỗ in một lần mỗi 15 phút.")
            last_session_log = 0
            while True:
                try:
                    print(_once(settings, store, client))
                    now = _now()
                    if now - last_session_log >= 15 * 60 * 1000:
                        print(session_log(settings, store, client, now))
                        last_session_log = now
                    print("---")
                except (BinanceError, requests.RequestException) as exc:
                    print(f"lỗi mạng hoặc sàn, sẽ thử lại: {exc}")
                time.sleep(settings.loop_seconds)
        elif args.command == "status":
            print(status_report(settings, store, _now()))
        elif args.command == "backtest":
            print(_backtest(settings, store))
        elif args.command == "reset-paper":
            state = store.reset_paper(settings.paper_cash, _now())
            print(f"đã reset paper. Vốn {state.cash:.2f} USDT. Không có vị thế.")
        else:
            parser.error(f"lệnh không rõ: {args.command}")
    except BinanceError as exc:
        print(f"lỗi Binance demo: {exc}")
        return 1
    except KeyboardInterrupt:
        print("đã dừng bot.")
        return 0
    finally:
        store.close()
    return 0


def _once(settings, store: Store, client: BinanceDemoClient) -> str:
    now = _now()
    if settings.mode == "paper":
        return run_paper_once(settings, store, now)
    return run_demo_once(settings, store, client, now)


def _backtest(settings, store: Store) -> str:
    now = _now()
    candles = closed_candles(store.candles(settings.symbol, settings.interval), now)
    filters = store.filters(settings.symbol)
    if filters is None or len(candles) < settings.lookback + 2:
        return "chưa đủ dữ liệu. Chạy sync trước."
    result = backtest(
        candles,
        store.funding(settings.symbol),
        lookback=settings.lookback,
        entry_threshold=settings.entry_threshold,
        exit_threshold=settings.exit_threshold,
        max_abs_funding=settings.max_abs_funding,
        position_pct=settings.position_pct,
        stop_pct=settings.stop_pct,
        daily_loss_pct=settings.daily_loss_pct,
        fee_rate=settings.fee_rate,
        filters=filters,
        cash=settings.paper_cash,
    )
    wins = [trade for trade in result.trades if trade.pnl > 0]
    pnl = result.ending_equity - result.start_equity
    lines = [
        f"backtest {settings.symbol} {settings.interval} trên {len(candles)} nến đóng",
        f"vốn đầu {result.start_equity:.2f} -> cuối {result.ending_equity:.2f} ({pnl:+.2f})",
        f"số lệnh đóng {len(result.trades)} | thắng {len(wins)} | thua {len(result.trades) - len(wins)}",
        f"tổng phí {result.fees:.4f} | drawdown lớn nhất {result.max_drawdown * 100:.2f}%",
        "đây là kiểm tra cơ chế và phí, không phải dự báo lãi.",
    ]
    if result.halted and result.halt_reason:
        lines.append(result.halt_reason)
    if result.trades:
        lines.append("10 lệnh cuối:")
        for trade in result.trades[-10:]:
            lines.append(
                f"- {trade.side} {trade.entry_price:.4f} -> {trade.exit_price:.4f} "
                f"pnl {trade.pnl:.4f} | {trade.reason}"
            )
    return "\n".join(lines)


def _now() -> int:
    return int(time.time() * 1000)


if __name__ == "__main__":
    raise SystemExit(main())
