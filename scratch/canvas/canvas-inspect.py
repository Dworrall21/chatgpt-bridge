#!/usr/bin/env python3
"""Connect to Canvas tab, inspect for iframes, try direct editing."""
import json, asyncio, websockets, base64, time, urllib.request

WS_URL = "ws://127.0.0.1:9222/devtools/page/22E3E3494E1018375C08A090DC286C1E"

async def main():
    ws = await asyncio.wait_for(websockets.connect(WS_URL, max_size=2**24), timeout=8)
    
    async def drain(t=1.0):
        dl = time.time() + t
        while time.time() < dl:
            try: await asyncio.wait_for(ws.recv(), timeout=0.3)
            except: break
    
    async def raw(m, p=None):
        mid[0] += 1
        c = {"id": mid[0], "method": m}
        if p: c["params"] = p
        await ws.send(json.dumps(c))
        await asyncio.sleep(0.1)
        dl = time.time() + 8
        while time.time() < dl:
            try: rd = await asyncio.wait_for(ws.recv(), timeout=4)
            except asyncio.TimeoutError: continue
            d = json.loads(rd)
            if d.get("id") == mid[0]: return d
        return None
    
    async def js(e):
        r = await raw("Runtime.evaluate", {"expression": e, "returnByValue": True, "awaitPromise": True})
        return r.get("result",{}).get("result",{}).get("value") if r else None
    
    mid = [0]
    
    print("=== Canvas Tab Inspection ===\n")
    
    # Navigate to the Canvas page
    await raw("Page.enable"); await drain(0.5)
    mid[0] += 1; await ws.send(json.dumps({"id": mid[0], "method": "Page.navigate", "params": {"url": "https://chatgpt.com/c/6a17b87d-4ca0-83e8-b345-dc41e61fae67"}}))
    await drain(5.0)
    mid[0] += 1; await ws.send(json.dumps({"id": mid[0], "method": "Runtime.enable"})); await drain(1.5)
    
    url = await js("window.location.href")
    print(f"URL: {url[:70]}")
    
    # Count iframes
    iframes = await js("document.querySelectorAll('iframe').length")
    print(f"Iframes: {iframes}")
    if iframes and iframes > 0:
        iframe_info = await js("""(()=>{
            return Array.from(document.querySelectorAll('iframe')).map(function(f){
                return {src: (f.src||'').slice(0,80), id: f.id||'no-id', w: f.offsetWidth||f.clientWidth, h: f.offsetHeight||f.clientHeight};
            });
        })()""")
        print("  Details:")
        for f in iframe_info:
            print(f"    {f['src']} ({f['w']}x{f['h']}) id={f['id']}")
    
    # Find Edit button
    edit = await js("""(()=>{
        var all = document.querySelectorAll('button');
        for(var b of all){
            var t = (b.textContent||'').trim();
            var aria = b.getAttribute('aria-label')||'';
            if(t.includes('Edit')||aria.includes('Edit')){
                var r = b.getBoundingClientRect();
                return JSON.stringify({text:t.slice(0,20),x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)});
            }
        }
        return 'NOT FOUND';
    })()""")
    print(f"Edit button: {edit}")
    
    # Check contenteditable
    ce = await js("""(()=>{
        var all = document.querySelectorAll('[contenteditable]');
        return Array.from(all).map(function(e){
            var r = e.getBoundingClientRect();
            return {tag:e.tagName, text:(e.textContent||'').trim().slice(0,30), x:Math.round(r.x), y:Math.round(r.y), w:Math.round(r.width), h:Math.round(r.height)};
        });
    })()""")
    print(f"Contenteditables ({len(ce) if ce else 0}):")
    if ce:
        for c in ce: print(f"  {c['tag']} \"{c['text']}\" at ({c['x']},{c['y']}) {c['w']}x{c['h']}")
    
    # Try clicking Edit button
    if edit and edit != 'NOT FOUND':
        e = json.loads(edit)
        print(f"\n>>> Clicking Edit at ({e['x']},{e['y']})")
        await raw("Input.dispatchMouseEvent", {"type":"mousePressed","x":e["x"],"y":e["y"],"button":"left","clickCount":1})
        await raw("Input.dispatchMouseEvent", {"type":"mouseReleased","x":e["x"],"y":e["y"],"button":"left"})
        await asyncio.sleep(1)
        
        # Check for new iframes after edit click
        iframes2 = await js("document.querySelectorAll('iframe').length")
        print(f"Iframes after edit: {iframes2}")
        if iframes2 and iframes2 > (iframes or 0):
            iframe_info2 = await js("""(()=>{
                return Array.from(document.querySelectorAll('iframe')).slice(-5).map(function(f){
                    return {src:(f.src||'').slice(0,80),id:f.id||'no-id'};
                });
            })()""")
            print("  New iframes:")
            for f in iframe_info2: print(f"    {f['src']} id={f['id']}")
    
    # Screenshot
    ss = await raw("Page.captureScreenshot", {"format":"jpeg","quality":85,"fromSurface":True})
    if ss:
        img = base64.b64decode(ss["result"]["data"])
        with open("/home/david/chatgpt-extension/ui-maps/exploration/canvas_tab_inspect.jpg","wb") as fh: fh.write(img)
        print(f"\nScreenshot: {len(img)} bytes")

asyncio.run(main())
