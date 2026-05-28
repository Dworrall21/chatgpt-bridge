#!/usr/bin/env python3
"""
chatgpt-deep-research-extractor.py — Resilient deep research extraction.

Tries ALL known methods with a single CDP recv loop (no coroutine collision):

  1. Network interception — captures raw API response before it enters the iframe
  2. Iframe scanning — checks all CDP targets for visible iframe targets
  3. DOM extraction — AX tree, innerText, conversation-turn selectors
  4. Screenshot fallback — takes screenshots if all else fails

Usage:
    python3 chatgpt-deep-research-extractor.py --url CONVERSATION_URL
"""

import sys, json, time, asyncio, urllib.request, os, base64, re
from datetime import datetime
from pathlib import Path

try:
    import websockets
except ImportError:
    print("pip install websockets")
    sys.exit(1)

CDP_PORT = 9222


class CDPSession:
    """Single-recv-loop CDP session. Routes responses to command futures
    and collects network events into a shared buffer."""

    def __init__(self, ws_url):
        self.ws_url = ws_url
        self.ws = None
        self.msg_id = 0
        self._pending = {}       # msg_id -> asyncio.Future
        self._network_events = []  # accumulated network events
        self._loop_task = None
        self._running = True
        self._buffer = asyncio.Queue()

    async def connect(self):
        self.ws = await websockets.connect(self.ws_url, max_size=2**22)
        self._loop_task = asyncio.create_task(self._recv_loop())
        return self

    async def _recv_loop(self):
        while self._running:
            try:
                raw = await asyncio.wait_for(self.ws.recv(), timeout=1)
                d = json.loads(raw)
                mid = d.get("id")

                if mid and mid in self._pending:
                    # This is a response to a command
                    fut = self._pending.pop(mid)
                    if not fut.done():
                        fut.set_result(d)
                elif d.get("method"):
                    # This is an event
                    method = d["method"]
                    params = d.get("params", {})

                    if method == "Network.responseReceived":
                        req_url = params.get("request", {}).get("url", "")
                        resp = params.get("response", {})
                        self._network_events.append({
                            "type": "responseReceived",
                            "url": req_url,
                            "status": resp.get("status"),
                            "mime": resp.get("mimeType", ""),
                            "requestId": params.get("requestId"),
                            "timestamp": time.time(),
                        })

                    elif method == "Network.loadingFinished":
                        self._network_events.append({
                            "type": "loadingFinished",
                            "requestId": params.get("requestId"),
                            "timestamp": time.time(),
                            "encodedDataLength": params.get("encodedDataLength", 0),
                        })

                    elif method == "Runtime.consoleAPICalled":
                        pass  # Ignore console noise
            except asyncio.TimeoutError:
                continue
            except websockets.exceptions.ConnectionClosed:
                break
            except Exception as e:
                if self._running:
                    print(f"  [recv loop error: {e}]")

    async def send(self, method, params=None, timeout=15):
        self.msg_id += 1
        mid = self.msg_id
        cmd = {"id": mid, "method": method}
        if params:
            cmd["params"] = params
        fut = asyncio.get_event_loop().create_future()
        self._pending[mid] = fut
        await self.ws.send(json.dumps(cmd))
        try:
            result = await asyncio.wait_for(fut, timeout=timeout)
            if "error" in result:
                raise RuntimeError(f"CDP error: {result['error']}")
            return result.get("result", {})
        except asyncio.TimeoutError:
            self._pending.pop(mid, None)
            raise TimeoutError(f"CDP command '{method}' timed out after {timeout}s")

    async def js(self, expression, await_promise=False, timeout_ms=5000):
        """Evaluate JS in page context and return the value."""
        result = await self.send("Runtime.evaluate", {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": await_promise,
            "timeout": timeout_ms,
        }, timeout=max(10, timeout_ms // 1000 + 2))
        exc = result.get("exceptionDetails")
        if exc:
            return f"JS_ERROR: {exc.get('text', '')}"
        val = result.get("result", {}).get("value")
        return val

    async def close(self):
        self._running = False
        if self._loop_task:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except:
                pass
        if self.ws:
            await self.ws.close()

    def get_network_events(self):
        """Return and clear network event buffer."""
        events = self._network_events[:]
        self._network_events.clear()
        return events

    def get_intercepted_responses(self):
        """Return collected network responses, deduplicated."""
        events = self._network_events[:]
        # Filter to interesting URLs
        interesting = [e for e in events if "deep_research" in e.get("url", "") or "textdocs" in e.get("url", "")]
        return interesting


def find_tabs():
    req = urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json", timeout=3)
    return json.loads(req.read())


async def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=None, help="Conversation URL")
    parser.add_argument("--out", default=None, help="Output directory")
    parser.add_argument("--wait", type=int, default=20, help="Seconds to wait for iframe content")
    args = parser.parse_args()

    target_url = args.url
    out_dir = Path(args.out or f"/home/david/chatgpt-extension/research-extractions/{datetime.now().strftime('%Y%m%d-%H%M%S')}")
    out_dir.mkdir(parents=True, exist_ok=True)

    results = {"network": None, "dom": None, "iframe": None, "screenshots": []}

    # ── Create/reserve a dedicated tab ──────────────────────
    print("Creating dedicated extraction tab...")
    try:
        new_tab = json.loads(urllib.request.urlopen(
            f"http://127.0.0.1:{CDP_PORT}/json/new", data=b"", timeout=5
        ).read())
        tab_id = new_tab["id"]
        ws_url = f"ws://127.0.0.1:{CDP_PORT}/devtools/page/{tab_id}"
        print(f"  Tab created: {tab_id[:16]}")
    except Exception as e:
        print(f"  Could not create tab: {e}")
        # Fall back to existing tab
        tabs = find_tabs()
        for t in tabs:
            if t.get("type") == "page":
                tab_id = t["id"]
                ws_url = f"ws://127.0.0.1:{CDP_PORT}/devtools/page/{tab_id}"
                print(f"  Using existing tab: {tab_id[:16]}")
                break
        else:
            print("ERROR: No page tab found")
            sys.exit(1)

    cdp = await CDPSession(ws_url).connect()
    print(f"  Connected to CDP\n")

    # ═══════════════════════════════════════════════════════════
    # METHOD 1: Network Interception
    # ═══════════════════════════════════════════════════════════
    print("═══ Method 1: Network Interception ═══")
    print("  Enabling Network + Page...")
    await cdp.send("Network.enable")
    await cdp.send("Page.enable")
    await cdp.send("Runtime.enable")

    # Navigate
    print(f"  Navigating to: {target_url}")
    await cdp.send("Page.navigate", {"url": target_url}, timeout=10)

    # Wait for page to load
    print(f"  Waiting {args.wait}s for content to load...")
    for i in range(args.wait):
        await asyncio.sleep(1)
        ne = len(cdp.get_network_events())
        if i % 5 == 0:
            print(f"    ... {i+1}s ({ne} network events captured)")

    # Re-enable after navigation
    await cdp.send("Runtime.enable")
    await cdp.send("Page.enable")

    # Check network events for interesting responses and try to get bodies
    print(f"\n  Checking network responses...")
    all_events = cdp._network_events  # peek at the internal list

    # Find interesting responseReceived events
    interesting = [e for e in all_events if e.get("type") == "responseReceived"
                   and (e.get("status") == 200 or e.get("status") == 206)
                   and ("conversation" in e.get("url", "") or "deep_research" in e.get("url", "") or "textdocs" in e.get("url", ""))]
    print(f"  Interesting responses: {len(interesting)}")
    for e in interesting[:10]:
        print(f"    {e.get('status')} {e.get('url', '')[:100]}")

    # Try to get bodies for loadingFinished events
    loaded = [e for e in all_events if e.get("type") == "loadingFinished"]
    for le in loaded:
        rid = le.get("requestId")
        if not rid:
            continue
        # Only try if we have a matching response event
        try:
            body = await cdp.send("Network.getResponseBody", {"requestId": rid}, timeout=5)
            if body:
                b64 = body.get("base64Encoded", False)
                body_str = body.get("body", "")
                if b64:
                    body_str = base64.b64decode(body_str).decode("utf-8", errors="replace")
                if len(body_str) > 100:
                    path = out_dir / f"network_{rid[:12]}.json"
                    path.write_text(body_str[:500000])
                    print(f"  ✓ Saved body ({len(body_str)} chars) to {path.name}")
                    results["network"] = f"{len(body_str)} chars from request {rid[:12]}"
        except Exception as e:
            pass

    # ═══════════════════════════════════════════════════════════
    # METHOD 2: DOM Extraction
    # ═══════════════════════════════════════════════════════════
    print(f"\n═══ Method 2: DOM Extraction ═══")

    # Check current URL
    current_url = await cdp.js("location.href")
    print(f"  URL: {current_url}")

    # main.innerText
    main_text = await cdp.js("(() => { var m = document.querySelector('main'); return m ? m.innerText.slice(0, 15000) : 'no main'; })()")
    if main_text:
        print(f"  main.innerText: {len(str(main_text))} chars")
        path = out_dir / "dom_main.txt"
        path.write_text(str(main_text))
        if len(str(main_text)) > 500:
            # Check if there's actual research content vs just user prompts
            if "Research completed" in str(main_text) or "citations" in str(main_text).lower():
                results["dom"] = f"main.innerText: {len(str(main_text))} chars WITH research content"
                print(f"  ✓ Contains research content!")
            else:
                lines = str(main_text).split('\n')
                print(f"  Preview: {lines[0][:80] if lines else 'empty'}")

    # Conversation turns
    turns = await cdp.js("""(() => {
        var t = document.querySelectorAll('[data-testid^="conversation-turn"]');
        var out = [];
        for (var i = 0; i < t.length; i++) {
            var role = t[i].getAttribute('data-message-author-role') || '?';
            var text = (t[i].innerText || '').slice(0, 2000);
            out.push({i: i, role: role, text: text});
        }
        return JSON.stringify(out);
    })()""")
    if turns:
        parsed = json.loads(turns)
        print(f"  Conversation turns: {len(parsed)}")
        for t in parsed:
            role = t.get("role", "?")
            txt = t.get("text", "")
            print(f"    [{t['i']}] role={role} ({len(txt)} chars)")
            if role == "assistant" and len(txt) > 500:
                print(f"      ✓ Assistant response found!")
                results["dom"] = f"assistant response: {len(txt)} chars"
        (out_dir / "dom_turns.json").write_text(json.dumps(parsed, indent=2))

    # AX tree
    await cdp.send("Accessibility.enable")
    ax = await cdp.send("Accessibility.getFullAXTree")
    ax_nodes = ax.get("nodes", [])
    big_nodes = []
    for node in ax_nodes:
        name = node.get("name", {}).get("value", "")
        role = node.get("role", {}).get("value", "")
        if name and len(name) > 500 and role not in ("RootWebArea",):
            big_nodes.append(role + ": " + name[:500])
    if big_nodes:
        print(f"  AX tree: {len(big_nodes)} large text nodes")
        for bn in big_nodes[:3]:
            print(f"    {bn[:100]}...")
        # Save longest
        longest = max(big_nodes, key=len)
        (out_dir / "ax_longest.txt").write_text(longest)
        if len(longest) > 500:
            results["dom"] = f"AX tree: {len(longest)} chars"
    else:
        print(f"  AX tree: no large text nodes found")

    # ═══════════════════════════════════════════════════════════
    # METHOD 3: Iframe Scanning
    # ═══════════════════════════════════════════════════════════
    print(f"\n═══ Method 3: Iframe CDP Scanning ═══")

    # Close session to avoid collisions while scanning iframes
    await cdp.close()

    tabs = find_tabs()
    dr_iframes = [t for t in tabs if "deep_research" in t.get("url", "")]
    print(f"  Deep research iframe targets: {len(dr_iframes)}")

    for i, drt in enumerate(dr_iframes):
        print(f"\n  Iframe {i+1}: {drt['id'][:16]} {drt.get('url','')[:80]}")
        try:
            dr_ws_url = f"ws://127.0.0.1:{CDP_PORT}/devtools/page/{drt['id']}"
            dr_cdp = await CDPSession(dr_ws_url).connect()

            await dr_cdp.send("Runtime.enable", {"timeout": 10000}, timeout=12)
            await dr_cdp.send("Page.enable")

            # innerText
            text = str(await dr_cdp.js("document.body ? document.body.innerText : ''") or "")
            print(f"    innerText: {len(text)} chars")
            if len(text) > 100:
                path = out_dir / f"iframe_{i}_body.txt"
                path.write_text(text)
                results["iframe"] = f"iframe {i}: {len(text)} chars"
                print(f"    ✓ Content found!")
            else:
                # Try innerHTML
                html = str(await dr_cdp.js("document.body ? document.body.innerHTML.slice(0, 3000) : ''") or "")
                print(f"    innerHTML: {len(html)} chars")
                if html:
                    path = out_dir / f"iframe_{i}_body.html"
                    path.write_text(html)

            # Screenshot
            ss = await dr_cdp.send("Page.captureScreenshot", {"format": "jpeg", "quality": 85, "captureBeyondViewport": False})
            if ss:
                data = base64.b64decode(ss["data"])
                path = out_dir / f"iframe_{i}_screenshot.jpg"
                with open(path, "wb") as f:
                    f.write(data)
                results.setdefault("screenshots", []).append(str(path))
                print(f"    Screenshot: {path.name} ({len(data)} bytes)")

            await dr_cdp.close()

        except Exception as e:
            print(f"    Error: {e}")

    # ═══════════════════════════════════════════════════════════
    # METHOD 4: Scrolling Screenshots (on main tab)
    # ═══════════════════════════════════════════════════════════
    print(f"\n═══ Method 4: Scrolling Screenshots ═══")

    # Reconnect to main tab
    main_tabs = [t for t in find_tabs() if t.get("id") == tab_id]
    if not main_tabs:
        print("  Main tab closed, skipping")
    else:
        cdp2 = await CDPSession(ws_url).connect()
        await cdp2.send("Runtime.enable")
        await cdp2.send("Page.enable")

        # Find scroll container
        dims = await cdp2.js("""(() => {
            var all = document.querySelectorAll('*');
            for (var i = 0; i < all.length; i++) {
                var el = all[i];
                var style = window.getComputedStyle(el);
                if ((style.overflowY === 'scroll' || style.overflowY === 'auto') && el.scrollHeight > 2000) {
                    return JSON.stringify({sh: el.scrollHeight, ch: el.clientHeight, st: el.scrollTop});
                }
            }
            return null;
        })()""")

        if dims:
            d = json.loads(dims)
            count = max(2, min(8, d['sh'] // d['ch'] + 1))
            print(f"  Container: {d['sh']}px scroll, {d['ch']}px viewport, {count} screenshots")
            for i in range(count):
                target = int(i * d['sh'] / max(1, count - 1))
                await cdp2.js(f"""
                    (() => {{
                        var all = document.querySelectorAll('*');
                        for (var j = 0; j < all.length; j++) {{
                            var el = all[j];
                            var style = window.getComputedStyle(el);
                            if ((style.overflowY === 'scroll' || style.overflowY === 'auto') && el.scrollHeight > 2000) {{
                                el.scrollTop = {target};
                                return;
                            }}
                        }}
                    }})()
                """)
                await asyncio.sleep(0.5)
                ss = await cdp2.send("Page.captureScreenshot", {"format": "jpeg", "quality": 80, "captureBeyondViewport": False})
                if ss:
                    data = base64.b64decode(ss["data"])
                    path = out_dir / f"scroll_{i}_y{target}.jpg"
                    with open(path, "wb") as f:
                        f.write(data)
                    results.setdefault("screenshots", []).append(str(path))
                    print(f"  [{i+1}/{count}] y={target} → {path.name} ({len(data)} bytes)")

        await cdp2.close()

    # ═══════════════════════════════════════════════════════════
    # Summary
    # ═══════════════════════════════════════════════════════════
    print(f"\n{'='*60}")
    print(f"  EXTRACTION RESULTS")
    print(f"{'='*60}")
    print(f"  Output: {out_dir}")

    for method, label in [("network", "Network Intercept"),
                           ("dom", "DOM / AX Tree"),
                           ("iframe", "Iframe Scanning")]:
        if results.get(method):
            print(f"  ✓ {label}: {results[method]}")
        else:
            print(f"  ✗ {label}: no content found")

    screenshots = results.get("screenshots", [])
    if screenshots:
        print(f"  Screenshots: {len(screenshots)} files")
        for s in screenshots:
            print(f"    {s}")

    # Write summary
    files = sorted(str(f) for f in out_dir.iterdir())
    summary = {
        "url": target_url,
        "timestamp": datetime.utcnow().isoformat(),
        "results": {k: str(v) if v else None for k, v in results.items()},
        "files": files,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    print(f"\n  Summary: {out_dir / 'summary.json'}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopped by user")
