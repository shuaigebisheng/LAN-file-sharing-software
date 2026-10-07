#!/usr/bin/env bash
cd "$(dirname "$0")"

NO_PAUSE=0
for a in "$@"; do
  [ "$a" = "--no-pause" ] && NO_PAUSE=1
done

echo "============================================"
echo "  LAN Transfer - Build"
echo "============================================"
echo

# ---- 1. python ----
PY=""
command -v python3 >/dev/null 2>&1 && PY=python3
[ -z "$PY" ] && command -v python >/dev/null 2>&1 && PY=python
if [ -z "$PY" ]; then
  echo "[X] Python not found"
  [ "$NO_PAUSE" = 0 ] && read -r _ || true
  exit 1
fi
echo "[1/4] Python: $($PY --version)"
echo

# ---- 2. files ----
echo "[2/4] Checking project files..."
for f in transfer.py qr.py web/index.html LanTransfer.spec; do
  if [ ! -e "$f" ]; then
    echo "[X] missing: $f"
    [ "$NO_PAUSE" = 0 ] && read -r _ || true
    exit 1
  fi
done
echo "      OK"
echo

# ---- 3. pyinstaller ----
echo "[3/4] Checking PyInstaller..."
if ! $PY -m PyInstaller --version >/dev/null 2>&1; then
  echo "      installing..."
  if ! $PY -m pip install --upgrade pyinstaller; then
    echo "[X] PyInstaller install failed"
    [ "$NO_PAUSE" = 0 ] && read -r _ || true
    exit 1
  fi
fi
$PY -m PyInstaller --version
echo

# ---- 4. clean + build ----
echo "[4/4] Building (first run may take 1-3 minutes)..."
echo
rm -rf build dist

if ! $PY -m PyInstaller --noconfirm --clean LanTransfer.spec; then
  echo "[X] build failed"
  [ "$NO_PAUSE" = 0 ] && read -r _ || true
  exit 1
fi

echo
echo "============================================"
echo "  Build OK"
echo "  Output: dist/LanTransfer"
echo "============================================"
echo
[ "$NO_PAUSE" = 0 ] && read -r _ || true
exit 0