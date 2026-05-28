#!/usr/bin/env python3
"""Analyze all 5 More submenu item screenshots via vision."""
import json, asyncio, urllib.request, websockets, base64, time, os

SCREENSHOT_DIR = "/home/david/chatgpt-extension/ui-maps/exploration"

items_to_analyze = [
    ("Add sources", "add_sources_clicked_direct.jpg"),
    ("Canvas", "canvas_clicked_direct.jpg"),
    ("Create task", "create_task_clicked_direct.jpg"),
    ("GitHub", "github_clicked_direct.jpg"),
    ("OpenAI Platform", "openai_platform_clicked_direct.jpg"),
]

async def main():
    tabs = json.loads(urllib.request.urlopen("http://127.0.0.1:9222/json").read())
    tab = next((t for t in tabs if "chatgpt.com" in t.get("url","")), None)
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
    
    async def send_vision_analysis(img_data, item_name):
        pm = await js("(()=>{var e=document.querySelector('[contenteditable=true]');if(!e)return null;var r=e.getBoundingClientRect();return JSON.stringify({x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height)})})()")
        if pm:
            p = json.loads(pm)
            await raw("Input.dispatchMouseEvent", {"type": "mousePressed", "x": p["x"]+p["w"]//2, "y": p["y"]+p["h"]//2, "button": "left"})
            await raw("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": p["x"]+p["w"]//2, "y": p["y"]+p["h"]//2, "button": "left"})
        b64 = base64.b64encode(img_data).decode()
        await js('(()=>{var b=atob('+json.dumps(b64)+');var a=new Uint8Array(b.length);for(var i=0;i<b.length;i++)a[i]=b.charCodeAt(i);var f=new File([a],"ss.jpg",{type:"image/jpeg"});var dt=new DataTransfer();dt.items.add(f);var pm=document.querySelector("[contenteditable=true]");pm.focus();pm.dispatchEvent(new ClipboardEvent("paste",{clipboardData:dt,bubbles:true,cancelable:true}));return dt.files.length})()')
        await asyncio.sleep(2)
        pj = json.dumps(f'I clicked "{item_name}" from the ChatGPT More submenu. What happened? Describe the page state. Any new UI or change?')
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
    
    await raw("Runtime.enable"); await asyncio.sleep(0.3)
    await raw("Input.enable")
    
    print("=== Analyzing More Submenu Items ===\n")
    
    for item_name, filename in items_to_analyze:
        print(f"\n--- {item_name} ---")
        path = os.path.join(SCREENSHOT_DIR, filename)
        if not os.path.exists(path):
            print(f"  File not found: {filename}")
            continue
        with open(path, "rb") as fh:
            img = fh.read()
        print(f"  Image: {len(img)} bytes")
        
        result = await send_vision_analysis(img, item_name)
        if result:
            print(f"  Analysis ({len(result)} chars):\n{result[:250]}...\n")
            with open(path.replace(".jpg", ".txt"), "w") as fh: fh.write(result)

asyncio.run(main())
