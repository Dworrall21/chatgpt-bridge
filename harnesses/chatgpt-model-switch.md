---
name: chatgpt-model-switch
domain: chatgpt.com
last_mapped: 2026-05-26
expires: 2026-06-26
tested: true
prerequisites:
  - Logged-in ChatGPT account
  - Chrome with CDP on port 9222 or Hermes browser tools
---

# ChatGPT Model Switching Harness

## Prerequisites
- Logged-in ChatGPT account
- Browser: real Chrome required (Playwright may silently fail on React)
- ChatGPT page loaded at chatgpt.com

## Flow (4 steps)

### Step 1: Click Model Pill
- Action: click
- Selector priority:
  1. `button.__composer-pill` (class-based, may change)
  2. Text-based: find button containing "Thinking", "Instant", "Pro", or model name
  3. Vision anchor: pill-shaped button near the composer input
- Wait for: dropdown menu appears
- Pitfalls:
  - Radix UI dynamic IDs (`#radix-_r_*`) change every render — NEVER use these
  - The pill text shows the current mode (e.g. "Thinking"), not the model name
  - Pill may not be visible if sidebar is open and viewport is narrow
- Decision point: false

### Step 2: Click "Configure..." in Menu
- Action: click
- Selector priority:
  1. `[data-testid="model-configure-modal"]` ← STABLE, use this
  2. `div[role="menuitem"]` containing text "Configure"
- Wait for: modal-intelligence-menu appears
- Pitfalls:
  - Menu may have other items (model quick-switch) — "Configure..." opens the full modal
  - Menu closes if you click outside it
- Decision point: false

### Step 3: Select Model from Modal
- Action: click
- Selector priority:
  1. `[data-testid="modal-intelligence-menu"]` buttons with `role="radio"`
  2. Text match: "Instant 5.3", "Thinking 5.4", "Pro" etc.
- Wait for: modal closes
- Pitfalls:
  - Available models depend on subscription tier (Free/Plus/Pro)
  - Radio buttons show tier + description: "Instant 5.3 For everyday chats", "Thinking 5.4 For complex questions"
  - Modal closes automatically after selection
  - Some options may be gated behind upgrade prompts
- Decision point: true — which model do you want?

### Step 4: Verify Model Changed
- Action: assert
- Check: `button.__composer-pill` text has changed to new model/mode name
- Fallback: check URL or page state for model indicator
- Pitfalls:
  - Change may take a moment to reflect in the pill text
  - No explicit success notification — the pill text IS the confirmation
- Decision point: false

## Discovered Selectors (stable)

| Element | Selector | Stability |
|---------|----------|-----------|
| Model pill | `button.__composer-pill` | MEDIUM (class may change) |
| Configure menu item | `[data-testid="model-configure-modal"]` | HIGH |
| Intelligence modal | `[data-testid="modal-intelligence-menu"]` | HIGH |
| Model radio buttons | `[data-testid="modal-intelligence-menu"] button[role="radio"]` | HIGH |
| Composer input | `div[aria-label="Chat with ChatGPT"][role="textbox"]` | HIGH |
| Send button | `[data-testid="send-button"]` | HIGH |
| Stop button | `[data-testid="stop-button"]` | HIGH |
| Conversation turns | `[data-testid="conversation-turn-N"]` | HIGH (N increments) |
| Close sidebar | `[data-testid="close-sidebar-button"]` | HIGH |
| Sidebar links | `#history a[aria-label="CONVERSATION_TITLE"]` | MEDIUM (title-dependent) |
