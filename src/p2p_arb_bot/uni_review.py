"""Checkpoint UNI: consulta y reporte privados. No hay comando de venta."""
import argparse
import asyncio
import os
from pathlib import Path

from .application.account_monitor import AccountMonitor
from .domain.failures import CollectorError, public_failure
from .infrastructure.scanner_output import PrivateAccountStatus
from .infrastructure.spot_client import SpotReadClient
from .infrastructure.spot_collectors import AccountCollector
from .infrastructure.spot_market import BingXMarketCollector


def validate_output(path: str) -> str:
    resolved=Path(path).resolve()
    forbidden={Path(os.getenv('ENV_PATH','.env')).resolve(), Path(os.getenv('STATUS_PATH','status.json')).resolve()}
    if resolved.name.startswith('.env') or resolved in forbidden:
        raise CollectorError('access_denied')
    return str(resolved)


def configured_clients():
    # Solo el proceso local consume credenciales. Nunca devolverlas ni serializarlas.
    clients={}
    from .domain.failures import failure_code
    from .infrastructure.spot_client import UnavailableReadClient
    for exchange,prefix in [('Binance','BINANCE'),('OKX','OKX'),('BingX','BINGX')]:
        try:
            clients[exchange]=SpotReadClient(exchange,
                os.getenv(prefix+'_API_KEY',''),os.getenv(prefix+'_API_SECRET',''),
                os.getenv(prefix+'_API_PASSPHRASE','') if exchange=='OKX' else '',
                base_url=os.getenv(prefix+'_API_BASE_URL') or None)
        except Exception as exc:
            clients[exchange]=UnavailableReadClient(exchange,failure_code(exc))
    return clients


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2,'Argumentos inválidos; consulte --help. No introduzca credenciales en argumentos.\n')


async def run(args):
    output=validate_output(args.output)
    clients=({e:SpotReadClient(e) for e in ('Binance','OKX','BingX')} if args.public_only else configured_clients())
    notifiers=[]
    if args.notify:
        from .infrastructure.telegram_notifier import TelegramNotifier
        token=os.getenv('TELEGRAM_BOT_TOKEN',''); chat=os.getenv('TELEGRAM_CHAT_ID','')
        if not token or not chat: raise CollectorError('not_configured')
        notifiers=[TelegramNotifier(token,chat)]
    if args.test_notification:
        if not notifiers: raise CollectorError('not_configured')
        from .application.events import MonitorEvent
        from datetime import datetime, timezone
        await notifiers[0].notify_event(MonitorEvent('notification_test','local-readonly','ok',
            'Prueba del monitor SPOT de solo lectura; no se ejecuta ninguna operación',datetime.now(timezone.utc)))
    monitor=AccountMonitor({e:AccountCollector(c) for e,c in clients.items()},
                           BingXMarketCollector(clients['BingX']),PrivateAccountStatus(output),notifiers)
    while True:
        summary=await monitor.snapshot()
        print(f"{summary['checkpoint']} · solo lectura · reporte privado: {output}")
        if not args.watch: return 0 if summary['checkpoint']=='READY_FOR_UNI_SELL_REVIEW' else 2
        await asyncio.sleep(monitor.refresh_s)


def main():
    parser=SafeParser(description='Observación UNI/USDT SPOT de solo lectura; no vende ni cambia permisos')
    modes=parser.add_mutually_exclusive_group()
    modes.add_argument('--public-only',action='store_true',help='Sin leer .env ni credenciales; balance/fee quedan desconocidos')
    modes.add_argument('--load-local-env',action='store_true',help='Solo en laptop: cargar credenciales locales sin mostrarlas')
    parser.add_argument('--output',default='uni_readonly_report.json')
    parser.add_argument('--watch',action='store_true')
    parser.add_argument('--notify',action='store_true',help='Enviar únicamente avisos por Telegram configurado localmente')
    parser.add_argument('--test-notification',action='store_true',help='Con --notify, enviar un aviso de prueba sin trading')
    args=parser.parse_args()
    try:
        validate_output(args.output)
        if args.load_local_env:
            from dotenv import load_dotenv
            load_dotenv(os.getenv('ENV_PATH') or '.env')
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        print('Checkpoint BLOCKED: '+public_failure(exc)['reason'])
        return 2


if __name__=='__main__':
    raise SystemExit(main())
