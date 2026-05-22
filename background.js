// background.js — MV3 Service Worker
// Owns the WebSocket connection (not subject to page CSP).
// Receives prompts from the local bridge over WebSocket, injects content.js
// into ChatGPT tabs, relays work to the content script, then returns results
// to the bridge.
//
// ── Content Script Watchdog ──
// When a content script port disconnects, a 60s grace timer starts. If the
// port hasn't reconnected within 60s, we attempt re-injection via
// chrome.scripting.executeScript. After 3 consecutive failures (≈3 min total),
// we escalate to chrome.runtime.reload() to restart the entire extension.
// All recovery attempts are logged to the console.

const HTTP_HEALTH_URL = "http://127.0.0.1:11557/health";
const WS_URL = "ws://127.0.0.1:11558";
const CHATGPT_URL_PATTERN = "https://chatgpt.com/*";
const CHATGPT_URL_RE = /^https:\/\/chatgpt\.com\//;
const PERIODIC_INJECT_INTERVAL_MS = 30_000; // bumped from 5s: watchdog handles post-disconnect

let ws = null;
let reconnectDelay = 2000;
let reconnectTimer = null;
let connecting = false;

// Maps tabId -> chrome.runtime.Port for content scripts that have called chrome.runtime.connect().
// A live port means the content script is loaded and healthy on that tab.
const connectedTabs = new Map();

// ── Content Script Watchdog State ──────────────────────────────────────
const WATCHDOG_GRACE_MS = 60_000;            // 60s grace before first re-injection
const WATCHDOG_RETRY_INTERVAL_MS = 60_000;   // 60s between re-injection attempts
const MAX_REINJECT_ATTEMPTS = 3;              // 3 consecutive failures → extension reload
const EXTENSION_RELOAD_COOLDOWN_MS = 180_000; // 3 min cooldown between reloads

// tabId -> { disconnectedAt: number, failureCount: number, timer: ReturnType<typeof setTimeout> }
const tabWatchdog = new Map();
let lastExtensionReload = 0;
let watchdogLog = []; // last 20 recovery events

function isChatGptUrl(url) {
  return typeof url === "string" && CHATGPT_URL_RE.test(url);
}

function logInjection(tabId, url, outcome, reason, detail) {
  console.log(
    `[ChatGPT Bridge] injection: ${outcome} | tab=${tabId} | reason=${reason}` +
      (detail ? ` | ${detail}` : ""),
    { url: url || "n/a" }
  );
}

// ── Injection ──────────────────────────────────────────────────────────

/**
 * Inject content.js into a single tab.
 * @param {number} tabId
 * @param {string} reason  one of: startup, onUpdated, periodic, retry, send-miss
 * @returns {Promise<boolean>}
 */
async function injectContentScript(tabId, reason = "unknown") {
  try {
    await chrome.scripting.executeScript({
      target: { tabId },
      files: ["content.js"],
      world: "ISOLATED",
    });
    logInjection(tabId, null, "OK", reason, "script injected");
    return true;
  } catch (err) {
    logInjection(tabId, null, "FAIL", reason, err?.message || String(err));
    return false;
  }
}

/**
 * Inject content script into all ChatGPT tabs that are missing a live connection.
 * @returns {Promise<void>}
 */
async function injectOpenChatGptTabs(reason = "startup") {
  let tabs = [];
  try {
    tabs = await chrome.tabs.query({ url: CHATGPT_URL_PATTERN });
  } catch (err) {
    console.warn("[ChatGPT Bridge] tabs.query failed", err?.message || err);
    return;
  }

  const targets = tabs
    .filter((tab) => tab.id && isChatGptUrl(tab.url))
    .filter((tab) => tab.status === "complete"); // skip pages still loading

  await Promise.all(
    targets.map((tab) => injectIfMissing(tab, reason))
  );
}

/**
 * Inject content script into a specific tab only if there is no live content-script port.
 * Skips tabs that are still loading.
 */
async function injectIfMissing(tab, reason = "check") {
  if (!tab.id) return;
  if (!isChatGptUrl(tab.url)) return;

  // Skip pages still loading — onUpdated('complete') will fire when ready.
  if (tab.status !== "complete") {
    logInjection(
      tab.id,
      tab.url,
      "SKIP",
      reason,
      `tab not ready (status=${tab.status})`
    );
    return;
  }

  // If content script has already connected, nothing to do.
  if (connectedTabs.has(tab.id)) {
    // console.debug(`[ChatGPT Bridge] skip inject: tab ${tab.id} already connected`);
    return;
  }

  logInjection(tab.id, tab.url, "ATTEMPT", reason, `no live port found`);
  await injectContentScript(tab.id, reason);
}

// ── Content Script Watchdog ──────────────────────────────────────────────

/**
 * Log a watchdog event. Keeps the last 20 entries for diagnostics.
 */
function logWatchdog(event, tabId, detail = "") {
  const entry = {
    event,
    tabId: tabId ?? null,
    detail,
    ts: new Date().toISOString(),
  };
  console.log(
    `[ChatGPT Bridge] watchdog: ${event} | tab=${entry.tabId}` +
      (detail ? ` | ${detail}` : "")
  );
  watchdogLog.push(entry);
  if (watchdogLog.length > 20) watchdogLog.shift();

  // Forward the last 5 recovery events to the Python bridge over WS
  // so the /health endpoint can surface them without the bridge hitting
  // chrome.* APIs directly (which are inaccessible to a service worker).
  if (ws && ws.readyState === WebSocket.OPEN) {
    try {
      ws.send(JSON.stringify({
        type: "watchdog_events",
        events: watchdogLog.slice(-5),
      }));
      ws.send(JSON.stringify(buildWatchdogStatusPayload()));
    } catch (_) { /* bridge may be down during rel; onreconnect will sync */ }
  }
}

function buildWatchdogStatusPayload(chromeAlive = connectedTabs.size > 0) {
  const last = watchdogLog[watchdogLog.length - 1] || null;
  return {
    type: "watchdog_status",
    recovery_events: watchdogLog.length,
    last_recovery: last?.ts || null,
    chrome_alive: chromeAlive,
    events: watchdogLog.slice(-5),
  };
}

async function publishWatchdogStatus() {
  let chromeAlive = connectedTabs.size > 0;
  try {
    const tabs = await chrome.tabs.query({ url: CHATGPT_URL_PATTERN });
    chromeAlive = tabs.some((tab) => tab.id && isChatGptUrl(tab.url));
  } catch (_) {
    // Best-effort only — fall back to the live port map.
  }
  sendWs(buildWatchdogStatusPayload(chromeAlive));
}

/**
 * Start the watchdog timer for a tab whose content script port just
 * disconnected.  We wait WATCHDOG_GRACE_MS (60 s) before the first
 * re-injection attempt.
 */
function startWatchdog(tabId) {
  if (tabWatchdog.has(tabId)) {
    clearTimeout(tabWatchdog.get(tabId).timer);
  }
  tabWatchdog.set(tabId, {
    disconnectedAt: Date.now(),
    failureCount: 0,
    timer: setTimeout(() => onWatchdogFire(tabId), WATCHDOG_GRACE_MS),
  });
  logWatchdog("timer-started", tabId, `grace=${WATCHDOG_GRACE_MS / 1000}s`);
}

/**
 * Cancel the watchdog for a tab — called when the content script port
 * reconnects (successful recovery).
 */
function cancelWatchdog(tabId) {
  if (tabWatchdog.has(tabId)) {
    clearTimeout(tabWatchdog.get(tabId).timer);
    tabWatchdog.delete(tabId);
    logWatchdog("cancelled", tabId, "port reconnected — recovery complete");
  }
}

/**
 * Watchdog timer fired — attempt re-injection, or escalate to extension
 * reload after MAX_REINJECT_ATTEMPTS consecutive failures.
 */
async function onWatchdogFire(tabId) {
  const entry = tabWatchdog.get(tabId);
  if (!entry) return;

  entry.failureCount += 1;
  const attempt = entry.failureCount;

  logWatchdog("fired", tabId, `attempt ${attempt}/${MAX_REINJECT_ATTEMPTS}`);

  // ── Escalation: 3 consecutive failures → reload extension ────────────
  if (attempt > MAX_REINJECT_ATTEMPTS) {
    const now = Date.now();
    if (now - lastExtensionReload < EXTENSION_RELOAD_COOLDOWN_MS) {
      const cooldownRemaining = Math.ceil(
        (EXTENSION_RELOAD_COOLDOWN_MS - (now - lastExtensionReload)) / 1000
      );
      logWatchdog(
        "reload-cooldown",
        tabId,
        `cooldown active, ${cooldownRemaining}s remaining`
      );
      entry.timer = setTimeout(
        () => onWatchdogFire(tabId),
        EXTENSION_RELOAD_COOLDOWN_MS - (now - lastExtensionReload)
      );
      return;
    }

    logWatchdog(
      "reload-extension",
      tabId,
      `${MAX_REINJECT_ATTEMPTS} consecutive failures — reloading extension`
    );
    lastExtensionReload = now;
    tabWatchdog.delete(tabId);
    chrome.runtime.reload();
    return; // unreachable — extension restarts
  }

  // ── Attempt re-injection ─────────────────────────────────────────────
  try {
    const tabs = await chrome.tabs.query({ url: CHATGPT_URL_PATTERN });
    const tab = tabs.find((t) => t.id === tabId);

    if (!tab) {
      logWatchdog("tab-gone", tabId, "tab closed — cancelling watchdog");
      tabWatchdog.delete(tabId);
      return;
    }

    const success = await injectContentScript(tabId, "watchdog");
    if (success) {
      logWatchdog("reinject-ok", tabId, `attempt ${attempt} succeeded`);
      // Wait 30s for the content script to reconnect via port.
      // If it doesn't, onWatchdogFire will be called again.
      entry.timer = setTimeout(() => onWatchdogFire(tabId), 30_000);
    } else {
      logWatchdog("reinject-fail", tabId, `attempt ${attempt} failed`);
      // Retry after 60s
      entry.timer = setTimeout(() => onWatchdogFire(tabId), WATCHDOG_RETRY_INTERVAL_MS);
    }
  } catch (err) {
    logWatchdog("reinject-error", tabId, err?.message || String(err));
    entry.timer = setTimeout(() => onWatchdogFire(tabId), WATCHDOG_RETRY_INTERVAL_MS);
  }
}

// ── Content Script Lifecycle (onConnect) ─────────────────────────────────

chrome.runtime.onConnect.addListener((port) => {
  if (port.name === "chatgpt-bridge-content-script") {
    const tabId = port.sender?.tab?.id;
    if (tabId) {
      connectedTabs.set(tabId, port);
      logInjection(
        tabId,
        port.sender?.tab?.url || null,
        "CONNECT",
        "content-radar",
        "content script opened port"
      );
      // Port (re)connected — cancel any watchdog for this tab
      cancelWatchdog(tabId);
      requestModelCatalog("content-connect").catch((err) => {
        console.warn("[ChatGPT Bridge] model catalog refresh failed", err?.message || err);
      });
    }

    // Forward any messages the content script sends through the port.
    port.onMessage.addListener((msg, _sender) => {
      if (msg.action === "ping") {
        port.postMessage({ action: "pong" });
      }
      if (msg.action === "poll") {
        sendWs({ type: "poll", id: msg.id, request_id: msg.id, poll: msg.poll || {} });
      }
    });

    port.onDisconnect.addListener(() => {
      if (tabId) {
        if (connectedTabs.get(tabId) === port) {
          connectedTabs.delete(tabId);
          logInjection(
            tabId,
            null,
            "DISCONNECT",
            "content-radar",
            "content script port closed"
          );
          // Port disconnected — start watchdog for recovery
          startWatchdog(tabId);
        }
      }
    });
  }
});

// ── Tab Updated ─────────────────────────────────────────────────────────

chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (changeInfo.status === "complete" && isChatGptUrl(tab?.url)) {
    logInjection(
      tabId,
      tab?.url,
      "TRIGGER",
      "onUpdated-complete",
      "tab finished loading"
    );
    injectIfMissing(tab, "onUpdated");
  }
});

chrome.tabs.onRemoved.addListener((tabId) => {
  if (connectedTabs.delete(tabId)) {
    console.log(`[ChatGPT Bridge] tab ${tabId} closed – removed from connectedTabs`);
  }
});

// ── Send → Content Script ────────────────────────────────────────────────

/**
 * Send a message to the content script on a tab. Re-injects if needed.
 */
async function sendToContentScript(tabId, message) {
  const timeoutMs = Math.min(Math.max(Number(message.timeout || 10) * 1000, 1000), 600000) + 1000;
  const sendOnce = () => chrome.tabs.sendMessage(tabId, message);
  const withTimeout = (promise) =>
    Promise.race([
      promise,
      new Promise((_, reject) => setTimeout(() => reject(new Error("Content script timed out")), timeoutMs)),
    ]);

  try {
    return await withTimeout(sendOnce());
  } catch (firstErr) {
    // Content script might be missing. Mark it disconnected so injectIfMissing will re-inject.
    if (connectedTabs.get(tabId)) {
      logInjection(tabId, null, "STALE", "send-miss", "port existed but message failed — clearing");
      connectedTabs.delete(tabId);
    }

    const injected = await injectContentScript(tabId, "retry");
    if (!injected) throw firstErr;
    await sleep(250);
    return await withTimeout(sendOnce());
  }
}

async function requestModelCatalog(reason = "manual", requestId = null) {
  const tab = await findChatGptTab();
  if (!tab || !tab.id) {
    throw new Error("No ChatGPT tab found. Open https://chatgpt.com/ first.");
  }
  await injectIfMissing(tab, reason);
  const rid = requestId || `models-${Date.now()}`;
  const result = await sendToContentScript(tab.id, {
    action: "enumerate_models",
    request_id: rid,
  });
  if (!result || result.success === false) {
    throw new Error(result?.error || "Could not enumerate models");
  }
  const models = Array.isArray(result.models) ? result.models : [];
  sendWs({
    type: "model_catalog",
    id: rid,
    models,
    fetched_at: Date.now(),
    source: reason,
  });
  return models;
}

// ── Bridge WebSocket ─────────────────────────────────────────────────────

async function handleBridgePrompt(data) {
  const id = data.id;
  try {
    const tab = await findChatGptTab();
    if (!tab) {
      throw new Error("No ChatGPT tab found. Open https://chatgpt.com/ in the debug Chrome profile.");
    }

    // ── Navigate to the target conversation BEFORE sending to content script ──
    // Using location.href inside the content script kills it and requires a slow
    // retry cycle. Instead, navigate the tab here and wait for it to finish loading.
    const needNavigate = !!data.conversation_id && tab.url && !tab.url.includes(`/c/${data.conversation_id}`);
    if (needNavigate) {
      console.log(`[ChatGPT Bridge] navigating tab ${tab.id} to /c/${data.conversation_id}`);
      await new Promise((resolve, reject) => {
        const timeout = setTimeout(() => reject(new Error("Navigation timed out")), 15000);
        const listener = (changedTabId, changeInfo) => {
          if (changedTabId === tab.id && changeInfo.status === "complete") {
            chrome.tabs.onUpdated.removeListener(listener);
            clearTimeout(timeout);
            resolve();
          }
        };
        chrome.tabs.onUpdated.addListener(listener);
        chrome.tabs.update(tab.id, { url: `https://chatgpt.com/c/${data.conversation_id}` });
      });
      // Small delay for content script injection after navigation
      await sleep(500);
    }

    // Ensure content script is present before sending (handles fresh navigation too)
    await injectIfMissing(tab, "send-miss");

    let lastError = null;
    for (let attempt = 0; attempt < 2; attempt++) {
      try {
        const result = await sendToContentScript(tab.id, {
          action: "prompt",
          prompt: data.prompt || "",
          options: data.options || {},
          files: data.files || [],
          timeout: data.timeout || 10,
          attempt: attempt,
          conversation_id: data.conversation_id || null,
          model_search: data.model_search || null,
          stream: data.stream || false,
          debug: !!data.debug,
          id: data.id || "",
        });

        if (!result || result.success === false) {
          lastError = new Error(result?.error || "Content script returned no response");
          continue; // retry
        }

        sendWs({
          type: "response",
          id,
          text: result.text || "",
          conversation_id: result.conversation_id || null,
          conversation_title: result.conversation_title || null,
          _debug: result._debug || null,
        });
        return;
      } catch (err) {
        lastError = err instanceof Error ? err : new Error(String(err));
        // Re-inject just in case the content script left the tab in a broken state.
        if (attempt === 0) {
          try { await injectContentScript(tab.id, "retry"); } catch (_) {}
          await sleep(250);
        }
        // loop will retry; if attempt === 1 lastError will be propagated below.
      }
    }
    // Both attempts exhausted.
    throw lastError || new Error("ChatGPT did not generate a response after 2 attempts");
  } catch (err) {
    sendWs({ type: "error", id, error: err?.message || String(err) });
  }
}

async function handleBridgeReload() {
  await injectOpenChatGptTabs("reload");
  const tabs = await chrome.tabs.query({ url: CHATGPT_URL_PATTERN });
  await Promise.all(
    tabs
      .filter((tab) => tab.id && isChatGptUrl(tab.url))
      .map((tab) => chrome.tabs.reload(tab.id).catch(() => {}))
  );
}

function sendWs(payload) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(payload));
    return true;
  }
  return false;
}

async function bridgeLooksAlive() {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 1000);
  try {
    const res = await fetch(HTTP_HEALTH_URL, {
      method: "GET",
      cache: "no-store",
      signal: controller.signal,
    });
    return res.ok;
  } catch {
    return false;
  } finally {
    clearTimeout(timer);
  }
}

async function findChatGptTab() {
  const tabs = await chrome.tabs.query({ url: CHATGPT_URL_PATTERN });
  const candidates = tabs.filter((tab) => tab.id && isChatGptUrl(tab.url));
  if (!candidates.length) return null;

  // Prefer the tab the user is actually looking at, otherwise use the first
  // ChatGPT tab in the persistent debug profile.
  return candidates.find((tab) => tab.active) || candidates[0];
}

async function connectWs() {
  if (connecting || (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING))) {
    return;
  }

  connecting = true;

  // Avoid creating a WebSocket while the Python bridge is down. Chrome records
  // refused WebSocket handshakes as extension errors even if onerror is handled,
  // so preflight with fetch() and quietly retry later.
  if (!(await bridgeLooksAlive())) {
    connecting = false;
    scheduleReconnect();
    return;
  }

  try {
    ws = new WebSocket(WS_URL);
  } catch (err) {
    connecting = false;
    scheduleReconnect();
    return;
  }

  ws.onopen = () => {
    connecting = false;
    reconnectDelay = 2000;
    injectOpenChatGptTabs("ws-open");
    publishWatchdogStatus();
    requestModelCatalog("ws-open").catch((err) => {
      console.warn("[ChatGPT Bridge] model catalog refresh failed", err?.message || err);
    });
  };

  ws.onmessage = (event) => {
    let data;
    try {
      data = JSON.parse(event.data);
    } catch {
      return;
    }

    if (data.type === "prompt" && data.id) {
      handleBridgePrompt(data);
    } else if (data.type === "list_models") {
      requestModelCatalog(data.reason || "manual", data.id || null).catch((err) => {
        sendWs({ type: "model_catalog_error", id: data.id || null, error: err?.message || String(err) });
      });
    } else if (data.type === "reload") {
      handleBridgeReload();
    }
  };

  ws.onclose = () => {
    connecting = false;
    ws = null;
    scheduleReconnect();
  };

  ws.onerror = () => {
    // onclose performs reconnect scheduling. Keep this quiet; the bridge may
    // legitimately be down between sessions.
  };
}

function scheduleReconnect() {
  if (reconnectTimer) return;
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    reconnectDelay = Math.min(reconnectDelay * 2, 30000);
    connectWs();
  }, reconnectDelay);
}

// ── Periodic health check ───────────────────────────────────────────────

/**
 * Every PERIODIC_INJECT_INTERVAL_MS, check all ChatGPT tabs and (re)inject content.js
 * if no live content-script port is attached. This catches:
 *  - Restored tabs missed by tabs.onUpdated
 *  - Content scripts that crashed without closing their port
 *  - Tabs whose port aged out due to a service-worker restart
 */
async function periodicInjectCheck() {
  const reason = "periodic";
  try {
    const tabs = await chrome.tabs.query({ url: CHATGPT_URL_PATTERN });
    const chatGptTabs = tabs.filter((tab) => tab.id && isChatGptUrl(tab.url));

    if (chatGptTabs.length === 0) return;

    for (const tab of chatGptTabs) {
      if (connectedTabs.has(tab.id)) {
        // Content script is live — skip.
        continue;
      }
      await injectIfMissing(tab, reason);
    }

    // If periodic injection reconnected a stale port, retry any pending WS messages.
    if (ws && ws.readyState === WebSocket.OPEN) {
      // ws messages are handled asynchronously; the socket is already alive.
    }
  } catch (err) {
    console.warn("[ChatGPT Bridge] periodicInjectCheck error", err?.message || err);
  }
}

/**
 * Start the periodic timer. Returns it so unit tests can clear it.
 * The timer is NOT tied to a specific DOM element; it lives in the service worker.
 */
let _periodicTimer = null;

function startPeriodicTimer() {
  if (_periodicTimer) return; // idempotent
  _periodicTimer = setInterval(periodicInjectCheck, PERIODIC_INJECT_INTERVAL_MS);
  console.log(`[ChatGPT Bridge] periodic inject timer started (every ${PERIODIC_INJECT_INTERVAL_MS / 1000}s)`);
}

// ── Message listeners ───────────────────────────────────────────────────

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  // ── Streaming delta + done: content script → background → bridge WS ──────
  // Content script sends {action: "delta", id, content} during response
  // polling and {action: "done", id} when generation finishes.
  // We relay both to the Python bridge host over WebSocket.
  if (message.action === "delta") {
    sendWs({ type: "delta", id: message.id, content: message.content || "" });
    sendResponse({ received: true });
    return false;
  }

  if (message.action === "done") {
    sendWs({ type: "done", id: message.id });
    sendResponse({ received: true });
    return false;
  }

  if (message.action === "poll") {
    sendWs({ type: "poll", id: message.id, poll: message.poll || {} });
    sendResponse({ received: true });
    return false;
  }

  if (message.action === "ping") {
    connectWs();
    sendResponse({ success: true, wsConnected: ws?.readyState === WebSocket.OPEN });
    return false;
  }
  return false;
});

// chrome.runtime.onStartup fires ONLY on fresh service-worker startup
// (not on install or runtime message wake-ups).  The top-level code below
// handles the other cases (install, message events). Both paths are needed
// for full coverage.

function onServiceWorkerStartup() {
  connectWs();
  injectOpenChatGptTabs("startup");
  startPeriodicTimer();
  // Clear stale watchdog state (service worker restarted from scratch)
  for (const [, entry] of tabWatchdog) {
    if (entry.timer) clearTimeout(entry.timer);
  }
  tabWatchdog.clear();
  lastExtensionReload = 0;
  watchdogLog = [];
  logWatchdog("service-worker-startup", null, "watchdog state reset");
}

chrome.runtime.onStartup.addListener(onServiceWorkerStartup);
chrome.runtime.onInstalled.addListener(onServiceWorkerStartup);

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}
