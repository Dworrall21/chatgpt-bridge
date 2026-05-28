#!/usr/bin/env python3
"""Deep research on ChatGPT via CDP: plus menu -> Deep research -> prompt -> wait -> save."""
import json, asyncio, urllib.request, websockets, time, sys, os

CDP_PORT = 9222

def find_tab():
    req = urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json", timeout=3)
    for t in json.loads(req.read()):
        if "chatgpt.com" in t.get("url", ""):
            return t["id"]
    return None

async def main():
    tab_id = find_tab()
    if not tab_id:
        print("ERROR: No ChatGPT tab"); sys.exit(1)

    async with websockets.connect(f"ws://127.0.0.1:{CDP_PORT}/devtools/page/{tab_id}", max_size=2**22) as ws:
        msg_id = 0
        
        async def drain():
            """Drain pending CDP events (not responses)."""
            try:
                while True:
                    raw = await asyncio.wait_for(ws.recv(), timeout=0.2)
                    d = json.loads(raw)
                    # Only drain events (have 'method', no 'id')
                    if d.get("id") is not None:
                        # This is a response, put it back by storing it
                        # Actually we can't put it back, so just stop draining
                        break
            except (asyncio.TimeoutError, Exception):
                pass

        async def js(expr, timeout=10):
            nonlocal msg_id
            msg_id += 1; mid = msg_id
            await ws.send(json.dumps({"id": mid, "method": "Runtime.evaluate",
                "params": {"expression": expr, "returnByValue": True, "timeout": int(timeout*1000)}}))
            deadline = time.time() + timeout + 5
            while time.time() < deadline:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=3)
                except asyncio.TimeoutError:
                    continue
                d = json.loads(raw)
                if d.get("id") == mid:
                    return d.get("result", {}).get("result", {}).get("value")
                # Skip events, keep looking for our response
            return None

        async def cdp(method, params=None):
            nonlocal msg_id
            msg_id += 1; mid = msg_id
            cmd = {"id": mid, "method": method}
            if params: cmd["params"] = params
            await ws.send(json.dumps(cmd))
            deadline = time.time() + 5
            while time.time() < deadline:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=3)
                except asyncio.TimeoutError:
                    continue
                d = json.loads(raw)
                if d.get("id") == mid:
                    return d.get("result", {})
            return None

        async def click_at(x, y):
            for t in ["mousePressed", "mouseReleased"]:
                await cdp("Input.dispatchMouseEvent", {"type": t, "x": x, "y": y, "button": "left", "clickCount": 1})
            await asyncio.sleep(0.3)

        for m in ["Runtime.enable", "DOM.enable", "Input.enable"]:
            await cdp(m)

        # 1. Navigate to fresh page
        print("[1/5] Navigating to fresh chat...")
        await cdp("Page.navigate", {"url": "https://chatgpt.com/"})
        await asyncio.sleep(5)
        # Re-enable after navigation (page context changed)
        for m in ["Runtime.enable", "DOM.enable", "Input.enable"]:
            await cdp(m)
        # Wait for page to fully load
        for i in range(15):
            await asyncio.sleep(1)
            ready = await js("""(() => {
                return !!document.querySelector('[data-testid="composer-plus-btn"]');
            })()""")
            if ready:
                print(f"  Page ready after {i+1}s")
                break
        url = await js("location.href")
        print(f"  URL: {url}")

        # 2. Plus button
        print("[2/5] Opening plus menu...")
        for i in range(5):
            plus = await js("""(() => {
                const btn = document.querySelector('[data-testid="composer-plus-btn"]');
                if (!btn) return null;
                const r = btn.getBoundingClientRect();
                if (r.width < 10) return null;
                return {x: Math.round(r.x+r.width/2), y: Math.round(r.y+r.height/2)};
            })()""")
            if plus: break
            await asyncio.sleep(1)
        if not plus:
            print("ERROR: Plus button not found"); return
        await click_at(plus["x"], plus["y"])
        await asyncio.sleep(0.8)

        # 3. Deep research
        print("[3/5] Selecting Deep research...")
        dr = await js("""(() => {
            const items = document.querySelectorAll('[role="menuitem"], [data-radix-collection-item]');
            for (const el of items) {
                if (el.textContent.toLowerCase().includes('deep research')) {
                    const r = el.getBoundingClientRect();
                    return {x: Math.round(r.x+r.width/2), y: Math.round(r.y+r.height/2), text: el.textContent.trim()};
                }
            }
            return null;
        })()""")
        if not dr:
            print("ERROR: Deep research not found in menu"); return
        print(f'  Found: "{dr["text"]}"')
        await click_at(dr["x"], dr["y"])
        await asyncio.sleep(1)

        # 4. Type and send
        print("[4/5] Sending prompt...")
        await js("""(() => {
            const el = document.querySelector('[role="textbox"][aria-label="Chat with ChatGPT"]');
            if (el) { el.focus(); return 'ok'; }
            return 'not_found';
        })()""")

        prompt = ("Do a deep research report on the Turing Machine. Cover: "
            "(1) Historical context - Turing's 1936 paper, the Entscheidungsproblem, 1930s math. "
            "(2) Formal definition - 7-tuple, tape, head, states, transitions. "
            "(3) Variants - multi-tape, nondeterministic, oracle, universal TM. "
            "(4) Church-Turing thesis, lambda calculus, recursive functions. "
            "(5) Complexity theory - P vs NP, time/space classes. "
            "(6) Modern relevance - quantum computing, hypercomputation, biological computing. "
            "(7) Key papers and milestones from 1936 to present. "
            "Be thorough, cite specific papers, theorems, and dates.")

        await cdp("Input.insertText", {"text": prompt})
        await asyncio.sleep(0.5)
        await cdp("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Enter", "code": "Enter", "text": "\r"})
        await cdp("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Enter", "code": "Enter"})

        # 5. Wait
        print("[5/5] Waiting for deep research (up to 10 min)...")
        for i in range(300):
            await asyncio.sleep(2)
            result = await js("""(() => {
                const stop = document.querySelector('[data-testid="stop-button"]');
                const streaming = !!stop;
                const assistants = document.querySelectorAll('[data-message-author-role="assistant"]');
                if (assistants.length === 0) return {streaming, len: 0, model: null};
                const last = assistants[assistants.length - 1];
                const md = last.querySelector('.markdown');
                const text = md ? md.textContent.trim() : '';
                const model = last.getAttribute('data-message-model-slug') || 'unknown';
                return {streaming, len: text.length, model};
            })()""")
            if not result: continue
            if i % 15 == 0:
                print(f"  [{i*2}s] streaming={result.get('streaming')} len={result.get('len')} model={result.get('model')}")
            if not result.get("streaming") and result.get("len", 0) > 500:
                print(f"\n  Done! {result['len']} chars, model: {result['model']}")
                full = await js("""(() => {
                    const a = document.querySelectorAll('[data-message-author-role="assistant"]');
                    const md = a[a.length-1].querySelector('.markdown');
                    return md ? md.textContent.trim() : null;
                })()""", timeout=30)
                if full:
                    out = os.path.expanduser("~/chatgpt-extension/turing-machine-research.md")
                    with open(out, "w") as f:
                        f.write("# The Turing Machine - Deep Research Report\n")
                        f.write(f"*Generated by ChatGPT Deep Research - {time.strftime('%Y-%m-%d %H:%M')}*\n")
                        f.write(f"*Model: {result.get('model', 'unknown')}*\n\n")
                        f.write(full)
                    print(f"  Saved: {out} ({len(full):,} chars)")
                    print(f"\n  Preview:\n  {full[:400]}...")
                return
        print("Timeout after 10 minutes")

if __name__ == "__main__":
    asyncio.run(main())
