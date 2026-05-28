#!/usr/bin/env python3
"""Fresh Canvas creation → immediately find the Canvas editor button."""
import json, asyncio, urllib.request, websockets, base64, time

async def main():
    tabs = json.loads(urllib.request.urlopen("http://127.0.0.1:9222/json", timeout=5).read())
    tab = next((t for t in tabs if "chatgpt.com" in t.get("url","") and "Canvas" not in t.get("title","")), None)
    if not tab: print("No tab"); return
    
    ws = await asyncio.wait_for(websockets.connect(tab["webSocketDebuggerUrl"], max_size=2**24), timeout=8)
    async def drain(t=1.0):
        dl=time.time()+t; 
        while time.time()<dl:
            try: await asyncio.wait_for(ws.recv(), timeout=0.3)
            except: break
    
    mid=[0]
    async def raw(m, p=None):
        mid[0]+=1; c={"id":mid[0],"method":m}
        if p: c["params"]=p; await ws.send(json.dumps(c)); await asyncio.sleep(0.1)
        dl=time.time()+10
        while time.time()<dl:
            try: rd=await asyncio.wait_for(ws.recv(), timeout=5)
            except asyncio.TimeoutError: continue
            d=json.loads(rd)
            if d.get("id")==mid[0]: return d
        return None
    async def js(e):
        r=await raw("Runtime.evaluate",{"expression":e,"returnByValue":True,"awaitPromise":True})
        return r.get("result",{}).get("result",{}).get("value") if r else None
    
    await raw("Page.enable")
    await raw("Runtime.enable"); await drain(1.5)
    await raw("Page.navigate",{"url":"https://chatgpt.com/"})
    await asyncio.sleep(8)
    await raw("Runtime.enable"); await drain(1.5)
    
    # Open Canvas mode
    plus = await js("(()=>{var b=document.querySelector('button[data-testid=\"composer-plus-btn\"]');if(!b)return null;var r=b.getBoundingClientRect();return JSON.stringify({x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)})})()")
    if plus:
        p=json.loads(plus)
        await raw("Input.dispatchMouseEvent",{"type":"mousePressed","x":p["x"],"y":p["y"],"button":"left"})
        await raw("Input.dispatchMouseEvent",{"type":"mouseReleased","x":p["x"],"y":p["y"],"button":"left"})
        await asyncio.sleep(1.5)
    for _ in range(3):
        await raw("Input.dispatchMouseEvent",{"type":"mousePressed","x":677,"y":633,"button":"left"})
        await raw("Input.dispatchMouseEvent",{"type":"mouseReleased","x":677,"y":633,"button":"left"})
    await asyncio.sleep(1.5)
    await raw("Input.dispatchMouseEvent",{"type":"mousePressed","x":797,"y":705,"button":"left"})
    await raw("Input.dispatchMouseEvent",{"type":"mouseReleased","x":797,"y":705,"button":"left"})
    await asyncio.sleep(2)
    
    # Type prompt
    await js("(()=>{var pm=document.querySelector('[contenteditable=true]');if(!pm)return;pm.focus();var s=window.getSelection();var r=document.createRange();r.selectNodeContents(pm);r.collapse(false);s.removeAllRanges();s.addRange(r);document.execCommand('insertText',false,'Create a Canvas document with a simple table of 3 programming languages.');})()")
    await asyncio.sleep(1)
    
    # Send
    await js("(()=>{var b=document.querySelector('button[data-testid=\"send-button\"]');if(!b||b.disabled)return 'disabled';b.dispatchEvent(new MouseEvent('mousedown',{bubbles:true,cancelable:true}));setTimeout(()=>{b.dispatchEvent(new MouseEvent('mouseup',{bubbles:true,cancelable:true}));b.dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true}))},50);return 'sent'})()")
    
    # Wait for response
    for i in range(24):
        await asyncio.sleep(5)
        tn = await js("(()=>document.querySelectorAll('[data-message-author-role]').length)()")
        sp = await js("(()=>{var b=document.querySelector('button[data-testid=\"stop-button\"]');return b?'gen':'no'})()")
        print(f"[{i*5}s] turns={tn} stop={sp}")
        if sp=="no" and tn and tn>=2:
            await asyncio.sleep(2)
            break
    
    # NOW find ALL edit-related buttons
    print(f"\n=== Searching for Canvas Edit button ===")
    edits = await js("""(()=>{
        var btns = document.querySelectorAll('button');
        return Array.from(btns).filter(function(b){
            var t=(b.textContent||'').trim();
            var aria=b.getAttribute('aria-label')||'';
            var tid=b.getAttribute('data-testid')||'';
            return t.toLowerCase().includes('edit')||aria.toLowerCase().includes('edit')||tid.toLowerCase().includes('edit')
                   ||t.toLowerCase().includes('canvas')||aria.toLowerCase().includes('canvas');
        }).map(function(b){
            var r=b.getBoundingClientRect();
            return {text:(b.textContent||'').trim().slice(0,25),aria:(b.getAttribute('aria-label')||'').slice(0,25),testid:(b.getAttribute('data-testid')||'').slice(0,25),x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2),visible:r.top>0&&r.top<window.innerHeight};
        });
    })()""")
    print(f"Canvas/Edit buttons ({len(edits) if edits else 0}):")
    if edits:
        for e in edits: print(f"  \"{e['text']:20s}\" aria=\"{e['aria']:20s}\" testid=\"{e['testid']:20s}\" at ({e['x']},{e['y']}) visible={e['visible']}")
    
    # Also find any new buttons that appeared (the Canvas document itself might have controls)
    all_btn_labels = await js("""(()=>{
        return Array.from(document.querySelectorAll('button')).slice(0,30).map(function(b){
            var t=(b.textContent||'').trim().slice(0,20);
            var aria=(b.getAttribute('aria-label')||'').slice(0,20);
            return (t||aria) ? t+'['+aria+']' : null;
        }).filter(Boolean).join(' | ');
    })()""")
    print(f"\nAll button labels:\n  {all_btn_labels}")
    
    ss=await raw("Page.captureScreenshot",{"format":"jpeg","quality":85,"fromSurface":True})
    if ss:
        img=base64.b64decode(ss["result"]["data"])
        path="/home/david/chatgpt-extension/ui-maps/exploration/canvas_fresh_doc.jpg"
        with open(path,"wb") as fh: fh.write(img)
        print(f"\nScreenshot: {len(img)} bytes -> {path}")

asyncio.run(main())
