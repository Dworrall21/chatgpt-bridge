#!/usr/bin/env python3
"""Idea 1: Self-Reactive UI Mapping. Screenshot → upload → describe UI."""
import json, asyncio, urllib.request, websockets, base64, time, sys, os

ANALYSIS_PROMPT = "Describe EVERY visible element on this ChatGPT page. For each: type, text/label, position, purpose. Be exhaustive."
SAVE_DIR = "/home/david/chatgpt-extension/ui-maps"
os.makedirs(SAVE_DIR, exist_ok=True)

async def main():
    req = urllib.request.urlopen("http://127.0.0.1:9222/json", timeout=3)
    tabs = json.loads(req.read())
    tab = next((t for t in tabs if "chatgpt.com" in t.get("url","")), None)
    if not tab:
        print("No ChatGPT tab")
        return
    
    ws = await websockets.connect("ws://127.0.0.1:9222/devtools/page/" + tab["id"], max_size=2**22)
    mid = [0]
    async def raw(method, params=None):
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
    
    async def js(expr):
        r = await raw("Runtime.evaluate", {"expression": expr, "returnByValue": True})
        if r: return r.get("result",{}).get("result",{}).get("value")
        return None
    
    await raw("Runtime.enable")
    await raw("Page.enable")
    await raw("Input.enable")
    
    timestamp = time.strftime("%H%M%S")
    
    # Navigate to fresh chat (NOT temporary - temp mode doesn't support image uploads)
    print("=== Starting UI Mapping Session ===")
    await raw("Page.navigate", {"url": "https://chatgpt.com/"})
    await asyncio.sleep(7)
    await raw("Runtime.enable")
    await raw("Input.enable")
    
    # Step 1: Screenshot
    print("\n--- State 1: Fresh Chat ---")
    ss = await raw("Page.captureScreenshot", {"format": "jpeg", "quality": 90, "fromSurface": True})
    if not ss or not ss.get("result",{}).get("data"):
        print("Screenshot failed"); return
    
    img_data = base64.b64decode(ss["result"]["data"])
    ss_path = f"{SAVE_DIR}/state1-fresh_{timestamp}.jpg"
    with open(ss_path, "wb") as f: f.write(img_data)
    print(f"Screenshot: {ss_path} ({len(img_data)} bytes)")
    
    # Check page state
    url = await js("window.location.href")
    body_text = await js("document.body.innerText.length")
    print(f"URL: {url[:60] if url else '?'}")
    print(f"Body text: {body_text}")
    
    # Find any contenteditable or textarea
    pm_info = await js("""(() => {
        // Try multiple selectors
        var selectors = [
            '[contenteditable=\"true\"]',
            'div[contenteditable]',
            'textarea',
            '[role=\"textbox\"]',
            '.ProseMirror'
        ];
        for (var s of selectors) {
            var el = document.querySelector(s);
            if (el) {
                var r = el.getBoundingClientRect();
                var tag = el.tagName;
                return JSON.stringify({found: true, tag: tag, sel: s, x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height)});
            }
        }
        return JSON.stringify({found: false});
    })()""")
    print(f"Editor: {pm_info}")
    
    pm = json.loads(pm_info) if pm_info else {"found": False}
    if not pm.get("found"):
        print("No editor found - trying to click the page to activate")
        # Click in the center of the page to activate
        await raw("Input.dispatchMouseEvent", {"type": "mousePressed", "x": 640, "y": 400, "button": "left"})
        await raw("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": 640, "y": 400, "button": "left"})
        await asyncio.sleep(2)
        
        # Check again
        pm_info = await js("(()=>{var el=document.querySelector('[contenteditable=true]');if(!el){var el2=document.querySelector('[role=textbox]');if(el2)el=el2}if(!el)return'none';var r=el.getBoundingClientRect();return JSON.stringify({x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height)})})()")
        print(f"After click: {pm_info}")
        if pm_info and pm_info != "none":
            pm = json.loads(pm_info)
        else:
            print("Still no editor. The page might need human interaction.")
            return
    
    # Click to focus editor
    pm_w = pm.get("w") or 400
    pm_h = pm.get("h") or 40
    cx, cy = pm["x"] + pm_w//2, pm["y"] + pm_h//2
    print(f"Focus at ({cx}, {cy})")
    await raw("Input.dispatchMouseEvent", {"type": "mousePressed", "x": cx, "y": cy, "button": "left"})
    await raw("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": cx, "y": cy, "button": "left"})
    await asyncio.sleep(0.5)
    
    # Paste screenshot
    b64 = base64.b64encode(img_data).decode()
    paste_js = '(()=>{var el=document.querySelector("[contenteditable=true],[role=textbox]");if(!el)return "no el";var b=atob(' + json.dumps(b64) + ');var a=new Uint8Array(b.length);for(var i=0;i<b.length;i++)a[i]=b.charCodeAt(i);var f=new File([a],"state1.jpg",{type:"image/jpeg"});var dt=new DataTransfer();dt.items.add(f);el.focus();el.dispatchEvent(new ClipboardEvent("paste",{clipboardData:dt,bubbles:true,cancelable:true}));return "pasted:"+dt.files.length})()'
    result = await js(paste_js)
    print(f"Paste: {result}")
    await asyncio.sleep(2)
    
    # Check for image
    has_img = await js("(()=>{var imgs=document.querySelectorAll('img[src^=\"blob:\"],img[src^=\"data:\"]');for(var i=0;i<imgs.length;i++){var r=imgs[i].getBoundingClientRect();if(r.width>50)return 'yes '+Math.round(r.width)+'x'+Math.round(r.height)}return 'no:'+imgs.length})()")
    print(f"Image: {has_img}")
    
    # Type prompt
    prompt_json = json.dumps(ANALYSIS_PROMPT)
    type_js = "(()=>{var el=document.querySelector('[contenteditable=true],[role=textbox]');if(!el)return;el.focus();var s=window.getSelection();var r=document.createRange();r.selectNodeContents(el);r.collapse(false);s.removeAllRanges();s.addRange(r);document.execCommand('insertText',false," + prompt_json + ")})()"
    await js(type_js)
    await asyncio.sleep(1)
    
    # Check composer text
    pm_text = await js("(()=>{var el=document.querySelector('[contenteditable=true],[role=textbox]');return el?el.textContent.slice(0,60):'?'})()")
    print(f"Composer: '{pm_text}'")
    
    # Wait for image upload to complete (can take 5-10s)
    await asyncio.sleep(2)
    
    # Check if send button exists at all
    send_btn = await js("(()=>{var b=document.querySelector('[data-testid=\"send-button\"]');if(!b)return 'no btn';var r=b.getBoundingClientRect();return JSON.stringify({x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height)})})()")
    print(f"Send btn: {send_btn}")
    
    # Wait for image upload to complete (poll for enabled button)
    for wait_cycle in range(10):
        sb = await js("(()=>{var b=document.querySelector('[data-testid=\"send-button\"]');if(!b)return null;return JSON.stringify({disabled:b.disabled,x:Math.round(b.getBoundingClientRect().x),y:Math.round(b.getBoundingClientRect().y),w:Math.round(b.getBoundingClientRect().width),h:Math.round(b.getBoundingClientRect().height)})})()")
        if sb:
            sbd = json.loads(sb)
            if not sbd["disabled"]:
                print(f"  Send button ENABLED at ({sbd['x']},{sbd['y']})")
                break
        print(f"  Waiting... ({wait_cycle+1}/10)")
        await asyncio.sleep(1)
    
    # Try to send via the send button
    if sb:
        sbd = json.loads(sb)
        # Click the button even if disabled — CDP clicks sometimes bypass React disabled state
        click_x = sbd.get("x", 1044) + (sbd.get("w", 48) - 5)
        click_y = sbd.get("y", 638) + (sbd.get("h", 48)//2)
        print(f"  Clicking send at ({click_x}, {click_y})...")
        await raw("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": click_x, "y": click_y})
        await asyncio.sleep(0.3)
        await raw("Input.dispatchMouseEvent", {"type": "mousePressed", "x": click_x, "y": click_y, "button": "left", "clickCount": 1})
        await raw("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": click_x, "y": click_y, "button": "left", "clickCount": 1})
    else:
        # Fallback to Enter
        print("  No send button, trying Enter...")
        await raw("Input.dispatchKeyEvent", {"type": "rawKeyDown", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13})
        await raw("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13})
    
    await asyncio.sleep(2)
    
    # Check if message was sent
    turns = await js("(()=>{var all=document.querySelectorAll('[data-message-author-role]');return all.length})()")
    pm_after = await js("(()=>{var el=document.querySelector('[contenteditable=true],[role=textbox]');return el?el.textContent.slice(0,30):'?'})()")
    stop = await js("(()=>{var b=document.querySelector('button[data-testid=\"stop-button\"]');return b?'gen':'no stop'})()")
    print(f"After submit: turns={turns} pm='{pm_after}' stop={stop}")
    
    print("Waiting for analysis...")
    for i in range(30):
        await asyncio.sleep(5)
        stop = await js("(()=>{var b=document.querySelector('button[data-testid=\"stop-button\"]');return b?'gen':'done'})()")
        if i % 3 == 0:
            pm_check = await js("(()=>{var el=document.querySelector('[contenteditable=true],[role=textbox]');return el?el.textContent.slice(0,20):'?'})()")
            print(f"  [{i*5}s] stop: {stop} pm:'{pm_check}'")
        if stop == "done":
            await asyncio.sleep(5)
            break
    
    # Read response
    response = await js("""(() => {
        var all = document.querySelectorAll('[data-message-author-role="assistant"]');
        if (!all.length) return '';
        return all[all.length - 1].textContent || '';
    })()""")
    
    if response:
        print(f"\n=== UI MAP ({len(response)} chars) ===")
        print("=" * 50)
        print(response if len(response) < 1500 else response[:1500] + f"\n... ({len(response)-1500} more chars)")
        map_path = f"{SAVE_DIR}/ui-map-fresh_{timestamp}.txt"
        with open(map_path, "w") as f: f.write(response)
        print(f"\nSaved: {map_path}")
    else:
        print("No response received. Checking conversation state...")
        val = await js("(()=>{var all=document.querySelectorAll('[data-message-author-role]');var r=[];all.forEach(function(e,i){r.push(e.getAttribute('data-message-author-role')+':'+(e.textContent||'').length)});return JSON.stringify(r)})()")
        print(f"Turns: {val}")

asyncio.run(main())
