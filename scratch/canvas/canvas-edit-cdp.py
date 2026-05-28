#!/usr/bin/env python3
"""CDP Canvas editing — explicitly connect to non-Canvas ChatGPT tab."""
import json, asyncio, urllib.request, websockets, base64, time

TAB_TITLE = "ChatGPT"  # The normal chat tab, not "Canvas Workspace Overview"

async def main():
    tabs = json.loads(urllib.request.urlopen("http://127.0.0.1:9222/json").read())
    tab = next((t for t in tabs if t.get("title") == "ChatGPT" and "chatgpt.com" in t.get("url","")), None)
    if not tab:
        tab = next((t for t in tabs if "chatgpt.com" in t.get("url","") and "Canvas" not in t.get("title","")), None)
    if not tab: print("No tab"); return
    
    print(f"Tab: {tab['title']} | {tab.get('url','')[:60]}")
    ws = await websockets.connect(tab["webSocketDebuggerUrl"], max_size=2**22)
    mid = [0]
    async def raw(m, p=None):
        mid[0] += 1; c = {"id": mid[0], "method": m}
        if p: c["params"] = p; await ws.send(json.dumps(c))
        dl = time.time() + 15
        while time.time() < dl:
            try: rd = await asyncio.wait_for(ws.recv(), timeout=8)
            except asyncio.TimeoutError: continue
            d = json.loads(rd)
            if d.get("id") == mid[0]: return d
        return None
    async def js(e):
        r = await raw("Runtime.evaluate", {"expression": e, "returnByValue": True, "awaitPromise": True})
        return r.get("result",{}).get("result",{}).get("value") if r else None
    
    await raw("Runtime.enable")
    await raw("Input.enable")
    
    # Navigate fresh
    await raw("Page.navigate", {"url": "https://chatgpt.com/"})
    await asyncio.sleep(8)
    await raw("Runtime.enable")
    
    print("\n=== Canvas CDP Editing ===\n")
    
    # Step 1: Open Canvas mode
    plus = await js("(()=>{var b=document.querySelector('button[data-testid=\"composer-plus-btn\"]');if(!b)return null;var r=b.getBoundingClientRect();return JSON.stringify({x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)})})()")
    if plus:
        p = json.loads(plus)
        await raw("Input.dispatchMouseEvent", {"type":"mousePressed","x":p["x"],"y":p["y"],"button":"left"})
        await raw("Input.dispatchMouseEvent", {"type":"mouseReleased","x":p["x"],"y":p["y"],"button":"left"})
        await asyncio.sleep(1.5)
    for _ in range(3):
        await raw("Input.dispatchMouseEvent", {"type":"mousePressed","x":677,"y":633,"button":"left"})
        await raw("Input.dispatchMouseEvent", {"type":"mouseReleased","x":677,"y":633,"button":"left"})
    await asyncio.sleep(1.5)
    await raw("Input.dispatchMouseEvent", {"type":"mousePressed","x":797,"y":705,"button":"left"})
    await raw("Input.dispatchMouseEvent", {"type":"mouseReleased","x":797,"y":705,"button":"left"})
    await asyncio.sleep(2)
    
    # Step 2: Type a simple Canvas prompt
    pm = await js("(()=>{var e=document.querySelector('[contenteditable=true]');if(!e)return null;var r=e.getBoundingClientRect();return JSON.stringify({x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height)})})()")
    if pm:
        p = json.loads(pm)
        await raw("Input.dispatchMouseEvent", {"type":"mousePressed","x":p["x"]+p["w"]//2,"y":p["y"]+p["h"]//2,"button":"left"})
        await raw("Input.dispatchMouseEvent", {"type":"mouseReleased","x":p["x"]+p["w"]//2,"y":p["y"]+p["h"]//2,"button":"left"})
    
    prompt = "Create a simple Canvas document with a bullet list of 3 programming languages."
    
    # Focus composer first
    pm = await js("(()=>{var e=document.querySelector('[contenteditable=true]');if(!e)return null;e.focus();return true})()")
    await asyncio.sleep(0.5)
    
    # Use execCommand to insert text (triggers React onChange)
    prompt = "Create a simple Canvas document with a bullet list of 3 programming languages."
    await js("(()=>{var pm=document.querySelector('[contenteditable=true]');if(!pm)return;pm.focus();var s=window.getSelection();var r=document.createRange();r.selectNodeContents(pm);r.collapse(false);s.removeAllRanges();s.addRange(r);document.execCommand('insertText',false,"+json.dumps(prompt)+");return document.execCommand('insertText',false,' ')})()")
    await asyncio.sleep(1.5)
    
    # Poll send button until enabled
    sb = None
    for _ in range(10):
        sb = await js("(()=>{var b=document.querySelector('button[data-testid=\"send-button\"]');if(!b)return null;var r=b.getBoundingClientRect();return JSON.stringify({disabled: b.disabled, x:Math.round(r.x+r.width/2), y:Math.round(r.y+r.height/2)})})()")
        if sb:
            sbd = json.loads(sb)
            if not sbd.get("disabled"):
                break
        await asyncio.sleep(1)
    
    if sb:
        sbd = json.loads(sb)
        # Try JavaScript click (React responds to this better than CDP mouse events)
        js_click = await js("(()=>{var b=document.querySelector('button[data-testid=\"send-button\"]');if(!b||b.disabled)return false;b.dispatchEvent(new MouseEvent('mousedown',{bubbles:true,cancelable:true,view:window}));setTimeout(()=>{b.dispatchEvent(new MouseEvent('mouseup',{bubbles:true,cancelable:true,view:window}));b.dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true,view:window}))},50);return true})()")
        print(f"Send button: JS click returned {js_click}")
        await asyncio.sleep(3)
    else:
        print("Send button not found")
    
    # Step 3: Wait for Canvas response
    resp_text = ""
    for i in range(20):
        await asyncio.sleep(5)
        tn = await js("(()=>document.querySelectorAll('[data-message-author-role]').length)()")
        sp = await js("(()=>{var b=document.querySelector('button[data-testid=\"stop-button\"]');return b?'gen':'no'})()")
        print(f"  [{i*5}s] turns={tn} stop={sp}")
        if sp == "no" and tn and tn >= 3:
            resp_text = await js("(()=>{var a=document.querySelectorAll('[data-message-author-role=\"assistant\"]');if(!a.length)return '';return a[a.length-1].textContent||''})()")
            break
    
    # Step 4: Find edit button
    edit = await js("""(()=>{
        var all = document.querySelectorAll('button,[role=\"button\"],a');
        for(var b of all){
            var t = b.textContent.trim();
            var aria = (b.getAttribute('aria-label')||'');
            var role = b.getAttribute('role')||'';
            if(t.startsWith('Edit') || aria.startsWith('Edit') || role === 'button' && t.includes('Edit')){
                var r = b.getBoundingClientRect();
                return {tag: b.tagName, text: t.slice(0,30), aria: aria.slice(0,30), x: Math.round(r.x+r.width/2), y: Math.round(r.y+r.height/2), w: Math.round(r.width), h: Math.round(r.height)};
            }
        }
        return null;
    })()""")
    
    ss = await raw("Page.captureScreenshot", {"format":"jpeg","quality":90,"fromSurface":True})
    img = base64.b64decode(ss["result"]["data"])
    ss_path = "/home/david/chatgpt-extension/ui-maps/exploration/canvas_edit_result.jpg"
    with open(ss_path, "wb") as fh: fh.write(img)
    
    print(f"\n=== Results ===")
    print(f"Response: {len(resp_text) if resp_text else 0} chars")
    print(f"Edit button: {json.dumps(edit) if edit else 'NOT FOUND'}")
    print(f"Screenshot: {len(img)} bytes -> {ss_path}")

asyncio.run(main())
