#!/usr/bin/env python3
"""Scroll to Edit button, click it, verify Canvas editor opens."""
import json, asyncio, websockets, base64, time

WS_URL = "ws://127.0.0.1:9222/devtools/page/30EB0C3D6684BF37613E2E46A5A0CA98"

async def main():
    ws = await asyncio.wait_for(websockets.connect(WS_URL, max_size=2**24), timeout=8)
    
    async def drain(t=1.0):
        dl = time.time()+t
        while time.time()<dl:
            try: await asyncio.wait_for(ws.recv(), timeout=0.3)
            except: break
    
    mid=[0]
    async def raw(m, p=None):
        mid[0]+=1; c={"id":mid[0],"method":m}
        if p: c["params"]=p; await ws.send(json.dumps(c)); await asyncio.sleep(0.1)
        dl=time.time()+8
        while time.time()<dl:
            try: rd=await asyncio.wait_for(ws.recv(), timeout=4)
            except asyncio.TimeoutError: continue
            d=json.loads(rd)
            if d.get("id")==mid[0]: return d
        return None
    
    async def js(e):
        r=await raw("Runtime.evaluate",{"expression":e,"returnByValue":True,"awaitPromise":True})
        return r.get("result",{}).get("result",{}).get("value") if r else None
    
    await raw("Runtime.enable")
    print("=== Canvas Edit Button → Split Editor ===\n")
    
    # Step 1: Find the Edit button with BOTH text AND aria-label
    step1 = await js("""(()=>{
        for(var b of document.querySelectorAll('button')){
            var t = (b.textContent||'').trim();
            var aria = b.getAttribute('aria-label')||'';
            var testid = b.getAttribute('data-testid')||'';
            if(t.includes('Edit') || aria.includes('Edit') || testid.includes('edit')){
                var r = b.getBoundingClientRect();
                return JSON.stringify({
                    text: t.slice(0,20),
                    aria: aria.slice(0,20),
                    testid: testid.slice(0,20),
                    x: Math.round(r.x+r.width/2),
                    y: Math.round(r.y+r.height/2),
                    visible: r.top > -r.height && r.top < window.innerHeight,
                    top: Math.round(r.top),
                    bottom: Math.round(r.bottom),
                    w: Math.round(r.width),
                    h: Math.round(r.height)
                });
            }
        }
        return 'NOT FOUND';
    })()""")
    print(f"Step 1 - Find Edit: {step1}")
    
    if step1 and step1 != 'NOT FOUND':
        e = json.loads(step1)
        print(f"  Visible: {e['visible']} | at ({e['x']},{e['y']}) | text='{e['text']}' aria='{e['aria']}'")
        
        # Step 2: Scroll the Edit button into view using JavaScript
        print(f"\nStep 2 - Scrolling to Edit button...")
        await js("""(()=>{
            var btn = Array.from(document.querySelectorAll('button'));
            var edit = btn.find(b => {
                var t = (b.textContent||'').trim();
                var aria = b.getAttribute('aria-label')||'';
                return t.includes('Edit') || aria.includes('Edit');
            });
            if(edit) edit.scrollIntoView({behavior:'instant',block:'center'});
        })()""")
        await asyncio.sleep(1.5)
        
        # Step 3: Verify button is now visible
        step3 = await js("""(()=>{
            for(var b of document.querySelectorAll('button')){
                var t=(b.textContent||'').trim();
                var aria=b.getAttribute('aria-label')||'';
                if(t.includes('Edit')||aria.includes('Edit')){
                    var r=b.getBoundingClientRect();
                    return JSON.stringify({x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2),visible:r.top>0&&r.top<window.innerHeight});
                }
            }
            return 'NOT FOUND';
        })()""")
        print(f"Step 3 - After scroll: {step3}")
        
        # Step 4: Click the Edit button via JavaScript
        print(f"\nStep 4 - Clicking Edit...")
        clicked = await js("""(()=>{
            var btn = Array.from(document.querySelectorAll('button'));
            var edit = btn.find(b => {
                var t=(b.textContent||'').trim();
                var aria=b.getAttribute('aria-label')||'';
                return t.includes('Edit')||aria.includes('Edit');
            });
            if(!edit) return 'no button';
            // Dispatch proper React-compatible click sequence
            edit.dispatchEvent(new PointerEvent('pointerdown',{bubbles:true,cancelable:true}));
            edit.dispatchEvent(new MouseEvent('mousedown',{bubbles:true,cancelable:true}));
            edit.dispatchEvent(new PointerEvent('pointerup',{bubbles:true,cancelable:true}));
            edit.dispatchEvent(new MouseEvent('mouseup',{bubbles:true,cancelable:true}));
            edit.dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true}));
            return 'clicked';
        })()""")
        print(f"Click result: {clicked}")
        await asyncio.sleep(3)
        
        # Step 5: Check what changed
        print(f"\nStep 5 - Verifying Canvas editor opened:")
        
        # Check for new elements
        split = await js("(()=>{return document.querySelectorAll('[class*=\"split\"],[class*=\"Split\"],section,aside').length})()")
        print(f"  Sections: {split}")
        
        iframes = await js("document.querySelectorAll('iframe').length")
        if iframes > 0:
            iframes_src = await js("""(()=>{
                return Array.from(document.querySelectorAll('iframe')).map(f=>(f.src||'').slice(0,80));
            })()""")
            print(f"  Iframes: {iframes} -> {iframes_src}")
        else:
            print(f"  Iframes: 0")
        
        ce = await js("""(()=>{
            return Array.from(document.querySelectorAll('[contenteditable]')).map(function(e){
                var r=e.getBoundingClientRect();
                return {text:(e.textContent||'').trim().slice(0,30),x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height)};
            });
        })()""")
        print(f"  Contenteditables ({len(ce)}):")
        for c in ce:
            print(f"    \"{c['text']}\" at ({c['x']},{c['y']}) {c['w']}x{c['h']}")
        
        # Check for new button labels (like "Save", "Close", "Share", etc.)
        new_btns = await js("""(()=>{
            return Array.from(document.querySelectorAll('button')).slice(0,30).map(function(b){
                return {
                    text: (b.textContent||'').trim().slice(0,20),
                    aria: (b.getAttribute('aria-label')||'').slice(0,20),
                    testid: (b.getAttribute('data-testid')||'').slice(0,20)
                };
            }).filter(b => b.text || b.aria);
        })()""")
        print(f"  Buttons ({len(new_btns)}):")
        for b in new_btns:
            if b['text'] or b['aria']:
                print(f"    \"{b['text']:20s}\" aria=\"{b['aria']:20s}\" testid=\"{b['testid']:20s}\"")
        
        # Take screenshot
        ss=await raw("Page.captureScreenshot",{"format":"jpeg","quality":90,"fromSurface":True})
        if ss:
            img=base64.b64decode(ss["result"]["data"])
            with open("/home/david/chatgpt-extension/ui-maps/exploration/canvas_editor_opened.jpg","wb") as fh: fh.write(img)
            print(f"\n  Screenshot: {len(img)} bytes ({len(img)/1024:.0f}K)")

asyncio.run(main())
