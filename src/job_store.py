"""
job_store.py - Short-lived, thread-safe storage for polished tab summaries.

The triage response must never expose full article text. Instead, kept tabs are
stored server-side behind an unguessable job id, and `/polish-tabs` reads from
this bounded store. Jobs expire quickly, custom-URL jobs never enter the shared
Gemma cache, and all mutations are protected by locks because polish batches run
concurrently.
"""

from __future__ import annotations

import copy
import secrets
import threading
import time
from collections import OrderedDict
from typing import Any


class SummaryJobStore:
    def __init__(self, ttl_s: float = 900.0, max_jobs: int = 24) -> None:
        self.ttl_s = max(30.0, float(ttl_s))
        self.max_jobs = max(1, int(max_jobs))
        self._jobs: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._lock = threading.RLock()

    def _prune_locked(self, now: float) -> None:
        expired = [job_id for job_id, job in self._jobs.items() if job["expires_at"] <= now]
        for job_id in expired:
            self._jobs.pop(job_id, None)
        while len(self._jobs) > self.max_jobs:
            self._jobs.popitem(last=False)

    def create(
        self,
        tabs: list[dict[str, Any]],
        *,
        rated: bool,
        effective_intent: str,
        preset_key: str,
        shared_cache_allowed: bool,
        total_tabs: int,
        kept_count: int,
        closed_count: int,
        closed_read_min: float,
        total_words: int,
        closed_words: int,
    ) -> str:
        now = time.monotonic()
        job_id = secrets.token_urlsafe(24)
        tabs_by_id: dict[str, dict[str, Any]] = {}
        for tab in tabs:
            tab_id = str(tab.get("id") or "").strip()
            if tab_id:
                tabs_by_id[tab_id] = tab
        job = {
            "job_id": job_id,
            "created_at": now,
            "expires_at": now + self.ttl_s,
            "last_access": now,
            "tabs": tabs_by_id,
            "polished": {},
            "in_flight": set(),
            "rated": bool(rated),
            "effective_intent": (effective_intent or "").strip(),
            "preset_key": preset_key,
            "shared_cache_allowed": bool(shared_cache_allowed),
            "total_tabs": int(total_tabs),
            "kept_count": int(kept_count),
            "closed_count": int(closed_count),
            "closed_read_min": float(closed_read_min),
            "total_words": int(total_words),
            "closed_words": int(closed_words),
        }
        with self._lock:
            self._prune_locked(now)
            self._jobs[job_id] = job
            self._prune_locked(now)
        return job_id

    def lookup(self, job_id: str) -> tuple[dict[str, Any] | None, str]:
        """Return `(job, state)` where state is active, expired, or missing."""
        now = time.monotonic()
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None, "missing"
            if job["expires_at"] <= now:
                self._jobs.pop(job_id, None)
                return None, "expired"
            job["last_access"] = now
            self._jobs.move_to_end(job_id)
            self._prune_locked(now)
            return job, "active"

    def get(self, job_id: str) -> dict[str, Any] | None:
        job, _ = self.lookup(job_id)
        return job

    def get_tab(self, job_id: str, tab_id: str) -> dict[str, Any] | None:
        job = self.get(job_id)
        if job is None:
            return None
        return job["tabs"].get(str(tab_id))

    def get_polished(self, job_id: str, tab_id: str) -> dict[str, Any] | None:
        job = self.get(job_id)
        if job is None:
            return None
        cached = job["polished"].get(str(tab_id))
        return copy.deepcopy(cached) if cached is not None else None

    def begin_polish(self, job_id: str, tab_id: str) -> bool:
        now = time.monotonic()
        with self._lock:
            self._prune_locked(now)
            job = self._jobs.get(job_id)
            if job is None:
                return False
            tab_key = str(tab_id)
            if tab_key not in job["tabs"] or tab_key in job["in_flight"]:
                return False
            job["in_flight"].add(tab_key)
            return True

    def end_polish(self, job_id: str, tab_id: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                job["in_flight"].discard(str(tab_id))

    def save_polished(self, job_id: str, tab_id: str, result: dict[str, Any]) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return False
            job["polished"][str(tab_id)] = copy.deepcopy(result)
            return True

    def visible_result(self, job_id: str, tab_id: str) -> dict[str, Any] | None:
        job = self.get(job_id)
        if job is None:
            return None
        tab_key = str(tab_id)
        polished = job["polished"].get(tab_key)
        if polished is not None:
            return copy.deepcopy(polished)
        tab = job["tabs"].get(tab_key)
        return copy.deepcopy(tab) if tab is not None else None

    def updated_minutes_saved(self, job: dict[str, Any]) -> float:
        if int(job.get("closed_count") or 0) > 0:
            return round(float(job.get("closed_read_min") or 0.0), 1)
        total = 0.0
        for tab_id, tab in job["tabs"].items():
            result = job["polished"].get(tab_id) or tab
            full_read_min = float(result.get("full_read_min") or 0.0)
            summary_sec = float(result.get("summary_read_sec") or 0.0)
            total += max(0.2, full_read_min - (summary_sec / 60.0))
        return round(total, 1)

    def reset(self) -> None:
        with self._lock:
            self._jobs.clear()

    def __len__(self) -> int:
        with self._lock:
            self._prune_locked(time.monotonic())
            return len(self._jobs)


class SummaryCache:
    def __init__(self, ttl_s: float = 3600.0, max_entries: int = 512) -> None:
        self.ttl_s = max(60.0, float(ttl_s))
        self.max_entries = max(1, int(max_entries))
        self._entries: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()
        self._lock = threading.RLock()

    def _prune_locked(self, now: float) -> None:
        expired = [key for key, (expires_at, _) in self._entries.items() if expires_at <= now]
        for key in expired:
            self._entries.pop(key, None)
        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)

    def get(self, key: str) -> dict[str, Any] | None:
        now = time.monotonic()
        with self._lock:
            self._prune_locked(now)
            entry = self._entries.get(key)
            if entry is None:
                return None
            self._entries.move_to_end(key)
            return copy.deepcopy(entry[1])

    def set(self, key: str, value: dict[str, Any]) -> None:
        now = time.monotonic()
        with self._lock:
            self._prune_locked(now)
            self._entries[key] = (now + self.ttl_s, copy.deepcopy(value))
            self._entries.move_to_end(key)
            self._prune_locked(now)

    def reset(self) -> None:
        with self._lock:
            self._entries.clear()

    def __len__(self) -> int:
        with self._lock:
            self._prune_locked(time.monotonic())
            return len(self._entries)
