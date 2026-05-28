#!/usr/bin/env python3
"""Open More submenu by properly hovering first, then clicking."""
import json, asyncio, urllib.request, websockets, base64, time, os

SCREENSHOT_DIR = "/home/david/chatgpt-extension/ui-maps/exploration"
os.makedirs(SCREENSHOT_DIR, exist_ok=True)

async def main():
    tabs = json.loads(urllib.request.urlopen("http://127.0.0.1:9222/json").read())
    tab = next((t for t in tabs if "chatgpt.com" in t.get("url","") and "temporary-chat" not in t.get("url","")), None)
    if not tab: tab = next((t for t in tabs if "chatgpt.com" in t.get("url","")), None)
    if not tab: print("No tab"); return

    ws = await websockets.connect(tab["webSocketDebuggerUrl"], max_size=2**22)
    mid = [0]
    async def raw(m, p=None):
        mid[0] += 1; c = {"id": mid[0], "method": m}
        if p: c["params"] = p
        await ws.send(json.dumps(c))
        dl = time.time() + 10
        while time.time() < dl:
            try: rd = await asyncio.wait_for(ws.recv(), timeout=5)
            except asyncio.TimeoutError: continue
            d = json.loads(rd)
            if d.get("id") == mid[0]: return d
        return None
    
    async def js(e):
        r = await raw("Runtime.evaluate", {"expression": e, "returnByValue": True, "awaitPromise": True})
        return r.get("result",{}).get("result",{}).get("value") if r else None
    
    async def send_vision(img_data, prompt_text):
        pm = await js("(()=>{var e=document.querySelector('[contenteditable=true]');if(!e)return null;var r=e.getBoundingClientRect();return JSON.stringify({x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height)})})()")
        if pm:
            p = json.loads(pm)
            await raw("Input.dispatchMouseEvent", {"type": "mousePressed", "x": p["x"]+p["w"]//2, "y": p["y"]+p["h"]//2, "button": "left"})
            await raw("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": p["x"]+p["w"]//2, "y": p["y"]+p["h"]//2, "button": "left"})
        b64 = base64.b64encode(img_data).decode()
        await js('(()=>{var b=atob('+json.dumps(b64)+');var a=new Uint8Array(b.length);for(var i=0;i<b.length;i++)a[i]=b.charCodeAt(i);var f=new File([a],"ss.jpg",{type:"image/jpeg"});var dt=new DataTransfer();dt.items.add(f);var pm=document.querySelector("[contenteditable=true]");pm.focus();pm.dispatchEvent(new ClipboardEvent("paste",{clipboardData:dt,bubbles:true,cancelable:true}));return dt.files.length})()')
        await asyncio.sleep(2)
        pj = json.dumps(prompt_text)
        await js("(()=>{var pm=document.querySelector('[contenteditable=true]');if(!pm)return;pm.focus();var s=window.getSelection();var r=document.createRange();r.selectNodeContents(pm);r.collapse(false);s.removeAllRanges();s.addRange(r);document.execCommand('insertText',false,"+pj+")})()")
        for a in range(3):
            await raw("Input.dispatchKeyEvent", {"type": "rawKeyDown", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13})
            await raw("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13})
            await asyncio.sleep(2)
            pt = await js("(()=>{var e=document.querySelector('[contenteditable=true]');return e?e.textContent.trim().slice(0,20):'?'})()")
            if not pt or len(pt)<3: break
            sb = await js("(()=>{var b=document.querySelector('[data-testid=\"send-button\"]');if(!b)return null;return JSON.stringify({x:Math.round(b.getBoundingClientRect().x+b.getBoundingClientRect().width/2),y:Math.round(b.getBoundingClientRect().y+b.getBoundingClientRect().height/2)})})()")
            if sb:
                sbd = json.loads(sb)
                await raw("Input.dispatchMouseEvent", {"type": "mousePressed", "x": sbd["x"], "y": sbd["y"], "button": "left"})
                await raw("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": sbd["x"], "y": sbd["y"], "button": "left"})
                await asyncio.sleep(2)
        for i in range(10):
            await asyncio.sleep(5)
            tn = await js("(()=>{return document.querySelectorAll('[data-message-author-role]').length})()")
            sp = await js("(()=>{var b=document.querySelector('button[data-testid=\"stop-button\"]');return b?'gen':'no'})()")
            if tn and tn>=2 and sp=="no":
                await asyncio.sleep(2)
                resp = await js("(()=>{var a=document.querySelectorAll('[data-message-author-role=\"assistant\"]');if(!a.length)return '';return a[a.length-1].textContent||''})()")
                return resp or ""
        return ""
    
    await raw("Runtime.enable"); await asyncio.sleep(0.3)
    await raw("Input.enable")
    await raw("Emulation.setDeviceMetricsOverride", {"width": 1280, "height": 891, "deviceScaleFactor": 1, "mobile": False})
    await raw("Page.navigate", {"url": "https://chatgpt.com/"})
    await asyncio.sleep(8)
    await raw("Runtime.enable"); await asyncio.sleep(0.3)
    await raw("Input.enable")
    
    ts = time.strftime("%H%M%S")
    print("=== Opening More Submenu via Hover ===\n")
    
    # Open plus menu via DOM
    plus = await js("(()=>{var b=document.querySelector('button[data-testid=\"composer-plus-btn\"]');if(!b)return null;var r=b.getBoundingClientRect();return JSON.stringify({x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)})})()")
    if plus:
        p = json.loads(plus)
        print(f"Plus at ({p['x']}, {p['y']})")
        for _ in range(3):
            await raw("Input.dispatchMouseEvent", {"type": "mousePressed", "x": p["x"], "y": p["y"], "button": "left"})
            await raw("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": p["x"], "y": p["y"], "button": "left"})
        await asyncio.sleep(1)
        
        # Hover over More item step-by-step
        # First hover over items above to get there naturally
        print("Moving mouse step by step to More...")
        for item_y in [450, 485, 510, 530, 565, 600, 620, 640]:
            await raw("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": 478, "y": item_y})
            await asyncio.sleep(0.15)
        
        # Now hover ON the More item (stay for CSS hover to activate)
        print("Hovering on More at (478, 640)...")
        for _ in range(3):
            await raw("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": 478, "y": 640})
            await asyncio.sleep(0.3)
        
        # Take screenshot to confirm hover state
        ss1 = await raw("Page.captureScreenshot", {"format": "jpeg", "quality": 95, "fromSurface": True})
        img1 = base64.b64decode(ss1["result"]["data"])
        with open(f"{SCREENSHOT_DIR}/more_hover_{ts}.jpg", "wb") as fh: fh.write(img1)
        print(f"Hover screenshot: {len(img1)} bytes")
        
        # Now move mouse slightly to the right (where chevron should appear)
        print("Moving to chevron area at (580, 640)...")
        for cx in range(478, 590, 8):
            await raw("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": cx, "y": 640})
            await asyncio.sleep(0.05)
        
        # Now CLICK on the chevron area
        print("Clicking at (580, 640)...")
        await raw("Input.dispatchMouseEvent", {"type": "mousePressed", "x": 580, "y": 640, "button": "left"})
        await raw("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": 580, "y": 640, "button": "left"})
        await asyncio.sleep(0.5)
        
        # Check if submenu appeared
        submenu = await js("(()=>{var menus=document.querySelectorAll('[role=\"menu\"]');return menus.length})()")
        print(f"Menus in DOM: {submenu}")
        
        # Screenshot
        ss2 = await raw("Page.captureScreenshot", {"format": "jpeg", "quality": 95, "fromSurface": True})
        img2 = base64.b64decode(ss2["result"]["data"])
        with open(f"{SCREENSHOT_DIR}/more_submenu_hover_{ts}.jpg", "wb") as fh: fh.write(img2)
        print(f"After-click screenshot: {len(img2)} bytes")
        
        # Vision analysis
        result = await send_vision(img2, """Look at the ChatGPT plus menu area. Is there a SUBMENU open? 
Look for a second menu/popup that appeared to the right of the main plus menu.
If there is a submenu, list EVERY item inside it — full labels, icons, and order.
If there is no submenu, what is visible instead?""")
        if result:
            print(f"\n=== Analysis ({len(result)} chars) ===\n{result}")
            with open(f"{SCREENSHOT_DIR}/more_submenu_hover_{ts}.txt", "w") as fh: fh.write(result)
            
            # If submenu opened, try clicking first item
            if "submenu" in result.lower() or len(result) > 200:
                # Try to get coordinates and click first item
                coord_prompt = """The submenu is open. Return the EXACT coordinates of every item in this submenu:
                [{"label":"name","x":center_x,"y":center_y,"w":width,"h":height}, ...]
                Viewport is 1280x891."""
                
                # Wait a moment and screenshot again
                await asyncio.sleep(1)
                ss3 = await raw("Page.captureScreenshot", {"format": "jpeg", "quality": 95, "fromSurface": True})
                img3 = base64.b64decode(ss3["result"]["data"])
                
                items_result = await send_vision(img3, coord_prompt)
                if items_result:
                    print(f"\nItem coordinates ({len(items_result)} chars):\n{items_result[:400]}")
                    with open(f"{SCREENSHOT_DIR}/more_submenu_items_{ts}.txt", "w") as fh: fh.write(items_result)
        else:
            print("No vision response")

asyncio.run(main())
