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
      handlePrompt(msg.prompt, msg.timeout, msg.attempt || 0, msg.conversation_id || null, msg.model_search || null, msg.stream || false, msg.id || "")
        .then(text => sendResponse({
          success: true,
          text,
          conversation_id: extractConversationId(),
          conversation_title: extractConversationTitle(),
        }))
        .catch(err => sendResponse({ success: false, error: err.message }));
      return true;
    }
    if (msg.action === "ping") {
      sendResponse({ success: true });
      return false;
    }
  });

  // ── Model Selection ───────────────────────────────────────────────────
  // Opens ChatGPT's model picker (Radix menu), searches for a model, and
  // clicks the first matching item that isn't "Use Thinking" or "Search the web".
  // Silent fallback — if anything goes wrong, we just stay on the current model.

  const MODEL_SWITCH_BTN = 'button[aria-label="Switch model"]';

  async function selectModel(searchTerm) {
    if (!searchTerm || typeof searchTerm !== 'string') return;
    searchTerm = searchTerm.trim();
    if (!searchTerm) return;

    try {
      // 1. Find and click the "Switch model" button
      const switchBtn = document.querySelector(MODEL_SWITCH_BTN);
      if (!switchBtn) {
        console.warn('[ChatGPT Bridge] selectModel: no Switch model button found');
        return;
      }
      switchBtn.scrollIntoView({ block: 'center' });
      await sleep(100);
      switchBtn.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }));
      switchBtn.click();
      await sleep(600);

      // 2. Find the search input inside the opened menu
      const menuEl = document.querySelector('[role="menu"]');
      if (!menuEl) {
        console.warn('[ChatGPT Bridge] selectModel: menu did not open');
        return;
      }
      const searchInput = menuEl.querySelector('input');
      if (!searchInput) {
        console.warn('[ChatGPT Bridge] selectModel: no search input in menu');
        // Close the menu by pressing Escape
        document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
        return;
      }

      // 3. Type the search term using the native value setter (handles React)
      searchInput.focus();
      const nativeInputValueSetter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype, 'value'
      ).set;
      nativeInputValueSetter.call(searchInput, searchTerm);
      searchInput.dispatchEvent(new Event('input', { bubbles: true }));
      searchInput.dispatchEvent(new Event('change', { bubbles: true }));
      await sleep(500);

      // 4. Find first valid menuitem — skip "Use Thinking" and "Search the web"
      const items = menuEl.querySelectorAll('[role="menuitem"]');
      const skipLabels = ['use thinking', 'search the web'];
      let target = null;
      for (const item of items) {
        const label = (item.textContent || '').trim().toLowerCase();
        if (skipLabels.some(skip => label.includes(skip))) continue;
        target = item;
        break;
      }

      if (target) {
        target.scrollIntoView({ block: 'nearest' });
        await sleep(100);
        target.click();
        console.log(`[ChatGPT Bridge] selectModel: selected "${target.textContent.trim()}" for search "${searchTerm}"`);
        await sleep(300);
      } else {
        console.warn(`[ChatGPT Bridge] selectModel: no matching model for "${searchTerm}", staying on current model`);
        // Close the menu
        document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
        await sleep(200);
      }
    } catch (err) {
      console.warn('[ChatGPT Bridge] selectModel error:', err.message || err);
      // Ensure any open menu is closed
      try { document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); } catch (_) {}
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

  async function handlePrompt(prompt, timeoutSeconds = 10, attempt = 0, conversationId = null, modelSearch = null, stream = false, requestId = "") {
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
        const text = await waitForResponse(timeout, assistantCountBefore, loop, { stream, requestId });
        if (stream && requestId) {
          sendDone(requestId);
        }
        return text;
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
    const { stream = false, requestId = "" } = options;
    const maxWait = Math.min(Math.max(timeoutSeconds, 1), 600) * 1000;
    const pollMs = stream ? 200 : 500;   // Faster polling when streaming for lower latency
    let elapsed = 0, lastText = "", stableCount = 0;
    let lastDeltaSent = "";  // Track last text sent as delta to avoid duplicates
    const EMPTY_CHECK_MS = 10_000;
    let emptyChecked = false;

    // Reset delta tracker for this request
    if (stream) {
      _lastDeltaText = "";
    }

    while (elapsed < maxWait) {
      await sleep(pollMs);
      elapsed += pollMs;
      const assistants = document.querySelectorAll('[data-message-author-role="assistant"]');
      const text = extractLatestResponse();

      // Only accept responses from NEW assistant elements created after we sent the prompt
      const newAssistantArrived = assistants.length > assistantCountBefore;

      if (text && text.length > 3 && newAssistantArrived && !isNoise(text)) {
        // Stream intermediate text to background.js
        if (stream && text !== lastDeltaSent && requestId) {
          sendDelta(requestId, text);
          lastDeltaSent = text;
        }

        if (text === lastText) {
          stableCount++;
          if (stableCount >= 3) return text;
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
        if (stableCount >= 3) return lastText;
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
    if (lastText) return lastText + "\n\n[WARNING: timed out]";
    throw new Error("ChatGPT did not generate a response after 2 attempts");
  }

  function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
})();