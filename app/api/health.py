import httpx
from fastapi import APIRouter, Request, Response

from app.core.config import get_settings

router = APIRouter(tags=["health"])


async def _check_database(request: Request) -> str:
    """SELECT 1 via the async engine; 'skipped' when running in-memory."""
    engine = getattr(request.app.state, "db_engine", None)
    if engine is None:
        return "skipped"
    try:
        from sqlalchemy import text

        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return "ok"
    except Exception:
        return "error"


async def _check_llm_egress(request: Request) -> str:
    """Cheap outbound-connectivity check with a short timeout (~2s).

    Only runs when a real provider key is configured (EchoLLMClient in
    dev/test => 'skipped', so tests never touch the network).
    """
    settings = get_settings()
    has_key = (settings.openrouter_api_key and settings.openrouter_api_key != "changeme") or (
        settings.gemini_api_key and settings.gemini_api_key != "changeme"
    )
    http_client = getattr(request.app.state, "http_client", None)
    if not has_key or http_client is None:
        return "skipped"
    try:
        # Unauthenticated catalog endpoint: any HTTP response proves egress.
        resp = await http_client.get(
            f"{settings.openrouter_base_url.rstrip('/')}/models",
            timeout=settings.ready_check_timeout_seconds,
        )
        return "ok" if resp.status_code < 500 else "error"
    except httpx.HTTPError:
        return "error"


@router.get("/health", summary="Liveness probe")
async def health() -> dict:
    return {"status": "ok"}


@router.get("/ready", summary="Readiness probe (DB + conectividade de saida)")
async def ready(request: Request, response: Response) -> dict:
    """Checks downstream dependencies."""
    checks = {
        "database": await _check_database(request),
        "llm_egress": await _check_llm_egress(request),
    }
    healthy = all(v in ("ok", "skipped") for v in checks.values())
    if not healthy:
        response.status_code = 503
    return {"status": "ready" if healthy else "degraded", "checks": checks}
