# Gemini Bridge Architecture — Reference for ChatGPT Extension

This document explains how the Gemini Bridge works end-to-end so the same
pattern can be applied to build a ChatGPT Chrome extension.

## Overview

The Gemini Bridge is a **local bridge** connecting CLI tools/agents to the
Gemini Web UI via a Chrome extension + Python bridge server. This bypasses
the need for API keys and uses the user's browser session.

## Architecture Diagram

```
Hermes Agent (or curl / any CLI tool)
      │
      │  POST /chat {"prompt": "..."}
      ▼
┌─────────────────────────────────────┐
│  Bridge Host (Python + aiohttp)     │
│  HTTP :11557 — accepts prompts      │
│  WS :11558   — background connects  │
└──────────┬──────────────────────────┘
           │ WebSocket (ws://127.0.0.1:11558)
           ▼
┌─────────────────────────────────────┐
│  Chrome Extension (Manifest V3)     │
│  content.js — injected into         │
│    gemini.google.com/*              │
│  background.js — minimal relay      │
│    (MV3 service worker)             │
└──────────┬──────────────────────────┘
           │ DOM interaction
           ▼
┌─────────────────────────────────────┐
│  Gemini Web UI                      │
│  (user's Pro subscription session)  │
└─────────────────────────────────────┘
```

## Three Components

### 1. Chrome Extension

**Location:** `~/gemini-extension/`

Files:
- `manifest.json` — Manifest V3, host_permissions for gemini.google.com, icon
- `content.js` — **The core.** WebSocket client + DOM interaction (987 lines)
- `background.js` — Minimal service worker for wake-up/relay

**Key architecture decisions:**

**A) WebSocket lives in content.js, NOT background.js.**
MV3 service workers die after ~5 min of inactivity. The content script
persists as long as the tab is open, making it the correct owner of the
persistent WebSocket connection. This was the #1 lesson learned.

- Owns the WebSocket to `ws://127.0.0.1:11558` (auto-reconnects with backoff)
- Relays bridge prompts to `content.js` with `chrome.runtime.sendMessage`
- Receives DOM results from `content.js` and returns them over WebSocket

**C) content.js:**
- Does DOM interaction only; no direct localhost WebSocket (ChatGPT CSP blocks it)
- Sets value using native property setter + dispatches input/change events
  (React/Angular won't detect programmatic `.value`= changes)
- Clicks send button or dispatches Enter keydown
- Polls DOM for new response text with stability detection
- Sends result back via WS `{"type": "response", "id": "...", "text": "..."}`
- Queue: if already processing, subsequent requests queue up

**C) background.js:**
- `chrome.runtime.onMessage.addListener` for relay from the bridge
- `chrome.runtime.reload()` support for refreshing extension code
- Tab reload on startup (after extension reload)

### 2. Bridge Host (Python)

**Location:** `~/gemini-extension/gemini-bridge-host.py` (1556 lines)

**Key architecture:**
- **aiohttp** for HTTP + WebSocket (not raw asyncio — hand-rolled HTTP parsing
  fails silently with real HTTP clients)
- Single process runs both HTTP (`:11557`) and WS (`:11558`) servers
- HTTP endpoints: `/chat` (POST), `/health` (GET), `/reload` (POST),
  `/restore` (POST), `/navigate` (POST), `/new_chat` (POST)
- Auto-installs deps via pip (aiohttp, websockets) with `--break-system-packages`
- SSRF protection: validates URLs before fetching, blocks private IP ranges
- Atomic writes for persistent bridge state (~/.hermes/gemini_bridge_state/)
- Metrics tracking: total/success/timeout/failed requests
- Consecutive failure tracking with auto-recovery (reloads page after 3 failures)
- WebSocket lock (`asyncio.Lock()`) serializes communication with the shared tab
- Pending futures map for request/response matching

### 3. CDP-Only Mode (Optional/Alternative)

**Location:** `~/gemini-extension/gemini-cdp.py` (273 lines)

Standalone mode that uses Chrome DevTools Protocol directly instead of the
extension. Requires Chrome with `--remote-debugging-port=9222`.

- Connects to `ws://127.0.0.1:9222/devtools/page/<PAGE_ID>`
- Uses `Runtime.evaluate` to execute JS in the Gemini tab
- Uses `Input.dispatchDragEvent` for file upload (CDP-level, not JS)
- No extension needed, but requires Chrome to be started with specific flags

## DOM Interaction Pattern (The Critical Parts)

### Input Detection
Multiple CSS selector fallbacks, ordered by reliability:
```javascript
div[contenteditable="true"][role="textbox"]
div[contenteditable="true"][data-placeholder]
rich-textarea div[contenteditable="true"]
div[contenteditable="true"]  // broadest fallback
```

### Setting Input Value (React/Vue/Angular Compatible)
```javascript
// For input/textarea: use the native property setter
const setter = Object.getOwnPropertyDescriptor(
  window.HTMLInputElement.prototype, "value"
).set;
setter.call(input, text);
// For contenteditable:
input.textContent = text;
// Always dispatch events:
input.dispatchEvent(new Event("input", {bubbles: true}));
input.dispatchEvent(new Event("change", {bubbles: true}));
```

### Send Button Detection
```javascript
button[aria-label*="Send"]
button[aria-label*="send"]
button[aria-label*="Submit"]
button[aria-label*="Run"]
// Fallback: Enter keydown
```

### Response Extraction (THE BIGGEST LESSON LEARNED)
**Do NOT use `containers[last]`** — the Gemini DOM uses the same class
`.model-response-text` for both user prompts AND assistant responses. In
a long conversation, the last container is often the USER's message.

**The fix:** Iterate backward through `.markdown` elements and find the
LAST one that actually has text:
```javascript
function extractLatestResponse() {
  const allMarkdown = document.querySelectorAll('.markdown');
  for (let i = allMarkdown.length - 1; i >= 0; i--) {
    const text = allMarkdown[i].textContent.trim();
    if (text && text.length > 0 && !isNoise(text)) {
      return extractResponseFromContainer(container, md, text);
    }
  }
  return null;
}
```

### Response Polling Strategy
- Poll interval: 1000ms
- Stability threshold: 3 consecutive identical reads
- Max wait: 180 seconds
- Returns partial results with warning on timeout
- Noise filter: skip "Gemini is AI and can make mistakes", "Loading...", etc.

## Port Conventions
- HTTP: 11557
- WebSocket: 11558

## Known Pitfalls (For ChatGPT Version)

1. **MV3 service worker death** — Don't put WebSocket in background.js
2. **Chrome localhost WS restriction** — May need `chrome://flags/#allow-insecure-localhost`
3. **React/framework event detection** — Must dispatch `input` + `change` events
4. **Response extraction** — Use backward `.markdown` scan, not `containers[last]`
5. **Short responses timeout** — Thinking mode takes ~180s even for trivial queries
6. **DOM selectors break on UI update** — Need multiple fallback strategies
7. **Port conflicts** — Check `ss -tlnp | grep 1155[56]` first
8. **PEP 668** — Pass `--break-system-packages` for pip installs on Ubuntu/Debian
9. **`asyncio.Event().wait()` pattern** — Not `serve_forever()` + `cleanup()` together
10. **`nonlocal` vs `global`** — In nested async functions within `main()`, use `nonlocal`
11. **Blob URLs** — Can't be fetched by Python, need to be converted to base64 in content script
12. **JS programmatic DragEvent can't upload files** — Use CDP's `Input.dispatchDragEvent` instead

## Files to Create for ChatGPT Version

```
~/chatgpt-extension/
├── manifest.json      # MV3, host_permissions for chatgpt.com
├── content.js         # WS client + DOM interaction for ChatGPT
├── background.js      # Minimal MV3 service worker
├── bridge-host.py     # Adapted from gemini-bridge-host.py (HTTP+WS ports)
├── chatgpt-chat       # CLI wrapper (bash)
├── icon.png           # Extension icon (needed or Chrome grays it out)
└── architecture.md    # This file
```

## DOM Selectors to Discover for ChatGPT

For the ChatGPT version, the following selectors need to be discovered
via Chrome DevTools inspection:

| Element | Selector to Find | Notes |
|---------|-----------------|-------|
| Input box | ? | Likely contenteditable or textarea |
| Send button | ? | Check aria-label, role, class |
| Response area | ? | Look at chatgpt.com DOM structure |
| Response text | ? | Find the deepest text container |
| New chat | ? | Button or link to start fresh |
| Model selector | ? | Optional for model switching |
| File upload | ? | Optional for file support |
| Thinking indicator | ? | To detect when generation starts/stops |
