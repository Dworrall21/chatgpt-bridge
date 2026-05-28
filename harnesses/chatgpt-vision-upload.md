# ChatGPT Vision Upload Harness

**name:** chatgpt-vision-upload
**domain:** chatgpt.com
**last_mapped:** 2026-05-27
**expires:** 2026-08-27
**tested:** true

## Overview

Upload an image to ChatGPT via programmatic paste and request analysis. Uses CDP `Runtime.evaluate` to create a `DataTransfer` with a `File` object and dispatch a `ClipboardEvent('paste')` on the ProseMirror composer.

## Activation Flow

1. Navigate to fresh temporary chat: `https://chatgpt.com/?temporary-chat=true`
2. Wait 6s for React to render
3. Re-enable CDP domains: `Runtime.enable`, `Input.enable`, `Page.enable`
4. Find ProseMirror: `document.querySelector('[contenteditable="true"]')`
5. Click at center of ProseMirror to focus
6. Paste image via DataTransfer + ClipboardEvent
7. Type prompt text via `document.execCommand('insertText')`
8. Submit via Enter key

## Image Paste via DataTransfer

```javascript
// Read image as base64
var binary = atob(BASE64_DATA);
var bytes = new Uint8Array(binary.length);
for (var i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i);
}
var file = new File([bytes], "screenshot.jpg", {type: "image/jpeg"});

// Create paste event
var dt = new DataTransfer();
dt.items.add(file);
var pasteEvent = new ClipboardEvent('paste', {
    clipboardData: dt,
    bubbles: true,
    cancelable: true
});

// Dispatch on focused ProseMirror
pm.focus();
pm.dispatchEvent(pasteEvent);
```

## Known Behavior

| Step | Result | Notes |
|------|--------|-------|
| Paste event dispatch | `false` (returned) | React still picks up the file via internal handlers |
| Image in composer | Visible (142x142 thumbnail) | Confirmed via `img[src^="blob:"]` query |
| ProseMirror text | Replaced by paste prompt | Text must be inserted AFTER paste |
| Enter submission | Works | Composer empties, generation starts |
| Stop button | Appears briefly during generation | `button[data-testid="stop-button"]` |
| Response | `[data-message-author-role="assistant"]` | Contains vision analysis of the image |

## Pitfalls

- **Paste must happen BEFORE typing text** — inserting text first then pasting doesn't work (paste replaces the prompt)
- **Deep research mode blocks regular submissions** — must use temporary chat, not a DR conversation
- **Enter may not work on all UI states** — check for send button as fallback: `button[data-testid="send-button"]`
- **Large images (>5MB) may fail** — ChatGPT has file size limits
- **`dispatchEvent` returns false** — don't rely on return value; check for `img[src^="blob:"]` to verify

## Test Results (May 2026)

- Image: `research-card-hq.jpg` (222KB, JPEG)
- Prompt: "Describe this screenshot in detail. What application is this? What does it show?"
- Response: 1,905 chars of accurate vision analysis
- Correctly identified: ChatGPT interface, Deep Research card, Turing Machine report, Executive Summary content, download/expand icons, feedback buttons
- Response time: ~20-30s
