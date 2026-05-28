#!/usr/bin/env python3
"""Combination approach: parent click to focus, then execCommand from outer iframe context."""
import json, asyncio, urllib.request, websockets, time, sys

CID = "6a163342-8044-83e8-88ad-a7303060dcda"

async def main():
    req = urllib.request.urlopen("http://127.0.0.1:9222/json", timeout=3)
    tabs = json.loads(req.read())
    tab = next((t for t in tabs if CID in t.get("url","")), None)
    iframe_t = next((t for t in tabs if "deep_research" in t.get("url","") and "web-sandbox" in t.get("url","")), None)
    
    if not tab or not iframe_t:
        print(f"Tab: {tab is not None}, Iframe target: {iframe_t is not None}")
        return
    
    # Connect to BOTH targets
    p_ws = await websockets.connect("ws://127.0.0.1:9222/devtools/page/" + tab["id"], max_size=2**22)
    i_ws = await websockets.connect("ws://127.0.0.1:9222/devtools/page/" + iframe_t["id"], max_size=2**22)
    
    mid = [0]
    async def raw(ws, method, params=None):
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
    
    async def pjs(expr):
        r = await raw(p_ws, "Runtime.evaluate", {"expression": expr, "returnByValue": True})
        if r: return r.get("result",{}).get("result",{}).get("value")
        return None
    
    async def ijs(expr):
        r = await raw(i_ws, "Runtime.evaluate", {"expression": expr, "returnByValue": True})
        if r: return r.get("result",{}).get("result",{}).get("value")
        return None
    
    await raw(p_ws, "Runtime.enable")
    await raw(p_ws, "Input.enable")
    await raw(i_ws, "Runtime.enable")
    await raw(i_ws, "Input.enable")
    
    # Get iframe bounds from parent
    ifr = await pjs("(()=>{var f=document.querySelector('iframe[src*=deep_research]');if(!f)return null;var r=f.getBoundingClientRect();return JSON.stringify({x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height)})})()")
    if not ifr or ifr == "null":
        print("No iframe"); return
    f = json.loads(ifr)
    print(f"Iframe: ({f['x']},{f['y']}) {f['w']}x{f['h']}")
    
    # Setup capture on both contexts
    await pjs("""(() => {
        window.__pcopied = '';
        var orig = document.execCommand;
        document.execCommand = function(cmd) {
            if (cmd === 'copy') {
                var sel = window.getSelection();
                window.__pcopied = sel ? sel.toString() : '';
            }
            return orig.apply(this, arguments);
        };
    })()""")
    
    await ijs("""(() => {
        window.__icopied = '';
        var orig = document.execCommand;
        document.execCommand = function(cmd) {
            if (cmd === 'copy') {
                var sel = window.getSelection();
                window.__icopied = sel ? sel.toString() : '';
            }
            return orig.apply(this, arguments);
        };
    })()""")
    
    result_file = "/tmp/extracted.txt"
    
    # Strategy: Click inside the iframe from PARENT to transfer focus
    # Then run execCommand('selectAll') and execCommand('copy') from OUTER IFRAME
    
    cx, cy = f["x"] + f["w"]//2, f["y"] + f["h"]//2
    print(f"\nClick at ({cx}, {cy}) from parent...")
    
    for pos in [(cx, cy), (f["x"] + 100, f["y"] + 100), (f["x"] + f["w"]//2, f["y"] + 200)]:
        await raw(p_ws, "Input.dispatchMouseEvent", {"type": "mouseMoved", "x": pos[0], "y": pos[1]})
        await asyncio.sleep(0.2)
        await raw(p_ws, "Input.dispatchMouseEvent", {"type": "mousePressed", "x": pos[0], "y": pos[1], "button": "left", "clickCount": 1})
        await raw(p_ws, "Input.dispatchMouseEvent", {"type": "mouseReleased", "x": pos[0], "y": pos[1], "button": "left", "clickCount": 1})
        await asyncio.sleep(0.3)
    
    await asyncio.sleep(1)
    
    # Now from outer iframe context: try to access inner iframe and select
    print("Running selectAll from outer iframe context...")
    
    result = await ijs("""(() => {
        // Try to access inner iframe
        var inner = document.querySelector('iframe');
        var out = {};
        
        if (inner) {
            try {
                // Try to focus inner iframe and select
                inner.focus();
                inner.contentWindow.focus();
                out.inner_focused = true;
            } catch(e) {
                out.inner_err = e.message;
            }
        }
        
        // execCommand('selectAll')
        try {
            var r = document.execCommand('selectAll');
            out.selectAll_result = r;
        } catch(e) {
            out.selectAll_err = e.message;
        }
        
        var sel = window.getSelection();
        out.selection_len = sel ? sel.toString().length : -1;
        
        return JSON.stringify(out);
    })()""")
    print(f"  Result: {result}")
    await asyncio.sleep(0.5)
    
    # Copy from outer iframe
    await ijs("document.execCommand('copy')")
    copied = await ijs("window.__icopied")
    if copied:
        print(f"✅ Outer iframe copy: {len(copied)} chars")
        with open(result_file, "w") as f: f.write(copied)
        print(f"Saved: {result_file}")
        print(f"First 100: {copied[:100]}")
    else:
        print("  Outer iframe copy: 0 chars")
        
        # Also try from parent
        await pjs("document.execCommand('copy')")
        pcopied = await pjs("window.__pcopied")
        if pcopied:
            print(f"✅ Parent copy: {len(pcopied)} chars")
            with open(result_file, "w") as f: f.write(pcopied)
            print(f"Saved: {result_file}")
            print(f"First 100: {pcopied[:100]}")
        else:
            print("  Parent copy: 0 chars")

asyncio.run(main())
