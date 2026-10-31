"""Simulación SPOT de liquidez; ningún resultado constituye efectivo disponible."""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_DOWN

from .accounts import Balance


@dataclass(frozen=True, slots=True)
class SpotMarket:
    symbol: str
    bids: tuple[tuple[Decimal,Decimal], ...]
    asks: tuple[tuple[Decimal,Decimal], ...]
    bid: Decimal
    ask: Decimal
    depth_timestamp: datetime
    ticker_timestamp: datetime
    timestamp: datetime
    tick_size: Decimal
    step_size: Decimal
    min_quantity: Decimal
    max_quantity: Decimal
    min_notional: Decimal
    max_notional: Decimal
    active: bool
    consistent: bool


@dataclass(frozen=True, slots=True)
class Commission:
    maker_rate: Decimal
    taker_rate: Decimal
    timestamp: datetime


def sell_review(balance: Balance, market: SpotMarket, fee: Commission, now: datetime,
                *, ttl_s: int = 60) -> dict:
    monetary = (balance.available,balance.locked,fee.maker_rate,fee.taker_rate)
    positive = (market.step_size,market.tick_size,market.min_quantity,market.max_quantity,
                market.min_notional,market.max_notional,market.bid,market.ask)
    if (not all(v.is_finite() and v>=0 for v in monetary)
            or max(fee.maker_rate,fee.taker_rate)>1
            or not all(v.is_finite() and v>0 for v in positive)
            or not market.bids or not market.asks
            or any(not p.is_finite() or not q.is_finite() or p<=0 or q<=0 for p,q in market.bids+market.asks)):
        return {'ready':False,'reason':'invalid monetary inputs'}
    def fresh(timestamp):
        return timestamp.tzinfo and 0 <= (now-timestamp).total_seconds() <= ttl_s
    if not all(fresh(t) for t in (balance.timestamp,market.depth_timestamp,market.ticker_timestamp,market.timestamp,fee.timestamp)):
        return {'ready':False,'reason':'snapshot stale or future'}
    if balance.exchange!='BingX' or balance.scope!='spot' or balance.asset!='UNI' or market.symbol!='UNI-USDT':
        return {'ready':False,'reason':'wrong account scope or symbol'}
    if not market.active or not market.consistent:
        return {'ready':False,'reason':'inactive or inconsistent market snapshot'}
    quantity=(balance.available/market.step_size).to_integral_value(rounding=ROUND_DOWN)*market.step_size
    if not market.min_quantity<=quantity<=market.max_quantity or quantity<=0:
        return {'ready':False,'reason':'available quantity outside symbol limits'}
    remaining=quantity; gross=Decimal('0'); legs=[]
    for price,capacity in market.bids:
        allocation=min(remaining,capacity)
        if allocation:
            gross+=price*allocation
            legs.append({'price':str(price),'quantity':str(allocation)})
            remaining-=allocation
        if not remaining: break
    filled=quantity-remaining
    vwap=gross/filled if filled else Decimal('0')
    ready=not remaining and market.min_notional<=gross<=market.max_notional
    return {'ready':bool(ready),'reason':'' if ready else 'insufficient depth or notional outside limits',
            'hypothetical':True,'quantity':str(quantity),'available_uni':str(balance.available),
            'locked_uni_excluded':str(balance.locked),'unfilled_uni':str(remaining),
            'gross_quote_equivalent':str(gross),'vwap':str(vwap),
            'slippage_pct':str((market.bids[0][0]-vwap)/market.bids[0][0]*100),
            'taker_rate':str(fee.taker_rate),'estimated_fee_quote_equivalent':str(gross*fee.taker_rate),
            'estimated_net_quote_equivalent':str(gross*(1-fee.taker_rate)),
            'fee_assumption':'quote equivalent; confirm fee asset/discounts manually',
            'available_cash':False,'legs':legs}
