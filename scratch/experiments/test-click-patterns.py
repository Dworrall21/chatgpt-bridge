#!/usr/bin/env python3
"""Test different click patterns for selecting content inside the iframe."""
import json, asyncio, urllib.request, websockets, time

CID = "6a1729b2-3480-83e8-b41d-390aed8b8cf8"

async def main():
    msg_id = [0]
    req = urllib.request.urlopen("http://127.0.0.1:9222/json", timeout=3)
    tabs = json.loads(req.read())
    tab = next(t for t in tabs if t.get("url","").startswith("https://chatgpt.com/"))
    ws_url = "ws://127.0.0.1:9222/devtools/page/" + tab["id"]
    async with websockets.connect(ws_url, max_size=2**22) as ws:
        async def raw(method, params=None):
            msg_id[0] += 1; mid = msg_id[0]
            cmd = {"id": mid, "method": method}
            if params: cmd["params"] = params
            await ws.send(json.dumps(cmd))
            deadline = time.time() + 10
            while time.time() < deadline:
                try: rd = await asyncio.wait_for(ws.recv(), timeout=5)
                except asyncio.TimeoutError: continue
                d = json.loads(rd)
                if d.get("id") == mid: return d
            return None
        
        async def js(expr):
            r = await raw("Runtime.evaluate", {"expression": expr, "returnByValue": True})
            if r: return r.get("result",{}).get("result",{}).get("value")
            return None
        
        async def click(x, y, count=1):
            for etype in ["mouseMoved","mousePressed","mouseReleased"]:
                await raw("Input.dispatchMouseEvent",
                    {"type": etype, "x": x, "y": y, "button": "left", "clickCount": count})
            await asyncio.sleep(0.3)
        
        async def ctrl_a():
            for kt in ["rawKeyDown","keyUp"]:
                await raw("Input.dispatchKeyEvent",
                    {"type": kt, "modifiers": 2, "key": "a", "code": "KeyA"})
            await asyncio.sleep(0.3)
        
        async def ctrl_c():
            for kt in ["rawKeyDown","keyUp"]:
                await raw("Input.dispatchKeyEvent",
                    {"type": kt, "modifiers": 2, "key": "c", "code": "KeyC"})
            await asyncio.sleep(0.3)
        
        async def extract(method_name):
            """Try extraction and return content."""
            # Monkey-patch clipboard
            await js("window.__cc='';navigator.clipboard.writeText=function(t){window.__cc=t;return Promise.resolve()}")
            # Also try interception of copy event
            await js("document.addEventListener('copy',function(e){window.__cc=window.getSelection().toString()})")
            
            await ctrl_a()
            sel_before = await js("window.getSelection().toString()") or ""
            await ctrl_c()
            monkey = await js("window.__cc") or ""
            clip = await js("navigator.clipboard.readText()") or ""
            sel_after = await js("window.getSelection().toString()") or ""
            
            success = len(monkey) if len(monkey) > len(clip) else len(clip)
            if len(sel_after) > success: success = len(sel_after)
            
            print(f"  [{method_name:25s}] sel={len(sel_before)}/{len(sel_after)} monkey={len(monkey)} clip={len(clip)} best={success}")
            if success > 100:
                text = monkey or clip or sel_after
                print(f"  >>> GOT TEXT! ({success} chars)")
                print(f"  >>> Preview: {text[:200]}")
                return text
            return ""
        
        await raw("Runtime.enable")
        await raw("Input.enable")
        await raw("Page.enable")
        
        # Get iframe position
        ifr_str = await js("(()=>{var f=document.querySelectorAll('iframe[src*=deep_research]');if(!f.length)return null;var r=f[0].getBoundingClientRect();return JSON.stringify({x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height)})})()")
        if not ifr_str or ifr_str == "null":
            print("No iframe found")
            return
        ifr = json.loads(ifr_str)
        cx = ifr["x"] + ifr["w"]//2
        cy = ifr["y"] + ifr["h"]//2
        print(f"Iframe: ({ifr['x']},{ifr['y']}) {ifr['w']}x{ifr['h']}  Center: ({cx},{cy})")
        
        # Scroll container to top
        await js("(()=>{var a=document.querySelectorAll('*');for(var i=0;i<a.length;i++){var e=a[i];var s=window.getComputedStyle(e);if((s.overflowY=='scroll'||s.overflowY=='auto')&&e.scrollHeight>1000){e.scrollTop=0;return}}})()")
        await asyncio.sleep(1)
        
        all_text = ""
        
        # --- Test 1: Triple-click center of iframe ---
        await click(cx, cy, 3)
        all_text = await extract("triple-click center")
        if all_text: return
        
        # --- Test 2: Single click then double click ---
        await click(cx, cy, 1)
        await asyncio.sleep(0.3)
        await click(cx, cy, 2)
        all_text = await extract("click+double-click")
        if all_text: return
        
        # --- Test 3: Click top-left of iframe then Ctrl+A ---
        await click(ifr["x"]+20, ifr["y"]+20, 1)
        all_text = await extract("click top-left")
        if all_text: return
        
        # --- Test 4: Click offset into iframe (text area) ---
        await click(ifr["x"]+ifr["w"]//3, ifr["y"]+ifr["h"]//3, 1)
        all_text = await extract("click offset 1/3")
        if all_text: return
        
        # --- Test 5: Focus parent first, Tab into iframe ---
        # Click the page title area first to focus parent
        await click(400, 50, 1)
        await asyncio.sleep(0.3)
        # Tab into iframe
        for kt in ["rawKeyDown","keyUp"]:
            await raw("Input.dispatchKeyEvent", {"type": kt, "key": "Tab", "code": "Tab"})
        await asyncio.sleep(0.5)
        all_text = await extract("Tab into iframe")
        if all_text: return
        
        # --- Test 6: Mouse down + drag across iframe ---
        await raw("Input.dispatchMouseEvent", {"type": "mousePressed", "x": ifr["x"]+10, "y": ifr["y"]+50, "button": "left", "clickCount": 1})
        for step in range(5):
            await raw("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": ifr["x"]+10 + step*50, "y": ifr["y"]+50})
        await raw("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": ifr["x"]+260, "y": ifr["y"]+50, "button": "left", "clickCount": 1})
        await asyncio.sleep(0.3)
        all_text = await extract("drag-select text")
        if all_text: return
        
        # --- Test 7: Click "Show more" button first (if visible) ---
        show_more = await js("(()=>{var btns=document.querySelectorAll('button');for(var i=0;i<btns.length;i++){if((btns[i].textContent||'').indexOf('Show more')>=0){var r=btns[i].getBoundingClientRect();return JSON.stringify({x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)})}}return null})()")
        if show_more and show_more != "null":
            sm = json.loads(show_more)
            print(f"  Clicking 'Show more' at ({sm['x']}, {sm['y']})")
            await click(sm["x"], sm["y"], 1)
            await asyncio.sleep(1)
            all_text = await extract("after Show more")
            if all_text: return
        
        # --- Test 8: Click response area below iframe (the action buttons area) ---
        await click(cx, ifr["y"]+ifr["h"]+20, 1)
        all_text = await extract("click below iframe")
        if all_text: return
        
        print("\n=== None of the methods worked ===")
        print("Let me check what document.activeElement is after clicking...")
        active = await js("(document.activeElement||{}).tagName")
        print(f"Active element: {active}")

asyncio.run(main())
