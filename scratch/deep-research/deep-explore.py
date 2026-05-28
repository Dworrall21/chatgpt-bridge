#!/usr/bin/env python3
"""Deep-dive into Library, Apps, Codex, and + button file options."""
import json, asyncio, urllib.request, websockets, base64, time, os, re

SCREENSHOT_DIR = "/home/david/chatgpt-extension/ui-maps/exploration"
os.makedirs(SCREENSHOT_DIR, exist_ok=True)

async def main():
    req = urllib.request.urlopen("http://127.0.0.1:9222/json", timeout=3)
    tabs = json.loads(req.read())
    tab = next((t for t in tabs if "chatgpt.com" in t.get("url","") and "/c/" in t.get("url","")), None)
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
        async def recv_until_id():
            while time.time() < dl:
                try: rd = await asyncio.wait_for(ws.recv(), timeout=5)
                except asyncio.TimeoutError: continue
                d = json.loads(rd)
                if d.get("id") == mid[0]: return d
            return None
        return await recv_until_id()
    
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
    
    async def explore_section(name, desc, click_x, click_y, prompt):
        print(f"\n\n=== {name} ===")
        print(f"Clicking at ({click_x}, {click_y})...")
        await click_coords(click_x, click_y)
        await asyncio.sleep(1.5)
        
        ss = await raw("Page.captureScreenshot", {"format": "jpeg", "quality": 90, "fromSurface": True})
        img = base64.b64decode(ss["result"]["data"])
        ts = time.strftime("%H%M%S")
        with open(f"{SCREENSHOT_DIR}/{name}_deep_{ts}.jpg", "wb") as fh: fh.write(img)
        print(f"Screenshot: {len(img)} bytes")
        
        result = await send_vision(img, prompt)
        if result:
            print(f"\nAnalysis ({len(result)} chars):\n{result[:500]}...\n")
            with open(f"{SCREENSHOT_DIR}/{name}_deep_{ts}.txt", "w") as fh: fh.write(result)
        return img, result
    
    await raw("Runtime.enable"); await asyncio.sleep(0.3)
    await raw("Input.enable")
    await raw("Emulation.setDeviceMetricsOverride", {"width": 1280, "height": 891, "deviceScaleFactor": 1, "mobile": False})
    
    # Navigate to a real conversation (not temp — temp pages block Library/Apps from opening)
    await raw("Page.navigate", {"url": "https://chatgpt.com/c/WEB:627c7c09-1d9b-40ce-a34d-8518e51d9154"})
    await asyncio.sleep(7)
    await raw("Runtime.enable"); await asyncio.sleep(0.3)
    await raw("Input.enable")
    
    current_url = await js("window.location.href")
    print(f"Base URL: {current_url[:70] if current_url else '?'}")
    
    # 1. Explore Library
    await explore_section(
        "library",
        "Library sidebar button",
        51, 149,
        """This is ChatGPT's Library page. List EVERYTHING visible:
1. What type of content does the Library show?
2. Are there files, projects, saved items? List them.
3. Any filter/sort/search options? 
4. What can you DO from this page?
5. Is there a file browser or document list?
6. List every button, tab, and interactive element visible."""
    )
    
    # 2. Explore Apps
    await explore_section(
        "apps",
        "Apps sidebar button",
        46, 185,
        """This is ChatGPT's Apps page. List EVERYTHING visible:
1. What apps are shown?
2. Is there a store/listing format?
3. Any categories or filters?
4. Can you install apps?
5. List every visible app name.
6. Are there any installed vs available apps?"""
    )
    
    # 3. Explore Codex
    await explore_section(
        "codex",
        "Codex sidebar button",
        50, 222,
        """This is ChatGPT's Codex page. List EVERYTHING visible:
1. What is Codex showing?
2. Is it a code editor, agent, or something else?
3. What options are available?
4. List every button and interactive element."""
    )
    
    # 4. Navigate back to main chat for + button exploration
    print("\n\n=== Plus Button: Add photos & files ===")
    await raw("Page.navigate", {"url": "https://chatgpt.com/?temporary-chat=true"})
    await asyncio.sleep(7)
    await raw("Runtime.enable"); await asyncio.sleep(0.3)
    await raw("Input.enable")
    
    # Click + button
    await click_coords(478, 831)
    
    ss_plus = await raw("Page.captureScreenshot", {"format": "jpeg", "quality": 90, "fromSurface": True})
    img_plus = base64.b64decode(ss_plus["result"]["data"])
    ts = time.strftime("%H%M%S")
    with open(f"{SCREENSHOT_DIR}/plus_menu_deep_{ts}.jpg", "wb") as fh: fh.write(img_plus)
    
    # Ask about file-related items in the menu
    result = await send_vision(img_plus, """The plus button menu is open. List EVERY item visible in the menu.
For items that mention "files", "photos", "uploads", or "documents", describe them in detail.
What file types are supported? Where do files come from?
Are there any "Recent files" or "Add photos & files" options?
List ALL menu items with their approximate positions.""")
    if result:
        print(f"\nPlus menu analysis ({len(result)} chars):\n{result[:400]}")
        with open(f"{SCREENSHOT_DIR}/plus_menu_deep_{ts}.txt", "w") as fh: fh.write(result)

asyncio.run(main())
