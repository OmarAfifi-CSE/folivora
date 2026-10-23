/**
 * app.js - Folivora v7 Universal Tab Summarizer, Topic-Focused Card Tabs & DEV Hall of Fame Explorer
 *
 * Strict Two-Mode State Machine:
 * • Mode A (`data.rated === false` — No interest specified):
 *   Renders clean Executive Summaries (`Complete Tab Summary`) for ALL open tabs with ZERO percentage badges
 *   and hides the "Unrelated / Safe to Close" column (unless a broken 404 link exists).
 * • Mode B (`data.rated === true` — User specified an interest in any life or tech topic):
 *   Displays exact 0–100% relevance ratings, match explanations, and interactive Segmented Card Tabs:
 *   - Tab 1 (Active by default): `🎯 Focused on "<Topic>"` (Synthesized topic overview + section-anchored takeaways)
 *   - Tab 2: `📖 Complete Tab Summary` (General article overview + 4 section-diverse takeaways)
 *   Never stacks both sections simultaneously!
 */

const state = {
  presetKey: null,
  lastResult: null,
  isPlayingAudio: false,
  audioElement: null,
  mediaRecorder: null,
  audioChunks: [],
  isRecording: false,
  triageController: null,
  triageRequestId: 0,
  digestController: null,
  summaryPolishControllers: new Set(),
  toastTimer: null,
};

const SAMPLE_LIVE_URLS = [
  "https://dev.to/omarafifi/reviving-daphq-how-i-built-an-open-source-bare-metal-file-transfer-engine-solo-with-github-copilot-5cd8",
  "https://dev.to/omarafifi/my-phone-broke-and-gemini-fixed-it-329l",
  "https://dev.to/sylwia-lask/how-to-become-a-tech-conference-speaker-and-why-you-might-regret-it-2ink",
  "https://dev.to/gramli/fine-tuning-qwen3-8b-with-tinker-from-50-to-76-accuracy-and-burning-through-my-budget-48mh",
];

function $(id) {
  return document.getElementById(id);
}

function showToast(message, type = "info", duration = 4000) {
  const toast = $("app-toast");
  if (!toast) return;
  if (state.toastTimer) {
    clearTimeout(state.toastTimer);
    state.toastTimer = null;
  }
  toast.textContent = String(message || "");
  toast.className = `app-toast visible ${type}`;
  if (duration > 0) {
    state.toastTimer = setTimeout(() => {
      toast.classList.remove("visible");
      state.toastTimer = null;
    }, duration);
  }
}

function hideToast() {
  const toast = $("app-toast");
  if (state.toastTimer) {
    clearTimeout(state.toastTimer);
    state.toastTimer = null;
  }
  toast?.classList.remove("visible");
}

function setAppBusy(isBusy) {
  $("intake-screen")?.setAttribute("aria-busy", isBusy ? "true" : "false");
  $("results-workspace")?.setAttribute("aria-busy", isBusy ? "true" : "false");
  document.body.classList.toggle("app-busy", isBusy);
  ["btn-run-triage", "btn-apply-live-filter", "btn-clear-live-filter"].forEach((id) => {
    const button = $(id);
    if (button) button.disabled = isBusy;
  });
}

function prefersReducedMotion() {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function scrollToTop() {
  window.scrollTo({ top: 0, behavior: prefersReducedMotion() ? "auto" : "smooth" });
}

function escapeHtml(str) {
  return String(str || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function formatTakeawayHtml(rawItem) {
  const str = String(rawItem || "").trim();
  const dashIdx = str.indexOf(" — ");
  if (dashIdx > 2 && dashIdx <= 78) {
    const head = str.slice(0, dashIdx).trim();
    const rest = str.slice(dashIdx + 3).trim();
    return `<li><strong class="takeaway-anchor">${escapeHtml(head)} —</strong> ${escapeHtml(rest)}</li>`;
  }
  return `<li>${escapeHtml(str)}</li>`;
}

function roundTo(num, decimals = 1) {
  const f = Math.pow(10, decimals);
  return Math.round((Number(num) || 0) * f) / f;
}

function extractUrlsFromText(rawText) {
  if (!rawText) return [];
  const matches = String(rawText).match(/https?:\/\/(?:(?!https?:\/\/)[^\s"'<>)\],])+/gi) || [];
  return [...new Set(matches.map((u) => u.replace(/[.,;]+$/, "")))].slice(0, 65);
}

function updateDetectedUrlCounter() {
  const box = $("input-custom-urls");
  const pill = $("detected-url-count");
  const runLabel = $("run-btn-label");
  const intentVal = ($("input-intent")?.value || "").trim();
  if (!box || !pill) return;
  const urls = extractUrlsFromText(box.value);
  const rawCount = (String(box.value).match(/https?:\/\//gi) || []).length;
  pill.textContent =
    rawCount > 65
      ? `65 of ${rawCount} links (max 65)`
      : `${urls.length} link${urls.length === 1 ? "" : "s"}`;
  if (runLabel) {
    if (urls.length > 0) {
      runLabel.textContent = intentVal
        ? `Summarize & Score ${urls.length} Link${urls.length === 1 ? "" : "s"} for "${intentVal}"`
        : `Summarize My ${urls.length} Open Link${urls.length === 1 ? "" : "s"}`;
    } else {
      runLabel.textContent = intentVal
        ? `Summarize & Filter Tabs for "${intentVal}"`
        : "Summarize My Open Tabs";
    }
  }
}

function switchIntakeMode(mode, focusTab = false) {
  document.querySelectorAll(".mode-tab").forEach((btn) => {
    const active = btn.getAttribute("data-mode") === mode;
    btn.classList.toggle("active", active);
    btn.setAttribute("aria-selected", active ? "true" : "false");
    btn.setAttribute("tabindex", active ? "0" : "-1");
    if (active && focusTab) btn.focus();
  });
  ["paste", "extension", "demo"].forEach((m) => {
    const panel = $(`mode-panel-${m}`);
    if (panel) {
      panel.classList.toggle("hidden", m !== mode);
      panel.hidden = m !== mode;
    }
  });
}

function showIntakeScreen() {
  stopAllAudio();
  state.triageController?.abort();
  state.digestController?.abort();
  state.summaryPolishControllers.forEach((controller) => controller.abort());
  state.summaryPolishControllers.clear();
  state.triageRequestId += 1;
  state.presetKey = null;
  setSummaryPolishStatus("");
  setAppBusy(false);
  hideToast();
  $("intake-screen")?.classList.remove("hidden");
  $("results-workspace")?.classList.add("hidden");
  $("btn-reset-view")?.classList.add("hidden");
  switchIntakeMode("paste");
  scrollToTop();
}

function showResultsWorkspace() {
  $("intake-screen")?.classList.add("hidden");
  $("results-workspace")?.classList.remove("hidden");
  $("btn-reset-view")?.classList.remove("hidden");
  scrollToTop();
}

function switchCardTab(cardEl, targetTab) {
  if (!cardEl) return;
  cardEl.querySelectorAll(".card-tab-btn").forEach((btn) => {
    const isAct = btn.getAttribute("data-card-tab") === targetTab;
    btn.classList.toggle("active", isAct);
    btn.setAttribute("aria-selected", isAct ? "true" : "false");
    btn.setAttribute("tabindex", isAct ? "0" : "-1");
  });
  cardEl.querySelectorAll(".card-tab-panel").forEach((panel) => {
    const isMatch = panel.getAttribute("data-card-panel") === targetTab;
    panel.classList.toggle("hidden", !isMatch);
    panel.hidden = !isMatch;
  });
}

function switchAllCardsTab(targetTab) {
  document.querySelectorAll(".global-switch-btn").forEach((btn) => {
    const active = btn.getAttribute("data-global-tab") === targetTab;
    btn.classList.toggle("active", active);
    btn.setAttribute("aria-pressed", active ? "true" : "false");
  });
  document.querySelectorAll("#kept-tabs-grid .kept-card").forEach((cardEl) => {
    if (cardEl.querySelector(".card-tabs-bar")) {
      switchCardTab(cardEl, targetTab);
    }
  });
}

function bindRovingTablist(container, tabSelector) {
  if (!container) return;
  container.addEventListener("keydown", (event) => {
    const tabs = [...container.querySelectorAll(tabSelector)].filter(
      (tab) => tab.offsetParent !== null
    );
    if (tabs.length === 0) return;
    const current = tabs.indexOf(document.activeElement);
    if (current < 0) return;
    let next = current;
    if (event.key === "ArrowRight" || event.key === "ArrowDown") {
      next = (current + 1) % tabs.length;
    } else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
      next = (current - 1 + tabs.length) % tabs.length;
    } else if (event.key === "Home") {
      next = 0;
    } else if (event.key === "End") {
      next = tabs.length - 1;
    } else {
      return;
    }
    event.preventDefault();
    tabs[next].focus();
    tabs[next].click();
  });
}

async function runTriage({ forcePreset = null, overrideIntent = null } = {}) {
  stopAllAudio();

  const urlsBox = $("input-custom-urls");
  const intentInput = $("input-intent");
  const liveIntentInput = $("live-intent-input");
  const liveBbCheck = $("chk-live-browserbase");
  const runBtn = $("btn-run-triage");
  const runLabel = $("run-btn-label");

  if (overrideIntent !== null) {
    if (intentInput) intentInput.value = overrideIntent;
    if (liveIntentInput) liveIntentInput.value = overrideIntent;
  }

  const resultsVisible = !$("results-workspace")?.classList.contains("hidden");
  const activeIntent =
    overrideIntent !== null
      ? overrideIntent.trim()
      : resultsVisible && liveIntentInput
      ? liveIntentInput.value.trim()
      : intentInput
      ? intentInput.value.trim()
      : "";

  if (intentInput) intentInput.value = activeIntent;
  if (liveIntentInput) liveIntentInput.value = activeIntent;

  if (forcePreset) {
    state.presetKey = forcePreset;
    if (urlsBox) urlsBox.value = "";
  }

  const extractedUrls = forcePreset ? [] : extractUrlsFromText(urlsBox ? urlsBox.value : "");
  const chosenPreset = forcePreset || state.presetKey;
  const rawUrlCount = (String(urlsBox ? urlsBox.value : "").match(/https?:\/\//gi) || []).length;

  if (activeIntent.length > 240) {
    showToast("Keep the topic under 240 characters.", "error");
    (resultsVisible ? liveIntentInput : intentInput)?.focus();
    return;
  }

  if (extractedUrls.length === 0 && !chosenPreset) {
    showToast("Paste at least one link, or choose a sample window or the DEV Hall of Fame.", "error", 5200);
    if (!resultsVisible) {
      switchIntakeMode("paste", true);
      urlsBox?.focus();
    }
    return;
  }

  if (rawUrlCount > 65) {
    showToast("Only the first 65 links will be analyzed.", "info", 4200);
  }

  state.triageController?.abort();
  state.digestController?.abort();
  state.summaryPolishControllers.forEach((controller) => controller.abort());
  state.summaryPolishControllers.clear();
  const controller = new AbortController();
  const requestId = ++state.triageRequestId;
  state.triageController = controller;

  if (runBtn) runBtn.disabled = true;
  if (runLabel) {
    runLabel.textContent =
      extractedUrls.length > 0
        ? `Reading & summarizing ${extractedUrls.length} live links…`
        : "Reading & summarizing tabs…";
  }

  const payload = {
    preset_key: chosenPreset || "postgres_docker_pile",
    custom_urls: extractedUrls,
    user_intent: activeIntent,
    verify_live_browserbase: Boolean(liveBbCheck && liveBbCheck.checked),
    include_gemma: false,
  };

  setAppBusy(true);
  showToast(
    resultsVisible
      ? activeIntent
        ? `Re-scoring tabs for “${activeIntent}”…`
        : "Rebuilding the unrated summary…"
      : "Reading and summarizing your tabs…",
    "info",
    0
  );

  try {
    const resp = await fetch("/triage", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal: controller.signal,
    });
    if (!resp.ok) {
      let detail = `HTTP ${resp.status}`;
      try {
        const errorPayload = await resp.json();
        if (errorPayload && errorPayload.detail) detail = String(errorPayload.detail);
      } catch (error) {
        detail = `HTTP ${resp.status}`;
      }
      throw new Error(detail);
    }
    const data = await resp.json();
    if (requestId !== state.triageRequestId) return;

    state.lastResult = data;
    renderWorkspace(data);
    showResultsWorkspace();
    setAppBusy(false);
    hideToast();

    upgradeDigestWithGemma(data, requestId);
    if (data.polish_available && Array.isArray(data.polish_targets) && data.polish_targets.length > 0) {
      upgradeTabSummaries(data, requestId, data.polish_targets);
    }
  } catch (err) {
    if (err.name === "AbortError") return;
    console.error("Triage failed:", err);
    if (requestId === state.triageRequestId) {
      setAppBusy(false);
      showToast(`Could not analyze tabs: ${err.message}`, "error", 6000);
    }
  } finally {
    if (requestId === state.triageRequestId) {
      state.triageController = null;
      if (runBtn) runBtn.disabled = false;
      updateDetectedUrlCounter();
    }
  }
}

async function upgradeDigestWithGemma(data, requestId) {
  const badge = $("gemma-source-badge");
  if (badge) badge.textContent = "Verifying summary…";

  state.digestController?.abort();
  const controller = new AbortController();
  state.digestController = controller;

  try {
    const resp = await fetch("/digest", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        total_tabs: data.total_tabs,
        kept_count: data.kept_count,
        closed_count: data.closed_count,
        minutes_saved: data.minutes_saved,
        top_tab_title: data.top_tab_title,
        top_tab_p50: data.top_tab_p50,
        rated: Boolean(data.rated),
      }),
      signal: controller.signal,
    });
    if (!resp.ok) return;
    const gemmaData = await resp.json();
    if (requestId !== state.triageRequestId) return;
    const verifiedByGemma =
      gemmaData &&
      (gemmaData.source === "gemma" || gemmaData.source === "gemma-retry");
    if (verifiedByGemma && gemmaData.spoken_digest) {
      state.lastResult.spoken_digest = gemmaData.spoken_digest;
      state.lastResult.digest_source = gemmaData.source;
      state.lastResult.digest_model = gemmaData.model;
      const dirEl = $("directive-text");
      if (dirEl) dirEl.textContent = gemmaData.spoken_digest;
      if (badge) badge.textContent = "Gemma Verified";
    } else if (badge) {
      badge.textContent = "Deterministic";
    }

    if (
      gemmaData &&
      Array.isArray(gemmaData.sentry_spans) &&
      gemmaData.sentry_spans.length > 0
    ) {
      state.lastResult.sentry_spans = [
        ...(state.lastResult.sentry_spans || []),
        ...gemmaData.sentry_spans,
      ];
      renderSentrySpans(state.lastResult.sentry_spans);
    }
  } catch (e) {
    if (e.name !== "AbortError") console.warn("Gemma upgrade skipped:", e);
    if (requestId === state.triageRequestId && badge) {
      badge.textContent = "Deterministic";
    }
  } finally {
    if (requestId === state.triageRequestId) state.digestController = null;
  }
}

function setSummaryPolishStatus(text) {
  const statusEl = $("summary-polish-status");
  if (statusEl) statusEl.textContent = String(text || "");
}

function findKeptTab(data, tabId) {
  return (data.kept_tabs || []).find((tab) => String(tab.id) === String(tabId));
}

function cardForTab(tabId) {
  return [...document.querySelectorAll("#kept-tabs-grid .kept-card")].find(
    (card) => String(card.dataset.tabId || "") === String(tabId)
  );
}

function disableAllPolishActions() {
  document.querySelectorAll("#kept-tabs-grid [data-polish-tab]").forEach((button) => {
    button.hidden = true;
    button.disabled = true;
  });
}

function setCardPolishState(cardEl, status, note = "", source = "") {
  if (!cardEl) return;
  const badge = cardEl.querySelector("[data-polish-badge]");
  const noteEl = cardEl.querySelector("[data-polish-note]");
  const button = cardEl.querySelector("[data-polish-tab]");
  const normalized = status || "ready";
  cardEl.dataset.summarySource = normalized;

  if (badge) {
    badge.className = "summary-source-badge";
    badge.classList.add("hidden");
    if (normalized === "polished") {
      badge.textContent =
        source === "gemini_verified"
          ? "Gemini grounded"
          : source === "gemma_verified"
            ? "Gemma grounded"
            : "AI grounded";
      badge.classList.add("verified");
      badge.classList.remove("hidden");
    } else if (normalized === "fallback") {
      badge.textContent = "Python summary";
      badge.classList.add("fallback");
      badge.classList.remove("hidden");
    } else if (normalized === "pending") {
      badge.textContent = "Polishing…";
      badge.classList.remove("hidden");
    }
  }

  const defaultNotes = {
    polished: "Grounded in this page's evidence.",
    fallback: "Deterministic Python summary; the AI rewrite did not pass grounding checks.",
    pending: "Improving clarity without adding facts…",
    ready: "Deterministic Python summary · AI polish available.",
    unavailable: "Deterministic Python summary.",
  };
  if (noteEl) noteEl.textContent = note || defaultNotes[normalized] || defaultNotes.ready;

  if (button) {
    button.classList.remove("hidden");
    button.hidden = false;
    button.disabled = false;
    if (normalized === "polished") {
      button.textContent = "Polished";
      button.disabled = true;
      button.hidden = true;
    } else if (normalized === "pending") {
      button.textContent = "Polishing…";
      button.disabled = true;
    } else if (normalized === "fallback") {
      button.textContent = "Python summary";
      button.disabled = true;
      button.hidden = true;
    } else if (normalized === "unavailable") {
      button.hidden = true;
    } else {
      button.textContent = "Polish with AI";
    }
  }
}

function updateCardSummaryDom(cardEl, tabId, result, data) {
  if (!cardEl) return;
  const sourceTab = findKeptTab(data, tabId) || {};
  const fullReadMin = Number(result.full_read_min || sourceTab.full_read_min || 0);
  const sumReadSec = Number(result.summary_read_sec || sourceTab.summary_read_sec || 25);
  const overview = result.overview != null ? result.overview : sourceTab.overview || "";
  const takeaways = Array.isArray(result.takeaways) ? result.takeaways : sourceTab.takeaways || [];
  const interest = result.interest_section || sourceTab.interest_section;

  const overviewEl = cardEl.querySelector("[data-summary-overview]");
  if (overviewEl) overviewEl.textContent = overview;

  const takeawaysEl = cardEl.querySelector("[data-summary-takeaways]");
  if (takeawaysEl) {
    takeawaysEl.innerHTML = takeaways.map((item) => formatTakeawayHtml(item)).join("");
  }

  const generalMeta = cardEl.querySelector("[data-general-meta]");
  if (generalMeta) {
    generalMeta.textContent = `Reads in ~${sumReadSec} sec (vs. ${fullReadMin} min full article)`;
  }
  const generalTabMeta = cardEl.querySelector("[data-general-tab-meta]");
  if (generalTabMeta) generalTabMeta.textContent = `~${sumReadSec}s read`;
  const readTimePill = cardEl.querySelector("[data-card-readtime]");
  if (readTimePill) {
    readTimePill.textContent = `Full article: ${fullReadMin} min → Summary: ~${sumReadSec}s`;
  }

  if (interest && interest.active) {
    const whyEl = cardEl.querySelector("[data-interest-why]");
    if (whyEl) whyEl.textContent = interest.why_relevant || "";
    const pointsEl = cardEl.querySelector("[data-interest-points]");
    if (pointsEl) {
      pointsEl.innerHTML = (interest.tailored_points || [])
        .map((point) => formatTakeawayHtml(point))
        .join("");
    }
    const interestMeta = cardEl.querySelector("[data-interest-meta]");
    if (interestMeta) {
      const pct = Math.round(Number(sourceTab.p50_score || 0));
      interestMeta.textContent = `${pct}% Relevance · ${Number(interest.mention_count || 0)} mentions in text`;
    }
  }

  const status = result.polish_status || "ready";
  const reasons =
    result.summary_validation && Array.isArray(result.summary_validation.reasons)
      ? result.summary_validation.reasons
      : [];
  const fallbackNote =
    status === "fallback" && reasons.length > 0
      ? `Kept verified Python summary — AI draft rejected by grounding gate (${reasons.slice(0, 2).join(", ")}).`
      : "";
  setCardPolishState(cardEl, status, fallbackNote, result.summary_source || "");
}

function updatePolishedCards(data, results) {
  Object.entries(results || {}).forEach(([tabId, result]) => {
    const index = (data.kept_tabs || []).findIndex((tab) => String(tab.id) === String(tabId));
    if (index < 0) return;
    const current = data.kept_tabs[index];
    data.kept_tabs[index] = { ...current, ...result, id: current.id };
    updateCardSummaryDom(cardForTab(tabId), tabId, result, data);
  });
}

function maybeRefreshDigestAfterPolish(data, requestId, previousMinutes) {
  const nextMinutes = Number(data.minutes_saved || 0);
  if (Math.abs(nextMinutes - Number(previousMinutes || 0)) < 0.05) return;
  const minutesEl = $("glance-minutes");
  if (minutesEl) minutesEl.textContent = `${nextMinutes} min`;
  upgradeDigestWithGemma(data, requestId);
}

async function upgradeTabSummaries(data, requestId, tabIds, { manual = false } = {}) {
  if (!data || !data.job_id) {
    if (manual) showToast("Polish is unavailable for this result. Run triage again.", "info");
    return;
  }
  const ids = [...new Set((tabIds || []).map((id) => String(id)).filter(Boolean))];
  if (ids.length === 0) return;

  const controller = new AbortController();
  state.summaryPolishControllers.add(controller);
  ids.forEach((tabId) => setCardPolishState(cardForTab(tabId), "pending"));
  setSummaryPolishStatus(
    manual ? "Polishing this summary with grounded AI…" : `Polishing ${ids.length} summaries with grounded AI…`
  );
  const previousMinutes = Number(data.minutes_saved || 0);

  try {
    const resp = await fetch("/polish-tabs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_id: data.job_id, tab_ids: ids }),
      signal: controller.signal,
    });
    if (requestId !== state.triageRequestId) return;
    if (!resp.ok) {
      let detail = `HTTP ${resp.status}`;
      try {
        const errorPayload = await resp.json();
        if (errorPayload && errorPayload.detail) detail = String(errorPayload.detail);
      } catch (error) {
        // Keep the HTTP status.
      }
      if (resp.status === 404 || resp.status === 410) {
        data.polish_available = false;
        disableAllPolishActions();
        ids.forEach((tabId) => setCardPolishState(cardForTab(tabId), "unavailable"));
        setSummaryPolishStatus("Polish session expired. Deterministic summaries remain active.");
      } else if (resp.status === 422) {
        ids.forEach((tabId) => setCardPolishState(cardForTab(tabId), "unavailable"));
        setSummaryPolishStatus("That tab is no longer part of this polish session.");
      } else if (resp.status === 503) {
        data.polish_available = false;
        disableAllPolishActions();
        ids.forEach((tabId) => setCardPolishState(cardForTab(tabId), "unavailable"));
        setSummaryPolishStatus("Grounded AI polish is unavailable. Deterministic summaries remain active.");
      } else {
        setSummaryPolishStatus("AI polish could not start. Deterministic summaries remain active.");
      }
      if (manual) showToast(detail, "error", 5000);
      return;
    }

    const payload = await resp.json();
    if (requestId !== state.triageRequestId) return;
    const results = payload.results || {};
    updatePolishedCards(data, results);
    if (payload.minutes_saved != null) data.minutes_saved = Number(payload.minutes_saved);

    const resultValues = Object.values(results);
    const polishedCount = resultValues.filter((result) => result.polish_status === "polished").length;
    const fallbackCount = resultValues.filter((result) => result.polish_status === "fallback").length;
    const pendingCount = resultValues.filter((result) => result.polish_status === "pending").length;
    if (polishedCount > 0 && fallbackCount === 0) {
      setSummaryPolishStatus(
        `${polishedCount} summar${polishedCount === 1 ? "y" : "ies"} polished and grounded in page evidence.`
      );
    } else if (polishedCount > 0 && fallbackCount > 0) {
      setSummaryPolishStatus(
        `${polishedCount} polished; ${fallbackCount} kept the deterministic version after grounding checks.`
      );
    } else if (fallbackCount > 0) {
      setSummaryPolishStatus(
        `${fallbackCount} rewrite${fallbackCount === 1 ? "" : "s"} could not be verified; deterministic summaries remain active.`
      );
    } else if (pendingCount > 0) {
      setSummaryPolishStatus("A summary is already being polished. The deterministic version remains visible.");
    }
    maybeRefreshDigestAfterPolish(data, requestId, previousMinutes);
  } catch (error) {
    if (error.name === "AbortError") return;
    if (requestId !== state.triageRequestId) return;
    console.warn("Grounded tab polish skipped:", error);
    ids.forEach((tabId) => setCardPolishState(cardForTab(tabId), "ready"));
    setSummaryPolishStatus("AI polish is unavailable. Deterministic summaries remain active.");
    if (manual) showToast("AI polish is unavailable right now.", "error");
  } finally {
    state.summaryPolishControllers.delete(controller);
  }
}

function polishSingleTabSummary(tabId, button) {
  if (!state.lastResult || !state.lastResult.job_id) {
    showToast("Polish is unavailable for this result. Run triage again.", "info");
    return;
  }
  if (button) {
    button.disabled = true;
    button.textContent = "Polishing…";
  }
  upgradeTabSummaries(state.lastResult, state.triageRequestId, [tabId], { manual: true });
}

function renderWorkspace(data) {
  const isRated = Boolean(data.rated);
  $("pile-title-display").textContent = data.pile_name || "Open Tabs";
  $("raw-tab-count").textContent = data.total_tabs;
  $("glance-minutes").textContent = `${data.minutes_saved} min`;
  $("glance-closed").textContent = data.closed_count;
  $("glance-kept").textContent = data.kept_count;
  const glanceWords = $("glance-words");
  if (glanceWords) glanceWords.textContent = Number(data.closed_words || 0).toLocaleString();
  $("glance-latency").textContent = `TabPFN: ${data.ml_latency_ms} ms`;

  const badge = $("gemma-source-badge");
  if (badge) badge.textContent = isRated ? "Verifying summary…" : "Deterministic";

  const polishTargets = new Set((data.polish_targets || []).map((id) => String(id)));
  const polishAvailable = Boolean(data.polish_available && data.job_id);
  setSummaryPolishStatus(
    polishAvailable
      ? polishTargets.size > 0
        ? `Queued ${polishTargets.size} summaries for grounded AI polish.`
        : "Deterministic summaries ready. AI polish is available per card."
      : "Deterministic summaries ready."
  );

  const focusPill = $("active-focus-pill");
  const clearLiveBtn = $("btn-clear-live-filter");
  if (focusPill) {
    if (isRated && data.effective_intent) {
      focusPill.textContent = `Filtered by Interest: "${data.effective_intent}"`;
      clearLiveBtn?.classList.remove("hidden");
    } else {
      focusPill.textContent = "Mode: Summarize All Tabs (No Rating)";
      clearLiveBtn?.classList.add("hidden");
    }
  }

  // Highlight matching live chip & intake chip
  const curIntent = (data.effective_intent || "").toLowerCase();
  document.querySelectorAll(".live-chip").forEach((c) => {
    const chipTopic = (c.getAttribute("data-live-topic") || "").toLowerCase();
    const active = chipTopic === curIntent;
    c.classList.toggle("active", active);
    c.setAttribute("aria-pressed", active ? "true" : "false");
  });
  document.querySelectorAll(".topic-chip").forEach((c) => {
    const chipTopic = (c.getAttribute("data-topic") || "").toLowerCase();
    const active = chipTopic === curIntent;
    c.classList.toggle("active", active);
    c.setAttribute("aria-pressed", active ? "true" : "false");
  });

  $("directive-text").textContent = data.spoken_digest;
  $("kept-header-count").textContent = data.kept_count;
  $("closed-header-count").textContent = data.closed_count;

  const keptColTitle = $("kept-col-title");
  const keptColSub = $("kept-col-sub");
  const keptCounterLabel = $("glance-kept-label");
  const closedCounterLabel = $("glance-closed-label");
  const closedColTitle = $("closed-col-title");
  const closedColSub = $("closed-col-sub");
  const splitContainer = $("split-results-container");
  const closePanel = $("col-close-panel");
  const globalSwitcher = $("global-card-tab-switcher");
  const hasClosed = Number(data.closed_count || 0) > 0;
  const isHallOfFame = data.preset_key === "dev_hall_of_fame_pile";

  if (!isRated) {
    splitContainer?.classList.toggle("single-col-mode", !hasClosed);
    closePanel?.classList.toggle("hidden", !hasClosed);
    globalSwitcher?.classList.add("hidden");
    if (keptColTitle) keptColTitle.innerHTML = `All Open Tabs Summarized (<span id="kept-header-count">${data.kept_count}</span>)`;
    if (keptColSub) keptColSub.textContent = "Complete executive summary and section-anchored takeaways from every page—type any topic above to unlock a Topic-Focused Tab.";
    if (keptCounterLabel) keptCounterLabel.textContent = `Tabs summarized (${Number(data.total_words || 0).toLocaleString()} words)`;
    if (closedColTitle) closedColTitle.innerHTML = `Unreadable or Broken Tabs (<span id="closed-header-count">${data.closed_count}</span>)`;
    if (closedColSub) closedColSub.textContent = "These links could not be read and can be safely skipped.";
    if (closedCounterLabel) {
      closedCounterLabel.innerHTML = `Broken or unreadable tabs (<span id="glance-words">${Number(data.closed_words || 0).toLocaleString()}</span> words)`;
    }
  } else if (data.closed_count === 0) {
    splitContainer?.classList.add("single-col-mode");
    closePanel?.classList.add("hidden");
    globalSwitcher?.classList.toggle("hidden", data.kept_count === 0);
    if (keptColTitle) keptColTitle.innerHTML = `Matches Your Interest (<span id="kept-header-count">${data.kept_count}</span>)`;
    if (keptColSub) keptColSub.textContent = `All ${data.kept_count} open tabs match "${data.effective_intent}" — switch between the Topic-Focused Tab and Complete Summary on any card.`;
    if (keptCounterLabel) keptCounterLabel.textContent = "Matching tabs";
    if (closedColTitle) {
      closedColTitle.innerHTML = isHallOfFame
        ? `All Hall of Fame Exhibits Match (<span id="closed-header-count">${data.closed_count}</span>)`
        : `Unrelated to Your Interest — Safe to Close (<span id="closed-header-count">${data.closed_count}</span>)`;
    }
    if (closedColSub) {
      closedColSub.textContent = isHallOfFame
        ? `Every exhibit in the collection matches "${data.effective_intent}".`
        : "These tabs don't cover the topic you asked for right now.";
    }
    if (closedCounterLabel) {
      closedCounterLabel.innerHTML = `${
        isHallOfFame ? "Other Hall of Fame exhibits" : "Unrelated tabs to close"
      } (0 min · <span id="glance-words">0</span> words)`;
    }
  } else {
    splitContainer?.classList.remove("single-col-mode");
    closePanel?.classList.remove("hidden");
    globalSwitcher?.classList.toggle("hidden", data.kept_count === 0);
    if (keptColTitle) keptColTitle.innerHTML = `Matches Your Interest (<span id="kept-header-count">${data.kept_count}</span>)`;
    if (keptColSub) keptColSub.textContent = `Ranked by relevance to "${data.effective_intent}" — click the tabs on any card to switch between Topic Focus and Complete Summary.`;
    if (keptCounterLabel) keptCounterLabel.textContent = "Relevant tabs kept";
    if (closedColTitle) {
      closedColTitle.innerHTML = isHallOfFame
        ? `Other Hall of Fame Exhibits (<span id="closed-header-count">${data.closed_count}</span>)`
        : `Unrelated to Your Interest — Safe to Close (<span id="closed-header-count">${data.closed_count}</span>)`;
    }
    if (closedColSub) {
      closedColSub.textContent = isHallOfFame
        ? `These masterpieces live in other Hall of Fame wings. Nothing is deleted—open them anytime or filter by another topic.`
        : "These tabs don't cover the topic you asked for right now.";
    }
    if (closedCounterLabel) {
      closedCounterLabel.innerHTML = isHallOfFame
        ? `Other exhibits not matched (${data.closed_read_min || data.minutes_saved} min · <span id="glance-words">${Number(data.closed_words || 0).toLocaleString()}</span> words)`
        : `Unrelated tabs to close (${data.closed_read_min || data.minutes_saved} min · <span id="glance-words">${Number(data.closed_words || 0).toLocaleString()}</span> words)`;
    }
  }

  if (isRated) {
    document.querySelectorAll(".global-switch-btn").forEach((btn) => {
      const active = btn.getAttribute("data-global-tab") === "interest";
      btn.classList.toggle("active", active);
      btn.setAttribute("aria-pressed", active ? "true" : "false");
    });
  }

  // 1. Visual Browser Tab Strip
  const tabStrip = $("visual-tab-strip");
  if (tabStrip) {
    const allTabs = data.all_tabs || [];
    const visibleTabs = allTabs.slice(0, 30);
    const hiddenTabCount = allTabs.length - visibleTabs.length;
    const tabsHtml = visibleTabs
      .map((t) => {
        const isKept = t.verdict === "KEEP_EXTRACT";
        const cls = isKept ? "mini-browser-tab tab-kept" : "mini-browser-tab tab-closed";
        const shortTitle = t.title.split(":")[0].slice(0, 26);
        const fullReadMin = t.full_read_min || roundTo(Number(t.word_count || 0) / 220, 1);
        const badgeText = isRated ? `${Math.round(t.p50_score)}%` : `${fullReadMin}m`;
        return `<div class="${cls}" title="${escapeHtml(t.title)}">
          <span class="mini-tab-pct">${badgeText}</span>
          <span>${escapeHtml(shortTitle)}</span>
        </div>`;
      })
      .join("");
    const overflowHtml =
      hiddenTabCount > 0
        ? `<div class="strip-overflow" title="Showing 30 of ${allTabs.length} tabs">+${hiddenTabCount} more</div>`
        : "";
    tabStrip.innerHTML = tabsHtml + overflowHtml;
  }

  // 2. Browserbase Session Banner
  const bbBanner = $("bb-session-banner");
  const bb = data.browserbase || {};
  if (bbBanner) {
    if (bb.live_session_created && bb.session_id) {
      bbBanner.classList.remove("hidden");
      const cdpPages = Number(bb.cdp_pages_inspected || 0);
      $("bb-session-text").textContent =
        `Browserbase Cloud Chromium Session: ${bb.session_id.slice(0, 8)}…` +
        (cdpPages > 0 ? ` · ${cdpPages} live DOM page${cdpPages === 1 ? "" : "s"} inspected over CDP` : "");
      const dbgLink = $("bb-debugger-link");
      if (dbgLink && bb.debugger_url) {
        dbgLink.href = bb.debugger_url;
        dbgLink.classList.remove("hidden");
      }
      const repLink = $("bb-replay-link");
      if (repLink && bb.replay_url) {
        repLink.href = bb.replay_url;
        repLink.classList.remove("hidden");
      }
    } else {
      bbBanner.classList.add("hidden");
    }
  }

  // 3. Left / Main Column: Summarized Cards with Interactive Segmented Tabs!
  const keptGrid = $("kept-tabs-grid");
  if (keptGrid) {
    if (!data.kept_tabs || data.kept_tabs.length === 0) {
      keptGrid.innerHTML = isRated
        ? `
        <div class="kept-card">
          <p class="tab-overview">
            <strong>0 of your ${data.total_tabs} open tabs matched "${escapeHtml(data.effective_intent)}".</strong>
            None of the open pages discuss that topic in depth. Their general summaries are available on the right, or you can switch to unrated summaries of all tabs:
          </p>
          <div>
            <button type="button" id="btn-empty-show-all" class="btn-action-outline pressable">
              Show General Summaries of All ${data.total_tabs} Tabs (No Filter)
            </button>
          </div>
        </div>`
        : `
        <div class="kept-card">
          <p class="tab-overview">
            <strong>No readable tabs were found in that pile.</strong>
            Try another sample window or paste different links.
          </p>
        </div>`;
      $("btn-empty-show-all")?.addEventListener("click", () => runTriage({ overrideIntent: "" }));
    } else {
      keptGrid.innerHTML = data.kept_tabs
        .map((t, cardIndex) => {
          const cardDomId = `tab-card-${cardIndex}`;
          const pct = Math.round(t.p50_score);
          const fullReadMin = t.full_read_min || roundTo(Number(t.word_count || 0) / 220, 1);
          const sumReadSec = t.summary_read_sec || 25;
          const topics = Array.isArray(t.topics) ? t.topics : [];
          const takeaways = Array.isArray(t.takeaways) ? t.takeaways : [];
          const overview = t.overview || t.snippet || "";
          const interestSec = t.interest_section;
          const hasInterestTab = Boolean(isRated && interestSec && interestSec.active);
          const isPolishTarget = polishTargets.has(String(t.id));
          const polishNote = isPolishTarget
            ? "Queued for grounded polish…"
            : polishAvailable
            ? "Deterministic Python summary · AI rewrite available."
            : "Deterministic Python summary.";
          const polishButtonHtml = polishAvailable && data.job_id
            ? `<button type="button" class="btn-polish-summary pressable" data-polish-tab="${escapeHtml(String(t.id))}" ${
                isPolishTarget ? "disabled" : ""
              }>${isPolishTarget ? "Polishing…" : "Polish with AI"}</button>`
            : "";

          const circumference = 69.1;
          const dashOffset = roundTo(circumference * (1 - Math.min(100, Math.max(4, pct)) / 100), 1);
          const scoreBoxHtml = isRated
            ? `<div class="radial-score-wrap" title="${pct}% relevance match">
                <svg class="radial-svg" viewBox="0 0 28 28" aria-hidden="true">
                  <circle class="radial-bg" cx="14" cy="14" r="11"></circle>
                  <circle class="radial-fill" cx="14" cy="14" r="11" stroke-dasharray="${circumference}" stroke-dashoffset="${dashOffset}"></circle>
                </svg>
                <span class="radial-label">${pct}% Match</span>
              </div>`
            : `<div class="score-box">
                <span class="readtime-pill font-mono" data-card-readtime>Full article: ${fullReadMin} min → Summary: ~${sumReadSec}s</span>
              </div>`;

          const wingPillHtml = t.wing
            ? `<span class="wing-pill">🏛️ Hall of Fame · ${escapeHtml(t.wing)}</span>`
            : "";
          const authorPillHtml = t.author
            ? `<span class="author-pill">by ${escapeHtml(t.author)}</span>`
            : "";

          const topicsHtml =
            topics.length > 0 || wingPillHtml
              ? `<div class="topic-tags-row">
                  ${wingPillHtml}
                  ${topics.map((tag) => `<span class="topic-tag">${escapeHtml(tag)}</span>`).join("")}
                  ${authorPillHtml}
                </div>`
              : "";

          // Special 1-click unpack banner if the user pasted the DEV Hall of Fame Roundup URL
          const hofUnpackHtml = t.is_collection_roundup
            ? `<div class="hof-unpack-banner no-print">
                <span class="hof-unpack-text">🏛️ This roundup post links to all 65 DEV Hall of Fame masterpieces (600+ min total reading).</span>
                <button type="button" class="btn-hof-unpack pressable">Unpack &amp; Filter All 65 Hall of Fame Articles →</button>
              </div>`
            : "";

          // Segmented Tab Bar inside the card (shown when a topic match exists!)
          const segmentedTabsHtml = hasInterestTab
            ? `<div class="card-tabs-bar no-print" role="tablist" aria-label="Summary view for ${escapeHtml(t.title)}">
                <button type="button" id="${cardDomId}-tab-interest" class="card-tab-btn active pressable" data-card-tab="interest" role="tab" aria-selected="true" aria-controls="${cardDomId}-panel-interest" tabindex="0">
                  <span>🎯 ${escapeHtml(interestSec.headline)}</span>
                  <span class="card-tab-meta">${interestSec.mention_count} mentions</span>
                </button>
                <button type="button" id="${cardDomId}-tab-general" class="card-tab-btn pressable" data-card-tab="general" role="tab" aria-selected="false" aria-controls="${cardDomId}-panel-general" tabindex="-1">
                  <span>📖 Complete Tab Summary</span>
                  <span class="card-tab-meta" data-general-tab-meta>~${sumReadSec}s read</span>
                </button>
              </div>`
            : "";

          // Panel A: Topic-Focused Synthesis (Active by default when `hasInterestTab` is true!)
          const interestPanelHtml = hasInterestTab
            ? `<div id="${cardDomId}-panel-interest" class="section-block section-interest card-tab-panel" data-card-panel="interest" role="tabpanel" aria-labelledby="${cardDomId}-tab-interest" tabindex="0">
                <div class="section-kicker-row">
                  <span class="section-kicker">
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><circle cx="12" cy="12" r="6"></circle><circle cx="12" cy="12" r="2"></circle></svg>
                    <span>${escapeHtml(interestSec.headline)}</span>
                  </span>
                  <span class="section-time-note" data-interest-meta>${pct}% Relevance · ${interestSec.mention_count} mentions in text</span>
                </div>
                <div class="interest-why-line" data-interest-why>${escapeHtml(interestSec.why_relevant)}</div>
                <ul class="takeaways-list" data-interest-points>
                  ${(Array.isArray(interestSec.tailored_points) ? interestSec.tailored_points : [])
                    .map((pt) => formatTakeawayHtml(pt))
                    .join("")}
                </ul>
              </div>`
            : "";

          // Panel B: Complete Tab Summary (Hidden initially when `hasInterestTab` is true; shown immediately in Unrated mode!)
          const generalPanelHiddenClass = hasInterestTab ? "hidden" : "";
          const generalPanelHiddenAttr = hasInterestTab ? "hidden" : "";
          const generalPanelHtml = `
            <div id="${cardDomId}-panel-general" class="section-block section-general card-tab-panel ${generalPanelHiddenClass}" data-card-panel="general" ${hasInterestTab ? `role="tabpanel" aria-labelledby="${cardDomId}-tab-general"` : `role="region" aria-label="Complete Tab Summary"`} tabindex="0" ${generalPanelHiddenAttr}>
              <div class="section-kicker-row">
                <span class="section-kicker">
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"></path><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"></path></svg>
                  <span>Complete Tab Summary</span>
                </span>
                <span class="section-time-note" data-general-meta>Reads in ~${sumReadSec} sec (vs. ${fullReadMin} min full article)</span>
              </div>
              <p class="tab-overview" data-summary-overview>${escapeHtml(overview)}</p>
              <ul class="takeaways-list" data-summary-takeaways>
                ${takeaways.map((item) => formatTakeawayHtml(item)).join("")}
              </ul>
            </div>`;

          return `
          <article class="kept-card" data-tab-id="${escapeHtml(String(t.id))}" data-summary-source="deterministic" aria-labelledby="${cardDomId}-title">
            <div class="kept-card-top">
              <div class="kept-title-group">
                <div class="domain-meta font-mono">
                  <span class="domain-pill">${escapeHtml(t.domain)}</span>
                  <span>${Number(t.word_count || 0).toLocaleString()} words (${fullReadMin} min full article)</span>
                  <span>·</span>
                  <span>${t.heading_count} sections</span>
                </div>
                <a id="${cardDomId}-title" href="${escapeHtml(t.url)}" target="_blank" rel="noopener" class="kept-title">${escapeHtml(t.title)}</a>
              </div>
              ${scoreBoxHtml}
            </div>

            ${topicsHtml}
            ${hofUnpackHtml}
            ${segmentedTabsHtml}
            ${interestPanelHtml}
            ${generalPanelHtml}
            ${
              polishAvailable && data.job_id
                ? `<div class="summary-polish-row no-print">
                    <span class="summary-polish-label">
                      <span class="summary-source-badge ${isPolishTarget ? "" : "hidden"}" data-polish-badge>${isPolishTarget ? "Polishing…" : ""}</span>
                      <span class="summary-polish-note" data-polish-note>${escapeHtml(polishNote)}</span>
                    </span>
                    ${polishButtonHtml}
                  </div>`
                : ""
            }
          </article>`;
        })
        .join("");

      // Bind per-card Segmented Tab click and keyboard listeners
      keptGrid.querySelectorAll(".card-tabs-bar").forEach((bar) => {
        bindRovingTablist(bar, ".card-tab-btn");
      });
      keptGrid.querySelectorAll(".card-tab-btn").forEach((btn) => {
        btn.addEventListener("click", () => {
          const cardEl = btn.closest(".kept-card");
          const targetTab = btn.getAttribute("data-card-tab") || "interest";
          switchCardTab(cardEl, targetTab);
        });
      });

      // Bind Hall of Fame Roundup unpack button if present
      keptGrid.querySelectorAll(".btn-hof-unpack").forEach((btn) => {
        btn.addEventListener("click", () => {
          runTriage({ forcePreset: "dev_hall_of_fame_pile" });
        });
      });

      keptGrid.querySelectorAll("[data-polish-tab]").forEach((btn) => {
        btn.addEventListener("click", () => {
          polishSingleTabSummary(btn.dataset.polishTab, btn);
        });
      });

      if (polishAvailable && polishTargets.size > 0) {
        polishTargets.forEach((tabId) => setCardPolishState(cardForTab(tabId), "pending"));
      }
    }
  }

  // 4. Right Column: Unrelated / Safe to Close Tabs
  const closedList = $("closed-tabs-list");
  if (closedList) {
    if (!data.closed_tabs || data.closed_tabs.length === 0) {
      closedList.innerHTML = `<div class="closed-item"><p class="closed-plain-why">${
        !isRated
          ? "No broken or unreadable tabs—nothing to close."
          : data.preset_key === "dev_hall_of_fame_pile"
          ? "Every Hall of Fame exhibit matches this topic."
          : "Every tab matched your interest—nothing to close."
      }</p></div>`;
    } else {
      const openAttr = data.kept_count === 0 ? "open" : "";
      closedList.innerHTML = data.closed_tabs
        .map((t) => {
          const pct = Math.round(t.p50_score);
          const fullReadMin = t.full_read_min || roundTo(Number(t.word_count || 0) / 220, 1);
          const sumReadSec = t.summary_read_sec || 25;
          const whyText = t.match_explanation || t.verdict_reason || `Low relevance (${pct}%)`;
          const overview = t.overview || "";
          const takeaways = Array.isArray(t.takeaways) ? t.takeaways : [];
          const peekHtml =
            overview || takeaways.length > 0
              ? `<details class="closed-peek" ${openAttr}>
                  <summary>Complete Tab Summary (~${sumReadSec}s read · skips ${fullReadMin} min full article)</summary>
                  <div class="closed-peek-body">
                    <p>${escapeHtml(overview)}</p>
                    ${
                      takeaways.length > 0
                        ? `<ul class="takeaways-list" style="margin-top:6px;">${takeaways
                            .slice(0, 3)
                            .map((tk) => formatTakeawayHtml(tk))
                            .join("")}</ul>`
                        : ""
                    }
                  </div>
                </details>`
              : "";

          return `
          <div class="closed-item">
            <div class="closed-top">
              <a href="${escapeHtml(t.url)}" target="_blank" rel="noopener" class="closed-title">${escapeHtml(t.title)}</a>
              <span class="low-score-pill font-mono">${isRated ? `${pct}% Match · ${fullReadMin}m article` : "Unreadable"}</span>
            </div>
            <div class="closed-plain-why">${escapeHtml(whyText)}</div>
            ${peekHtml}
          </div>`;
        })
        .join("");
    }
  }

  renderSentrySpans(data.sentry_spans || []);
}

function renderSentrySpans(spans) {
  const container = $("sentry-spans-list");
  if (!container) return;
  container.innerHTML = spans
    .map(
      (s) => `
      <div class="span-item">
        <div class="span-top">
          <span>${escapeHtml(s.span_name)}</span>
          <span>${s.latency_ms} ms</span>
        </div>
        <div class="span-sub">
          model: ${escapeHtml(s["gen_ai.request.model"])} ·
          in_tokens: ${s["gen_ai.usage.input_tokens"]} · out_tokens: ${s["gen_ai.usage.output_tokens"]}
        </div>
      </div>`
    )
    .join("");
}

function stopAllAudio() {
  if (state.audioElement) {
    state.audioElement.pause();
    state.audioElement.currentTime = 0;
  }
  if ("speechSynthesis" in window) {
    window.speechSynthesis.cancel();
  }
  state.isPlayingAudio = false;
  updateAudioButtonsUI(false);
}

function updateAudioButtonsUI(playing) {
  const heroBtn = $("btn-play-audio-hero");
  const heroPlayIcon = $("hero-icon-play");
  const heroPauseIcon = $("hero-icon-pause");
  const heroLabel = $("hero-play-label");

  if (heroBtn) heroBtn.classList.toggle("is-playing", playing);
  if (heroPlayIcon) heroPlayIcon.classList.toggle("hidden", playing);
  if (heroPauseIcon) heroPauseIcon.classList.toggle("hidden", !playing);
  if (heroLabel) {
    heroLabel.textContent = playing ? "Pause Voice Summary" : "Listen to Summary (15s)";
  }
}

function speakViaBrowserFallback(text) {
  if (!("speechSynthesis" in window)) return;
  window.speechSynthesis.cancel();
  const utter = new SpeechSynthesisUtterance(text);
  utter.rate = 0.96;
  utter.onend = () => {
    state.isPlayingAudio = false;
    updateAudioButtonsUI(false);
  };
  state.isPlayingAudio = true;
  updateAudioButtonsUI(true);
  window.speechSynthesis.speak(utter);
}

async function togglePlayDigestAudio() {
  if (state.isPlayingAudio) {
    stopAllAudio();
    return;
  }
  const text =
    (state.lastResult && state.lastResult.spoken_digest) ||
    $("directive-text").textContent.trim();
  if (!text) return;

  const heroLabel = $("hero-play-label");
  if (heroLabel) heroLabel.textContent = "Synthesizing audio…";

  try {
    const resp = await fetch("/synthesize-audio", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    if (!resp.ok) throw new Error(`TTS HTTP ${resp.status}`);
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    state.audioElement = new Audio(url);
    state.isPlayingAudio = true;
    updateAudioButtonsUI(true);
    state.audioElement.onended = () => {
      state.isPlayingAudio = false;
      updateAudioButtonsUI(false);
    };
    await state.audioElement.play();
  } catch (err) {
    console.warn("Using browser speech fallback:", err);
    speakViaBrowserFallback(text);
  }
}

async function toggleMicRecording() {
  const micBtn = $("btn-mic-scribe");
  const micLabel = $("mic-label");
  const badge = $("scribe-privacy-badge");

  if (state.isRecording && state.mediaRecorder) {
    state.mediaRecorder.stop();
    state.isRecording = false;
    micBtn.classList.remove("recording");
    micLabel.textContent = "Transcribing…";
    return;
  }

  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    alert("Microphone access is not available in this browser.");
    return;
  }

  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    state.audioChunks = [];
    state.mediaRecorder = new MediaRecorder(stream);
    state.mediaRecorder.ondataavailable = (e) => {
      if (e.data && e.data.size > 0) state.audioChunks.push(e.data);
    };
    state.mediaRecorder.onstop = async () => {
      stream.getTracks().forEach((tr) => tr.stop());
      const blob = new Blob(state.audioChunks, { type: "audio/webm" });
      const formData = new FormData();
      formData.append("file", blob, "voice_note.webm");

      try {
        const resp = await fetch("/transcribe-voice", { method: "POST", body: formData });
        if (!resp.ok) throw new Error(`Scribe HTTP ${resp.status}`);
        const res = await resp.json();
        if (res.text) {
          $("input-intent").value = res.text;
          updateDetectedUrlCounter();
          if (badge) badge.classList.remove("hidden");
        }
      } catch (err) {
        console.error("Scribe transcription error:", err);
      } finally {
        micLabel.textContent = "Speak Interest";
      }
    };

    state.mediaRecorder.start();
    state.isRecording = true;
    micBtn.classList.add("recording");
    micLabel.textContent = "Stop Recording";
  } catch (err) {
    console.error("Mic error:", err);
  }
}

async function copyTextToClipboard(text) {
  if (!text) return false;
  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch (error) {
      console.warn("Clipboard API unavailable, using fallback:", error);
    }
  }
  const helper = document.createElement("textarea");
  helper.value = text;
  helper.setAttribute("readonly", "");
  helper.style.position = "fixed";
  helper.style.opacity = "0";
  document.body.appendChild(helper);
  helper.select();
  let copied = false;
  try {
    copied = document.execCommand("copy");
  } catch (error) {
    copied = false;
  }
  helper.remove();
  return copied;
}

function downloadMarkdownCheatSheet() {
  if (!state.lastResult) {
    showToast("Run an analysis before exporting a Markdown summary.", "error");
    return;
  }
  const d = state.lastResult;
  const isRated = Boolean(d.rated);
  const lines = [
    `# Folivora — Executive Tab Summary`,
    `> **Overview:** ${d.spoken_digest}`,
    `> **Time Saved:** ${d.minutes_saved} minutes of full-page reading saved`,
    ``,
    `## ${isRated ? `Matches Your Interest (${d.kept_count})` : `All Summarized Tabs (${d.kept_count})`}`,
    ...d.kept_tabs.map((t) => {
      const pctStr = isRated ? ` — ${Math.round(t.p50_score)}% Match` : "";
      const isec = t.interest_section;
      const interestBlock =
        isRated && isec && isec.active
          ? `- **${isec.headline}:** ${isec.why_relevant}\n` +
            (isec.tailored_points || []).map((p) => `  - ${p}`).join("\n") +
            `\n`
          : "";
      const tks = (t.takeaways || []).map((k) => `  - ${k}`).join("\n");
      return `### [${t.title}](${t.url})${pctStr}\n${interestBlock}- **Complete Summary:** ${t.overview || ""}\n${tks}\n`;
    }),
  ];
  if (d.closed_tabs && d.closed_tabs.length > 0) {
    lines.push(
      d.preset_key === "dev_hall_of_fame_pile"
        ? `## Other Hall of Fame Exhibits — Not Matched (${d.closed_count})`
        : `## Unrelated / Safe to Close (${d.closed_count})`,
      ...d.closed_tabs.map(
        (t) => `- [${t.title}](${t.url}) (${t.match_explanation || t.verdict_reason})`
      )
    );
  }
  const blob = new Blob([lines.join("\n")], { type: "text/markdown;charset=utf-8" });
  const downloadUrl = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = downloadUrl;
  a.download = "folivora-summary.md";
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(downloadUrl), 0);
  showToast("Markdown summary exported.", "success");
}

function downloadKeepersBookmarksHtml() {
  if (!state.lastResult || !Array.isArray(state.lastResult.kept_tabs)) return;
  const kept = state.lastResult.kept_tabs;
  const rows = kept.map(
    (t) => `    <DT><A HREF="${escapeHtml(t.url)}">${escapeHtml(t.title || t.url)}</A>`
  );
  const html = [
    "<!DOCTYPE NETSCAPE-Bookmark-file-1>",
    '<META HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=UTF-8">',
    "<TITLE>Folivora Surviving Tabs</TITLE>",
    "<H1>Folivora Surviving Tabs</H1>",
    "<DL><p>",
    `  <DT><H3>Folivora Keepers (${kept.length})</H3>`,
    "  <DL><p>",
    ...rows,
    "  </DL><p>",
    "</DL><p>",
  ].join("\n");
  const blob = new Blob([html], { type: "text/html;charset=utf-8" });
  const downloadUrl = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = downloadUrl;
  a.download = "folivora-keepers-bookmarks.html";
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(downloadUrl), 0);
  showToast(`Exported ${kept.length} surviving tab${kept.length === 1 ? "" : "s"} as bookmarks.html.`, "success");
}

document.addEventListener("DOMContentLoaded", () => {
  const modeTablist = document.querySelector(".intake-mode-tabs");
  bindRovingTablist(modeTablist, ".mode-tab");
  document.querySelectorAll(".mode-tab").forEach((btn) => {
    btn.addEventListener("click", () => {
      switchIntakeMode(btn.getAttribute("data-mode") || "paste");
    });
  });
  document.querySelectorAll(".topic-chip, .live-chip").forEach((chip) => {
    chip.setAttribute("aria-pressed", chip.classList.contains("active") ? "true" : "false");
  });
  document.querySelectorAll(".global-switch-btn").forEach((btn) => {
    btn.setAttribute("aria-pressed", btn.classList.contains("active") ? "true" : "false");
  });

  const urlsBox = $("input-custom-urls");
  if (urlsBox) {
    urlsBox.addEventListener("input", updateDetectedUrlCounter);
  }
  $("input-intent")?.addEventListener("input", updateDetectedUrlCounter);

  $("btn-paste-clipboard")?.addEventListener("click", async () => {
    try {
      const text = await navigator.clipboard.readText();
      const extracted = extractUrlsFromText(text);
      if (urlsBox && extracted.length > 0) {
        urlsBox.value = extracted.join("\n");
        updateDetectedUrlCounter();
        showToast(`Pasted ${extracted.length} link${extracted.length === 1 ? "" : "s"} from clipboard.`, "success");
      } else {
        showToast("No http/https links found in your clipboard.", "error");
      }
    } catch (_) {
      showToast("Clipboard permission denied—press Ctrl+V inside the link box.", "error");
    }
  });

  $("btn-fill-live-urls")?.addEventListener("click", () => {
    if (urlsBox) {
      urlsBox.value = SAMPLE_LIVE_URLS.join("\n");
      updateDetectedUrlCounter();
    }
  });

  $("btn-open-hof-preset")?.addEventListener("click", () => {
    runTriage({ forcePreset: "dev_hall_of_fame_pile" });
  });

  $("btn-switch-to-hof")?.addEventListener("click", () => {
    runTriage({ forcePreset: "dev_hall_of_fame_pile" });
  });

  // Global Card Tab Switcher in Results Header
  document.querySelectorAll(".global-switch-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const targetTab = btn.getAttribute("data-global-tab") || "interest";
      switchAllCardsTab(targetTab);
    });
  });

  // Intake topic chips
  document.querySelectorAll(".topic-chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      document.querySelectorAll(".topic-chip").forEach((c) => {
        c.classList.remove("active");
        c.setAttribute("aria-pressed", "false");
      });
      chip.classList.add("active");
      chip.setAttribute("aria-pressed", "true");
      const intentInput = $("input-intent");
      if (intentInput) {
        intentInput.value = chip.getAttribute("data-topic") || "";
        updateDetectedUrlCounter();
      }
    });
  });

  // Live filter bar inside Results Workspace
  $("btn-apply-live-filter")?.addEventListener("click", () => {
    const val = ($("live-intent-input")?.value || "").trim();
    runTriage({ overrideIntent: val });
  });
  $("live-intent-input")?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      const val = ($("live-intent-input")?.value || "").trim();
      runTriage({ overrideIntent: val });
    }
  });
  $("btn-clear-live-filter")?.addEventListener("click", () => {
    runTriage({ overrideIntent: "" });
  });
  document.querySelectorAll(".live-chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      const topic = chip.getAttribute("data-live-topic") || "";
      runTriage({ overrideIntent: topic });
    });
  });

  $("file-upload-bookmarks")?.addEventListener("change", async (e) => {
    const input = e.currentTarget;
    const file = input.files && input.files[0];
    if (!file) return;
    if (file.size > 5 * 1024 * 1024) {
      showToast("That bookmarks file is larger than 5 MB.", "error");
      input.value = "";
      return;
    }
    if (!/\.(html?|txt|json|md)$/i.test(file.name)) {
      showToast("Choose an HTML, text, JSON, or Markdown bookmarks file.", "error");
      input.value = "";
      return;
    }
    try {
      const text = await file.text();
      const extracted = extractUrlsFromText(text);
      if (urlsBox && extracted.length > 0) {
        urlsBox.value = extracted.join("\n");
        updateDetectedUrlCounter();
        const clipped = (text.match(/https?:\/\//gi) || []).length > 65;
        showToast(
          clipped
            ? "Imported the first 65 links from that file."
            : `Imported ${extracted.length} link${extracted.length === 1 ? "" : "s"}.`,
          "success"
        );
      } else {
        showToast("No links were found in that file.", "error");
      }
    } catch (error) {
      console.error("Bookmarks import failed:", error);
      showToast("Could not read that bookmarks file.", "error");
    } finally {
      input.value = "";
    }
  });

  document.querySelectorAll(".demo-pile-card, .btn-QuickDemo").forEach((btn) => {
    btn.addEventListener("click", () => {
      const preset =
        btn.getAttribute("data-preset") ||
        btn.getAttribute("data-quick-preset") ||
        "postgres_docker_pile";
      state.presetKey = preset;
      if (urlsBox) urlsBox.value = "";
      updateDetectedUrlCounter();
      runTriage({ forcePreset: preset });
    });
  });

  $("btn-run-triage")?.addEventListener("click", () => runTriage());
  $("btn-reset-view")?.addEventListener("click", showIntakeScreen);
  $("nav-home-link")?.addEventListener("click", (e) => {
    e.preventDefault();
    showIntakeScreen();
  });
  $("btn-back-to-input")?.addEventListener("click", showIntakeScreen);

  $("btn-play-audio-hero")?.addEventListener("click", togglePlayDigestAudio);
  $("btn-mic-scribe")?.addEventListener("click", toggleMicRecording);
  $("btn-print-card-hero")?.addEventListener("click", () => window.print());
  $("btn-export-md")?.addEventListener("click", downloadMarkdownCheatSheet);
  $("btn-export-bookmarks")?.addEventListener("click", downloadKeepersBookmarksHtml);

  $("btn-copy-digest")?.addEventListener("click", async (e) => {
    const txt = $("directive-text")?.textContent || "";
    const btn = e.currentTarget;
    const prev = btn.innerHTML;
    const copied = await copyTextToClipboard(txt);
    btn.textContent = copied ? "Copied" : "Copy failed";
    showToast(
      copied ? "Summary copied to clipboard." : "Clipboard access was blocked. Select the summary text instead.",
      copied ? "success" : "error"
    );
    setTimeout(() => {
      btn.innerHTML = prev;
    }, 1400);
  });

  const params = new URLSearchParams(window.location.search);
  const extUrls = params.get("urls");
  const extIntent = params.get("intent");
  if (extIntent && $("input-intent")) {
    $("input-intent").value = extIntent;
  }
  if (extUrls && urlsBox) {
    urlsBox.value = extUrls;
    updateDetectedUrlCounter();
    runTriage();
  }
});
