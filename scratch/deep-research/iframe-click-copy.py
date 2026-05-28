#!/usr/bin/env python3
"""Click inside iframe from its own CDP context, then Ctrl+A → execCommand('copy')."""
import json, asyncio, urllib.request, websockets, time, sys

async def main():
    req = urllib.request.urlopen("http://127.0.0.1:9222/json", timeout=3)
    targets = json.loads(req.read())
    
    iframe_t = next((t for t in targets if "deep_research" in t.get("url","") and "web-sandbox" in t.get("url","")), None)
    if not iframe_t:
        for t in targets:
            u = t.get("url","")
            if "web-sandbox" in u or "oaiusercontent" in u:
                iframe_t = t
                break
    
    if not iframe_t:
        print("No iframe target")
        return
    
    print(f"Iframe: {iframe_t['url'][:60]}")
    
    ws_url = "ws://127.0.0.1:9222/devtools/page/" + iframe_t["id"]
    async with websockets.connect(ws_url, max_size=2**22) as ws:
        mid = [0]
        async def raw(method, params=None):
            mid[0] += 1
            cmd = {"id": mid[0], "method": method}
            if params: cmd["params"] = params
            await ws.send(json.dumps(cmd))
            dl = time.time() + 10
            while time.time() < dl:
                try: rd = await asyncio.wait_for(ws.recv(), timeout=5)
                except asyncio.TimeoutError: continue
                d = json.loads(rd)
                if d.get("id") == mid[0]: return d
            return None
        
        async def js(expr):
            r = await raw("Runtime.evaluate", {"expression": expr, "returnByValue": True})
            if r: return r.get("result",{}).get("result",{}).get("value")
            return None
        
        await raw("Runtime.enable")
        await raw("Input.enable")
        
        # Check iframe document state
        doc = await js("document.readyState")
        body_len = await js("document.body.innerText.length")
        has_inner = await js("document.querySelector('iframe') !== null")
        print(f"Ready: {doc}, bodyText: {body_len}, hasNestedIframe: {has_inner}")
        
        # Monkey-patch capture
        await js("""(() => {
            window.__copied = '';
            var orig = execCopy = document.execCommand;
            document.execCommand = function(cmd) {
                if (cmd === 'copy') {
                    var sel = window.getSelection();
                    window.__copied = sel ? sel.toString() : '';
                }
                return orig.apply(this, arguments);
            };
        })()""")
        
        # Method 1: Ctrl+A from iframe context (no click)
        print("\n--- Method 1: Ctrl+A from iframe context ---")
        await raw("Input.dispatchKeyEvent", {"type": "rawKeyDown", "modifiers": 2, "key": "a", "code": "KeyA", "windowsVirtualKeyCode": 65})
        await raw("Input.dispatchKeyEvent", {"type": "keyUp", "modifiers": 2, "key": "a", "code": "KeyA", "windowsVirtualKeyCode": 65})
        await asyncio.sleep(1)
        
        sel1 = await js("window.getSelection().toString().length")
        cp1 = await js("var r = document.execCommand('copy'); window.__copied.length")
        print(f"  Selection: {sel1}, Copied: {cp1}")
        
        # Method 2: Click then Ctrl+A
        print("\n--- Method 2: Click inside iframe content then Ctrl+A ---")
        await js("window.__copied = ''")
        
        # Get iframe dimensions - click in the center of the content area
        ifr_info = await js("""(() => {
            var f = document.querySelector('iframe');
            if (!f) { var r = document.body.getBoundingClientRect(); return JSON.stringify({x:0,y:0,w:r.width,h:r.height,note:'no nested'}); }
            var r = f.getBoundingClientRect();
            return JSON.stringify({x:r.x, y:r.y, w:r.width, h:r.height, note:'nested'});
        })()""")
        print(f"  Inner/body bounds: {ifr_info}")
        
        # Click at center of content
        if ifr_info:
            info = json.loads(ifr_info)
            cx, cy = info["x"] + info["w"]//2, info["y"] + info["h"]//2
            print(f"  Click at ({cx}, {cy})")
            
            await raw("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": cx, "y": cy})
            await asyncio.sleep(0.3)
            
            # Triple click to select all
            for click_count in [1, 2, 3]:
                await raw("Input.dispatchMouseEvent", {"type": "mousePressed", "x": cx, "y": cy, "button": "left", "clickCount": click_count})
                await raw("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": cx, "y": cy, "button": "left", "clickCount": click_count})
                await asyncio.sleep(0.3)
            
            await asyncio.sleep(1)
            
            sel2 = await js("window.getSelection().toString().length")
            cp2 = await js("var r = document.execCommand('copy'); window.__copied.length")
            print(f"  Selection: {sel2}, Copied: {cp2}")
            
            if cp2 > 0:
                content = await js("window.__copied")
                with open("/tmp/iframe-extracted.txt", "w") as f:
                    f.write(content)
                print(f"  Saved {len(content)} chars to /tmp/iframe-extracted.txt")
                print(f"  First 100: {content[:100]}")
        
        # Method 3: Try focus + Ctrl+A from iframe context
        print("\n--- Method 3: Focus inner iframe via CDP ---")
        await js("window.__copied = ''")
        
        # Try to focus the inner iframe
        focused = await js("""(() => {
            var f = document.querySelector('iframe');
            if (f) { f.focus(); return 'inner focused: ' + f.tagName; }
            return 'no inner iframe';
        })()""")
        print(f"  {focused}")
        
        await asyncio.sleep(0.5)
        
        active = await js("document.activeElement ? document.activeElement.tagName : 'none'")
        print(f"  Active element: {active}")
        
        # Ctrl+A
        await raw("Input.dispatchKeyEvent", {"type": "rawKeyDown", "modifiers": 2, "key": "a", "code": "KeyA", "windowsVirtualKeyCode": 65})
        await raw("Input.dispatchKeyEvent", {"type": "keyUp", "modifiers": 2, "key": "a", "code": "KeyA", "windowsVirtualKeyCode": 65})
        await asyncio.sleep(1)
        
        cp3 = await js("var r = document.execCommand('copy'); window.__copied.length")
        print(f"  Copied: {cp3}")

asyncio.run(main())
