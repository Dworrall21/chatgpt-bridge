#!/usr/bin/env python3
"""CDP flight recorder — captures events for user interactions."""
import json, asyncio, urllib.request, websockets, time, os, signal

RECORDING_DIR = f"/home/david/chatgpt-extension/recordings/canvas-edit-{int(time.time())}"
os.makedirs(RECORDING_DIR, exist_ok=True)

async def main():
    tabs = json.loads(urllib.request.urlopen("http://127.0.0.1:9222/json").read())
    tab = next((t for t in tabs if "chatgpt.com" in t.get("url","")), None)
    if not tab: print("No tab"); return
    
    ws = await websockets.connect(tab["webSocketDebuggerUrl"], max_size=2**22)
    mid = [0]
    events = []
    
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
    
    await raw("Runtime.enable")
    
    # Subscribe to all mouse/keyboard events
    await raw("Page.enable")
    await raw("Runtime.runIfWaitingForDebugger")
    
    print(f"Recording to {RECORDING_DIR}/")
    print("Press Ctrl+C to stop recording")
    
    # Take initial screenshot
    ss = await raw("Page.captureScreenshot", {"format": "jpeg", "quality": 90, "fromSurface": True})
    if ss:
        import base64
        img = base64.b64decode(ss["result"]["data"])
        with open(f"{RECORDING_DIR}/frame_0000.jpg", "wb") as fh: fh.write(img)
        events.append({"t": time.time(), "type": "screenshot", "frame": 0})
    
    frame = 0
    try:
        while True:
            try:
                rd = await asyncio.wait_for(ws.recv(), timeout=0.5)
                d = json.loads(rd)
                method = d.get("method", "")
                
                # Capture mouse events
                if method in ["Input.dispatchMouseEvent", "Input.dispatchKeyEvent"]:
                    events.append({"t": time.time(), "type": method, "data": d.get("params", {})})
                
                # Capture execution context changes
                if method in ["Runtime.executionContextCreated", "Runtime.executionContextDestroyed", "Page.frameNavigated"]:
                    events.append({"t": time.time(), "type": method})
                    
            except asyncio.TimeoutError:
                pass
            
            # Screenshot every 2s
            if time.time() % 2 < 0.5:
                try:
                    ss = await raw("Page.captureScreenshot", {"format": "jpeg", "quality": 85, "fromSurface": True})
                    if ss:
                        import base64
                        img = base64.b64decode(ss["result"]["data"])
                        frame += 1
                        with open(f"{RECORDING_DIR}/frame_{frame:04d}.jpg", "wb") as fh: fh.write(img)
                        events.append({"t": time.time(), "type": "screenshot", "frame": frame})
                except:
                    pass
    
    except asyncio.CancelledError:
        pass
    finally:
        # Save events
        with open(f"{RECORDING_DIR}/events.jsonl", "w") as fh:
            for e in events:
                fh.write(json.dumps(e) + "\n")
        print(f"\nRecording saved: {len(events)} events, {frame} frames")
        print(f"Directory: {RECORDING_DIR}")

asyncio.run(main())
