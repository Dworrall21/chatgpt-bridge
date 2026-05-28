---
name: chatgpt-file-upload
domain: chatgpt.com
last_mapped: 2026-05-26
expires: 2026-06-26
tested: true
prerequisites:
  - Logged-in ChatGPT account
  - File to upload on disk
---

# ChatGPT File Upload Harness

## Prerequisites
- Logged-in ChatGPT account
- Browser: real Chrome (Playwright may silently fail on React)
- ChatGPT page loaded at chatgpt.com
- File path known (e.g. /tmp/report.pdf)

## Flow (4 steps)

### Step 1: Click Plus / Attachment Button
- Action: click
- Selector priority:
  1. `[data-testid="composer-plus-btn"]` ← STABLE
  2. `button[aria-label="Add files and more"]`
- Wait for: dropdown menu with menuitems
- Pitfalls:
  - Button may not be visible if composer is collapsed
  - Menu is a Radix dropdown — items have dynamic IDs
- Decision point: false

### Step 2: Select "Add photos & files"
- Action: click
- Selector priority:
  1. `div[role="menuitem"]` matching text "Add photos & files"
  2. Keyboard shortcut shown in menu: Ctrl+U
- Wait for: file picker dialog opens (native OS dialog)
- Pitfalls:
  - Menu also has "Recent files" option — don't click that
  - Menu closes if you click outside
  - File picker is a native dialog — cannot be automated via DOM. Must use `input#upload-files` directly (see alt approach below).
- Decision point: false

### Step 2 (ALT): Set File Directly (bypass native dialog)
- Action: set file on input
- Selector: `input#upload-files` ← STABLE
- Method: Use CDP `DOM.setFileInputFiles` or `input.files = DataTransfer`
- Pitfalls:
  - Input is hidden (`type="file"`) — not directly clickable
  - Must use programmatic file set, not user interaction
  - Accept attribute may filter file types
- Decision point: false

### Step 3: Verify File Attached
- Action: assert
- Check: file name/preview appears in composer area
- Check: send button appears `[data-testid="send-button"]`
- Pitfalls:
  - Large files may take time to upload — poll for appearance
  - Some file types show preview (images), others show just filename
  - File size limits may apply
- Decision point: false

### Step 4: Send Message with File
- Action: click or Enter
- Selector: `[data-testid="send-button"]` or press Enter in textbox
- Wait for: `[data-testid="conversation-turn-N"]` appears with assistant response
- Pitfalls:
  - You can send with just a file (no text) or file + text
  - If adding text, type it before sending
  - Streaming indicator: `[data-testid="stop-button"]` appears, disappears when done
- Decision point: true — what prompt to send with the file?

## Discovered Selectors

| Element | Selector | Stability |
|---------|----------|-----------|
| Plus/attach button | `[data-testid="composer-plus-btn"]` | HIGH |
| Add files menuitem | `div[role="menuitem"]` text "Add photos & files" | MEDIUM (text-based) |
| File input | `input#upload-files` | HIGH |
| Photo input | `input#upload-photos` | HIGH |
| Camera input | `input#upload-camera` | HIGH |
| Send button | `[data-testid="send-button"]` | HIGH |
| Conversation turn | `[data-testid="conversation-turn-N"]` | HIGH |
