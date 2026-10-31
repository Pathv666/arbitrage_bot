from pathlib import Path
import pytest
from p2p_arb_bot.network_snapshot import upsert, show

def test_upsert_network_snapshot_replaces_same_key_and_preserves_others(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text("FOO=bar\nNETWORK_COSTS_JSON=[]\n", encoding="utf-8")
    upsert(env, exchange="Binance", asset="USDT", network="TRC20",
           deposit_enabled=True, withdraw_enabled=True, withdraw_fee="1",
           min_withdrawal="10", timestamp="2026-10-31T08:00:00+00:00")
    upsert(env, exchange="OKX", asset="USDT", network="TRC20",
           deposit_enabled=True, withdraw_enabled=True, withdraw_fee="0.8",
           min_withdrawal="2", timestamp="2026-10-31T08:01:00+00:00")
    upsert(env, exchange="Binance", asset="USDT", network="TRC20",
           deposit_enabled=True, withdraw_enabled=True, withdraw_fee="1.5",
           min_withdrawal="10", timestamp="2026-10-31T08:02:00+00:00")
    rows = show(env)
    assert len(rows) == 2
    b = next(x for x in rows if x["exchange"] == "Binance")
    assert b["withdraw_fee"] == "1.5"
    assert "FOO=bar" in env.read_text(encoding="utf-8")

def test_upsert_rejects_bad_number_and_naive_timestamp(tmp_path: Path):
    env = tmp_path / ".env"
    with pytest.raises(Exception):
        upsert(env, exchange="Binance", asset="USDT", network="TRC20",
               deposit_enabled=True, withdraw_enabled=True, withdraw_fee="-1",
               min_withdrawal="1")
    with pytest.raises(ValueError):
        upsert(env, exchange="Binance", asset="USDT", network="TRC20",
               deposit_enabled=True, withdraw_enabled=True, withdraw_fee="1",
               min_withdrawal="1", timestamp="2026-10-31T08:00:00")
