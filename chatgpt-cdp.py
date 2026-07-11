#!/usr/bin/env python3
"""CDP client: GPT-5.6 Sol/High, one MoA chat per Hermes session."""
from __future__ import annotations

import argparse, asyncio, hashlib, json, os, re, sqlite3, sys, time, urllib.request
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urljoin, urlparse
import websockets
try:
    import fcntl
except ImportError:  # Linux is the supported Hermes host.
    fcntl = None

CDP_PORT = int(os.getenv("CHATGPT_CDP_PORT", "9222"))
ORIGIN = "https://chatgpt.com"
DEFAULT_PROJECT = os.getenv("CHATGPT_CDP_PROJECT", "MoA")
DEFAULT_MODEL = os.getenv("CHATGPT_CDP_MODEL", "5.6 Sol")
DEFAULT_EFFORT = os.getenv("CHATGPT_CDP_EFFORT", "High")
DEFAULT_TIMEOUT = int(os.getenv("CHATGPT_CDP_TIMEOUT", "600"))
DEFAULT_STATE = Path(os.getenv("CHATGPT_CDP_STATE_FILE", "~/.hermes/chatgpt_bridge_state/chatgpt-cdp.json")).expanduser()
DEFAULT_HERMES_DB = Path(os.getenv("HERMES_STATE_DB", "~/.hermes/state.db")).expanduser()
CONV_RE = re.compile(r"/c/([A-Za-z0-9-]+)")


def clean(value):
    return re.sub(r"\s+", " ", str(value or "")).strip().strip("-–—:;,. ")


def hermes_summary(session_id, prompt, override=None, db_path=DEFAULT_HERMES_DB):
    for value in (override, os.getenv("HERMES_SESSION_SUMMARY"), os.getenv("HERMES_SESSION_TITLE")):
        if clean(value):
            return clean(value)
    path = Path(db_path).expanduser()
    if path.is_file():
        try:
            with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=1) as db:
                row = db.execute("SELECT title FROM sessions WHERE id = ? LIMIT 1", (session_id,)).fetchone()
            if row and clean(row[0]):
                return clean(row[0])
        except (OSError, sqlite3.Error):
            pass
    first = next((clean(re.sub(r"^[#>*`\-\s]+", "", x)) for x in prompt.splitlines() if clean(x)), "Hermes session")
    return first[:77].rstrip() + "..." if len(first) > 80 else first


def chat_title(summary, session_id):
    if not clean(session_id):
        raise ValueError("Hermes session id is required")
    suffix = " — " + clean(session_id)
    summary = clean(summary) or "Hermes session"
    room = max(1, 180 - len(suffix))
    if len(summary) > room:
        summary = summary[:max(1, room - 3)].rstrip() + "..."
    return summary + suffix


def conv_id(url):
    match = CONV_RE.search(str(url or ""))
    return match.group(1) if match else None


class State:
    def __init__(self, path=DEFAULT_STATE):
        self.path = Path(path).expanduser()
        self.data = {"version": 1, "sessions": {}}
        try:
            loaded = json.loads(self.path.read_text())
            if isinstance(loaded, dict) and isinstance(loaded.get("sessions"), dict):
                self.data = loaded
        except (OSError, json.JSONDecodeError):
            pass

    def get(self, session_id):
        value = self.data["sessions"].get(session_id)
        return dict(value) if isinstance(value, dict) else {}

    def set(self, session_id, **fields):
        entry = self.get(session_id)
        entry.update({k: v for k, v in fields.items() if v is not None})
        entry["updated_at"] = int(time.time())
        self.data["sessions"][session_id] = entry
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.data, indent=2, sort_keys=True))
        os.replace(tmp, self.path)


@contextmanager
def lock_session(state_path, session_id):
    digest = hashlib.sha256(session_id.encode()).hexdigest()[:24]
    lock_dir = Path(state_path).expanduser().parent / "locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    with (lock_dir / f"chatgpt-cdp-{digest}.lock").open("a+") as handle:
        if fcntl:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def find_tab():
    tabs = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json", timeout=3).read())
    tabs = [t for t in tabs if t.get("type") == "page" and "chatgpt.com" in t.get("url", "")]
    tabs.sort(key=lambda t: ("/c/" not in t.get("url", ""), "/g/" not in t.get("url", "")))
    return tabs[0] if tabs else None


class CDP:
    def __init__(self, ws):
        self.ws, self.seq = ws, 0

    async def call(self, method, params=None, timeout=20):
        self.seq += 1
        rid = self.seq
        await self.ws.send(json.dumps({"id": rid, "method": method, "params": params or {}}))
        while True:
            msg = json.loads(await asyncio.wait_for(self.ws.recv(), timeout=timeout))
            if msg.get("id") != rid:
                continue
            if msg.get("error"):
                raise RuntimeError(f"CDP {method}: {msg['error']}")
            return msg.get("result", {})

    async def js(self, code, timeout=20):
        result = await self.call("Runtime.evaluate", {"expression": code, "returnByValue": True, "awaitPromise": True, "timeout": int(timeout * 1000)}, timeout + 5)
        if result.get("exceptionDetails"):
            details = result["exceptionDetails"]
            raise RuntimeError(details.get("exception", {}).get("description") or details.get("text") or "JavaScript error")
        return result.get("result", {}).get("value")

    async def wait(self, condition, label, timeout=30):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                if await self.js(f"Boolean({condition})", min(timeout, 10)):
                    return
            except Exception:
                pass
            await asyncio.sleep(.25)
        raise RuntimeError(f"Timed out waiting for {label}")

    async def url(self):
        return str(await self.js("location.href"))

    async def navigate(self, url):
        absolute = urljoin(ORIGIN + "/", url)
        parsed = urlparse(absolute)
        if parsed.scheme != "https" or parsed.hostname not in {"chatgpt.com", "www.chatgpt.com"}:
            raise RuntimeError(f"Unsafe ChatGPT navigation: {absolute}")
        await self.call("Page.navigate", {"url": absolute}, 10)
        await self.wait("document.readyState !== 'loading'", "page load")

    async def input_ready(self):
        await self.wait("document.querySelector('#prompt-textarea')", "prompt input")

    async def sidebar(self):
        await self.js("document.querySelector('button[aria-label=\"Open sidebar\"]')?.click(); true")
        await asyncio.sleep(.4)

    async def project(self, name, saved_url=None):
        if saved_url:
            await self.navigate(saved_url)
            await self.input_ready()
            return await self.url()
        await self.sidebar()
        result = await self.js(f"""(()=>{{
const want={json.dumps(name)}.toLowerCase(), norm=x=>String(x||'').replace(/\\s+/g,' ').trim().toLowerCase();
for(const el of document.querySelectorAll('a[href],button,[role=\"link\"]')){{
 const a=el.closest('a[href]')||(el.matches('a[href]')?el:null), href=a?.getAttribute('href')||'';
 if(norm(el.getAttribute('aria-label')||el.textContent)===want && (el.closest('nav,aside,#history')||/\\/g\\/|project/i.test(href))){{(a||el).click();return true;}}
}} return false;}})()""")
        if not result:
            raise RuntimeError(f'Could not find project "{name}"; refusing a non-project fallback')
        await asyncio.sleep(2)
        await self.input_ready()
        return await self.url()

    async def find_title(self, title):
        await self.sidebar()
        return await self.js(f"""(()=>{{const w={json.dumps(title)},n=x=>String(x||'').replace(/\\s+/g,' ').trim();
const a=[...document.querySelectorAll('a[href*=\"/c/\"]')].find(x=>n(x.getAttribute('aria-label'))===w||n(x.textContent)===w);
return a?new URL(a.getAttribute('href'),location.origin).href:null;}})()""")

    async def valid_chat(self, title):
        try:
            await self.input_ready()
            if not conv_id(await self.url()):
                return False
            await self.sidebar()
            return bool(await self.find_title(title))
        except Exception:
            return False

    async def configure(self, model, effort):
        result = await self.js(f"""(async()=>{{
const sleep=x=>new Promise(r=>setTimeout(r,x)), norm=x=>String(x||'').toLowerCase().replace(/[^a-z0-9]+/g,' ').replace(/\\s+/g,' ').trim();
const click=e=>{{if(!e)return false;e.scrollIntoView({{block:'center'}});e.click();return true;}};
const wait=async f=>{{for(let i=0;i<40;i++){{const x=f();if(x)return x;await sleep(200);}}return null;}};
const open=async()=>{{const pill=await wait(()=>document.querySelector('button.__composer-pill,.__composer-pill,button[aria-label*=\"model\" i]'));if(!pill)throw Error('Model selector not found');click(pill);
 const c=await wait(()=>document.querySelector('[data-testid=\"model-configure-modal\"]')||[...document.querySelectorAll('[role=\"menuitem\"]')].find(x=>norm(x.textContent).includes('configure')));if(c){{click(c);await sleep(300);}}
 return wait(()=>document.querySelector('[data-testid=\"modal-intelligence-menu\"]')||[...document.querySelectorAll('[role=\"dialog\"],[role=\"menu\"],[role=\"listbox\"]')].find(x=>/model|intelligence|reasoning|thinking/i.test(x.textContent||'')));}};
let modal=await open();if(!modal)throw Error('Model configuration did not open');
const wanted=norm({json.dumps(model)}), tokens=wanted.split(' ').filter(Boolean);
const models=[...modal.querySelectorAll('[role=\"radio\"],[role=\"option\"],[role=\"menuitem\"],button')].map((e,i)=>{{const l=norm(e.getAttribute('aria-label')||e.textContent);return tokens.every(t=>l.includes(t))?{{e,l,i}}:null;}}).filter(Boolean).sort((a,b)=>a.l.length-b.l.length||a.i-b.i);
if(!models.length)throw Error('Required model not found: '+{json.dumps(model)});click(models[0].e);await sleep(400);
modal=document.querySelector('[data-testid=\"modal-intelligence-menu\"]')||await open();if(!modal)throw Error('Effort configuration did not open');
const wantedEffort=norm({json.dumps(effort)});let chosen=null;
for(const e of modal.querySelectorAll('[data-testid*=\"effort\" i],[role=\"radio\"],[role=\"option\"],[role=\"menuitem\"],button')){{
 const l=norm(e.getAttribute('aria-label')||e.textContent);if(!(l===wantedEffort||l.startsWith(wantedEffort+' ')))continue;
 let p=e,ctx='';while(p&&p!==modal.parentElement){{ctx+=' '+norm(p.getAttribute?.('data-testid'))+' '+norm(p.getAttribute?.('aria-label'))+' '+norm(p.textContent);if(/effort|reasoning|thinking/.test(ctx)){{chosen=e;break;}}p=p.parentElement;}}if(chosen)break;
}}
if(!chosen)throw Error('Required reasoning effort not found: '+{json.dumps(effort)});click(chosen);await sleep(300);document.dispatchEvent(new KeyboardEvent('keydown',{{key:'Escape',bubbles:true}}));
return JSON.stringify({{model:models[0].l,effort:norm(chosen.textContent||chosen.getAttribute('aria-label'))}});}})()""", 30)
        return json.loads(result)

    async def prompt(self, text):
        result = json.loads(await self.js(f"""(()=>{{const i=document.querySelector('#prompt-textarea');if(!i)return '{{}}';
const a=[...document.querySelectorAll('[data-message-author-role=\"assistant\"]')],last=a.at(-1)?.querySelector('.markdown,.prose')?.textContent?.trim()||'';
i.focus();document.execCommand('selectAll');document.execCommand('delete');document.execCommand('insertText',false,{json.dumps(text)});i.dispatchEvent(new Event('input',{{bubbles:true}}));
return JSON.stringify({{count:a.length,last}});}})()"""))
        await asyncio.sleep(.4)
        return int(result.get("count", 0)), result.get("last", "")

    async def send(self):
        ok = await self.js("(()=>{const b=document.querySelector('#composer-submit-button,button[data-testid=\"send-button\"]');if(!b||b.disabled)return false;b.click();return true;})()")
        if not ok:
            raise RuntimeError("Send button unavailable")

    async def wait_chat_url(self):
        for _ in range(120):
            url = await self.url()
            if conv_id(url):
                return url
            await asyncio.sleep(.25)
        raise RuntimeError("No conversation URL after send")

    async def rename(self, title):
        await self.sidebar()
        result = json.loads(await self.js(f"""(async()=>{{const sleep=x=>new Promise(r=>setTimeout(r,x)),n=x=>String(x||'').replace(/\\s+/g,' ').trim(),path=location.pathname;
const link=[...document.querySelectorAll('a[href*=\"/c/\"]')].find(x=>new URL(x.getAttribute('href'),location.origin).pathname===path);if(!link)return JSON.stringify({{ok:false,step:'link'}});
const row=link.closest('li,[data-testid^=\"history-item-\"],div');row?.dispatchEvent(new MouseEvent('mouseover',{{bubbles:true}}));await sleep(200);
const options=row?.querySelector('button[data-testid$=\"-options\"],button[aria-label^=\"Open conversation options\"]')||[...document.querySelectorAll('button[aria-label^=\"Open conversation options\"]')].find(x=>n(x.getAttribute('aria-label')).endsWith(n(link.textContent)));
if(!options)return JSON.stringify({{ok:false,step:'options'}});options.click();await sleep(200);
const rename=[...document.querySelectorAll('[role=\"menuitem\"],button')].find(x=>n(x.textContent).toLowerCase()==='rename');if(!rename)return JSON.stringify({{ok:false,step:'menu'}});rename.click();await sleep(200);
const input=document.querySelector('input[aria-label=\"Chat title\"]');if(!input)return JSON.stringify({{ok:false,step:'input'}});Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,{json.dumps(title)});input.dispatchEvent(new Event('input',{{bubbles:true}}));input.dispatchEvent(new KeyboardEvent('keydown',{{key:'Enter',code:'Enter',keyCode:13,bubbles:true}}));await sleep(500);
const ok=[...document.querySelectorAll('a[href*=\"/c/\"]')].some(x=>n(x.getAttribute('aria-label'))==={json.dumps(title)}||n(x.textContent)==={json.dumps(title)});return JSON.stringify({{ok,step:ok?'done':'verify'}});}})()""", 20))
        if not result.get("ok"):
            raise RuntimeError(f"Could not rename conversation ({result.get('step')})")

    async def response(self, timeout, before_count, before_text):
        end, last, stable = time.monotonic() + timeout, "", 0
        while time.monotonic() < end:
            await asyncio.sleep(.75)
            data = json.loads(await self.js(f"""(()=>{{const a=[...document.querySelectorAll('[data-message-author-role=\"assistant\"]')],gen=!!document.querySelector('[data-testid=\"stop-button\"],[class*=\"result-streaming\"]');let text='';
for(let i=a.length-1;i>=0;i--){{const t=a[i].querySelector('.markdown,.prose')?.textContent?.trim()||'';if(t&&!(i===a.length-1&&t==={json.dumps(before_text)})){{text=t;break;}}}}
return JSON.stringify({{count:a.length,gen,text:text.substring(0,200000)}});}})()"""))
            text = data.get("text", "")
            if data.get("count", 0) > before_count and len(text) > 3:
                stable = stable + 1 if text == last else 0
                last = text
                if (not data.get("gen") and stable >= 2) or stable >= 4:
                    return last
        if last:
            return last + "\n\n[WARNING: timed out]"
        raise RuntimeError("Timed out waiting for ChatGPT response")


async def run(prompt, session_id, summary, project, model, effort, timeout, state_path):
    tab = find_tab()
    if not tab:
        raise RuntimeError("No ChatGPT tab found; open chatgpt.com with CDP enabled")
    state = State(state_path)
    entry = state.get(session_id)
    title = chat_title(summary, session_id)
    ws_url = tab.get("webSocketDebuggerUrl") or f"ws://127.0.0.1:{CDP_PORT}/devtools/page/{tab['id']}"
    async with websockets.connect(ws_url, max_size=2**22) as ws:
        c = CDP(ws)
        await c.js("document.querySelector('[data-testid=\"stop-button\"]')?.click();true")
        reused = False
        saved_url = entry.get("conversation_url") or (f"{ORIGIN}/c/{entry['conversation_id']}" if entry.get("conversation_id") else None)
        if saved_url and entry.get("project_name") == project and entry.get("project_url"):
            await c.navigate(saved_url)
            reused = await c.valid_chat(title)
        project_url = entry.get("project_url")
        if not reused:
            project_url = await c.project(project, project_url)
            found = await c.find_title(title)
            if found:
                await c.navigate(found)
                reused = await c.valid_chat(title)
                if not reused:
                    raise RuntimeError(f'Found "{title}" but could not open it')
        await c.input_ready()
        await c.configure(model, effort)
        before_count, before_text = await c.prompt(prompt)
        await c.send()
        if not reused:
            conversation_url = await c.wait_chat_url()
            state.set(session_id, conversation_id=conv_id(conversation_url), conversation_url=conversation_url, conversation_title=title, project_name=project, project_url=project_url, model=model, effort=effort, rename_pending=True)
            await c.rename(title)
            state.set(session_id, rename_pending=False)
        else:
            conversation_url = await c.url()
            state.set(session_id, conversation_id=conv_id(conversation_url), conversation_url=conversation_url, conversation_title=title, project_name=project, project_url=project_url, model=model, effort=effort)
        return await c.response(timeout, before_count, before_text)


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="GPT-5.6 Sol/High in one MoA chat per Hermes session")
    p.add_argument("prompt", nargs="?")
    p.add_argument("--file", "-f")
    p.add_argument("--timeout", "-t", type=int, default=DEFAULT_TIMEOUT)
    p.add_argument("--session-id", default=os.getenv("HERMES_SESSION_ID"))
    p.add_argument("--summary")
    p.add_argument("--project", default=DEFAULT_PROJECT)
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--effort", default=DEFAULT_EFFORT)
    p.add_argument("--state-file", type=Path, default=DEFAULT_STATE)
    p.add_argument("--hermes-db", type=Path, default=DEFAULT_HERMES_DB)
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    try:
        prompt = Path(args.file).read_text() if args.file else args.prompt if args.prompt else sys.stdin.read() if not sys.stdin.isatty() else ""
    except OSError as exc:
        print(f"Error: {exc}", file=sys.stderr); return 2
    session_id = clean(args.session_id)
    if not prompt:
        print("Error: no prompt", file=sys.stderr); return 2
    if not session_id:
        print("Error: set HERMES_SESSION_ID or pass --session-id", file=sys.stderr); return 2
    if args.timeout <= 0:
        print("Error: --timeout must be positive", file=sys.stderr); return 2
    summary = hermes_summary(session_id, prompt, args.summary, args.hermes_db)
    print(f'Sending to {args.project}: "{chat_title(summary, session_id)}" ({args.model}, {args.effort})', file=sys.stderr)
    try:
        with lock_session(args.state_file, session_id):
            answer = asyncio.run(run(prompt, session_id, summary, args.project, args.model, args.effort, args.timeout, args.state_file))
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr); return 1
    print(answer)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
