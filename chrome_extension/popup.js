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
    undoBtn.textContent = `↩️ Restore ${urls.length} Last Closed Tab${urls.length === 1 ? "" : "s"}`;
    undoBtn.classList.remove("hidden");
  } else {
    undoBtn.classList.add("hidden");
  }
}

document.getElementById("btn-open-dashboard").addEventListener("click", async () => {
  await scanCurrentWindowTabs();
  const serverUrl = document.getElementById("server-url").value.replace(/\/$/, "");
  const intent = document.getElementById("intent-input").value.trim();
  const urlsParam = encodeURIComponent(detectedTabs.map((t) => t.url).join("\n"));
  const intentParam = intent ? `&intent=${encodeURIComponent(intent)}` : "";
  chrome.tabs.create({ url: `${serverUrl}/?urls=${urlsParam}${intentParam}` });
});

document.getElementById("btn-quick-summary").addEventListener("click", async () => {
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

  resBox.textContent = `Analyzing ${detectedTabs.length} open tabs with TabPFN + Gemma…`;
  resBox.classList.remove("hidden");

  try {
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
        if (errorPayload && errorPayload.detail) detail = String(errorPayload.detail);
      } catch (error) {
        detail = `HTTP ${resp.status}`;
      }
      throw new Error(detail);
    }
    const data = await resp.json();
    resBox.textContent = data.spoken_digest || "Triage complete.";

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
        `💤 Hibernate ${pendingCloseTabIds.length} Unrelated Tab${pendingCloseTabIds.length === 1 ? "" : "s"} (0 RAM)`;
      document.getElementById("btn-close-unrelated").textContent =
        `🗑️ Close ${pendingCloseTabIds.length} Unrelated Tab${pendingCloseTabIds.length === 1 ? "" : "s"} (With Undo)`;
      cleanupBox.classList.remove("hidden");
    }
  } catch (e) {
    resBox.textContent = `Could not analyze these tabs: ${e.message}`;
  }
});

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
  if (chrome.storage && chrome.storage.local) {
    await chrome.storage.local.set({ lastClosedUrls: pendingCloseUrls });
  }
  await chrome.tabs.remove(pendingCloseTabIds);
  const closedCount = pendingCloseTabIds.length;
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
