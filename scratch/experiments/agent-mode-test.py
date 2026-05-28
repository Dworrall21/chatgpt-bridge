#!/usr/bin/env python3
"""Test Agent mode and set up long-form monitoring task."""
import json, asyncio, urllib.request, websockets, base64, time, os

async def main():
    tabs = json.loads(urllib.request.urlopen("http://127.0.0.1:9222/json").read())
    tab = next((t for t in tabs if "chatgpt.com" in t.get("url","") and "temporary-chat" not in t.get("url","")), None)
    if not tab: tab = next((t for t in tabs if "chatgpt.com" in t.get("url","")), None)
    if not tab: print("No tab"); return

    ws = await websockets.connect(tab["webSocketDebuggerUrl"], max_size=2**22)
    mid = [0]
    async def raw(m, p=None):
        mid[0] += 1; c = {"id": mid[0], "method": m}
        if p: c["params"] = p; await ws.send(json.dumps(c))
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
    
    print("=== Agent Mode Test ===\n")
    
    # Step 1: Open plus menu
    plus = await js("(()=>{var b=document.querySelector('button[data-testid=\"composer-plus-btn\"]');if(!b)return null;var r=b.getBoundingClientRect();return JSON.stringify({x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)})})()")
    if plus:
        p = json.loads(plus)
        print(f"Plus at ({p['x']}, {p['y']})")
        await raw("Input.dispatchMouseEvent", {"type": "mousePressed", "x": p["x"], "y": p["y"], "button": "left"})
        await raw("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": p["x"], "y": p["y"], "button": "left"})
        await asyncio.sleep(1)
    
    # Step 2: Open More submenu
    for _ in range(3):
        await raw("Input.dispatchMouseEvent", {"type": "mousePressed", "x": 677, "y": 633, "button": "left"})
        await raw("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": 677, "y": 633, "button": "left"})
    await asyncio.sleep(1)
    
    # Step 3: Click Agent mode at (797, 633)
    print("Clicking Agent mode at (797, 633)...")
    await raw("Input.dispatchMouseEvent", {"type": "mousePressed", "x": 797, "y": 633, "button": "left"})
    await raw("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": 797, "y": 633, "button": "left"})
    await asyncio.sleep(2.5)
    
    # Step 4: Check composer state
    pm_pos = await js("(()=>{var e=document.querySelector('[contenteditable=true]');if(!e)return null;var r=e.getBoundingClientRect();return JSON.stringify({x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height)})})()")
    print(f"Composer: {pm_pos}")
    
    mode = await js("(()=>{var s=document.querySelector('[data-testid=\"model-selector-button\"],[data-testid=\"model-switcher\"]');return s?s.textContent.trim().slice(0,40):'not found'})()")
    print(f"Mode selector: {mode}")
    
    # Take screenshot
    ss = await raw("Page.captureScreenshot", {"format": "jpeg", "quality": 90, "fromSurface": True})
    img = base64.b64decode(ss["result"]["data"])
    path = "/home/david/chatgpt-extension/ui-maps/exploration/agent_mode_active.jpg"
    with open(path, "wb") as fh: fh.write(img)
    print(f"Screenshot: {len(img)} bytes")
    
    # Step 5: Type a monitoring task
    prompt_text = """You are now in Agent mode. Please set up a monitoring system that:
1. Checks if there are any changes to the ChatGPT user interface on this page
2. Monitors for new features or menu items
3. Reports any differences from the known UI map

Provide your analysis of what Agent mode can do."""
    
    # Focus composer
    if pm_pos:
        p = json.loads(pm_pos)
        await raw("Input.dispatchMouseEvent", {"type": "mousePressed", "x": p["x"]+p["w"]//2, "y": p["y"]+p["h"]//2, "button": "left"})
        await raw("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": p["x"]+p["w"]//2, "y": p["y"]+p["h"]//2, "button": "left"})
        await asyncio.sleep(0.5)
    
    # Type via execCommand
    pj = json.dumps(prompt_text)
    await js("(()=>{var pm=document.querySelector('[contenteditable=true]');if(!pm)return;pm.focus();var s=window.getSelection();var r=document.createRange();r.selectNodeContents(pm);r.collapse(false);s.removeAllRanges();s.addRange(r);document.execCommand('insertText',false,"+pj+")})()")
    await asyncio.sleep(1)
    
    ss2 = await raw("Page.captureScreenshot", {"format": "jpeg", "quality": 90, "fromSurface": True})
    img2 = base64.b64decode(ss2["result"]["data"])
    path2 = "/home/david/chatgpt-extension/ui-maps/exploration/agent_mode_prompt.jpg"
    with open(path2, "wb") as fh: fh.write(img2)
    print(f"Prompt screenshot: {len(img2)} bytes")
    
    # Step 6: Send via Enter
    print("Sending prompt via Enter...")
    await raw("Input.dispatchKeyEvent", {"type": "rawKeyDown", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13})
    await raw("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13})
    await asyncio.sleep(2)
    
    # Check if text still there (Enter didn't work) → try send button
    pt = await js("(()=>{var e=document.querySelector('[contenteditable=true]');return e?e.textContent.trim().slice(0,20):'?'})()")
    if pt and len(pt) > 3:
        print(f"  Text still in composer ({pt}...), trying send button...")
        sb = await js("(()=>{var b=document.querySelector('[data-testid=\"send-button\"]');if(!b)return null;return JSON.stringify({x:Math.round(b.getBoundingClientRect().x+b.getBoundingClientRect().width/2),y:Math.round(b.getBoundingClientRect().y+b.getBoundingClientRect().height/2)})})()")
        if sb:
            sbd = json.loads(sb)
            await raw("Input.dispatchMouseEvent", {"type": "mousePressed", "x": sbd["x"], "y": sbd["y"], "button": "left"})
            await raw("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": sbd["x"], "y": sbd["y"], "button": "left"})
            await asyncio.sleep(2)
    
    # Step 7: Wait for response and poll
    print("Waiting for Agent mode response...")
    for i in range(24):
        await asyncio.sleep(5)
        tn = await js("(()=>{return document.querySelectorAll('[data-message-author-role]').length})()")
        sp = await js("(()=>{var b=document.querySelector('button[data-testid=\"stop-button\"]');return b?'gen':'no'})()")
        mode_status = await js("(()=>{var s=document.querySelector('[data-testid=\"model-selector-button\"]');return s?s.textContent.trim().slice(0,30):'no selector'})()")
        print(f"  [{i*5}s] turns={tn} stop={sp} mode={mode_status}")
        
        if sp == "no" and tn and tn >= 3:
            await asyncio.sleep(2)
            resp = await js("(()=>{var a=document.querySelectorAll('[data-message-author-role=\"assistant\"]');if(!a.length)return '';return a[a.length-1].textContent||''})()")
            if resp:
                # Extract only the last assistant response (not our prompt)
                last_resp = resp
                print(f"\n=== Agent Response ({len(last_resp)} chars) ===\n{last_resp[:500]}...")
                with open("/home/david/chatgpt-extension/ui-maps/exploration/agent_mode_response.txt", "w") as fh:
                    fh.write(last_resp)
            break
        
        if i == 18:  # 90s timeout
            ss3 = await raw("Page.captureScreenshot", {"format": "jpeg", "quality": 90, "fromSurface": True})
            img3 = base64.b64decode(ss3["result"]["data"])
            with open("/home/david/chatgpt-extension/ui-maps/exploration/agent_mode_timeout.jpg", "wb") as fh: fh.write(img3)
            print("  TIMEOUT - screenshot saved")

asyncio.run(main())
