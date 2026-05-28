#!/usr/bin/env python3
"""Idea 5: Structural UI Audit — systematically map multiple ChatGPT page states."""
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
    
    base_url = "https://chatgpt.com/?temporary-chat=true"
    
    states = [
        ("temp_chat", base_url),
        ("sidebar_collapsed", base_url, "toggle sidebar"),
        ("plus_menu", base_url, "open plus menu"),
        ("model_picker", base_url, "open model picker"),
    ]
    
    ui_map_dir = "/home/david/chatgpt-extension/ui-maps"
    os.makedirs(ui_map_dir, exist_ok=True)
    
    print("=== Idea 5: Structural UI Audit ===\n")
    
    for state_info in states:
        name = state_info[0]
        url = state_info[1]
        action = state_info[2] if len(state_info) > 2 else None
        
        print(f"\n--- State: {name} ---")
        
        # Navigate
        await raw("Page.navigate", {"url": url})
        await asyncio.sleep(7)
        await raw("Runtime.enable")
        await asyncio.sleep(0.3)
        await raw("Input.enable")
        
        # Perform action if needed
        if action == "toggle sidebar":
            # Find and click sidebar toggle
            toggle = await js("(()=>{var b=document.querySelector('button[aria-label*=\"sidebar\"], button[data-testid=\"sidebar-toggle\"]');if(!b)return null;var r=b.getBoundingClientRect();return JSON.stringify({x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)})})()")
            if toggle:
                t = json.loads(toggle)
                await raw("Input.dispatchMouseEvent", {"type": "mousePressed", "x": t["x"], "y": t["y"], "button": "left"})
                await raw("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": t["x"], "y": t["y"], "button": "left"})
                await asyncio.sleep(1)
                print(f"  Toggled sidebar at ({t['x']},{t['y']})")
        
        if action == "open plus menu":
            plus = await js("(()=>{var b=document.querySelector('[data-testid=\"composer-plus-btn\"]');if(!b)return null;var r=b.getBoundingClientRect();return JSON.stringify({x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)})})()")
            if plus:
                p = json.loads(plus)
                await raw("Input.dispatchMouseEvent", {"type": "mousePressed", "x": p["x"], "y": p["y"], "button": "left"})
                await raw("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": p["x"], "y": p["y"], "button": "left"})
                await asyncio.sleep(1)
                print(f"  Opened plus menu at ({p['x']},{p['y']})")
        
        if action == "open model picker":
            picker = await js("(()=>{var b=document.querySelector('[data-testid=\"model-selector-button\"], button[aria-label*=\"model\"]');if(!b)return null;var r=b.getBoundingClientRect();return JSON.stringify({x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)})})()")
            if picker:
                p = json.loads(picker)
                await raw("Input.dispatchMouseEvent", {"type": "mousePressed", "x": p["x"], "y": p["y"], "button": "left"})
                await raw("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": p["x"], "y": p["y"], "button": "left"})
                await asyncio.sleep(1)
                print(f"  Opened model picker at ({p['x']},{p['y']})")
        
        # Screenshot
        ss = await raw("Page.captureScreenshot", {"format": "jpeg", "quality": 90, "fromSurface": True})
        img = base64.b64decode(ss["result"]["data"])
        state_path = f"{ui_map_dir}/state_{name}_{time.strftime('%H%M%S')}.jpg"
        with open(state_path, "wb") as fh: fh.write(img)
        print(f"  Screenshot: {len(img)} bytes")
        
        # Send for analysis
        pm = await js("(()=>{var el=document.querySelector('[contenteditable=true]');if(!el)return null;var r=el.getBoundingClientRect();return JSON.stringify({x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height)})})()")
        if pm:
            p = json.loads(pm)
            cx, cy = p["x"] + p["w"]//2, p["y"] + p["h"]//2
            await raw("Input.dispatchMouseEvent", {"type": "mousePressed", "x": cx, "y": cy, "button": "left"})
            await raw("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": cx, "y": cy, "button": "left"})
            await asyncio.sleep(0.5)
        
        b64 = base64.b64encode(img).decode()
        paste_js = '(()=>{var b=atob(' + json.dumps(b64) + ');var a=new Uint8Array(b.length);for(var i=0;i<b.length;i++)a[i]=b.charCodeAt(i);var f=new File([a],"state.jpg",{type:"image/jpeg"});var dt=new DataTransfer();dt.items.add(f);var pm=document.querySelector("[contenteditable=true]");pm.focus();pm.dispatchEvent(new ClipboardEvent("paste",{clipboardData:dt,bubbles:true,cancelable:true}));return dt.files.length})()'
        await js(paste_js)
        await asyncio.sleep(2)
        
        desc_prompt = json.dumps(f"Describe every visible UI element in this screenshot of ChatGPT (state: {name}). For each element, list: tag type, text/label, approximate position (left/right/top/bottom/middle), and likely function. Be thorough.")
        await js("(()=>{var pm=document.querySelector('[contenteditable=true]');if(!pm)return;pm.focus();var s=window.getSelection();var r=document.createRange();r.selectNodeContents(pm);r.collapse(false);s.removeAllRanges();s.addRange(r);document.execCommand('insertText',false," + desc_prompt + ")})()")
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
        
        print(f"  Waiting for analysis of {name}...")
        for i in range(15):
            await asyncio.sleep(5)
            turns = await js("(()=>{return document.querySelectorAll('[data-message-author-role]').length})()")
            stop = await js("(()=>{var b=document.querySelector('button[data-testid=\"stop-button\"]');return b?'gen':'no'})()")
            if turns and turns >= 2 and stop == "no":
                await asyncio.sleep(3)
                resp = await js("(()=>{var all=document.querySelectorAll('[data-message-author-role=\"assistant\"]');if(!all.length)return '';return all[all.length-1].textContent||''})()")
                if resp:
                    chars = len(resp)
                    desc_path = f"{ui_map_dir}/audit_{name}_{time.strftime('%H%M%S')}.txt"
                    with open(desc_path, "w") as fh: fh.write(resp)
                    print(f"  Got {chars} chars → saved to {desc_path}")
                break
            print(f"    [{i*5}s] turns={turns} stop={stop}")

asyncio.run(main())
