let detectedTabs = [];
let pendingCloseTabIds = [];
let pendingCloseUrls = [];

function normalizeUrl(url) {
  return String(url || "").trim().replace(/\/$/, "");
}

async function scanCurrentWindowTabs() {
  const tabs = await chrome.tabs.query({ currentWindow: true });
  detectedTabs = tabs.filter(
    (t) =>
      t.url &&
      (t.url.startsWith("http://") || t.url.startsWith("https://")) &&
      !t.url.includes("localhost:8000") &&
      !t.url.includes("folivora.tech") &&
      !t.url.includes("folivora.onrender.com")
  );
  const badge = document.getElementById("tab-count-badge");
  badge.textContent = `${detectedTabs.length} open tabs`;
}

async function refreshUndoButton() {
  const undoBtn = document.getElementById("btn-undo-close");
  if (!chrome.storage || !chrome.storage.local) return;
  const data = await chrome.storage.local.get(["lastClosedUrls"]);
  const urls = Array.isArray(data.lastClosedUrls) ? data.lastClosedUrls : [];
  if (urls.length > 0) {
    undoBtn.textContent = `Restore ${urls.length} Last Closed Tab${urls.length === 1 ? "" : "s"}`;
    undoBtn.classList.remove("hidden");
  } else {
    undoBtn.classList.add("hidden");
  }
}

async function openDashboard() {
  await scanCurrentWindowTabs();
  const serverUrl = document.getElementById("server-url").value.replace(/\/$/, "");
  const intent = document.getElementById("intent-input").value.trim();
  const urlsParam = encodeURIComponent(detectedTabs.map((t) => t.url).join("\n"));
  const intentParam = intent ? `&intent=${encodeURIComponent(intent)}` : "";
  chrome.tabs.create({ url: `${serverUrl}/?urls=${urlsParam}${intentParam}` });
}

document.getElementById("btn-open-dashboard").addEventListener("click", openDashboard);

document.getElementById("btn-quick-summary").addEventListener("click", async (event) => {
  const btn = event.currentTarget;
  if (btn.disabled) return; // never fire duplicate /triage POSTs on double-click
  btn.disabled = true;
  try {
    await runQuickSummary();
  } finally {
    btn.disabled = false;
  }
});

async function runQuickSummary() {
  const runBtn = document.getElementById("btn-quick-summary");
  const origLabel = runBtn.textContent;
  runBtn.textContent = "Working…";
  try {
    await scanCurrentWindowTabs();
    const resBox = document.getElementById("result-box");
    const cleanupBox = document.getElementById("cleanup-box");
    const serverUrl = document.getElementById("server-url").value.replace(/\/$/, "");
    const intent = document.getElementById("intent-input").value.trim();

    cleanupBox.classList.add("hidden");
    pendingCloseTabIds = [];
    pendingCloseUrls = [];

    if (detectedTabs.length === 0) {
      resBox.textContent = "No external http/https tabs open in this window.";
      resBox.classList.remove("hidden");
      return;
    }

    resBox.textContent = `Summarizing ${detectedTabs.length} open tabs…`;
    resBox.classList.remove("hidden");

    const resp = await fetch(`${serverUrl}/triage`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        custom_urls: detectedTabs.map((t) => t.url),
        user_intent: intent,
        verify_live_browserbase: false,
        include_gemma: true,
      }),
    });
    if (!resp.ok) {
      let detail = `HTTP ${resp.status}`;
      try {
        const errorPayload = await resp.json();
        if (errorPayload && errorPayload.detail) {
          // FastAPI 422s arrive as a list of {msg, ...} objects; flatten to messages.
          detail = Array.isArray(errorPayload.detail)
            ? errorPayload.detail.map((d) => (d && d.msg) || JSON.stringify(d)).join("; ")
            : String(errorPayload.detail);
        }
      } catch (error) {
        detail = `HTTP ${resp.status}`;
      }
      throw new Error(detail);
    }
    const data = await resp.json();
    const esc = (s) =>
      String(s ?? "").replace(/[&<>"']/g, (c) => (
        { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
      ));
    const trunc = (s, n) => {
      const str = String(s ?? "");
      return str.length > n ? `${str.slice(0, n - 1).trimEnd()}\u2026` : str;
    };
    const kept = Array.isArray(data.kept_tabs) ? data.kept_tabs : [];
    const closed = Array.isArray(data.closed_tabs) ? data.closed_tabs : [];
    const walls = kept.filter((t) => t && t.is_login_walled);
    const rated = Boolean(data.rated); // % badges only exist when a topic was given
    const parts = [];
    parts.push('<div class="result-kicker">Triage result</div>');
    parts.push(
      `<div class="stat-line">${esc(String(kept.length))} kept &middot; ${esc(String(closed.length))} set aside &middot; ~${esc(String(data.minutes_saved ?? 0))} min saved</div>`
    );
    parts.push('<div class="result-divider"></div>');
    for (const t of kept.slice(0, 3)) {
      if (t.is_login_walled) {
        parts.push(
          `<div class="tab-row"><span class="tab-title">&#128274; ${esc(t.domain || "?")}</span>` +
          `<span class="tab-meta">${esc(t.wall_label || "login-walled")} &middot; kept open</span></div>`
        );
      } else {
        const meta = [`~${esc(String(t.full_read_min ?? "?"))} min read`];
        if (rated && t.p50_score) meta.push(`${esc(String(t.p50_score))}% match`);
        parts.push(
          `<div class="tab-row"><span class="tab-title">&#9733; ${esc(trunc(t.title, 56))}</span>` +
          `<span class="tab-meta">${esc(meta.join(" &middot; "))}</span></div>`
        );
      }
    }
    if (kept.length > 3) {
      parts.push(`<div class="tab-row tab-more">+${esc(String(kept.length - 3))} more in the full digest</div>`);
    }
    if (walls.length > 0) {
      parts.push(
        `<div class="mini-note">&#128274; ${esc(String(walls.length))} walled tab${walls.length === 1 ? "" : "s"} kept open — fetches carry no cookies, so the real content can't be inspected.</div>`
      );
    }
    if (!rated) {
      parts.push('<div class="mini-note">Tip: type a topic above to score and filter these tabs.</div>');
    }
    parts.push('<button type="button" class="btn btn-primary inline-open" id="btn-inline-open-digest">Open Full Reading Digest &rarr;</button>');
    resBox.innerHTML = parts.join("");
    document.getElementById("btn-inline-open-digest")?.addEventListener("click", openDashboard);

    const closedUrls = new Set(
      (data.closed_tabs || []).map((item) => normalizeUrl(item.url))
    );
    const matchingBrowserTabs = detectedTabs.filter((tab) =>
      closedUrls.has(normalizeUrl(tab.url))
    );
    if (matchingBrowserTabs.length > 0) {
      pendingCloseTabIds = matchingBrowserTabs.map((tab) => tab.id).filter(Boolean);
      pendingCloseUrls = matchingBrowserTabs.map((tab) => tab.url).filter(Boolean);
      document.getElementById("btn-hibernate-closed").textContent =
        `Put ${pendingCloseTabIds.length} Unrelated Tab${pendingCloseTabIds.length === 1 ? "" : "s"} to Sleep`;
      document.getElementById("btn-close-unrelated").textContent =
        `Close ${pendingCloseTabIds.length} Unrelated Tab${pendingCloseTabIds.length === 1 ? "" : "s"} (With Undo)`;
      cleanupBox.classList.remove("hidden");
    }
  } catch (e) {
    const resBox = document.getElementById("result-box");
    resBox.textContent = `Could not analyze these tabs: ${e.message}`;
    resBox.classList.remove("hidden");
  } finally {
    runBtn.textContent = origLabel;
  }
}

document.getElementById("btn-hibernate-closed").addEventListener("click", async () => {
  const resBox = document.getElementById("result-box");
  let count = 0;
  for (const tabId of pendingCloseTabIds) {
    try {
      await chrome.tabs.discard(tabId);
      count += 1;
    } catch (_) {
      // Active or already discarded tab
    }
  }
  resBox.textContent = `Hibernated ${count} unrelated tab${count === 1 ? "" : "s"} (freed RAM while keeping them in your tab bar).`;
});

document.getElementById("btn-close-unrelated").addEventListener("click", async () => {
  const resBox = document.getElementById("result-box");
  const cleanupBox = document.getElementById("cleanup-box");
  if (pendingCloseTabIds.length === 0) return;
  // Filter to tabs that still exist; a tab closed manually between the summary
  // and this click would otherwise reject the whole chrome.tabs.remove batch and
  // leave a Restore button pointing at tabs that were never closed.
  const lookups = await Promise.allSettled(pendingCloseTabIds.map((id) => chrome.tabs.get(id)));
  const targets = pendingCloseTabIds.filter((_, i) => lookups[i].status === "fulfilled");
  if (targets.length === 0) {
    pendingCloseTabIds = [];
    pendingCloseUrls = [];
    cleanupBox.classList.add("hidden");
    resBox.textContent = "Those tabs were already closed.";
    resBox.classList.remove("hidden");
    return;
  }
  let closedIds = [];
  try {
    await chrome.tabs.remove(targets);
    closedIds = targets;
  } catch (e) {
    // Close one-by-one so a single pinned/locked tab doesn't abort the batch.
    for (const tabId of targets) {
      try {
        await chrome.tabs.remove(tabId);
        closedIds.push(tabId);
      } catch (_) {
        // skip
      }
    }
  }
  // Snapshot only the tabs that were actually closed, so the Undo button can
  // never restore a tab that is still open.
  const closedIdSet = new Set(closedIds);
  const closedUrls = pendingCloseUrls.filter((_, i) => closedIdSet.has(pendingCloseTabIds[i]));
  if (chrome.storage && chrome.storage.local && closedUrls.length > 0) {
    await chrome.storage.local.set({ lastClosedUrls: closedUrls });
  }
  const closedCount = closedIds.length;
  pendingCloseTabIds = [];
  pendingCloseUrls = [];
  cleanupBox.classList.add("hidden");
  await scanCurrentWindowTabs();
  await refreshUndoButton();
  resBox.textContent = `Closed ${closedCount} unrelated tab${closedCount === 1 ? "" : "s"}. Click Restore above anytime to reopen them.`;
});

document.getElementById("btn-undo-close").addEventListener("click", async () => {
  if (!chrome.storage || !chrome.storage.local) return;
  const data = await chrome.storage.local.get(["lastClosedUrls"]);
  const urls = Array.isArray(data.lastClosedUrls) ? data.lastClosedUrls : [];
  for (const url of urls) {
    await chrome.tabs.create({ url, active: false });
  }
  await chrome.storage.local.set({ lastClosedUrls: [] });
  await scanCurrentWindowTabs();
  await refreshUndoButton();
  const resBox = document.getElementById("result-box");
  resBox.textContent = `Restored ${urls.length} tab${urls.length === 1 ? "" : "s"}.`;
  resBox.classList.remove("hidden");
});

scanCurrentWindowTabs();
refreshUndoButton();
