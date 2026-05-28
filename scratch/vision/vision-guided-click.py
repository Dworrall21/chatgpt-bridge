#!/usr/bin/env python3
"""
Vision-Guided Automation for ChatGPT

Finds and clicks elements by sending screenshots to ChatGPT's vision model.
Bypasses DOM/AX tree limitations for elements in iframes, canvases, or sandboxed content.

Usage:
    python3 vision-guided-click.py "description of target element"

Example:
    python3 vision-guided-click.py "the 'New chat' button in the sidebar"
    python3 vision-guided-click.py "the plus button at the bottom of the composer"

Pipeline:
    1. Set standardized viewport (1280x891)
    2. Screenshot current state
    3. Upload to temporary chat: "Where is X? Return JSON: {x, y, w, h}"
    4. Parse JSON coordinates from response
    5. Click via CDP at those coordinates
    6. Verify with a second screenshot
    7. If not found: auto-retry with plus click, scroll, or ask ChatGPT
"""
import json, asyncio, urllib.request, websockets, base64, time, sys, os, re

SAVE_DIR = os.path.expanduser("~/chatgpt-extension/vision-clicks")
os.makedirs(SAVE_DIR, exist_ok=True)

COORD_PROMPT = """Look at this screenshot of ChatGPT. The viewport is 1280x891 pixels.
Find the TARGET_ELEMENT and return its bounding box in this EXACT JSON format:
{{"found": true, "label": "short description", "x": center_x, "y": center_y, "w": width_in_px, "h": height_in_px}}
If you can't find it, return: {{"found": false, "reason": "why not"}}
Rules:
- x,y should be the CENTER of the element
- Coordinates MUST be in the 1280x891 viewport coordinate system
- Be as precise as possible — this will be used to click with a mouse"""

async def connect_cdp():
    req = urllib.request.urlopen("http://127.0.0.1:9222/json", timeout=3)
    tabs = json.loads(req.read())
    tab = next((t for t in tabs if "chatgpt.com" in t.get("url","")), None)
    if not tab: raise RuntimeError("No ChatGPT tab found")
    ws = await websockets.connect("ws://127.0.0.1:9222/devtools/page/" + tab["id"], max_size=2**22)
    return ws, tab

async def cdp(ws, mid, method, params=None):
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

async def js(ws, mid, expr):
    r = await cdp(ws, mid, "Runtime.evaluate", {"expression": expr, "returnByValue": True, "awaitPromise": True})
    if r: return r.get("result",{}).get("result",{}).get("value")
    return None

async def paste_image(ws, mid, img_data, text):
    """Paste image and type text into ChatGPT composer."""
    b64 = base64.b64encode(img_data).decode()
    paste_code = (
        '(()=>{var pm=document.querySelector("[contenteditable=true]");'
        "if(!pm)return;var b=atob(" + json.dumps(b64) + ");"
        "var a=new Uint8Array(b.length);for(var i=0;i<b.length;i++)a[i]=b.charCodeAt(i);"
        'var f=new File([a],"img.jpg",{type:"image/jpeg"});'
        "var dt=new DataTransfer();dt.items.add(f);"
        "pm.focus();pm.dispatchEvent(new ClipboardEvent('paste',{clipboardData:dt,bubbles:true,cancelable:true}));"
        "return dt.files.length})()"
    )
    result = await js(ws, mid, paste_code)
    await asyncio.sleep(2)
    
    await js(ws, mid, 
        "(()=>{var pm=document.querySelector('[contenteditable=true]');"
        "if(!pm)return;pm.focus();var s=window.getSelection();var r=document.createRange();"
        "r.selectNodeContents(pm);r.collapse(false);s.removeAllRanges();s.addRange(r);"
        "document.execCommand('insertText',false," + json.dumps(text) + ")})()"
    )
    await asyncio.sleep(1)
    return result

async def send_message(ws, mid):
    """Send message via Enter, fallback to send button click."""
    for attempt in range(3):
        await cdp(ws, mid, "Input.dispatchKeyEvent", {
            "type": "rawKeyDown", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13
        })
        await cdp(ws, mid, "Input.dispatchKeyEvent", {
            "type": "keyUp", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13
        })
        await asyncio.sleep(2)
        
        pm_txt = await js(ws, mid, 
            "(()=>{var el=document.querySelector('[contenteditable=true]');"
            "return el?el.textContent.trim().slice(0,20):'?'})()"
        )
        if not pm_txt or len(pm_txt) < 3:
            return "enter"
        
        # Fallback: send button click
        sb = await js(ws, mid,
            "(()=>{var b=document.querySelector('[data-testid=\"send-button\"]');"
            "if(!b)return null;return JSON.stringify({"
            "x:Math.round(b.getBoundingClientRect().x+b.getBoundingClientRect().width/2),"
            "y:Math.round(b.getBoundingClientRect().y+b.getBoundingClientRect().height/2)"
            "})})()"
        )
        if sb:
            sbd = json.loads(sb)
            await cdp(ws, mid, "Input.dispatchMouseEvent", {
                "type": "mousePressed", "x": sbd["x"], "y": sbd["y"], "button": "left"
            })
            await cdp(ws, mid, "Input.dispatchMouseEvent", {
                "type": "mouseReleased", "x": sbd["x"], "y": sbd["y"], "button": "left"
            })
            await asyncio.sleep(2)
            pm_txt = await js(ws, mid,
                "(()=>{var el=document.querySelector('[contenteditable=true]');"
                "return el?el.textContent.trim().slice(0,20):'?'})()"
            )
            if not pm_txt or len(pm_txt) < 3:
                return "click"
    return "failed"

async def wait_for_response(ws, mid, timeout_sec=90):
    """Wait for ChatGPT to finish generating a response."""
    for i in range(timeout_sec // 5):
        await asyncio.sleep(5)
        turns = await js(ws, mid,
            "(()=>{return document.querySelectorAll('[data-message-author-role]').length})()"
        )
        stop = await js(ws, mid,
            "(()=>{var b=document.querySelector('button[data-testid=\"stop-button\"]');"
            "return b?'gen':'no'})()"
        )
        if turns and turns >= 2 and stop == "no":
            await asyncio.sleep(3)
            resp = await js(ws, mid,
                "(()=>{var all=document.querySelectorAll('[data-message-author-role=\"assistant\"]');"
                "if(!all.length)return '';return all[all.length-1].textContent||''})()"
            )
            if resp:
                return resp
        if i % 4 == 0:
            print(f"  [{i*5}s] turns={turns} stop={stop}")
    return None

def parse_coordinates(response):
    """Extract JSON with x,y from response text."""
    for cand in re.findall(r'\{[^}]*\}', response.replace("\n", " ")):
        try:
            parsed = json.loads(cand)
            if isinstance(parsed, dict):
                if "x" in parsed and "y" in parsed and "found" in parsed:
                    return parsed
        except:
            continue
    return None

async def click_and_verify(ws, mid, x, y):
    """Click at coordinates and return element hit."""
    await cdp(ws, mid, "Input.dispatchMouseEvent", {
        "type": "mouseMoved", "x": x, "y": y
    })
    await asyncio.sleep(0.3)
    await cdp(ws, mid, "Input.dispatchMouseEvent", {
        "type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1
    })
    await cdp(ws, mid, "Input.dispatchMouseEvent", {
        "type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1
    })
    await asyncio.sleep(0.5)
    return await js(ws, mid, 
        "(()=>{var el=document.elementFromPoint(" + str(x) + "," + str(y) + ");"
        "return el?el.tagName+'.'+(el.className||'').slice(0,30):'none'})()"
    )

async def main():
    target_desc = sys.argv[1] if len(sys.argv) > 1 else "describe what to find"
    timestamp = time.strftime("%H%M%S")
    
    print(f"=== Vision-Guided Automation ===")
    print(f"Target: {target_desc}")
    
    ws, mid = [0], [0]
    ws_sock, _ = await connect_cdp()
    ws[0] = ws_sock
    
    # Setup
    await cdp(ws[0], mid, "Runtime.enable")
    await asyncio.sleep(0.3)
    await cdp(ws[0], mid, "Input.enable")
    await cdp(ws[0], mid, "Emulation.setDeviceMetricsOverride", {
        "width": 1280, "height": 891, "deviceScaleFactor": 1, "mobile": False
    })
    
    # Navigate to temporary chat
    await cdp(ws[0], mid, "Page.navigate", {"url": "https://chatgpt.com/?temporary-chat=true"})
    await asyncio.sleep(8)
    await cdp(ws[0], mid, "Runtime.enable")
    await asyncio.sleep(0.3)
    await cdp(ws[0], mid, "Input.enable")
    
    # Screenshot
    print("--- Step 1: Screenshot ---")
    ss = await cdp(ws[0], mid, "Page.captureScreenshot", {"format": "jpeg", "quality": 90, "fromSurface": True})
    img = base64.b64decode(ss["result"]["data"])
    img_path = f"{SAVE_DIR}/state_{timestamp}.jpg"
    with open(img_path, "wb") as fh: fh.write(img)
    print(f"Screenshot: {img_path} ({len(img)} bytes)")
    
    # Ask ChatGPT
    print(f"--- Step 2: Ask ChatGPT ---")
    prompt = COORD_PROMPT.replace("TARGET_ELEMENT", target_desc)
    
    # Get PM position
    pm_info = await js(ws[0], mid,
        "(()=>{var el=document.querySelector('[contenteditable=true]');"
        "if(!el)return null;var r=el.getBoundingClientRect();"
        "return JSON.stringify({x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height)})})()"
    )
    if pm_info:
        pm = json.loads(pm_info)
        cx, cy = pm["x"] + pm["w"]//2, pm["y"] + pm["h"]//2
        await cdp(ws[0], mid, "Input.dispatchMouseEvent", {"type": "mousePressed", "x": cx, "y": cy, "button": "left"})
        await cdp(ws[0], mid, "Input.dispatchMouseEvent", {"type": "mouseReleased", "x": cx, "y": cy, "button": "left"})
        await asyncio.sleep(0.5)
    
    await paste_image(ws[0], mid, img, prompt)
    print(f"Pasted image + prompt ({len(prompt)} chars)")
    
    # Wait for upload
    for i in range(15):
        sb = await js(ws[0], mid,
            "(()=>{var b=document.querySelector('[data-testid=\"send-button\"]');"
            "if(!b)return null;return JSON.stringify({disabled:b.disabled})})()"
        )
        if sb and json.loads(sb)["disabled"] == False:
            break
        if i % 5 == 0: print(f"  Uploading... ({i+1}/15)")
        await asyncio.sleep(1)
    
    # Send
    method = await send_message(ws[0], mid)
    print(f"  Sent via: {method}")
    
    # Wait for response
    print("  Waiting for analysis...")
    response = await wait_for_response(ws[0], mid)
    
    if not response:
        print("  No response received")
        return
    
    print(f"\n  Response ({len(response)} chars)")
    print(f"  {response[:200]}...")
    
    # Parse coords
    coords = parse_coordinates(response)
    if not coords:
        print("  Could not parse coordinates")
        return
    
    print(f"\n  Parsed: {coords}")
    
    if not coords.get("found", True):
        print(f"  Not found: {coords.get('reason', 'unknown')}")
        return
    
    # Click
    print(f"--- Step 3: Click at ({coords['x']}, {coords['y']}) ---")
    for trial in range(3):
        tx = coords["x"] + (trial - 1) * 5
        ty = coords["y"] + (trial - 1) * 5
        hit = await click_and_verify(ws[0], mid, tx, ty)
        print(f"  Trial {trial+1}: ({tx},{ty}) → {hit}")
    
    # Verify
    ss2 = await cdp(ws[0], mid, "Page.captureScreenshot", {"format": "jpeg", "quality": 90, "fromSurface": True})
    img2 = base64.b64decode(ss2["result"]["data"])
    after_path = f"{SAVE_DIR}/after_{timestamp}.jpg"
    with open(after_path, "wb") as fh: fh.write(img2)
    print(f"\nAfter: {after_path} ({len(img2)} bytes)")
    
    url = await js(ws[0], mid, "window.location.href")
    print(f"URL: {url[:60] if url else '?'}")

if __name__ == "__main__":
    asyncio.run(main())
