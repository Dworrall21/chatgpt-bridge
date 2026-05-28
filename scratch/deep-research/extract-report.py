#!/usr/bin/env python3
"""Extract - do NOT navigate, use current tab state"""
import json, asyncio, urllib.request, websockets, base64, sys, time

CID = "6a1729b2-3480-83e8-b41d-390aed8b8cf8"
OUT = f"/home/david/chatgpt-extension/research-sessions/{CID}/report.md"

async def main():
    msg_id = [0]
    req = urllib.request.urlopen("http://127.0.0.1:9222/json", timeout=3)
    tabs = json.loads(req.read())
    tab = next(t for t in tabs if t.get("url","").startswith("https://chatgpt.com/c/"))
    print(f"Using tab: {tab.get('url','')[:80]}")
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
        
        await raw("Runtime.enable")
        await raw("Input.enable")
        
        # Find iframe center
        ifr_str = await js("(()=>{var f=document.querySelectorAll('iframe[src*=deep_research]');if(!f.length)return null;var r=f[0].getBoundingClientRect();return JSON.stringify({x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)})})()")
        if not ifr_str or ifr_str == "null":
            print("No iframe")
            return
        ifr = json.loads(ifr_str)
        print(f"Iframe: ({ifr['x']}, {ifr['y']})")
        
        # Monkey-patch
        await js("window.__cc='';navigator.clipboard.writeText=function(t){window.__cc=t;return Promise.resolve()}")
        
        # Step 1: Scroll the scroll container to make iframe visible
        await js("(()=>{var a=document.querySelectorAll('*');for(var i=0;i<a.length;i++){var e=a[i];var s=window.getComputedStyle(e);if((s.overflowY=='scroll'||s.overflowY=='auto')&&e.scrollHeight>1000){e.scrollTop=0;return}}})()")
        await asyncio.sleep(2)
        
        # Step 2: Hover to reveal iframe action buttons
        await raw("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": ifr["x"], "y": ifr["y"]-50})
        await asyncio.sleep(0.5)
        
        # Step 3: Focus iframe by clicking inside it
        await raw("Input.dispatchMouseEvent", {"type": "mousePressed", "x": ifr["x"], "y": ifr["y"], "button": "left", "clickCount": 1})
        await raw("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": ifr["x"], "y": ifr["y"], "button": "left", "clickCount": 1})
        await asyncio.sleep(0.5)
        
        # Step 4: Ctrl+A
        await raw("Input.dispatchKeyEvent", {"type": "rawKeyDown", "modifiers": 2, "key": "a", "code": "KeyA"})
        await raw("Input.dispatchKeyEvent", {"type": "keyUp", "modifiers": 2, "key": "a", "code": "KeyA"})
        await asyncio.sleep(0.5)
        
        # Step 5: Ctrl+C
        await raw("Input.dispatchKeyEvent", {"type": "rawKeyDown", "modifiers": 2, "key": "c", "code": "KeyC"})
        await raw("Input.dispatchKeyEvent", {"type": "keyUp", "modifiers": 2, "key": "c", "code": "KeyC"})
        await asyncio.sleep(0.5)
        
        # Step 6: Read clipboard
        content = await js("window.__cc") or ""
        clip = await js("navigator.clipboard.readText()") or ""
        print(f"Monkey: {len(content)} chars")
        print(f"Clipboard: {len(clip)} chars")
        
        text = content or clip
        if len(text) > 100:
            with open(OUT, "w") as f: f.write(text)
            print(f"SAVED: {OUT} ({len(text)} chars)")
            print(text[:500])
        else:
            print("No content. Let me try OCR from the screenshot...")
            
            # Take tall screenshot
            await raw("Runtime.enable")
            await raw("Page.enable")
            
            # Set viewport tall
            await raw("Emulation.setDeviceMetricsOverride", {"width": 1280, "height": 2000, "deviceScaleFactor": 2, "mobile": False})
            await asyncio.sleep(1)
            
            ss = await raw("Page.captureScreenshot", {"format": "jpeg", "quality": 85, "fromSurface": True})
            if ss and ss.get("result",{}).get("data"):
                data = base64.b64decode(ss["result"]["data"])
                with open(f"/tmp/research-tall-{CID[:8]}.jpg", "wb") as f: f.write(data)
                print(f"Tall screenshot: {len(data)} bytes")
                
                # OCR
                from PIL import Image
                import pytesseract
                img = Image.open(f"/tmp/research-tall-{CID[:8]}.jpg")
                ocr_text = pytesseract.image_to_string(img)
                with open(OUT, "w") as f: f.write(ocr_text)
                print(f"OCR SAVED: {OUT} ({len(ocr_text)} chars)")
                print(ocr_text[:500])

asyncio.run(main())
