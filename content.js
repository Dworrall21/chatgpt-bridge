// content.js — ChatGPT Bridge Content Script
// DOM interaction only. WebSocket lives in background.js (avoids page CSP).
// Communicates with background via chrome.runtime ports and sendMessage.
// Supports streaming: sends intermediate text deltas via chrome.runtime.sendMessage.

(function () {
  if (window.__chatgptBridgeLoaded) return;
  window.__chatgptBridgeLoaded = true;

  console.log("[ChatGPT Bridge] Content script loaded on", location.host);

  // ── Port-based alive signaling ────────────────────────────────────────────
  // Open a persistent port so background.js can track this tab via
  // chrome.runtime.onConnect. Do NOT use sendMessage for health alone —
  // messages are dropped if the service worker is asleep.
  let port = null;
  try {
    port = chrome.runtime.connect({ name: "chatgpt-bridge-content-script" });
    console.log("[ChatGPT Bridge] alive port opened");
  } catch (_) {
    console.warn("[ChatGPT Bridge] could not open alive port — injection may be unreliable");
  }

  if (port) {
    port.onDisconnect.addListener(() => {
      console.warn(
        "[ChatGPT Bridge] alive port disconnected — background will re-inject on next cycle"
      );
    });
    port.onMessage.addListener((msg) => {
      if (msg.action === "pong") {
        // Background acknowledged our port; nothing to do.
      }
    });
  }

  // Keep the MV3 service worker warm enough to maintain the bridge WebSocket.
  // ChatGPT's CSP forces the WebSocket into background.js, but the local bridge
  // cannot wake a sleeping service worker by itself. A lightweight content-script
  // ping wakes background.js and makes it reconnect when needed.
  setInterval(() => {
    try {
      chrome.runtime.sendMessage({ action: "ping" }, () => void chrome.runtime.lastError);
    } catch (_) {}
  }, 20000);

  // ── Message relay to background.js ─────────────────────────────────────
  chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    if (msg.action === "prompt") {
      handlePrompt(
        msg.prompt,
        msg.timeout,
        msg.attempt || 0,
        msg.conversation_id || null,
        msg.model_search || null,
        msg.stream || false,
        msg.id || "",
        !!msg.debug,
      )
        .then((result) => sendResponse({
          success: true,
          text: result.text,
          conversation_id: extractConversationId(),
          conversation_title: extractConversationTitle(),
          _debug: result._debug || null,
        }))
        .catch(err => sendResponse({ success: false, error: err.message }));
      return true;
    }
    if (msg.action === "enumerate_models") {
      enumerateAvailableModels()
        .then(models => sendResponse({ success: true, models }))
        .catch(err => sendResponse({ success: false, error: err.message }));
      return true;
    }
    if (msg.action === "ping") {
      sendResponse({ success: true });
      return false;
    }
  });

  // ── Model Selection / Model Catalog ───────────────────────────────────
  // Opens ChatGPT's model picker, enumerates menu items, and selects the best
  // match for a search term. The same picker scan is reused for model catalog
  // enumeration when the bridge asks for available models.

  const MODEL_SKIP_LABELS = ['use thinking', 'search the web'];
  const MODEL_PICKER_SELECTORS = [
    'button[aria-label="Switch model"]',
    'button[title="Switch model"]',
    'button[aria-label*="model"]',
    'button[title*="model"]',
    '[role="button"][aria-label*="model"]',
  ];

  function normalizeModelText(text) {
    return String(text || '')
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, ' ')
      .replace(/\s+/g, ' ')
      .trim();
  }

  function findSwitchModelButton() {
    const candidates = [];
    for (const selector of MODEL_PICKER_SELECTORS) {
      for (const el of document.querySelectorAll(selector)) {
        candidates.push(el);
      }
    }
    for (const el of candidates) {
      const label = [el.getAttribute('aria-label'), el.getAttribute('title'), el.textContent]
        .filter(Boolean)
        .join(' ')
        .trim();
      const norm = normalizeModelText(label);
      if (!norm) continue;
      if (norm.includes('switch model') || (norm.includes('model') && norm.includes('switch'))) {
        return el;
      }
    }
    return null;
  }

  async function waitForSwitchModelButton(timeoutMs = 8000) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      const button = findSwitchModelButton();
      if (button) return button;
      await sleep(250);
    }
    return null;
  }

  function collectMenuItems(menuEl) {
    if (!menuEl) return [];
    const nodes = menuEl.querySelectorAll('[role="menuitem"], [role="menuitemradio"], [role="option"]');
    const labels = [];
    const seen = new Set();
    for (const item of nodes) {
      const label = (item.textContent || '').replace(/\s+/g, ' ').trim();
      if (!label) continue;
      const norm = normalizeModelText(label);
      if (!norm) continue;
      if (MODEL_SKIP_LABELS.some(skip => norm.includes(skip))) continue;
      if (seen.has(norm)) continue;
      seen.add(norm);
      labels.push(label);
    }
    return labels;
  }

  function scoreModelMatch(searchTerm, label) {
    const search = normalizeModelText(searchTerm);
    const candidate = normalizeModelText(label);
    if (!search || !candidate) return null;
    if (candidate === search) return [0, candidate.length, label];
    if (candidate.startsWith(search)) return [1, candidate.length, label];
    if (candidate.includes(search)) return [2, candidate.length, label];
    const tokens = search.split(' ').filter(Boolean);
    if (tokens.length && tokens.every(tok => candidate.includes(tok))) return [3, candidate.length, label];
    return null;
  }

  async function openModelPicker() {
    const switchBtn = await waitForSwitchModelButton();
    if (!switchBtn) {
      console.warn('[ChatGPT Bridge] model picker: no Switch model button found');
      return null;
    }

    switchBtn.scrollIntoView({ block: 'center' });
    switchBtn.focus();
    await sleep(150);

    // Some ChatGPT builds only react to a real-looking pointer sequence.
    switchBtn.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, cancelable: true, pointerId: 1, pointerType: 'mouse', isPrimary: true }));
    switchBtn.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, button: 0 }));
    switchBtn.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, button: 0 }));
    switchBtn.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, button: 0 }));
    if (typeof switchBtn.click === 'function') {
      switchBtn.click();
    }

    const deadline = Date.now() + 5000;
    while (Date.now() < deadline) {
      const menuEl = document.querySelector('[role="menu"], [role="listbox"], [role="dialog"]');
      if (menuEl) return menuEl;
      await sleep(200);
    }

    console.warn('[ChatGPT Bridge] model picker: menu did not open');
    return null;
  }

  async function closeModelPicker() {
    try {
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    } catch (_) {}
    await sleep(150);
  }

  async function enumerateAvailableModels() {
    try {
      const menuEl = await openModelPicker();
      if (!menuEl) return [];
      const labels = collectMenuItems(menuEl);
      await closeModelPicker();
      return labels;
    } catch (err) {
      console.warn('[ChatGPT Bridge] enumerateAvailableModels error:', err.message || err);
      await closeModelPicker();
      return [];
    }
  }

  async function selectModel(searchTerm) {
    if (!searchTerm || typeof searchTerm !== 'string') return;
    searchTerm = searchTerm.trim();
    if (!searchTerm) return;

    try {
      const menuEl = await openModelPicker();
      if (!menuEl) return;

      const searchInput = menuEl.querySelector('input');
      if (searchInput) {
        searchInput.focus();
        const nativeInputValueSetter = Object.getOwnPropertyDescriptor(
          window.HTMLInputElement.prototype, 'value'
        ).set;
        nativeInputValueSetter.call(searchInput, searchTerm);
        searchInput.dispatchEvent(new Event('input', { bubbles: true }));
        searchInput.dispatchEvent(new Event('change', { bubbles: true }));
        await sleep(600);
      }

      const labels = collectMenuItems(menuEl);
      const ranked = labels
        .map((label, idx) => ({ label, idx, score: scoreModelMatch(searchTerm, label) }))
        .filter(item => item.score)
        .sort((a, b) => {
          const sa = a.score;
          const sb = b.score;
          for (let i = 0; i < sa.length; i++) {
            if (sa[i] < sb[i]) return -1;
            if (sa[i] > sb[i]) return 1;
          }
          return a.idx - b.idx;
        });

      const targetLabel = ranked.length ? ranked[0].label : null;
      if (!targetLabel) {
        console.warn(`[ChatGPT Bridge] selectModel: no matching model for "${searchTerm}"`);
        await closeModelPicker();
        return;
      }

      const targetItem = Array.from(menuEl.querySelectorAll('[role="menuitem"], [role="menuitemradio"], [role="option"]'))
        .find(item => (item.textContent || '').replace(/\s+/g, ' ').trim() === targetLabel);
      if (targetItem) {
        targetItem.scrollIntoView({ block: 'nearest' });
        await sleep(100);
        targetItem.click();
        console.log(`[ChatGPT Bridge] selectModel: selected "${targetLabel}" for search "${searchTerm}"`);
        await sleep(300);
      } else {
        console.warn(`[ChatGPT Bridge] selectModel: target "${targetLabel}" disappeared before click`);
        await closeModelPicker();
      }
    } catch (err) {
      console.warn('[ChatGPT Bridge] selectModel error:', err.message || err);
      await closeModelPicker();
    }
  }

  // ── DOM Interaction ─────────────────────────────────────────────────────

  function findInput() {
    const selectors = [
      '#prompt-textarea',
      'div[contenteditable="true"][role="textbox"]',
      'div.ProseMirror[contenteditable]',
      'textarea[placeholder*="Send a message"]',
    ];
    for (const sel of selectors) {
      const el = document.querySelector(sel);
      if (el) return el;
    }
    return null;
  }

  function findSendButton() {
    const selectors = [
      '#composer-submit-button',
      'button[data-testid="send-button"]',
      'button[aria-label="Send prompt"]',
      'button[aria-label*="Send"]',
    ];
    for (const sel of selectors) {
      const el = document.querySelector(sel);
      if (el && !el.disabled) return el;
    }
    return null;
  }

  // ── New-chat navigation ──────────────────────────────────────────────────

  function isConversationPage() {
    const path = location.pathname || '';
    return path.includes('/c/') || path.includes('/chat/');
  }

  async function navigateToNewChat() {
    // Try the "New chat" link first, then the button, then fall back to direct nav.
    const newChatLink = document.querySelector('a[href="/"]');
    const newChatBtn  = document.querySelector('button[aria-label="New chat"]');
    if (newChatLink) {
      newChatLink.click();
    } else if (newChatBtn) {
      newChatBtn.click();
    } else {
      location.href = '/';
    }

    // Poll for the textarea to appear (up to 10 s)
    for (let i = 0; i < 10; i++) {
      await sleep(1000);
      if (document.querySelector('#prompt-textarea')) return;
    }
    throw new Error('ChatGPT input box did not appear after navigation');
  }

  // ── Conversation metadata ──────────────────────────────────────────────

  function extractConversationId() {
    const match = location.pathname.match(/\/c\/([a-f0-9-]+)/);
    return match ? match[1] : null;
  }

  function extractConversationTitle() {
    // Try the <title> tag first, fall back to the sidebar link text
    const title = document.title;
    if (title && !title.includes('ChatGPT')) return title.replace(/ ?[-–] ChatGPT$/i, '').trim();
    const sidebarItem = document.querySelector('a[href="' + location.pathname + '"]');
    if (sidebarItem) return sidebarItem.textContent.trim();
    return null;
  }

  async function handlePrompt(prompt, timeoutSeconds = 10, attempt = 0, conversationId = null, modelSearch = null, stream = false, requestId = "", debug = false) {
    const timeout = Number(timeoutSeconds || 10);

    for (let loop = attempt; loop < 2; loop++) {
      // Navigate to a fresh chat only when no conversation_id was given and we're on an old sheet.
      if (!conversationId && isConversationPage()) {
        await navigateToNewChat();
      }

      // If we have a conversation_id, navigate only when we're not already on it.
      // Staying on the page avoids a full reload — the content script stays loaded,
      // and we type directly into whatever conversation DOM is currently visible.
      if (conversationId) {
        const alreadyOnTarget = (extractConversationId() === conversationId);
        if (!alreadyOnTarget) {
          try {
            location.href = `/c/${conversationId}`;
            for (let i = 0; i < 10; i++) {
              await sleep(1000);
              if (document.querySelector('#prompt-textarea')) break;
            }
          } catch (_) { /* navigate best-effort */ }
        }
      }

      // ── Model selection ──────────────────────────────────────────────
      // If model_search is specified, select the model before typing.
      // selectModel silently falls back if the button/menu isn't available.
      if (modelSearch) {
        await selectModel(modelSearch);
      }

      const input = findInput();
      if (!input) throw new Error("Could not find ChatGPT input box");

      // Count existing assistant messages so we can ignore them during polling
      const assistantCountBefore = document.querySelectorAll('[data-message-author-role="assistant"]').length;

      // Clear and set input. ChatGPT uses ProseMirror; direct innerHTML/textContent
      // changes make the text visible but do not update ProseMirror state, so the
      // send button never appears. execCommand('insertText') routes through the
      // editor's real input pipeline.
      input.focus();
      if (input.tagName === "TEXTAREA" || input.tagName === "INPUT") {
        const setter = Object.getOwnPropertyDescriptor(
          window[input.tagName === "TEXTAREA" ? "HTMLTextAreaElement" : "HTMLInputElement"].prototype,
          "value"
        ).set;
        setter.call(input, "");
        input.dispatchEvent(new Event("input", { bubbles: true }));
        setter.call(input, prompt);
        input.dispatchEvent(new Event("input", { bubbles: true }));
      } else {
        document.execCommand("selectAll", false, null);
        document.execCommand("delete", false, null);
        document.execCommand("insertText", false, prompt);
      }
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new Event("change", { bubbles: true }));
      await sleep(500);

      // Send
      const sendBtn = findSendButton();
      if (sendBtn) {
        sendBtn.click();
      } else {
        input.dispatchEvent(new KeyboardEvent("keydown", {
          key: "Enter", keyCode: 13, bubbles: true, cancelable: true
        }));
      }

      try {
        const result = await waitForResponse(timeout, assistantCountBefore, loop, { stream, requestId, debug });
        if (stream && requestId) {
          sendDone(requestId);
        }
        return result;
      } catch (err) {
        const msg = err?.message || String(err);
        if (!/empty after 10s|did not generate a response/i.test(msg) || loop === 1) {
          throw err;
        }
        // Retry once on structurally empty response containers.
        const stopBtn = document.querySelector('[data-testid="stop-button"]');
        if (stopBtn) stopBtn.click();
        await sleep(1000);
      }
    }

    throw new Error("ChatGPT did not generate a response after 2 attempts");
  }

  // ── Streaming delta sender ────────────────────────────────────────────
  // Sends intermediate text to background.js via chrome.runtime.sendMessage.
  // Background relays these to the Python bridge as WS delta messages.

  let _lastDeltaText = "";

  function sendDelta(requestId, content) {
    if (!requestId || content === _lastDeltaText) return;
    _lastDeltaText = content;
    try {
      chrome.runtime.sendMessage({
        action: "delta",
        id: requestId,
        content: content,
      }, () => void chrome.runtime.lastError);
    } catch (_) {
      // Port may be closed during page unload; ignore.
    }
  }

  // ── Streaming done sentinel ────────────────────────────────────────────
  // Tells the bridge that streaming is complete so the SSE connection can close.

  function sendDone(requestId) {
    if (!requestId) return;
    try {
      chrome.runtime.sendMessage({
        action: "done",
        id: requestId,
      }, () => void chrome.runtime.lastError);
    } catch (_) {
      // Port may be closed during page unload; ignore.
    }
  }

  // ── Response extraction ─────────────────────────────────────────────────

  function extractLatestResponse() {
    // Scan assistant messages backward for .markdown content
    const assistants = document.querySelectorAll('[data-message-author-role="assistant"]');
    for (let i = assistants.length - 1; i >= 0; i--) {
      const md = assistants[i].querySelector('.markdown');
      if (md) {
        const text = md.textContent.trim();
        if (text.length > 3) return text;
      }
    }
    return null;
  }

  function isGenerating() {
    return !!document.querySelector('[data-testid="stop-button"], [class*="result-streaming"]');
  }

  function isNoise(text) {
    return [/ChatGPT can make mistakes/i, /ChatGPT may produce/i].some(p => p.test(text));
  }

  async function waitForResponse(timeoutSeconds = 10, assistantCountBefore = 0, attempt = 0, options = {}) {
    const { stream = false, requestId = "", debug = false } = options;
    const maxWait = Math.min(Math.max(timeoutSeconds, 1), 600) * 1000;
    const pollMs = stream ? 200 : 500;   // Faster polling when streaming for lower latency
    const startTs = performance.now();
    let lastPollAt = startTs;
    let elapsed = 0, lastText = "", stableCount = 0;
    let lastDeltaSent = "";  // Track last text sent as delta to avoid duplicates
    let pollCount = 0;
    const pollIntervalsMs = [];
    const EMPTY_CHECK_MS = 10_000;
    let emptyChecked = false;

    // Reset delta tracker for this request
    if (stream) {
      _lastDeltaText = "";
    }

    while (elapsed < maxWait) {
      await sleep(pollMs);
      const now = performance.now();
      const intervalMs = Math.max(0, Math.round(now - lastPollAt));
      lastPollAt = now;
      elapsed = Math.round(now - startTs);
      pollCount += 1;
      pollIntervalsMs.push(intervalMs);

      const assistants = document.querySelectorAll('[data-message-author-role="assistant"]');
      const text = extractLatestResponse();

      // Only accept responses from NEW assistant elements created after we sent the prompt
      const newAssistantArrived = assistants.length > assistantCountBefore;
      const textChanged = text !== lastText;

      if (debug && requestId) {
        try {
          chrome.runtime.sendMessage({
            action: "poll",
            id: requestId,
            poll: {
              interval_ms: intervalMs,
              poll_index: pollCount,
              text_changed: textChanged,
              assistant_count: assistants.length,
              generating: isGenerating(),
              text_length: text ? text.length : 0,
              stable_count: stableCount,
            },
          }, () => void chrome.runtime.lastError);
        } catch (_) {
          // Debug logging is best-effort only.
        }
      }

      if (text && text.length > 3 && newAssistantArrived && !isNoise(text)) {
        // Stream intermediate text to background.js
        if (stream && text !== lastDeltaSent && requestId) {
          sendDelta(requestId, text);
          lastDeltaSent = text;
        }

        if (text === lastText) {
          stableCount++;
          if (stableCount >= 3) {
            return {
              text,
              _debug: debug ? {
                elapsed_ms: Math.round(performance.now() - startTs),
                ws_send_ms: null,
                poll_count: pollCount,
                poll_intervals_ms: pollIntervalsMs,
              } : null,
            };
          }
        } else {
          stableCount = 0;
          lastText = text;
        }
      }
      if (!isGenerating() && lastText.length > 3 && newAssistantArrived) {
        // Generation done — send final delta if text changed
        if (stream && lastText !== lastDeltaSent && requestId) {
          sendDelta(requestId, lastText);
          lastDeltaSent = lastText;
        }
        stableCount++;
        if (stableCount >= 3) {
          return {
            text: lastText,
            _debug: debug ? {
              elapsed_ms: Math.round(performance.now() - startTs),
              ws_send_ms: null,
              poll_count: pollCount,
              poll_intervals_ms: pollIntervalsMs,
            } : null,
          };
        }
      }

      // ── Empty container detection (10 s mark) ────────────────────────────
      if (!emptyChecked && elapsed >= EMPTY_CHECK_MS) {
        emptyChecked = true;
        if (isGenerating() && !lastText) {
          throw new Error(
            "ChatGPT response container is empty after 10s — retrying"
          );
        }
      }
    }
    if (lastText) {
      return {
        text: lastText + "\n\n[WARNING: timed out]",
        _debug: debug ? {
          elapsed_ms: Math.round(performance.now() - startTs),
          ws_send_ms: null,
          poll_count: pollCount,
          poll_intervals_ms: pollIntervalsMs,
        } : null,
      };
    }
    throw new Error("ChatGPT did not generate a response after 2 attempts");
  }

  function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
})();