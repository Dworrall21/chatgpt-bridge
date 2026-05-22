# ChatGPT Bridge — Extension Scaffold

## v1.0.0 (initial scaffold)

Manifest V3 extension files created from scratch, following the proven Gemini Bridge
pattern. All key lessons from the Gemini version are reflected here:

- WebSocket lives in `content.js`, never in `background.js`
- No `type: "module"` in the background declaration
- Icons are non-empty (three sizes generated programmatically)
- No unused permissions declared
- `host_permissions` scoped to `https://chatgpt.com/*`

Extension directory: `~/chatgpt-extension/`
