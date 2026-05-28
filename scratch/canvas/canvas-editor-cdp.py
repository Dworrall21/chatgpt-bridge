#!/usr/bin/env python3
"""Connect to the Canvas workspace tab and try CDP editing."""
import json, asyncio, urllib.request, websockets, base64, time

WS_URL = "ws://127.0.0.1:9222/devtools/page/30EB0C3D6684BF37613E2E46A5A0CA98"

async def main():
    ws = await websockets.connect(WS_URL, max_size=2**22)
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
    
    print("=== Canvas Editor CDP Inspection ===\n")
    
    # Current URL
    url = await js("window.location.href")
    print(f"URL: {url[:80]}")
    
    # Page title
    title = await js("document.title")
    print(f"Title: {title}")
    
    # Check for Canvas-iframe targets
    all_targets = json.loads(urllib.request.urlopen("http://127.0.0.1:9222/json").read())
    canvas_targets = [t for t in all_targets if "oaiusercontent" in t.get("url","")]
    print(f"\nCanvas iframe targets available: {len(canvas_targets)}")
    for ct in canvas_targets:
        print(f"  {ct['id'][:20]} {ct.get('title','?')[:40]} | {ct.get('url','')[:60]}")
    
    # Check DOM structure
    doc_type = await js("document.contentType")
    print(f"\nDocument type: {doc_type}")
    
    # Find all iframes
    iframe_count = await js("document.querySelectorAll('iframe').length")
    print(f"Iframes in page: {iframe_count}")
    if iframe_count and iframe_count > 0:
        iframe_details = await js("""(()=>{
            var frames = document.querySelectorAll('iframe');
            return Array.from(frames).map(f => ({
                src: f.src.slice(0,80),
                id: f.id || 'no-id',
                width: f.offsetWidth,
                height: f.offsetHeight
            }));
        })()""")
        if iframe_details:
            for fd in iframe_details:
                print(f"  iframe: id={fd['id']} {fd['src']} ({fd['width']}x{fd['height']})")
    
    # Find contenteditable in Canvas area
    ce = await js("""(()=>{
        var results = [];
        document.querySelectorAll('[contenteditable]').forEach(function(el){
            var r = el.getBoundingClientRect();
            results.push({
                tag: el.tagName,
                class: el.className.slice(0,40),
                text: (el.textContent||'').trim().slice(0,30),
                x: Math.round(r.x), y: Math.round(r.y),
                w: Math.round(r.width), h: Math.round(r.height)
            });
        });
        return results;
    })()""")
    print(f"\nContenteditable elements: {len(ce) if ce else 0}")
    if ce:
        for c in ce:
            print(f"  {c['tag']} class={c['class']} text='{c['text']}' at ({c['x']},{c['y']}) {c['w']}x{c['h']}")
    
    # Find Edit button position
    edit = await js("""(()=>{
        var btns = document.querySelectorAll('button');
        for(var b of btns){
            var t = (b.textContent||'').trim();
            var aria = b.getAttribute('aria-label')||'';
            var data_testid = b.getAttribute('data-testid')||'';
            if(t.includes('Edit') || aria.includes('Edit') || data_testid.includes('edit')){
                var r = b.getBoundingClientRect();
                return {
                    text: t.slice(0,40),
                    aria: aria.slice(0,40),
                    testid: data_testid.slice(0,40),
                    x: Math.round(r.x+r.width/2),
                    y: Math.round(r.y+r.height/2),
                    w: Math.round(r.width),
                    h: Math.round(r.height)
                };
            }
        }
        return null;
    })()""")
    print(f"\nEdit button: {json.dumps(edit, indent=2) if edit else 'NOT FOUND'}")
    
    # Take screenshot
    ss = await raw("Page.captureScreenshot", {"format": "jpeg", "quality": 90, "fromSurface": True})
    if ss:
        img = base64.b64decode(ss["result"]["data"])
        with open("/home/david/chatgpt-extension/ui-maps/exploration/canvas_editor_state.jpg", "wb") as fh: fh.write(img)
        print(f"\nScreenshot: {len(img)} bytes")

asyncio.run(main())
