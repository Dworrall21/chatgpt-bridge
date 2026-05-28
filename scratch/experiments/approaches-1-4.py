#!/usr/bin/env python3
"""Try the 4 approaches for extracting content from the nested iframe."""
import json, asyncio, urllib.request, websockets, time, sys

CID = "6a163342-8044-83e8-88ad-a7303060dcda"

async def main():
    req = urllib.request.urlopen("http://127.0.0.1:9222/json", timeout=3)
    tabs = json.loads(req.read())
    tab = next((t for t in tabs if CID in t.get("url","")), None)
    if not tab:
        print("No tab found"); return
    
    ws_url = "ws://127.0.0.1:9222/devtools/page/" + tab["id"]
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
        await raw("Page.enable")
        
        # Get iframe bounds
        ifr = await js("(()=>{var f=document.querySelector('iframe[src*=deep_research]');if(!f)return null;var r=f.getBoundingClientRect();return JSON.stringify({x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height)})})()")
        if not ifr or ifr == "null":
            print("No iframe"); return
        f = json.loads(ifr)
        print(f"Iframe: ({f['x']},{f['y']}) {f['w']}x{f['h']}")
        
        # Vision-guided download button coords from earlier: (982, 359) or (iframe.x+700, iframe.y+70)
        # Let's use relative to iframe for robustness: top-right = (f.x + f.w - 30, f.y + 15)
        btn_x = f["x"] + f["w"] - 40  # 40px from right
        btn_y = f["y"] + 22           # 22px from top
        print(f"Target btn: ({btn_x}, {btn_y})")
        
        # Monkey-patch to capture copies
        await js("""(() => {
            window.__copied = '';
            var orig = document.execCommand;
            document.execCommand = function(cmd) {
                if (cmd === 'copy') {
                    var sel = window.getSelection();
                    window.__copied = sel ? sel.toString() : '';
                }
                return orig.apply(this, arguments);
            };
            document.__selectAll = function() {
                document.execCommand('selectAll');
            };
        })()""")
        
        result_file = "/tmp/extracted.txt"
        
        # ===== APPROACH 1: Right-click =====
        print("\n" + "="*60)
        print("APPROACH 1: Right-click at download button")
        print("="*60)
        
        # Move mouse
        await raw("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": btn_x, "y": btn_y})
        await asyncio.sleep(0.5)
        
        # Right-click (button: 2)
        await raw("Input.dispatchMouseEvent", {"type": "mousePressed", "x": btn_x, "y": btn_y, "button": "right", "clickCount": 1})
        await raw("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": btn_x, "y": btn_y, "button": "right", "clickCount": 1})
        await asyncio.sleep(1.5)
        
        # Check if any context menu appeared — take screenshot
        ss = await raw("Page.captureScreenshot", {"format": "jpeg", "quality": 80, "fromSurface": True})
        if ss and ss.get("result",{}).get("data"):
            import base64
            with open("/tmp/approach1.jpg", "wb") as fout:
                fout.write(base64.b64decode(ss["result"]["data"]))
            print("Screenshot: /tmp/approach1.jpg")
        
        # Also try left-click after right-click (to dismiss any menu and open the real one)
        # Some dropdowns need a left-click to dismiss context menu
        await raw("Input.dispatchMouseEvent", {"type": "mousePressed", "x": btn_x, "y": btn_y, "button": "left", "clickCount": 1})
        await raw("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": btn_x, "y": btn_y, "button": "left", "clickCount": 1})
        await asyncio.sleep(1.5)
        
        await js("window.__copied = ''")
        
        # Try Ctrl+A then copy
        await raw("Input.dispatchKeyEvent", {"type": "rawKeyDown", "modifiers": 2, "key": "a", "code": "KeyA", "windowsVirtualKeyCode": 65})
        await raw("Input.dispatchKeyEvent", {"type": "keyUp", "modifiers": 2, "key": "a", "code": "KeyA", "windowsVirtualKeyCode": 65})
        await asyncio.sleep(0.5)
        
        await js("document.execCommand('copy')")
        a1 = await js("window.__copied")
        if a1:
            print(f"✅ APPROACH 1 WORKED! {len(a1)} chars")
            with open(result_file, "w") as f: f.write(a1)
            print(f"Saved to {result_file}")
            return
        print("❌ Approach 1: no content")
        
        # ===== APPROACH 2: document.execCommand('selectAll') =====
        print("\n" + "="*60)
        print("APPROACH 2: Click to focus + execCommand('selectAll')")
        print("="*60)
        
        await js("window.__copied = ''")
        
        # Click at center of iframe to focus it
        cx, cy = f["x"] + f["w"]//2, f["y"] + f["h"]//2
        print(f"Click at ({cx}, {cy})")
        
        await raw("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": cx, "y": cy})
        await asyncio.sleep(0.3)
        await raw("Input.dispatchMouseEvent", {"type": "mousePressed", "x": cx, "y": cy, "button": "left", "clickCount": 1})
        await raw("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": cx, "y": cy, "button": "left", "clickCount": 1})
        await asyncio.sleep(0.5)
        
        # Try execCommand('selectAll') - this is a native browser command that should
        # select all in the currently focused frame/document
        sel = await js("""(() => {
            try { document.execCommand('selectAll'); return 'ok'; }
            catch(e) { return 'err: ' + e.message; }
        })()""")
        print(f"  execCommand('selectAll'): {sel}")
        await asyncio.sleep(0.5)
        
        sel_len = await js("(window.getSelection()||{}).toString().length")
        print(f"  Selection length: {sel_len}")
        
        # Try to copy
        await js("document.execCommand('copy')")
        a2 = await js("window.__copied")
        if a2:
            print(f"✅ APPROACH 2 WORKED! {len(a2)} chars")
            with open(result_file, "w") as f: f.write(a2)
            print(f"Saved to {result_file}")
            return
        print("❌ Approach 2: no content")
        
        # ===== APPROACH 3: Page.dispatchKeyEvent =====
        print("\n" + "="*60)
        print("APPROACH 3: Page.dispatchKeyEvent instead of Input.dispatchKeyEvent")
        print("="*60)
        
        await js("window.__copied = ''")
        
        # Click to focus first
        await raw("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": cx, "y": cy})
        await asyncio.sleep(0.3)
        await raw("Input.dispatchMouseEvent", {"type": "mousePressed", "x": cx, "y": cy, "button": "left", "clickCount": 1})
        await raw("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": cx, "y": cy, "button": "left", "clickCount": 1})
        await asyncio.sleep(0.5)
        
        # Use Page.dispatchKeyEvent instead of Input.dispatchKeyEvent
        # Page domain has its own key event dispatching
        await raw("Page.dispatchKeyEvent", {"type": "rawKeyDown", "modifiers": 2, "key": "a", "code": "KeyA", "windowsVirtualKeyCode": 65})
        await raw("Page.dispatchKeyEvent", {"type": "keyUp", "modifiers": 2, "key": "a", "code": "KeyA", "windowsVirtualKeyCode": 65})
        await asyncio.sleep(0.5)
        
        await js("document.execCommand('copy')")
        a3 = await js("window.__copied")
        if a3:
            print(f"✅ APPROACH 3 WORKED! {len(a3)} chars")
            with open(result_file, "w") as f: f.write(a3)
            print(f"Saved to {result_file}")
            return
        print("❌ Approach 3: no content")
        
        # ===== APPROACH 4: Focus inner iframe from outer iframe's context =====
        print("\n" + "="*60)
        print("APPROACH 4: Focus inner iframe + events from outer iframe context")
        print("="*60)
        
        # Find the outer iframe's CDP target
        req2 = urllib.request.urlopen("http://127.0.0.1:9222/json", timeout=3)
        all_targets = json.loads(req2.read())
        iframe_t = next((t for t in all_targets if "deep_research" in t.get("url","") and "web-sandbox" in t.get("url","")), None)
        
        if iframe_t:
            print(f"Outer iframe target found: {iframe_t['url'][:50]}")
            
            async with websockets.connect("ws://127.0.0.1:9222/devtools/page/" + iframe_t["id"], max_size=2**22) as ifr_ws:
                imid = [0]
                async def iraw(method, params=None):
                    imid[0] += 1
                    cmd = {"id": imid[0], "method": method}
                    if params: cmd["params"] = params
                    await ifr_ws.send(json.dumps(cmd))
                    dl = time.time() + 10
                    while time.time() < dl:
                        try: rd = await asyncio.wait_for(ifr_ws.recv(), timeout=5)
                        except asyncio.TimeoutError: continue
                        d = json.loads(rd)
                        if d.get("id") == imid[0]: return d
                    return None
                
                async def ijs(expr):
                    r = await iraw("Runtime.evaluate", {"expression": expr, "returnByValue": True})
                    if r: return r.get("result",{}).get("result",{}).get("value")
                    return None
                
                await iraw("Runtime.enable")
                await iraw("Input.enable")
                
                # Find and focus the inner iframe inside the outer iframe
                inner_focused = await ijs("""(() => {
                    var f = document.querySelector('iframe');
                    if (f) {
                        f.focus();
                        f.contentWindow.focus();
                        return 'focused: ' + f.tagName;
                    }
                    return 'no iframe';
                })()""")
                print(f"  Outer->Inner focus: {inner_focused}")
                await asyncio.sleep(0.5)
                
                # Try sending Ctrl+A from outer iframe's context
                await iraw("Input.dispatchKeyEvent", {"type": "rawKeyDown", "modifiers": 2, "key": "a", "code": "KeyA", "windowsVirtualKeyCode": 65})
                await iraw("Input.dispatchKeyEvent", {"type": "keyUp", "modifiers": 2, "key": "a", "code": "KeyA", "windowsVirtualKeyCode": 65})
                await asyncio.sleep(0.5)
                
                # Try to copy from outer iframe context
                await ijs("""(() => {
                    window.__copied2 = '';
                    try { document.execCommand('copy'); } catch(e) {}
                    try {
                        var inner = document.querySelector('iframe');
                        if (inner && inner.contentWindow) {
                            inner.contentWindow.document.execCommand('copy');
                        }
                    } catch(e) {}
                })()""")
                await asyncio.sleep(0.5)
                
                # Also try from parent page after outer iframe focused inner
                await js("document.execCommand('copy')")
                a4 = await js("window.__copied")
                if a4:
                    print(f"✅ APPROACH 4 WORKED! {len(a4)} chars")
                    with open(result_file, "w") as f: f.write(a4)
                    print(f"Saved to {result_file}")
                    return
                print("❌ Approach 4: no content")
        else:
            print("  No outer iframe target found")
        
        print("\n" + "="*60)
        print("All 4 approaches failed")
        print("="*60)

asyncio.run(main())
