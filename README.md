# chatgpt-bridge

Local bridge that delegates **planning / review / design** reasoning from the Hermes
CLI agent to the ChatGPT desktop app (GPT-5.6 Sol @ High effort), with a hard
constraint: **every Hermes-initiated conversation lives inside the `chatgpt-bridge`
project** — never general Chat.

Design: see `/home/david/chatgpt-bridge-design.md` (ground-up design produced with
GPT-5.6 Sol @ High, 2026-07-31).

## Status

- **Phase 0 (implemented, tested)**: protocol schemas, HMAC + peer-UID auth, SQLite
  registry + state machine, Unix-socket HTTP API, job queue, Hermes-side router/
  packer/reducer/accounting/provider client. All capabilities OFF by default.
- **Phase 1 (implemented, read-only)**: CDP discovery — app identity, project-list
  observation, model-picker observation. No conversation creation, no sends.
- **Phase 2 (implemented, CANARY VERIFIED live 2026-07-31)**: project-scoped
  conversation creation via `Start new chat in chatgpt-bridge` primitive, Work-mode
  model/effort enforcement (5.6 Sol + High via Model/Effort submenus), synthetic
  canary send + CANARY-OK response, thread-placement proof, binding enrolled
  (`cloud:Dworrall21/chatgpt-bridge`, 2 sources). Command: `chatgpt-bridge.py canary`.
- **Phase 3 (implemented, LIVE VERIFIED 2026-07-31)**: real delegation through the
  daemon — `hermes/tools/delegate_chatgpt_desktop.py` builds a compact packet
  (router gate → prompt builder with HERMES-BRIDGE sentinel → UDS submit → poll),
  the daemon's CDP executor creates a project conversation, enforces Sol/High,
  sends, waits, and stores the bounded result. Verified: accepted→queued→completed,
  project_verified=true, result returned. One task per conversation (no reuse yet).
- **Phase 4 (implemented, LIVE VERIFIED 2026-07-31)**: per-session conversation
  reuse — thread id captured post-send, `reuse_session` policy reopens the same
  project conversation (verified: two requests → same conversation_id, turn_count 2,
  accumulated context estimate), sharding thresholds (turns 24 / context 70k /
  idle 7d / quarantine), `--new-shard` override, restart recovery (`recover()`:
  intent-without-evidence → fail-closed; post-send → NEEDS_RECONCILIATION).
- Phases 5-6: not yet implemented (auto-routing, default offload).

## Layout

```
scripts/chatgpt-bridge.py      CLI: daemon | discover
src/chatgpt_bridge/
  protocol/                    JSON schemas, canonicalization, HMAC signing
  registry.py                  SQLite (WAL): bindings, sessions, conversations, requests, accounting
  state_machine.py             durable request lifecycle
  queue.py                     global UI lock + per-session queues (stub executor)
  server.py                    UDS HTTP API
  hermes/                      router (narrow), context packer, result reducer, token accounting, provider client
  app/                         CDP client + Phase-1 read-only discovery
  security/                    peer auth, content policy, redaction, output gate
  metrics/                     counters + JSONL events
migrations/                    SQL migrations
hermes/                        provider + routing config examples (disabled)
systemd/                       service/socket units (disabled)
tests/                         unit + contract
```

## Run

```bash
cd ~/Projects/chatgpt-bridge
.venv/bin/python -m pytest tests -q          # 37 passing

# Phase 1: read-only discovery against the running app (CDP 9222)
.venv/bin/python scripts/chatgpt-bridge.py discover

# Phase 0 daemon (all flags off; fails closed)
export CHATGPT_BRIDGE_HMAC_SECRET="$(openssl rand -hex 32)"
.venv/bin/python scripts/chatgpt-bridge.py daemon \
  --config config/bridge.example.toml \
  --db ~/.local/state/chatgpt-bridge/registry.sqlite3
```

## Feature flags (all default OFF)

| Env var | Meaning |
|---|---|
| `CHATGPT_BRIDGE_ENABLED` | master switch |
| `CHATGPT_BRIDGE_ALLOW_CREATE` | allow project-scoped conversation creation |
| `CHATGPT_BRIDGE_ALLOW_SEND` | allow sending prompts |
| `CHATGPT_BRIDGE_AUTOROUTE` | automatic narrow routing |

## Invariants

1. No generic new-chat primitive reachable from Hermes.
2. Conversation unusable until project ID verified == pinned `chatgpt-bridge` ID.
3. Every submission re-asserts Work mode, project destination, Sol, High.
4. At-most-once sends; ambiguous sends reconciled, never blindly retried.
5. Model output is untrusted data; never auto-executed.
6. No app-side fallback to general Chat in code or config.
