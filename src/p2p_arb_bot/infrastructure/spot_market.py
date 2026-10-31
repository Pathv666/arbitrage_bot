"""BingX market/fee metadata oficiales, separado de órdenes y balances."""
from datetime import datetime, timezone

from ..domain.failures import CollectorError, safe_collector
from ..domain.spot_review import SpotMarket, Commission
from .spot_collectors import decimal, timestamp_ms, SYMBOL


class BingXMarketCollector:
    def __init__(self, client): self.client=client

    @safe_collector
    async def market(self) -> SpotMarket:
        metadata=await self.client.get('/openApi/spot/v1/common/symbols',{'symbol':SYMBOL})
        rows=[r for r in metadata['symbols'] if r['symbol']==SYMBOL]
        if len(rows)!=1: raise CollectorError('schema')
        row=rows[0]
        data=await self.client.get('/openApi/spot/v1/market/depth',{'symbol':SYMBOL,'limit':100})
        ticker=await self.client.get('/openApi/spot/v1/ticker/bookTicker',{'symbol':SYMBOL})
        if not isinstance(ticker,list) or len(ticker)!=1 or ticker[0]['symbol']!=SYMBOL:
            raise CollectorError('schema')
        ticker=ticker[0]
        def levels(key, reverse):
            out=tuple((decimal(p),decimal(q)) for p,q in data[key])
            if not out or any(p<=0 or q<=0 for p,q in out): raise CollectorError('schema')
            if out!=tuple(sorted(out,key=lambda v:v[0],reverse=reverse)): raise CollectorError('schema')
            if len({p for p,q in out})!=len(out): raise CollectorError('schema')
            return out
        bids=levels('bids',True); asks=levels('asks',False)
        bid=decimal(ticker['bidPrice']); ask=decimal(ticker['askPrice'])
        tick=decimal(row['tickSize']); step=decimal(row['stepSize'])
        if not 0<bid<ask or bids[0][0]>=asks[0][0] or tick<=0 or step<=0: raise CollectorError('schema')
        consistent=abs(bids[0][0]-bid)<=2*tick and abs(asks[0][0]-ask)<=2*tick
        min_qty=decimal(row['minQty']); max_qty=decimal(row['maxQty'])
        min_notional=decimal(row['minNotional']); max_notional=decimal(row['maxNotional'])
        if row.get('maxMarketNotional') is not None:
            max_notional=min(max_notional,decimal(row['maxMarketNotional']))
        if max_qty<min_qty or max_notional<min_notional: raise CollectorError('schema')
        return SpotMarket(SYMBOL,bids,asks,bid,ask,timestamp_ms(data['ts'],max_age_s=60),
                          timestamp_ms(ticker['time'],max_age_s=60),datetime.now(timezone.utc),
                          tick,step,min_qty,max_qty,min_notional,max_notional,
                          row['status']==1 and row.get('apiStateSell') is True,consistent)

    @safe_collector
    async def fees(self) -> Commission:
        row=await self.client.get('/openApi/spot/v1/user/commissionRate',{'symbol':SYMBOL})
        if row['symbol']!=SYMBOL: raise CollectorError('schema')
        maker,taker=decimal(row['makerCommissionRate']),decimal(row['takerCommissionRate'])
        if max(maker,taker)>1: raise CollectorError('schema')
        return Commission(maker,taker,datetime.now(timezone.utc))
