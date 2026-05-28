#!/usr/bin/env python3
"""Approach 3: Ask ChatGPT where to find the 'Deep research' option."""
import json, asyncio, urllib.request, websockets, base64, time

async def main():
    req = urllib.request.urlopen("http://127.0.0.1:9222/json", timeout=3)
    tabs = json.loads(req.read())
    tab = next((t for t in tabs if "chatgpt.com" in t.get("url","")), None)
    if not tab: print("No ChatGPT tab"); return

    ws = await websockets.connect("ws://127.0.0.1:9222/devtools/page/" + tab["id"], max_size=2**22)
    mid = [0]
    async def raw(method, params=None):
        mid[0] += 1; cmd = {"id": mid[0], "method": method}
        if params: cmd["params"] = params
        await ws.send(json.dumps(cmd))
        dl = time.time() + 10
        while time.time() < dl:
            try: rd = await asyncio.wait_for(ws.recv(), timeout=5)
            except asyncio.TimeoutError: continue
            d = json.loads(rd)
            if d.get("id") == mid[0]: return d
        return None
    
    async def js(expr):
        r = await raw("Runtime.evaluate", {"expression": expr, "returnByValue": True})
        if r: return r.get("result",{}).get("result",{}).get("value")
        return None
    
    await raw("Runtime.enable")
    await raw("Page.enable")
    await raw("Input.enable")
    await raw("Emulation.setDeviceMetricsOverride", {"width": 1280, "height": 891, "deviceScaleFactor": 1, "mobile": False})
    
    # Screenshot the current page (should have plus menu open)
    ss = await raw("Page.captureScreenshot", {"format": "jpeg", "quality": 90, "fromSurface": True})
    img = base64.b64decode(ss["result"]["data"])
    
    # Navigate to fresh temp chat
    await raw("Page.navigate", {"url": "https://chatgpt.com/?temporary-chat=true"})
    await asyncio.sleep(7)
    await raw("Runtime.enable")
    await raw("Input.enable")
    
    # Find and focus PM
    pm = await js("(()=>{var el=document.querySelector('[contenteditable=true]');if(!el)return null;var r=el.getBoundingClientRect();return JSON.stringify({x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height)})})()")
    if pm:
        pl = json.loads(pm)
        cx, cy = pl["x"] + pl["w"]//2, pl["y"] + pl["h"]//2
        await raw("Input.dispatchMouseEvent", {"type": "mousePressed", "x": cx, "y": cy, "button": "left"})
        await raw("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": cx, "y": cy, "button": "left"})
        await asyncio.sleep(0.5)
    
    # Paste screenshot
    b64 = base64.b64encode(img).decode()
    paste_js = '(()=>{var b=atob(' + json.dumps(b64) + ');var a=new Uint8Array(b.length);for(var i=0;i<b.length;i++)a[i]=b.charCodeAt(i);var f=new File([a],"state.jpg",{type:"image/jpeg"});var dt=new DataTransfer();dt.items.add(f);var pm=document.querySelector("[contenteditable=true]");pm.focus();pm.dispatchEvent(new ClipboardEvent("paste",{clipboardData:dt,bubbles:true,cancelable:true}));return dt.files.length})()'
    await js(paste_js)
    await asyncio.sleep(2)
    
    # Ask where Deep research is
    ask = json.dumps("Look at this ChatGPT screenshot. The plus menu is open but 'Deep research' isn't visible. Where should I look for the 'Deep research' option in the current ChatGPT interface? Is it in the model picker dropdown, composer, settings, or somewhere else? Be specific about location.")
    await js("(()=>{var pm=document.querySelector('[contenteditable=true]');if(!pm)return;pm.focus();var s=window.getSelection();var r=document.createRange();r.selectNodeContents(pm);r.collapse(false);s.removeAllRanges();s.addRange(r);document.execCommand('insertText',false," + ask + ")})()")
    await asyncio.sleep(1)
    
    # Send
    for i in range(3):
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
    
    # Wait for response
    print("Asking ChatGPT where Deep research is...")
    for i in range(15):
        await asyncio.sleep(5)
        turns = await js("(()=>{return document.querySelectorAll('[data-message-author-role]').length})()")
        stop = await js("(()=>{var b=document.querySelector('button[data-testid=\"stop-button\"]');return b?'gen':'no'})()")
        if turns >= 2 and stop == "no":
            await asyncio.sleep(3)
            resp = await js("(()=>{var all=document.querySelectorAll('[data-message-author-role=\"assistant\"]');if(!all.length)return '';return all[all.length-1].textContent||''})()")
            if resp:
                print(f"\n=== ChatGPT says ===\n{resp}")
            break
        print(f"  [{i*5}s] turns={turns} stop={stop}")

asyncio.run(main())
