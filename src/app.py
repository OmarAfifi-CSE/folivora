"""
app.py - Folivora Core FastAPI Gateway

"I had 42 browser tabs open for three weeks 'to read later.' So I built an agent
that opens them in Browserbase, predicts which 36 I'll never touch, and reads me
the 6 that matter in 20 seconds."

Integrates:
1. Browserbase Cloud Chromium CDP Inspection (`src/browserbase_service.py`)
2. Prior Labs TabPFN Calibrated Quantile Engine (`src/tabpfn_engine.py`)
3. Google Gemma 3/4 Digest Synthesizer + `check_output()` 1-Retry Fact Gate (`src/gemma_service.py`)
4. ElevenLabs Scribe v2 STT (with immediate privacy deletion) + Flash v2.5 TTS (`src/audio_service.py`)
5. Sentry OpenTelemetry GenAI Agent Tracing (`src/telemetry.py`)
"""

import io
import ipaddress
import os
import sys
import threading
import time
import zipfile
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

SRC_DIR = Path(__file__).resolve().parent
ROOT_DIR = SRC_DIR.parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

load_dotenv(ROOT_DIR / ".env")

from fastapi import FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from starlette.middleware.gzip import GZipMiddleware

from audio_service import AudioService
from browserbase_service import BrowserbaseService, PRESET_TAB_PILES
from gemma_service import GemmaDigestService
from job_store import SummaryJobStore
from summary_service import TabSummaryService
from tabpfn_engine import TabPFNQuantileEngine
from telemetry import init_sentry

sentry_enabled = init_sentry(release="folivora@1.0.0")
tabpfn_engine = TabPFNQuantileEngine(context_rows=200)
browserbase_service = BrowserbaseService()
audio_service = AudioService()
summary_service = TabSummaryService()
summary_job_store = SummaryJobStore(
    ttl_s=float(os.getenv("SUMMARY_JOB_TTL_S", "900")),
    max_jobs=int(os.getenv("SUMMARY_JOB_MAX", "24")),
)


@asynccontextmanager
async def _lifespan(_: FastAPI):
    def _bg_warmup():
        try:
            tabpfn_engine.warm_up()
        except Exception:
            pass

    try:
        threading.Thread(target=_bg_warmup, daemon=True).start()
    except Exception:
        pass
    yield


app = FastAPI(
    title="Folivora — The Open-Weight Tab Sieve & Page Devourer",
    description=(
        "Opens your hoarded browser tabs in Browserbase, predicts calibrated P10/P50/P90 "
        "utility quantiles with Prior Labs TabPFN, verifies a 2-sentence Gemma digest via "
        "check_output(), and speaks it aloud with ElevenLabs."
    ),
    version="1.0.0",
    lifespan=_lifespan,
)

app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=6)

_CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com; "
    "img-src 'self' data:; "
    "media-src 'self' blob:; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
)


_RATE_LIMITS = {
    "/triage": (30, 60.0),
    "/digest": (60, 60.0),
    "/polish-tabs": (20, 60.0),
    "/transcribe-voice": (10, 60.0),
    "/synthesize-audio": (30, 60.0),
}
_RATE_LIMIT_ENABLED = os.getenv("RATE_LIMIT_ENABLED", "1").lower() not in {"0", "false", "no"}


class SlidingWindowRateLimiter:
    def __init__(self, limits: dict[str, tuple[int, float]]) -> None:
        self._limits = limits
        self._buckets: dict[tuple[str, str], deque[float]] = {}
        self._lock = threading.Lock()
        self._last_sweep = time.monotonic()

    def allow(self, route: str, identity: str) -> tuple[bool, int]:
        limit, window = self._limits[route]
        now = time.monotonic()
        key = (route, identity)
        with self._lock:
            bucket = self._buckets.setdefault(key, deque())
            cutoff = now - window
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= limit:
                retry_after = max(1, int(bucket[0] + window - now) + 1)
                return False, retry_after
            bucket.append(now)
            if now - self._last_sweep >= 60:
                stale = [k for k, b in self._buckets.items() if not b or b[-1] <= cutoff]
                for k in stale:
                    if k != key:
                        del self._buckets[k]
                self._last_sweep = now
            return True, 0

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()
            self._last_sweep = time.monotonic()


rate_limiter = SlidingWindowRateLimiter(_RATE_LIMITS)


def _rate_limit_identity(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
    if forwarded:
        return forwarded[:128]
    return (request.client.host if request.client else "unknown")[:128]


@app.middleware("http")
async def enforce_rate_limit(request: Request, call_next):
    route = request.url.path.rstrip("/") or "/"
    if _RATE_LIMIT_ENABLED and route in _RATE_LIMITS:
        allowed, retry_after = rate_limiter.allow(route, _rate_limit_identity(request))
        if not allowed:
            return JSONResponse(
                status_code=429,
                content={"detail": "Too many requests. Please retry shortly."},
                headers={"Retry-After": str(retry_after), "Cache-Control": "no-store"},
            )
    return await call_next(request)


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
    response.headers.setdefault("Permissions-Policy", "microphone=(self), camera=(), geolocation=()")
    if request.url.path not in {"/docs", "/redoc", "/openapi.json"}:
        response.headers.setdefault("Content-Security-Policy", _CSP)
    if request.url.path.startswith(("/triage", "/digest", "/polish-tabs", "/transcribe-voice", "/synthesize-audio")):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


class TriageRequest(BaseModel):
    preset_key: str = Field(
        default="postgres_docker_pile",
        max_length=64,
        pattern=r"^[a-z0-9_]+$",
        description="Preset tab pile key",
    )
    custom_urls: list[str] = Field(
        default_factory=list,
        max_length=65,
        description="Optional live URLs to inspect in Browserbase",
    )
    user_intent: str = Field(default="", max_length=240, description="Optional spoken or typed filter rule")
    verify_live_browserbase: bool = Field(
        default=False,
        description="When True, spins up a live Browserbase cloud Chromium session over CDP",
    )
    include_gemma: bool = Field(
        default=False,
        description="When True, calls live Gemma inline; when False, returns instant template and upgrades via /digest",
    )

    @field_validator("custom_urls")
    @classmethod
    def validate_custom_urls(cls, values: list[str]) -> list[str]:
        clean: list[str] = []
        seen: set[str] = set()
        for raw in values:
            url = (raw or "").strip()
            if not url:
                continue
            if len(url) > 2048:
                raise ValueError("Each URL must be 2048 characters or fewer.")
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise ValueError(f"Unsupported URL: {url[:80]}")
            if parsed.username or parsed.password:
                raise ValueError("URLs with embedded credentials are not allowed.")
            host = parsed.hostname.lower().strip(".")
            if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
                raise ValueError(f"Local addresses are not allowed: {host}")
            try:
                ip = ipaddress.ip_address(host)
            except ValueError:
                ip = None
            if ip and (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved):
                raise ValueError(f"Private or local addresses are not allowed: {host}")
            if url not in seen:
                seen.add(url)
                clean.append(url)
        return clean


class DigestUpgradeRequest(BaseModel):
    total_tabs: int = Field(..., ge=1, le=2000)
    kept_count: int = Field(..., ge=0, le=2000)
    closed_count: int = Field(..., ge=0, le=2000)
    minutes_saved: float = Field(..., ge=0.0, le=1_000_000.0)
    top_tab_title: str = Field(default="pgvector Documentation", max_length=300)
    top_tab_p50: float = Field(default=84.0, ge=0.0, le=100.0)
    rated: bool = Field(default=True)


class PolishTabsRequest(BaseModel):
    job_id: str = Field(min_length=16, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    tab_ids: list[str] = Field(min_length=1, max_length=8)
    model_config = {"extra": "forbid"}

    @field_validator("tab_ids")
    @classmethod
    def validate_tab_ids(cls, values: list[str]) -> list[str]:
        clean: list[str] = []
        seen: set[str] = set()
        for raw in values:
            tab_id = (raw or "").strip()
            if not tab_id or len(tab_id) > 96:
                raise ValueError("Invalid tab id.")
            if tab_id not in seen:
                seen.add(tab_id)
                clean.append(tab_id)
        if not clean:
            raise ValueError("Select at least one tab to polish.")
        return clean


class AudioSynthesisRequest(BaseModel):
    text: str


@app.get("/health")
def health_check():
    reg = getattr(tabpfn_engine, "_regressor", None)
    if reg is None:
        ml_state = "warming"
    elif reg == "FALLBACK":
        ml_state = "histgb-quantile-fallback"
    else:
        ml_state = "tabpfn-ready"

    return {
        "status": "healthy",
        "service": "folivora-core",
        "version": "1.0.0",
        "ml_engine": ml_state,
        "engine_label": tabpfn_engine.engine_name,
        "integrations": {
            "browserbase": browserbase_service.is_configured,
            "tabpfn_token": bool(os.getenv("TABPFN_TOKEN")),
            "gemma_openrouter": bool(os.getenv("OPENROUTER_API_KEY")),
            "gemma_google": bool(os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")),
            "gemma_ollama": bool(os.getenv("OLLAMA_BASE_URL")),
            "gemma_tab_polish": summary_service.available,
            "elevenlabs": audio_service.is_configured,
            "sentry": sentry_enabled,
        },
    }


_ALL_TAB_FIELDS = ("id", "title", "url", "verdict", "word_count", "full_read_min", "p50_score")
_KEPT_TAB_FIELDS = (
    "id",
    "title",
    "url",
    "domain",
    "heading_count",
    "word_count",
    "full_read_min",
    "summary_read_sec",
    "p50_score",
    "overview",
    "takeaways",
    "topics",
    "wing",
    "author",
    "curator_take",
    "is_collection_roundup",
    "collection_preset_key",
    "collection_count",
    "is_broken_404",
    "match_explanation",
    "verdict_reason",
    "summary_source",
    "summary_validation",
    "polish_status",
)
_CLOSED_TAB_FIELDS = (
    "id",
    "title",
    "url",
    "domain",
    "word_count",
    "full_read_min",
    "summary_read_sec",
    "p50_score",
    "overview",
    "takeaways",
    "is_broken_404",
    "match_explanation",
    "verdict_reason",
)
_INTEREST_FIELDS = ("active", "headline", "why_relevant", "tailored_points", "mention_count")


def _client_tab(tab: dict, fields: tuple[str, ...]) -> dict:
    payload = {field: tab.get(field) for field in fields if field in tab}
    interest = tab.get("interest_section")
    if interest:
        payload["interest_section"] = {
            field: interest.get(field) for field in _INTEREST_FIELDS if field in interest
        }
    return payload


@app.get("/presets")
def list_presets():
    return {
        key: {
            "name": val["name"],
            "default_intent": val["default_intent"],
            "tab_count": len(val["tabs"]),
        }
        for key, val in PRESET_TAB_PILES.items()
    }


@app.get("/benchmarks")
def get_benchmarks():
    return TabPFNQuantileEngine.load_precomputed_benchmarks()


@app.post("/triage")
def triage_tabs(req: TriageRequest):
    """
    Fast-lane endpoint:
    1. Inspects the tab pile (or live URLs via Browserbase CDP when `verify_live_browserbase=True`).
    2. Computes calibrated P10, P50, and P90 utility quantiles via TabPFN.
    3. Separates `KEEP_EXTRACT` reference tabs from `SAFE_CLOSE` clutter and computes the Lazy Payoff.
    4. Returns the instant verified digest (upgraded asynchronously via `/digest` by the browser).
    """
    if not req.custom_urls and req.preset_key not in PRESET_TAB_PILES:
        raise HTTPException(status_code=400, detail=f"Unknown preset: {req.preset_key}")

    t0 = time.perf_counter()
    trace_collector: list[dict] = []

    try:
        bb_result = browserbase_service.inspect_tabs(
            preset_key=req.preset_key,
            custom_urls=req.custom_urls,
            user_intent=req.user_intent,
            verify_live_browserbase=req.verify_live_browserbase,
            trace_collector=trace_collector,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    is_rated = bool(bb_result.get("rated", False))

    try:
        ml_result = tabpfn_engine.evaluate_tab_pile(
            tabs=bb_result["tabs"],
            trace_collector=trace_collector,
            rated=is_rated,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    polish_targets: list[str] = []
    job_id: str | None = None
    polish_available = bool(summary_service.available and ml_result["kept_tabs"])
    if polish_available:
        for tab in ml_result["kept_tabs"]:
            tab["summary_source"] = "deterministic"
            tab["polish_status"] = "pending"
        polish_targets = [
            str(tab.get("id"))
            for tab in ml_result["kept_tabs"][: summary_service.max_tabs]
            if tab.get("id")
        ]
        job_id = summary_job_store.create(
            ml_result["kept_tabs"],
            rated=is_rated,
            effective_intent=bb_result.get("effective_intent", ""),
            preset_key=bb_result.get("preset_key", ""),
            shared_cache_allowed=bb_result.get("preset_key") != "custom_urls",
            total_tabs=ml_result["total_tabs"],
            kept_count=ml_result["kept_count"],
            closed_count=ml_result["closed_count"],
            closed_read_min=ml_result.get("closed_read_min", 0.0),
            total_words=ml_result["total_words"],
            closed_words=ml_result["closed_words"],
        )

    top_tab = ml_result["kept_tabs"][0] if ml_result["kept_tabs"] else ml_result["all_tabs"][0]
    gemma_service = GemmaDigestService()

    if req.include_gemma and is_rated:
        digest_info = gemma_service.synthesize_digest(
            total_tabs=ml_result["total_tabs"],
            kept_count=ml_result["kept_count"],
            closed_count=ml_result["closed_count"],
            minutes_saved=ml_result["minutes_saved"],
            top_tab_title=top_tab["title"],
            top_tab_p50=top_tab["p50_score"],
            trace_collector=trace_collector,
        )
        spoken_digest = digest_info["spoken_digest"]
        digest_source = digest_info["source"]
        digest_model = digest_info["model"]
    else:
        spoken_digest = gemma_service.deterministic_template(
            total_tabs=ml_result["total_tabs"],
            kept_count=ml_result["kept_count"],
            closed_count=ml_result["closed_count"],
            minutes_saved=ml_result["minutes_saved"],
            top_tab_title=top_tab["title"],
            rated=is_rated,
        )
        digest_source = "verified-template"
        digest_model = "deterministic-fast-lane"

    total_ms = round((time.perf_counter() - t0) * 1000.0, 2)

    return {
        "status": "success",
        "mode": ml_result["mode"],
        "rated": is_rated,
        "preset_key": bb_result["preset_key"],
        "pile_name": bb_result["pile_name"],
        "effective_intent": bb_result["effective_intent"],
        "browserbase": bb_result["browserbase"],
        "ml_engine": ml_result["engine"],
        "ml_latency_ms": ml_result["inference_latency_ms"],
        "total_pipeline_ms": total_ms,
        "total_tabs": ml_result["total_tabs"],
        "kept_count": ml_result["kept_count"],
        "closed_count": ml_result["closed_count"],
        "total_words": ml_result["total_words"],
        "closed_words": ml_result["closed_words"],
        "minutes_saved": ml_result["minutes_saved"],
        "top_tab_title": top_tab["title"],
        "top_tab_p50": top_tab["p50_score"],
        "spoken_digest": spoken_digest,
        "digest_source": digest_source,
        "digest_model": digest_model,
        "job_id": job_id,
        "polish_available": polish_available,
        "polish_targets": polish_targets,
        "polish_limit": summary_service.max_tabs,
        "kept_tabs": [_client_tab(tab, _KEPT_TAB_FIELDS) for tab in ml_result["kept_tabs"]],
        "closed_tabs": [_client_tab(tab, _CLOSED_TAB_FIELDS) for tab in ml_result["closed_tabs"]],
        "all_tabs": [_client_tab(tab, _ALL_TAB_FIELDS) for tab in ml_result["all_tabs"]],
        "sentry_spans": trace_collector,
    }


@app.post("/digest")
def upgrade_digest_with_gemma(req: DigestUpgradeRequest):
    """
    Slow-lane progressive upgrade: calls live open-weight Gemma 3/4 behind `check_output()`
    with 1 retry naming any rejected tokens.
    """
    trace_collector: list[dict] = []
    gemma_service = GemmaDigestService()
    res = gemma_service.synthesize_digest(
        total_tabs=req.total_tabs,
        kept_count=req.kept_count,
        closed_count=req.closed_count,
        minutes_saved=req.minutes_saved,
        top_tab_title=req.top_tab_title,
        top_tab_p50=req.top_tab_p50,
        trace_collector=trace_collector,
    )
    res["sentry_spans"] = trace_collector
    return res


@app.post("/polish-tabs")
def polish_tabs(req: PolishTabsRequest):
    """
    Progressive grounded rewrite for up to eight kept tabs. The deterministic
    cards remain visible while this runs; every rejected draft falls back to the
    deterministic summary without exposing article text to the browser.
    """
    job, job_state = summary_job_store.lookup(req.job_id)
    if job_state == "expired":
        raise HTTPException(
            status_code=410,
            detail="This polish job expired. Re-run triage to refresh the summaries.",
        )
    if job is None:
        raise HTTPException(status_code=404, detail="Polish job not found.")
    if not summary_service.available:
        raise HTTPException(status_code=503, detail="Gemma tab polishing is not configured.")

    unknown = [tab_id for tab_id in req.tab_ids if tab_id not in job["tabs"]]
    if unknown:
        raise HTTPException(status_code=422, detail="One or more tab ids are not part of this job.")

    results: dict[str, dict] = {}
    pending: list[str] = []
    targets: list[tuple[str, dict]] = []
    for tab_id in req.tab_ids:
        existing = summary_job_store.get_polished(req.job_id, tab_id)
        if existing is not None:
            results[tab_id] = existing
            continue
        if not summary_job_store.begin_polish(req.job_id, tab_id):
            visible = summary_job_store.visible_result(req.job_id, tab_id) or {}
            visible = _client_tab(visible, _KEPT_TAB_FIELDS)
            visible["polish_status"] = "pending"
            results[tab_id] = visible
            pending.append(tab_id)
            continue
        targets.append((tab_id, job["tabs"][tab_id]))

    trace_collector: list[dict] = []
    try:
        if targets:
            polished = summary_service.polish_many(
                targets,
                rated=bool(job["rated"]),
                intent=str(job["effective_intent"] or ""),
                shared_cache_allowed=bool(job["shared_cache_allowed"]),
                trace_collector=trace_collector,
            )
            for tab_id, result in polished.items():
                summary_job_store.save_polished(req.job_id, tab_id, result)
                results[tab_id] = result
    finally:
        for tab_id, _ in targets:
            summary_job_store.end_polish(req.job_id, tab_id)

    polished_count = sum(1 for result in results.values() if result.get("polish_status") == "polished")
    fallback_count = sum(1 for result in results.values() if result.get("polish_status") == "fallback")
    return {
        "job_id": req.job_id,
        "results": {tab_id: _client_tab(result, _KEPT_TAB_FIELDS) for tab_id, result in results.items()},
        "polished_count": polished_count,
        "fallback_count": fallback_count,
        "pending_count": len(pending),
        "minutes_saved": summary_job_store.updated_minutes_saved(job),
        "kept_count": job["kept_count"],
        "closed_count": job["closed_count"],
        "total_words": job["total_words"],
        "closed_words": job["closed_words"],
        "sentry_spans": trace_collector,
    }


@app.post("/transcribe-voice")
async def transcribe_voice_note(file: UploadFile = File(...)):
    """
    Accepts a spoken voice note from the lazy user, transcribes it via ElevenLabs Scribe (`scribe_v1`),
    and immediately deletes the remote transcript on ElevenLabs for privacy.
    """
    max_upload_bytes = 10 * 1024 * 1024
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_upload_bytes:
            raise HTTPException(status_code=400, detail="Audio file exceeds 10 MB limit.")
        chunks.append(chunk)
    audio_bytes = b"".join(chunks)
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Empty audio upload.")

    trace_collector: list[dict] = []
    try:
        res = audio_service.transcribe_and_forget(
            audio_bytes=audio_bytes,
            filename=file.filename or "voice_note.webm",
            content_type=file.content_type or "audio/webm",
            trace_collector=trace_collector,
        )
        res["sentry_spans"] = trace_collector
        return res
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.post("/synthesize-audio")
def synthesize_audio(req: AudioSynthesisRequest):
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Missing text for audio synthesis.")
    if len(text) > 1000:
        raise HTTPException(status_code=400, detail="Text exceeds 1000-character synthesis limit.")
    try:
        audio_bytes = audio_service.synthesize_speech(text)
        return Response(
            content=audio_bytes,
            media_type="audio/mpeg",
            headers={
                "X-Voice-Id": audio_service.voice_id,
                "X-Voice-Name": audio_service.voice_name,
            },
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


STATIC_DIR = SRC_DIR / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/download-extension.zip")
def download_chrome_extension(request: Request):
    """Packages `chrome_extension/` as a ready-to-load ZIP archive with the current server URL pre-filled."""
    ext_dir = ROOT_DIR / "chrome_extension"
    if not ext_dir.exists():
        raise HTTPException(status_code=404, detail="Chrome extension directory not found")
    origin = str(request.base_url).rstrip("/")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in sorted(ext_dir.rglob("*")):
            if not file_path.is_file():
                continue
            rel_name = file_path.relative_to(ext_dir).as_posix()
            if file_path.name == "popup.html" and origin:
                content = file_path.read_text(encoding="utf-8").replace(
                    'value="http://localhost:8000"',
                    f'value="{origin}"',
                )
                zf.writestr(rel_name, content.encode("utf-8"))
            else:
                zf.write(str(file_path), arcname=rel_name)
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": 'attachment; filename="folivora-chrome-extension.zip"',
            "Cache-Control": "no-store",
        },
    )


@app.get("/favicon.ico")
def serve_favicon():
    fav = STATIC_DIR / "favicon.svg"
    if fav.exists():
        return FileResponse(str(fav), media_type="image/svg+xml")
    raise HTTPException(status_code=404, detail="Favicon not found")


@app.get("/")
def serve_index():
    idx = STATIC_DIR / "index.html"
    if idx.exists():
        return FileResponse(str(idx), headers={"Cache-Control": "no-store"})
    return {"status": "ok", "service": "Folivora API active"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
