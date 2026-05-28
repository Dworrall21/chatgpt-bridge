// content.js — ChatGPT Bridge Content Script
// DOM interaction only. WebSocket lives in background.js (avoids page CSP).
// Communicates with background via chrome.runtime ports and sendMessage.
// Supports streaming: sends intermediate text deltas via chrome.runtime.sendMessage.

(function () {
  if (window.__chatgptBridgeLoaded && window.__chatgptBridgePortAlive) return;
  window.__chatgptBridgeLoaded = true;

  console.log("[ChatGPT Bridge] Content script loaded on", location.host);

  // ── Port-based alive signaling ────────────────────────────────────────────
  // Open a persistent port so background.js can track this tab via
  // chrome.runtime.onConnect. Do NOT use sendMessage for health alone —
  // messages are dropped if the service worker is asleep.
  let port = null;
  try {
    port = chrome.runtime.connect({ name: "chatgpt-bridge-content-script" });
    window.__chatgptBridgePortAlive = true;
    console.log("[ChatGPT Bridge] alive port opened");
  } catch (_) {
    console.warn("[ChatGPT Bridge] could not open alive port — injection may be unreliable");
    window.__chatgptBridgePortAlive = false;
  }

  if (port) {
    port.onDisconnect.addListener(() => {
      window.__chatgptBridgePortAlive = false;
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

  // ── Duplicate send guard ──────────────────────────────────────────────────
  // Track when the last prompt was sent. If a duplicate arrives within the
  // cooldown window, reject it. The cooldown is 10s normally, but extends
  // to 120s if ChatGPT's thinking indicator is visible (stop-button or
  // result-streaming class), since thinking models take much longer.
  let lastPromptSentAt = 0;
  const DUPLICATE_COOLDOWN_MS = 10_000;       // 10s between messages
  const DUPLICATE_COOLDOWN_THINKING_MS = 120_000;  // 120s if thinking

  function getDuplicateCooldown() {
    // If ChatGPT is still thinking/generating, use the longer cooldown
    const isThinking = !!document.querySelector(
      '[data-testid="stop-button"], [class*="result-streaming"]'
    );
    return isThinking ? DUPLICATE_COOLDOWN_THINKING_MS : DUPLICATE_COOLDOWN_MS;
  }

  function isDuplicatePrompt() {
    const now = Date.now();
    const elapsed = now - lastPromptSentAt;
    const cooldown = getDuplicateCooldown();
    if (elapsed < cooldown) {
      console.warn(
        `[ChatGPT Bridge] rejecting duplicate prompt — ${elapsed}ms since last ` +
        `(cooldown: ${cooldown}ms, thinking: ${cooldown > DUPLICATE_COOLDOWN_MS})`
      );
      return true;
    }
    return false;
  }

  // ── Message relay to background.js ─────────────────────────────────────
  chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    if (msg.action === "prompt") {
      // Reject duplicate prompts within the cooldown window
      if (isDuplicatePrompt()) {
        sendResponse({ success: false, error: "Duplicate prompt — cooldown active" });
        return false;
      }
      lastPromptSentAt = Date.now();
      handlePrompt(
        msg.prompt,
        msg.timeout,
        msg.attempt || 0,
        msg.conversation_id || null,
        msg.model_search || null,
        msg.stream || false,
        msg.id || "",
        !!msg.debug,
        !!msg.new_conversation,
      )
        .then((result) => {
          sendResponse({
            success: true,
            text: result.text,
            conversation_id: extractConversationId(),
            conversation_title: extractConversationTitle(),
            _debug: result._debug || null,
          });
        })
        .catch(err => {
          sendResponse({ success: false, error: err.message });
        });
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
  // Ordered by specificity — most specific first. Covers multiple ChatGPT DOM versions.
  const MODEL_PICKER_SELECTORS = [
    // Current ChatGPT (2025-2026)
    'button[aria-label="Model selector"]',
    'button[aria-label*="Model selector"]',
    // Older ChatGPT builds
    'button[aria-label="Switch model"]',
    'button[title="Switch model"]',
    // Fallback: any button whose aria-label or title contains "model" + "switch"/"selector"
    'button[aria-label*="odel"][aria-label*="elect"]',
    'button[title*="odel"][title*="elect"]',
    // Broad fallback (last resort — may match non-model buttons)
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
    // Prefer exact/narrow matches first
    for (const el of candidates) {
      const label = [el.getAttribute('aria-label'), el.getAttribute('title'), el.textContent]
        .filter(Boolean)
        .join(' ')
        .trim();
      const norm = normalizeModelText(label);
      if (!norm) continue;
      // High-confidence: contains both "model" and "selector" or "switch"
      if (norm.includes('switch model') || norm.includes('model selector') ||
          norm.includes('select model') || norm.includes('choose model') ||
          norm.includes('change model') || norm.includes('pick model')) {
        return el;
      }
    }
    // Fallback: any model-related button
    for (const el of candidates) {
      const label = [el.getAttribute('aria-label'), el.getAttribute('title'), el.textContent]
        .filter(Boolean)
        .join(' ')
        .trim();
      const norm = normalizeModelText(label);
      if (!norm) continue;
      if (norm.includes('model') && norm.length < 60) {
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
    await sleep(200);

    // Some ChatGPT builds only react to a real-looking pointer sequence.
    switchBtn.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, cancelable: true, pointerId: 1, pointerType: 'mouse', isPrimary: true }));
    switchBtn.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, button: 0 }));
    switchBtn.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, button: 0 }));
    switchBtn.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, button: 0 }));
    if (typeof switchBtn.click === 'function') {
      switchBtn.click();
    }

    // Wait for menu to appear — try multiple times with increasing delays
    // ChatGPT sometimes needs a moment to render the menu after the click
    let menuEl = null;
    for (let attempt = 0; attempt < 3; attempt++) {
      const deadline = Date.now() + 3000;
      while (Date.now() < deadline) {
        menuEl = document.querySelector('[role="menu"], [role="listbox"], [role="dialog"]');
        if (menuEl) break;
        await sleep(200);
      }
      if (menuEl) break;
      // Menu didn't open — try clicking the button again
      if (typeof switchBtn.click === 'function') {
        switchBtn.click();
      }
      await sleep(500);
    }

    if (!menuEl) {
      console.warn('[ChatGPT Bridge] model picker: menu did not open');
      return null;
    }

    // Check if menu only shows login/signup (unauthenticated session)
    const menuText = menuEl.textContent || '';
    if (menuText.includes('Log in') && menuText.includes('Sign up') && !menuText.includes('GPT')) {
      console.warn('[ChatGPT Bridge] model picker: menu shows login prompt — session not authenticated');
      return null;
    }

    return menuEl;
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

  // Track whether model selection has already been attempted on this page load
  // to prevent rapid retry loops that trigger ChatGPT rate limiting
  let _modelSelectionAttempted = false;
  let _modelSelectionSuccess = false;

  async function selectModel(searchTerm) {
    if (!searchTerm || typeof searchTerm !== 'string') return;
    searchTerm = searchTerm.trim();
    if (!searchTerm) return;

    // If we already successfully selected a model on this page load, skip
    if (_modelSelectionSuccess) {
      return;
    }

    // If we already failed once on this page load, don't retry (prevents 429 loops)
    if (_modelSelectionAttempted) {
      console.warn('[ChatGPT Bridge] selectModel: skipping retry — already attempted on this page');
      return;
    }

    _modelSelectionAttempted = true;

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
      if (!labels.length) {
        console.warn('[ChatGPT Bridge] selectModel: no model items found in menu');
        await closeModelPicker();
        return;
      }

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
        _modelSelectionSuccess = true;
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

  // ── Typing simulation ────────────────────────────────────────────────────
  // 500 WPM ≈ 42 chars/sec ≈ 24ms per character.
  // ChatGPT's ProseMirror editor expects real keystrokes; instant insertText
  // can confuse the send button state. Typing character-by-character with
  // small delays ensures the editor properly tracks input state.
  const TYPING_DELAY_MS = 24; // 500 WPM

  async function typeText(text, input) {
    for (let i = 0; i < text.length; i++) {
      const char = text[i];
      // Dispatch a real keyboard event for each character
      input.dispatchEvent(new KeyboardEvent('keydown', {
        key: char, bubbles: true, cancelable: true
      }));
      input.dispatchEvent(new KeyboardEvent('keypress', {
        key: char, charCode: char.charCodeAt(0), bubbles: true, cancelable: true
      }));
      document.execCommand('insertText', false, char);
      input.dispatchEvent(new KeyboardEvent('keyup', {
        key: char, bubbles: true, cancelable: true
      }));
      input.dispatchEvent(new Event('input', { bubbles: true }));
      await sleep(TYPING_DELAY_MS);
    }
  }

  // ── Send with retry ──────────────────────────────────────────────────────
  // Wait for the send button to become available, with retries.
  // Verifies the message was actually sent by checking for a new user message.
  // Returns true if sent successfully, false otherwise.
  async function sendWithRetry(input, maxRetries = 5, retryDelayMs = 1000) {
    // Count user messages before sending
    const userMessagesBefore = document.querySelectorAll(
      '[data-message-author-role="user"]'
    ).length;

    for (let attempt = 0; attempt < maxRetries; attempt++) {
      const sendBtn = findSendButton();
      if (sendBtn) {
        sendBtn.click();
        console.log(`[ChatGPT Bridge] send button clicked (attempt ${attempt + 1})`);
        // Verify the message was actually sent
        await sleep(500);
        const userMessagesAfter = document.querySelectorAll(
          '[data-message-author-role="user"]'
        ).length;
        if (userMessagesAfter > userMessagesBefore) {
          console.log('[ChatGPT Bridge] message sent successfully');
          return true;
        }
        console.warn('[ChatGPT Bridge] send clicked but no new user message detected, retrying');
      } else if (attempt === 0) {
        console.log('[ChatGPT Bridge] send button not found, trying Enter key');
        input.dispatchEvent(new KeyboardEvent('keydown', {
          key: 'Enter', keyCode: 13, bubbles: true, cancelable: true
        }));
        await sleep(500);
        const userMessagesAfter = document.querySelectorAll(
          '[data-message-author-role="user"]'
        ).length;
        if (userMessagesAfter > userMessagesBefore) {
          return true;
        }
      }
      await sleep(retryDelayMs);
    }
    console.warn('[ChatGPT Bridge] send button not found after retries');
    return false;
  }

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

  async function handlePrompt(prompt, timeoutSeconds = 10, attempt = 0, conversationId = null, modelSearch = null, stream = false, requestId = "", debug = false, newConversation = false) {
    const timeout = Number(timeoutSeconds || 10);

    for (let loop = attempt; loop < 2; loop++) {
      // Navigate to a fresh chat only when needed.
      // If background.js already navigated (newConversation=true), the page is
      // already fresh — skip to avoid a redundant second navigation.
      if (!conversationId) {
        const currentPath = location.pathname || '';
        const isOnConversationPage = currentPath.includes('/c/') || currentPath.includes('/chat/');
        const isFreshPage = currentPath === '/' || currentPath === '' || currentPath.includes('temporary-chat');
        if (isOnConversationPage && !isFreshPage) {
          await navigateToNewChat();
        }
        // If newConversation=true and we're already on a fresh page, do nothing.
        // If newConversation=false and no conversationId, we're continuing on
        // whatever page is already loaded — also do nothing.
      }

      // If we have a conversation_id, navigate only when we're not already on it.
      // Staying on the page avoids a full reload — the content script stays loaded.
      if (conversationId) {
        const alreadyOnTarget = (extractConversationId() === conversationId);
        if (!alreadyOnTarget) {
          try {
            location.href = `/c/${conversationId}`;
            // Wait for page to fully load
            for (let i = 0; i < 15; i++) {
              await sleep(1000);
              if (document.querySelector('#prompt-textarea')) break;
            }
            // After navigation, wait a bit more for conversation history to render
            await sleep(2000);
          } catch (_) { /* navigate best-effort */ }
        }
      }

      // Capture the state of existing assistant messages before sending.
      // We track both the count AND the text of the last assistant message
      // to detect genuinely new responses. Element-identity Sets don't work
      // reliably with React re-renders that recreate DOM nodes.
      const assistantsBefore = document.querySelectorAll('[data-message-author-role="assistant"]');
      const lastAssistantTextBefore = assistantsBefore.length > 0
        ? (assistantsBefore[assistantsBefore.length - 1].querySelector('.markdown')?.textContent?.trim() || "")
        : "";
      const assistantCountBefore = assistantsBefore.length;

      // ── Model selection ──────────────────────────────────────────────
      // If model_search is specified, select the model before typing.
      // selectModel silently falls back if the button/menu isn't available.
      if (modelSearch) {
        await selectModel(modelSearch);
      }

      const input = findInput();
      if (!input) throw new Error("Could not find ChatGPT input box");

      // Clear and set input. ChatGPT uses ProseMirror; direct innerHTML/textContent
      // changes make the text visible but do not update ProseMirror state, so the
      // send button never appears. We type character-by-character at 500 WPM to
      // ensure ProseMirror properly tracks input and enables the send button.
      input.focus();
      // Clear existing text
      document.execCommand("selectAll", false, null);
      document.execCommand("delete", false, null);
      await sleep(100);

      // Type at 500 WPM (~24ms per character)
      await typeText(prompt, input);

      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new Event("change", { bubbles: true }));
      await sleep(500);

      // Send with retry — wait for send button to become available
      const sent = await sendWithRetry(input);
      if (!sent) throw new Error("Failed to send: send button not available");

      try {
        const result = await waitForResponse(timeout, assistantCountBefore, lastAssistantTextBefore, loop, { stream, requestId, debug });
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
        conversation_id: extractConversationId(),
        conversation_title: extractConversationTitle(),
      }, () => void chrome.runtime.lastError);
    } catch (_) {
      // Port may be closed during page unload; ignore.
    }
  }

  // ── Response extraction ─────────────────────────────────────────────────

  function extractLatestResponse(lastAssistantTextBefore) {
    // Scan assistant messages backward for .markdown content.
    // Skip the last assistant message if its text matches what was already
    // present before we sent the prompt. This handles React re-renders that
    // recreate DOM nodes (making element-identity Sets unreliable).
    const assistants = document.querySelectorAll('[data-message-author-role="assistant"]');
    for (let i = assistants.length - 1; i >= 0; i--) {
      const md = assistants[i].querySelector('.markdown');
      if (md) {
        const text = md.textContent.trim();
        if (text.length > 3) {
          // If this is the last assistant message and its text matches
          // what was already there before, skip it (stale).
          if (i === assistants.length - 1 && text === lastAssistantTextBefore) {
            continue;
          }
          return text;
        }
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

  async function waitForResponse(timeoutSeconds = 10, assistantCountBefore = 0, lastAssistantTextBefore = "", attempt = 0, options = {}) {
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

      // Check if any NEW assistant message has appeared.
      // Use count + last-text comparison (not element identity) because
      // React re-renders recreate DOM nodes, making Set-based filtering unreliable.
      const allAssistants = document.querySelectorAll('[data-message-author-role="assistant"]');
      const newAssistantArrived = allAssistants.length > assistantCountBefore;

      const text = extractLatestResponse(lastAssistantTextBefore);
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
              assistant_count: allAssistants.length,
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