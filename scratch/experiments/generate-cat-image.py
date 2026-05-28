#!/usr/bin/env python3
"""
generate-cat-image.py — Use ChatGPT to generate a cat image via CDP.
"""
import json, asyncio, urllib.request, websockets, time, sys, os

CDP_PORT = 9222

def find_tab():
    req = urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json", timeout=3)
    tabs = json.loads(req.read())
    for t in tabs:
        if "chatgpt.com" in t.get("url", ""):
            return t["id"]
    return None

async def main():
    tab_id = find_tab()
    if not tab_id:
        print("ERROR: No ChatGPT tab")
        sys.exit(1)

    ws_url = f"ws://127.0.0.1:{CDP_PORT}/devtools/page/{tab_id}"
    
    async with websockets.connect(ws_url, max_size=2**22) as ws:
        msg_id = 0
        
        async def js(expr, timeout=10):
            nonlocal msg_id
            msg_id += 1
            mid = msg_id
            await ws.send(json.dumps({"id": mid, "method": "Runtime.evaluate",
                "params": {"expression": expr, "returnByValue": True, "timeout": int(timeout*1000)}}))
            deadline = time.time() + timeout + 5
            while time.time() < deadline:
                raw = await asyncio.wait_for(ws.recv(), timeout=timeout+5)
                d = json.loads(raw)
                if d.get("id") == mid:
                    exc = d.get("result", {}).get("exceptionDetails")
                    if exc:
                        raise RuntimeError(f"JS error: {exc.get('text','')}")
                    return d.get("result", {}).get("result", {}).get("value")
            raise TimeoutError()
        
        async def cdp_method(method, params=None):
            nonlocal msg_id
            msg_id += 1
            mid = msg_id
            cmd = {"id": mid, "method": method}
            if params:
                cmd["params"] = params
            await ws.send(json.dumps(cmd))
            deadline = time.time() + 5
            while time.time() < deadline:
                raw = await asyncio.wait_for(ws.recv(), timeout=5)
                d = json.loads(raw)
                if d.get("id") == mid:
                    return d.get("result", {})
            raise TimeoutError(f"CDP {method} timeout")
        
        async def click(selector):
            coords = await js(f"""(() => {{
                const el = document.querySelector('{selector}');
                if (!el) return null;
                el.scrollIntoView({{block:'center'}});
                const r = el.getBoundingClientRect();
                return {{x: Math.round(r.x+r.width/2), y: Math.round(r.y+r.height/2)}};
            }})()""")
            if not coords:
                raise RuntimeError(f"Element not found: {selector}")
            await cdp_method("Input.dispatchMouseEvent",
                {"type": "mousePressed", "x": coords["x"], "y": coords["y"], "button": "left", "clickCount": 1})
            await cdp_method("Input.dispatchMouseEvent",
                {"type": "mouseReleased", "x": coords["x"], "y": coords["y"], "button": "left", "clickCount": 1})
            await asyncio.sleep(0.3)
        
        # Enable CDP domains
        for m in ["Runtime.enable", "DOM.enable", "Input.enable"]:
            await cdp_method(m)
        
        # Step 1: New chat
        print("[1/4] Opening new chat...")
        await click('a[data-testid="create-new-chat-button"]')
        await asyncio.sleep(2)
        
        # Step 2: Focus input and type
        print("[2/4] Typing prompt...")
        await js("""(() => {
            const el = document.querySelector("[role='textbox'][aria-label='Chat with ChatGPT']");
            if (el) { el.focus(); return 'ok'; }
            return 'not_found';
        })()""")
        
        await cdp_method("Input.insertText", {"text": "Generate an image of a cute orange tabby cat sitting in a warm sunbeam. Make it photorealistic."})
        await asyncio.sleep(0.5)
        
        # Step 3: Send
        print("[3/4] Sending...")
        await cdp_method("Input.dispatchKeyEvent",
            {"type": "keyDown", "key": "Enter", "code": "Enter", "text": "\r"})
        await cdp_method("Input.dispatchKeyEvent",
            {"type": "keyUp", "key": "Enter", "code": "Enter"})
        
        # Step 4: Wait for image
        print("[4/4] Waiting for image generation (up to 120s)...")
        image_url = None
        for i in range(60):
            await asyncio.sleep(2)
            
            result = await js("""(() => {
                const stop = document.querySelector('[data-testid="stop-button"]');
                const streaming = !!stop;
                
                // Find all images in assistant messages
                const imgs = document.querySelectorAll('[data-message-author-role="assistant"] img');
                const urls = [];
                for (const img of imgs) {
                    const src = img.src || '';
                    if (src && !src.startsWith('data:') && img.naturalWidth > 50) {
                        urls.push(src);
                    }
                }
                
                // Also check for links to images
                const links = document.querySelectorAll('[data-message-author-role="assistant"] a[href]');
                const linkUrls = [];
                for (const a of links) {
                    const href = a.href || '';
                    if (href.match(/\\.(png|jpg|jpeg|webp)/i) || href.includes('oai') || href.includes('dalle') || href.includes('blob:')) {
                        linkUrls.push(href);
                    }
                }
                
                return {streaming, urls, linkUrls, imgCount: imgs.length};
            })""")
            
            if i % 5 == 0:
                streaming = result.get("streaming", "?") if result else "?"
                img_count = result.get("imgCount", 0) if result else 0
                url_count = len(result.get("urls", [])) if result else 0
                print(f"  [{i*2}s] streaming={streaming} imgs={img_count} urls={url_count}")
            
            if not result:
                continue
            if not result.get("streaming") and (result.get("urls") or result.get("linkUrls")):
                image_url = (result.get("urls", []) + result.get("linkUrls", []))[-1]
                print(f"\n  Image found: {image_url[:80]}...")
                break
            
            # After streaming stops, wait a bit more for images to load
            if not result.get("streaming") and i > 5:
                # Maybe the image is in a canvas or different element
                canvas_url = await js("""(() => {
                    const canvas = document.querySelector('[data-message-author-role="assistant"] canvas');
                    if (canvas) return canvas.toDataURL('image/png');
                    return null;
                })""")
                if canvas_url and canvas_url.startswith("data:"):
                    # Canvas image found — save as data URL
                    image_url = canvas_url
                    print(f"\n  Canvas image found!")
                    break
        
        if not image_url:
            # Last resort: look for any image-like content
            print("\n  Checking for any image content...")
            image_url = await js("""(() => {
                // Check all images on the page
                const all = document.querySelectorAll('img');
                for (const img of all) {
                    const src = img.src || '';
                    if (src.includes('oai') || src.includes('dalle') || (src.includes('blob:') && img.naturalWidth > 100)) {
                        return src;
                    }
                }
                // Check for download links
                const dlLinks = document.querySelectorAll('a[download], a[href*="generated"]');
                for (const a of dlLinks) return a.href;
                return null;
            })""")
            if image_url:
                print(f"  Found via fallback: {image_url[:80]}")
        
        return image_url

url = asyncio.run(main())
if url:
    # Download the image
    print(f"\nDownloading to ~/Downloads...")
    
    if url.startswith("data:"):
        # Data URL — decode and save
        import base64
        header, data = url.split(",", 1)
        ext = "png" if "png" in header else "jpg"
        out_path = os.path.expanduser(f"~/Downloads/cat-image.{ext}")
        with open(out_path, "wb") as f:
            f.write(base64.b64decode(data))
        print(f"Saved to: {out_path}")
    elif url.startswith("blob:"):
        # Blob URL — need to fetch via CDP
        print("Blob URL detected — fetching via CDP...")
        tab_id = find_tab()
        async def fetch_blob():
            ws_url2 = f"ws://127.0.0.1:{CDP_PORT}/devtools/page/{tab_id}"
            async with websockets.connect(ws_url2, max_size=2**22) as ws2:
                msg_id = 1
                await ws2.send(json.dumps({"id": msg_id, "method": "Runtime.evaluate",
                    "params": {"expression": f"""
                        fetch('{url}').then(r => r.blob()).then(b => {{
                            const reader = new FileReader();
                            reader.onload = () => resolve(reader.result);
                            reader.readAsDataURL(b);
                        }})
                    """, "returnByValue": True, "awaitPromise": True, "timeout": 30000}}))
                while True:
                    raw = await asyncio.wait_for(ws2.recv(), timeout=30)
                    d = json.loads(raw)
                    if d.get("id") == msg_id:
                        data_url = d.get("result",{}).get("result",{}).get("value")
                        if data_url and data_url.startswith("data:"):
                            import base64
                            header, b64data = data_url.split(",", 1)
                            ext = "png" if "png" in header else "jpg"
                            out_path = os.path.expanduser(f"~/Downloads/cat-image.{ext}")
                            with open(out_path, "wb") as f:
                                f.write(base64.b64decode(b64data))
                            print(f"Saved to: {out_path}")
                        else:
                            print(f"Failed to fetch blob: {data_url}")
                        break
        asyncio.run(fetch_blob())
    else:
        # Regular URL — download with curl
        import subprocess
        ext = "png"
        if ".jpg" in url or ".jpeg" in url:
            ext = "jpg"
        elif ".webp" in url:
            ext = "webp"
        out_path = os.path.expanduser(f"~/Downloads/cat-image.{ext}")
        result = subprocess.run(["curl", "-sL", "-o", out_path, url], timeout=30)
        if result.returncode == 0:
            size = os.path.getsize(out_path)
            print(f"Saved to: {out_path} ({size} bytes)")
        else:
            print(f"Download failed: curl returned {result.returncode}")
else:
    print("\nNo image URL found. ChatGPT may not have generated an image.")
    print("Possible reasons: rate limit, content policy, or model doesn't support image gen.")
