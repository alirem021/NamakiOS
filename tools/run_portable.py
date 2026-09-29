"""Run the same NamakiOS image on Windows, macOS and desktop Unix hosts."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import socket
import struct
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parent.parent


def runtime_cache():
    if os.environ.get('NAMAKI_STATE_DIR'):
        return Path(os.environ['NAMAKI_STATE_DIR']).expanduser().resolve()
    project = hashlib.sha256(str(ROOT).encode()).hexdigest()[:12]
    if sys.platform == 'win32':
        base = Path(os.environ.get('LOCALAPPDATA', str(Path.home() / 'AppData/Local')))
    elif sys.platform == 'darwin':
        base = Path.home() / 'Library/Caches'
    else:
        base = Path(os.environ.get('XDG_CACHE_HOME', str(Path.home() / '.cache')))
    return base / 'NamakiOS' / project


def find_qemu():
    override = os.environ.get('NAMAKI_QEMU')
    if override:
        executable = shutil.which(override) or (override if Path(override).is_file() else None)
        if executable:
            return str(executable)
        raise RuntimeError('NAMAKI_QEMU does not name a QEMU executable.')
    for name in ('qemu-system-i386', 'qemu-system-x86_64'):
        executable = shutil.which(name)
        if executable:
            return executable
    candidates = [Path(os.environ.get('PROGRAMFILES', 'C:/Program Files')) / 'qemu/qemu-system-i386.exe',
                  Path('/opt/homebrew/bin/qemu-system-i386'), Path('/usr/local/bin/qemu-system-i386'),
                  Path('/opt/homebrew/bin/qemu-system-x86_64'), Path('/opt/local/bin/qemu-system-i386')]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    raise RuntimeError('Install QEMU system-x86 (i386 or x86_64), or set NAMAKI_QEMU.')


def capabilities(qemu, option):
    result = subprocess.run([qemu, '-no-user-config', option, 'help'], capture_output=True, text=True,
        timeout=10, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    return result.stdout + result.stderr


def display_options(available, host):
    if host == 'darwin' and 'cocoa' in available:
        return ['-display', 'cocoa,zoom-to-fit=on', '-full-screen']
    if 'gtk' in available:
        return ['-display', 'gtk,full-screen=on,zoom-to-fit=on']
    if 'sdl' in available:
        return ['-display', 'sdl', '-full-screen']
    raise RuntimeError('QEMU needs a graphical GTK, SDL or Cocoa display backend.')


def audio_options(available, host):
    order = ('sdl', 'dsound') if host == 'win32' else (('coreaudio', 'sdl') if host == 'darwin' else ('pa', 'sdl', 'alsa', 'oss'))
    driver = next((name for name in order if name in available.split()), 'none')
    # Capture is transported through COM1, independent of QEMU's audio driver.
    return ['-audiodev', f'{driver},id=snd,in.voices=0', '-device', 'AC97,audiodev=snd']


def working_audio_options(qemu, available, host):
    if host != 'win32':
        return audio_options(available, host)
    # Listing a driver does not mean its Windows devices can initialize.
    # Ask a paused, invisible VM to reach its event loop before using it.
    for driver in ('sdl', 'dsound'):
        if driver not in available.split():
            continue
        options = ['-audiodev', f'{driver},id=snd,in.voices=0', '-device', 'AC97,audiodev=snd']
        try:
            result = subprocess.run([qemu, '-no-user-config', '-nodefaults', '-m', '32',
                '-display', 'none', '-S', '-qmp', 'stdio'] + options,
                input='{"execute":"qmp_capabilities"}\n{"execute":"quit"}\n',
                capture_output=True, text=True, timeout=5,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if result.returncode == 0 and '"return"' in result.stdout:
                return options
        except (OSError, subprocess.TimeoutExpired):
            pass
    print('QEMU speaker drivers are unavailable; booting without speaker output. The microphone bridge remains enabled.', flush=True)
    return ['-audiodev', 'none,id=snd,in.voices=0', '-device', 'AC97,audiodev=snd']


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def existing_service():
    try:
        with socket.create_connection(('127.0.0.1', 8765), 1) as client:
            client.settimeout(1)
            client.sendall(b'P\n')
            def exact(n):
                data = bytearray()
                while len(data) < n:
                    chunk = client.recv(n - len(data))
                    if not chunk:
                        return b''
                    data.extend(chunk)
                return bytes(data)
            header = exact(4)
            if len(header) != 4:
                return False
            length = struct.unpack('<I', header)[0]
            return length < 100 and exact(length) == b'P NamakiOS-Chromium/1'
    except OSError:
        return False


def main():
    for output in (sys.stdout, sys.stderr):
        if hasattr(output, 'reconfigure'):
            output.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true', help='Check prerequisites without launching the OS')
    parser.add_argument('--test-speaker', action='store_true', help='Play a host speaker test tone')
    parser.add_argument('--test-microphone', action='store_true', help='Check microphone input for five seconds')
    parser.add_argument('--headless', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--qmp-port', type=int, default=0, help=argparse.SUPPRESS)
    parser.add_argument('--no-microphone', action='store_true', help='Run without recording host microphone')
    args = parser.parse_args()
    if args.test_microphone:
        from mic_bridge import test_input
        return test_input()
    if args.test_speaker:
        from speaker_bridge import test_output
        return test_output()
    from bundle_runtime import configure
    bundled = configure()
    state = runtime_cache()
    state.mkdir(parents=True, exist_ok=True)
    os.environ['NAMAKI_STATE_DIR'] = str(state)
    os.chdir(ROOT)
    import browser_bridge as bridge
    bridge.STATE = state
    from mic_bridge import Microphone
    qemu = find_qemu()
    browser = bridge.find_browser()
    image = ROOT / 'build/os-modern.img'
    if not image.is_file():
        raise RuntimeError('Missing build/os-modern.img. Extract the entire portable ZIP first.')
    from PIL import Image
    try:
        import sounddevice
        microphone_module = True
    except (ImportError, OSError):
        microphone_module = False
    managed_qemu = bool(bundled and bundled.get('managed_qemu', True))
    web_desktop = bool(managed_qemu and not args.headless)
    gui = display_options(capabilities(qemu, '-display'), sys.platform) if not args.headless and not web_desktop else ['-display', 'none']
    audio = working_audio_options(qemu, capabilities(qemu, '-audio'), sys.platform) if not args.headless and not managed_qemu else ['-audiodev', 'none,id=snd', '-device', 'AC97,audiodev=snd']
    speaker_path = None
    speaker_device = None
    if not args.headless and (managed_qemu or 'none,id=snd,in.voices=0' in audio):
        from speaker_bridge import output_settings
        try:
            speaker_device, speaker_rate, speaker_channels = output_settings()
            speaker_path = state / 'speaker.wav'
            audio = ['-audiodev', f'wav,id=snd,path=speaker.wav,in.voices=0,out.frequency={speaker_rate},out.channels={speaker_channels},out.format=s16', '-device', 'AC97,audiodev=snd']
        except Exception as error:
            print(f'Speaker initialization failed: {type(error).__name__}: {error}', flush=True)
    print(f'Host: {platform.system()} {platform.machine()}\nQEMU: {qemu}\nBrowser: {browser}\nRuntime cache: {state}', flush=True)
    if not microphone_module:
        print('Microphone package missing. Run setup for sounddevice/PortAudio; the OS can still boot.', flush=True)
    if args.check:
        print('Display:', 'Bundled Chromium fullscreen desktop' if web_desktop else gui, '\nAudio:', audio, '\nMicrophone module:', microphone_module)
        print('Prerequisite check passed.', flush=True)
        return 0
    listener = socket.socket()
    try:
        listener.bind(('127.0.0.1', 8765))
        listener.listen(4)
    except OSError as error:
        listener.close()
        if error.errno in (48, 98, 10048) or getattr(error, 'winerror', None) == 10048:
            known = existing_service()
            print('NamakiOS is already running; use its existing QEMU window.' if known else
                  'Port 8765 is occupied. Close the previous NamakiOS/QEMU session before trying again.', flush=True)
            return 0 if known else 1
        raise
    chromium = dev = chromium_log = guest = mic = guest_log = desktop = gateway = speaker = None
    stop = threading.Event()
    old_handlers = {}
    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            old_handlers[sig] = signal.signal(sig, lambda *_: stop.set())
        chromium, dev, chromium_log = bridge.launch_browser()
        print('Stage: renderer connected; opening home page.', flush=True)
        bridge.navigate(dev, 'about:home')
        print('Stage: home page ready; preparing QEMU.', flush=True)
        threading.Thread(target=bridge.serve, args=(dev, listener), daemon=True).start()
        mic_port = free_port()
        qmp_port = args.qmp_port or (free_port() if web_desktop else 0)
        networking = ['-netdev', 'user,id=net0']
        working_directory = ROOT
        if managed_qemu:
            from guest_network import Gateway
            gateway = Gateway()
            networking = ['-netdev', f'socket,id=net0,connect=127.0.0.1:{gateway.port}']
            # Relative QMP filenames also work when the user's path contains Persian.
            working_directory = state
            shutil.copyfile(image, state / 'os-modern.img')
            firmware = Path(os.environ['NAMAKI_QEMU_BIOS'])
            shutil.copytree(firmware, state / 'firmware', dirs_exist_ok=True)
        options = [qemu, '-no-user-config', '-m', '256', '-accel', 'tcg', '-drive', 'format=raw,file=build/os-modern.img',
            '-snapshot', '-device', 'rtl8139,netdev=net0',
            '-chardev', f'socket,id=mic,host=127.0.0.1,port={mic_port},server=on,wait=off',
            '-serial', 'chardev:mic', '-name', 'Namaki OS - Modern Browser'] + networking + gui + audio
        if managed_qemu:
            options[options.index('format=raw,file=build/os-modern.img')] = 'format=raw,file=os-modern.img'
            options += ['-L', 'firmware']
        if qmp_port:
            options += ['-qmp', f'tcp:127.0.0.1:{qmp_port},server=on,wait=off']
        if speaker_path:
            # WAV output paths are relative for QEMU's Windows Unicode support.
            if not managed_qemu:
                audio_index = next(i for i, option in enumerate(options) if option.startswith('wav,id=snd,path='))
                speaker_path = ROOT / 'build/speaker.wav'
                options[audio_index] = options[audio_index].replace('path=speaker.wav', 'path=build/speaker.wav')
            speaker_path.unlink(missing_ok=True)
        guest_log = open(state / 'qemu.log', 'ab')
        guest = subprocess.Popen(options, cwd=working_directory, stdout=guest_log, stderr=guest_log,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        print(f'Stage: QEMU launched (PID {guest.pid}); fullscreen desktop={web_desktop}.', flush=True)
        if speaker_path:
            from speaker_bridge import Speaker
            speaker = Speaker(speaker_path, speaker_device)
        if web_desktop:
            from desktop_display import Desktop, QMP
            desktop = Desktop(QMP(qmp_port, guest), state, browser)
            print('Stage: fullscreen desktop connected.', flush=True)
        if microphone_module and not args.no_microphone:
            mic = Microphone(mic_port, lambda message: print(message, flush=True))
            mic.start()
        print('NamakiOS started. Close QEMU to stop the browser and microphone.', flush=True)
        while guest.poll() is None and not stop.wait(.2):
            if desktop and desktop.process.poll() is not None:
                print(f'Desktop browser exited with code {desktop.process.returncode}. See {state / "desktop.log"}', flush=True)
                break
        if guest.poll() is not None and guest.returncode != 0:
            raise RuntimeError(f'QEMU exited with code {guest.returncode}. See {state / "qemu.log"}')
        return 0
    finally:
        if speaker:
            speaker.stop()
        if desktop:
            desktop.close()
        if mic:
            mic.stop()
        if guest and guest.poll() is None:
            guest.terminate()
            try:
                guest.wait(timeout=5)
            except subprocess.TimeoutExpired:
                guest.kill()
                guest.wait()
        listener.close()
        if gateway:
            gateway.close()
        if dev:
            try:
                dev.call('Browser.close')
            except Exception:
                pass
            dev.close()
        if chromium:
            try:
                chromium.wait(timeout=5)
            except subprocess.TimeoutExpired:
                chromium.terminate()
                chromium.wait(timeout=5)
        if chromium_log:
            chromium_log.close()
        if guest_log:
            guest_log.close()
        if speaker_path:
            speaker_path.unlink(missing_ok=True)
        for sig, handler in old_handlers.items():
            signal.signal(sig, handler)


if __name__ == '__main__':
    from diagnostics import run
    sys.exit(run(main, append=True))
