#!/usr/bin/env python3
"""Explore all 6 items in the 'More' submenu one by one."""
import json, asyncio, urllib.request, websockets, base64, time, os, re

SCREENSHOT_DIR = "/home/david/chatgpt-extension/ui-maps/exploration"
os.makedirs(SCREENSHOT_DIR, exist_ok=True)

MORE_ITEMS = [
    ("agent_mode", "the 'Agent mode' item"),
    ("add_sources", "the 'Add sources' item"),
    ("canvas", "the 'Canvas' item"),
    ("create_task", "the 'Create task' item"),
    ("github", "the 'GitHub' item"),
    ("openai_platform", "the 'OpenAI Platform' item"),
]

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
    await raw("Page.navigate", {"url": "https://chatgpt.com/"})
    await asyncio.sleep(8)
    await raw("Runtime.enable"); await asyncio.sleep(0.3)
    await raw("Input.enable")
    
    print("=== Exploring More Submenu Items ===\n")
    
    for name, desc in MORE_ITEMS:
        print(f"\n{'='*50}")
        print(f"[{name}] {desc}")
        
        # Re-open plus menu
        plus = await js("(()=>{var b=document.querySelector('button[data-testid=\"composer-plus-btn\"]');if(!b)return null;var r=b.getBoundingClientRect();return JSON.stringify({x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)})})()")
        if plus:
            p = json.loads(plus)
            await raw("Input.dispatchMouseEvent", {"type": "mousePressed", "x": p["x"], "y": p["y"], "button": "left"})
            await raw("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": p["x"], "y": p["y"], "button": "left"})
            await asyncio.sleep(1.5)
        
        # Open More submenu
        await raw("Input.dispatchMouseEvent", {"type": "mousePressed", "x": 677, "y": 633, "button": "left"})
        await raw("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": 677, "y": 633, "button": "left"})
        await asyncio.sleep(1)
        
        # Now click the item via vision-guided coordinates
        ss = await raw("Page.captureScreenshot", {"format": "jpeg", "quality": 95, "fromSurface": True})
        img = base64.b64decode(ss["result"]["data"])
        
        coord_prompt = f"""The \"More\" submenu is open to the right of the plus menu in ChatGPT.
        Find {desc} in this submenu and return its center coordinates:
        {{"found":true,"label":"{name}","x":center_x,"y":center_y,"w":width,"h":height}}
        If not found: {{"found":false}}"""
        
        result = await send_vision(img, coord_prompt)
        if result:
            print(f"  Vision response: {result[:200]}")
            coords = None
            for cand in re.findall(r'\{[^}]*\}', result.replace("\n"," ")):
                try:
                    d = json.loads(cand)
                    if d.get("found") and "x" in d: coords = d; break
                except: pass
            
            if coords:
                print(f"  Clicking '{name}' at ({coords['x']}, {coords['y']})")
                await raw("Input.dispatchMouseEvent", {"type": "mousePressed", "x": coords["x"], "y": coords["y"], "button": "left"})
                await raw("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": coords["x"], "y": coords["y"], "button": "left"})
                await asyncio.sleep(2)
                
                ts = time.strftime("%H%M%S")
                ss2 = await raw("Page.captureScreenshot", {"format": "jpeg", "quality": 90, "fromSurface": True})
                img2 = base64.b64decode(ss2["result"]["data"])
                with open(f"{SCREENSHOT_DIR}/{name}_clicked_{ts}.jpg", "wb") as fh: fh.write(img2)
                print(f"  Screenshot: {len(img2)} bytes")
                
                analysis = await send_vision(img2, f"I clicked '{name}' from the More submenu. What happened? Describe the new page state in detail. What changed?")
                if analysis:
                    print(f"  Analysis ({len(analysis)} chars): {analysis[:300]}...")
                    with open(f"{SCREENSHOT_DIR}/{name}_clicked_{ts}.txt", "w") as fh: fh.write(analysis)

asyncio.run(main())
