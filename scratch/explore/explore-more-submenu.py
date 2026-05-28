#!/usr/bin/env python3
"""Explore the 'More' submenu in the plus button menu — using vision to find exact chevron position."""
import json, asyncio, urllib.request, websockets, base64, time, os, re

SCREENSHOT_DIR = "/home/david/chatgpt-extension/ui-maps/exploration"
os.makedirs(SCREENSHOT_DIR, exist_ok=True)

async def main():
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
    
    await raw("Runtime.enable"); await asyncio.sleep(0.3)
    await raw("Input.enable")
    await raw("Emulation.setDeviceMetricsOverride", {"width": 1280, "height": 891, "deviceScaleFactor": 1, "mobile": False})
    
    # Navigate fresh
    await raw("Page.navigate", {"url": "https://chatgpt.com/"})
    await asyncio.sleep(8)
    await raw("Runtime.enable"); await asyncio.sleep(0.3)
    await raw("Input.enable")
    
    ts = time.strftime("%H%M%S")
    print("=== Exploring: Plus Menu 'More' Submenu ===\n")
    
    # Step 1: Open the plus menu using DOM position
    plus = await js("(()=>{var b=document.querySelector('button[data-testid=\"composer-plus-btn\"]');if(!b)return null;var r=b.getBoundingClientRect();return JSON.stringify({x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)})})()")
    print(f"Plus button: {plus}")
    if plus:
        p = json.loads(plus)
        await raw("Input.dispatchMouseEvent", {"type": "mousePressed", "x": p["x"], "y": p["y"], "button": "left"})
        await raw("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": p["x"], "y": p["y"], "button": "left"})
        await asyncio.sleep(1)
    
    # Step 2: Ask vision model to locate the "More" item's chevron precisely
    ss1 = await raw("Page.captureScreenshot", {"format": "jpeg", "quality": 95, "fromSurface": True})
    img1 = base64.b64decode(ss1["result"]["data"])
    with open(f"{SCREENSHOT_DIR}/more_menu_open_{ts}.jpg", "wb") as fh: fh.write(img1)
    
    find_prompt = """The ChatGPT plus menu is open. Find the "More" menu item (the one with three-dot icon, sixth item in the list).
    
    It has a right-facing chevron arrow (>) on the far right edge.
    
    Return EXACT coordinates for the CHEVRON (the > icon on the right side), NOT the center of the item:
    {"found":true,"label":"more_chevron","chevron_x":x,"chevron_y":y,"item_x":center_x,"item_y":center_y,"w":item_width,"h":item_height}
    
    If you can't find it: {"found":false,"reason":"..."}
    
    The viewport is 1280x891 pixels. Be precise — the chevron is at the right edge of the menu item."""
    
    result = await send_vision(img1, find_prompt)
    if result:
        print(f"\nVision response ({len(result)} chars):\n{result[:400]}\n")
        with open(f"{SCREENSHOT_DIR}/more_chevron_find_{ts}.txt", "w") as fh: fh.write(result)
        
        # Parse coordinates
        coords = None
        for cand in re.findall(r'\{[^}]*\}', result.replace("\n"," ")):
            try:
                p = json.loads(cand)
                if p.get("found") and "chevron_x" in p: coords = p; break
            except: pass
        
        if coords:
            cx, cy = coords["chevron_x"], coords["chevron_y"]
            print(f"\nChevron at ({cx}, {cy})")
            
            # Click the chevron
            await raw("Input.dispatchMouseEvent", {"type": "mousePressed", "x": cx, "y": cy, "button": "left"})
            await raw("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": cx, "y": cy, "button": "left"})
            await asyncio.sleep(1.5)
            
            # Screenshot the submenu
            ss2 = await raw("Page.captureScreenshot", {"format": "jpeg", "quality": 95, "fromSurface": True})
            img2 = base64.b64decode(ss2["result"]["data"])
            with open(f"{SCREENSHOT_DIR}/more_submenu_open_{ts}.jpg", "wb") as fh: fh.write(img2)
            print(f"Submenu screenshot: {len(img2)} bytes")
            
            # Ask ChatGPT to list everything in the submenu
            list_prompt = """The "More" submenu is open in ChatGPT's plus button menu. 
            
            List EVERY item visible in this submenu. Be thorough — read every label.
            
            For each item:
            1. The full text label
            2. Any icon
            3. The position in the submenu
            4. What it does (infer from label)
            
            Also: Is there a scrollbar? How many items total? Are there any sections or dividers?"""
            
            analysis = await send_vision(img2, list_prompt)
            if analysis:
                print(f"\n=== MORE SUBMENU CONTENTS ({len(analysis)} chars) ===\n")
                print(analysis)
                with open(f"{SCREENSHOT_DIR}/more_submenu_list_{ts}.txt", "w") as fh: fh.write(analysis)
                
                # Now try clicking the first item in the submenu
                print(f"\n\n=== Clicking First Submenu Item ===\n")
                
                # Ask for coordinates of each submenu item
                item_coords_prompt = f"""The "More" submenu is open. List every item with its coordinates:
                Return as JSON array: [{{"label":"item_name","x":center_x,"y":center_y,"w":width,"h":height}}]
                The viewport is 1280x891 pixels."""
                
                items_result = await send_vision(img2, item_coords_prompt)
                if items_result:
                    print(f"Submenu items ({len(items_result)} chars):\n{items_result[:300]}")
                    with open(f"{SCREENSHOT_DIR}/more_submenu_items_{ts}.txt", "w") as fh: fh.write(items_result)
                    
                    # Parse and click first item
                    try:
                        items_json = json.loads(items_result)
                        items_arr = items_json if isinstance(items_json, list) else items_json.get("items", [])
                        if items_arr and len(items_arr) > 0:
                            first = items_arr[0]
                            print(f"Clicking: '{first.get('label','?')}' at ({first['x']}, {first['y']})")
                            await raw("Input.dispatchMouseEvent", {"type": "mousePressed", "x": first["x"], "y": first["y"], "button": "left"})
                            await raw("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": first["x"], "y": first["y"], "button": "left"})
                            await asyncio.sleep(2)
                            
                            ss3 = await raw("Page.captureScreenshot", {"format": "jpeg", "quality": 90, "fromSurface": True})
                            img3 = base64.b64decode(ss3["result"]["data"])
                            with open(f"{SCREENSHOT_DIR}/more_first_item_click_{ts}.jpg", "wb") as fh: fh.write(img3)
                            
                            result3 = await send_vision(img3, f"I clicked '{first.get('label','?')}' from the More submenu. What happened?")
                            if result3:
                                print(f"\nResult: {result3[:300]}")
                                with open(f"{SCREENSHOT_DIR}/more_first_item_result_{ts}.txt", "w") as fh: fh.write(result3)
                    except (json.JSONDecodeError, KeyError, IndexError) as e:
                        print(f"Could not parse/click: {e}")
        else:
            print("Could not find chevron coordinates")
            if result:
                print(f"Trying to parse coordinates from full response...")
                # Try broader parsing
                nums = re.findall(r'chevron_x["\']?\s*:\s*(\d+)', result)
                nums2 = re.findall(r'chevron_y["\']?\s*:\s*(\d+)', result)
                if nums and nums2:
                    cx, cy = int(nums[0]), int(nums2[0])
                    print(f"Regex-parsed chevron at ({cx}, {cy})")
                    await raw("Input.dispatchMouseEvent", {"type": "mousePressed", "x": cx, "y": cy, "button": "left"})
                    await raw("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": cx, "y": cy, "button": "left"})
                    await asyncio.sleep(1.5)
                    ss2 = await raw("Page.captureScreenshot", {"format": "jpeg", "quality": 95, "fromSurface": True})
                    img2 = base64.b64decode(ss2["result"]["data"])
                    with open(f"{SCREENSHOT_DIR}/more_submenu_open_fallback_{ts}.jpg", "wb") as fh: fh.write(img2)
                    print(f"Fallback screenshot: {len(img2)} bytes")

asyncio.run(main())
