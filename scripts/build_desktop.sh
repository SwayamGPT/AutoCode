#!/usr/bin/env bash
# Build the installable AutoCode desktop app for the current OS.
#   macOS  -> dist/AutoCode.app and dist/AutoCode.dmg
#   Linux  -> dist/AutoCode/ (run dist/AutoCode/AutoCode)
#   Windows (Git Bash) -> dist/AutoCode/AutoCode.exe
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-python3}"

npm --prefix ui ci
npm --prefix ui run build

"$PYTHON" -m pip install -r requirements.txt -r requirements-desktop.txt
"$PYTHON" -m PyInstaller --noconfirm --clean packaging/autocode.spec

if [[ "$(uname)" == "Darwin" ]]; then
  rm -f dist/AutoCode.dmg
  hdiutil create -volname AutoCode -srcfolder dist/AutoCode.app -ov -format UDZO dist/AutoCode.dmg
  echo "Built dist/AutoCode.app and dist/AutoCode.dmg"
else
  echo "Built dist/AutoCode/"
fi
