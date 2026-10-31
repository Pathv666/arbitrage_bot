"""Canal Telegram opcional; solo alertas, jamás comandos de trading."""
from datetime import datetime

from curl_cffi.requests import AsyncSession

from ..domain.execution import Route
from .route_alert import route_alert_lines


def message_chunks(text: str, limit: int = 3500) -> list[str]:
    """Divide sin perder legs, contando UTF-16 como Telegram y reservando cabecera."""
    chunks = []
    current = ""
    units = 0
    for line in text.splitlines(keepends=True):
        line_units = len(line.encode("utf-16-le")) // 2
        if current and units + line_units > limit:
            chunks.append(current)
            current, units = "", 0
        for character in line:
            size = 2 if ord(character) > 0xFFFF else 1
            if units + size > limit:
                chunks.append(current)
                current, units = "", 0
            current += character
            units += size
    if current:
        chunks.append(current)
    return chunks


class TelegramNotifier:
    def __init__(self, token, chat_id):
        self.token, self.chat_id = token, chat_id

    async def notify_route(self, route: Route, detected_at: datetime) -> None:
        chunks = message_chunks("\n".join(route_alert_lines(route, detected_at)))
        for index, chunk in enumerate(chunks, 1):
            await self._send(f"Ruta V1 · parte {index}/{len(chunks)}\n{chunk}")

    async def notify_opportunity(self, opp):
        text = (f"P2P {opp.asset}/{opp.fiat} · solo lectura\n"
                f"Compra {opp.buy_pay_method} @ {opp.buy_price}\n"
                f"Venta {opp.sell_pay_method} @ {opp.sell_price}\n"
                f"Margen neto estimado {opp.net_pct:.3f}%\n"
                "Verifica legs, liquidez y costes en el dashboard.")
        await self._send(text)

    async def _send(self, text: str) -> None:
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
