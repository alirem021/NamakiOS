"""Local fullscreen desktop for QEMU builds that have no native GUI backend.

Chromium displays the guest framebuffer. Input travels through QMP; web pages
inside the guest still use the existing, independent Chromium browser bridge.
"""
import http.server
import io
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import threading
import time
import urllib.request

from PIL import Image

PAGE = r'''<!doctype html><html><head><meta charset="utf-8"><title>NamakiOS</title>
<style>html,body{margin:0;width:100%;height:100%;background:#000;overflow:hidden}
body{display:flex;align-items:center;justify-content:center}canvas{max-width:100vw;max-height:100vh;
width: min(100vw,133.333vh);height:auto;outline:none;cursor:default}</style></head>
<body><canvas id="screen" width="1024" height="768" tabindex="0"></canvas><script>
const screen=document.getElementById('screen'),ctx=screen.getContext('2d');screen.focus();
const base=location.pathname.replace(/\/$/,'');
let stopped=false;
async function frame(){while(!stopped){try{const r=await fetch(base+'/frame',{cache:'no-store'});
if(!r.ok)throw Error();const bitmap=await createImageBitmap(await r.blob());ctx.drawImage(bitmap,0,0);bitmap.close();
}catch(e){await new Promise(r=>setTimeout(r,300));}await new Promise(r=>setTimeout(r,80));}}frame();
let queue=[],sending=false;
async function send(event){queue.push(event);if(sending)return;sending=true;
while(queue.length){const batch=queue.splice(0,100);try{await fetch(base+'/input',{method:'POST',
headers:{'Content-Type':'application/json'},body:JSON.stringify(batch)});}catch(e){queue=[];}}
sending=false;}
function position(e){const r=screen.getBoundingClientRect();return {type:'move',
x:Math.max(0,Math.min(1023,Math.round((e.clientX-r.left)*1024/r.width))),
y:Math.max(0,Math.min(767,Math.round((e.clientY-r.top)*768/r.height)))};}
let lastMove=0;screen.addEventListener('pointermove',e=>{if(performance.now()-lastMove>20){send(position(e));lastMove=performance.now();}});
screen.addEventListener('pointerdown',e=>{e.preventDefault();screen.focus();screen.setPointerCapture(e.pointerId);
send(position(e));send({type:'button',button:e.button,down:true});});
screen.addEventListener('pointerup',e=>{e.preventDefault();send(position(e));send({type:'button',button:e.button,down:false});});
screen.addEventListener('contextmenu',e=>e.preventDefault());
screen.addEventListener('wheel',e=>{e.preventDefault();send({type:'wheel',up:e.deltaY<0});},{passive:false});
const keys={Enter:'ret',Escape:'esc',Space:'spc',Backspace:'backspace',Tab:'tab',Delete:'delete',
ArrowUp:'up',ArrowDown:'down',ArrowLeft:'left',ArrowRight:'right',Home:'home',End:'end',PageUp:'pgup',PageDown:'pgdn',
ShiftLeft:'shift',ShiftRight:'shift_r',ControlLeft:'ctrl',ControlRight:'ctrl_r',AltLeft:'alt',AltRight:'alt_r',
MetaLeft:'meta_l',MetaRight:'meta_r',CapsLock:'caps_lock',Minus:'minus',Equal:'equal',BracketLeft:'bracket_left',
BracketRight:'bracket_right',Backslash:'backslash',Semicolon:'semicolon',Quote:'apostrophe',Comma:'comma',
Period:'dot',Slash:'slash',Backquote:'grave_accent'};
let held=new Set();function key(e,down){if(e.code==='F11'){if(down){e.preventDefault();
if(!document.fullscreenElement)document.documentElement.requestFullscreen().catch(()=>{});else document.exitFullscreen();}return;}
const name=keys[e.code]||(e.code.startsWith('Key')?e.code.slice(3).toLowerCase():
e.code.startsWith('Digit')?e.code.slice(5):/^F[1-9][0-2]?$/.test(e.code)?e.code.toLowerCase():null);
if(!name)return;e.preventDefault();if(down&&held.has(name))return;
if(down)held.add(name);else held.delete(name);send({type:'key',key:name,down});}
addEventListener('keydown',e=>key(e,true));addEventListener('keyup',e=>key(e,false));
addEventListener('blur',()=>{for(const key of held)send({type:'key',key,down:false});held.clear();});
</script></body></html>'''


class QMP:
    def __init__(self, port, guest):
        deadline = time.monotonic() + 20
        while True:
            try:
                self.sock = socket.create_connection(('127.0.0.1', port), 1)
                break
            except OSError:
                if guest.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError('QEMU did not open its desktop control channel.')
                time.sleep(.1)
        self.sock.settimeout(10)
        self.stream = self.sock.makefile('rwb')
        self.lock = threading.Lock()
        self.stream.readline()
        self.call('qmp_capabilities')

    def call(self, command, **arguments):
        with self.lock:
            self.stream.write(json.dumps({'execute': command, 'arguments': arguments}).encode() + b'\n')
            self.stream.flush()
            while True:
                reply = self.stream.readline()
                if not reply:
                    raise EOFError('QEMU closed')
                reply = json.loads(reply)
                if 'error' in reply:
                    raise RuntimeError(reply['error'])
                if 'return' in reply:
                    return reply['return']

    def close(self):
        try:
            self.stream.close()
        except OSError:
            # The guest may already have closed QMP. Cleanup must still close
            # our socket and allow the launcher to stop its other children.
            pass
        finally:
            self.sock.close()


class Desktop:
    def __init__(self, qmp, state, browser):
        self.qmp, self.state = qmp, Path(state)
        self.frame_lock = threading.Lock()
        self.last_frame = b""
        self.frame_stop = threading.Event()
        self.frame_thread = threading.Thread(
            target=self._capture_loop,
            daemon=True,
        )
        self.frame_thread.start()
        token = '/' + secrets.token_urlsafe(24)
        desktop = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def reply(self, data, kind):
                self.send_response(200)
                self.send_header('Content-Type', kind)
                self.send_header('Content-Length', str(len(data)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'")
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                try:
                    if self.path == token:
                        self.reply(PAGE.encode(), 'text/html; charset=utf-8')
                    elif self.path == token + '/frame':
                        self.reply(desktop.frame(), 'image/png')
                    else:
                        self.send_error(404)
                except (OSError, EOFError, RuntimeError):
                    self.close_connection = True

            def do_POST(self):
                if self.path != token + '/input':
                    self.send_error(404)
                    return
                try:
                    size = int(self.headers.get('Content-Length', '0'))
                    if not 0 < size <= 32768:
                        raise ValueError('Invalid input size')
                    desktop.input(json.loads(self.rfile.read(size)))
                    self.reply(b'{}', 'application/json')
                except (ValueError, KeyError, TypeError, RuntimeError, OSError, EOFError):
                    self.send_error(400)

        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.url = f'http://127.0.0.1:{self.server.server_port}{token}'
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.log = open(self.state / 'desktop.log', 'ab')
        profile = self.state / 'desktop-profile'
        profile.mkdir(parents=True, exist_ok=True)
        active = profile / 'DevToolsActivePort'
        if active.exists():
            active.unlink()
        self.process = subprocess.Popen([str(browser), f'--app={self.url}', '--start-fullscreen',
            *(['--disable-gpu'] if os.environ.get('NAMAKI_BROWSER_SOFTWARE') == '1' else []),
            '--remote-debugging-port=0', '--remote-debugging-address=127.0.0.1',
            '--no-first-run', '--no-default-browser-check',
            f'--user-data-dir={self.state / "desktop-profile"}'], stdout=self.log, stderr=self.log,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        # Edge can create a welcome/sync tab even with --no-first-run. This is
        # our private profile; keep only the guest desktop's own page.
        from browser_bridge import DevTools, local_request
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                port = int(active.read_text().splitlines()[0])
                with local_request(f'http://127.0.0.1:{port}/json/list') as response:
                    targets = json.load(response)
                page = next(t for t in targets if t['type'] == 'page' and t.get('url') == self.url)
                dev = DevTools(page['webSocketDebuggerUrl'])
                try:
                    for target in targets:
                        if target['type'] == 'page' and target['id'] != page['id']:
                            dev.call('Target.closeTarget', targetId=target['id'])
                finally:
                    dev.close()
                break
            except (OSError, StopIteration, ValueError):
                time.sleep(.1)

    def _capture_loop(self):
        while not self.frame_stop.is_set():
            started = time.monotonic()

            try:
                self.qmp.call("screendump", filename="desktop.ppm")

                with Image.open(self.state / "desktop.ppm") as image:
                    output = io.BytesIO()
                    image.save(output, format="PNG", compress_level=1)

                with self.frame_lock:
                    self.last_frame = output.getvalue()

            except (OSError, EOFError, RuntimeError):
                break

            elapsed = time.monotonic() - started
            self.frame_stop.wait(max(0, (1 / 45) - elapsed))


    def frame(self):
        with self.frame_lock:
            return self.last_frame

    def input(self, batch):
        if not isinstance(batch, list) or len(batch) > 100:
            raise ValueError('Invalid batch')
        events = []
        for item in batch:
            kind = item['type']
            if kind == 'move':
                for axis, limit in (('x', 1023), ('y', 767)):
                    value = max(0, min(limit, int(item[axis]))) * 32767 // limit
                    events.append({'type': 'abs', 'data': {'axis': axis, 'value': value}})
            elif kind == 'button':
                events.append({'type': 'btn', 'data': {'button': {0: 'left', 1: 'middle', 2: 'right'}[item['button']], 'down': bool(item['down'])}})
            elif kind == 'wheel':
                button = 'wheel-up' if item['up'] else 'wheel-down'
                events.extend([{'type': 'btn', 'data': {'button': button, 'down': down}} for down in (True, False)])
            elif kind == 'key':
                events.append({'type': 'key', 'data': {'key': {'type': 'qcode', 'data': item['key']}, 'down': bool(item['down'])}})
            else:
                raise ValueError('Unknown input')
        if events:
            self.qmp.call('input-send-event', events=events)

    def close(self):
        self.frame_stop.set()
        self.frame_thread.join(timeout=2)
        self.server.shutdown()
        self.server.server_close()
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self.log.close()
        self.qmp.close()
