# ChatGPT Bridge

Chrome extension + local Python bridge that lets AI agents use ChatGPT via browser session — no API keys required.

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

**Key design:** WebSocket lives in `background.js` (service worker), NOT `content.js`. ChatGPT's CSP blocks WebSocket connections to localhost from page context. Service workers are immune to page CSP.

## Quick Start

### 1. Start Chrome with remote debugging
```bash
./start-chrome.sh
```
This launches Chrome with a persistent profile on port 9222.

### 2. Load the extension
1. Open `chrome://extensions`
2. Enable "Developer mode" (top-right)
3. Click "Load unpacked" → select this directory
4. Navigate to `https://chatgpt.com` — content script auto-injects

### 3. Start the bridge
```bash
python3 bridge-host.py
```

### 4. Send a prompt
```bash
# Simple chat
curl -X POST http://127.0.0.1:11557/chat \
  -H 'Content-Type: application/json' \
  -d '{"prompt": "Say hello"}'

# OpenAI-compatible
curl -X POST http://127.0.0.1:11557/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model": "chatgpt-5.5", "messages": [{"role": "user", "content": "Say hello"}]}'

# With conversation continuation
curl -X POST http://127.0.0.1:11557/chat \
  -H 'Content-Type: application/json' \
  -d '{"prompt": "Continue from before", "conversation_id": "<uuid>"}'

# Streaming
curl -X POST http://127.0.0.1:11557/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"messages": [{"role": "user", "content": "Stream this"}], "stream": true}'

# File upload (images, PDFs)
curl -X POST http://127.0.0.1:11557/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"messages": [{"role": "user", "content": "Describe this image"}], "files": ["/path/to/image.png"]}'

# Model selection
curl -X POST http://127.0.0.1:11557/chat \
  -H 'Content-Type: application/json' \
  -d '{"prompt": "Think step by step", "model_search": "thinking"}'
```

### 5. Use as a Hermes provider
```bash
hermes -p chatgpt-bridge chat "Your prompt here"
```

## API Reference

### GET /health
Returns bridge status, extension count, uptime, and request metrics.

### POST /chat
| Field | Type | Default | Description |
|---|---|---|---|
| prompt | string | required | The prompt to send |
| conversation_id | string | null | Continue an existing conversation |
| model_search | string | null | Switch model (e.g. "thinking", "5.5") |
| timeout | int | 10 | Timeout in seconds (max 600) |
| files | string[] | [] | File paths to upload via CDP |

### POST /v1/chat/completions
OpenAI-compatible endpoint. Supports `messages[]`, `model`, `stream`, `max_tokens`, `timeout`, `conversation_id`, `model_search`, `files[]`.

When `stream: true`, returns SSE frames:
```
data: {"id": "...", "object": "chat.completion.chunk", "choices": [{"delta": {"content": "..."}, "finish_reason": null}]}

data: [DONE]
```

## Project Structure

```
chatgpt-extension/
├── manifest.json           # MV3 extension manifest
├── background.js           # Service worker: WebSocket + injection
├── content.js              # DOM interaction (input, send, response)
├── bridge-host.py          # Python HTTP + WS bridge server
├── cleanup-test-chats.py   # CDP-based test conversation cleanup
├── chatgpt-cdp.py          # Standalone CDP file upload script
├── chatgpt-chat            # Bash CLI wrapper
├── chatgpt-bridge          # Hermes CLI wrapper
├── start-chrome.sh         # Chrome launcher + watchdog
├── dom-selectors.md        # ChatGPT DOM reference
├── DEVELOPMENT.md          # Internal development notes
├── docs/
│   ├── review-20260528.md  # Full code review and fix plan
│   └── skills/             # Hermes skill documentation (see below)
├── harnesses/              # Mapped interaction flows
├── tests/                  # Unit and smoke tests
└── scratch/                # Experiment scripts (gitignored data artifacts)
```

## Skills Documentation

The `docs/skills/` directory contains the Hermes Agent skills associated with this project:

- **[chatgpt-bridge.md](docs/skills/chatgpt-bridge.md)** — Core ChatGPT bridge skill: setup, architecture, pitfalls, session management, CDP interaction patterns, deep research extraction, and full reference index.
- **[browser-bridge-extension.md](docs/skills/browser-bridge-extension.md)** — Generic browser bridge pattern: extension design, CSP considerations, CDP-direct alternative, port conventions, and common pitfalls.

These skills are loaded by Hermes Agent to provide domain knowledge when working with the ChatGPT bridge.

## Cleaning Up Test Conversations

Test prompts should be prefixed with `[bridge-test]` so they're identifiable for batch cleanup:

```bash
curl -X POST http://127.0.0.1:11557/chat \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"[bridge-test] Reply with exactly: ok","timeout":30}'
```

To list, find, or delete test conversations:

```bash
# List all sidebar conversations
python3 cleanup-test-chats.py --list

# Delete conversations matching a title pattern
python3 cleanup-test-chats.py --find "[bridge-test]"

# Delete specific conversations by ID
python3 cleanup-test-chats.py --ids <conv-id-1> <conv-id-2>
```

## Ports

| Service | Port |
|---|---|
| Chrome DevTools Protocol | 9222 |
| Bridge HTTP | 11557 |
| Bridge WebSocket | 11558 |

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `extensions: 0` in /health | Extension not loaded or | Load via chrome://extensions |
| `503 No ChatGPT tab` | No chatgpt.com tab open | Open chatgpt.com in debug Chrome |
| `504 Gateway Timeout` | Prompt timed out | Increase `timeout` param |
| `ERR_CONNECTION_REFUSED` | Bridge not running | `python3 bridge-host.py` |
| `__pycache__` load error | Stale cache dir | `find ... -name __pycache__ -exec rm -rf {} +` (auto-cleaned) |

## Development

See [DEVELOPMENT.md](DEVELOPMENT.md) for internal architecture, kanban board state, and issue tracking.

## License

MIT
