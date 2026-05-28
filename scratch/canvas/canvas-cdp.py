#!/usr/bin/env python3
"""Try CDP interaction with the Canvas editor — find Edit button and type into Canvas."""
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
    
    # Check current state
    url = await js("window.location.href")
    print(f"URL: {url[:70]}")
    
    turns = await js("(()=>document.querySelectorAll('[data-message-author-role]').length)()")
    print(f"Turns: {turns}")
    
    # Check for Canvas editor elements
    split_view = await js("(()=>{return document.querySelectorAll('[class*=\"split\"],[class*=\"Split\"]').length})()")
    print(f"Split view containers: {split_view}")
    
    # Find all contenteditable elements (for Canvas editing)
    contenteditables = await js("(()=>{var els=document.querySelectorAll('[contenteditable]');return Array.from(els).map(e=>e.getAttribute('aria-label')||e.getAttribute('role')||e.tagName+'['+e.className.slice(0,30)+']').join(', ')})()")
    print(f"Contenteditable elements: {contenteditables}")
    
    # Find iframes in the Canvas area
    iframes = await js("(()=>{return document.querySelectorAll('iframe').length})()")
    print(f"Iframes: {iframes}")
    if iframes > 0:
        iframe_info = await js("(()=>{return Array.from(document.querySelectorAll('iframe')).map(f=>f.src.slice(0,60)).join(', ')})()")
        print(f"  URLs: {iframe_info}")
    
    # Check for the Canvas document area
    canvas_els = await js("(()=>{var classes=[];document.querySelectorAll('*').forEach(e=>e.classList.forEach(c=>{if(c.toLowerCase().includes('canvas')||c.toLowerCase().includes('artifact'))classes.push(c)}));return [...new Set(classes)].join(', ')})()")
    print(f"Canvas CSS classes: {canvas_els}")
    
    # Find Edit button position
    edit_btn = await js("""(()=>{
        var btns = document.querySelectorAll('button');
        for(var b of btns){
            var t = b.textContent.trim();
            var aria = b.getAttribute('aria-label')||'';
            if(t.includes('Edit') || aria.includes('Edit')){
                var r = b.getBoundingClientRect();
                return JSON.stringify({
                    text: t.slice(0,30),
                    aria: aria.slice(0,30),
                    x: Math.round(r.x+r.width/2),
                    y: Math.round(r.y+r.height/2),
                    w: Math.round(r.width),
                    h: Math.round(r.height)
                });
            }
        }
        return 'not found';
    })()""")
    print(f"Edit button: {edit_btn}")
    
    # Screenshot
    ss = await raw("Page.captureScreenshot", {"format": "jpeg", "quality": 90, "fromSurface": True})
    img = base64.b64decode(ss["result"]["data"])
    path = "/home/david/chatgpt-extension/ui-maps/exploration/canvas_cdp_state.jpg"
    with open(path, "wb") as fh: fh.write(img)
    print(f"Screenshot: {len(img)} bytes")

asyncio.run(main())
