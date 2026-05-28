# ChatGPT.com DOM Selectors

> Reverse-engineered via Chrome DevTools Protocol (CDP) on `ws://127.0.0.1:9222`
> Page: `https://chatgpt.com/`
> Date: 2026-05-21

---

## 1. Input / Prompt Area

### Primary Input (ProseMirror rich text editor)
```
Selector:    #prompt-textarea
Tag:         DIV
Attributes:  contenteditable="true", role="textbox"
Class:       ProseMirror
aria-label:  "Chat with ChatGPT"
```

### Fallback Textarea
```
Selector:    textarea[name="prompt-textarea"]
Tag:         TEXTAREA
Class:       wcDTda_fallbackTextarea
Placeholder: "Ask anything"
aria-label:  "Chat with ChatGPT"
```

### Notes
- ChatGPT uses a ProseMirror-based rich text editor as the primary input.
- A fallback `<textarea>` exists but is typically hidden.
- To set text programmatically, target `#prompt-textarea` (contenteditable div):
  ```js
  const el = document.querySelector('#prompt-textarea');
  el.textContent = 'your prompt';
  el.dispatchEvent(new Event('input', { bubbles: true }));
  el.dispatchEvent(new Event('change', { bubbles: true }));
  ```

---

## 2. Send Button

### Text Send Button
```
Selector:    #composer-submit-button
Tag:         BUTTON
Class:       composer-submit-btn composer-submit-button-color h-9 w-9
aria-label:  "Send prompt"
data-testid: send-button
```

### Voice/Dictation Button
```
Selector:    button[aria-label="Start dictation"]
Tag:         BUTTON
Class:       composer-btn h-9 min-h-9 w-9 min-w-9
aria-label:  "Start dictation"
```

### Notes
- The text send button only appears/reveals when text is present in the input.
- It is inside the composer `<form>` element.
- To trigger send programmatically:
  ```js
  document.querySelector('#composer-submit-button').click();
  ```

---

## 3. Response Containers

### Assistant Message Container
```
Selector:    [data-message-author-role="assistant"]
Tag:         ARTICLE or DIV (wrapped in ARTICLE)
Class:       min-h-8 text-message relative flex w-full flex-col items-end gap-2 text-start break-words whitespace-normal outline-none keyboard-focused:focus-ring [.text-message+&]:mt-1
```

### Response Text (Markdown)
```
Selector:    [data-message-author-role="assistant"] .markdown
Tag:         DIV
Class:       markdown prose dark:prose-invert wrap-break-word w-full light markdown-new-styling
```

### User Message Container
```
Selector:    [data-message-author-role="user"]
Tag:         ARTICLE or DIV
Class:       same as assistant (distinguished by data-message-author-role)
```

### Notes
- Messages are wrapped in `<article>` elements at the top level of the thread.
- The `.markdown` class is reliable for extracting rendered response text.
- **Pitfall**: `containers[containers.length - 1]` is wrong in conversations — the last container may be the user's message. Always filter by `[data-message-author-role="assistant"]` or scan `.markdown` elements backward to find the last assistant response.
- Recommended extraction:
  ```js
  function extractLatestResponse() {
    const assistantMsgs = document.querySelectorAll('[data-message-author-role="assistant"]');
    if (assistantMsgs.length === 0) return null;
    const last = assistantMsgs[assistantMsgs.length - 1];
    const md = last.querySelector('.markdown');
    return md ? md.textContent.trim() : last.textContent.trim();
  }
  ```

---

## 4. Thinking / Streaming Indicator

### Scroll-Root Stream Active Class
```
Selector:    [class*="group-data-stream-active"]
```

### Scroll-to-Bottom Button (shows during streaming)
```
Selector:    button[class*="group-data-stream-active"]
Class:       btn-secondary bg-token-bg-primary/65! ... group-data-stream-active/scroll-root:w-10
```

### Spinner Dots Inside Button
```
Selector:    span[class*="group-data-stream-active/scroll-root:opacity-100"]
Class:       *:bg-token-text-primary/70 absolute inset-0 flex items-center justify-center gap-0.75 *:h-1 *:w-1 *:rounded-full opacity-0 group-data-stream-active/scroll-root:opacity-100
```

### Notes
- ChatGPT uses `group-data-stream-active/scroll-root` CSS state classes rather than traditional spinners.
- The most reliable way to detect streaming is to poll for new content and check text stability.
- There are no persistent "Thinking..." text labels visible in the DOM during generation.

---

## 5. New Chat

### Sidebar New Chat Button (Primary)
```
Selector:    a[data-testid="create-new-chat-button"]
Tag:         A
Class:       group __menu-item hoverable gap-1.5
Text:        "New chat"
Href:        https://chatgpt.com/
```

### Header New Chat Button (Alternative)
```
Selector:    a[href="https://chatgpt.com/"]
Tag:         A
Class:       group __menu-item hoverable
Text:        "New chatCtrlShiftO"
```

### Notes
- The `data-testid="create-new-chat-button"` is the most reliable selector.
- There are two instances of this data-testid on the page (one in the sidebar, one in the header area).
- To trigger new chat programmatically:
  ```js
  document.querySelector('a[data-testid="create-new-chat-button"]').click();
  ```

---

## 6. Model Selector

### Composer Model Pill
```
Selector:    .__composer-pill
Tag:         BUTTON
Class:       __composer-pill __composer-pill--neutral group/pill
ID:          radix-_r_5_ (dynamic)
Text:        "Extended"
```

### Model Configure Menu Item (after clicking pill)
```
Selector:    [data-testid="model-configure-modal"]
Tag:         DIV
role:        menuitem
Text:        "Configure..."
Stability:   HIGH
```

### Intelligence / Model Selection Modal
```
Selector:    [data-testid="modal-intelligence-menu"]
Tag:         DIV
Text:        "IntelligenceModel5.5InstantFor everyday chatsThinkingFor complex questionsPro..."
Stability:   HIGH
```

### Model Radio Buttons (inside modal)
```
Selector:    [data-testid="modal-intelligence-menu"] button[role="radio"]
Tag:         BUTTON
role:        radio
Options:     "Instant 5.3 For everyday chats", "Thinking 5.4 For complex questions"
Stability:   HIGH
```

### Notes
- ChatGPT does not display the model name (e.g., "GPT-4o") prominently in the main chat UI.
- Instead, a pill button in the composer shows the current mode (e.g., "Thinking", "Instant").
- **The pill uses Radix UI dynamic IDs** (`#radix-_r_*`) — NEVER rely on these.
- The model switching flow is: click pill → click "Configure..." (`data-testid="model-configure-modal"`) → modal opens (`data-testid="modal-intelligence-menu"`) → click radio button.
- The Configure menu item and modal both have stable `data-testid` attributes. Use those.
- Available models depend on subscription tier (Free/Plus/Pro).
- Modal closes automatically after selection.

---

## 7. Thread / Conversation Structure

### Main Thread Container
```
Selector:    main
Class:       @container/main relative flex min-w-0 flex-1 flex-col -translate-y-[calc(env(safe-area-inset-bottom, ...
```

### Scroll Root
```
Selector:    [class*="group/scroll-root"]
Class:       @w-sm/main:[scrollbar-gutter:var(--stage-scroll-gutter)] touch:[scrollbar-width:none] group/scroll-root relative flex min-h-0 min-w-0 flex-1 flex-col ...
```

### Message Turn Wrapper
```
Selector:    article
Class:       (varies, typically contains text-message classes)
```

---

## 8. Accessibility / ARIA

### ARIA Live Regions
```
Selector:    [aria-live="assertive"], [aria-live="polite"]
Class:       sr-only
```
- These are screen-reader-only regions for announcing updates.
- They are empty during normal operation.

---

## 9. File Upload

### Plus / Attachment Button (opens menu)
```
Selector:    [data-testid="composer-plus-btn"]
Tag:         BUTTON
aria-label:  "Add files and more"
Stability:   HIGH
```

### File Upload Menu Items
```
"Add photos & files"  — triggers file picker (Ctrl+U shortcut)
"Recent files"        — shows recent uploads
```
Note: Menu items are Radix-based with dynamic IDs. Match by text content or `role="menuitem"`.

### File Inputs (hidden, programmatic only)
```
Selector:    input#upload-files
Type:        file

Selector:    input#upload-photos
Type:        file
Class:       sr-only select-none

Selector:    input#upload-camera
Type:        file
Class:       sr-only select-none
```

### Notes
- The plus button `[data-testid="composer-plus-btn"]` is the primary entry point for file uploads.
- Clicking "Add photos & files" opens a native OS file picker — cannot automate via DOM.
- To upload programmatically, set files directly on `input#upload-files` via CDP `DOM.setFileInputFiles` or DataTransfer API.
- After file is attached, the send button `[data-testid="send-button"]` appears.
- File preview shows in the composer area before sending.

---

## 10. Sidebar Elements

### Open Sidebar
```
Selector:    button[aria-label="Open sidebar"]
Class:       text-token-text-primary contrast-high:not-dark:keyboard-focused:focus-ring ... mx-2 cursor-e-resize rtl:cursor-w-resize
```

### Close Sidebar
```
Selector:    button[data-testid="close-sidebar-button"]
aria-label:  "Close sidebar"
```

### Profile Menu
```
Selector:    div[aria-label="Open profile menu"]
Class:       group __menu-item hoverable gap-2 ms-2 me-1.5 gap-2! pe-1.5 data-fill:max-w-full [&>div:first-child]:gap-2!
```

### Conversation Options Button (per-item)
```
Selector:    [data-testid="history-item-N-options"]
Tag:         BUTTON
aria-label:  "Open conversation options for TITLE"
N:           0-indexed position in sidebar list
Stability:   HIGH
```

### Conversation Options Menu Items
```
"Rename"   — opens inline title editor
"Delete"   — removes conversation (may show confirmation)
"Archive"  — moves to archive
"Share"    — share options
```
Note: Menu items are Radix-based with dynamic IDs. Match by text content or `role="menuitem"`.

### Rename Input (inline in sidebar)
```
Selector:    input[aria-label="Chat title"]
Tag:         INPUT
Type:        text
Stability:   HIGH
```
- Appears inline replacing the conversation title
- Enter confirms, Escape cancels
- After confirm, `aria-label` on conversation link updates to new title

### Sidebar Conversation Links
```
Selector:    #history a[aria-label="CONVERSATION_TITLE"]
Tag:         A
Stability:   MEDIUM (title changes after rename)
```

---

## Summary Table

| Element          | Primary Selector                                    | Fallback Selector                        |
|------------------|------------------------------------------------------|------------------------------------------|
| Input            | `#prompt-textarea`                                   | `textarea[name="prompt-textarea"]`       |
| Send Button      | `#composer-submit-button`                            | `[data-testid="send-button"]`            |
| User Message     | `[data-message-author-role="user"]`                  | —                                        |
| Assistant Msg    | `[data-message-author-role="assistant"]`             | —                                        |
| Response Text    | `[data-message-author-role="assistant"] .markdown`   | `.markdown-new-styling`                  |
| New Chat         | `a[data-testid="create-new-chat-button"]`            | `a[href="https://chatgpt.com/"]`         |
| Model Pill       | `.__composer-pill`                                   | —                                        |
| Composer Form    | `form.group\\/composer`                             | `form`                                   |
| Sidebar Toggle   | `button[aria-label="Open sidebar"]`                  | —                                        |
| Close Sidebar    | `[data-testid="close-sidebar-button"]`               | —                                        |
| Model Pill       | `button.__composer-pill`                              | Text match ("Thinking"/"Instant")        |
| Configure Menu   | `[data-testid="model-configure-modal"]`               | `div[role="menuitem"]` with "Configure"  |
| Model Modal      | `[data-testid="modal-intelligence-menu"]`             | —                                        |
| Model Radio      | `[data-testid="modal-intelligence-menu"] button[role="radio"]` | —                               |
| Conversation Turn| `[data-testid="conversation-turn-N"]`                 | —                                        |
| Stop Button      | `[data-testid="stop-button"]`                         | —                                        |
| Plus/Attach Btn  | `[data-testid="composer-plus-btn"]`                   | `button[aria-label="Add files and more"]` |
| File Input       | `input#upload-files`                                  | `input#upload-photos`                    |
| Conv Options Btn | `[data-testid="history-item-N-options"]`              | `button[aria-label="Open conversation options for TITLE"]` |
| Rename Input     | `input[aria-label="Chat title"]`                      | —                                        |
| Conv Link        | `#history a[aria-label="TITLE"]`                      | —                                        |

---

## Pitfalls

1. **Send button visibility**: The text send button (`#composer-submit-button`) is hidden/disabled when the input is empty. It appears only after text is entered.

2. **Response container ambiguity**: The class `text-message` is used for BOTH user and assistant messages. Always use `[data-message-author-role="assistant"]` to target only assistant responses.

3. **Thinking indicators**: ChatGPT does not use traditional loading spinners. Streaming state is indicated by CSS group classes (`group-data-stream-active`). The most reliable completion detection is text stability polling (3 consecutive identical polls).

4. **Dynamic IDs**: Radix UI components (dropdowns, pills) use dynamic IDs like `radix-_r_5_`. Do not rely on these IDs.

5. **ProseMirror input**: Setting `.textContent` on the ProseMirror div works, but for complex formatting (newlines, etc.), you may need to manipulate the ProseMirror internal state or use the textarea fallback.

6. **Class name stability**: ChatGPT uses hashed/utility class names (Tailwind-style). The `data-testid` and `aria-label` attributes are more stable than classes. The `data-message-author-role` attribute is the most stable for message targeting.