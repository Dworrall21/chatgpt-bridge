---
name: chatgpt-deep-research
domain: chatgpt.com
last_mapped: 2026-05-27
expires: 2026-06-27
tested: true
prerequisites:
  - ChatGPT Plus subscription (Deep Research is a paid feature)
  - Logged-in ChatGPT account
  - Chrome with CDP on port 9222
---

# ChatGPT Deep Research Harness

## Overview

Deep Research is a ChatGPT Plus feature accessible via the composer's plus button menu. It generates comprehensive research reports using multi-step web searches and synthesis. The response renders inside a **cross-origin sandboxed iframe** (`connector_openai_deep_research.web-sandbox.oaiusercontent.com`), which creates unique extraction challenges.

## Activation Flow (5 steps)

### Step 1: Ensure Fresh Chat
- **Action**: Navigate to `https://chatgpt.com/` (or `?temporary-chat=true`)
- **Wait for**: ProseMirror editor visible (`[contenteditable="true"].ProseMirror`)
- **CDP**: Call `Page.navigate({"url": "https://chatgpt.com/"})` then wait 3-5s
- **Pitfall**: After `Page.navigate`, re-call `Runtime.enable` — the execution context resets
- **Pitfall**: The page uses a nested scrollable div, not `window.scrollY`. Find it with:
  ```javascript
  var all = document.querySelectorAll('*');
  for (var i = 0; i < all.length; i++) {
    var el = all[i];
    var style = window.getComputedStyle(el);
    if ((style.overflowY === 'scroll' || style.overflowY === 'auto') && el.scrollHeight > 2000) {
      el.scrollTop = 0;  // Start at top
      break;
    }
  }
  ```

### Step 2: Open Plus Button Menu
- **Action**: click the plus button
- **Selector**: `[data-testid="composer-plus-btn"]`
- **Coords**: `(578, 712)` in 891px viewport (varies by viewport size)
- **Click method**: CDP `Input.dispatchMouseEvent` (`.click()` doesn't fire React/Radix events)
- **Wait for**: Menu popup appears (Radix dropdown with `[role="menuitemradio"]`)
- **Pitfall**: `querySelectorAll('[role="menuitem"]')` returns empty — menu items are Radix `menuitemradio` role, visible only via `Accessibility.getFullAXTree`

### Step 3: Find "Deep research" Menu Item
- **Selector method**: Use CDP `Accessibility.getFullAXTree` to find the `menuitemradio` node whose `name.value` matches "Deep research"
- **Click method**: Get the `backendDOMNodeId` and call `DOM.getBoxModel`, then click the center coords via CDP mouse events
- **Fallback**: Use `Runtime.evaluate` with `document.querySelector('[data-testid="composer-plus-btn"]')` to find plus button, then `Accessibility.getFullAXTree` to find the menu items
- **Known coords**: `(424, 607)` in ~891x1280 viewport (tested May 2026)
- **Click sequence**: `mouseMoved` → `mousePressed` → `mouseReleased`
- **Pitfall**: The menu closes if you click outside or if the page loses focus

### Step 4: Type Research Prompt
- **Selector**: `.ProseMirror` contenteditable div (same as normal message input)
- **Coords**: `(319, 773)` in 891px viewport
- **Input method**: CDP `Input.insertText({"text": "your prompt"})` — goes through browser input pipeline properly for ProseMirror
- **Pitfall**: `execCommand('insertText')` may not work via CDP; use `Input.insertText` instead
- **Pitfall**: The ProseMirror editor is `contenteditable`, not a `<textarea>` — setting `.textContent` or `.innerHTML` doesn't update internal state

### Step 5: Submit
- **Action**: Press Enter
- **Method**: CDP `Input.dispatchKeyEvent` with key='Enter'
- **CDP pattern**:
  ```python
  await raw('Input.dispatchKeyEvent', {'type': 'rawKeyDown', 'key': 'Enter', 'code': 'Enter', 'windowsVirtualKeyCode': 13})
  await raw('Input.dispatchKeyEvent', {'type': 'keyUp', 'key': 'Enter', 'code': 'Enter', 'windowsVirtualKeyCode': 13})
  ```
- **Wait for**: Research progress indicator appears (the iframe loads)

## Response Extraction

### Critical: Response is Cross-Origin Ephemeral

The deep research response renders in a **sandboxed iframe** from:
```
https://connector_openai_deep_research.web-sandbox.oaiusercontent.com/?app=chatgpt&...
```
This iframe is:
- **Cross-origin** — CORS blocks access to `iframe.contentWindow.document`
- **Sandboxed** — nested iframe with `src="about:blank"` that loads React SPA dynamically
- **Ephemeral** — after page navigation/reload, the visible rendering is gone; only user messages persist

The conversation API (`/backend-api/conversation/<uuid>/textdocs`) returns 401 even from page `fetch()` — requires auth cookies not accessible to JS.

### What Won't Work
| Method | Result | Why |
|--------|--------|-----|
| `document.querySelector("main").innerText` | Only shows user messages | Research in iframe, not main DOM |
| `[data-message-author-role="assistant"]` | No results | Deep research doesn't use this structure |
| `.ProseMirror` innerText | Empty | Input area, not response |
| `Accessibility.getFullAXTree` | No response content | Iframe is opaque to AX tree |
| `fetch()` from page context | 401 Unauthorized | HTTP-only cookies not accessible by JS |
| curl with cookies | HTML (Cloudflare challenge) | Cloudflare blocks non-browser requests |
| **"Copy response" button** | **Empty copy** | React handler tries to read iframe → CORS blocked → copies nothing |
| **navigator.clipboard monkey-patch** | **Empty** | Handler doesn't go through standard clipboard API |

### What Works

#### ✅ **BEST: Inner iframe execCommand via outer iframe CDP target** (152K chars, tested)
**This is the new #1 method.** The inner iframe that holds the research content is **same-origin** to the outer iframe's document. By connecting to the outer iframe's CDP target, we can directly access `inner.contentWindow.document` and run `execCommand('selectAll')` + `execCommand('copy')` on it.

**How it works:**
1. Click inside the outer iframe (from parent page CDP) to transfer focus
2. Connect to the **outer iframe** CDP target
3. Access the inner iframe via `document.querySelector('iframe').contentWindow`
4. Monkey-patch `document.execCommand('copy')` on the inner iframe to capture to `window.__copied`
5. `inner.contentWindow.document.execCommand('selectAll')`
6. `inner.contentWindow.document.execCommand('copy')`
7. Read `inner.contentWindow.__copied` — gets the full report text

**Why it works:** The iframe chain is:
```
Parent page (cross-origin) → Outer iframe (CDP target) → Inner iframe (same-origin to outer)
```
The outer iframe's sandbox attribute (`allow-scripts`) still permits same-origin access to the inner iframe's `contentWindow`.

**Script:** `scratch/deep-research/deep-research-extract.py`
```bash
python3 scratch/deep-research/deep-research-extract.py <conversation-id> [output-path]
```

**Results:** 152,476 chars extracted from the Turing Machine report (full report).

#### ✅ **Ctrl+A → Ctrl+C** (works sometimes, previously extracted 36K chars)
This bypasses CORS by using the browser's native clipboard. **However**, the research content is in a **nested iframe** (double-sandboxed), so this only works when the outer iframe's focus chain reaches the inner iframe. It's inconsistent — worked for the Turing Machine report (36K chars) but failed on subsequent attempts (0 chars).

**Steps:**
1. Click inside the deep research iframe to focus it
2. Ctrl+A to select all content
3. Ctrl+C to copy to system clipboard
4. Read clipboard via `navigator.clipboard.readText()`

**Note:** `navigator.clipboard.readText()` often returns empty in CDP context (no user gesture). Use `document.execCommand('copy')` as fallback. Even then, the double-nested iframe blocks selection access.

#### ✅ **Screenshot + OCR** (fallback, ~90% accuracy)
Take a tall screenshot (set `Emulation.setDeviceMetricsOverride` to 1280x2000) and run Tesseract OCR.

**Results**: ~36,660 chars of plain text extracted from a single deep research report (the full Executive Summary + all sections). This matches the content of the downloaded `.md` file but without markdown formatting.

**Why it works**: The browser's clipboard API is OS-level. When you copy from within a cross-origin iframe, the content goes to the system clipboard just like any other copy. The parent page can then read it with `navigator.clipboard.readText()` — there's no origin restriction on reading the clipboard (just a user-gesture requirement, which CDP can satisfy).

**Pitfalls:**
- The iframe must be focused before Ctrl+A (use a mouse click at iframe center)
- Wait 0.3-0.5s between key events for the browser to process them
- `navigator.clipboard.readText()` returns a Promise — use `awaitPromise: true`
- The clipboard permission is implicitly granted by user gesture (but CDP's mouse click may not count — if readText fails with "not allowed", try adding `Runtime.evaluate` with `userGesture: true`)
- The extracted text is plain text only — no markdown formatting, LaTeX, or mermaid diagrams (use the download button for formatted content)

#### ✅ **Download button** (works reliably, tested May 2026)
The research card has a **download/export button** (OCR reads as `©` or similar icon at the top-right of the research card). Clicking it:
1. Triggers a file download in the browser
2. Saves a `.md` file to `~/Downloads/deep-research-report.md`
3. Contains the **full report** in well-formatted markdown with:
   - All 7 sections (Executive Summary, Historical Context, Formal Model, etc.)
   - Mathematical notation (LaTeX)
   - Mermaid diagrams (timeline, complexity class hierarchy)
   - Tables (variant comparison, milestone timeline)
   - Primary bibliography with DOIs
4. Report size: ~42KB, ~270 lines

**File naming pattern**: `deep-research-report.md`, `deep-research-report (1).md`, `deep-research-report (2).md`, etc.

Control flow for detecting download:
```python
# After clicking download button, check ~/Downloads for new .md files
import glob, os
before = set(glob.glob(os.path.expanduser("~/Downloads/*.md")))
# ... click download button ...
await asyncio.sleep(3)
after = set(glob.glob(os.path.expanduser("~/Downloads/*.md")))
new_files = after - before
# Read the newest .md file
```

#### ✅ **Screenshot + OCR** (works, imperfect)
- `Page.captureScreenshot({'format': 'jpeg', 'quality': 92})` captures visible iframe content
- Use `tesseract` (Python: `pytesseract.image_to_string()`) for OCR
- Best results from **tall screenshots** (set `Emulation.setDeviceMetricsOverride` to 2400x8000 then capture)
- OCR quality: ~80-90% accurate on research content; LaTeX/mermaid may be garbled
- Crop the research card region for better results (~300, 200, 2200, 1800)

#### ⚠️ Screenshot-based extraction (fallback)
- Use `Input.mouseMoved` to hover over research card y-range (400-890ms) to reveal action buttons
- Buttons are revealed by CSS `:hover` on the conversation-turn container
- Action buttons appear at bottom of research card (y=917): Copy response, Good, Bad, Share, More actions
- None of these provide better extraction than the download button

#### ⚠️ Expand/Collapse buttons (partial)
Inside the research card iframe, sections may be collapsed. The expand/collapse chevron icon:
- Toggles between collapsed (showing only section title) and expanded (showing full section content)
- Clicking chevrons inside the native view does NOT help with extraction — the rendered content changes but is still inside the CORS-blocked iframe
- For full content, always use the **download button** instead

### Research Progress Monitoring

After submitting a Deep Research request, the page shows:
- **Stop button**: `[data-testid="stop-button"]` — present while research is active
- **Progress**: The sandboxed iframe shows research progress (searches completed, citations found)
- **Completion indicator**: "Research completed in Xm · Y citations · Z searches"

Check if research is still running by polling for the stop button:
```javascript
!!document.querySelector('[data-testid="stop-button"]')
```

## Key Selectors Recorded

| Element | Selector | Notes |
|---------|----------|-------|
| Plus button | `[data-testid="composer-plus-btn"]` | Opens the attachment/tools menu |
| Send button | `[data-testid="send-button"]` | Send message |
| Stop button | `[data-testid="stop-button"]` | Stop generation |
| ProseMirror input | `.ProseMirror` | `contenteditable`, not textarea |
| Close button | `[data-testid="close-button"]` | Radix dialog close |
| Conversation turn | `[data-testid="conversation-turn-N"]` | Each turn (user+assistant) |
| Sidebar nav | `nav[aria-label="Sidebar"]` | Left sidebar |
| Open sidebar button | `button[aria-label="Open sidebar"]` | Toggle sidebar |
| Share button | `[data-testid="share-chat-button"]` | Thread header |
| Conversation options | `[data-testid="conversation-options-button"]` | Thread header menu |
| Thread flyout | `[data-testid="stage-thread-flyout"]` | Side panel flyout |
| Conversation files | `[data-testid="modal-conversation-files"]` | Files in thread modal |
| New project | `[data-testid="modal-new-project-enhanced"]` | Create project modal |
| Composer actions | `[data-testid="composer-footer-actions"]` | Bottom toolbar |
| Apps button | Text content="Apps" | In composer footer |
| Sites button | `button[aria-label*="Sites"]` | Web search toggle |

## Flow Diagram

```
Fresh Page
    │
    ▼
┌──────────────────────────┐
│ Click [composer-plus-btn] │  ← CDP mouse events required
└──────────┬───────────────┘
           ▼
┌──────────────────────────┐
│ Menu opens (Radix dropdown)│
│ Items: Add photos, Files,  │
│   Create image,            │
│   Deep research,           │
│   Web search, More         │
└──────────┬───────────────┘
           ▼
┌──────────────────────────┐
│ Click "Deep research"     │  ← menuitemradio in AXTree
│ (coords ~424,607)         │     Not found via role=menuitem
└──────────┬───────────────┘
           ▼
┌──────────────────────────┐
│ Deep Research mode active │
│ - Textbox placeholder     │
│   "Get a detailed report" │
│ - Footer shows buttons:   │
│   Deep research, Apps,    │
│   Sites, Thinking         │
└──────────┬───────────────┘
           ▼
┌──────────────────────────┐
│ Type prompt into .PM     │  ← Input.insertText
└──────────┬───────────────┘
           ▼
┌──────────────────────────┐
│ Press Enter to submit    │  ← dispatchKeyEvent
└──────────┬───────────────┘
           ▼
┌──────────────────────────────────────────┐
│ Research runs in sandboxed iframe         │
│ Progress: N searches, citations, time    │
│ Poll: [data-testid="stop-button"]        │
│ Output: Cross-origin, ephemeral,         │
│   only extractable via screenshots       │
└──────────────────────────────────────────┘
```

## Pitfalls

### 1. IIFE invocation
CDP `Runtime.evaluate` requires multiline expressions to end with `()` — otherwise they return function objects, not values:
```python
# WRONG — returns {} 
await js("(() => { const x = 1; return x; })")
# CORRECT — returns 1
await js("(() => { const x = 1; return x; })()")
```

### 2. Page.navigate resets context
After `Page.navigate()`, you MUST re-enable `Runtime.enable`, `DOM.enable`, etc. The execution context changes. Re-query the tab websocket URL too — the page ID may change.

### 3. Scrollable container
ChatGPT uses a nested scrollable div, NOT `document.body`. To scroll to top/bottom:
```javascript
// Find the main scrollable container
var all = document.querySelectorAll('*');
for (var i = 0; i < all.length; i++) {
  var el = all[i];
  var style = window.getComputedStyle(el);
  if ((style.overflowY === 'scroll' || style.overflowY === 'auto') && el.scrollHeight > 2000) {
    el.scrollTop = 0;  // Scroll to start
    break;
  }
}
```

### 4. "Show more" has concatenated text
The toggle button has text content "Show moreShow less" as one string. Match with `textContent.indexOf('Show more') >= 0`, not exact equality.

### 5. Deep research response is ephemeral
The report is rendered in a cross-origin iframe and NOT saved to conversation history. Extract via the **download button** DURING the session. On page reload, the response content is lost forever — only user messages remain. The download button saves a `.md` file to `~/Downloads/` that you can retrieve later.

### 6. Cloudflare blocks API access
The `/backend-api/conversation/<id>/textdocs` endpoint returns HTML (Cloudflare challenge), not JSON, when accessed via curl even with cookies. The browser's native fetch uses HTTP-only cookies that can't be extracted.

### 7. "Copy response" button yields nothing
The action buttons at the bottom of the deep research card (Copy response, Good, Bad, Share, More actions) appear when hovering over the card area (y=400-890). However, the "Copy response" button does NOT work — the React handler tries to access the iframe's `contentDocument` which is CORS-blocked. Even with `navigator.clipboard` monkey-patched, the captured content is empty. Always use the **download button** instead.

### 8. Download button detection
The download button icon is rendered inside the sandboxed iframe, so it can't be selected via DOM queries. Click it via approximate screen coordinates:
- Icon position: top-right of research card, approximately at `(x=790, y=430)` in 891px viewport when scrolled to top of conversation
- Alternative: Use `Input.dispatchMouseEvent` with `mouseMoved` → `mousePressed` → `mouseReleased` at the known icon region
- After clicking, poll `~/Downloads/` for new `.md` files with `glob("~/Downloads/*.md")`

## Recording Sessions

### Session 1: General navigation (206s, 847 frames)
A 206-second recording session (847 frames at 4.1fps, 879 events, 92MB) captured navigation patterns:
- **Conversation browsing**: Clicking previous research conversations (turn-1, turn-5)
- **Sidebar interaction**: Opening/closing sidebar
- **Share/options flow**: Share button → modal → close
- **Conversation options**: Options button → menu → menuitem selection → close
- **Composer footer**: "Apps" and "Sites" buttons (web search toggle)
- **DOM mutations**: Thread flyout, conversation files modal, new project modal detected as added/removed elements
- **Key finding**: The research card has a "Copy response" action button at bottom (y=917) but navigating to it via container scroll is tricky

### Session 2: Download + Expand buttons (57s, 286 frames)
A 57-second recording session (286 frames at 5fps, 3 click events, 1 DOM mutation) captured the download and expand button flow:
- **Download button click**: Triggered file download to `~/Downloads/deep-research-report.md` (42KB, 271 lines)
- **Expand/collapse click**: Toggled section visibility within the research card iframe
- **Key finding**: The download button is the ONLY reliable way to get the full report content

## Cron-Based Monitoring Pattern

For long-running deep research sessions (5-45+ minutes), use a **cron job** instead of a blocking loop. This is more resilient because:
- CDP connections can drop during long runs — cron reconnects each tick
- You get periodic progress screenshots you can review later
- Completion is detected cleanly (no stop button → content in iframe)
- Extraction happens automatically when done

### Script

The `deep-research-monitor.py` script provides three commands:

```
# Submit a new deep research
python3 deep-research-monitor.py submit "Your research prompt..."

# Run one monitor check (for testing or manual)
python3 deep-research-monitor.py monitor <conversation_id>

# Force extraction of completed research
python3 deep-research-monitor.py extract <conversation_id>
```

### Setup

```bash
# Submit research → prints conversation_id
CID=$(python3 deep-research-monitor.py submit "Your prompt")
echo "Conversation ID: $CID"

# Set up cron (via hermes cronjob tool):
cronjob action=create \\
  name="Deep Research Monitor" \\
  schedule="every 5m" \\
  prompt="cd /home/david/chatgpt-extension && python3 -u deep-research-monitor.py monitor $CID"
```

### Monitor output format

The monitor script prints machine-readable status:

```
STATUS=running      Screenshot=screenshots/progress_20260527-104226.jpg
STATUS=waiting      Content=0 chars  Screenshot=screenshots/progress_20260527-104226.jpg
STATUS=complete     Report=report.md (36660 chars)
```

- `running`: Stop button visible — research is actively generating
- `waiting`: No stop button, but content not yet available — research may be queued or just finished
- `complete`: Content successfully extracted and saved

### Output directory structure

```
~/chatgpt-extension/research-sessions/<conversation_id>/
├── session.json       # Metadata: prompt, status, timestamps
├── screenshots/
│   ├── initial.jpg    # Screenshot right after submission
│   ├── progress_*.jpg # Periodic screenshots from each cron tick
│   └── ...
└── report.md          # Final extracted report (when complete)
```

### Detection logic

1. Navigate to conversation URL
2. Check for `[data-testid="stop-button"]` — if present, research is still running
3. Take screenshot (regardless of status)
4. If stop button is gone, try Ctrl+A → Ctrl+C on the deep research iframe
5. If extracted content > 100 chars, mark as complete and save
6. If content is empty, mark as waiting (research may have queued the response)

### Pitfalls

- Don't re-navigate to `chatgpt.com/` — navigate directly to the /c/<id> URL
- After `Page.navigate`, always call `Runtime.enable` to reinitialize the execution context
- The iframe may briefly flash/change during transitions — take screenshots AFTER the 6s settle delay
- On 'waiting' for >30 min, the research may have failed silently — check manually

## References

- `dom-selectors.md` — master selector reference
- `references/chatgpt-com-selectors.md` — detailed selector documentation
- `references/cdp-observer-technique.md` — CDP observer pattern
- `references/chatgpt-e2e-test-patterns.md` — E2E test patterns
