#!/usr/bin/env python3
"""Batch test all untested plus menu features."""
import json, asyncio, urllib.request, websockets, base64, time, os, re

SCREENSHOT_DIR = "/home/david/chatgpt-extension/ui-maps/exploration/testing"
os.makedirs(SCREENSHOT_DIR, exist_ok=True)

async def test_feature(tab, feature_name, click_x, click_y, needs_submenu=False, submenu_click=None):
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
    
    # Navigate fresh each time
    await raw("Page.navigate", {"url": "https://chatgpt.com/"})
    await asyncio.sleep(8)
    await raw("Runtime.enable"); await asyncio.sleep(0.3)
    await raw("Input.enable")
    
    # Open plus menu
    plus = await js("(()=>{var b=document.querySelector('button[data-testid=\"composer-plus-btn\"]');if(!b)return null;var r=b.getBoundingClientRect();return JSON.stringify({x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)})})()")
    if plus:
        p = json.loads(plus)
        for _ in range(3):
            await raw("Input.dispatchMouseEvent", {"type": "mousePressed", "x": p["x"], "y": p["y"], "button": "left"})
            await raw("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": p["x"], "y": p["y"], "button": "left"})
        await asyncio.sleep(1)
    
    # If needs submenu, click chevron first
    if needs_submenu and submenu_click:
        print(f"  Opening submenu at ({submenu_click[0]}, {submenu_click[1]})...")
        for _ in range(3):
            await raw("Input.dispatchMouseEvent", {"type": "mousePressed", "x": submenu_click[0], "y": submenu_click[1], "button": "left"})
            await raw("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": submenu_click[0], "y": submenu_click[1], "button": "left"})
        await asyncio.sleep(0.5)
    
    # Click the feature
    print(f"  Clicking '{feature_name}' at ({click_x}, {click_y})...")
    await raw("Input.dispatchMouseEvent", {"type": "mousePressed", "x": click_x, "y": click_y, "button": "left"})
    await raw("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": click_x, "y": click_y, "button": "left"})
    await asyncio.sleep(2.5)
    
    # Check URL
    url = await js("window.location.href")
    print(f"  URL: {url[:70] if url else '?'}")
    
    # Check if any new elements appeared
    menus = await js("(()=>{return document.querySelectorAll('[role=\"dialog\"], [role=\"menu\"]').length})()")
    print(f"  Dialogs/menus: {menus}")
    
    # Screenshot
    ts = time.strftime("%H%M%S")
    ss = await raw("Page.captureScreenshot", {"format": "jpeg", "quality": 90, "fromSurface": True})
    img = base64.b64decode(ss["result"]["data"])
    path = f"{SCREENSHOT_DIR}/{feature_name}_{ts}.jpg"
    with open(path, "wb") as fh: fh.write(img)
    print(f"  Screenshot: {len(img)} bytes")
    
    # Vision analysis via the page's own chat
    pm = await js("(()=>{var e=document.querySelector('[contenteditable=true]');if(!e)return null;var r=e.getBoundingClientRect();return JSON.stringify({x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height)})})()")
    if pm:
        p = json.loads(pm)
        await raw("Input.dispatchMouseEvent", {"type": "mousePressed", "x": p["x"]+p["w"]//2, "y": p["y"]+p["h"]//2, "button": "left"})
        await raw("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": p["x"]+p["w"]//2, "y": p["y"]+p["h"]//2, "button": "left"})
    b64 = base64.b64encode(img).decode()
    await js('(()=>{var b=atob('+json.dumps(b64)+');var a=new Uint8Array(b.length);for(var i=0;i<b.length;i++)a[i]=b.charCodeAt(i);var f=new File([a],"ss.jpg",{type:"image/jpeg"});var dt=new DataTransfer();dt.items.add(f);var pm=document.querySelector("[contenteditable=true]");pm.focus();pm.dispatchEvent(new ClipboardEvent("paste",{clipboardData:dt,bubbles:true,cancelable:true}));return dt.files.length})()')
    await asyncio.sleep(2)
    pj = json.dumps(f'I clicked "{feature_name}" in the ChatGPT plus menu. What happened? Describe what changed on the page.')
    await js("(()=>{var pm=document.querySelector('[contenteditable=true]');if(!pm)return;pm.focus();var s=window.getSelection();var r=document.createRange();r.selectNodeContents(pm);r.collapse(false);s.removeAllRanges();s.addRange(r);document.execCommand('insertText',false,"+pj+")})()")
    for a in range(3):
        await raw("Input.dispatchKeyEvent", {"type": "rawKeyDown", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13})
        await raw("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13})
        await asyncio.sleep(2)
        pt = await js("(()=>{var e=document.querySelector('[contenteditable=true]');return e?e.textContent.trim().slice(0,20):'?'})()")
        if not pt or len(pt)<3: break
    for i in range(10):
        await asyncio.sleep(5)
        tn = await js("(()=>{return document.querySelectorAll('[data-message-author-role]').length})()")
        sp = await js("(()=>{var b=document.querySelector('button[data-testid=\"stop-button\"]');return b?'gen':'no'})()")
        if tn and tn>=2 and sp=="no":
            await asyncio.sleep(2)
            resp = await js("(()=>{var a=document.querySelectorAll('[data-message-author-role=\"assistant\"]');if(!a.length)return '';return a[a.length-1].textContent||''})()")
            if resp:
                with open(path.replace(".jpg", ".txt"), "w") as fh: fh.write(resp)
                return resp
    return ""

async def main():
    tabs = json.loads(urllib.request.urlopen("http://127.0.0.1:9222/json").read())
    tab = next((t for t in tabs if "chatgpt.com" in t.get("url","")), None)
    if not tab: print("No tab"); return
    
    print("=== Testing Untested Plus Menu Features ===\n")
    
    features = [
        # (name, click_x, click_y, needs_submenu, submenu_click)
        ("create_image", 478, 530, False, None),
        ("deep_research", 478, 565, False, None),
        ("web_search", 478, 600, False, None),
        ("add_photos", 478, 450, False, None),
        ("projects_submenu", 478, 680, False, None),
        # Recent Files submenu items
        ("recent_files_add", 573, 485, True, (573, 485)),
    ]
    
    for name, cx, cy, needs_sub, sub_click in features:
        print(f"\n--- {name} ---")
        result = await test_feature(tab, name, cx, cy, needs_sub, sub_click)
        print(f"  Result ({len(result) if result else 0} chars): {(result[:200] if result else 'no response')}...")

asyncio.run(main())
