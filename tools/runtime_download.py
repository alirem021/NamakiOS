"""Download verified runtime components into the destination user's cache."""
import hashlib
import io
import json
import lzma
import os
import re
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tarfile
import threading
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / 'build/runtime-downloads'
PAYLOAD = ROOT / 'build/runtime-cache'
LOCK = threading.Lock()


def fetch(url, name=None, digest=None):
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / (name or url.rsplit('/', 1)[-1])
    if not path.is_file():
        temporary = path.with_name(path.name + '.partial')
        curl = shutil.which('curl.exe') or shutil.which('curl')
        if not curl:
            with urllib.request.urlopen(url, timeout=60) as response, temporary.open('wb') as output:
                shutil.copyfileobj(response, output)
            result = None
        else:
            result = subprocess.run([curl,
            '-4', '-L', '--fail', '--silent', '--show-error', '--retry', '2',
            '--speed-time', '60', '--speed-limit', '1024',
            '--connect-timeout', '30', '--max-time', '1200', url, '-o', str(temporary)])
        if result and result.returncode:
            raise RuntimeError(f'Download failed: {url}')
        temporary.replace(path)
        with LOCK:
            print(f'Downloaded {path.name}: {path.stat().st_size / 1024**2:.1f} MiB', flush=True)
    hasher = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            hasher.update(block)
    actual = hasher.hexdigest()
    if digest and actual.lower() != digest.removeprefix('sha256:').lower():
        if path.resolve().is_relative_to(CACHE.resolve()):
            path.unlink()
        raise RuntimeError(f'Checksum mismatch: {path.name}')
    return path, actual


def metadata(url, name):
    return json.loads(fetch(url, name)[0].read_text(encoding='utf-8'))


def unpack(path, folder, strip=0):
    folder.mkdir(parents=True, exist_ok=True)
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                parts = Path(info.filename).parts[strip:]
                if not parts or info.is_dir():
                    continue
                destination = folder.joinpath(*parts)
                if not destination.resolve().is_relative_to(folder.resolve()):
                    raise RuntimeError('Unsafe archive path')
                destination.parent.mkdir(parents=True, exist_ok=True)
                mode = info.external_attr >> 16
                if stat.S_ISLNK(mode):
                    target = archive.read(info).decode('utf-8')
                    if not (destination.parent / target).resolve().is_relative_to(folder.resolve()):
                        raise RuntimeError('Unsafe archive symlink')
                    if destination.is_symlink():
                        destination.unlink()
                    os.symlink(target, destination)
                    continue
                destination.write_bytes(archive.read(info))
                if mode & 0o111:
                    destination.chmod(0o755)
    else:
        with tarfile.open(path) as archive:
            members = archive.getmembers()
            # Resolve links to regular files, so Windows-built ZIPs need no symlinks.
            for member in members:
                parts = Path(member.name).parts[strip:]
                if not parts or member.isdir():
                    continue
                destination = folder.joinpath(*parts)
                if not destination.resolve().is_relative_to(folder.resolve()):
                    raise RuntimeError('Unsafe archive path')
                if not (member.isfile() or member.issym() or member.islnk()):
                    continue
                if member.issym() and os.name != 'nt':
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    if not (destination.parent / member.linkname).resolve().is_relative_to(folder.resolve()):
                        raise RuntimeError('Unsafe archive symlink')
                    if destination.is_symlink():
                        destination.unlink()
                    os.symlink(member.linkname, destination)
                    continue
                source = archive.extractfile(member)
                if source:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(source.read())
                    if member.mode & 0o111:
                        destination.chmod(0o755)


def wheel_matches(name, host):
    if not name.endswith('.whl'):
        return False
    if name.endswith('py3-none-any.whl') or name.endswith('py2.py3-none-any.whl'):
        return True
    python_tag, abi_tag, _ = name.rsplit('-', 3)[1:]
    compatible_abi = abi_tag == 'abi3' and python_tag.startswith('cp3') and int(python_tag[3:]) <= sys.version_info.minor
    if f'-cp{sys.version_info.major}{sys.version_info.minor}-' not in name and '-py3-' not in name and not compatible_abi:
        return False
    if host == 'windows-x64':
        return name.endswith('win_amd64.whl')
    if host.startswith('macos'):
        arch = 'arm64' if host.endswith('arm64') else 'x86_64'
        return 'macosx' in name and (name.endswith(arch + '.whl') or name.endswith('universal2.whl'))
    arch = 'aarch64' if host.endswith('arm64') else 'x86_64'
    return 'manylinux' in name and name.endswith(arch + '.whl')


def libraries(host, site, packages):
    for package, data in packages.items():
        wheels = [entry for entry in data['urls'] if wheel_matches(entry['filename'], host)]
        if not wheels:
            raise RuntimeError(f'No compatible {package} wheel for {host}')
        # Native sounddevice wheels include PortAudio on Windows and macOS.
        wheels.sort(key=lambda e: ('none-any' in e['filename'], 'universal2' not in e['filename'], e['filename']))
        wheel = wheels[0]
        path, _ = fetch(wheel['url'], digest=wheel['digests']['sha256'])
        unpack(path, site)
        if package == 'sounddevice' and host.startswith('linux'):
            source = site / 'sounddevice.py'
            content = source.read_text(encoding='utf-8')
            content = content.replace("_libname = _find_library('portaudio')",
                "_libname = __import__('os').environ.get('NAMAKI_PORTAUDIO') or _find_library('portaudio')")
            source.write_text(content, encoding='utf-8')


def deb_archive(path):
    data = ar_data(path)
    if data[:4] == b'\x28\xb5\x2f\xfd':
        import zstandard
        data = zstandard.ZstdDecompressor().stream_reader(io.BytesIO(data)).read()
    return tarfile.open(fileobj=io.BytesIO(data))


def ar_data(path):
    data = path.read_bytes()
    if data[:8] != b'!<arch>\n':
        raise RuntimeError('Invalid Debian archive')
    offset = 8
    while offset + 60 <= len(data):
        header = data[offset:offset + 60]
        name = header[:16].decode().strip().rstrip('/')
        size = int(header[48:58])
        body = data[offset + 60:offset + 60 + size]
        if name.startswith('data.tar'):
            return body
        offset += 60 + size + size % 2
    raise RuntimeError('No Debian data archive')


def linux_libraries(host):
    arch = 'arm64' if host.endswith('arm64') else 'amd64'
    mirror = 'https://ports.ubuntu.com/ubuntu-ports' if arch == 'arm64' else 'https://archive.ubuntu.com/ubuntu'
    index = fetch(f'{mirror}/dists/noble/main/binary-{arch}/Packages.xz', f'ubuntu-noble-main-{arch}.xz')[0]
    records = {}
    for block in lzma.decompress(index.read_bytes()).decode().split('\n\n'):
        record = {}
        for line in block.splitlines():
            if ': ' in line and not line.startswith(' '):
                key, value = line.split(': ', 1)
                record[key] = value
        if 'Package' in record:
            records[record['Package']] = record
    # Chrome runtime closure plus PortAudio. libc/loader remain OS-owned.
    wanted = ['libportaudio2', 'libasound2t64', 'libjack-jackd2-0', 'libnss3', 'libnspr4',
        'libatk1.0-0t64', 'libatk-bridge2.0-0t64', 'libcups2t64', 'libdrm2', 'libxkbcommon0',
        'libxcomposite1', 'libxdamage1', 'libxfixes3', 'libxrandr2', 'libgbm1', 'libpango-1.0-0',
        'libcairo2', 'libatspi2.0-0t64', 'libglib2.0-0t64', 'libdbus-1-3', 'libx11-6', 'libxcb1',
        'libxext6', 'libxi6', 'libxrender1', 'libxcursor1', 'libxss1', 'libxtst6', 'libfontconfig1',
        'libfreetype6', 'libharfbuzz0b', 'libpixman-1-0', 'libpng16-16t64', 'libexpat1',
        'libxau6', 'libxdmcp6', 'libbsd0', 'libmd0', 'libffi8', 'libpcre2-8-0', 'zlib1g',
        'libselinux1', 'libmount1', 'libblkid1', 'libuuid1', 'libsystemd0', 'liblzma5',
        'liblz4-1', 'libzstd1', 'libgcrypt20', 'libgpg-error0', 'libcap2', 'libbz2-1.0',
        'libbrotli1', 'libgraphite2-3', 'libdatrie1', 'libthai0', 'libfribidi0', 'libwayland-server0',
        'libstdc++6', 'libgcc-s1', 'libgnutls30t64', 'libnettle8t64', 'libhogweed6t64',
        'libgmp10', 'libidn2-0', 'libunistring5', 'libtasn1-6', 'libp11-kit0']
    folder = PAYLOAD / host / 'system-libs'
    folder.mkdir(parents=True, exist_ok=True)
    notices = folder / 'licenses'
    notices.mkdir(exist_ok=True)
    downloaded = []
    for name in wanted:
        record = records.get(name)
        if not record:
            universe = fetch(f'{mirror}/dists/noble/universe/binary-{arch}/Packages.xz', f'ubuntu-noble-universe-{arch}.xz')[0]
            for block in lzma.decompress(universe.read_bytes()).decode().split('\n\n'):
                entry = {}
                for line in block.splitlines():
                    if ': ' in line and not line.startswith(' '):
                        key, value = line.split(': ', 1)
                        entry[key] = value
                if 'Package' in entry:
                    records[entry['Package']] = entry
            record = records.get(name)
            if not record:
                raise RuntimeError(f'Missing Ubuntu dependency {name} ({arch})')
        # Include each library's runtime closure (NSS/SQLite, CUPS/Kerberos,
        # Cairo/XCB, etc.) without installing packages into the host OS.
        for group in record.get('Depends', '').split(','):
            candidates = [re.split(r'[\s(:]', alternative.strip())[0] for alternative in group.split('|')]
            dependency = next((candidate for candidate in candidates if candidate in records), None)
            if dependency and dependency.startswith('lib') and dependency not in ('libc6', 'libc-bin') and dependency not in wanted:
                wanted.append(dependency)
        path, digest = fetch(mirror + '/' + record['Filename'], digest=record['SHA256'])
        with deb_archive(path) as archive:
            for member in archive.getmembers():
                basename = Path(member.name).name
                if '.so' in basename and (member.isfile() or member.issym() or member.islnk()):
                    source = archive.extractfile(member)
                    if source:
                        (folder / basename).write_bytes(source.read())
                elif basename == 'copyright' and member.isfile():
                    (notices / (name + '.txt')).write_bytes(archive.extractfile(member).read())
        downloaded.append({'package': name, 'version': record['Version'], 'sha256': digest,
            'url': mirror + '/' + record['Filename']})
    (folder / 'sources.json').write_text(json.dumps(downloaded, indent=2))

