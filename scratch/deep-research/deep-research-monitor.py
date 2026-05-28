#!/usr/bin/env python3
"""
deep-research-monitor.py — Submit deep research & monitor progress via CDP.

Commands:
  submit <prompt>          Submit a new deep research, save session info, print conversation_id
  monitor <conversation_id>  Check status, screenshot, extract if done
  extract <conversation_id>  Force extraction via Ctrl+A → Ctrl+C

Output directory: ~/chatgpt-extension/research-sessions/<conversation_id>/
  screenshots/   - Periodic progress screenshots
  report.md      - Final extracted report (when complete)
  session.json   - Metadata (prompt, status, timestamps)
"""

import json, asyncio, urllib.request, websockets, base64, os, sys, time
from pathlib import Path

CDP_PORT = 9222
SESSION_DIR = Path(os.path.expanduser("~/chatgpt-extension/research-sessions"))

async def raw(ws, msg_id, method, params=None, timeout=10):
    """Send CDP command and wait for response."""
    msg_id += 1
    cmd = {'id': msg_id, 'method': method}
    if params: cmd['params'] = params
    await ws.send(json.dumps(cmd))
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            rd = await asyncio.wait_for(ws.recv(), timeout=5)
            d = json.loads(rd)
            if d.get('id') == msg_id: return d, msg_id
        except asyncio.TimeoutError: continue
    return None, msg_id

async def js(ws, msg_id, expr):
    """Evaluate JS expression and return value."""
    r, msg_id = await raw(ws, msg_id, 'Runtime.evaluate', {
        'expression': expr, 'returnByValue': True})
    if r:
        try: return r.get('result',{}).get('result',{}).get('value'), msg_id
        except: pass
    return None, msg_id

def save_screenshot(data, path):
    """Save base64 screenshot to disk."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'wb') as f:
        f.write(base64.b64decode(data))

async def submit(prompt):
    """Submit a deep research prompt and return conversation_id."""
    msg_id = 0
    req = urllib.request.urlopen(f'http://127.0.0.1:{CDP_PORT}/json', timeout=3)
    tabs = json.loads(req.read())
    tab = next((t for t in tabs if 'chatgpt.com' in t.get('url','')), None)
    if not tab:
        print("ERROR: No ChatGPT tab found", file=sys.stderr)
        sys.exit(1)
    
    async with websockets.connect(
        f'ws://127.0.0.1:{CDP_PORT}/devtools/page/{tab["id"]}', max_size=2**22
    ) as ws:
        # --- Step 1: Navigate to fresh chat ---
        r, msg_id = await raw(ws, msg_id, 'Page.navigate',
                              {'url': 'https://chatgpt.com/'})
        await asyncio.sleep(6)
        r, msg_id = await raw(ws, msg_id, 'Runtime.enable')
        
        # --- Step 2: Find and click plus button ---
        val, msg_id = await js(ws, msg_id,
            '(()=>{var b=document.querySelector("[data-testid=composer-plus-btn]");'
            'if(!b)return"null";var r=b.getBoundingClientRect();'
            'return JSON.stringify({x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)})})()')
        if val == 'null' or val is None:
            print("ERROR: Plus button not found", file=sys.stderr)
            sys.exit(1)
        plus = json.loads(val)
        print(f"Plus btn: ({plus['x']}, {plus['y']})")
        for etype in ['mouseMoved','mousePressed','mouseReleased']:
            r, msg_id = await raw(ws, msg_id, 'Input.dispatchMouseEvent',
                {'type': etype, 'x': plus['x'], 'y': plus['y'], 'button': 'left', 'clickCount': 1})
        await asyncio.sleep(2)
        
        # --- Step 3: Find and click Deep research ---
        r, msg_id = await raw(ws, msg_id, 'Accessibility.enable')
        r, msg_id = await raw(ws, msg_id, 'Accessibility.getFullAXTree')
        nodes = r.get('result',{}).get('nodes',[])
        dr_node = None
        for node in nodes:
            name = node.get('name',{}).get('value','')
            if node.get('role',{}).get('value','') == 'menuitemradio' and 'deep' in name.lower():
                dr_node = node; break
        if not dr_node:
            print("ERROR: Deep research not in menu", file=sys.stderr)
            sys.exit(1)
        
        dom_id = dr_node['backendDOMNodeId']
        r, msg_id = await raw(ws, msg_id, 'DOM.getBoxModel', {'backendNodeId': dom_id})
        if r and r.get('result',{}).get('model'):
            model = r['result']['model']
            cx = round(model['content'][0] + model['width']/2)
            cy = round(model['content'][1] + model['height']/2)
        else:
            print("ERROR: No box model", file=sys.stderr)
            sys.exit(1)
        
        for etype in ['mouseMoved','mousePressed','mouseReleased']:
            r, msg_id = await raw(ws, msg_id, 'Input.dispatchMouseEvent',
                {'type': etype, 'x': cx, 'y': cy, 'button': 'left', 'clickCount': 1})
        await asyncio.sleep(2)
        
        # --- Step 4: Type prompt ---
        val, msg_id = await js(ws, msg_id,
            '(()=>{var pm=document.querySelector(".ProseMirror");'
            'if(!pm)return"null";var r=pm.getBoundingClientRect();'
            'return JSON.stringify({x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)})})()')
        if val == 'null' or val is None:
            print("ERROR: ProseMirror not found", file=sys.stderr)
            sys.exit(1)
        pm = json.loads(val)
        for etype in ['mouseMoved','mousePressed','mouseReleased']:
            r, msg_id = await raw(ws, msg_id, 'Input.dispatchMouseEvent',
                {'type': etype, 'x': pm['x'], 'y': pm['y'], 'button': 'left', 'clickCount': 1})
        await asyncio.sleep(0.5)
        r, msg_id = await raw(ws, msg_id, 'Input.insertText', {'text': prompt})
        await asyncio.sleep(1)
        
        # --- Step 5: Submit via Enter ---
        for kt in ['rawKeyDown','keyUp']:
            r, msg_id = await raw(ws, msg_id, 'Input.dispatchKeyEvent',
                {'type': kt, 'key': 'Enter', 'code': 'Enter', 'windowsVirtualKeyCode': 13})
        await asyncio.sleep(3)
        
        # --- Step 6: Get conversation_id from URL ---
        url, msg_id = await js(ws, msg_id, 'window.location.href')
        cid = url.split('/')[-1] if url else 'unknown'
        
        # Save session info
        session_dir = SESSION_DIR / cid
        session_dir.mkdir(parents=True, exist_ok=True)
        info = {
            'conversation_id': cid,
            'url': url,
            'prompt': prompt,
            'submitted_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
            'status': 'submitted',
        }
        with open(session_dir / 'session.json', 'w') as f:
            json.dump(info, f, indent=2)
        
        # Initial screenshot
        r, msg_id = await raw(ws, msg_id, 'Page.captureScreenshot',
                              {'format': 'jpeg', 'quality': 80, 'fromSurface': True})
        if r and 'data' in r:
            save_screenshot(r['data'], session_dir / f'screenshots/initial.jpg')
        
        print(cid)
        return cid

async def monitor(cid):
    """Check research status, take screenshot, extract if complete."""
    session_dir = SESSION_DIR / cid
    session_dir.mkdir(parents=True, exist_ok=True)
    screenshots_dir = session_dir / 'screenshots'
    screenshots_dir.mkdir(parents=True, exist_ok=True)
    
    # Load session info
    info = {}
    info_path = session_dir / 'session.json'
    if info_path.exists():
        with open(info_path) as f: info = json.load(f)
    
    msg_id = 0
    req = urllib.request.urlopen(f'http://127.0.0.1:{CDP_PORT}/json', timeout=3)
    tabs = json.loads(req.read())
    tab = next((t for t in tabs if t.get('url','').startswith('https://chatgpt.com')), None)
    if not tab:
        print("ERROR: No ChatGPT tab found", file=sys.stderr)
        return
    
    async with websockets.connect(
        f'ws://127.0.0.1:{CDP_PORT}/devtools/page/{tab["id"]}', max_size=2**22
    ) as ws:
        # Navigate to the conversation
        r, msg_id = await raw(ws, msg_id, 'Page.navigate',
                              {'url': f'https://chatgpt.com/c/{cid}'})
        await asyncio.sleep(6)
        r, msg_id = await raw(ws, msg_id, 'Runtime.enable')
        
        # Check status
        stop, msg_id = await js(ws, msg_id,
            '!!document.querySelector("[data-testid=stop-button]")')
        dr_mode, msg_id = await js(ws, msg_id,
            '!!document.querySelector("[aria-label*=\\\"Deep research\\\"]")')
        
        # Take timestamped screenshot
        ts = time.strftime('%Y%m%d-%H%M%S')
        r, msg_id = await raw(ws, msg_id, 'Page.captureScreenshot',
                              {'format': 'jpeg', 'quality': 80, 'fromSurface': True})
        if r and 'data' in r:
            save_screenshot(r['data'], screenshots_dir / f'progress_{ts}.jpg')
        
        # Check if research completed (if no stop button and report content exists)
        if stop:
            status = 'running'
            print(f"STATUS=running  Screenshot=screenshots/progress_{ts}.jpg")
        else:
            # Try to extract content
            r, msg_id = await raw(ws, msg_id, 'Input.enable')
            
            iframes_str, msg_id = await js(ws, msg_id,
                '(()=>{var f=document.querySelectorAll("iframe[src*=deep_research]");'
                'if(!f.length)return"null";var r=f[0].getBoundingClientRect();'
                'return JSON.stringify({x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)})})()')
            
            if iframes_str and iframes_str != 'null':
                ctr = json.loads(iframes_str)
                # Triple-click iframe to select all
                for etype in ['mouseMoved','mousePressed','mouseReleased']:
                    r, msg_id = await raw(ws, msg_id, 'Input.dispatchMouseEvent',
                        {'type': etype, 'x': ctr['x'], 'y': ctr['y'], 'button': 'left', 'clickCount': 3})
                await asyncio.sleep(1)
                
                # Ctrl+A
                for kt in ['rawKeyDown','keyUp']:
                    r, msg_id = await raw(ws, msg_id, 'Input.dispatchKeyEvent',
                        {'type': kt, 'modifiers': 2, 'key': 'a', 'code': 'KeyA'})
                await asyncio.sleep(0.5)
                
                # Ctrl+C
                for kt in ['rawKeyDown','keyUp']:
                    r, msg_id = await raw(ws, msg_id, 'Input.dispatchKeyEvent',
                        {'type': kt, 'modifiers': 2, 'key': 'c', 'code': 'KeyC'})
                await asyncio.sleep(0.5)
                
                content, msg_id = await js(ws, msg_id, 'navigator.clipboard.readText()')
                content = content or ''
                
                if len(content) > 100:
                    # Successfully extracted!
                    status = 'complete'
                    report_path = session_dir / 'report.md'
                    with open(report_path, 'w') as f: f.write(content)
                    print(f"STATUS=complete  Report=report.md ({len(content)} chars)")
                else:
                    status = 'waiting'
                    print(f"STATUS=waiting  Content={len(content)} chars  Screenshot=screenshots/progress_{ts}.jpg")
            else:
                status = 'waiting'
                print(f"STATUS=waiting  No iframe  Screenshot=screenshots/progress_{ts}.jpg")
        
        info['status'] = status
        info['last_checked'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        with open(info_path, 'w') as f: json.dump(info, f, indent=2)

async def extract(cid):
    """Force extract report content from completed research."""
    session_dir = SESSION_DIR / cid
    session_dir.mkdir(parents=True, exist_ok=True)
    
    msg_id = 0
    req = urllib.request.urlopen(f'http://127.0.0.1:{CDP_PORT}/json', timeout=3)
    tabs = json.loads(req.read())
    tab = next((t for t in tabs if t.get('url','').startswith('https://chatgpt.com')), None)
    if not tab: print("ERROR: No ChatGPT tab"); return
    
    async with websockets.connect(
        f'ws://127.0.0.1:{CDP_PORT}/devtools/page/{tab["id"]}', max_size=2**22
    ) as ws:
        r, msg_id = await raw(ws, msg_id, 'Page.navigate',
                              {'url': f'https://chatgpt.com/c/{cid}'})
        await asyncio.sleep(6)
        r, msg_id = await raw(ws, msg_id, 'Runtime.enable')
        r, msg_id = await raw(ws, msg_id, 'Input.enable')
        
        iframes_str, msg_id = await js(ws, msg_id,
            '(()=>{var f=document.querySelectorAll("iframe[src*=deep_research]");'
            'if(!f.length)return"null";var r=f[0].getBoundingClientRect();'
            'return JSON.stringify({x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)})})()')
        if not iframes_str or iframes_str == 'null':
            print("No deep research iframe found")
            return
        
        ctr = json.loads(iframes_str)
        for etype in ['mouseMoved','mousePressed','mouseReleased']:
            r, msg_id = await raw(ws, msg_id, 'Input.dispatchMouseEvent',
                {'type': etype, 'x': ctr['x'], 'y': ctr['y'], 'button': 'left', 'clickCount': 3})
        await asyncio.sleep(1)
        for kt in ['rawKeyDown','keyUp']:
            r, msg_id = await raw(ws, msg_id, 'Input.dispatchKeyEvent',
                {'type': kt, 'modifiers': 2, 'key': 'a', 'code': 'KeyA'})
        await asyncio.sleep(0.5)
        for kt in ['rawKeyDown','keyUp']:
            r, msg_id = await raw(ws, msg_id, 'Input.dispatchKeyEvent',
                {'type': kt, 'modifiers': 2, 'key': 'c', 'code': 'KeyC'})
        await asyncio.sleep(0.5)
        
        content, msg_id = await js(ws, msg_id, 'navigator.clipboard.readText()')
        content = content or ''
        report_path = session_dir / 'report.md'
        with open(report_path, 'w') as f: f.write(content)
        print(f"Report saved: {report_path} ({len(content)} chars)")

if __name__ == '__main__':
    if len(sys.argv) < 3:
        print("Usage:")
        print("  python3 deep-research-monitor.py submit <prompt>")
        print("  python3 deep-research-monitor.py monitor <conversation_id>")
        print("  python3 deep-research-monitor.py extract <conversation_id>")
        sys.exit(1)
    
    cmd = sys.argv[1]
    arg = sys.argv[2]
    
    if cmd == 'submit':
        cid = asyncio.run(submit(arg))
        print(f"\nConversation: https://chatgpt.com/c/{cid}")
        print(f"To monitor: python3 deep-research-monitor.py monitor {cid}")
    elif cmd == 'monitor':
        asyncio.run(monitor(arg))
    elif cmd == 'extract':
        asyncio.run(extract(arg))
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
