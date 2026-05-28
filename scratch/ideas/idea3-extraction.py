#!/usr/bin/env python3
"""Idea 3: Content Extraction via Screenshot Analysis.
Take tall screenshot of deep research report, ask ChatGPT to extract ALL text."""
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
    
    # Navigate to Turing Machine conversation
    await raw("Page.navigate", {"url": "https://chatgpt.com/c/6a163342-8044-83e8-88ad-a7303060dcda"})
    await asyncio.sleep(8)
    await raw("Runtime.enable")
    await asyncio.sleep(0.3)
    
    url = await js("window.location.href")
    print(f"URL: {url[:60] if url else '?'}")
    
    # Check if research report is visible
    body_text = await js("document.body.innerText.length")
    print(f"Body text: {body_text} chars")
    
    # Take a tall screenshot (use tall viewport to capture more content)
    await raw("Emulation.setDeviceMetricsOverride", {"width": 1280, "height": 3000, "deviceScaleFactor": 1, "mobile": False})
    await asyncio.sleep(1)
    
    ss = await raw("Page.captureScreenshot", {"format": "jpeg", "quality": 95, "fromSurface": True, "captureBeyondViewport": True})
    img = base64.b64decode(ss["result"]["data"])
    print(f"Screenshot: {len(img)} bytes")
    tall_path = "/home/david/chatgpt-extension/vision-clicks/tall_report.jpg"
    with open(tall_path, "wb") as fh: fh.write(img)
    
    # Now send to fresh temp chat for extraction
    await raw("Emulation.clearDeviceMetricsOverride")
    await raw("Emulation.setDeviceMetricsOverride", {"width": 1280, "height": 891, "deviceScaleFactor": 1, "mobile": False})
    await raw("Page.navigate", {"url": "https://chatgpt.com/?temporary-chat=true"})
    await asyncio.sleep(8)
    await raw("Runtime.enable")
    await asyncio.sleep(0.3)
    await raw("Input.enable")
    
    # Focus PM
    pm = await js("(()=>{var el=document.querySelector('[contenteditable=true]');if(!el)return null;var r=el.getBoundingClientRect();return JSON.stringify({x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height)})})()")
    if pm:
        p = json.loads(pm)
        await raw("Input.dispatchMouseEvent", {"type": "mousePressed", "x": p["x"]+p["w"]//2, "y": p["y"]+p["h"]//2, "button": "left"})
        await raw("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": p["x"]+p["w"]//2, "y": p["y"]+p["h"]//2, "button": "left"})
        await asyncio.sleep(0.5)
    
    # Paste tall screenshot
    b64 = base64.b64encode(img).decode()
    paste_js = '(()=>{var b=atob(' + json.dumps(b64) + ');var a=new Uint8Array(b.length);for(var i=0;i<b.length;i++)a[i]=b.charCodeAt(i);var f=new File([a],"report.jpg",{type:"image/jpeg"});var dt=new DataTransfer();dt.items.add(f);var pm=document.querySelector("[contenteditable=true]");pm.focus();pm.dispatchEvent(new ClipboardEvent("paste",{clipboardData:dt,bubbles:true,cancelable:true}));return dt.files.length})()'
    await js(paste_js)
    await asyncio.sleep(2)
    
    # Prompt: extract ALL text
    prompt = json.dumps("This is a screenshot of the full 'Turing Machine' deep research report. Read and EXTRACT ALL the text from this screenshot. Return the COMPLETE text content as accurately as possible. Include headings, subheadings, paragraphs, and all body text. Do not summarize — extract verbatim.")
    await js("(()=>{var pm=document.querySelector('[contenteditable=true]');if(!pm)return;pm.focus();var s=window.getSelection();var r=document.createRange();r.selectNodeContents(pm);r.collapse(false);s.removeAllRanges();s.addRange(r);document.execCommand('insertText',false," + prompt + ")})()")
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
    
    print("Waiting for extraction...")
    for i in range(20):
        await asyncio.sleep(5)
        turns = await js("(()=>{return document.querySelectorAll('[data-message-author-role]').length})()")
        stop = await js("(()=>{var b=document.querySelector('button[data-testid=\"stop-button\"]');return b?'gen':'no'})()")
        if turns and turns >= 2 and stop == "no":
            await asyncio.sleep(3)
            resp = await js("(()=>{var all=document.querySelectorAll('[data-message-author-role=\"assistant\"]');if(!all.length)return '';return all[all.length-1].textContent||''})()")
            if resp:
                chars = len(resp)
                words = len(resp.split())
                print(f"\n=== Extracted text ({chars} chars, ~{words} words) ===")
                print(resp[:500] + "...")
                print(f"\n...end: {resp[-200:]}")
                
                save_path = "/home/david/chatgpt-extension/vision-clicks/extracted-text.txt"
                with open(save_path, "w") as fh: fh.write(resp)
                print(f"\nSaved to {save_path}")
            break
        print(f"  [{i*5}s] turns={turns} stop={stop}")

asyncio.run(main())
