"""Monitor privado: transiciones y errores sin inferencias de fills o saldo."""
import asyncio
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D

import pytest
from p2p_arb_bot.application.account_monitor import AccountMonitor
from p2p_arb_bot.domain.failures import CollectorError
from p2p_arb_bot.domain.spot_review import Commission
from p2p_arb_bot.infrastructure.spot_collectors import AccountCollector
from p2p_arb_bot.uni_review import validate_output
from tests.test_spot_collectors import now, balances, market, order, Client


class Store:
    def __init__(self): self.latest=None
    async def publish_private(self,snapshot): self.latest=snapshot


class Sink:
    def __init__(self): self.events=[]
    async def notify_event(self,event): self.events.append(event)


class Collector:
    def __init__(self,e): self.e=e; self.b=balances(e); self.o=(); self.fail=False
    async def balances(self):
        if self.fail: raise RuntimeError('private-sentinel')
        return self.b
    async def orders(self): return self.o


class MarketCollector:
    async def market(self): return market()
    async def fees(self): return Commission(D('.001'),D('.002'),now())


def test_monitor_new_order_after_empty_baseline_and_balance_change_not_spam():
    async def run():
        store=Store(); sink=Sink(); collectors={e:Collector(e) for e in ('Binance','OKX','BingX')}
        monitor=AccountMonitor(collectors,MarketCollector(),store,[sink],refresh_s=0)
        monitor.events.cooldown_s=0
        await monitor.snapshot()
        assert not sink.events and store.latest['checkpoint']=='READY_FOR_UNI_SELL_REVIEW'
        c=collectors['BingX']; c.b=(replace(c.b[0],available=D('3')),c.b[1])
        c.o=(AccountCollector(Client('BingX',{})).parse_order(order(),'open'),)
        await monitor.snapshot()
        assert [e.category for e in sink.events]==['balance','order']
        assert store.latest['checkpoint']=='BLOCKED'
        assert store.latest['orders'][0]['status']=='pending'
        await monitor.snapshot()
        assert len(sink.events)==2
        c.o=()
        await monitor.snapshot()
        assert store.latest['health']['BingX:orders']['status']=='incomplete'
        assert not [e for e in sink.events if e.category=='order' and 'filled' in e.state]
        c.fail=True
        health=await monitor.snapshot()
        assert health['status']=='degraded' and 'balances' not in health
        assert not [b for b in store.latest['balances'] if b['exchange']=='BingX']
        assert 'sentinel' not in str(store.latest)+str(sink.events)
    asyncio.run(run())


@pytest.mark.parametrize('path',['.env','.env.backup','status.json'])
def test_report_cannot_overwrite_env_or_public_status(tmp_path,monkeypatch,path):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(CollectorError): validate_output(path)


def test_stale_account_snapshot_not_available_and_no_public_amounts():
    async def run():
        store=Store(); collectors={e:Collector(e) for e in ('Binance','OKX','BingX')}
        c=collectors['BingX']; c.b=tuple(replace(b,timestamp=now()-timedelta(minutes=5)) for b in c.b)
        monitor=AccountMonitor(collectors,MarketCollector(),store,[],refresh_s=0)
        health=await monitor.snapshot()
        assert store.latest['checkpoint']=='BLOCKED'
        assert not [b for b in store.latest['balances'] if b['exchange']=='BingX']
        assert 'available' not in str(health) and 'locked' not in str(health)
    asyncio.run(run())


def test_account_snapshot_publisher_failure_is_not_ready(tmp_path):
    from p2p_arb_bot.infrastructure.scanner_output import PrivateAccountStatus
    fake=tmp_path/'not-a-directory'; fake.write_text('fixture')
    with pytest.raises(CollectorError):
        asyncio.run(PrivateAccountStatus(str(fake/'status.json')).publish_private({'fixture':True}))


def test_unknown_account_errors_drop_cache_and_recover_without_zero_balance():
    async def run():
        store=Store(); c=Collector('BingX')
        monitor=AccountMonitor({'BingX':c},MarketCollector(),store,[],refresh_s=0)
        await monitor.snapshot()
        c.fail=True
        await monitor.snapshot()
        assert store.latest['balances']==[] and store.latest['checkpoint']=='BLOCKED'
        c.fail=False
        await monitor.snapshot()
        assert store.latest['balances'][0]['available']==2
    asyncio.run(run())


def test_checkpoint_requires_all_exchanges_even_if_bingx_is_healthy():
    async def run():
        store=Store()
        monitor=AccountMonitor({'BingX':Collector('BingX')},MarketCollector(),store,[],refresh_s=0)
        assert (await monitor.snapshot())['checkpoint']=='BLOCKED'
    asyncio.run(run())


def test_old_order_timestamp_is_not_a_stale_collection_snapshot():
    async def run():
        c=Collector('BingX')
        row=order('FILLED','2')
        row['updateTime']=int((now()-timedelta(days=1)).timestamp()*1000)
        c.o=(AccountCollector(Client('BingX',{})).parse_order(row,'history'),)
        store=Store(); monitor=AccountMonitor({'BingX':c},MarketCollector(),store,[],refresh_s=0)
        await monitor.snapshot()
        assert store.latest['health']['BingX:orders']['status']=='ok'
        assert store.latest['orders'][0]['status']=='filled'
    asyncio.run(run())


@pytest.mark.parametrize('prefix',['BINANCE','OKX','BINGX'])
def test_partial_credentials_block_only_dependent_collector(monkeypatch,tmp_path,prefix):
    from p2p_arb_bot import main
    from p2p_arb_bot.config import Defaults
    from p2p_arb_bot.application.scanner import ScannerService
    for name in ('BINANCE','OKX','BYBIT','BINGX'):
        for suffix in ('API_KEY','API_SECRET','API_PASSPHRASE'):
            monkeypatch.delenv(name+'_'+suffix,raising=False)
    monkeypatch.setenv(prefix+'_API_KEY','fixture')
    captured={}
    async def run(service,targets,interval):
        captured['networks']=await service.network_provider.snapshot()
        captured['health']=service.network_provider.health
    monkeypatch.setattr(ScannerService,'run_forever',run)
    asyncio.run(main.amain(Defaults(db_path=str(tmp_path/'db'),beep=False)))
    exchange={'BINANCE':'Binance','OKX':'OKX','BINGX':'BingX'}[prefix]
    assert captured['health'][exchange]['code']=='not_configured'
    assert captured['networks']==()


def test_scanner_reads_health_without_waiting_for_account_network(tmp_path):
    from p2p_arb_bot.application.scanner import ScannerService
    from p2p_arb_bot.domain.models import WatchTarget
    from p2p_arb_bot.infrastructure.scanner_output import ScannerStatus, ManualNetworkCosts
    from p2p_arb_bot.infrastructure.sqlite_repo import SQLiteRepository
    from p2p_arb_bot.scanner_config import ScannerSettings
    from tests.test_scanner_v1 import Source
    from tests.test_execution_v1 import networks
    class Background:
        def public_health(self): return {'status':'pending','collectors':{},'checkpoint':'BLOCKED'}
        async def snapshot(self): raise AssertionError('scanner must not await account I/O')
    async def run():
        repo=SQLiteRepository(str(tmp_path/'db'))
        service=ScannerService({e:Source(e) for e in ('Binance','OKX')},repo,
                               ScannerStatus(str(tmp_path/'status')),ManualNetworkCosts(networks()),
                               ScannerSettings(),[],account_monitor=Background())
        routes=await service.run_cycle(WatchTarget('USDT','VND',(),D('47300000'),D('0.1')))
        assert routes[0].feasible
        repo.close()
    asyncio.run(run())


def test_balance_reversion_is_observed_while_one_channel_is_unavailable():
    class Broken(Sink):
        async def notify_event(self,event): raise RuntimeError('fixture transport failure')
    async def run():
        console=Sink(); collectors={e:Collector(e) for e in ('Binance','OKX','BingX')}
        monitor=AccountMonitor(collectors,MarketCollector(),Store(),[console,Broken()],refresh_s=0)
        monitor.events.cooldown_s=0
        await monitor.snapshot()
        c=collectors['BingX']
        c.b=(replace(c.b[0],available=D('3')),c.b[1]); await monitor.snapshot()
        c.b=(replace(c.b[0],available=D('2')),c.b[1]); await monitor.snapshot()
        messages=[e for e in console.events if e.category=='balance']
        assert len(messages)==2 and 'available=2' in messages[-1].summary
    asyncio.run(run())


def test_console_public_log_does_not_reveal_private_balance_or_order_details():
    from io import StringIO
    from rich.console import Console
    from p2p_arb_bot.application.events import MonitorEvent
    from p2p_arb_bot.infrastructure.console_notifier import ConsoleNotifier
    output=StringIO(); notifier=ConsoleNotifier(beep=False,console=Console(file=output))
    for category in ('balance','order'):
        asyncio.run(notifier.notify_event(MonitorEvent(category,'private-order-id','private-state',
                                                      'private-balance-12345 private-order-id',now())))
    text=output.getvalue()
    assert 'private-balance-12345' not in text and 'private-order-id' not in text
    assert 'panel privado' in text
