#!/bin/sh
set -eu

WEB_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
CLI_ROOT="$WEB_ROOT/cli"
BACKEND_ROOT="$WEB_ROOT/build/macos"
BACKEND_PATH="$BACKEND_ROOT/rh-workflow-desk-server"

if [ "$(uname -s)" != "Darwin" ]; then
  echo "macOS 构建只能在 Darwin 上执行。" >&2
  exit 1
fi
if [ "$(uname -m)" != "arm64" ]; then
  echo "当前脚本生成 Apple Silicon (arm64) 安装包；当前架构为 $(uname -m)。" >&2
  exit 1
fi

mkdir -p "$BACKEND_ROOT"
rm -f "$BACKEND_PATH"

echo "[1/3] 构建内置 Python 本地服务…"
PYTHONPATH="$CLI_ROOT/src:$WEB_ROOT${PYTHONPATH:+:$PYTHONPATH}" \
  uv run --project "$WEB_ROOT" --with "pyinstaller>=6.0" pyinstaller \
    --noconfirm \
    --clean \
    --onefile \
    --name rh-workflow-desk-server \
    --distpath "$BACKEND_ROOT" \
    --workpath "$BACKEND_ROOT/work" \
    --specpath "$BACKEND_ROOT/spec" \
    --paths "$CLI_ROOT/src" \
    --paths "$WEB_ROOT" \
    --add-data "$WEB_ROOT/static:static" \
    "$WEB_ROOT/backend/backend_entry.py"

test -x "$BACKEND_PATH"
echo "[2/3] 构建 Electron macOS 安装包…"
(cd "$WEB_ROOT" && npx electron-builder --mac dmg zip --arm64 --publish never)

echo "[3/3] 生成 SHA-256 校验文件…"
if command -v shasum >/dev/null 2>&1; then
  (cd "$WEB_ROOT/dist" && shasum -a 256 RH-Workflow-Desk-*.dmg RH-Workflow-Desk-*.zip > SHA256SUMS.txt)
fi

echo "构建完成：$WEB_ROOT/dist"
