"""Manage verified network-cost snapshots without hand-editing JSON."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

KEY = "NETWORK_COSTS_JSON"

def _bool(value: str) -> bool:
    v = value.strip().lower()
    if v in {"1", "true", "yes", "y", "on"}:
        return True
    if v in {"0", "false", "no", "n", "off"}:
        return False
    raise argparse.ArgumentTypeError("expected true/false")

def _nonnegative(value: str) -> str:
    d = Decimal(value)
    if not d.is_finite() or d < 0:
        raise argparse.ArgumentTypeError("expected finite non-negative number")
    return str(d)

def _read_env(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []

def _read_snapshots(lines: list[str]) -> list[dict]:
    for line in lines:
        if line.startswith(KEY + "="):
            raw = line.split("=", 1)[1].strip() or "[]"
            data = json.loads(raw)
            if not isinstance(data, list):
                raise ValueError(f"{KEY} must be a JSON list")
            return data
    return []

def _write_snapshots(path: Path, lines: list[str], snapshots: list[dict]) -> None:
    encoded = json.dumps(snapshots, separators=(",", ":"), ensure_ascii=False)
    replacement = f"{KEY}={encoded}"
    out = []
    replaced = False
    for line in lines:
        if line.startswith(KEY + "="):
            if not replaced:
                out.append(replacement)
                replaced = True
            continue
        out.append(line)
    if not replaced:
        if out and out[-1] != "":
            out.append("")
        out.append(replacement)
    path.write_text("\n".join(out) + "\n", encoding="utf-8")

def upsert(path: Path, *, exchange: str, asset: str, network: str,
           deposit_enabled: bool, withdraw_enabled: bool,
           withdraw_fee: str, min_withdrawal: str,
           timestamp: str | None = None) -> dict:
    lines = _read_env(path)
    snapshots = _read_snapshots(lines)
    ts = timestamp or datetime.now(timezone.utc).isoformat()
    parsed = datetime.fromisoformat(ts)
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    item = {
        "exchange": exchange,
        "asset": asset,
        "network": network,
        "deposit_enabled": bool(deposit_enabled),
        "withdraw_enabled": bool(withdraw_enabled),
        "withdraw_fee": _nonnegative(withdraw_fee),
        "min_withdrawal": _nonnegative(min_withdrawal),
        "timestamp": parsed.isoformat(),
    }
    key = (exchange, asset, network)
    snapshots = [
        x for x in snapshots
        if (x.get("exchange"), x.get("asset"), x.get("network")) != key
    ]
    snapshots.append(item)
    snapshots.sort(key=lambda x: (x["asset"], x["network"], x["exchange"]))
    _write_snapshots(path, lines, snapshots)
    return item

def show(path: Path) -> list[dict]:
    return _read_snapshots(_read_env(path))

def main() -> int:
    parser = argparse.ArgumentParser(description="Manage NETWORK_COSTS_JSON snapshots")
    parser.add_argument("--env", default=".env")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("show")
    p = sub.add_parser("set")
    p.add_argument("--exchange", required=True, choices=["Binance", "OKX", "Bybit", "BingX"])
    p.add_argument("--asset", default="USDT")
    p.add_argument("--network", required=True)
    p.add_argument("--deposit-enabled", type=_bool, required=True)
    p.add_argument("--withdraw-enabled", type=_bool, required=True)
    p.add_argument("--withdraw-fee", type=_nonnegative, required=True)
    p.add_argument("--min-withdrawal", type=_nonnegative, required=True)
    p.add_argument("--timestamp")
    args = parser.parse_args()
    env = Path(args.env)
    if args.cmd == "show":
        rows = show(env)
        if not rows:
            print("No network snapshots configured.")
            return 0
        now = datetime.now(timezone.utc)
        for row in rows:
            ts = datetime.fromisoformat(row["timestamp"])
            age = (now - ts.astimezone(timezone.utc)).total_seconds()
            print(
                f'{row["exchange"]:8} {row["asset"]}/{row["network"]:8} '
                f'deposit={row["deposit_enabled"]} withdraw={row["withdraw_enabled"]} '
                f'fee={row["withdraw_fee"]} min={row["min_withdrawal"]} '
                f'age={age:.0f}s {row["timestamp"]}'
            )
        return 0
    item = upsert(
        env, exchange=args.exchange, asset=args.asset, network=args.network,
        deposit_enabled=args.deposit_enabled, withdraw_enabled=args.withdraw_enabled,
        withdraw_fee=args.withdraw_fee, min_withdrawal=args.min_withdrawal,
        timestamp=args.timestamp,
    )
    print(json.dumps(item, indent=2, ensure_ascii=False))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
