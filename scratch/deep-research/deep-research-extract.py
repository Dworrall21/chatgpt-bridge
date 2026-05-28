#!/usr/bin/env python3
"""
deep-research-extract.py — Extract full report from deep research via inner iframe.

Strategy:
  1. Click inside the outer iframe (from parent page) to set focus
  2. From outer iframe's CDP context, access inner iframe's contentWindow.document
  3. execCommand('selectAll') + execCommand('copy') on the inner iframe's document
  4. Read from monkey-patched window.__copied on the inner iframe
  
This works because the inner iframe is same-origin to the outer iframe (sandbox attribute
doesn't change the effective origin in this case), so contentWindow is accessible.
"""
import json, asyncio, urllib.request, websockets, time, sys, os

async def extract_deep_research(cid, output_path=None):
    """Extract full research report text from a deep research conversation.
    
    Args:
        cid: Conversation ID (e.g., "6a163342-8044-83e8-88ad-a7303060dcda")
        output_path: Optional file path to save the extracted text
    
    Returns:
        The extracted text, or None if extraction failed
    """
    req = urllib.request.urlopen("http://127.0.0.1:9222/json", timeout=3)
    targets = json.loads(req.read())
    
    tab = next((t for t in targets if cid in t.get("url","")), None)
    iframe_t = next((t for t in targets if "deep_research" in t.get("url","") and "web-sandbox" in t.get("url","")), None)
    
    if not tab:
        print(f"Conversation tab not found: {cid}")
        return None
    if not iframe_t:
        print("Deep research iframe target not found")
        return None
    
    async with websockets.connect("ws://127.0.0.1:9222/devtools/page/" + tab["id"], max_size=2**22) as p_ws:
        async with websockets.connect("ws://127.0.0.1:9222/devtools/page/" + iframe_t["id"], max_size=2**22) as i_ws:
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
            
            # Get iframe bounds
            ifr = await pjs("(()=>{var f=document.querySelector('iframe[src*=deep_research]');if(!f)return null;var r=f.getBoundingClientRect();return JSON.stringify({x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height)})})()")
            if not ifr or ifr == "null":
                print("No deep research iframe found")
                return None
            f = json.loads(ifr)
            
            # Click inside the iframe to transfer focus
            click_points = [
                (f["x"] + f["w"]//2, f["y"] + f["h"]//2),
                (f["x"] + f["w"]//2, f["y"] + f["h"]//3),
                (f["x"] + f["w"]//4, f["y"] + f["h"]//4),
            ]
            for px, py in click_points:
                await raw(p_ws, "Input.dispatchMouseEvent", {"type": "mousePressed", "x": px, "y": py, "button": "left"})
                await raw(p_ws, "Input.dispatchMouseEvent", {"type": "mouseReleased", "x": px, "y": py, "button": "left"})
                await asyncio.sleep(0.3)
            
            await asyncio.sleep(0.5)
            
            # From outer iframe, monkey-patch and extract from inner iframe
            patch = await ijs("""(() => {
                var inner = document.querySelector('iframe');
                if (!inner) return 'no inner';
                try {
                    var iw = inner.contentWindow;
                    if (!iw) return 'no contentWindow';
                    iw.__copied = '';
                    var orig = iw.document.execCommand;
                    iw.document.execCommand = function(cmd) {
                        if (cmd === 'copy') {
                            var sel = iw.getSelection();
                            iw.__copied = sel ? sel.toString() : '';
                        }
                        return orig.apply(this, arguments);
                    };
                    return 'patched:' + iw.document.body.innerText.length;
                } catch(e) {
                    return 'err:' + e.message;
                }
            })()""")
            
            # SelectAll + Copy on inner iframe
            result = await ijs("""(() => {
                var inner = document.querySelector('iframe');
                try {
                    var iw = inner.contentWindow;
                    var id = iw.document;
                    id.execCommand('selectAll');
                    id.execCommand('copy');
                    return (iw.__copied || '').length;
                } catch(e) {
                    return -1;
                }
            })()""")
            
            if result and isinstance(result, int) and result > 0:
                content = await ijs("""(() => {
                    var inner = document.querySelector('iframe');
                    return inner.contentWindow.__copied || '';
                })()""")
                
                if content:
                    if output_path:
                        with open(output_path, "w") as f:
                            f.write(content)
                    return content
            
            return None
    
    return None


if __name__ == "__main__":
    cid = sys.argv[1] if len(sys.argv) > 1 else "6a163342-8044-83e8-88ad-a7303060dcda"
    out = sys.argv[2] if len(sys.argv) > 2 else None
    
    text = asyncio.run(extract_deep_research(cid, out))
    if text:
        print(f"\n✅ Extracted {len(text)} chars")
        if out:
            print(f"   Saved to {out}")
        print(f"   First 100: {text[:100]}")
    else:
        print("\n❌ Extraction failed")
