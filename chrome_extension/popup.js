// Folivora popup — a one-click launcher: scans this window's open tabs and
// opens the full Reading Digest with them prefilled (and your topic, if any).
let detectedTabs = [];

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

async function openDashboard() {
  await scanCurrentWindowTabs();
  const serverUrl = document.getElementById("server-url").value.replace(/\/$/, "");
  const intent = document.getElementById("intent-input").value.trim();
  const urlsParam = encodeURIComponent(detectedTabs.map((t) => t.url).join("\n"));
  const intentParam = intent ? `&intent=${encodeURIComponent(intent)}` : "";
  chrome.tabs.create({ url: `${serverUrl}/?urls=${urlsParam}${intentParam}` });
}

document.getElementById("btn-open-dashboard").addEventListener("click", openDashboard);
document.getElementById("intent-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    openDashboard();
  }
});

scanCurrentWindowTabs();
