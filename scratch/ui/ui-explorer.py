#!/usr/bin/env python3
"""
Recursive ChatGPT UI Explorer.
Takes screenshots, asks ChatGPT what tools/features are visible, 
clicks unexplored ones, and recurses. Builds a comprehensive UI plan map.
"""
import json, asyncio, urllib.request, websockets, base64, time, os, sys

MAPPED_FILE = "/home/david/chatgpt-extension/ui-maps/explored_tools.txt"
SCREENSHOT_DIR = "/home/david/chatgpt-extension/ui-maps/exploration"
os.makedirs(SCREENSHOT_DIR, exist_ok=True)

# Track tools we've already explored to avoid loops
explored = set()
if os.path.exists(MAPPED_FILE):
    with open(MAPPED_FILE) as f:
        explored = set(line.strip() for line in f if line.strip())

async def connect_cdp():
    req = urllib.request.urlopen("http://127.0.0.1:9222/json", timeout=3)
    tabs = json.loads(req.read())
    tab = next((t for t in tabs if "chatgpt.com" in t.get("url","")), None)
    if not tab: raise RuntimeError("No ChatGPT tab")
    ws = await websockets.connect(tab["webSocketDebuggerUrl"], max_size=2**22)
    return ws, tab

async def main():
    ws, _ = await connect_cdp()
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
    
    # Navigate to fresh chat
    await raw("Page.navigate", {"url": "https://chatgpt.com/?temporary-chat=true"})
    await asyncio.sleep(8)
    await raw("Runtime.enable"); await asyncio.sleep(0.3)
    await raw("Input.enable")
    
    print("=== ChatGPT UI Explorer ===\n")
    
    async def send_prompt(img_data, prompt_text):
        """Send image + prompt to ChatGPT, wait for response."""
        pm = await js("(()=>{var e=document.querySelector('[contenteditable=true]');if(!e)return null;var r=e.getBoundingClientRect();return JSON.stringify({x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height)})})()")
        if pm:
            p = json.loads(pm)
            await raw("Input.dispatchMouseEvent", {"type": "mousePressed", "x": p["x"]+p["w"]//2, "y": p["y"]+p["h"]//2, "button": "left"})
            await raw("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": p["x"]+p["w"]//2, "y": p["y"]+p["h"]//2, "button": "left"})
        
        b64 = base64.b64encode(img_data).decode()
        await js('(()=>{var b=atob('+json.dumps(b64)+');var a=new Uint8Array(b.length);for(var i=0;i<b.length;i++)a[i]=b.charCodeAt(i);var f=new File([a],"ss.jpg",{type:"image/jpeg"});var dt=new DataTransfer();dt.items.add(f);var pm=document.querySelector("[contenteditable=true]");pm.focus();pm.dispatchEvent(new ClipboardEvent("paste",{clipboardData:dt,bubbles:true,cancelable:true}));return dt.files.length})()')
        await asyncio.sleep(2)
        
        pj = json.dumps(prompt_text)
        await js("(()=>{var pm=document.querySelector('[contenteditable=true]');if(!pm)return;pm.focus();var s=window.getSelection();var r=document.createRange();r.selectNodeContents(pm);r.collapse(false);s.removeAllRanges();s.addRange(r);document.execCommand('insertText',false,"+pj+")})()")
        
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
        
        for i in range(15):
            await asyncio.sleep(5)
            tn = await js("(()=>{return document.querySelectorAll('[data-message-author-role]').length})()")
            sp = await js("(()=>{var b=document.querySelector('button[data-testid=\"stop-button\"]');return b?'gen':'no'})()")
            if tn and tn>=2 and sp=="no":
                await asyncio.sleep(2)
                resp = await js("(()=>{var a=document.querySelectorAll('[data-message-author-role=\"assistant\"]');if(!a.length)return '';return a[a.length-1].textContent||''})()")
                return resp or ""
        return ""
    
    async def screenshot():
        ss = await raw("Page.captureScreenshot", {"format": "jpeg", "quality": 90, "fromSurface": True})
        return base64.b64decode(ss["result"]["data"])
    
    async def click_coords(x, y):
        await raw("Input.dispatchMouseEvent", {"type": "mousePressed", "x": x, "y": y, "button": "left"})
        await raw("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": x, "y": y, "button": "left"})
        await asyncio.sleep(1)
    
    # ─── Exploration Loop ───
    # Stack of actions to try: (action_name, description, click_x, click_y)
    # Start with basic navigation
    actions_to_try = [
        ("sidebar_toggle", "Toggle the sidebar to see what's hidden", 26, 26),
        ("temperature_mode", "Toggle temperature mode if visible", 50, 60),
        ("library_button", "Click the Library button in sidebar", 60, 120),
        ("my_gpts", "Click 'My GPTs' or 'Explore GPTs' in sidebar", 60, 180),
        ("new_chat", "Click New Chat button", 60, 80),
    ]
    
    plan_entries = []
    
    # Phase 1: Map the initial state
    print("Phase 1: Mapping initial state...")
    img = await screenshot()
    ts = time.strftime("%H%M%S")
    with open(f"{SCREENSHOT_DIR}/state_initial_{ts}.jpg", "wb") as fh: fh.write(img)
    
    init_prompt = """You are a UI mapper. This is a screenshot of ChatGPT. List EVERY visible clickable/interactive element you can see in the screenshot. For each element, give:
1. A short name (like "New Chat", "Library", "Settings")
2. Approximate position (left/right/top/bottom/middle)
3. What you think it does
4. Whether we've already explored it

The viewport is 1280x891 pixels. Be thorough — look for icons, text links, buttons, menus, and any tappable items."""
    
    result = await send_prompt(img, init_prompt)
    if result:
        print(f"\n=== Initial UI Map ({len(result)} chars) ===\n")
        print(result[:600] + "..." if len(result) > 600 else result)
        plan_entries.append(("initial_state", result))
    
    # Phase 2: Click each unexplored tool
    print("\n\nPhase 2: Exploring each tool...")
    for action_name, desc, cx, cy in actions_to_try:
        if action_name in explored:
            print(f"  Skipping {action_name} (already explored)")
            continue
        
        print(f"\n--- Trying: {desc} ---")
        
        # Navigate back to a fresh temp chat
        await raw("Page.navigate", {"url": "https://chatgpt.com/?temporary-chat=true"})
        await asyncio.sleep(7)
        await raw("Runtime.enable"); await asyncio.sleep(0.3)
        await raw("Input.enable")
        
        # Take screenshot BEFORE click
        img_before = await screenshot()
        with open(f"{SCREENSHOT_DIR}/{action_name}_before_{ts}.jpg", "wb") as fh: fh.write(img_before)
        
        # Click the target
        await click_coords(cx, cy)
        await asyncio.sleep(1.5)
        
        # Take screenshot AFTER click
        img_after = await screenshot()
        with open(f"{SCREENSHOT_DIR}/{action_name}_after_{ts}.jpg", "wb") as fh: fh.write(img_after)
        
        # Ask ChatGPT what changed
        change_prompt = f"""I just clicked "{desc}" in ChatGPT. The screen might have changed. 
Look at the screenshot. Did the click do anything? 
What is the current state of the page now? 
List any NEW visible tools, menus, or interactive elements that were not there before.
If a menu opened, describe every item in it."""
        
        result = await send_prompt(img_after, change_prompt)
        if result:
            print(f"\n  Result ({len(result)} chars):\n  {result[:300]}...")
            plan_entries.append((action_name, result))
        
        explored.add(action_name)
    
    # Phase 3: Generate the plan map
    print("\n\n" + "=" * 60)
    print("EXPLORATION COMPLETE — BUILDING PLAN MAP")
    print("=" * 60)
    
    plan_map_path = f"{SCREENSHOT_DIR}/plan_map_{time.strftime('%Y%m%d_%H%M%S')}.md"
    with open(plan_map_path, "w") as fh:
        fh.write("# ChatGPT UI Plan Map\n\n")
        fh.write(f"Generated: {time.asctime()}\n\n")
        fh.write("## Explored Tools\n\n")
        for name, analysis in plan_entries:
            fh.write(f"### {name}\n\n{analysis}\n\n---\n\n")
        fh.write(f"\n## Unexplored Tools & Next Steps\n\n")
        fh.write("_Add findings from further exploration here_\n")
    
    print(f"\nPlan map saved to {plan_map_path}")
    print(f"Explored {len(explored)} tools total")
    
    # Save explored set
    with open(MAPPED_FILE, "w") as fh:
        for e in sorted(explored):
            fh.write(e + "\n")

asyncio.run(main())
