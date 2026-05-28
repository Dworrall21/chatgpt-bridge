#!/usr/bin/env python3
"""Idea 6 retry: Single-image regression comparison.
Take screenshot of current state → save → toggle sidebar → take screenshot → 
send both separately with comparison prompt + conversation context."""
import json, asyncio, urllib.request, websockets, base64, time, os

async def main():
    req = urllib.request.urlopen("http://127.0.0.1:9222/json", timeout=3)
    tabs = json.loads(req.read())
    tab = next((t for t in tabs if "chatgpt.com" in t.get("url","") and "temporary-chat" in t.get("url","")), None)
    if not tab:
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
    
    reg_dir = "/home/david/chatgpt-extension/ui-maps/regression"
    os.makedirs(reg_dir, exist_ok=True)
    
    print("=== Idea 6: Regression Testing (single-image) ===\n")
    
    # Save a BEFORE screenshot (current state on whatever tab is open)
    ss_before = await raw("Page.captureScreenshot", {"format": "jpeg", "quality": 90, "fromSurface": True})
    img_before = base64.b64decode(ss_before["result"]["data"])
    path_before = f"{reg_dir}/before_{time.strftime('%H%M%S')}.jpg"
    with open(path_before, "wb") as fh: fh.write(img_before)
    print(f"BEFORE: {path_before} ({len(img_before)} bytes)")
    url_before = await js("window.location.href")
    print(f"  URL: {url_before[:60] if url_before else '?'}")
    turns_before = await js("(()=>{return document.querySelectorAll('[data-message-author-role]').length})()")
    print(f"  Turns: {turns_before}")
    
    # Now toggle the sidebar
    toggle = await js("(()=>{var b=document.querySelector('[aria-label*=\"Close sidebar\"], [aria-label*=\"Open sidebar\"], [data-testid=\"sidebar-toggle\"]');if(!b)return null;var r=b.getBoundingClientRect();return JSON.stringify({x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)})})()")
    if toggle:
        t = json.loads(toggle)
        print(f"Toggle button at ({t['x']},{t['y']})")
        await raw("Input.dispatchMouseEvent", {"type": "mousePressed", "x": t["x"], "y": t["y"], "button": "left"})
        await raw("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": t["x"], "y": t["y"], "button": "left"})
        await asyncio.sleep(1.5)
    else:
        print("No toggle found — will try clicking sidebar header")
        # Try clicking at the ChatGPT logo area
        await raw("Input.dispatchMouseEvent", {"type": "mousePressed", "x": 50, "y": 15, "button": "left"})
        await raw("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": 50, "y": 15, "button": "left"})
        await asyncio.sleep(1)
    
    # Save AFTER screenshot
    ss_after = await raw("Page.captureScreenshot", {"format": "jpeg", "quality": 90, "fromSurface": True})
    img_after = base64.b64decode(ss_after["result"]["data"])
    path_after = f"{reg_dir}/after_{time.strftime('%H%M%S')}.jpg"
    with open(path_after, "wb") as fh: fh.write(img_after)
    print(f"AFTER: {path_after} ({len(img_after)} bytes)")
    
    # Navigate to fresh temp chat for analysis
    await raw("Page.navigate", {"url": "https://chatgpt.com/?temporary-chat=true"})
    await asyncio.sleep(7)
    await raw("Runtime.enable"); await asyncio.sleep(0.3)
    await raw("Input.enable")
    
    # Step 1: Send BEFORE screenshot and ask for description
    print("\n--- Sending BEFORE screenshot ---")
    pm = await js("(()=>{var e=document.querySelector('[contenteditable=true]');if(!e)return null;var r=e.getBoundingClientRect();return JSON.stringify({x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height)})})()")
    if pm:
        p = json.loads(pm)
        await raw("Input.dispatchMouseEvent", {"type": "mousePressed", "x": p["x"]+p["w"]//2, "y": p["y"]+p["h"]//2, "button": "left"})
        await raw("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": p["x"]+p["w"]//2, "y": p["y"]+p["h"]//2, "button": "left"})
        await asyncio.sleep(0.3)
    
    b64 = base64.b64encode(img_before).decode()
    await js('(()=>{var b=atob('+json.dumps(b64)+');var a=new Uint8Array(b.length);for(var i=0;i<b.length;i++)a[i]=b.charCodeAt(i);var f=new File([a],"before.jpg",{type:"image/jpeg"});var dt=new DataTransfer();dt.items.add(f);var pm=document.querySelector("[contenteditable=true]");pm.focus();pm.dispatchEvent(new ClipboardEvent("paste",{clipboardData:dt,bubbles:true,cancelable:true}));return dt.files.length})()')
    await asyncio.sleep(2)
    
    desc_prompt = json.dumps("Describe this screenshot of ChatGPT in detail. Note: sidebar state (open/closed?), visible elements, layout.")
    await js("(()=>{var pm=document.querySelector('[contenteditable=true]');if(!pm)return;pm.focus();var s=window.getSelection();var r=document.createRange();r.selectNodeContents(pm);r.collapse(false);s.removeAllRanges();s.addRange(r);document.execCommand('insertText',false,"+desc_prompt+")})()")
    await asyncio.sleep(1)
    
    for attempt in range(3):
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
        print(f"  [{i*5}s] turns={tn} stop={sp}")
        if tn and tn>=2 and sp=="no":
            await asyncio.sleep(2)
            break
    
    # Step 2: Send AFTER screenshot asking for comparison
    print("\n--- Sending AFTER screenshot ---")
    b64 = base64.b64encode(img_after).decode()
    await js('(()=>{var b=atob('+json.dumps(b64)+');var a=new Uint8Array(b.length);for(var i=0;i<b.length;i++)a[i]=b.charCodeAt(i);var f=new File([a],"after.jpg",{type:"image/jpeg"});var dt=new DataTransfer();dt.items.add(f);var pm=document.querySelector("[contenteditable=true]");pm.focus();pm.dispatchEvent(new ClipboardEvent("paste",{clipboardData:dt,bubbles:true,cancelable:true}));return dt.files.length})()')
    await asyncio.sleep(2)
    
    compare_prompt = json.dumps("Compare this screenshot (the 'after' state) with the 'before' screenshot I previously shared. What changed? Did the sidebar toggle open/close? List all visual differences between the two screenshots.")
    await js("(()=>{var pm=document.querySelector('[contenteditable=true]');if(!pm)return;pm.focus();var s=window.getSelection();var r=document.createRange();r.selectNodeContents(pm);r.collapse(false);s.removeAllRanges();s.addRange(r);document.execCommand('insertText',false,"+compare_prompt+")})()")
    await asyncio.sleep(1)
    
    for attempt in range(3):
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
        print(f"  [{i*5}s] turns={tn} stop={sp}")
        if tn and tn>=2 and sp=="no":
            await asyncio.sleep(2)
            # Get comparison text
            cmp = await js("(()=>{var a=document.querySelectorAll('[data-message-author-role=\"assistant\"]');if(!a.length)return '';return a[a.length-1].textContent||''})()")
            if cmp:
                print(f"\n=== Comparison ({len(cmp)} chars) ===\n{cmp[:500]}\n...\n{cmp[-200:]}")
                with open(f"{reg_dir}/comparison_{time.strftime('%H%M%S')}.txt", "w") as fh: fh.write(cmp)
            break

asyncio.run(main())
