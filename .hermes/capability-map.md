# ChatGPT Bridge — Full Capability Map

This defines "full functionality" — the complete set of capabilities the
ChatGPT bridge should support. Each capability is a discrete deliverable
that can become a kanban task when its prerequisites are met.

## Legend
- ✅ = done
- 🔄 = in progress
- ⬜ = not started
- 🔗 = blocked on parent capabilities

---

## Layer 1: Core Pipeline (Phase 1) ✅
1.1 ✅ DOM Discovery — chatgpt.com selectors reverse-engineered
1.2 ✅ Extension Scaffold — manifest.json, background.js, icons
1.3 ✅ Content Script — DOM interaction (input, send, response extraction)
1.4 ✅ Bridge Host — Python HTTP+WS server (aiohttp + websockets)
1.5 ✅ CLI Wrapper — bash chatgpt-chat script
1.6 ✅ Integration Test — pipeline verified

## Layer 2: Reliability & Observability (Phase 2) ✅
2.1 ✅ Hermes Skill (T7)
2.2 ✅ New Chat Navigation (T8)
2.3 ✅ Content Script Reliability (T9)
2.4 ✅ Response Retry (T10)
2.5 ✅ Integration Test Phase 2 (T11)

## Layer 3: Feature Parity with Gemini Bridge
3.1 ⬜ **File Upload** — Attach files (images, PDFs) to prompts via
    CDP Input.dispatchDragEvent. ChatGPT supports image/file upload.
    Parent: T8 (needs new-chat page for clean upload state)
3.2 ⬜ **Conversation History** — Track conversation IDs, allow
    continue vs fresh-chat per request. Bridge state already has
    last_conversation_id field.
3.3 ⬜ **Fresh Page Recovery** — When ChatGPT errors out, navigate
    to / and wait for input. Reload page on consecutive failures.
    Parent: T9 (needs reliable injection)

## Layer 4: Hermes Agent Integration ✅
4.1 ✅ **Provider Integration** — Registered as custom provider in
    Hermes config + profile at ~/.hermes/profiles/chatgpt-bridge/
4.2 ✅ **OpenAI-compatible API** — /v1/chat/completions endpoint on
    the bridge at http://127.0.0.1:11557/v1/chat/completions
    Usage: `hermes -p chatgpt-bridge chat "prompt"`
    Direct: `curl http://127.0.0.1:11557/v1/chat/completions -d '{"model":"chatgpt-5.5","messages":[{"role":"user","content":"Hi"}]}'`
4.3 ⬜ **Tool/Function Calling** — Support ChatGPT's function calling
    through the bridge. Requires structured output from ChatGPT's API
    via the web UI (more complex extraction).
    Parent: 4.2

## Layer 5: Production Hardening
5.1 ⬜ **Auto-Restart Extension** — When content script dies or
    extension errors spike, auto-reload the extension and tab via
    chrome.developerPrivate API (already proven to work).
    Parent: T9
5.2 ⬜ **Chrome Crash Recovery** — When Chrome/CDP goes down, wait
    for watchdog to restart, then re-establish all connections.
    Parent: T9
5.3 ⬜ **Bridge CLI Daemon** — `chatgpt-bridge start|stop|restart|status`
    that manages the bridge host process lifecycle.
5.4 ⬜ **Logging & Metrics Dashboard** — Structured logging, request
    tracing, and a simple dashboard showing bridge health/stats.
    Parent: T11

## Layer 6: Advanced Features
6.1 ⬜ **Streaming** — Stream ChatGPT responses token-by-token via
    SSE from the bridge. Complex: requires polling DOM at high
    frequency and detecting token-by-token updates.
    Parent: 3.2
6.2 ⬜ **Multiple Tabs** — Support multiple simultaneous ChatGPT
    conversations by opening additional tabs. Requires tab pool
    management.
6.3 ⬜ **Image Generation** — Access ChatGPT's DALL-E integration
    through the bridge (generate images from prompts).
    Parent: 3.1 (file handling infrastructure)

## Layer 7: Knowledge & Documentation
7.1 ⬜ **Usage Docs** — User-facing README with setup, troubleshooting,
    and common workflows
7.2 ⬜ **Agent Guide** — How Hermes agents can use the skill
7.3 ⬜ **Troubleshooting Guide** — Common issues and fixes
