"""First-run setup: reuse installed dependencies, download only missing ones."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

from bundle_runtime import host_key
import runtime_download as download

ROOT = Path(__file__).resolve().parent.parent


def cache_root():
    if os.environ.get('NAMAKI_RUNTIME_CACHE'):
        return Path(os.environ['NAMAKI_RUNTIME_CACHE']).expanduser().resolve()
    if sys.platform == 'win32':
        base = Path(os.environ.get('LOCALAPPDATA', str(Path.home() / 'AppData/Local')))
    elif sys.platform == 'darwin':
        base = Path.home() / 'Library/Caches'
    else:
        base = Path(os.environ.get('XDG_CACHE_HOME', str(Path.home() / '.cache')))
    return base / 'NamakiOS/runtime'


class SetupLock:
    def __init__(self, path):
        self.path = path

    def __enter__(self):
        self.file = self.path.open('a+b')
        self.file.seek(0)
        if not self.file.read(1):
            self.file.write(b'0')
            self.file.flush()
        deadline = time.monotonic() + 1200
        announced = False
        while True:
            try:
                self.file.seek(0)
                if sys.platform == 'win32':
                    import msvcrt
                    msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except (OSError, BlockingIOError):
                if not announced:
                    print('Another NamakiOS setup is running. Waiting for it...', flush=True)
                    announced = True
                if time.monotonic() > deadline:
                    raise RuntimeError('Timed out waiting for the other setup process.')
                time.sleep(1)

    def __exit__(self, *_):
        self.file.seek(0)
        if sys.platform == 'win32':
            import msvcrt
            msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(self.file, fcntl.LOCK_UN)
        self.file.close()


def available_dependencies(site):
    environment = dict(os.environ)
    environment['PYTHONPATH'] = str(site)
    check = subprocess.run([sys.executable, '-c', 'import PIL, sounddevice, cffi, pycparser'],
        env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return check.returncode == 0


def install_dependencies(host, folder):
    site = folder / f'packages-{sys.version_info.major}.{sys.version_info.minor}'
    site.mkdir(parents=True, exist_ok=True)
    if not available_dependencies(site):
        print('Downloading missing Python/microphone libraries...', flush=True)
        probe = '''import importlib, json
missing = []
for package, module in [('Pillow','PIL'),('sounddevice','sounddevice'),('cffi','cffi'),('pycparser','pycparser')]:
    try:
        importlib.import_module(module)
    except Exception:
        missing.append(package)
print(json.dumps(missing))
'''
        environment = dict(os.environ, PYTHONPATH=str(site))
        missing = json.loads(subprocess.check_output([sys.executable, '-c', probe], env=environment, text=True))
        packages = {name: download.metadata(f'https://pypi.org/pypi/{name}/json', name + '-release.json') for name in missing}
        download.libraries(host, site, packages)
    sys.path.insert(0, str(site))
    os.environ['PYTHONPATH'] = str(site) + os.pathsep + os.environ.get('PYTHONPATH', '')
    return site


def edge_fallback(host, folder):
    # Official Edge packages are a fallback when Google's download is unavailable.
    products = download.metadata('https://edgeupdates.microsoft.com/api/products?view=enterprise', 'edge-products.json')
    stable = next(p for p in products if p['Product'] == 'Stable')
    target = 'Linux' if host.startswith('linux') else 'MacOS' if host.startswith('macos') else 'Windows'
    releases = [r for r in stable['Releases'] if r['Platform'] == target and r['Architecture'] in ('x64', 'universal')]
    if host == 'linux-arm64' or not releases:
        raise RuntimeError('Browser download failed. Check the connection to the official Chromium download service and run again.')
    release = max(releases, key=lambda r: tuple(map(int, r['ProductVersion'].split('.'))))
    kind = {'Linux': 'deb', 'MacOS': 'pkg', 'Windows': 'msi'}[target]
    asset = next(a for a in release['Artifacts'] if a['ArtifactName'] == kind)
    path, _ = download.fetch(asset['Location'], digest=asset['Hash'])
    if target == 'Linux':
        with download.deb_archive(path) as archive:
            members = [m for m in archive.getmembers() if m.name.startswith('./opt/microsoft/msedge/')]
            for member in members:
                if member.isdir() or not (member.isfile() or member.issym() or member.islnk()):
                    continue
                relative = Path(member.name).relative_to('opt/microsoft/msedge')
                destination = folder / 'browser' / relative
                if not destination.resolve().is_relative_to((folder / 'browser').resolve()):
                    raise RuntimeError('Unsafe Edge archive path')
                source = archive.extractfile(member)
                if source:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(source.read())
                    if member.mode & 0o111:
                        destination.chmod(0o755)
        return folder / 'browser/msedge'
    if target == 'MacOS':
        destination = folder / 'edge-expanded'
        if destination.exists():
            shutil.rmtree(destination)
        subprocess.run(['pkgutil', '--expand-full', str(path), str(destination)], check=True)
        app = next(destination.rglob('Microsoft Edge.app'))
        shutil.copytree(app, folder / 'browser/Microsoft Edge.app', dirs_exist_ok=True)
        return folder / 'browser/Microsoft Edge.app/Contents/MacOS/Microsoft Edge'
    # Extract an MSI without installing Edge or requiring administrator access.
    release = download.metadata('https://api.github.com/repos/activescott/lessmsi/releases/latest', 'lessmsi-release.json')
    asset = next(a for a in release['assets'] if a['name'].endswith('.zip'))
    extractor, _ = download.fetch(asset['browser_download_url'], digest=asset.get('digest'))
    download.unpack(extractor, folder / 'lessmsi')
    executable = next((folder / 'lessmsi').rglob('lessmsi.exe'))
    destination = folder / 'edge-expanded'
    subprocess.run([str(executable), 'x', str(path), str(destination) + os.sep], check=True)
    browser = next(destination.rglob('msedge.exe'))
    return browser


def install_browser(host, folder, source):
    print('No Chromium browser found. Downloading browser runtime...', flush=True)
    release = download.metadata('https://googlechromelabs.github.io/chrome-for-testing/last-known-good-versions-with-downloads.json', 'chrome-release.json')
    asset = next(a for a in release['channels']['Stable']['downloads']['chrome'] if a['platform'] == source['chrome_platform'])
    try:
        path, _ = download.fetch(asset['url'])
        download.unpack(path, folder / 'browser', strip=1)
        name = 'chrome.exe' if host.startswith('windows') else 'chrome' if host.startswith('linux') else 'Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing'
        browser = folder / 'browser' / name
    except RuntimeError:
        print('Chromium download unavailable. Trying official Microsoft Edge package...', flush=True)
        browser = edge_fallback(host, folder)
    if not browser.is_file():
        raise RuntimeError('Browser download is incomplete. Run NamakiOS again to retry.')
    return str(browser)


def prepare(folder, host, source):
    download.CACHE = folder / 'downloads'
    download.PAYLOAD = folder.parent
    if host.startswith('linux') and (folder / 'system-libs').is_dir():
        os.environ['LD_LIBRARY_PATH'] = str(folder / 'system-libs') + ':' + os.environ.get('LD_LIBRARY_PATH', '')
        os.environ['NAMAKI_PORTAUDIO'] = str(folder / 'system-libs/libportaudio.so.2')
    site = install_dependencies(host, folder)
    if host.startswith('linux'):
        import ctypes.util
        if not ctypes.util.find_library('portaudio'):
            linux_dependencies(folder, host, site)
    import run_portable
    import browser_bridge
    manifest_path = folder / 'runtime.json'
    settings = json.loads(manifest_path.read_text()) if manifest_path.is_file() else {}
    qemu = os.environ.get('NAMAKI_QEMU') or settings.get('qemu')
    managed = settings.get('managed_qemu', False)
    if os.environ.get('NAMAKI_QEMU'):
        managed = False
    if not qemu or not Path(qemu).is_file():
        try:
            qemu = run_portable.find_qemu()
            run_portable.display_options(run_portable.capabilities(qemu, '-display'), sys.platform)
            managed = False
        except RuntimeError:
            print('No QEMU found. Downloading portable QEMU...', flush=True)
            archive, _ = download.fetch(source['qemu']['url'], digest=source['qemu']['sha256'])
            download.unpack(archive, folder / 'qemu')
            qemu = str(folder / 'qemu/bin' / ('qemu-system-x86_64.exe' if host.startswith('windows') else 'qemu-system-x86_64'))
            managed = True
    browser = os.environ.get('NAMAKI_BROWSER') or settings.get('browser')
    if not browser or not Path(browser).is_file():
        try:
            browser = str(browser_bridge.find_browser())
        except RuntimeError:
            if host.startswith('linux'):
                linux_dependencies(folder, host, site)
            browser = install_browser(host, folder, source)
    settings = {'qemu': qemu, 'browser': browser, 'managed_qemu': managed,
        'bios': str(folder / 'qemu/share/qemu') if managed else None}
    temporary = manifest_path.with_suffix('.tmp')
    temporary.write_text(json.dumps(settings, indent=2), encoding='utf-8')
    temporary.replace(manifest_path)
    os.environ['NAMAKI_RUNTIME_DIR'] = str(folder)
    print('Runtime ready. Future starts reuse this cache.', flush=True)


def linux_dependencies(folder, host, site):
    # The GUI/audio runtime libraries live in the user's cache, not /usr/lib.
    if importlib.util.find_spec('zstandard') is None:
        package = download.metadata('https://pypi.org/pypi/zstandard/json', 'zstandard-release.json')
        download.libraries(host, site, {'zstandard': package})
    if not (folder / 'system-libs/sources.json').is_file():
        print('Downloading Linux browser/audio runtime libraries...', flush=True)
        download.linux_libraries(host)
    library = folder / 'system-libs'
    os.environ['LD_LIBRARY_PATH'] = str(library) + ':' + os.environ.get('LD_LIBRARY_PATH', '')
    os.environ['NAMAKI_PORTAUDIO'] = str(library / 'libportaudio.so.2')


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--setup-only', action='store_true')
    args, remaining = parser.parse_known_args()
    host = host_key()
    sources = json.loads((ROOT / 'tools/runtime-sources.json').read_text(encoding='utf-8'))
    if host not in sources:
        raise RuntimeError(f'Automatic downloads are not available for {host}. Supported: Windows x64, Linux x64/ARM64, macOS Intel/Apple Silicon.')
    folder = cache_root() / host
    folder.mkdir(parents=True, exist_ok=True)
    print(f'NamakiOS portable build: audio-mic-fix-4\nNamakiOS automatic setup ({host})\nRuntime cache: {folder}', flush=True)
    with SetupLock(folder / 'setup.lock'):
        prepare(folder, host, sources[host])
    if args.setup_only:
        return 0
    if sys.platform != 'win32':
        os.execvpe(sys.executable, [sys.executable, str(ROOT / 'tools/run_portable.py')] + remaining, dict(os.environ))
    # One Windows launcher process owns all children and its signal cleanup.
    sys.argv = [str(ROOT / 'tools/run_portable.py')] + remaining
    import run_portable
    return run_portable.main()


if __name__ == '__main__':
    from diagnostics import run
    sys.exit(run(main))
