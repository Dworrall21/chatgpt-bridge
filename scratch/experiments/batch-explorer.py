#!/usr/bin/env python3
"""
Vision-guided batch exploration of ALL unexplored ChatGPT tools.
For each target: screenshot → ask ChatGPT for coordinates → click → analyze result.
"""
import json, asyncio, urllib.request, websockets, base64, time, os, re

SCREENSHOT_DIR = "/home/david/chatgpt-extension/ui-maps/exploration"
os.makedirs(SCREENSHOT_DIR, exist_ok=True)

# All unexplored tools with descriptions for the vision model
TARGETS = [
    ("search_chats", "the 'Search chats' icon/button in the left sidebar"),
    ("library", "the 'Library' button in the left sidebar"),
    ("apps", "the 'Apps' button in the left sidebar"),
    ("codex", "the 'Codex' button in the left sidebar"),
    ("sidebar_more", "the 'More' button at the bottom of the sidebar navigation list"),
    ("explore_gpts", "the 'Explore GPTs' button in the GPTs section of the sidebar"),
    ("profile", "the user account/profile area at the bottom-left of the sidebar, shows email or name"),
    ("plus_button", "the + plus button at the bottom-left of the chat composer input area"),
    ("model_selector", "the model selector button in the composer, currently showing 'Thinking'"),
    ("microphone", "the microphone/voice input button in the composer"),
    ("create_image", "the 'Create an image' quick action button below the composer"),
    ("write_edit", "the 'Write or edit' quick action button below the composer"),
    ("look_up", "the 'Look something up' quick action button below the composer"),
]

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
        for i in range(12):
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
    
    # Setup
    await raw("Runtime.enable"); await asyncio.sleep(0.3)
    await raw("Input.enable")
    await raw("Emulation.setDeviceMetricsOverride", {"width": 1280, "height": 891, "deviceScaleFactor": 1, "mobile": False})
    await raw("Page.navigate", {"url": "https://chatgpt.com/?temporary-chat=true"})
    await asyncio.sleep(7)
    await raw("Runtime.enable"); await asyncio.sleep(0.3)
    await raw("Input.enable")
    
    results = {}
    ts = time.strftime("%H%M%S")
    
    print(f"=== Batch Vision-Guided Exploration ({len(TARGETS)} targets) ===\n")
    
    for name, desc in TARGETS:
        print(f"\n[{name}] {desc}")
        
        # 1. Take screenshot
        ss = await raw("Page.captureScreenshot", {"format": "jpeg", "quality": 90, "fromSurface": True})
        img = base64.b64decode(ss["result"]["data"])
        
        # 2. Ask ChatGPT where the button is
        coord_prompt = f"""The viewport is 1280x891 pixels. Find {desc} and return its bounding box in this EXACT JSON format:
{{"found":true,"label":"{name}","x":center_x,"y":center_y,"w":width_px,"h":height_px}}
If you can't find it: {{"found":false,"reason":"why not"}}
Be precise — x,y MUST be the CENTER of the element in the 1280x891 viewport."""
        
        result = await send_vision(img, coord_prompt)
        if not result:
            print(f"  ❌ No response from vision model")
            continue
        
        # 3. Parse coordinates
        coords = None
        for cand in re.findall(r'\{[^}]*\}', result.replace("\n"," ")):
            try:
                p = json.loads(cand)
                if "found" in p and "x" in p:
                    coords = p; break
            except: pass
        
        if not coords:
            print(f"  ❌ Could not parse coordinates from response: {result[:120]}...")
            results[name] = {"status": "parse_failed", "response": result[:200]}
            continue
        
        if not coords.get("found", True):
            print(f"  ⏭️  Not found: {coords.get('reason','')}")
            results[name] = {"status": "not_found", "reason": coords.get("reason","")}
            continue
        
        print(f"  🎯 Found at ({coords['x']}, {coords['y']})")
        
        # 4. Click at coordinates
        await click_coords(coords["x"], coords["y"])
        
        # 5. Take after screenshot
        ss2 = await raw("Page.captureScreenshot", {"format": "jpeg", "quality": 90, "fromSurface": True})
        img2 = base64.b64decode(ss2["result"]["data"])
        with open(f"{SCREENSHOT_DIR}/{name}_{ts}.jpg", "wb") as fh: fh.write(img2)
        
        # 6. Ask what happened
        change_prompt = f"""I clicked '{name}'. What changed in the page? 
Describe the current state. Did a menu open? Did the page navigate?
If any new elements/tools/menus appeared, list every item visible inside them."""
        
        analysis = await send_vision(img2, change_prompt)
        if analysis:
            print(f"  📝 Analysis ({len(analysis)} chars): {analysis[:200]}...")
            results[name] = {"status": "clicked", "analysis": analysis, "coords": coords}
            
            # Save analysis
            with open(f"{SCREENSHOT_DIR}/{name}_{ts}.txt", "w") as fh: fh.write(analysis)
        else:
            print(f"  ⚠️ Clicked but no analysis received")
            results[name] = {"status": "clicked_no_analysis"}
    
    # Summary
    print("\n\n" + "=" * 60)
    print("EXPLORATION COMPLETE")
    print("=" * 60)
    for name, r in results.items():
        status = r.get("status","?")
        coords = r.get("coords",{})
        print(f"  {name}: {status}" + (f" @ ({coords.get('x')},{coords.get('y')})" if coords else ""))

asyncio.run(main())
