#!/usr/bin/env python3
"""Find 'View all tools' in the plus menu and look for Deep research."""
import json, asyncio, urllib.request, websockets, time

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
    await asyncio.sleep(0.5)
    await raw("Input.enable")
    await raw("Emulation.setDeviceMetricsOverride", {"width": 1280, "height": 891, "deviceScaleFactor": 1, "mobile": False})
    
    # Navigate fresh
    await raw("Page.navigate", {"url": "https://chatgpt.com/?temporary-chat=true"})
    await asyncio.sleep(8)
    
    await raw("Runtime.enable")
    await asyncio.sleep(0.5)
    await raw("Input.enable")
    
    # Click plus
    plus = await js("(()=>{var b=document.querySelector('[data-testid=composer-plus-btn]');if(!b)return null;var r=b.getBoundingClientRect();return JSON.stringify({x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)})})()")
    if not plus: print("No plus btn"); return
    p = json.loads(plus)
    print(f"Plus at ({p['x']},{p['y']})")
    
    await raw("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": p["x"], "y": p["y"]})
    await asyncio.sleep(0.5)
    for _ in range(2):
        await raw("Input.dispatchMouseEvent", {"type": "mousePressed", "x": p["x"], "y": p["y"], "button": "left"})
        await raw("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": p["x"], "y": p["y"], "button": "left"})
        await asyncio.sleep(0.3)
    await asyncio.sleep(1.5)
    
    # List all visible interactive elements
    items = await js("""(() => {
        var all = document.querySelectorAll('button, [role="menuitemradio"], [role="menuitem"], a, [role="option"]');
        var r2 = [];
        all.forEach(function(el) {
            var r = el.getBoundingClientRect();
            var t = (el.textContent || '').trim();
            if (t && r.width > 30 && r.x >= 0 && r.y >= 0 && r.x < 1280 && r.y < 891) {
                r2.push({text: t.slice(0, 50), x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height)});
            }
        });
        return JSON.stringify(r2);
    })()""")
    
    menu = json.loads(items or "[]")
    print(f"Visible ({len(menu)}):")
    for m in menu:
        print(f"  ({m['x']},{m['y']}) {m['w']}x{m['h']} \"{m['text']}\"")
    
    # Check for View all / More
    target = None
    for m in menu:
        t = m["text"].lower()
        if "view all" in t or "more" in t or ("show" in t and "all" in t):
            target = m
            break
    
    if target:
        cx, cy = target["x"] + target["w"]//2, target["y"] + target["h"]//2
        print(f"\\nClicking '{target['text']}' at ({cx},{cy})")
        await raw("Input.dispatchMouseEvent", {"type": "mousePressed", "x": cx, "y": cy, "button": "left"})
        await raw("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": cx, "y": cy, "button": "left"})
        await asyncio.sleep(2)
        
        # Re-scan
        items2 = await js("""(() => {
            var all = document.querySelectorAll('button, [role="menuitemradio"], [role="menuitem"]');
            var r3 = [];
            all.forEach(function(el) {
                var r = el.getBoundingClientRect();
                var t = (el.textContent || '').trim();
                if (t && r.width > 30 && r.x >= 0 && r.y >= 0 && r.x < 1280 && r.y < 891) {
                    r3.push({text: t.slice(0, 50), x: Math.round(r.x), y: Math.round(r.y)});
                }
            });
            return JSON.stringify(r3);
        })()""")
        menu2 = json.loads(items2 or "[]")
        print(f"After click ({len(menu2)}):")
        for m2 in menu2:
            print(f"  ({m2['x']},{m2['y']}) \"{m2['text']}\"")
    else:
        print("\\nNo View all / More button found")
    
    # Also check sidebar for Deep research
    sidebar = await js("""(() => {
        var side = document.querySelectorAll('[class*=\"sidebar\"] a, [class*=\"sidebar\"] button, nav a, nav button, [class*=\"nav\"] a');
        var r4 = [];
        side.forEach(function(el) {
            var t = (el.textContent || '').trim();
            var d = el.getAttribute('href') || el.getAttribute('aria-label') || '';
            if (t) r4.push(t.slice(0, 40) + ' (' + d.slice(0, 30) + ')');
        });
        return JSON.stringify(r4);
    })()""")
    print(f"\\nSidebar items: {sidebar}")

asyncio.run(main())
