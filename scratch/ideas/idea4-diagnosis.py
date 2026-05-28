#!/usr/bin/env python3
"""Idea 4: Failure Diagnosis — deliberately fail a CDP action and ask ChatGPT what went wrong."""
import json, asyncio, urllib.request, websockets, base64, time, os

async def main():
    req = urllib.request.urlopen("http://127.0.0.1:9222/json", timeout=3)
    tabs = json.loads(req.read())
    tab = next((t for t in tabs if "chatgpt.com" in t.get("url","")), None)
    if not tab: print("No tab"); return

    ws = await websockets.connect("ws://127.0.0.1:9222/devtools/page/" + tab["id"], max_size=2**22)
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
    
    await raw("Runtime.enable")
    await asyncio.sleep(0.3)
    await raw("Input.enable")
    await raw("Emulation.setDeviceMetricsOverride", {"width": 1280, "height": 891, "deviceScaleFactor": 1, "mobile": False})
    
    # Scenario: Try to click a non-existent element at off-screen coordinates
    # This simulates what happens when coordinates go stale
    print("=== Idea 4: Failure Diagnosis ===")
    print("Scenario: CDP click at stale coordinates (button moved or element gone)")
    
    # First take a screenshot of the current state
    await raw("Page.navigate", {"url": "https://chatgpt.com/?temporary-chat=true"})
    await asyncio.sleep(7)
    await raw("Runtime.enable")
    await asyncio.sleep(0.3)
    await raw("Input.enable")
    
    # Click at coordinates where something USED to be but moved
    # The '+' button was at (478, 831) earlier - let's click there and see what happens
    print("\n--- Deliberate failure click at (10, 10) ---")
    await raw("Input.dispatchMouseEvent", {"type": "mousePressed", "x": 10, "y": 10, "button": "left"})
    await raw("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": 10, "y": 10, "button": "left"})
    
    # Check what happened
    hit_before = await js("(()=>{var el=document.elementFromPoint(10,10);return el?el.tagName+'.'+(el.className||'').slice(0,30):'none'})()")
    print(f"Element hit at (10,10): {hit_before}")
    
    # Take screenshot of the "failed" state
    ss = await raw("Page.captureScreenshot", {"format": "jpeg", "quality": 90, "fromSurface": True})
    img = base64.b64decode(ss["result"]["data"])
    fail_path = "/home/david/chatgpt-extension/vision-clicks/failure_state.jpg"
    with open(fail_path, "wb") as fh: fh.write(img)
    print(f"Screenshot saved: {fail_path}")
    
    # Now send to ChatGPT for diagnosis
    print("\n--- Sending failure state to ChatGPT for diagnosis ---")
    
    # Focus PM
    pm = await js("(()=>{var el=document.querySelector('[contenteditable=true]');if(!el)return null;var r=el.getBoundingClientRect();return JSON.stringify({x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height)})})()")
    if pm:
        p = json.loads(pm)
        cx, cy = p["x"] + p["w"]//2, p["y"] + p["h"]//2
        await raw("Input.dispatchMouseEvent", {"type": "mousePressed", "x": cx, "y": cy, "button": "left"})
        await raw("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": cx, "y": cy, "button": "left"})
        await asyncio.sleep(0.5)
    
    b64 = base64.b64encode(img).decode()
    paste_js = '(()=>{var b=atob(' + json.dumps(b64) + ');var a=new Uint8Array(b.length);for(var i=0;i<b.length;i++)a[i]=b.charCodeAt(i);var f=new File([a],"failure.jpg",{type:"image/jpeg"});var dt=new DataTransfer();dt.items.add(f);var pm=document.querySelector("[contenteditable=true]");pm.focus();pm.dispatchEvent(new ClipboardEvent("paste",{clipboardData:dt,bubbles:true,cancelable:true}));return dt.files.length})()'
    await js(paste_js)
    await asyncio.sleep(2)
    
    diagnosis_prompt = json.dumps("I just attempted a CDP mouse click at coordinates (10, 10) on this ChatGPT page. The click hit element: 'HTML' (the page background, nothing interactive). I expected to hit something useful. What went wrong? Look at the screenshot and tell me: 1) What element IS at (10, 10)? 2) What was I probably trying to click? 3) What should the correct coordinates be? Give a brief diagnosis.")
    await js("(()=>{var pm=document.querySelector('[contenteditable=true]');if(!pm)return;pm.focus();var s=window.getSelection();var r=document.createRange();r.selectNodeContents(pm);r.collapse(false);s.removeAllRanges();s.addRange(r);document.execCommand('insertText',false," + diagnosis_prompt + ")})()")
    await asyncio.sleep(1)
    
    # Send
    for attempt in range(3):
        await raw("Input.dispatchKeyEvent", {"type": "rawKeyDown", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13})
        await raw("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13})
        await asyncio.sleep(2)
        pm_txt = await js("(()=>{var el=document.querySelector('[contenteditable=true]');return el?el.textContent.trim().slice(0,20):'?'})()")
        if not pm_txt or len(pm_txt) < 3: break
        sb = await js("(()=>{var b=document.querySelector('[data-testid=\"send-button\"]');if(!b)return null;return JSON.stringify({x:Math.round(b.getBoundingClientRect().x+b.getBoundingClientRect().width/2),y:Math.round(b.getBoundingClientRect().y+b.getBoundingClientRect().height/2)})})()")
        if sb:
            sbd = json.loads(sb)
            await raw("Input.dispatchMouseEvent", {"type": "mousePressed", "x": sbd["x"], "y": sbd["y"], "button": "left"})
            await raw("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": sbd["x"], "y": sbd["y"], "button": "left"})
            await asyncio.sleep(2)
    
    print("Waiting for diagnosis...")
    for i in range(15):
        await asyncio.sleep(5)
        turns = await js("(()=>{return document.querySelectorAll('[data-message-author-role]').length})()")
        stop = await js("(()=>{var b=document.querySelector('button[data-testid=\"stop-button\"]');return b?'gen':'no'})()")
        if turns and turns >= 2 and stop == "no":
            await asyncio.sleep(3)
            resp = await js("(()=>{var all=document.querySelectorAll('[data-message-author-role=\"assistant\"]');if(!all.length)return '';return all[all.length-1].textContent||''})()")
            if resp:
                print(f"\n=== Diagnosis ({len(resp)} chars) ===\n{resp}")
            break
        print(f"  [{i*5}s] turns={turns} stop={stop}")

asyncio.run(main())
