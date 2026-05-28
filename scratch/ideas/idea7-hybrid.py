#!/usr/bin/env python3
"""Idea 7: Hybrid Extraction — try DOM, execCommand, and vision."""
import json, asyncio, urllib.request, websockets, base64, time, os, re

async def main():
    req = urllib.request.urlopen("http://127.0.0.1:9222/json", timeout=3)
    targets = json.loads(req.read())
    
    # Find parent + outer iframe targets
    parent_tab = next((t for t in targets if "chatgpt.com" in t.get("url","") and "/c/" in t.get("url","")), None)
    if not parent_tab: parent_tab = next((t for t in targets if "chatgpt.com" in t.get("url","") and "temporary-chat" not in t.get("url","")), None)
    if not parent_tab: parent_tab = next((t for t in targets if "chatgpt.com" in t.get("url","")), None)
    outer = next((t for t in targets if "web-sandbox.oaiusercontent.com" in t.get("url","")), None)
    
    if not parent_tab: print("No ChatGPT tab"); return
    
    # Use webSocketDebuggerUrl for ALL target connections
    parent_ws_url = parent_tab.get("webSocketDebuggerUrl")
    outer_ws_url = outer.get("webSocketDebuggerUrl") if outer else None
    
    ws = await websockets.connect(parent_ws_url, max_size=2**22)
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
    
    await raw("Runtime.enable"); await asyncio.sleep(0.3)
    await raw("Input.enable")
    await raw("Emulation.setDeviceMetricsOverride", {"width": 1280, "height": 891, "deviceScaleFactor": 1, "mobile": False})
    
    url = await js("window.location.href")
    print(f"Tab: {url[:60] if url else '?'}")
    print(f"Outer iframe target: {'YES' if outer else 'NO'}")
    
    print("\n=== Method 1: DOM ---")
    body = await js("document.body.innerText.length")
    print(f"  body.innerText: {body} chars")
    
    turns = await js("(()=>{return document.querySelectorAll('[data-message-author-role]').length})()")
    print(f"  Conversation turns: {turns}")
    
    print("\n=== Method 2: Inner iframe execCommand ---")
    if outer:
        try:
            ow = await websockets.connect(outer_ws_url, max_size=2**22)
            om = [0]
            async def oraw(m, p=None):
                om[0] += 1; c = {"id": om[0], "method": m}
                if p: c["params"] = p
                await ow.send(json.dumps(c))
                dl = time.time() + 10
                while time.time() < dl:
                    try: rd = await asyncio.wait_for(ow.recv(), timeout=5)
                    except asyncio.TimeoutError: continue
                    d = json.loads(rd)
                    if d.get("id") == om[0]: return d
                return None
            await oraw("Runtime.enable"); await asyncio.sleep(0.2)
            
            extract = await oraw("Runtime.evaluate", {"expression": """
                (()=>{var inner=document.querySelector('iframe');if(!inner)return 'no iframe';
                var iw=inner.contentWindow;if(!iw)return 'no contentWindow';
                iw.__copied='';
                iw.addEventListener('copy',function(e){var s=iw.getSelection();if(s&&s.toString())iw.__copied=s.toString()});
                iw.document.execCommand('selectAll');iw.document.execCommand('copy');
                return 'copied '+iw.__copied.length;
                })()
            """, "returnByValue": True, "awaitPromise": True})
            er = extract.get("result",{}).get("result",{}).get("value","err") if extract else "err"
            print(f"  Result: {er}")
            
            content = await oraw("Runtime.evaluate", {"expression": "(()=>{var i=document.querySelector('iframe');return i?i.contentWindow.__copied||'':''})()", "returnByValue": True})
            ct = content.get("result",{}).get("result",{}).get("value","") if content else ""
            print(f"  Content: {len(ct)} chars")
            await ow.close()
        except Exception as e:
            print(f"  Error: {e}")
    else:
        ct = ""
        print("  No iframe target available")
    
    print("\n=== Method 3: Vision (screenshot) ---")
    # Take tall screenshot
    await raw("Emulation.setDeviceMetricsOverride", {"width": 1280, "height": 3000, "deviceScaleFactor": 1, "mobile": False})
    ss = await raw("Page.captureScreenshot", {"format": "jpeg", "quality": 90, "fromSurface": True, "captureBeyondViewport": True})
    img = base64.b64decode(ss["result"]["data"])
    print(f"  Screenshot: {len(img)} bytes")
    
    await raw("Emulation.setDeviceMetricsOverride", {"width": 1280, "height": 891, "deviceScaleFactor": 1, "mobile": False})
    await raw("Page.navigate", {"url": "https://chatgpt.com/?temporary-chat=true"})
    await asyncio.sleep(7)
    await raw("Runtime.enable"); await asyncio.sleep(0.3)
    
    pm = await js("(()=>{var e=document.querySelector('[contenteditable=true]');if(!e)return null;var r=e.getBoundingClientRect();return JSON.stringify({x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height)})})()")
    if pm:
        p = json.loads(pm)
        await raw("Input.dispatchMouseEvent", {"type": "mousePressed", "x": p["x"]+p["w"]//2, "y": p["y"]+p["h"]//2, "button": "left"})
        await raw("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": p["x"]+p["w"]//2, "y": p["y"]+p["h"]//2, "button": "left"})
    
    b64 = base64.b64encode(img).decode()
    await js('(()=>{var b=atob('+json.dumps(b64)+');var a=new Uint8Array(b.length);for(var i=0;i<b.length;i++)a[i]=b.charCodeAt(i);var f=new File([a],"ss.jpg",{type:"image/jpeg"});var dt=new DataTransfer();dt.items.add(f);var pm=document.querySelector("[contenteditable=true]");pm.focus();pm.dispatchEvent(new ClipboardEvent("paste",{clipboardData:dt,bubbles:true,cancelable:true}));return dt.files.length})()')
    await asyncio.sleep(2)
    
    prompt = json.dumps("Extract ALL visible text from this screenshot. Return as much verbatim text as possible.")
    await js("(()=>{var pm=document.querySelector('[contenteditable=true]');if(!pm)return;pm.focus();var s=window.getSelection();var r=document.createRange();r.selectNodeContents(pm);r.collapse(false);s.removeAllRanges();s.addRange(r);document.execCommand('insertText',false,"+prompt+")})()")
    
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
    
    vision_txt = ""
    for i in range(15):
        await asyncio.sleep(5)
        tn = await js("(()=>{return document.querySelectorAll('[data-message-author-role]').length})()")
        sp = await js("(()=>{var b=document.querySelector('button[data-testid=\"stop-button\"]');return b?'gen':'no'})()")
        print(f"  [{i*5}s] turns={tn} stop={sp}")
        if tn and tn>=2 and sp=="no":
            await asyncio.sleep(3)
            vision_txt = await js("(()=>{var a=document.querySelectorAll('[data-message-author-role=\"assistant\"]');if(!a.length)return '';return a[a.length-1].textContent||''})()")
            break
    
    vl = len(vision_txt) if vision_txt else 0
    print(f"\n=== RESULTS ===")
    print(f"Method 1 (DOM):    {body} chars")
    print(f"Method 2 (iframe): {len(ct)} chars" if locals().get('ct') else f"Method 2 (iframe): N/A")
    print(f"Method 3 (vision): {vl} chars")

asyncio.run(main())
