"""Observador privado de cuentas, órdenes y revisión UNI. Nunca ejecuta fondos."""
import asyncio
import time
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal

from ..domain.failures import CollectorError, public_failure
from ..domain.spot_review import sell_review
from .events import EventMonitor


class AccountMonitor:
    def __init__(self, collectors, market_collector, publisher, notifiers,
                 *, refresh_s: int = 30, ttl_s: int = 60,
                 min_changes=None):
        self.collectors=collectors; self.market_collector=market_collector
        self.publisher=publisher; self.events=EventMonitor(notifiers)
        self.refresh_s=refresh_s; self.ttl_s=ttl_s
        self.min_changes=min_changes or {'UNI':Decimal('0.001'),'USDT':Decimal('1')}
        self._last_poll=None; self._data={}; self._health={}
        self._balances={}; self._orders={}; self._order_initialized=set()
        self._summary={"status":"pending","collectors":{},"checkpoint":"BLOCKED"}

    async def snapshot(self) -> dict:
        now=datetime.now(timezone.utc)
        if self._last_poll is None or time.monotonic()-self._last_poll>=self.refresh_s:
            tasks={}
            for exchange,collector in self.collectors.items():
                tasks[f'{exchange}:balances']=collector.balances
                tasks[f'{exchange}:orders']=collector.orders
            tasks['BingX:market']=self.market_collector.market
            tasks['BingX:fees']=self.market_collector.fees
            async def poll(key, fetch):
                try:
                    data=await asyncio.wait_for(fetch(),timeout=90)
                    current=datetime.now(timezone.utc)
                    if key.endswith(":orders"):
                        pass  # timestamp de la orden no es timestamp del snapshot
                    elif isinstance(data,tuple):
                        if any(not self._fresh(x.timestamp,current) for x in data): raise CollectorError('schema')
                    elif not self._fresh(data.timestamp,current): raise CollectorError('schema')
                    self._data[key]=data
                    self._health[key]={'status':'ok','timestamp':current.isoformat()}
                except Exception as exc:
                    self._data.pop(key,None)
                    failure=public_failure(exc)
                    self._health[key]={'status':'not_configured' if failure['code']=='not_configured' else 'error',
                                       **failure,'timestamp':datetime.now(timezone.utc).isoformat()}
            await asyncio.gather(*(poll(key,fetch) for key,fetch in tasks.items()))
            self._last_poll=time.monotonic()
        now=datetime.now(timezone.utc)
        for key,health in self._health.items():
            if not self._fresh(datetime.fromisoformat(health['timestamp']),now):
                self._data.pop(key,None)
                health.update(status='stale',code='timeout',reason='Snapshot caducado; saldo no disponible')
            await self.events.observe('account_collector',key,health['status']+':'+health.get('code','ok'),
                                      f"{key}: {health['status']} · {health.get('reason','ok')}",now,
                                      initial=health['status']=='error')
        for key,data in self._data.items():
            if key.endswith(':balances'):
                for b in data:
                    identity=f'{b.exchange}:{b.scope}:{b.asset}'
                    old=self._balances.get(identity)
                    state=f'{b.available}:{b.locked}'
                    significant=old and max(abs(b.available-old.available),abs(b.locked-old.locked))>=self.min_changes[b.asset]
                    pending = self.events.observed.get(('balance',identity)) != self.events.notified.get(('balance',identity))
                    if old is None or significant or pending:
                        await self.events.observe('balance',identity,state,
                            f'{identity}: available={b.available}, locked={b.locked}',now)
                        if old is None or significant: self._balances[identity]=b
            if key.endswith(':orders'):
                exchange=key.split(':')[0]
                known=self._orders.setdefault(exchange,{})
                current={o.order_id:o for o in data}
                missing=[o.order_id for o in known.values() if o.status in {'pending','partial'} and o.order_id not in current]
                if missing:
                    self._health[key].update(status='incomplete',code='schema',reason='Tracked open order missing; no fill inferred')
                baseline=exchange not in self._order_initialized
                self._order_initialized.add(exchange)
                for order in data:
                    state=f'{order.status}:{order.executed}'
                    await self.events.observe('order',f'{exchange}:{order.order_id}',state,
                        f'{exchange} UNI-USDT order {order.order_id}: {order.status}, executed={order.executed}/{order.quantity}',now,
                        initial=not baseline and order.order_id not in known)
                    known[order.order_id]=order
        balances=self._data.get('BingX:balances',())
        uni=next((b for b in balances if b.asset=='UNI' and b.scope=='spot'),None)
        market=self._data.get('BingX:market'); fees=self._data.get('BingX:fees')
        review=sell_review(uni,market,fees,now,ttl_s=self.ttl_s) if uni and market and fees else {
            'ready':False,'reason':'Missing fresh UNI balance, market or authenticated fee metadata'}
        required={f'{e}:{kind}' for e in ('Binance','OKX','BingX') for kind in ('balances','orders')} | {'BingX:market','BingX:fees'}
        complete=required.issubset(self._health) and all(h['status']=='ok' for h in self._health.values())
        ready=review['ready'] and complete
        open_bingx=[o for o in self._data.get('BingX:orders',()) if o.status in {'pending','partial'}]
        if open_bingx:
            ready=False; review['open_orders_require_manual_review']=True
        snapshot={'last_update':now.isoformat(),'spot_only':True,'read_only':True,
                  'health':self._health,
                  'balances':[asdict(b) for key,data in self._data.items() if key.endswith(':balances') for b in data],
                  'orders':[asdict(o) for key,data in self._data.items() if key.endswith(':orders') for o in data],
                  'market':asdict(market) if market else None,'fees':asdict(fees) if fees else None,
                  'uni_review':review,'checkpoint':'READY_FOR_UNI_SELL_REVIEW' if ready else 'BLOCKED',
                  'order_coverage':'UNI-USDT only; API recent history window, not a complete ledger'}
        await self.publisher.publish_private(snapshot)
        self._summary = {'status':'ok' if complete else 'degraded',
                'collectors':{k:dict(v) for k,v in self._health.items()},'checkpoint':snapshot['checkpoint']}
        return self.public_health()

    def public_health(self):
        summary={**self._summary,'collectors':{k:dict(v) for k,v in self._summary['collectors'].items()}}
        now=datetime.now(timezone.utc)
        for health in summary['collectors'].values():
            if not self._fresh(datetime.fromisoformat(health['timestamp']),now):
                health['status']='stale'
                summary.update(status='degraded',checkpoint='BLOCKED')
        return summary

    async def run_forever(self):
        while True:
            try:
                await self.snapshot()
            except Exception:
                self._data.clear()
                self._summary={'status':'error','collectors':{},'checkpoint':'BLOCKED'}
            await asyncio.sleep(self.refresh_s)

    def _fresh(self,stamp,now):
        return stamp.tzinfo and 0 <= (now-stamp).total_seconds() <= self.ttl_s
