#!/usr/bin/env python3
"""Detailed exploration of the 'More' sidebar menu."""
import json, asyncio, urllib.request, websockets, base64, time, os, re

SCREENSHOT_DIR = "/home/david/chatgpt-extension/ui-maps/exploration"
os.makedirs(SCREENSHOT_DIR, exist_ok=True)

async def main():
    req = urllib.request.urlopen("http://127.0.0.1:9222/json", timeout=3)
    tabs = json.loads(req.read())
    tab = next((t for t in tabs if "chatgpt.com" in t.get("url","")), None)
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
    
    # Navigate fresh
    await raw("Page.navigate", {"url": "https://chatgpt.com/?temporary-chat=true"})
    await asyncio.sleep(7)
    await raw("Runtime.enable"); await asyncio.sleep(0.3)
    await raw("Input.enable")
    
    ts = time.strftime("%H%M%S")
    
    # 1. Click "More" at known coordinates (47, 750)
    print("=== Exploring: More Menu ===\n")
    print("Clicking 'More' at (47, 750)...")
    await raw("Input.dispatchMouseEvent", {"type": "mousePressed", "x": 47, "y": 750, "button": "left"})
    await raw("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": 47, "y": 750, "button": "left"})
    await asyncio.sleep(1.5)
    
    # 2. Screenshot the open menu
    ss = await raw("Page.captureScreenshot", {"format": "jpeg", "quality": 95, "fromSurface": True})
    img = base64.b64decode(ss["result"]["data"])
    with open(f"{SCREENSHOT_DIR}/more_menu_{ts}.jpg", "wb") as fh: fh.write(img)
    print(f"Screenshot: {len(img)} bytes\n")
    
    # 3. Ask ChatGPT to enumerate EVERY item in the menu
    prompt = """This is a screenshot of ChatGPT with the "More" popup menu open in the sidebar.
    
    List EVERY item visible in the popup menu. For each item:
    1. The full text/label
    2. Any icon it has (if visible)
    3. The approximate position in the menu (top, middle, bottom)
    4. What you think it does
    
    Also:
    5. Is there a scrollbar? Can you see any items that are partially hidden?
    6. How many total items are visible?
    7. Is there anything at the bottom of the menu (like settings links)?
    
    The viewport is 1280x891 pixels. Be THOROUGH — read every label completely."""
    
    print("Analyzing menu contents...")
    result = await send_vision(img, prompt)
    
    if result:
        print(f"\n=== MORE MENU INVENTORY ({len(result)} chars) ===\n")
        # Print full result
        print(result)
        
        with open(f"{SCREENSHOT_DIR}/more_menu_{ts}.txt", "w") as fh: fh.write(result)
        
        # Now try clicking individual items in the menu
        print("\n\n=== Exploring More Menu Items ===\n")
        
        # Parse the analysis for item labels
        lines = result.split("\n")
        items_found = []
        for line in lines:
            line = line.strip()
            # Look for list items or named items
            if line and len(line) > 3 and not line.startswith("#") and not line.startswith("The") and not line.startswith("I"):
                # Check if it looks like a menu item name
                pass
        
        # Ask vision model for coordinates of each menu item
        coord_prompt = f"""The viewport is 1280x891 pixels. The "More" popup menu is open at the bottom-left of the sidebar.
        
        List every clickable item in this menu. For each item, return:
        {{"items": [
          {{"label": "item name", "x": center_x, "y": center_y, "w": width, "h": height}},
          ...
        ]}}
        
        Return the array even if some items have approximate coordinates.
        If there are scrollable items, include the ones currently visible."""
        
        result2 = await send_vision(img, coord_prompt)
        if result2:
            print(f"\n=== Menu Item Coordinates ({len(result2)} chars) ===\n")
            print(result2[:500])
            with open(f"{SCREENSHOT_DIR}/more_menu_coords_{ts}.txt", "w") as fh: fh.write(result2)
            
            # Try clicking first visible menu item
            try:
                items_json = json.loads(result2)
                if "items" in items_json:
                    items = items_json["items"]
                    print(f"\nFound {len(items)} items. Clicking first: '{items[0].get('label','?')}'")
                    await raw("Input.dispatchMouseEvent", {"type": "mousePressed", "x": items[0]["x"], "y": items[0]["y"], "button": "left"})
                    await raw("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": items[0]["x"], "y": items[0]["y"], "button": "left"})
                    await asyncio.sleep(2)
                    ss3 = await raw("Page.captureScreenshot", {"format": "jpeg", "quality": 90, "fromSurface": True})
                    img3 = base64.b64decode(ss3["result"]["data"])
                    with open(f"{SCREENSHOT_DIR}/more_first_item_{ts}.jpg", "wb") as fh: fh.write(img3)
                    print(f"After-click screenshot: {len(img3)} bytes")
                    
                    result3 = await send_vision(img3, f"I clicked on '{items[0].get('label','?')}' from the More menu. What happened? Describe the new state.")
                    if result3:
                        print(f"\nResult: {result3[:300]}...")
                        with open(f"{SCREENSHOT_DIR}/more_first_item_{ts}.txt", "w") as fh: fh.write(result3)
            except json.JSONDecodeError:
                print("Could not parse items JSON")
    else:
        print("No response from vision model")

asyncio.run(main())
