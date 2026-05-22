#!/usr/bin/env python3
"""
chatgpt-cdp.py — Send prompts to ChatGPT via Chrome CDP (no extension needed).

Usage:
    python3 chatgpt-cdp.py "Your prompt here"
    python3 chatgpt-cdp.py --file prompt.txt
    echo "prompt" | python3 chatgpt-cdp.py

Requirements:
    Chrome running with --remote-debugging-port=9222
    Signed in to ChatGPT
"""
import sys, json, time, asyncio, urllib.request, websockets

CDP_PORT = 9222

def find_chatgpt_tab():
    req = urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json", timeout=3)
    tabs = json.loads(req.read())
    for t in tabs:
        if "chatgpt.com" in t.get("url", ""):
            return t["id"]
    return None

async def send_prompt(prompt, timeout_s=10):
    page_id = find_chatgpt_tab()
    if not page_id:
        raise RuntimeError("No ChatGPT tab found. Open https://chatgpt.com first.")

    ws_url = f"ws://127.0.0.1:{CDP_PORT}/devtools/page/{page_id}"
    async with websockets.connect(ws_url, max_size=2**20) as ws:
        async def js(expr, t=10):
            rid = int(time.time() * 1000000) % 1000000
            await ws.send(json.dumps({"id": rid, "method": "Runtime.evaluate", "params": {
                "expression": expr, "returnByValue": True, "timeout": int(t * 1000)
            }}))
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=t + 5)
                d = json.loads(raw)
                if d.get("id") == rid:
                    exc = d.get("result", {}).get("exceptionDetails")
                    if exc:
                        raise RuntimeError(exc.get("text", "CDP eval error"))
                    return d["result"]["result"].get("value")

        # 1. Stop any ongoing generation and start fresh
        await js("""
var stop = document.querySelector('[data-testid="stop-button"]');
if (stop) stop.click();
""")
        await asyncio.sleep(1)

        # Go to new chat (try multiple approaches)
        await js("""
var nc = document.querySelector('a[href="/"]');
if (nc) { nc.click(); }
else { location.href = '/'; }
""")
        await asyncio.sleep(3)

        # 2. Find input and set prompt — wait for it to be ready
        input_ready = False
        for wait_i in range(10):
            input_ok = await js("!!document.querySelector('#prompt-textarea')", t=5)
            if input_ok:
                input_ready = True
                break
            await asyncio.sleep(1)
        
        if not input_ready:
            raise RuntimeError("ChatGPT input box did not appear after navigation")

        escaped = json.dumps(prompt)
        input_ok = await js(f"""
var input = document.querySelector('#prompt-textarea');
if (!input) input = document.querySelector('div[contenteditable="true"][role="textbox"]');
if (input) {{
    input.focus();
    input.textContent = '';
    input.dispatchEvent(new Event('input', {{bubbles: true}}));
    input.textContent = {escaped};
    input.dispatchEvent(new Event('input', {{bubbles: true}}));
    input.dispatchEvent(new Event('change', {{bubbles: true}}));
    true
}} else false
""")
        if not input_ok:
            raise RuntimeError("Could not find ChatGPT input box")
        await asyncio.sleep(1)

        # 3. Click send (try button first, then Enter key)
        sent = await js("""
var b = document.querySelector('#composer-submit-button');
if (b && !b.disabled) { b.click(); 'button'; }
else {
    // Use Enter key as fallback
    var input = document.querySelector('#prompt-textarea');
    if (input) {
        input.dispatchEvent(new KeyboardEvent('keydown', {
            key: 'Enter', code: 'Enter', keyCode: 13, which: 13,
            bubbles: true, cancelable: true
        }));
        'enter';
    } else 'no input';
}
""")

        # 4. Wait for response — scan using data-message-author-role
        poll_ms = 2000
        max_polls = int(timeout_s * 1000 / poll_ms)
        last_text = ""
        stable_count = 0

        for i in range(max_polls):
            await asyncio.sleep(poll_ms / 1000)
            result = await js("""
(function() {
    // Find ALL assistant messages, then take the last one with content
    var assistants = document.querySelectorAll('[data-message-author-role="assistant"]');
    for (var i = assistants.length - 1; i >= 0; i--) {
        // Try .markdown first
        var md = assistants[i].querySelector('.markdown');
        if (md) {
            var text = md.textContent.trim();
            if (text.length > 3) return JSON.stringify({found: true, text: text.substring(0, 100000)});
        }
        // Check if we got an empty thinking container (retry needed)
        var thinking = assistants[i].querySelector('.result-thinking');
        if (thinking && thinking.textContent.trim().length === 0) {
            return JSON.stringify({found: false, emptyThinking: true});
        }
        // Try prose
        var prose = assistants[i].querySelector('.prose');
        if (prose) {
            var text = prose.textContent.trim();
            if (text.length > 3) return JSON.stringify({found: true, text: text});
        }
    }
    return JSON.stringify({found: false});
})()
""", t=5)

            if result:
                parsed = json.loads(result) if isinstance(result, str) else json.loads(result)
                if parsed.get("found"):
                    text = parsed["text"]
                    if text == last_text:
                        stable_count += 1
                        if stable_count >= 3:
                            return text
                    else:
                        stable_count = 0
                        last_text = text

        if last_text:
            return last_text + "\n\n[WARNING: timed out]"
        
        # Check what went wrong
        diag = await js("""
JSON.stringify({
    url: location.href,
    turns: document.querySelectorAll('section[data-testid^="conversation-turn-"]').length,
    assistants: document.querySelectorAll('[data-message-author-role="assistant"]').length,
    hasInput: !!document.querySelector('#prompt-textarea'),
    inputText: (document.querySelector('#prompt-textarea')||{}).textContent || '',
    stopBtn: !!document.querySelector('[data-testid="stop-button"]'),
})
""")
        raise RuntimeError(f"Timed out waiting for ChatGPT response. Page state: {diag}")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Send prompts to ChatGPT via CDP")
    parser.add_argument("prompt", nargs="?", help="Prompt text")
    parser.add_argument("--file", "-f", help="Read prompt from file")
    parser.add_argument("--timeout", "-t", type=int, default=10)
    args = parser.parse_args()

    if args.file:
        with open(args.file) as f:
            prompt = f.read()
    elif args.prompt:
        prompt = args.prompt
    elif not sys.stdin.isatty():
        prompt = sys.stdin.read()
    else:
        parser.print_help()
        sys.exit(1)

    print(f"Sending prompt: {prompt[:80]}...", file=sys.stderr)
    response = asyncio.run(send_prompt(prompt, args.timeout))
    print(response)
