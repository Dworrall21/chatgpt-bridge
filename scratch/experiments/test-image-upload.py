#!/usr/bin/env python3
"""Upload image to fresh ChatGPT chat and request analysis."""
import json, asyncio, urllib.request, websockets, base64, time, sys, os

async def main():
    img_path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/research-card-hq.jpg"
    
    if not os.path.exists(img_path):
        print(f"Image not found: {img_path}")
        return
    
    with open(img_path, "rb") as f:
        data = f.read()
    
    req = urllib.request.urlopen("http://127.0.0.1:9222/json", timeout=3)
    tabs = json.loads(req.read())
    tab = next((t for t in tabs if "chatgpt.com" in t.get("url","")), None)
    if not tab:
        print("No ChatGPT tab"); return
    
    ws = await websockets.connect("ws://127.0.0.1:9222/devtools/page/" + tab["id"], max_size=2**22)
    mid = [0]
    async def raw(method, params=None):
        mid[0] += 1
        cmd = {"id": mid[0], "method": method}
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
    
    # Navigate to fresh chat
    print("Navigating to fresh chat...")
    await raw("Page.navigate", {"url": "https://chatgpt.com/?temporary-chat=true"})
    await asyncio.sleep(6)
    await raw("Runtime.enable")
    await raw("Input.enable")
    
    # Find and focus ProseMirror
    pm = await js("(()=>{var pm=document.querySelector('[contenteditable=true]');if(!pm)return null;var r=pm.getBoundingClientRect();return JSON.stringify({x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height)})})()")
    f = json.loads(pm) if pm else None
    if not f: print("No PM"); return
    print(f"PM: ({f['x']},{f['y']}) {f['w']}x{f['h']}")
    
    cx, cy = f["x"] + f["w"]//2, f["y"] + f["h"]//2
    await raw("Input.dispatchMouseEvent", {"type": "mousePressed", "x": cx, "y": cy, "button": "left"})
    await raw("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": cx, "y": cy, "button": "left"})
    await asyncio.sleep(0.5)
    
    # Paste image
    b64 = base64.b64encode(data).decode()
    print(f"Pasting image ({len(data)} bytes)...")
    
    paste_js = '(()=>{var pm=document.querySelector("[contenteditable=true]");if(!pm)return;var b=atob(' + json.dumps(b64) + ');var a=new Uint8Array(b.length);for(var i=0;i<b.length;i++)a[i]=b.charCodeAt(i);var f=new File([a],"screenshot.jpg",{type:"image/jpeg"});var dt=new DataTransfer();dt.items.add(f);pm.focus();pm.dispatchEvent(new ClipboardEvent("paste",{clipboardData:dt,bubbles:true,cancelable:true}));return dt.files.length})()'
    paste_result = await js(paste_js)
    print(f"  Paste result: {paste_result}")
    await asyncio.sleep(2)
    
    # Check image
    has_img = await js("(()=>{var imgs=document.querySelectorAll('img[src^=\"blob:\"],img[src^=\"data:\"]');for(var i=0;i<imgs.length;i++){var r=imgs[i].getBoundingClientRect();if(r.width>50)return 'yes '+Math.round(r.width)+'x'+Math.round(r.height)}return 'no:'+imgs.length})()")
    print(f"  Image: {has_img}")
    
    # Type prompt
    print("Typing prompt...")
    await js("(()=>{var pm=document.querySelector('[contenteditable=true]');if(!pm)return;pm.focus();var sel=window.getSelection();var r=document.createRange();r.selectNodeContents(pm);r.collapse(false);sel.removeAllRanges();sel.addRange(r);document.execCommand('insertText',false,'Describe this screenshot in detail. What application is this? What does it show?')})()")
    await asyncio.sleep(1)
    
    # Send via enter
    print("Sending via Enter...")
    await raw("Input.dispatchKeyEvent", {"type": "rawKeyDown", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13})
    await raw("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13})
    await asyncio.sleep(3)
    
    pm_after = await js("(()=>{var pm=document.querySelector('[contenteditable=true]');return pm?pm.textContent.slice(0,30):'?'})()")
    stop = await js("(()=>{var b=document.querySelector('button[data-testid=\"stop-button\"]');return b?'stop found':'no stop'})()")
    ai = await js("(()=>{var all=document.querySelectorAll('[data-message-author-role=assistant]');return 'assistant:'+all.length})()")
    print(f"  PM: '{pm_after}'  {stop}  {ai}")
    
    if "stop" in stop:
        print("\n✅ Generating! Waiting for response...")
        for i in range(6):
            await asyncio.sleep(10)
            a = await js("(()=>{var all=document.querySelectorAll('[data-message-author-role=assistant]');if(!all.length)return 'wait';var last=all[all.length-1];return 'len:'+(last.textContent||'').length})()")
            print(f"  {a}")
            if "len:" in a and int(a.split(":")[1]) > 100:
                print("\n✅ Response received!")
                break
    else:
        print("\nNot generating - need different submit approach")
        # Try clicking send button if it exists
        send_btn = await js("(()=>{var btn=document.querySelector('[data-testid=\"send-button\"],button[aria-label=\"Send prompt\"]');if(!btn)return 'no btn';var r=btn.getBoundingClientRect();btn.click();return 'clicked at '+Math.round(r.x)+','+Math.round(r.y)})()")
        print(f"  Send button: {send_btn}")
        await asyncio.sleep(5)
        stop2 = await js("(()=>{var b=document.querySelector('button[data-testid=\"stop-button\"]');return b?'stop found':'no stop'})()")
        print(f"  After click: {stop2}")

asyncio.run(main())
