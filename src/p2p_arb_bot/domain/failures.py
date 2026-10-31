"""Errores públicos cerrados: jamás interpolar respuesta, URL firmada o excepción."""
import asyncio
import json
from functools import wraps

MESSAGES = {
    "timeout": "Timeout; compruebe conectividad y reintente tras cooldown",
    "dns": "DNS no disponible para el endpoint oficial",
    "connectivity": "No se pudo conectar al endpoint oficial",
    "tls": "Fallo TLS/certificado; no desactive verificación",
    "access_denied": "Acceso denegado; compruebe IP, región y permisos Read",
    "authentication": "Autenticación rechazada; verifique credenciales Read localmente",
    "clock": "Timestamp rechazado; sincronice el reloj del equipo",
    "rate_limit": "Límite de peticiones; espere cooldown",
    "http_error": "HTTP no exitoso en endpoint oficial",
    "api_error": "API rechazó la consulta read-only",
    "schema": "Respuesta incompleta o esquema inválido; datos descartados",
    "unavailable": "Collector no disponible; datos descartados",
    "not_configured": "Credenciales Read no configuradas; snapshot no disponible",
}


class CollectorError(RuntimeError):
    def __init__(self, code: str):
        self.code = code if code in MESSAGES else "unavailable"
        super().__init__(MESSAGES[self.code])


def failure_code(exc: Exception) -> str:
    if isinstance(exc, CollectorError):
        return exc.code
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return "timeout"
    if isinstance(exc, (ValueError, TypeError, KeyError, ArithmeticError, AttributeError, json.JSONDecodeError)):
        return "schema"
    code = getattr(exc, "code", None)
    if not isinstance(code, int):
        code = None
    return {5: "dns", 6: "dns", 7: "connectivity", 28: "timeout", 35: "tls", 60: "tls"}.get(code, "unavailable")


def public_failure(exc: Exception) -> dict:
    code = failure_code(exc)
    return {"code": code, "reason": MESSAGES[code]}


def safe_collector(function):
    """Eleva fuera de except: tampoco conserva contexto con secretos del transporte."""
    @wraps(function)
    async def safe(*args, **kwargs):
        code = None
        try:
            return await function(*args, **kwargs)
        except Exception as exc:
            code = failure_code(exc)
        raise CollectorError(code)
    return safe


def check_http(status: int) -> None:
    if status == 200:
        return
    raise CollectorError({401: "authentication", 403: "access_denied", 429: "rate_limit"}.get(status, "http_error"))


def check_api(code, *, success="0") -> None:
    if str(code) == success:
        return
    safe = str(code) if isinstance(code, (str, int)) else ""
    category = "clock" if safe in {"-1021", "50102", "10002"} else (
        "authentication" if safe in {"-2014", "-2015", "50111", "50113", "10003", "10004", "100001", "100004", "100413", "100419"}
        else "rate_limit" if safe in {"-1003", "50011", "10006", "100410"} else "api_error")
    raise CollectorError(category)
