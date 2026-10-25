"""Configuration. Secrets are read on the server, never sent to the browser."""
from dataclasses import dataclass
from pathlib import Path
import os
from urllib.parse import urlparse
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env", override=False)


@dataclass(frozen=True)
class Settings:
    db_path: Path
    mode: str
    llm_base_url: str
    llm_api_key: str
    llm_model: str
    access_token: str
    llm_timeout: float
    max_rounds: int
    max_tool_calls: int
    max_output_tokens: int
    requests_per_minute: int
    max_concurrent: int
    seed_loans: int

    @classmethod
    def from_env(cls):
        mode = os.getenv("APP_MODE", "demo").lower()
        if mode not in {"demo", "llm"}:
            raise ValueError("APP_MODE должен быть demo или llm")
        base = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        u = urlparse(base)
        if u.scheme not in {"https", "http"} or not u.hostname or u.username or u.password:
            raise ValueError("Некорректный LLM_BASE_URL")
        # HTTP only for a local model server; no user-controlled URLs are accepted.
        if u.scheme == "http" and u.hostname not in {"localhost", "127.0.0.1", "host.docker.internal", "ollama"}:
            raise ValueError("Для внешнего LLM endpoint требуется HTTPS")
        return cls(
            db_path=Path(os.getenv("DATABASE_PATH", str(ROOT / "data/portfolio.sqlite"))).resolve(),
            mode=mode,
            llm_base_url=base,
            llm_api_key=os.getenv("LLM_API_KEY", os.getenv("OPENAI_API_KEY", "")),
            llm_model=os.getenv("LLM_MODEL", ""),
            access_token=os.getenv("APP_ACCESS_TOKEN", ""),
            llm_timeout=float(os.getenv("LLM_TIMEOUT_SECONDS", "60")),
            max_rounds=max(1, min(8, int(os.getenv("MAX_AGENT_ROUNDS", "5")))),
            max_tool_calls=max(1, min(16, int(os.getenv("MAX_TOOL_CALLS", "10")))),
            max_output_tokens=max(256, min(16000, int(os.getenv("MAX_OUTPUT_TOKENS", "4096")))),
            requests_per_minute=max(1, int(os.getenv("REQUESTS_PER_MINUTE", "12"))),
            max_concurrent=max(1, min(8, int(os.getenv("MAX_CONCURRENT_REQUESTS", "2")))),
            seed_loans=max(100, min(50000, int(os.getenv("SEED_LOANS", "10000")))),
        )

    @property
    def llm_configured(self) -> bool:
        return bool(self.llm_api_key and self.llm_model)
