# ChatGPT Chrome Extension — Development Notes

> User-facing documentation lives in [README.md](README.md).
> Quick start, API reference, troubleshooting → that file.
> This file covers internal architecture, kanban state, and issues.

---

## Project Structure

```
~/chatgpt-extension/
├── manifest.json           # MV3, host_permissions for chatgpt.com
├── background.js           # Service worker: WebSocket + content script injection
├── content.js              # DOM interaction (input, send, response extraction)
├── bridge-host.py          # Python HTTP (:11557) + WS (:11558) bridge server
├── chatgpt-cdp.py          # Standalone CDP script (no extension needed)
├── chatgpt-chat            # Bash CLI wrapper for sending prompts
├── dom-selectors.md        # ChatGPT DOM selectors reference
├── architecture.md         # Gemini bridge architecture explanation
├── DEVELOPMENT.md          # This file — development notes
├── README.md               # User-facing docs (see above)
├── start-chrome.sh         # Launches Chrome debug instance + watchdog
├── CHANGELOG.md            # Version history
└── icon*.png               # Extension icons (16, 48, 128)
```

---

## Architecture

```
Hermes Agent / curl
    │  POST /chat {"prompt": "..."}
    ▼
Bridge Host (Python, localhost:11557 HTTP + :11558 WS)
    │  WebSocket (background.js → port 11558)
    ▼
Extension Background (service worker, immune to page CSP)
    │  chrome.runtime.sendMessage
    ▼
Extension Content Script (injected into chatgpt.com)
    │  DOM interaction
    ▼
ChatGPT Web UI (browser session)
```

> **Key design decision:** WebSocket lives in `background.js`, NOT `content.js`.
> ChatGPT's CSP blocks WebSocket connections to localhost from the page context.
> Service workers are not subject to page CSP, so the WebSocket connects from
> the background script. content.js uses `chrome.runtime.sendMessage` to relay
> prompts/responses.

### Inference vs Implementation

- **Extension is the inference engine** — ChatGPT's LLM generates the response.
- **Bridge is the transport layer** — handles HTTP, WS coordination, CDP upload,
  SSRF protection, metrics, conversation state persistence.
- **Hermes is the orchestrator** — dispatches tasks, manages context, calls
  endpoints.

---

## What's Working

### Pipeline
- ✅ **CDP approach** (`chatgpt-cdp.py`): sends prompts and extracts responses
      via Chrome DevTools Protocol; no extension required.
- ✅ **Bridge host** (`bridge-host.py`): running on :11557/:11558, health
      endpoint works, metrics tracking (success/timeout/failed).
- ✅ **Extension injection**: `background.js` injects `content.js` into
      chatgpt.com tabs via `chrome.scripting.executeScript` with retry.
- ✅ **No CSP errors**: WebSocket in background.js with health preflight — no
      more blocked-connection errors in the extension console.
- ✅ **Content script**: loads, detects DOM, uses `execCommand` for ProseMirror
      input (native setter for the textarea div).
- ✅ **Stale response filter**: records previous assistant message before each
      prompt, excludes it from polling results.
- ✅ **Service worker keepalive**: content script pings background every 20s,
      which re-triggers WebSocket connection if down.
- ✅ **Pipeline test**: `Reply with exactly SUCCESS` returns `"SUCCESS"` in ~4s.

### Infrastructure
- ✅ **Chrome debug instance**: persistent profile at `~/.chrome-chatgpt-debug`
      on port 9222. Watchdog auto-restarts if Chrome crashes.
- ✅ **Start script**: `start-chrome.sh` launches Chrome with persistent profile.
- ✅ **Kanban board**: `chatgpt-extension` board, active sprint.
- ✅ **DOM selectors**: documented from live inspection of chatgpt.com.

---

## Issues Fixed

| Issue | Fix |
|---|---|
| CSP blocking WebSocket to localhost | Moved WebSocket from content.js to background.js (service worker immune to page CSP) |
| `chrome.runtime` undefined in content.js | Removed `world: "MAIN"` — uses default ISOLATED world |
| `chrome.runtime.onStartup` doesn't exist in MV3 | Removed invalid event listener |
| Extension not persisting across restarts | Persistent user-data-dir (not /tmp) |
| Chrome crashing | Watchdog auto-restarts Chrome |
| `--load-extension` flag doesn't work with existing profiles | Extension loaded once via "Load unpacked", persists in profile |
| Backward response extraction needed | Iterate assistant messages backward, not use last container |
| WebSocket connection refused | Added bridge health preflight before WS connect (fetch /health first) |
| Bridge deadlock on concurrent requests | Removed nested `ws_lock` in `chat()` — was blocking the only connected tab |
| `__pycache__` blocking extension load | Added cleanup step; Chrome rejects dirs starting with underscore |
| Stale responses returned by poller | Added previousResponse filter to ignore pre-existing assistant messages |
| Extension disabled after stale `__pycache__` | Programmatic extension reload via `developerPrivate` API |
| Service worker not reconnecting on wake | Preflight + wake ping from content script every 20s |

---

## Open Issues

### Sequential Prompt Reliability (~50%)
Two back-to-back prompts: first succeeds quickly, second often times out.
Conversation-pollution — ChatGPT's page state gets confused after the first
response. **Being addressed in T8: New Chat Navigation.**

### Content Script Injection Reliability (~60%)
`chrome.tabs.onUpdated` sometimes misses the `complete` event for restored tabs.
**Being addressed in T9: Content Script Reliability.**

### ChatGPT Empty Responses (~40%)
Instant-mode sometimes returns empty thinking containers. **T10: Response
Retry & Timeout Handling** added stable polling with 3-read stability check as
workaround.

### DOM Selectors May Break
ChatGPT updates their UI frequently. Selectors documented May 2026, may need
refreshing:
- Input: `#prompt-textarea` (ProseMirror contenteditable div)
- Send: `#composer-submit-button` (appears after text is entered)
- Response: `[data-message-author-role="assistant"]` → `.markdown`
- New chat: `a[href="/"]`

---

## Kanban Board

Board: `chatgpt-extension`
Switch profiles: `hermes kanban boards switch chatgpt-extension`

### Completed

#### T1 — DOM Discovery
chatgpt.com selectors documented from live inspection.

#### T2 — Extension Scaffold
Manifest V3 extension created (manifest, background.js, icons).

#### T3 — Content Script
WebSocket + DOM interaction for ChatGPT. ExecCommand approach for ProseMirror
textarea input.

#### T4 — Bridge Host
Python HTTP+WS server on :11557/:11558 with SSRF protection and metrics.

#### T5 — CLI Wrapper
`chatgpt-chat` bash script wrapping bridge calls with timeout, conversation,
and model-selection flags.

#### T6 — Pipeline Integration
End-to-end test: `Reply with exactly SUCCESS` → `"SUCCESS"` in ~4s.

#### T7 — ChatGPT Bridge Hermes Skill
Custom Hermes skill created for calling the bridge from agent runs.

#### T8 — New Chat Navigation
Extension navigates to a fresh chat URL before each prompt, eliminating
sequential-prompt pollution.

#### T9 — Content Script Reliability
Port-based alive signaling, retry-on-send-failure, injection on worker startup.
Reliability up from ~60% to stable.

#### T10 — Response Retry & Timeout Handling
Three-read stability check and auto-retry on empty responses. Default timeout 10s,
user-configurable up to 600s.

#### T11 — Phase 2 Integration
All Phase 2 tests pass. Bridge undo on abort implemented.

#### T12 — /v1/chat/completions
OpenAI-compatible endpoint added to bridge-host.py. Accepts `messages[]`,
`model`, `stream`. Returns OpenAI-standard JSON.

#### T13 — ChatGPT Custom Provider Registered
Bridge registered as a Hermes custom model provider under the name  `chatgpt-bridge`. `hermes -p chatgpt-bridge chat "prompt"` works through the Hermes pipeline.

#### T14 — chatgpt-bridge Hermes Profile
Dedicated Hermes profile (`chatgpt-bridge`) created; `hermes -p chatgpt-bridge
chat "prompt"` routes through the bridge end-to-end.

#### T15 — CDP File Upload
`upload_files_cdp()` in bridge-host.py uses `Input.dispatchDragEvent` for
image/PDF/composer drag-and-drop. Both `/chat` and `/v1/chat/completions`
accept `files` arrays. Fallback when CDP unavailable.

#### T16 — SSE Live Streaming
`/v1/chat/completions` supports `stream: true` — Server-Sent Events with
delta frames and `[DONE]` sentinel. Content.js emits `done` when generation
stabilises.

#### T17 — Conversation History
Extension sends `conversation_id` and `conversation_title` with each response.
Bridge persists to `state.json` and returns both fields to callers. New chat
navigation runs before each prompt. `/chat` falls back to stored
`last_conversation_id` when none is supplied.

#### T18 — Dynamic Model Selection
`model_search` parameter opens the ChatGPT Radix model picker, types the term,
and selects the first match. Injected into content.js via `selectModel()`. Works
with both `/chat` and `/v1/chat/completions`.

#### T19 — Auto-Restart Watchdog
CDP watchdog monitors `http://127.0.0.1:9222/json/version`. After 30s of
unavailability it restarts Chrome via `start-chrome.sh`. Content script pings
extension keepalive every 20s; extension re-injects into new tabs.

### Remaining

None active in current sprint. Future work around model selection UX, long-form
streaming robustness, and SSRF refinement is in Triage.

---

## Ports

| Service | Port |
|---|---|
| Chrome DevTools Protocol | 9222 |
| Bridge HTTP | 11557 |
| Bridge WS | 11558 |
