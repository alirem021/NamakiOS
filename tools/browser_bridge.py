"""Private Chromium renderer for the browser inside NamakiOS (Python + Pillow)."""
import argparse
import base64
import hashlib
import io
import itertools
import json
import os
from pathlib import Path
import socket
import shutil
import struct
import subprocess
import sys
import time
import threading
import urllib.request
import uuid

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
STATE = Path(os.environ.get('NAMAKI_STATE_DIR', str(ROOT / 'build')))
WIDTH, HEIGHT = 748, 478
PORT = 8765


def local_request(url, timeout=2):
    # CDP is loopback-only. Windows system proxies and VPN proxies must not
    # intercept this connection; web navigation still uses browser settings.
    return urllib.request.build_opener(urllib.request.ProxyHandler({})).open(url, timeout=timeout)


def read_exact(sock, count):
    data = bytearray()
    while len(data) < count:
        chunk = sock.recv(count - len(data))
        if not chunk:
            raise EOFError('Connection closed')
        data.extend(chunk)
    return bytes(data)


class DevTools:
    """Small, synchronous CDP client; browser events are consumed between replies."""
    def __init__(self, url):
        from urllib.parse import urlsplit
        address = urlsplit(url)
        self.sock = socket.create_connection((address.hostname, address.port), 10)
        self.sock.settimeout(20)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f'GET {address.path} HTTP/1.1\r\nHost: {address.netloc}\r\n'
                           f'Upgrade: websocket\r\nConnection: Upgrade\r\n'
                           f'Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n').encode())
        header = bytearray()
        while not header.endswith(b'\r\n\r\n'):
            header.extend(read_exact(self.sock, 1))
            if len(header) > 16384:
                raise RuntimeError('Invalid WebSocket handshake')
        accept = base64.b64encode(hashlib.sha1((key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest())
        if b' 101 ' not in header.split(b'\r\n')[0] or accept not in header:
            raise RuntimeError('Chromium WebSocket handshake failed')
        self.sequence = 0
        self.lock = threading.RLock()

    def send(self, payload, opcode=1):
        mask = os.urandom(4)
        size = len(payload)
        header = bytes([0x80 | opcode])
        if size < 126:
            header += bytes([0x80 | size])
        elif size < 65536:
            header += bytes([0xFE]) + struct.pack('!H', size)
        else:
            header += bytes([0xFF]) + struct.pack('!Q', size)
        self.sock.sendall(header + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

    def receive(self):
        chunks = bytearray()
        while True:
            first, second = read_exact(self.sock, 2)
            size = second & 127
            if size == 126:
                size = struct.unpack('!H', read_exact(self.sock, 2))[0]
            elif size == 127:
                size = struct.unpack('!Q', read_exact(self.sock, 8))[0]
            if size > 16 * 1024 * 1024:
                raise RuntimeError('Oversized Chromium message')
            mask = read_exact(self.sock, 4) if second & 128 else None
            data = read_exact(self.sock, size)
            if mask:
                data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
            opcode = first & 15
            if opcode == 8:
                raise EOFError('Chromium closed the connection')
            if opcode == 9:
                self.send(data, 10)
                continue
            if opcode == 10:
                continue
            chunks.extend(data)
            if first & 128:
                return json.loads(chunks)

    def call(self, method, **params):
        with self.lock:
            return self._call(method, **params)

    def _call(self, method, **params):
        self.sequence += 1
        sequence = self.sequence
        self.send(json.dumps({'id': sequence, 'method': method, 'params': params}).encode())
        while True:
            answer = self.receive()
            if answer.get('id') != sequence:
                continue
            if 'error' in answer:
                raise RuntimeError(answer['error'].get('message', str(answer['error'])))
            return answer.get('result', {})

    def close(self):
        self.sock.close()


def find_browser():
    override = os.environ.get('NAMAKI_BROWSER')
    if override:
        path = shutil.which(override) or (override if Path(override).is_file() else None)
        if not path:
            raise RuntimeError('NAMAKI_BROWSER does not name an installed browser.')
        return Path(path)
    candidates = [
        Path(os.environ.get('PROGRAMFILES(X86)', 'C:/Program Files (x86)')) / 'Microsoft/Edge/Application/msedge.exe',
        Path(os.environ.get('PROGRAMFILES', 'C:/Program Files')) / 'Google/Chrome/Application/chrome.exe',
        Path(os.environ.get('PROGRAMFILES', 'C:/Program Files')) / 'Microsoft/Edge/Application/msedge.exe',
        Path(os.environ.get('LOCALAPPDATA', 'C:/Users/Default/AppData/Local')) / 'Google/Chrome/Application/chrome.exe',
        Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'),
        Path('/Applications/Chromium.app/Contents/MacOS/Chromium'),
        Path('/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge'),
        Path.home() / 'Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    for name in ('chromium', 'chromium-browser', 'google-chrome', 'google-chrome-stable',
                 'microsoft-edge', 'microsoft-edge-stable', 'chrome', 'msedge'):
        executable = shutil.which(name)
        if executable:
            return Path(executable)
    raise RuntimeError('Install Chromium, Google Chrome or Microsoft Edge, or set NAMAKI_BROWSER.')


class BrowserStartupError(RuntimeError):
    pass


def launch_browser():
    browser = find_browser()
    try:
        return _launch_browser(browser, STATE / 'browser-profile')
    except BrowserStartupError as error:
        print(f'{error}\nRetrying Chromium with a fresh private profile and software rendering...', flush=True)
    result = _launch_browser(browser, STATE / ('browser-recovery-' + uuid.uuid4().hex), software=True)
    os.environ['NAMAKI_BROWSER_SOFTWARE'] = '1'
    return result


def _launch_browser(browser, profile, software=False):
    profile.mkdir(parents=True, exist_ok=True)
    active = profile / 'DevToolsActivePort'
    if active.exists():
        active.unlink()
    log = open(STATE / 'chromium.log', 'ab')
    log.write(f'\nNamakiOS renderer: {browser}; software={software}; profile={profile}\n'.encode('utf-8'))
    log.flush()
    process = subprocess.Popen([
        str(browser), '--headless', '--remote-debugging-port=0',
        '--remote-debugging-address=127.0.0.1', f'--user-data-dir={profile}',
        '--no-first-run', '--no-default-browser-check', '--disable-background-timer-throttling',
        f'--window-size={WIDTH},{HEIGHT}', *(['--disable-gpu'] if software else []), 'about:blank',
    ], stdout=log, stderr=log, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    try:
        deadline = time.monotonic() + 30
        exited_at = None
        last_error = 'DevToolsActivePort has not appeared'
        while time.monotonic() < deadline:
            if active.exists():
                dev = None
                try:
                    port = int(active.read_text().splitlines()[0])
                    with local_request(f'http://127.0.0.1:{port}/json/list') as reply:
                        targets = json.load(reply)
                    page = next((t for t in targets if t['type'] == 'page' and t.get('url') == 'about:blank'),
                                next(t for t in targets if t['type'] == 'page'))
                    dev = DevTools(page['webSocketDebuggerUrl'])
                    for target in targets:
                        if target['type'] == 'page' and target['id'] != page['id']:
                            dev.call('Target.closeTarget', targetId=target['id'])
                    dev.call('Page.enable')
                    dev.call('Emulation.setDeviceMetricsOverride', width=WIDTH, height=HEIGHT,
                             deviceScaleFactor=1, mobile=False)
                    return process, dev, log
                except (OSError, EOFError, StopIteration, ValueError, IndexError, RuntimeError) as error:
                    if dev is not None:
                        dev.close()
                    detail = f'{type(error).__name__}: {error}'
                    if detail != last_error:
                        log.write(f'NamakiOS DevTools connection: {detail}\n'.encode('utf-8'))
                        log.flush()
                    last_error = detail
            if process.poll() is not None:
                # Edge can hand off to a child before DevToolsActivePort is written.
                # A successful CDP connection, rather than the launcher PID, is decisive.
                if exited_at is None:
                    exited_at = time.monotonic()
                if time.monotonic() - exited_at >= 5:
                    raise BrowserStartupError(f'Chromium exited (code {process.returncode}); {last_error}. Log: {STATE / "chromium.log"}')
            time.sleep(.1)
        raise BrowserStartupError(f'Chromium startup timed out; {last_error}. Log: {STATE / "chromium.log"}')
    except BaseException:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        log.close()
        raise


def encode_frame(image, url, title):
    image = image.convert('RGB')
    if image.size != (WIDTH, HEIGHT):
        image = image.resize((WIDTH, HEIGHT))
    url = url.encode('ascii', 'replace')[:1023]
    title = title.encode('ascii', 'replace')[:127]
    result = bytearray(b'F' + struct.pack('<HHHH', WIDTH, HEIGHT, len(url), len(title)) + url + title)
    # Lossless run-length coding: 16-bit run followed by a 0xRRGGBB pixel.
    pixels = image.get_flattened_data() if hasattr(image, 'get_flattened_data') else image.getdata()
    for color, group in itertools.groupby(pixels):
        count = sum(1 for _ in group)
        pixel = color[0] << 16 | color[1] << 8 | color[2]
        while count:
            run = min(count, 65535)
            result.extend(struct.pack('<HI', run, pixel))
            count -= run
    return bytes(result)


def snapshot(dev):
    info = dev.call('Runtime.evaluate', expression='({url:location.href,title:document.title})', returnByValue=True)
    info = info.get('result', {}).get('value', {})
    shot = dev.call('Page.captureScreenshot', format='png', captureBeyondViewport=False)
    image = Image.open(io.BytesIO(base64.b64decode(shot['data'])))
    return encode_frame(image, info.get('url', ''), info.get('title', ''))


def navigate(dev, url):
    if url == 'about:home':
        url = (ROOT / 'assets/browser-home.html').as_uri()
    elif url == 'about:demo':
        url = (ROOT / 'assets/browser-demo.html').as_uri()
    elif not url.startswith(('https://', 'http://')):
        raise ValueError('Use an HTTP or HTTPS address.')
    reply = dev.call('Page.navigate', url=url)
    if reply.get('errorText'):
        raise RuntimeError(reply['errorText'])


def command(dev, line):
    op, _, argument = line.partition(' ')
    if op == 'P':
        return b'P NamakiOS-Chromium/1'
    if op == 'F':
        return snapshot(dev)
    if op == 'N':
        navigate(dev, argument)
    elif op in ('B', 'D'):
        history = dev.call('Page.getNavigationHistory')
        index = history['currentIndex'] + (-1 if op == 'B' else 1)
        if 0 <= index < len(history['entries']):
            dev.call('Page.navigateToHistoryEntry', entryId=history['entries'][index]['id'])
    elif op == 'R':
        dev.call('Page.reload')
    elif op == 'C':
        x, y = map(int, argument.split())
        if not (0 <= x < WIDTH and 0 <= y < HEIGHT):
            raise ValueError('Click outside viewport')
        for event in ('mousePressed', 'mouseReleased'):
            dev.call('Input.dispatchMouseEvent', type=event, x=x, y=y, button='left', clickCount=1)
    elif op == 'S':
        delta = max(-3000, min(3000, int(argument)))
        dev.call('Input.dispatchMouseEvent', type='mouseWheel', x=WIDTH // 2, y=HEIGHT // 2,
                 deltaX=0, deltaY=delta)
    elif op == 'K':
        key = int(argument)
        special = {8: ('Backspace', 8), 9: ('Tab', 9), 10: ('Enter', 13), 27: ('Escape', 27),
                   256: ('ArrowUp', 38), 257: ('ArrowDown', 40), 258: ('ArrowLeft', 37),
                   259: ('ArrowRight', 39), 260: ('PageUp', 33), 261: ('PageDown', 34),
                   262: ('Home', 36), 263: ('End', 35), 264: ('Delete', 46)}
        if key in special:
            name, code = special[key]
            for event in ('keyDown', 'keyUp'):
                dev.call('Input.dispatchKeyEvent', type=event, key=name, windowsVirtualKeyCode=code)
        elif 32 <= key < 127:
            dev.call('Input.dispatchKeyEvent', type='keyDown', key=chr(key), text=chr(key),
                     unmodifiedText=chr(key))
            dev.call('Input.dispatchKeyEvent', type='keyUp', key=chr(key))
    else:
        raise ValueError('Unknown browser command')
    return b'A'


def serve(dev, listener):
    while True:
        try:
            client, _ = listener.accept()
        except OSError:
            if listener.fileno() == -1:
                return
            raise
        print('NamakiOS browser connected', flush=True)
        try:
            with client:
                client.settimeout(30)
                client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                pending = bytearray()
                previous_frame = None
                while True:
                    part = client.recv(2048)
                    if not part:
                        break
                    pending.extend(part)
                    if len(pending) > 65536:
                        raise ValueError('Oversized command queue')
                    while b'\n' in pending:
                        raw, _, pending = pending.partition(b'\n')
                        if len(raw) > 1100:
                            raise ValueError('Oversized command')
                        try:
                            reply = command(dev, raw.decode('utf-8').strip())
                            if reply[:1] == b'F':
                                digest = hashlib.sha256(reply).digest()
                                if digest == previous_frame:
                                    reply = b'A'
                                previous_frame = digest
                        except Exception as error:
                            print(f'Browser: {error}', flush=True)
                            reply = b'E' + str(error).encode('ascii', 'replace')[:200]
                        client.sendall(struct.pack('<I', len(reply)) + reply)
        except (OSError, EOFError, ValueError) as error:
            print(f'Guest disconnected: {error}', flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=PORT)
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    ROOT.joinpath('build').mkdir(exist_ok=True)
    listener = socket.socket()
    # Bind only locally. QEMU user networking exposes host loopback as 10.0.2.2.
    listener.bind(('127.0.0.1', args.port))
    listener.listen(1)
    process = dev = log = None
    try:
        process, dev, log = launch_browser()
        if args.self_test:
            navigate(dev, 'about:demo')
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                result = dev.call('Runtime.evaluate', expression='document.querySelector("#counter")?.textContent',
                                  returnByValue=True)['result'].get('value')
                if result == '0':
                    break
                time.sleep(.1)
            assert result == '0', result
            command(dev, 'C 85 190')
            result = dev.call('Runtime.evaluate', expression='document.querySelector("#counter").textContent',
                              returnByValue=True)['result']['value']
            assert result == '1', result
            frame = snapshot(dev)
            assert frame[:1] == b'F'
            ROOT.joinpath('build/browser-self-test.json').write_text(json.dumps({'javascript': True,
                'mouse': True, 'frame_bytes': len(frame)}, indent=2))
            print('PASS: JavaScript, mouse input, CSS, screenshot transport', flush=True)
        else:
            navigate(dev, 'about:home')
            print(f'Chromium bridge ready on 127.0.0.1:{args.port}', flush=True)
            serve(dev, listener)
    finally:
        listener.close()
        if dev:
            try:
                dev.call('Browser.close')
            except Exception:
                pass
            dev.close()
        if process:
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.terminate()
        if log:
            log.close()


if __name__ == '__main__':
    main()
