"""Web API. All portfolio reads, including dashboard data, go through MCP."""
import asyncio
from collections import deque
from contextlib import asynccontextmanager
import hmac
import logging
from pathlib import Path
import time
import uuid
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from . import __version__
from .agent import AgentError, ChatRequest, SCENARIOS, run_agent
from .config import ROOT, Settings
from .db import seed_database
from .mcp_client import MCPClient, MCPError
from .tool_registry import Snapshot, METHODOLOGY

logger = logging.getLogger("riskinsight")

class GlobalLimiter:
    """Single-process, global budget: cannot be bypassed by spoofing X-Forwarded-For."""
    def __init__(self, limit):
        self.limit = limit
        self.times = deque()
    def allow(self):
        now = time.monotonic()
        while self.times and self.times[0] < now - 60:
            self.times.popleft()
        if len(self.times) >= self.limit:
            return False
        self.times.append(now)
        return True


def create_app(settings=None):
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app):
        await asyncio.to_thread(seed_database, settings.db_path, settings.seed_loans)
        app.state.gate = asyncio.Semaphore(settings.max_concurrent)
        app.state.limiter = GlobalLimiter(settings.requests_per_minute)
        app.state.settings = settings
        yield

    app = FastAPI(title="RiskInsight API", version=__version__, lifespan=lifespan,
                  description="Educational synthetic portfolio analytics through MCP", docs_url="/api/docs", redoc_url=None)

    @app.middleware("http")
    async def headers(request, call_next):
        if request.headers.get("content-length", "").isdigit() and int(request.headers["content-length"]) > 20000:
            return JSONResponse({"detail": "Слишком большой запрос"}, status_code=413)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Cache-Control"] = "no-store" if request.url.path.startswith("/api") else "no-cache"
        # Presentation uses self-contained inline JS/CSS. App scripts never render LLM HTML.
        if not request.url.path.startswith("/api/docs"):
            response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        return response

    async def authorized(request: Request):
        if settings.access_token:
            supplied = request.headers.get("Authorization", "")
            expected = "Bearer " + settings.access_token
            if not hmac.compare_digest(supplied.encode(), expected.encode()):
                raise HTTPException(401, "Требуется APP_ACCESS_TOKEN (не ключ LLM)")

    @app.exception_handler(MCPError)
    async def mcp_error(_, exc):
        logger.error("MCP request failed: %s", type(exc).__name__)
        return JSONResponse({"detail": "MCP недоступен или вернул ошибку. Проверьте БД и серверный журнал."}, status_code=503)

    @app.get("/", include_in_schema=False)
    async def home():
        return FileResponse(ROOT / "static/index.html")

    @app.get("/presentation", include_in_schema=False)
    async def presentation():
        return FileResponse(ROOT / "docs/presentation.html")

    @app.get("/api/health")
    async def health():
        return {"status": "ok", "version": __version__, "database_exists": settings.db_path.exists(),
                "mode": settings.mode, "llm_configured": settings.llm_configured,
                "auth_required": bool(settings.access_token)}

    @app.get("/api/ready", dependencies=[Depends(authorized)])
    async def ready():
        async with app.state.gate, MCPClient(settings.db_path) as client:
            await client.request("ping")
        return {"status": "ready", "mcp": "ok"}

    @app.get("/api/context", dependencies=[Depends(authorized)])
    async def context():
        async with app.state.gate, MCPClient(settings.db_path) as client:
            data = await client.call_tool("get_dataset_context", {})
            tools = await client.list_tools()
        return {**data, "mode": settings.mode, "llm_configured": settings.llm_configured,
                "model": settings.llm_model if settings.mode == "llm" else None,
                "tools": [{"name": t["name"], "description": t["description"]} for t in tools],
                "scenarios": SCENARIOS}

    @app.get("/api/dashboard", dependencies=[Depends(authorized)])
    async def dashboard(as_of: str | None = None, product: str | None = None, region: str | None = None, risk_band: str | None = None):
        try:
            params = Snapshot(as_of=as_of, product=product, region=region, risk_band=risk_band).model_dump(exclude_none=True)
        except ValidationError:
            raise HTTPException(422, "Некорректные фильтры") from None
        async with app.state.gate, MCPClient(settings.db_path) as client:
            ctx = await client.call_tool("get_dataset_context", {})
            selected = as_of or ctx["latest_date"]
            if selected not in ctx["dates"]:
                raise HTTPException(422, "Нет выбранного среза")
            for k, v in {"product": product, "region": region, "risk_band": risk_band}.items():
                if v is not None and v not in ctx["filters"][k]:
                    raise HTTPException(422, f"Неизвестный фильтр {k}")
            summary = await client.call_tool("get_portfolio_summary", params)
            buckets = await client.call_tool("get_delinquency_distribution", params)
            segments = await client.call_tool("analyze_segment", {**params, "dimension": "product"})
            dates = [d for d in ctx["dates"] if d <= selected]
            period = {"start_date": dates[max(0, len(dates) - 6)], "end_date": selected}
            period.update({k: v for k, v in params.items() if k != "as_of"})
            trend = await client.call_tool("get_npl_trend", period)
        return {"summary": summary, "delinquency": buckets, "segments": segments, "trend": trend, "source": "MCP / stdio / SQLite"}

    @app.post("/api/chat", dependencies=[Depends(authorized)])
    async def chat(body: ChatRequest):
        if not app.state.limiter.allow():
            raise HTTPException(429, "Минутный лимит запросов исчерпан", headers={"Retry-After": "60"})
        if settings.mode == "llm" and not settings.llm_configured:
            raise HTTPException(503, "Настройте LLM_API_KEY и LLM_MODEL на сервере")
        rid = uuid.uuid4().hex[:12]
        try:
            async with asyncio.timeout(settings.llm_timeout * (settings.max_rounds + 1) + 45):
                async with app.state.gate:
                    result = await run_agent(body, settings)
        except AgentError as exc:
            raise HTTPException(502 if settings.mode == "llm" else 422, str(exc)) from None
        except TimeoutError:
            raise HTTPException(504, "Превышен общий тайм-аут анализа") from None
        # No prompts, keys, user text or raw data in the application log.
        logger.info("analysis id=%s mode=%s tools=%d ms=%d", rid, settings.mode, len(result["trace"]), result["duration_ms"])
        return {"request_id": rid, **result}

    @app.get("/api/methodology", response_class=PlainTextResponse, dependencies=[Depends(authorized)])
    async def methodology():
        async with app.state.gate, MCPClient(settings.db_path) as client:
            r = await client.request("resources/read", {"uri": "riskinsight://methodology"})
        return r["contents"][0]["text"]

    app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
    return app

app = create_app()
