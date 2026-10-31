"""Regresiones de profundidad VND, inventario y costes sobre asset transferido."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from p2p_arb_bot.domain.models import Ad
from p2p_arb_bot.domain.execution import (
    blend, eligible, optimize_routes, CostPolicy, NetworkCost, RiskPolicy,
)
from p2p_arb_bot.domain.payments import normalize_payment

NOW = datetime.now(timezone.utc)
RISK = RiskPolicy(max_payment_minutes=30)


def ad(id="a", price="25785", cap="100000000", side="BUY", exchange="Binance", **kwargs):
    return Ad(id, D(price), D("100000"), D("100000"), D(cap), ("Vietcombank",),
              id, id, side, exchange=exchange, payment_time_minutes=15,
              completed_orders=500, completion_rate=D("99"), timestamp=NOW, **kwargs)


def networks(fee="1", minimum="1", timestamp=NOW):
    return tuple(NetworkCost(e, "USDT", "TRC20", True, True, D(fee), D(minimum), timestamp)
                 for e in ("Binance", "OKX", "Bybit"))


def books():
    return {"Binance": [ad()], "OKX": [ad("s", "26200", side="SELL", exchange="OKX")]}


def optimize(book=None, **kwargs):
    defaults = dict(asset="USDT", fiat="VND", size=D("47300000"), risk=RISK,
                    costs=CostPolicy(), networks=networks(), now=NOW)
    defaults.update(kwargs)
    return optimize_routes(book or books(), **defaults)


def test_golden_473m_blends_three_ads():
    ads = [ad("1", cap="26000000"), ad("2", price="25786", cap="12000000"),
           ad("3", price="25804", cap="20000000")]
    fill = blend(ads, "BUY", D("47300000"), RISK, NOW)
    expected_asset = D("26000000")/D("25785")+D("12000000")/D("25786")+D("9300000")/D("25804")
    assert fill.total_asset == expected_asset
    assert fill.total_fiat == D("47300000")
    assert fill.effective_price == D("47300000")/expected_asset
    assert D("25788") < fill.effective_price < D("25790")
    assert fill.effective_price != D("25785")
    assert len(fill.legs) == fill.counterparties == 3
    assert fill.unfilled == 0


def test_partial_fill_inventory_max_and_min():
    a = replace(ad(), surplus=D("10"), min_amount=D("100000"), max_amount=D("500000"))
    fill = blend([a], "BUY", D("500000"), RISK, NOW)
    assert fill.total_fiat == D("257850")
    assert fill.unfilled == D("242150")
    assert blend([a], "BUY", D("99999"), RISK, NOW).total_asset == 0
    assert blend([ad(cap="200000")], "BUY", D("500000"), RISK, NOW).total_fiat == 200000


def test_stale_future_missing_timezone_and_outlier():
    base = ad()
    for timestamp in (NOW-timedelta(seconds=46), NOW+timedelta(seconds=1), NOW.replace(tzinfo=None)):
        assert not eligible([replace(base, timestamp=timestamp)], "BUY", RISK, NOW)
    assert [a.adv_no for a in eligible([ad("1"), ad("2"), ad("bait", price="20000")], "BUY", RISK, NOW)] == ["1", "2"]


@pytest.mark.parametrize("change", [
    {"completion_rate": D("90")}, {"completed_orders": 4},
    {"completion_rate": None}, {"completed_orders": None},
    {"payment_time_minutes": 45}, {"payment_time_minutes": None},
    {"pay_methods": ("TCB",)}, {"advertiser_no": "excluded"},
    {"price": D("NaN")}, {"price": D("0")}, {"surplus": D("0")},
    {"min_amount": D("-1")}, {"trade_type": "SELL"},
])
def test_merchant_and_defensive_filters(change):
    risk = replace(RISK, min_completion_rate=D("98"), min_completed_orders=100,
                   allowed_payments=("VCB",), excluded_merchants=("excluded",))
    assert not eligible([replace(ad(), **change)], "BUY", risk, NOW)


def test_payment_registry_and_duplicate_ads():
    assert normalize_payment("Vietcombank") == normalize_payment("VCB")
    assert normalize_payment("Techcombank") == "TCB"
    assert len(eligible([ad(), ad()], "BUY", RISK, NOW)) == 1


def test_optimizer_all_directions_and_network_fee_once():
    book = {
        "Binance": [ad("b", "27000", side="SELL")],
        "OKX": [ad("o", "25000", exchange="OKX")],
        "Bybit": [ad("y", "26000", side="SELL", exchange="Bybit")],
    }
    routes = optimize(book)
    assert len(routes) == 6
    best = routes[0]
    assert best.buy_exchange == "OKX" and best.sell_exchange == "Binance"
    assert best.feasible
    expected = (D("47300000")/25000-1)*27000
    assert abs(best.sell.total_fiat-expected) < D("0.000001")
    assert dict(best.estimated_costs)["network"] == 27000
    assert abs(best.net_edge_pct-((expected-D("47300000")-D("94600"))/D("47300000")*100)) < D("0.000000001")


def test_cost_deductions_and_actual_sell_size():
    cost = CostPolicy(p2p_buy_pct=D("0.1"), p2p_sell_pct=D("0.2"), trading_pct=D("0.3"),
                      tax_pct=D("0.4"), safety_pct=D("0.5"))
    best = optimize(costs=cost)[0]
    expected_asset = best.buy.total_asset*D("0.999")-1
    assert abs(best.sell.total_asset-expected_asset) < D("0.00000000001")
    expected_income = expected_asset*26200
    expected_extra = expected_income*D("0.006")+D("47300000")*D("0.008")
    assert abs(best.net_edge_pct-(expected_income-D("47300000")-expected_extra)/D("47300000")*100) < D("0.00000001")


@pytest.mark.parametrize("net", [(), networks(timestamp=NOW-timedelta(days=2)),
                                  networks(minimum="10000"), networks(fee="10000"),
                                  tuple(replace(n, deposit_enabled=False) for n in networks()),
                                  tuple(replace(n, withdraw_enabled=False) for n in networks())])
def test_unknown_stale_disabled_networks_and_minimum_reject(net):
    assert not any(r.feasible for r in optimize(networks=net))


def test_partial_sell_rejected_and_counterparty_limit_penalty():
    book = books()
    book["OKX"] = [ad("s", "26200", cap="20000000", side="SELL", exchange="OKX")]
    assert not optimize(book)[0].feasible
    book["OKX"].append(ad("s2", "26190", side="SELL", exchange="OKX"))
    route = optimize(book)[0]
    assert route.feasible and route.counterparties == 3
    assert dict(route.estimated_costs)["counterparty"] == D("9460")
    compact = optimize(book, risk=replace(RISK, max_counterparties=2))[0]
    assert compact.feasible and compact.counterparties == 2
    assert [leg.ad.adv_no for leg in compact.sell.legs] == ["s2"]


def test_two_networks_chooses_lower_cost_and_cross_asset_rejected():
    values = networks()+tuple(replace(n, network="OTHER", withdraw_fee=D("10")) for n in networks())
    routes = optimize(networks=values)
    assert routes[0].network == "TRC20"
    book = books()
    book["OKX"] = [replace(book["OKX"][0], fiat="USD")]
    assert not any(r.feasible for r in optimize(book))


def test_latest_disabled_network_overrides_old_enabled_snapshot_and_no_duplicate_routes():
    old = tuple(replace(n, timestamp=NOW-timedelta(seconds=10)) for n in networks())
    newer_disabled = tuple(replace(n, withdraw_enabled=False) for n in networks())
    assert not any(r.feasible for r in optimize(networks=old+newer_disabled))
    assert len(optimize(networks=old+networks()+networks())) == 2


def test_optimizer_prefers_executable_compact_route_over_many_tiny_cheaper_ads():
    buy_ads = [
        ad(f"tiny{i}", price=str(25740 + i), cap="3000000")
        for i in range(10)
    ]
    buy_ads += [
        ad("large1", price="25810", cap="26000000"),
        ad("large2", price="25820", cap="25000000"),
    ]
    sell_ads = [
        ad("sell1", price="26500", cap="30000000", side="SELL", exchange="OKX"),
        ad("sell2", price="26490", cap="30000000", side="SELL", exchange="OKX"),
    ]
    route = optimize(
        {"Binance": buy_ads, "OKX": sell_ads},
        risk=replace(RISK, max_counterparties=4),
    )[0]
    assert route.feasible
    assert route.buy.unfilled == 0
    assert route.sell.unfilled == 0
    assert route.counterparties <= 4
    assert {leg.ad.adv_no for leg in route.buy.legs} <= {"large1", "large2"}


def test_optimizer_total_counterparty_budget_is_shared_across_both_sides():
    buy_ads = [
        ad("b1", price="25780", cap="25000000"),
        ad("b2", price="25790", cap="25000000"),
    ]
    sell_ads = [
        ad("s1", price="26500", cap="25000000", side="SELL", exchange="OKX"),
        ad("s2", price="26490", cap="25000000", side="SELL", exchange="OKX"),
    ]
    assert optimize(
        {"Binance": buy_ads, "OKX": sell_ads},
        risk=replace(RISK, max_counterparties=3),
    )[0].feasible is False
    route = optimize(
        {"Binance": buy_ads, "OKX": sell_ads},
        risk=replace(RISK, max_counterparties=4),
    )[0]
    assert route.feasible and route.counterparties == 4


def test_network_diagnostics_explain_directional_blockers():
    source = NetworkCost(
        "Binance", "USDT", "TRC20", True, True, D("1"), D("10"), NOW
    )
    destination = NetworkCost(
        "OKX", "USDT", "TRC20", False, True, D("0"), D("0"), NOW
    )
    route = optimize(networks=(source, destination))[0]
    assert route.feasible is False
    assert route.network == ""
    diag = next(d for d in route.network_diagnostics if d.network == "TRC20")
    assert diag.source_present and diag.source_withdraw_enabled and diag.source_fresh
    assert diag.source_fee == D("1") and diag.source_min_withdrawal == D("10")
    assert diag.destination_present and not diag.destination_deposit_enabled
    assert diag.destination_fresh
    assert "depósito deshabilitado en destino" in diag.reason


def test_network_diagnostics_show_missing_side():
    source = NetworkCost(
        "Binance", "USDT", "BSC", True, True, D("0.1"), D("1"), NOW
    )
    destination = NetworkCost(
        "OKX", "USDT", "ARBITRUM", True, True, D("0"), D("0"), NOW
    )
    route = optimize(networks=(source, destination))[0]
    bsc = next(d for d in route.network_diagnostics if d.network == "BSC")
    arb = next(d for d in route.network_diagnostics if d.network == "ARBITRUM")
    assert bsc.source_present and not bsc.destination_present
    assert "red ausente en destino" in bsc.reason
    assert not arb.source_present and arb.destination_present
    assert "red ausente en origen" in arb.reason
