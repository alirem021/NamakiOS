#!/bin/sh
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
case "$(uname -s)-$(uname -m)" in
  Linux-x86_64) host_key="linux-x64"; python_url="https://github.com/astral-sh/python-build-standalone/releases/download/20260924/cpython-3.13.15%2B20260924-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz"; python_sha="d0b640eed27fbdd6f5f2bd33444aee53df2c8863f8b2a96f4094717411e3de9c" ;;
  Linux-aarch64|Linux-arm64) host_key="linux-arm64"; python_url="https://github.com/astral-sh/python-build-standalone/releases/download/20260924/cpython-3.13.15%2B20260924-aarch64-unknown-linux-gnu-install_only_stripped.tar.gz"; python_sha="5ad58156cbec94e5643c13caa792e92df72a23f33d3a6425d4cbff5c4b7a040c" ;;
  Darwin-x86_64) host_key="macos-x64"; python_url="https://github.com/astral-sh/python-build-standalone/releases/download/20260924/cpython-3.13.15%2B20260924-x86_64-apple-darwin-install_only_stripped.tar.gz"; python_sha="327814efd865a0b6a99c149b12a261e9d0ad409183515c745d41bda2d07282e9" ;;
  Darwin-arm64) host_key="macos-arm64"; python_url="https://github.com/astral-sh/python-build-standalone/releases/download/20260924/cpython-3.13.15%2B20260924-aarch64-apple-darwin-install_only_stripped.tar.gz"; python_sha="064afb7c2fc0bbf511d886288adf98696af5105e36c138cdf2c199c0146fcf68" ;;
  *) echo "Automatic setup supports Linux x64/ARM64 and macOS Intel/Apple Silicon." >&2; exit 1 ;;
esac
if [ -n "${NAMAKI_RUNTIME_CACHE:-}" ]; then runtime_root="$NAMAKI_RUNTIME_CACHE"
elif [ "$(uname -s)" = Darwin ]; then runtime_root="$HOME/Library/Caches/NamakiOS/runtime"
else runtime_root="${XDG_CACHE_HOME:-$HOME/.cache}/NamakiOS/runtime"; fi
python_bin="$runtime_root/$host_key/python/bin/python3"
if [ ! -x "$python_bin" ] && command -v python3 >/dev/null 2>&1; then
  if python3 -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >/dev/null 2>&1; then python_bin=$(command -v python3); fi
fi
if [ ! -x "$python_bin" ]; then
  echo "Python was not found. Downloading a private Python runtime..."
  mkdir -p "$runtime_root/$host_key/python"
  archive="$runtime_root/$host_key/python.tar.gz"
  if [ ! -f "$archive" ]; then
    if command -v curl >/dev/null 2>&1; then curl -fL --retry 3 "$python_url" -o "$archive.partial"
    elif command -v wget >/dev/null 2>&1; then wget "$python_url" -O "$archive.partial"
    else echo "This host needs curl or wget for its first download." >&2; exit 1; fi
    if command -v sha256sum >/dev/null 2>&1; then actual=$(sha256sum "$archive.partial" | cut -d " " -f 1)
    elif command -v shasum >/dev/null 2>&1; then actual=$(shasum -a 256 "$archive.partial" | cut -d " " -f 1)
    else actual=$(openssl dgst -sha256 "$archive.partial" | sed "s/^.*= //"); fi
    [ "$actual" = "$python_sha" ] || { echo "Python download checksum mismatch." >&2; exit 1; }
    mv "$archive.partial" "$archive"
  fi
  tar -xzf "$archive" -C "$runtime_root/$host_key/python" --strip-components=1
fi
exec "$python_bin" tools/bootstrap.py "$@"
