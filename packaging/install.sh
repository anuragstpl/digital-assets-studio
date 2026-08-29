#!/usr/bin/env bash
# Install Artalo Digi Suit from source on macOS or Linux.
#
#   bash packaging/install.sh
#
# Creates a virtual environment, installs everything, and adds a launcher.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"

find_python() {
  for v in python3.12 python3.11 python3.10; do
    command -v "$v" >/dev/null 2>&1 && { echo "$v"; return; }
  done
  if command -v python3 >/dev/null 2>&1 &&
     python3 -c 'import sys; sys.exit(0 if (3,10) <= sys.version_info < (3,13) else 1)'; then
    echo python3; return
  fi
  return 1
}

PY=$(find_python) || {
  echo "Python 3.10, 3.11 or 3.12 is required and none was found."
  echo "  macOS:  brew install python@3.12"
  echo "  Ubuntu: sudo apt install python3.12 python3.12-venv"
  exit 1
}
echo "==> Using $($PY --version)"

[ -d .venv ] || "$PY" -m venv .venv
VENV="$REPO/.venv/bin/python"

echo "==> Installing dependencies (this takes a couple of minutes)"
"$VENV" -m pip install --upgrade pip >/dev/null
"$VENV" -m pip install -r requirements.txt

echo "==> Checking the install"
"$VENV" run.py --selftest

if [[ "$OSTYPE" == darwin* ]]; then
  # a double-clickable launcher, since there is no .app in a source install
  LAUNCHER="$REPO/Artalo Digi Suit.command"
  cat > "$LAUNCHER" <<LAUNCH
#!/usr/bin/env bash
cd "$REPO"
exec "$VENV" run.py
LAUNCH
  chmod +x "$LAUNCHER"
  echo
  echo "Installed. Double-click 'Artalo Digi Suit.command', or run:"
else
  mkdir -p "$HOME/.local/bin"
  cat > "$HOME/.local/bin/digital-assets-studio" <<LAUNCH
#!/usr/bin/env bash
cd "$REPO"
exec "$VENV" run.py "\$@"
LAUNCH
  chmod +x "$HOME/.local/bin/digital-assets-studio"
  echo
  echo "Installed. Run 'digital-assets-studio' (if ~/.local/bin is on your PATH), or:"
fi
echo "    $VENV run.py"

command -v ffmpeg >/dev/null 2>&1 || {
  echo
  echo "ffmpeg was not found. Video and audio steps need it:"
  [[ "$OSTYPE" == darwin* ]] && echo "    brew install ffmpeg" || echo "    sudo apt install ffmpeg"
}
