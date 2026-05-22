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

### Notes
- ChatGPT does not display the model name (e.g., "GPT-4o") prominently in the main chat UI.
- Instead, a pill button in the composer shows the current mode (e.g., "Extended" for extended thinking, or model-specific pills).
- The model selector dropdown is likely accessible by clicking this pill.
- The pill ID is dynamic (`radix-_r_*`) and changes on every render.
- **No stable data-testid** was found for the model selector.
- The sidebar may contain conversation titles that include model names (e.g., "Gemini model selection"), but these are conversation history items, not the active model selector.

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

### File Inputs
```
Selector:    input#upload-files
Type:        file
```

```
Selector:    input#upload-photos
Type:        file
Class:       sr-only select-none
```

```
Selector:    input#upload-camera
Type:        file
Class:       sr-only select-none
```

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

---

## Pitfalls

1. **Send button visibility**: The text send button (`#composer-submit-button`) is hidden/disabled when the input is empty. It appears only after text is entered.

2. **Response container ambiguity**: The class `text-message` is used for BOTH user and assistant messages. Always use `[data-message-author-role="assistant"]` to target only assistant responses.

3. **Thinking indicators**: ChatGPT does not use traditional loading spinners. Streaming state is indicated by CSS group classes (`group-data-stream-active`). The most reliable completion detection is text stability polling (3 consecutive identical polls).

4. **Dynamic IDs**: Radix UI components (dropdowns, pills) use dynamic IDs like `radix-_r_5_`. Do not rely on these IDs.

5. **ProseMirror input**: Setting `.textContent` on the ProseMirror div works, but for complex formatting (newlines, etc.), you may need to manipulate the ProseMirror internal state or use the textarea fallback.

6. **Class name stability**: ChatGPT uses hashed/utility class names (Tailwind-style). The `data-testid` and `aria-label` attributes are more stable than classes. The `data-message-author-role` attribute is the most stable for message targeting.