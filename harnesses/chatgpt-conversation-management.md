---
name: chatgpt-conversation-management
domain: chatgpt.com
last_mapped: 2026-05-26
expires: 2026-06-26
tested: true
prerequisites:
  - Logged-in ChatGPT account
  - Conversations in sidebar history
---

# ChatGPT Conversation Management Harness

## Prerequisites
- Logged-in ChatGPT account
- Browser: real Chrome
- Sidebar open (click `[aria-label="Open sidebar"]` if closed)
- At least one conversation in history

## Flow A: Rename Conversation (3 steps)

### Step 1: Open Conversation Options Menu
- Action: click
- Selector priority:
  1. `[data-testid="history-item-N-options"]` where N is 0-indexed position in sidebar list
  2. `button[aria-label="Open conversation options for TITLE"]`
- Wait for: dropdown menu appears
- Pitfalls:
  - Options button only appears on hover — may need to hover the parent `li` first
  - N is 0-indexed from top of sidebar list
  - The `aria-label` includes the conversation title, which changes after rename
  - Menu is Radix-based — item IDs are dynamic
- Decision point: false

### Step 2: Click "Rename"
- Action: click
- Selector priority:
  1. `div[role="menuitem"]` matching text "Rename"
  2. Fallback: `div:nth-of-type(3)` inside the menu (3rd item)
- Wait for: inline input appears replacing the conversation title
- Pitfalls:
  - Menu also has "Delete", "Archive", "Share" etc. — be precise
  - Menu closes after selection
  - Other menu items have dynamic Radix IDs
- Decision point: false

### Step 3: Type New Name and Confirm
- Action: type + Enter
- Selector: `input[aria-label="Chat title"]` ← STABLE
- Wait for: input disappears, sidebar shows new title
- Pitfalls:
  - Input replaces the title inline in the sidebar
  - Enter confirms, Escape cancels
  - Empty name may not be accepted
  - The `aria-label` on the conversation link updates to new title
- Decision point: true — what should the new name be?

## Flow B: Delete Conversation (2 steps)

### Step 1: Open Conversation Options Menu
- Same as Rename Step 1

### Step 2: Click "Delete"
- Action: click
- Selector priority:
  1. `div[role="menuitem"]` matching text "Delete"
- Wait for: confirmation dialog or conversation removed from sidebar
- Pitfalls:
  - May show confirmation dialog — need to confirm
  - Deletion is permanent (or moves to trash with expiry)
  - If confirmation dialog appears, look for `[data-testid]` or `button` with "Delete" / "Confirm"
- Decision point: true — confirm deletion?

## Flow C: Open Sidebar (1 step)

### Step 1: Click Open Sidebar Button
- Action: click
- Selector: `button[aria-label="Open sidebar"]` ← STABLE
- Wait for: sidebar visible, conversation list loaded
- Pitfalls:
  - Button only visible when sidebar is closed
  - Sidebar may take a moment to load conversation list
- Decision point: false

## Discovered Selectors

| Element | Selector | Stability |
|---------|----------|-----------|
| Options button | `[data-testid="history-item-N-options"]` | HIGH |
| Options aria-label | `button[aria-label="Open conversation options for TITLE"]` | MEDIUM (title-dependent) |
| Rename menuitem | `div[role="menuitem"]` text "Rename" | MEDIUM (text-based) |
| Delete menuitem | `div[role="menuitem"]` text "Delete" | MEDIUM (text-based) |
| Rename input | `input[aria-label="Chat title"]` | HIGH |
| Open sidebar | `button[aria-label="Open sidebar"]` | HIGH |
| Close sidebar | `[data-testid="close-sidebar-button"]` | HIGH |
| Sidebar links | `#history a[aria-label="TITLE"]` | MEDIUM (title-dependent) |
