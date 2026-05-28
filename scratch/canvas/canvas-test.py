#!/usr/bin/env python3
"""Test the Canvas feature from the More submenu."""
import json, asyncio, urllib.request, websockets, base64, time, os

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
    
    await raw("Runtime.enable"); await asyncio.sleep(0.3)
    await raw("Input.enable")
    await raw("Emulation.setDeviceMetricsOverride", {"width": 1280, "height": 891, "deviceScaleFactor": 1, "mobile": False})
    await raw("Page.navigate", {"url": "https://chatgpt.com/"})
    await asyncio.sleep(8)
    await raw("Runtime.enable"); await asyncio.sleep(0.3)
    await raw("Input.enable")
    
    print("=== Testing Canvas ===\n")
    
    # Open plus menu
    plus = await js("(()=>{var b=document.querySelector('button[data-testid=\"composer-plus-btn\"]');if(!b)return null;var r=b.getBoundingClientRect();return JSON.stringify({x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)})})()")
    if plus:
        p = json.loads(plus)
        await raw("Input.dispatchMouseEvent", {"type":"mousePressed","x":p["x"],"y":p["y"],"button":"left"})
        await raw("Input.dispatchMouseEvent", {"type":"mouseReleased","x":p["x"],"y":p["y"],"button":"left"})
        await asyncio.sleep(1.5)
    
    # Open More submenu
    for _ in range(3):
        await raw("Input.dispatchMouseEvent", {"type":"mousePressed","x":677,"y":633,"button":"left"})
        await raw("Input.dispatchMouseEvent", {"type":"mouseReleased","x":677,"y":633,"button":"left"})
    await asyncio.sleep(1)
    
    # Click Canvas at (797, 705)
    print("Clicking Canvas at (797, 705)...")
    await raw("Input.dispatchMouseEvent", {"type":"mousePressed","x":797,"y":705,"button":"left"})
    await raw("Input.dispatchMouseEvent", {"type":"mouseReleased","x":797,"y":705,"button":"left"})
    await asyncio.sleep(2.5)
    
    # Check state
    pm = await js("(()=>{var e=document.querySelector('[contenteditable=true]');if(!e)return null;var r=e.getBoundingClientRect();return JSON.stringify({x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height)})})()")
    print(f"Composer: {pm}")
    
    ss1 = await raw("Page.captureScreenshot", {"format":"jpeg","quality":90,"fromSurface":True})
    img1 = base64.b64decode(ss1["result"]["data"])
    with open("/home/david/chatgpt-extension/ui-maps/exploration/canvas_activated.jpg","wb") as fh: fh.write(img1)
    print(f"Canvas activated screenshot: {len(img1)} bytes")
    
    # Type canvas prompt
    prompt = """Create a Canvas document that maps out the ChatGPT user interface as a mind map. 
Include all 14 plus menu features, the sidebar navigation, and composer tools."""
    
    if pm:
        p = json.loads(pm)
        await raw("Input.dispatchMouseEvent", {"type":"mousePressed","x":p["x"]+p["w"]//2,"y":p["y"]+p["h"]//2,"button":"left"})
        await raw("Input.dispatchMouseEvent", {"type":"mouseReleased","x":p["x"]+p["w"]//2,"y":p["y"]+p["h"]//2,"button":"left"})
    pj = json.dumps(prompt)
    await js("(()=>{var pm=document.querySelector('[contenteditable=true]');if(!pm)return;pm.focus();var s=window.getSelection();var r=document.createRange();r.selectNodeContents(pm);r.collapse(false);s.removeAllRanges();s.addRange(r);document.execCommand('insertText',false,"+pj+")})()")
    await asyncio.sleep(1)
    
    ss2 = await raw("Page.captureScreenshot", {"format":"jpeg","quality":90,"fromSurface":True})
    img2 = base64.b64decode(ss2["result"]["data"])
    with open("/home/david/chatgpt-extension/ui-maps/exploration/canvas_prompt_typed.jpg","wb") as fh: fh.write(img2)
    
    # Send via Enter
    print("Sending via Enter...")
    await raw("Input.dispatchKeyEvent", {"type":"rawKeyDown","key":"Enter","code":"Enter","windowsVirtualKeyCode":13})
    await raw("Input.dispatchKeyEvent", {"type":"keyUp","key":"Enter","code":"Enter","windowsVirtualKeyCode":13})
    await asyncio.sleep(2)
    
    pt = await js("(()=>{var e=document.querySelector('[contenteditable=true]');return e?e.textContent.trim().slice(0,20):'?'})()")
    if pt and len(pt) > 3:
        print(f"  Enter didn't work, trying send button...")
        sb = await js("(()=>{var b=document.querySelector('[data-testid=\"send-button\"]');if(!b)return null;return JSON.stringify({x:Math.round(b.getBoundingClientRect().x+b.getBoundingClientRect().width/2),y:Math.round(b.getBoundingClientRect().y+b.getBoundingClientRect().height/2)})})()")
        if sb:
            sbd = json.loads(sb)
            await raw("Input.dispatchMouseEvent", {"type":"mousePressed","x":sbd["x"],"y":sbd["y"],"button":"left"})
            await raw("Input.dispatchMouseEvent", {"type":"mouseReleased","x":sbd["x"],"y":sbd["y"],"button":"left"})
            await asyncio.sleep(2)
    
    print("Waiting for Canvas response...")
    for i in range(30):
        await asyncio.sleep(5)
        tn = await js("(()=>{return document.querySelectorAll('[data-message-author-role]').length})()")
        sp = await js("(()=>{var b=document.querySelector('button[data-testid=\"stop-button\"]');return b?'gen':'no'})()")
        print(f"  [{i*5}s] turns={tn} stop={sp}", end="")
        
        if sp == "gen":
            # Check for canvas-specific elements
            canvas_el = await js("(()=>{return document.querySelectorAll('[class*=\"canvas\"],[class*=\"artifact\"]').length})()")
            print(f" canvas={canvas_el}")
        else:
            print()
        
        if sp == "no" and tn and tn >= (4 if i > 0 else 3):
            await asyncio.sleep(2)
            resp = await js("(()=>{var a=document.querySelectorAll('[data-message-author-role=\"assistant\"]');if(!a.length)return '';return a[a.length-1].textContent||''})()")
            if resp:
                print(f"\n=== Canvas Response ({len(resp)} chars) ===\n{resp[:300]}...")
                with open("/home/david/chatgpt-extension/ui-maps/exploration/canvas_response.txt","w") as fh: fh.write(resp)
                break
        
        if i == 25:
            await raw("Page.captureScreenshot", {"format":"jpeg","quality":90,"fromSurface":True})
            ss3 = await raw("Page.captureScreenshot", {"format":"jpeg","quality":90,"fromSurface":True})
            img3 = base64.b64decode(ss3["result"]["data"])
            with open("/home/david/chatgpt-extension/ui-maps/exploration/canvas_timeout.jpg","wb") as fh: fh.write(img3)

asyncio.run(main())
