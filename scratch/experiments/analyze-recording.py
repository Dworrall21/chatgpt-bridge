#!/usr/bin/env python3
"""Send key recording frames to ChatGPT for analysis."""
import json, asyncio, urllib.request, websockets, base64, time, os

REC_DIR = "/home/david/chatgpt-extension/recordings/canvas-edit-1779937902"
SCREENSHOT_DIR = "/home/david/chatgpt-extension/ui-maps/exploration"

# Select key frames representing state changes
KEY_FRAMES = [0, 1, 12, 30, 33, 36, 46, 52, 57]

async def analyze_frame(ws, raw, js, img_data, frame_num, description):
    pm = await js("(()=>{var e=document.querySelector('[contenteditable=true]');if(!e)return null;var r=e.getBoundingClientRect();return JSON.stringify({x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height)})})()")
    if pm:
        p = json.loads(pm)
        await raw("Input.dispatchMouseEvent", {"type":"mousePressed","x":p["x"]+p["w"]//2,"y":p["y"]+p["h"]//2,"button":"left"})
        await raw("Input.dispatchMouseEvent", {"type":"mouseReleased","x":p["x"]+p["w"]//2,"y":p["y"]+p["h"]//2,"button":"left"})
    b64 = base64.b64encode(img_data).decode()
    await js('(()=>{var b=atob('+json.dumps(b64)+');var a=new Uint8Array(b.length);for(var i=0;i<b.length;i++)a[i]=b.charCodeAt(i);var f=new File([a],"ss.jpg",{type:"image/jpeg"});var dt=new DataTransfer();dt.items.add(f);var pm=document.querySelector("[contenteditable=true]");pm.focus();pm.dispatchEvent(new ClipboardEvent("paste",{clipboardData:dt,bubbles:true,cancelable:true}));return dt.files.length})()')
    await asyncio.sleep(2)
    pj = json.dumps(description)
    await js("(()=>{var pm=document.querySelector('[contenteditable=true]');if(!pm)return;pm.focus();var s=window.getSelection();var r=document.createRange();r.selectNodeContents(pm);r.collapse(false);s.removeAllRanges();s.addRange(r);document.execCommand('insertText',false,"+pj+")})()")
    for a in range(3):
        await raw("Input.dispatchKeyEvent", {"type":"rawKeyDown","key":"Enter","code":"Enter","windowsVirtualKeyCode":13})
        await raw("Input.dispatchKeyEvent", {"type":"keyUp","key":"Enter","code":"Enter","windowsVirtualKeyCode":13})
        await asyncio.sleep(2)
        pt = await js("(()=>{var e=document.querySelector('[contenteditable=true]');return e?e.textContent.trim().slice(0,20):'?'})()")
        if not pt or len(pt)<3: break
        sb = await js("(()=>{var b=document.querySelector('[data-testid=\"send-button\"]');if(!b)return null;return JSON.stringify({x:Math.round(b.getBoundingClientRect().x+b.getBoundingClientRect().width/2),y:Math.round(b.getBoundingClientRect().y+b.getBoundingClientRect().height/2)})})()")
        if sb:
            sbd = json.loads(sb)
            await raw("Input.dispatchMouseEvent", {"type":"mousePressed","x":sbd["x"],"y":sbd["y"],"button":"left"})
            await raw("Input.dispatchMouseEvent", {"type":"mouseReleased","x":sbd["x"],"y":sbd["y"],"button":"left"})
            await asyncio.sleep(2)
    for i in range(10):
        await asyncio.sleep(5)
        tn = await js("(()=>{return document.querySelectorAll('[data-message-author-role]').length})()")
        sp = await js("(()=>{var b=document.querySelector('button[data-testid=\"stop-button\"]');return b?'gen':'no'})()")
        if tn and tn>=2 and sp=="no":
            await asyncio.sleep(2)
            resp = await js("(()=>{var a=document.querySelectorAll('[data-message-author-role=\"assistant\"]');if(!a.length)return '';return a[a.length-1].textContent||''})()")
            return resp or ""
    return ""

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
    await raw("Page.navigate", {"url": "https://chatgpt.com/"})
    await asyncio.sleep(8)
    await raw("Runtime.enable"); await asyncio.sleep(0.3)
    await raw("Input.enable")
    
    print("=== Analyzing Canvas Recording ===\n")
    
    for frame_num in KEY_FRAMES:
        path = os.path.join(REC_DIR, f"frame_{frame_num:04d}.jpg")
        if not os.path.exists(path):
            print(f"  frame {frame_num}: not found")
            continue
        with open(path, "rb") as fh:
            img = fh.read()
        
        # Send current frame + context about session
        if frame_num == 0:
            prompt = """This is the START of a Canvas editing session. Describe what's visible on this ChatGPT page. Is there a Canvas workspace open? What do you see?"""
        elif frame_num == 1:
            prompt = """Frame 1 — right after the previous frame. What changed?"""
        elif frame_num == 12:
            prompt = """Frame 12 — the page appears to have changed significantly (file size grew 27K). What's happening? Is there a Canvas editor panel?"""
        elif frame_num == 30:
            prompt = """Frame 30 — file size DROPPED 43K. What changed on the page? Did the Canvas close?"""
        elif frame_num == 33:
            prompt = """Frame 33 — file size dropped 47K (biggest change). Describe what's visible now vs frame 30.""" 
        elif frame_num == 36:
            prompt = """Frame 36 — page state. Is the Canvas editor open? What content do you see?"""
        elif frame_num == 46:
            prompt = """Frame 46 — this is the LARGEST frame (177K). What's on the page? Describe every visible element."""
        elif frame_num == 52:
            prompt = """Frame 52 — file size dropped 43K. What changed from frame 46?"""
        elif frame_num == 57:
            prompt = """Frame 57 — the END of the recording session. What's the final state? Describe what was accomplished."""
        else:
            prompt = f"Frame {frame_num} ({len(img)} bytes). Describe the page state."
        
        print(f"  Frame {frame_num} ({len(img)} bytes)...", end=" ", flush=True)
        result = await analyze_frame(ws, raw, js, img, frame_num, prompt)
        if result:
            print(f"✅ ({len(result)} chars)")
            os.makedirs(SCREENSHOT_DIR, exist_ok=True)
            with open(f"{SCREENSHOT_DIR}/canvas_recording_frame_{frame_num}.txt", "w") as fh:
                fh.write(result)
        else:
            print("❌ no response")

asyncio.run(main())
