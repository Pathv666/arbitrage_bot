"""Registro local de bancos; no conecta cuentas ni valida identidad bancaria."""
import re
import unicodedata

BANK_ALIASES = {
    "VCB": ("vietcombank", "vcb", "jointstockcommercialbankforforeigntradeofvietnam"),
    "TCB": ("techcombank", "tcb"),
    "MB": ("mbbank", "mb", "militarybank"),
    "BIDV": ("bidv", "bankforinvestmentanddevelopmentofvietnam"),
    "ICB": ("vietinbank", "icb"),
    "ACB": ("acb", "asia commercial bank"),
    "VPB": ("vpbank", "vpb"),
    "TPB": ("tpbank", "tpb"),
    "STB": ("sacombank", "stb"),
    "AGR": ("agribank", "agr"),
    "VIB": ("vib",),
    "HDB": ("hdbank", "hdb"),
}


def _key(value: str) -> str:
    text = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", text.lower())


def normalize_payment(value: str) -> str:
    key = _key(value)
    for bank, aliases in BANK_ALIASES.items():
        if key in {_key(bank), *(_key(alias) for alias in aliases)}:
            return bank
    return key.upper()
