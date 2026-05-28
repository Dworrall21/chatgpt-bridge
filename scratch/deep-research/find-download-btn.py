#!/usr/bin/env python3
"""
find-download-btn.py — Robust download button finder for deep research cards.

The download button lives INSIDE the sandboxed iframe (top-right of the research card).
Since we can't access the iframe's DOM, we find it by relative positioning:
  1. Locate the deep research iframe via querySelector
  2. Compute the top-right corner position
  3. Hover to reveal the button
  4. Click

Handles any window size because positions are computed from iframe.getBoundingClientRect().
"""
import json, asyncio, urllib.request, websockets, time, os, glob

async def find_and_click_download(raw, js):
    """Find and click the download button on a deep research card.
    
    Returns True if download was triggered (new file in ~/Downloads/).
    """
    # 1. Find iframe
    ifr_info = await js("""(() => {
        var f = document.querySelector('iframe[src*="deep_research"]');
        if (!f) return null;
        var r = f.getBoundingClientRect();
        return JSON.stringify({
            x: Math.round(r.x), y: Math.round(r.y),
            w: Math.round(r.width), h: Math.round(r.height),
            // Download button is at the top-right of the card header
            // Inside the iframe, the header area is approximately:
            //   x: iframe.x + iframe.w - 60 (right side padding)
            //   y: iframe.y + 25 (top of the card title bar)
            btn_x: Math.round(r.x + r.width - 55),
            btn_y: Math.round(r.y + 28),
            // Alternative: more central top area
            btn2_x: Math.round(r.x + r.width - 30),
            btn2_y: Math.round(r.y + 15)
        });
    })()""")
    
    if not ifr_info or ifr_info == "null":
        print("No deep research iframe found")
        return False
    
    info = json.loads(ifr_info)
    print(f"Iframe: ({info['x']},{info['y']}) {info['w']}x{info['h']}")
    print(f"Download btn candidate 1: ({info['btn_x']}, {info['btn_y']}) — card header right")
    print(f"Download btn candidate 2: ({info['btn2_x']}, {info['btn2_y']}) — card top-right corner")
    
    # 2. Check ~/Downloads/ before click
    before = set(glob.glob(os.path.expanduser("~/Downloads/deep-research-report*.md")))
    
    # 3. Try candidate 1: hover then click at top-right header area
    positions_to_try = [
        (info['btn_x'], info['btn_y'], "candidate 1 (card header right)"),
        (info['btn2_x'], info['btn2_y'], "candidate 2 (card top-right)"),
        (info['x'] + info['w'] - 60, info['y'] + 60, "candidate 3 (slightly down)"),
        (info['x'] + info['w'] - 40, info['y'] + 80, "candidate 4 (action bar area)"),
    ]
    
    for px, py, label in positions_to_try:
        print(f"\nTrying {label} at ({px}, {py})...")
        
        # Hover first to reveal the button
        await raw("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": px, "y": py})
        await asyncio.sleep(0.8)
        
        # Click
        for etype in ["mousePressed", "mouseReleased"]:
            await raw("Input.dispatchMouseEvent", {
                "type": etype, "x": px, "y": py,
                "button": "left", "clickCount": 1
            })
        await asyncio.sleep(0.5)
        
        # Also try double-click candidate 1
        if "candidate 1" in label:
            for etype in ["mousePressed", "mouseReleased"]:
                await raw("Input.dispatchMouseEvent", {
                    "type": etype, "x": px, "y": py,
                    "button": "left", "clickCount": 2
                })
            await asyncio.sleep(0.5)
    
    # 4. Wait for download and check for new files
    await asyncio.sleep(3)
    after = set(glob.glob(os.path.expanduser("~/Downloads/deep-research-report*.md")))
    new_files = after - before
    
    if new_files:
        print(f"\n✅ Downloaded: {new_files}")
        for f in new_files:
            with open(f) as fh:
                content = fh.read()
                print(f"   Size: {len(content)} chars")
        return True
    
    print("\n❌ No download detected")
    return False


async def check_download_progress(js):
    """Check if deep research mode is active (has the button indicator)."""
    dr_btn = await js("""(() => {
        var btns = document.querySelectorAll('button');
        for (var i = 0; i < btns.length; i++) {
            var label = (btns[i].getAttribute('aria-label') || '').toLowerCase();
            if (label.indexOf('deep research') >= 0) return true;
        }
        return false;
    })()""")
    return dr_btn


async def main():
    """Interactive test: find download button on current page."""
    msg_id = [0]
    req = urllib.request.urlopen("http://127.0.0.1:9222/json", timeout=3)
    tabs = json.loads(req.read())
    tab = next(t for t in tabs if t.get("url","").startswith("https://chatgpt.com/"))
    ws_url = "ws://127.0.0.1:9222/devtools/page/" + tab["id"]
    async with websockets.connect(ws_url, max_size=2**22) as ws:
        async def raw(method, params=None):
            msg_id[0] += 1; mid = msg_id[0]
            cmd = {"id": mid, "method": method}
            if params: cmd["params"] = params
            await ws.send(json.dumps(cmd))
            deadline = time.time() + 10
            while time.time() < deadline:
                try: rd = await asyncio.wait_for(ws.recv(), timeout=5)
                except asyncio.TimeoutError: continue
                d = json.loads(rd)
                if d.get("id") == mid: return d
            return None
        
        async def js(expr):
            r = await raw("Runtime.evaluate", {"expression": expr, "returnByValue": True})
            if r: return r.get("result",{}).get("result",{}).get("value")
            return None
        
        await raw("Runtime.enable")
        await raw("Input.enable")
        await raw("Page.enable")
        
        # Reset viewport to standard size
        await raw("Emulation.setDeviceMetricsOverride", {
            "width": 1280, "height": 891,
            "deviceScaleFactor": 2, "mobile": False
        })
        await asyncio.sleep(2)
        await raw("Runtime.enable")
        
        # Scroll container to top
        await js("(()=>{var a=document.querySelectorAll('*');for(var i=0;i<a.length;i++){var e=a[i];var s=window.getComputedStyle(e);if((s.overflowY=='scroll'||s.overflowY=='auto')&&e.scrollHeight>1000){e.scrollTop=0;return}}})()")
        await asyncio.sleep(1)
        
        # Check if deep research is active
        dr_active = await check_download_progress(js)
        print(f"Deep research mode: {'ACTIVE' if dr_active else 'INACTIVE'}")
        
        # Check if we're on a conversation URL
        await raw("Runtime.evaluate", {"expression": "window.location.href", "returnByValue": True})
        
        # Find and click download button
        result = await find_and_click_download(raw, js)
        
        if result:
            print("\n✅ Download button works!")
        else:
            # If no download, try scrolling to center the iframe
            print("\nTrying alternative: hover over full iframe area...")
            ifr_info = await js("(()=>{var f=document.querySelector('iframe[src*=deep_research]');if(!f)return null;return JSON.stringify({x:Math.round(f.getBoundingClientRect().x+f.getBoundingClientRect().width/2),y:Math.round(f.getBoundingClientRect().y+30)})})()")
            if ifr_info and ifr_info != "null":
                info = json.loads(ifr_info)
                for y_off in [20, 50, 100, 150]:
                    py = info['y'] + y_off
                    print(f"  Hover at ({info['x']}, {py})")
                    await raw("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": info['x'], "y": py})
                    await asyncio.sleep(0.5)

asyncio.run(main())
