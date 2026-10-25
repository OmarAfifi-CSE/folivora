"""
telemetry.py - Sentry Agent Tracing (OpenTelemetry GenAI Spans) & Privacy Scrubber

Wraps every Browserbase session, TabPFN quantile inference, Gemma generation,
and ElevenLabs Scribe/Flash call in standardized `gen_ai.request` spans.
Includes a unit-tested regex secret scrubber that redacts credentials (`api_key`,
`access_token`, `signingKey`) without hiding `gen_ai.usage.input_tokens` or
`gen_ai.usage.output_tokens`.
"""

import os
import re
import time
from contextlib import contextmanager
import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration

# Negative lookbehind/lookahead ensures `input_tokens` and `output_tokens` are NOT matched,
# while `access_token`, `bot_token`, `tabpfn_token`, `api_key`, and `signingKey` ARE redacted.
SECRET_KEY_PATTERN = re.compile(
    r"(api_key|apikey|secret|password|authorization|signingkey|(?<!put_)token(?!s))",
    re.IGNORECASE,
)


def scrub_dict_secrets(data):
    """
    Recursively scrubs sensitive keys from a dict (descending into nested dicts
    and lists of dicts) while preserving OpenTelemetry token usage counters
    (`input_tokens`, `output_tokens`, `tokens_per_second`).
    """
    if isinstance(data, list):
        return [scrub_dict_secrets(item) for item in data]
    if not isinstance(data, dict):
        return data
    cleaned = {}
    for key, val in data.items():
        key_str = str(key)
        if SECRET_KEY_PATTERN.search(key_str):
            cleaned[key] = "[Redacted]"
        elif isinstance(val, (dict, list)):
            cleaned[key] = scrub_dict_secrets(val)
        elif isinstance(val, str) and ("wss://connect." in val and "signingKey=" in val):
            cleaned[key] = val.split("signingKey=")[0] + "signingKey=[Redacted]"
        else:
            cleaned[key] = val
    return cleaned


def before_send_scrubber(event: dict, hint: dict | None = None) -> dict:
    """
    Sentry `before_send` hook: redacts secrets across `extra`, `contexts`,
    `breadcrumbs`, and `spans` (including lists of dicts) while keeping
    `gen_ai.usage.input_tokens` and `gen_ai.usage.output_tokens` visible.
    """
    if not isinstance(event, dict):
        return event
    if "extra" in event and isinstance(event["extra"], dict):
        event["extra"] = scrub_dict_secrets(event["extra"])
    if "contexts" in event and isinstance(event["contexts"], dict):
        event["contexts"] = scrub_dict_secrets(event["contexts"])
    if "breadcrumbs" in event and isinstance(event["breadcrumbs"], dict):
        crumbs = event["breadcrumbs"].get("values")
        if isinstance(crumbs, list):
            event["breadcrumbs"]["values"] = scrub_dict_secrets(crumbs)
    if "spans" in event and isinstance(event["spans"], list):
        for span in event["spans"]:
            if isinstance(span, dict) and isinstance(span.get("data"), dict):
                span["data"] = scrub_dict_secrets(span["data"])
    return event


def init_sentry(release: str = "folivora@1.0.0") -> bool:
    dsn = os.getenv("SENTRY_DSN", "").strip()
    if not dsn:
        return False
    sentry_sdk.init(
        dsn=dsn,
        integrations=[FastApiIntegration()],
        traces_sample_rate=1.0,
        profiles_sample_rate=1.0,
        environment=os.getenv("ENVIRONMENT", "production"),
        release=release,
        before_send=before_send_scrubber,
    )
    return True


@contextmanager
def trace_genai_span(
    name: str,
    model_name: str,
    op: str = "gen_ai.request",
    trace_collector: list | None = None,
):
    """
    Context manager that opens a Sentry span (`op="gen_ai.request"`), measures wall latency,
    computes token throughput & cost if token counts are set, and appends a clean summary
    to `trace_collector` for the live UI telemetry waterfall.
    """
    meta: dict = {
        "span_name": name,
        "op": op,
        "gen_ai.request.model": model_name,
        "gen_ai.usage.input_tokens": 0,
        "gen_ai.usage.output_tokens": 0,
        "gen_ai.cost.estimated_usd": 0.0,
    }
    t0 = time.perf_counter()
    with sentry_sdk.start_span(op=op, name=name) as span:
        try:
            yield meta
        finally:
            elapsed_s = max(0.0005, time.perf_counter() - t0)
            latency_ms = round(elapsed_s * 1000.0, 2)
            in_tok = int(meta.get("gen_ai.usage.input_tokens") or 0)
            out_tok = int(meta.get("gen_ai.usage.output_tokens") or 0)
            tok_per_s = round(out_tok / elapsed_s, 1) if out_tok > 0 else 0.0
            cost_usd = round((in_tok * 0.00000015) + (out_tok * 0.00000030), 7)

            meta["latency_ms"] = latency_ms
            meta["gen_ai.performance.tokens_per_second"] = tok_per_s
            meta["gen_ai.cost.estimated_usd"] = cost_usd

            scrubbed_meta = scrub_dict_secrets(meta)
            for k, v in scrubbed_meta.items():
                span.set_data(k, v)

            if trace_collector is not None:
                trace_collector.append(scrubbed_meta)
