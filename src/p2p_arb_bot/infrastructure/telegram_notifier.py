"""Canal Telegram opcional; solo alertas, jamás comandos de trading."""
from curl_cffi.requests import AsyncSession


class TelegramNotifier:
    def __init__(self, token, chat_id):
        self.token, self.chat_id = token, chat_id

    async def notify_opportunity(self, opp):
        text = (f"P2P {opp.asset}/{opp.fiat} · solo lectura\n"
                f"Compra {opp.buy_pay_method} @ {opp.buy_price}\n"
                f"Venta {opp.sell_pay_method} @ {opp.sell_price}\n"
                f"Margen neto estimado {opp.net_pct:.3f}%\n"
                "Verifica legs, liquidez y costes en el dashboard.")
        try:
            async with AsyncSession() as session:
                response = await session.post(f"https://api.telegram.org/bot{self.token}/sendMessage",
                                              json={"chat_id": self.chat_id, "text": text}, timeout=8)
                if response.status_code != 200 or response.json().get("ok") is not True:
                    raise RuntimeError("Telegram rechazó el aviso")
        except Exception:
            # No propagar URL/token en errores de transporte ni logs.
            raise RuntimeError("No se pudo enviar aviso Telegram") from None

    async def notify_heartbeat(self, target, best_spread):
        pass
