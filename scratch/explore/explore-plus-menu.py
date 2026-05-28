#!/usr/bin/env python3
"""Systematic vision-guided exploration of every option in the plus menu."""
import json, asyncio, urllib.request, websockets, base64, time, os, re

SCREENSHOT_DIR = "/home/david/chatgpt-extension/ui-maps/exploration"
os.makedirs(SCREENSHOT_DIR, exist_ok=True)

# The items we know are in the plus menu
MENU_ITEMS = [
    ("add_photos_files", "the 'Add photos & files' menu item in the plus button dropdown", (478, 450)),
    ("recent_files", "the 'Recent Files' menu item in the plus button dropdown", (478, 485)),
    ("create_image", "the 'Create image' menu item in the plus button dropdown", (478, 530)),
    ("deep_research", "the 'Deep research' menu item in the plus button dropdown", (478, 565)),
    ("web_search", "the 'Web search' menu item in the plus button dropdown", (478, 600)),
    ("more_submenu", "the 'More' menu item in the plus button dropdown (the one with three-dot icon, NOT sidebar)", (478, 640)),
    ("projects_submenu", "the 'Projects' menu item in the plus button dropdown", (478, 680)),
]

async def connect_and_explore():
    req = urllib.request.urlopen("http://127.0.0.1:9222/json", timeout=3)
    tabs = json.loads(req.read())
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
    
    async def click_coords(x, y):
        await raw("Input.dispatchMouseEvent", {"type": "mousePressed", "x": x, "y": y, "button": "left"})
        await raw("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": x, "y": y, "button": "left"})
        await asyncio.sleep(1.5)
    
    async def explore_item(name, desc, click_coords_xy, after_prompt, is_submenu=False):
        print(f"\n{'='*60}")
        print(f"[{name}] {desc.split('.')[0]}")
        print(f"  Clicking at ({click_coords_xy[0]}, {click_coords_xy[1]})...")
        
        # If this is a submenu item, we need to open the submenu
        if is_submenu:
            # For items with chevrons, click the chevron area
            await click_coords(click_coords_xy[0] + 95, click_coords_xy[1])
            await asyncio.sleep(0.5)
        else:
            await click_coords(click_coords_xy[0], click_coords_xy[1])
        
        await asyncio.sleep(1)
        
        ss = await raw("Page.captureScreenshot", {"format": "jpeg", "quality": 90, "fromSurface": True})
        img = base64.b64decode(ss["result"]["data"])
        ts = time.strftime("%H%M%S")
        fname = f"{SCREENSHOT_DIR}/{name}_explored_{ts}.jpg"
        with open(fname, "wb") as fh: fh.write(img)
        print(f"  Screenshot: {len(img)} bytes -> {fname}")
        
        result = await send_vision(img, after_prompt)
        if result:
            print(f"  Analysis ({len(result)} chars):\n  {result[:300]}...\n")
            with open(f"{SCREENSHOT_DIR}/{name}_explored_{ts}.txt", "w") as fh: fh.write(result)
        return result
    
    # Setup
    await raw("Runtime.enable"); await asyncio.sleep(0.3)
    await raw("Input.enable")
    await raw("Emulation.setDeviceMetricsOverride", {"width": 1280, "height": 891, "deviceScaleFactor": 1, "mobile": False})
    
    # Navigate to fresh main page
    await raw("Page.navigate", {"url": "https://chatgpt.com/"})
    await asyncio.sleep(8)
    await raw("Runtime.enable"); await asyncio.sleep(0.3)
    await raw("Input.enable")
    
    url = await js("window.location.href")
    print(f"Base URL: {url[:50] if url else '?'}")
    
    # Open the plus menu first
    print("\nOpening plus menu...")
    # Get live plus button position from DOM
    plus_pos = await js("(()=>{var b=document.querySelector('button[data-testid=\"composer-plus-btn\"]');if(!b)return null;var r=b.getBoundingClientRect();return JSON.stringify({x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)})})()")
    print(f"  Plus button: {plus_pos}")
    if plus_pos:
        p = json.loads(plus_pos)
        await click_coords(p["x"], p["y"])
    else:
        await click_coords(478, 402)  # fallback
    
    # Verify menu opened
    menu_check = await js("(()=>{return document.querySelectorAll('[role=\"menuitem\"]').length})()")
    print(f"  Menu items detected: {menu_check}")
    
    if menu_check and menu_check >= 4:
        # Take screenshot of open menu
        ss = await raw("Page.captureScreenshot", {"format": "jpeg", "quality": 95, "fromSurface": True})
        img = base64.b64decode(ss["result"]["data"])
        ts = time.strftime("%H%M%S")
        with open(f"{SCREENSHOT_DIR}/plus_menu_open_{ts}.jpg", "wb") as fh: fh.write(img)
        print(f"  Menu screenshot: {len(img)} bytes")
    else:
        print("  Menu didn't open! Trying vision-guided plus button...")
        # Use vision to find the plus button
        ss = await raw("Page.captureScreenshot", {"format": "jpeg", "quality": 90, "fromSurface": True})
        img = base64.b64decode(ss["result"]["data"])
        
        coord_prompt = """The viewport is 1280x891 pixels. Find the '+' plus button in the bottom-left of the chat composer and return its center coordinates as JSON: {"found":true,"x":center_x,"y":center_y}. If not found: {"found":false}"""
        result = await send_vision(img, coord_prompt)
        if result:
            for cand in re.findall(r'\{[^}]*\}', result.replace("\n"," ")):
                try:
                    p = json.loads(cand)
                    if p.get("found") and "x" in p:
                        print(f"  Vision-guided plus at ({p['x']}, {p['y']})")
                        await click_coords(p["x"], p["y"])
                        await asyncio.sleep(1)
                        break
                except: pass
    
    # Now explore each menu item
    for name, desc, coords in MENU_ITEMS:
        is_submenu = name in ("recent_files", "more_submenu", "projects_submenu")
        
        if is_submenu:
            prompt = f"""The plus button submenu for '{name}' is open. List EVERY item visible in the submenu.
            Describe the full contents and what each option does."""
        else:
            prompt = f"""I clicked '{name}' in the plus menu. What happened? Describe the current page state.
            What changed? Is there a new interface, dialog, or action visible?
            If the page shows a notification about the action, describe it."""
        
        await explore_item(name, desc, coords, prompt, is_submenu)
        
        # After exploration, re-open plus menu for next item
        if name != MENU_ITEMS[-1][0]:  # Not on last item
            print("  Re-opening plus menu for next item...")
            # The original plus position may have changed - re-query
            plus_pos = await js("(()=>{var b=document.querySelector('button[data-testid=\"composer-plus-btn\"]');if(!b)return null;var r=b.getBoundingClientRect();return JSON.stringify({x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)})})()")
            if plus_pos:
                p = json.loads(plus_pos)
                await click_coords(p["x"], p["y"])
            await asyncio.sleep(1)

asyncio.run(connect_and_explore())
