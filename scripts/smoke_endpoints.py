"""Smoke read-only explícito; no se ejecuta desde pytest ni crea órdenes."""
import asyncio
from p2p_arb_bot.infrastructure.exchanges import BinanceSource, OKXSource, BybitSource, BingXSource


async def main():
    async def check(factory):
        source = factory()
        try:
            for side in ("BUY", "SELL"):
                ads = await source.fetch_ads(asset="USDT", fiat="VND", trade_type=side, pay_types=[], rows=10)
                print(f"{source.exchange} {side}: OK, {len(ads)} ads")
                if ads:
                    ad = ads[0]
                    print(f"  normalized: side={ad.trade_type}, price={ad.price}, methods={ad.pay_methods}, payment_minutes={ad.payment_time_minutes}")
        except Exception as exc:
            # Los clientes no contienen credenciales en las URL de mercado.
            print(f"{source.exchange}: {type(exc).__name__}: {exc}")
        finally:
            await source.aclose()
    await asyncio.gather(*(check(f) for f in (BinanceSource, OKXSource, BybitSource, BingXSource)))


if __name__ == "__main__":
    asyncio.run(main())
