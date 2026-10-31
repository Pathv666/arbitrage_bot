"""Contratos oficiales, fail-closed y balances/órdenes sin llamadas de escritura."""
import asyncio
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from types import SimpleNamespace

import pytest

from p2p_arb_bot.domain.accounts import Balance
from p2p_arb_bot.domain.failures import CollectorError
from p2p_arb_bot.domain.spot_review import SpotMarket, Commission, sell_review
from p2p_arb_bot.infrastructure.spot_client import SpotReadClient
from p2p_arb_bot.infrastructure.spot_collectors import AccountCollector
from p2p_arb_bot.infrastructure.spot_market import BingXMarketCollector


def now(): return datetime.now(timezone.utc)


def market():
    return SpotMarket('UNI-USDT',((D('7'),D('1')),(D('6.9'),D('9'))),((D('7.1'),D('10')),),
                      D('7'),D('7.1'),now(),now(),now(),D('.001'),D('.001'),D('.1'),D('1000'),D('1'),D('100000'),True,True)


def balances(exchange='BingX'):
    return (Balance(exchange,'spot','UNI',D('2'),D('5'),now()), Balance(exchange,'spot','USDT',D('10'),D('1'),now()))


@pytest.mark.parametrize('exchange',['Binance','OKX','BingX'])
@pytest.mark.parametrize('path',['/api/v3/order','/openApi/spot/v1/trade/order','/api/v5/trade/order','/sapi/v1/capital/withdraw/apply','/api/v5/account/set-leverage','/swap/v2/order'])
def test_mutating_or_derivative_paths_rejected_before_transport(exchange,path):
    with pytest.raises(CollectorError) as error:
        asyncio.run(SpotReadClient(exchange).get(path))
    assert error.value.code=='access_denied'


@pytest.mark.parametrize('url',['http://api.binance.com','https://api.binance.com.evil.example','https://user:password@example.com','https://example.com','https://api.binance.com/other'])
def test_official_host_required(url):
    with pytest.raises(CollectorError): SpotReadClient('Binance',base_url=url)


def test_transport_error_never_retains_signature_headers_or_credentials(monkeypatch):
    class Session:
        async def __aenter__(self): return self
        async def __aexit__(self,*args): pass
        async def get(self,*args,**kwargs): raise RuntimeError('private-sentinel URL signature key passphrase')
    monkeypatch.setattr('p2p_arb_bot.infrastructure.spot_client.AsyncSession',Session)
    with pytest.raises(CollectorError) as error:
        asyncio.run(SpotReadClient('BingX','fixture','fixture').get('/openApi/spot/v1/account/balance'))
    assert 'sentinel' not in str(error.value) and error.value.__context__ is None


@pytest.mark.parametrize('exchange,path',[('Binance','/api/v3/account'),('OKX','/api/v5/asset/balances'),('BingX','/openApi/spot/v1/account/balance')])
def test_authenticated_calls_are_get_only_no_redirects_and_sanitized_api_errors(monkeypatch,exchange,path):
    calls=[]
    class Session:
        async def __aenter__(self): return self
        async def __aexit__(self,*args): pass
        async def get(self,url,**kwargs):
            calls.append((url,kwargs))
            return SimpleNamespace(status_code=200,json=lambda:{'code':-2015,'msg':'private-sentinel'})
    monkeypatch.setattr('p2p_arb_bot.infrastructure.spot_client.AsyncSession',Session)
    with pytest.raises(CollectorError):
        asyncio.run(SpotReadClient(exchange,'fixture','fixture','fixture').get(path))
    assert calls[0][1]['allow_redirects'] is False


def test_missing_credentials_fail_without_network():
    with pytest.raises(CollectorError) as error:
        asyncio.run(SpotReadClient('BingX').get('/openApi/spot/v1/account/balance'))
    assert error.value.code=='not_configured'


class Client:
    def __init__(self,exchange,payloads): self.exchange=exchange; self.payloads=payloads
    async def get(self,path,params=None): return self.payloads[path]


@pytest.mark.parametrize('exchange',['Binance','BingX'])
def test_spot_balance_free_locked_and_missing_asset_not_zero(exchange):
    path='/api/v3/account' if exchange=='Binance' else '/openApi/spot/v1/account/balance'
    rows=[{'asset':'UNI','free':'2','locked':'5'},{'asset':'USDT','free':'10','locked':'0'}]
    collector=AccountCollector(Client(exchange,{path:{'balances':rows}}))
    result=asyncio.run(collector.balances())
    assert result[0].available==2 and result[0].total==7
    rows.pop()
    with pytest.raises(CollectorError): asyncio.run(collector.balances())


def test_okx_cash_never_uses_unrealized_pnl_or_unified_equity():
    funding=[{'ccy':a,'availBal':'2','frozenBal':'1','bal':'3'} for a in ('UNI','USDT')]
    details=[{'ccy':a,'availBal':'1000','cashBal':'10','frozenBal':'2','eq':'9000','upl':'8990','liab':'0'} for a in ('UNI','USDT')]
    c=AccountCollector(Client('OKX',{'/api/v5/asset/balances':funding,'/api/v5/account/balance':[{'details':details}]}))
    rows=asyncio.run(c.balances())
    assert len(rows)==4 and rows[-1].scope=='trading_cash' and rows[-1].available==8
    details[0]['liab']='1'
    with pytest.raises(CollectorError): asyncio.run(c.balances())


def order(status='NEW',executed='0',identity='1'):
    return {'orderId':identity,'symbol':'UNI-USDT','side':'SELL','origQty':'2','executedQty':executed,
            'status':status,'updateTime':int(now().timestamp()*1000)}


@pytest.mark.parametrize('raw,executed,expected',[('NEW','0','pending'),('PARTIALLY_FILLED','1','partial'),('FILLED','2','filled'),('CANCELED','1','cancelled'),('FAILED','0','failed')])
def test_order_status_mapping(raw,executed,expected):
    c=AccountCollector(Client('BingX',{}))
    assert c.parse_order(order(raw,executed),'history').status==expected


@pytest.mark.parametrize('raw,executed,source',[('FILLED','0','history'),('FILLED','2','open'),('NEW','1','open'),('PARTIALLY_FILLED','0','history'),('UNKNOWN','0','history')])
def test_open_order_is_not_fill_and_inconsistent_execution_rejects(raw,executed,source):
    with pytest.raises(CollectorError): AccountCollector(Client('BingX',{})).parse_order(order(raw,executed),source)


def test_history_terminal_order_wins_over_earlier_open_snapshot():
    pending=order(); terminal=order('FILLED','2')
    c=AccountCollector(Client('BingX',{'/openApi/spot/v1/trade/openOrders':{'orders':[pending]},
                                   '/openApi/spot/v1/trade/historyOrders':{'orders':[terminal]}}))
    rows=asyncio.run(c.orders())
    assert len(rows)==1 and rows[0].status=='filled'


def test_sell_inputs_use_available_only_walk_depth_and_deduct_actual_taker_rate():
    m=market(); b=balances()[0]; fee=Commission(D('.001'),D('.002'),now())
    result=sell_review(b,m,fee,now())
    assert result['ready'] and result['quantity']=='2'
    assert len(result['legs'])==2 and D(result['vwap'])==D('6.95')
    assert D(result['gross_quote_equivalent'])==D('13.9')
    assert D(result['estimated_fee_quote_equivalent'])==D('.0278')
    assert result['available_cash'] is False and result['locked_uni_excluded']=='5'
    assert not sell_review(replace(b,available=D('20')),m,fee,now())['ready']
    assert not sell_review(b,replace(m,consistent=False),fee,now())['ready']
    assert not sell_review(b,m,replace(fee,timestamp=now()-timedelta(minutes=2)),now())['ready']


def market_payloads():
    stamp=int(now().timestamp()*1000)
    return {'/openApi/spot/v1/common/symbols':{'symbols':[{'symbol':'UNI-USDT','tickSize':'.001','stepSize':'.001','minQty':'.1','maxQty':'100','minNotional':'1','maxNotional':'10000','status':1,'apiStateSell':True}]},
            '/openApi/spot/v1/market/depth':{'bids':[['7','1'],['6.9','9']],'asks':[['7.1','10']],'ts':stamp},
            '/openApi/spot/v1/ticker/bookTicker':[{'symbol':'UNI-USDT','bidPrice':'7','askPrice':'7.1','time':stamp}],
            '/openApi/spot/v1/user/commissionRate':{'symbol':'UNI-USDT','makerCommissionRate':'.001','takerCommissionRate':'.002'}}


def test_official_market_depth_symbols_and_account_fee_contracts():
    c=BingXMarketCollector(Client('BingX',market_payloads()))
    m=asyncio.run(c.market()); fee=asyncio.run(c.fees())
    assert m.active and m.consistent and m.bid==7 and m.ask==D('7.1')
    assert len(m.bids)==2 and fee.taker_rate==D('.002')


@pytest.mark.parametrize('change',['stale','crossed','missing_ts','nan','duplicate','missing_fee','wrong_symbol'])
def test_incomplete_market_and_fee_inputs_fail_closed(change):
    payloads=market_payloads()
    depth=payloads['/openApi/spot/v1/market/depth']
    if change=='stale': depth['ts']=int((now()-timedelta(minutes=10)).timestamp()*1000)
    if change=='crossed': depth['bids'][0][0]='8'
    if change=='missing_ts': depth.pop('ts')
    if change=='nan': depth['bids'][0][0]='NaN'
    if change=='duplicate': depth['bids'].append(depth['bids'][0])
    if change=='missing_fee': payloads['/openApi/spot/v1/user/commissionRate'].pop('takerCommissionRate')
    if change=='wrong_symbol': payloads['/openApi/spot/v1/ticker/bookTicker'][0]['symbol']='OTHER-USDT'
    c=BingXMarketCollector(Client('BingX',payloads))
    with pytest.raises(CollectorError): asyncio.run(c.fees() if change=='missing_fee' else c.market())


def test_timestamp_disagreement_is_not_sell_ready():
    payloads=market_payloads()
    payloads['/openApi/spot/v1/ticker/bookTicker'][0]['bidPrice']='6.5'
    m=asyncio.run(BingXMarketCollector(Client('BingX',payloads)).market())
    assert not m.consistent


def test_binance_and_okx_order_query_contracts():
    for exchange,payloads in [
        ('Binance',{'/api/v3/openOrders':[order()],'/api/v3/allOrders':[order('FILLED','2')]}),
        ('OKX',{'/api/v5/trade/orders-pending':[], '/api/v5/trade/orders-history':[
            {'ordId':'1','instId':'UNI-USDT','instType':'SPOT','tdMode':'cash','side':'sell','sz':'2','accFillSz':'2','state':'filled','uTime':int(now().timestamp()*1000)}]})]:
        rows=asyncio.run(AccountCollector(Client(exchange,payloads)).orders())
        assert rows[0].status=='filled'


def test_margin_spot_order_is_not_cash_spot_order():
    row={'ordId':'1','instId':'UNI-USDT','instType':'SPOT','tdMode':'cross','side':'sell',
         'sz':'2','accFillSz':'0','state':'live','uTime':int(now().timestamp()*1000)}
    with pytest.raises(CollectorError): AccountCollector(Client('OKX',{})).parse_order(row,'open')


def test_bingx_reverse_ask_order_is_normalized_before_quote_comparison():
    payloads=market_payloads()
    payloads['/openApi/spot/v1/market/depth']['asks']=[['7.3','1'],['7.1','10']]
    result=asyncio.run(BingXMarketCollector(Client('BingX',payloads)).market())
    assert result.asks[0][0]==D('7.1') and result.consistent


def test_read_permission_rejection_never_suggests_enabling_trade():
    from p2p_arb_bot.domain.failures import check_api
    with pytest.raises(CollectorError) as error: check_api(100004)
    assert error.value.code=='read_permission'
    assert 'no habilite Trade/Withdraw' in str(error.value)


def test_okx_missing_zero_balance_assets_are_zero_not_schema_error():
    c=AccountCollector(Client('OKX',{
        '/api/v5/asset/balances':[],
        '/api/v5/account/balance':[],
    }))
    rows=asyncio.run(c.balances())
    assert [(r.scope,r.asset,r.available,r.locked) for r in rows] == [
        ('funding','UNI',D('0'),D('0')),
        ('funding','USDT',D('0'),D('0')),
        ('trading_cash','UNI',D('0'),D('0')),
        ('trading_cash','USDT',D('0'),D('0')),
    ]


def test_okx_partial_nonzero_response_zero_fills_missing_asset():
    funding=[{'ccy':'USDT','availBal':'2','frozenBal':'1','bal':'3'}]
    details=[{'ccy':'USDT','availBal':'2','cashBal':'3','frozenBal':'1','liab':''}]
    c=AccountCollector(Client('OKX',{
        '/api/v5/asset/balances':funding,
        '/api/v5/account/balance':[{'details':details}],
    }))
    rows=asyncio.run(c.balances())
    uni=[r for r in rows if r.asset=='UNI']
    assert len(uni)==2 and all(r.total==0 for r in uni)


def test_bingx_history_does_not_require_total_field():
    pending=order(); terminal=order('FILLED','2')
    c=AccountCollector(Client('BingX',{
        '/openApi/spot/v1/trade/openOrders':{'orders':[pending]},
        '/openApi/spot/v1/trade/historyOrders':{'orders':[terminal]},
    }))
    rows=asyncio.run(c.orders())
    assert len(rows)==1 and rows[0].status=='filled'


def test_bingx_fee_symbol_echo_is_optional_but_mismatch_rejected():
    payloads=market_payloads()
    payloads['/openApi/spot/v1/user/commissionRate'].pop('symbol')
    fee=asyncio.run(BingXMarketCollector(Client('BingX',payloads)).fees())
    assert fee.taker_rate==D('.002')
    payloads['/openApi/spot/v1/user/commissionRate']['symbol']='OTHER-USDT'
    with pytest.raises(CollectorError):
        asyncio.run(BingXMarketCollector(Client('BingX',payloads)).fees())
