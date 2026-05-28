#!/usr/bin/env python3
"""Clean up test ChatGPT conversations.

Two modes:
  1. Named IDs: python3 cleanup-test-chats.py --ids <ID1> <ID2> ...
  2. Search & destroy: python3 cleanup-test-chats.py --find "[bridge-test]"

Requires Chrome running with --remote-debugging-port=9222
and a logged-in ChatGPT session.
"""

import json, time, sys, urllib.request, asyncio, argparse

CDP_PORT = 9222


def find_chatgpt_tab():
    req = urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json", timeout=3)
    tabs = json.loads(req.read())
    for t in tabs:
        if "chatgpt.com" in t.get("url", ""):
            return t
    return None


async def cdp_eval(ws, expr, timeout_s=10):
    rid = int(time.time() * 1_000_000) % 1_000_000
    await ws.send(json.dumps({
        "id": rid, "method": "Runtime.evaluate",
        "params": {"expression": expr, "returnByValue": True,
                   "timeout": int(timeout_s * 1000)},
    }))
    while True:
        msg = await asyncio.wait_for(ws.recv(), timeout=timeout_s + 5)
        data = json.loads(msg)
        if data.get("id") == rid:
            exc = data.get("result", {}).get("exceptionDetails")
            if exc:
                print(f"  CDP exception: {exc.get('text', '')}")
                return None
            return data.get("result", {}).get("result", {}).get("value")


async def cdp_raw(ws, method, params):
    rid = int(time.time() * 1_000_000) % 1_000_000
    await ws.send(json.dumps({"id": rid, "method": method, "params": params}))
    while True:
        msg = await asyncio.wait_for(ws.recv(), timeout=10)
        data = json.loads(msg)
        if data.get("id") == rid:
            return data


async def click_at(ws, x, y):
    await asyncio.sleep(0.1)
    await cdp_raw(ws, "Input.dispatchMouseEvent", {
        "type": "mousePressed", "x": round(x), "y": round(y),
        "button": "left", "clickCount": 1,
    })
    await asyncio.sleep(0.05)
    await cdp_raw(ws, "Input.dispatchMouseEvent", {
        "type": "mouseReleased", "x": round(x), "y": round(y),
        "button": "left", "clickCount": 1,
    })
    await asyncio.sleep(0.1)


async def delete_conversation(ws, conv_id):
    """Delete a single conversation by navigating to its page and using the header menu."""
    print(f"\n--- Delete {conv_id[:8]}... ---")

    # Navigate to the conversation
    await cdp_raw(ws, "Page.navigate", {"url": f"https://chatgpt.com/c/{conv_id}"})
    await asyncio.sleep(4)

    # Check it exists in sidebar
    exists = await cdp_eval(ws, f"""
        document.querySelector('a[href*="/c/{conv_id}"]') !== null
    """)
    if not exists:
        print(f"  Not found in sidebar, skipping")
        return False

    # Find header menu button
    menu_rect = await cdp_eval(ws, """
        (() => {
            var selectors = [
                'button[data-testid="conversation-options-button"]',
                'button[aria-label*="options"]',
                'button[aria-label*="Options"]',
                'button[aria-label*="More"]',
                'button[aria-label*="more"]',
                'button[data-testid*="menu"]',
            ];
            for (var s of selectors) {
                var btn = document.querySelector(s);
                if (!btn) continue;
                var r = btn.getBoundingClientRect();
                if (r.width > 0 && r.height > 0) {
                    return JSON.stringify({
                        x: Math.round(r.x + r.width/2),
                        y: Math.round(r.y + r.height/2)
                    });
                }
            }
            return null;
        })()
    """, timeout_s=5)
    if not menu_rect:
        print(f"  Header menu not found")
        return False

    import json as _j
    mr = _j.loads(menu_rect)
    print(f"  Header menu at ({mr['x']}, {mr['y']})")
    await click_at(ws, mr["x"], mr["y"])
    await asyncio.sleep(1.5)

    # Find Delete in dropdown
    del_rect = await cdp_eval(ws, """
        (() => {
            var items = document.querySelectorAll('[role="menuitem"]');
            for (var i = 0; i < items.length; i++) {
                if (items[i].textContent.trim() === 'Delete') {
                    var r = items[i].getBoundingClientRect();
                    return JSON.stringify({
                        x: Math.round(r.x + r.width/2),
                        y: Math.round(r.y + r.height/2)
                    });
                }
            }
            return null;
        })()
    """, timeout_s=3)
    if not del_rect:
        print(f"  Delete menu item not found")
        return False

    import json as _j2
    dr = _j2.loads(del_rect)
    print(f"  Clicking Delete at ({dr['x']}, {dr['y']})")
    await click_at(ws, dr["x"], dr["y"])
    await asyncio.sleep(1.5)

    # Confirm deletion dialog if it appears
    confirm = await cdp_eval(ws, """
        (() => {
            var btns = document.querySelectorAll('button');
            for (var i = 0; i < btns.length; i++) {
                var txt = btns[i].textContent.trim().toLowerCase();
                if (txt === 'delete' || txt === 'confirm') {
                    var r = btns[i].getBoundingClientRect();
                    if (r.width > 0 && r.height > 0) {
                        return JSON.stringify({
                            x: Math.round(r.x + r.width/2),
                            y: Math.round(r.y + r.height/2)
                        });
                    }
                }
            }
            return null;
        })()
    """, timeout_s=3)
    if confirm:
        import json as _j3
        cr = _j3.loads(confirm)
        print(f"  Confirming at ({cr['x']}, {cr['y']})")
        await click_at(ws, cr["x"], cr["y"])
        await asyncio.sleep(2)

    print(f"  ✓ Deleted")
    return True


async def search_and_destroy(ws, search_text):
    """Search sidebar for conversations matching text and delete each one."""
    print(f"\nSearching sidebar for \"{search_text}\"...")

    # Navigate to main page to load sidebar
    await cdp_raw(ws, "Page.navigate", {"url": "https://chatgpt.com/"})
    await asyncio.sleep(5)

    # Find matching conversation IDs
    found_json = await cdp_eval(ws, f"""
        (() => {{
            var links = document.querySelectorAll('nav a[href*="/c/"]');
            var matches = [];
            for (var i = 0; i < links.length; i++) {{
                var title = (links[i].textContent || '').trim();
                if (title.toLowerCase().indexOf('{search_text.lower()}') >= 0) {{
                    var href = links[i].getAttribute('href');
                    var m = href.match(/\\/c\\/([a-f0-9-]+)/);
                    if (m) {{
                        matches.push({{conv_id: m[1], title: title}});
                    }}
                }}
            }}
            return JSON.stringify(matches);
        }})()
    """)
    if not found_json:
        print("  No matches found (or sidebar not loaded)")
        return []

    import json as _j
    matches = _j.loads(found_json)
    print(f"  Found {len(matches)} conversation(s):")
    for m in matches:
        print(f"    {m['conv_id'][:8]} — {m['title'][:60]}")

    if not matches:
        return []

    for m in matches:
        await delete_conversation(ws, m["conv_id"])
        await asyncio.sleep(2)

    return matches


async def main():
    parser = argparse.ArgumentParser(
        description="Clean up test ChatGPT conversations via CDP."
    )
    parser.add_argument("--ids", nargs="+", default=None,
                        help="Specific conversation IDs to delete")
    parser.add_argument("--find", type=str, default=None,
                        help="Search sidebar for conversations matching this text and delete them")
    parser.add_argument("--list", action="store_true",
                        help="List recent sidebar conversations without deleting")
    args = parser.parse_args()

    if not args.ids and not args.find and not args.list:
        parser.print_help()
        print("\nProvide --ids, --find, or --list to do something.")
        return 1

    tab = find_chatgpt_tab()
    if not tab:
        print("No ChatGPT tab found. Start Chrome with --remote-debugging-port=9222")
        return 1

    ws_url = tab["webSocketDebuggerUrl"]
    print(f"Connected to: {tab.get('url', '?')[:60]}")

    import websockets
    async with websockets.connect(ws_url, max_size=2**20) as ws:
        if args.list:
            await cdp_raw(ws, "Page.navigate", {"url": "https://chatgpt.com/"})
            await asyncio.sleep(5)
            sidebar = await cdp_eval(ws, """
                (() => {
                    var links = Array.from(document.querySelectorAll('nav a[href*="/c/"]'));
                    return JSON.stringify(links.map(function(a) {
                        var m = a.getAttribute('href').match(/\\/c\\/([a-f0-9-]+)/);
                        return {
                            conv_id: m ? m[1] : '?',
                            title: (a.textContent || '').trim().slice(0, 80)
                        };
                    }));
                })()
            """)
            if sidebar:
                import json as _j
                for item in _j.loads(sidebar):
                    print(f"  {item['conv_id'][:8]}  {item['title']}")
            return 0

        if args.ids:
            total = 0
            for cid in args.ids:
                ok = await delete_conversation(ws, cid)
                if ok:
                    total += 1
                await asyncio.sleep(2)
            print(f"\nDeleted: {total}/{len(args.ids)}")

        if args.find:
            deleted = await search_and_destroy(ws, args.find)
            print(f"\nDeleted: {len(deleted)} conversations")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
