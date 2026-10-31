"""Contratos REST oficiales de consulta SPOT. Nunca crea/modifica/cancela órdenes."""
from datetime import datetime, timezone
from decimal import Decimal
import re

from ..domain.accounts import Balance, ObservedOrder
from ..domain.failures import CollectorError, safe_collector

ASSETS = ('UNI','USDT')
SYMBOL = 'UNI-USDT'


def decimal(value) -> Decimal:
    value=Decimal(str(value))
    if not value.is_finite() or value < 0:
        raise CollectorError('schema')
    return value


def timestamp_ms(value, *, max_age_s=None) -> datetime:
    stamp=datetime.fromtimestamp(int(value)/1000, timezone.utc)
    age=(datetime.now(timezone.utc)-stamp).total_seconds()
    if age < -2 or (max_age_s is not None and age > max_age_s):
        raise CollectorError('schema')
    return stamp


class AccountCollector:
    def __init__(self, client): self.client=client

    @safe_collector
    async def balances(self) -> tuple[Balance, ...]:
        e=self.client.exchange
        now=datetime.now(timezone.utc)
        if e=='Binance':
            data=await self.client.get('/api/v3/account')
            if data.get('accountType','SPOT')!='SPOT': raise CollectorError('schema')
            rows=data['balances']; scope='spot'
        elif e=='BingX':
            data=await self.client.get('/openApi/spot/v1/account/balance')
            rows=data['balances']; scope='spot'
        else:
            # Funding es efectivo; NO eq/availEq/unrealizedPnL de una cuenta unificada.
            rows=await self.client.get('/api/v5/asset/balances',{'ccy':','.join(ASSETS)})
            scope='funding'
        result=[]
        for a in ASSETS:
            selected=[r for r in rows if r.get('asset' if e!='OKX' else 'ccy')==a]
            if len(selected)>1: raise CollectorError('schema')
            if not selected and e=='OKX':
                # OKX documents funding balances as non-zero assets only.
                result.append(Balance(e,scope,a,Decimal('0'),Decimal('0'),now))
                continue
            if len(selected)!=1: raise CollectorError('schema')
            row=selected[0]
            free=decimal(row['free' if e!='OKX' else 'availBal'])
            locked=decimal(row['locked' if e!='OKX' else 'frozenBal'])
            if e=='OKX' and decimal(row['bal'])!=free+locked: raise CollectorError('schema')
            result.append(Balance(e,scope,a,free,locked,now))
        if e=='OKX':
            trading=await self.client.get('/api/v5/account/balance',{'ccy':','.join(ASSETS)})
            if len(trading)>1: raise CollectorError('schema')
            details=trading[0].get('details',[]) if trading else []
            if not isinstance(details,list): raise CollectorError('schema')
            for a in ASSETS:
                selected=[r for r in details if r.get('ccy')==a]
                if len(selected)>1: raise CollectorError('schema')
                if not selected:
                    # Trading balance endpoint likewise returns non-zero assets only.
                    result.append(Balance(e,'trading_cash',a,Decimal('0'),Decimal('0'),now))
                    continue
                row=selected[0]
                liab=row.get('liab','0')
                if liab not in ('',None) and decimal(liab)!=0: raise CollectorError('schema')
                cash=decimal(row['cashBal']); frozen=decimal(row['frozenBal'])
                if frozen>cash: raise CollectorError('schema')
                available=min(decimal(row['availBal']),cash-frozen)
                result.append(Balance(e,'trading_cash',a,available,frozen,now))
        return tuple(result)

    @safe_collector
    async def orders(self) -> tuple[ObservedOrder, ...]:
        e=self.client.exchange
        if e=='Binance':
            query={'symbol':'UNIUSDT'}
            pending=await self.client.get('/api/v3/openOrders',query)
            history=await self.client.get('/api/v3/allOrders',{**query,'limit':1000})
            if len(history)>=1000: raise CollectorError('schema')
        elif e=='BingX':
            pending=(await self.client.get('/openApi/spot/v1/trade/openOrders',{'symbol':SYMBOL}))['orders']
            history=[]
            # Official contract exposes data.orders and bounds pageIndex*pageSize
            # to 10,000; it does not require a total field.
            for page in range(1,101):
                data=await self.client.get('/openApi/spot/v1/trade/historyOrders',{'symbol':SYMBOL,'pageIndex':page,'pageSize':100})
                page_orders=data.get('orders')
                if not isinstance(page_orders,list): raise CollectorError('schema')
                history.extend(page_orders)
                if len(page_orders)<100: break
            else: raise CollectorError('schema')
        else:
            async def pages(path):
                result=[]; after=None
                for _ in range(10):
                    query={'instType':'SPOT','instId':SYMBOL,'limit':100}
                    if after: query['after']=after
                    rows=await self.client.get(path,query)
                    result.extend(rows)
                    if len(rows)<100: return result
                    if rows[-1]['ordId']==after: raise CollectorError('schema')
                    after=rows[-1]['ordId']
                raise CollectorError('schema')
            pending=await pages('/api/v5/trade/orders-pending')
            history=await pages('/api/v5/trade/orders-history')
        if not isinstance(pending,list) or not isinstance(history,list): raise CollectorError('schema')
        # Órdenes históricas terminales tienen precedencia sobre un open snapshot anterior.
        merged={}
        for source,rows in [('open',pending),('history',history)]:
            for row in rows:
                order=self.parse_order(row,source)
                old=merged.get(order.order_id)
                if old and old.timestamp>order.timestamp: continue
                merged[order.order_id]=order
        return tuple(merged.values())

    def parse_order(self, row, source):
        e=self.client.exchange
        raw=row['state' if e=='OKX' else 'status']
        mapping={'NEW':'pending','PENDING':'pending','live':'pending',
                 'PARTIALLY_FILLED':'partial','partially_filled':'partial',
                 'FILLED':'filled','filled':'filled','CANCELED':'cancelled','CANCELLED':'cancelled',
                 'canceled':'cancelled','FAILED':'failed','REJECTED':'failed','EXPIRED':'failed',
                 'mmp_canceled':'cancelled'}
        if raw not in mapping: raise CollectorError('schema')
        status=mapping[raw]
        quantity=decimal(row['sz' if e=='OKX' else 'origQty'])
        executed=decimal(row['accFillSz' if e=='OKX' else 'executedQty'])
        if quantity<=0 or executed>quantity: raise CollectorError('schema')
        if status=='filled' and (executed!=quantity or source=='open'): raise CollectorError('schema')
        if status=='partial' and not 0<executed<quantity: raise CollectorError('schema')
        if status=='pending' and executed: raise CollectorError('schema')
        identity=str(row['ordId' if e=='OKX' else 'orderId'])
        if not re.fullmatch(r'[0-9]{1,32}',identity): raise CollectorError('schema')
        symbol=row['instId' if e=='OKX' else 'symbol']
        if symbol not in {SYMBOL,'UNIUSDT'}: raise CollectorError('schema')
        side=str(row['side']).upper()
        if side not in {'BUY','SELL'}: raise CollectorError('schema')
        if e=='OKX' and (row.get('instType')!='SPOT' or row.get('tdMode')!='cash'): raise CollectorError('schema')
        stamp=timestamp_ms(row['uTime' if e=='OKX' else 'updateTime'])
        return ObservedOrder(e,identity,SYMBOL,side,status,quantity,executed,stamp,source)
