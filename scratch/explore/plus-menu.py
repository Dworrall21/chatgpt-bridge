#!/usr/bin/env python3
"""Open plus menu on fresh ChatGPT page and list all items."""
import json, asyncio, urllib.request, websockets, base64, time

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
    
    await raw("Runtime.enable"); await asyncio.sleep(0.3)
    await raw("Input.enable")
    await raw("Emulation.setDeviceMetricsOverride", {"width": 1280, "height": 891, "deviceScaleFactor": 1, "mobile": False})
    
    # Navigate fresh
    await raw("Page.navigate", {"url": "https://chatgpt.com/"})
    await asyncio.sleep(8)
    await raw("Runtime.enable"); await asyncio.sleep(0.3)
    await raw("Input.enable")
    
    url = await js("window.location.href")
    print(f"URL: {url[:50] if url else '?'}")
    
    # Click + at (478, 831) with fallback
    for attempt in range(5):
        print(f"Click + attempt {attempt+1}...")
        await raw("Input.dispatchMouseEvent", {"type": "mousePressed", "x": 478, "y": 831, "button": "left"})
        await raw("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": 478, "y": 831, "button": "left"})
        await asyncio.sleep(1)
        items = await js("(()=>{return document.querySelectorAll('[role=\"menuitem\"], [data-testid*=\"menu-item\"]').length})()")
        print(f"  Menu items: {items}")
        if items and items > 0: break
        
        # Also try finding the + button coordinates via DOM
        plus = await js("(()=>{var b=document.querySelector('button[data-testid=\"composer-plus-btn\"]');if(!b)return null;var r=b.getBoundingClientRect();return JSON.stringify({x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)})})()")
        print(f"  DOM plus button: {plus}")
        if plus:
            p = json.loads(plus)
            await raw("Input.dispatchMouseEvent", {"type": "mousePressed", "x": p["x"], "y": p["y"], "button": "left"})
            await raw("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": p["x"], "y": p["y"], "button": "left"})
            await asyncio.sleep(1)
            items = await js("(()=>{return document.querySelectorAll('[role=\"menuitem\"]').length})()")
            if items and items > 0: print(f"  Menu items after DOM click: {items}"); break
    
    ss = await raw("Page.captureScreenshot", {"format": "jpeg", "quality": 90, "fromSurface": True})
    img = base64.b64decode(ss["result"]["data"])
    ts = time.strftime("%H%M%S")
    path = f"/home/david/chatgpt-extension/ui-maps/exploration/plus_fresh_{ts}.jpg"
    with open(path, "wb") as fh: fh.write(img)
    print(f"Screenshot: {len(img)} bytes -> {path}")
    
    # Send for analysis
    pm = await js("(()=>{var e=document.querySelector('[contenteditable=true]');if(!e)return null;var r=e.getBoundingClientRect();return JSON.stringify({x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height)})})()")
    if pm:
        p = json.loads(pm)
        await raw("Input.dispatchMouseEvent", {"type": "mousePressed", "x": p["x"]+p["w"]//2, "y": p["y"]+p["h"]//2, "button": "left"})
        await raw("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": p["x"]+p["w"]//2, "y": p["y"]+p["h"]//2, "button": "left"})
    b64 = base64.b64encode(img).decode()
    await js('(()=>{var b=atob('+json.dumps(b64)+');var a=new Uint8Array(b.length);for(var i=0;i<b.length;i++)a[i]=b.charCodeAt(i);var f=new File([a],"ss.jpg",{type:"image/jpeg"});var dt=new DataTransfer();dt.items.add(f);var pm=document.querySelector("[contenteditable=true]");pm.focus();pm.dispatchEvent(new ClipboardEvent("paste",{clipboardData:dt,bubbles:true,cancelable:true}));return dt.files.length})()')
    await asyncio.sleep(2)
    prompt = json.dumps("The plus button menu is open. List EVERY item. For each: label, icon, position. Be thorough.")
    await js("(()=>{var pm=document.querySelector('[contenteditable=true]');if(!pm)return;pm.focus();var s=window.getSelection();var r=document.createRange();r.selectNodeContents(pm);r.collapse(false);s.removeAllRanges();s.addRange(r);document.execCommand('insertText',false,"+prompt+")})()")
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
        print(f"  [{i*5}s] turns={tn} stop={sp}")
        if tn and tn>=2 and sp=="no":
            await asyncio.sleep(2)
            resp = await js("(()=>{var a=document.querySelectorAll('[data-message-author-role=\"assistant\"]');if(!a.length)return '';return a[a.length-1].textContent||''})()")
            if resp:
                print(f"\n=== Plus Menu ({len(resp)} chars) ===\n{resp}")
                with open(f"/home/david/chatgpt-extension/ui-maps/exploration/plus_fresh_{ts}.txt", "w") as fh: fh.write(resp)
            break

asyncio.run(main())
