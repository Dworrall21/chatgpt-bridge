#!/usr/bin/env python3
"""Try the 3 quick action buttons on a regular (non-temp) ChatGPT page."""
import json, asyncio, urllib.request, websockets, base64, time, os, re

SCREENSHOT_DIR = "/home/david/chatgpt-extension/ui-maps/exploration"
os.makedirs(SCREENSHOT_DIR, exist_ok=True)

async def main():
    req = urllib.request.urlopen("http://127.0.0.1:9222/json", timeout=3)
    tabs = json.loads(req.read())
    # Find an existing conversation tab (non-temp)
    tab = next((t for t in tabs if "chatgpt.com" in t.get("url","") and "/c/" in t.get("url","")), None)
    if not tab: tab = next((t for t in tabs if "chatgpt.com" in t.get("url","")), None)
    if not tab: print("No ChatGPT tab"); return
    
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
    
    # Setup
    await raw("Runtime.enable"); await asyncio.sleep(0.3)
    await raw("Input.enable")
    await raw("Emulation.setDeviceMetricsOverride", {"width": 1280, "height": 891, "deviceScaleFactor": 1, "mobile": False})
    
    # Navigate to main ChatGPT (non-temp)
    await raw("Page.navigate", {"url": "https://chatgpt.com/"})
    await asyncio.sleep(7)
    await raw("Runtime.enable"); await asyncio.sleep(0.3)
    await raw("Input.enable")
    
    current_url = await js("window.location.href")
    print(f"Current URL: {current_url[:70] if current_url else '?'}")
    
    TARGETS = [
        ("create_image", "the 'Create an image' quick action button below the composer. It's a horizontal button with an icon."),
        ("write_edit", "the 'Write or edit' quick action button below the composer. It's a horizontal button with an icon."),
        ("look_up", "the 'Look something up' quick action button below the composer. It's a horizontal button with an icon."),
    ]
    
    ts = time.strftime("%H%M%S")
    
    for name, desc in TARGETS:
        print(f"\n--- {name} ---")
        
        ss = await raw("Page.captureScreenshot", {"format": "jpeg", "quality": 90, "fromSurface": True})
        img = base64.b64decode(ss["result"]["data"])
        
        coord_prompt = f"""The viewport is 1280x891 pixels. Find {desc} and return its bounding box:
{{"found":true,"label":"{name}","x":center_x,"y":center_y,"w":width_px,"h":height_px}}
If not found: {{"found":false,"reason":"explain why"}}"""
        
        result = await send_vision(img, coord_prompt)
        if not result:
            print(f"  No response")
            continue
        
        coords = None
        for cand in re.findall(r'\{[^}]*\}', result.replace("\n"," ")):
            try:
                p = json.loads(cand)
                if "found" in p and "x" in p: coords = p; break
            except: pass
        
        if not coords or not coords.get("found", False):
            print(f"  Not found: {coords.get('reason','parse error') if coords else 'no parse'}")
            continue
        
        print(f"  Found at ({coords['x']}, {coords['y']})")
        
        # Click
        await raw("Input.dispatchMouseEvent", {"type": "mousePressed", "x": coords["x"], "y": coords["y"], "button": "left"})
        await raw("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": coords["x"], "y": coords["y"], "button": "left"})
        await asyncio.sleep(2)
        
        ss2 = await raw("Page.captureScreenshot", {"format": "jpeg", "quality": 90, "fromSurface": True})
        img2 = base64.b64decode(ss2["result"]["data"])
        with open(f"{SCREENSHOT_DIR}/{name}_regular_{ts}.jpg", "wb") as fh: fh.write(img2)
        print(f"  After: {len(img2)} bytes")
        
        change_prompt = f"I clicked '{name}'. What happened? Describe the current state."
        analysis = await send_vision(img2, change_prompt)
        if analysis:
            print(f"  Analysis ({len(analysis)} chars): {analysis[:200]}")
            with open(f"{SCREENSHOT_DIR}/{name}_regular_{ts}.txt", "w") as fh: fh.write(analysis)

asyncio.run(main())
