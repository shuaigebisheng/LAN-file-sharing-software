#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

# ============================================================
#  参数:  --src      强制用源码运行
#         --exe      强制用编译产物
#         --rebuild  先重新编译再运行
# ============================================================

MODE=auto
for arg in "$@"; do
    case "$arg" in
        --src)     MODE=src ;;
        --exe)     MODE=exe ;;
        --rebuild) MODE=rebuild ;;
    esac
done

# ---------- 定位编译产物 ----------
EXE_PATH=""
if [ -x "dist/局域网互传" ]; then
    EXE_PATH="dist/局域网互传"
elif [ -x "./局域网互传" ]; then
    EXE_PATH="./局域网互传"
fi

# ---------- 定位 Python ----------
PY=""
for c in python3 python; do
    if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done

# ---------- 分支函数 ----------
use_exe() {
    echo "[启动] 编译版: $EXE_PATH"
    exec "$EXE_PATH"
}

use_src() {
    if [ -z "$PY" ]; then
        echo "[X] 未找到 Python，也没有编译产物。"
        echo "    请安装 Python，或先运行 ./build.sh 编译。"
        exit 1
    fi
    for f in transfer.py qr.py web/index.html; do
        [ -e "$f" ] || { echo "[X] 缺少: $f"; exit 1; }
    done

    echo "[启动] 源码模式"
    echo "[Python] $($PY --version)"
    echo
    if ! $PY -c "import PIL" >/dev/null 2>&1; then
        echo "[提示] 未安装 Pillow，图片缩略图将回退为原图。"
        echo "       安装命令:  $PY -m pip install Pillow pillow-heif"
        echo
    fi
    echo "  按 Ctrl+C 停止服务"
    echo
    exec $PY transfer.py
}

do_rebuild() {
    echo
    echo "[编译] 调用 build.sh ..."
    echo
    ./build.sh
    # 重新定位
    EXE_PATH=""
    if [ -x "dist/局域网互传" ]; then
        EXE_PATH="dist/局域网互传"
    elif [ -x "./局域网互传" ]; then
        EXE_PATH="./局域网互传"
    fi
    if [ -z "$EXE_PATH" ]; then
        echo "[X] 编译完成但未找到输出文件"
        exit 1
    fi
    use_exe
}

# ---------- 时间戳比较（返回 0=stale, 1=新鲜） ----------
is_stale() {
    local newest exe_ts
    # Linux: stat -c %Y ; macOS: stat -f %m
    newest=$(find transfer.py qr.py web -type f -exec stat -c %Y {} + 2>/dev/null \
             | sort -n | tail -1)
    if [ -z "$newest" ]; then
        newest=$(find transfer.py qr.py web -type f -exec stat -f %m {} + 2>/dev/null \
                 | sort -n | tail -1)
    fi

    exe_ts=$(stat -c %Y "$EXE_PATH" 2>/dev/null \
             || stat -f %m "$EXE_PATH" 2>/dev/null)

    [ -n "$newest" ] && [ -n "$exe_ts" ] && [ "$newest" -gt "$exe_ts" ]
}

# ---------- 显式模式 ----------
case "$MODE" in
    src)     use_src ;;
    rebuild) do_rebuild ;;
    exe)
        if [ -n "$EXE_PATH" ]; then use_exe
        else echo "[!] 未找到编译产物，改用源码运行"; use_src
        fi
        ;;
esac

# ---------- 自动模式 ----------
if [ -z "$EXE_PATH" ]; then use_src; fi
if [ -z "$PY" ];       then use_exe; fi

if is_stale; then
    echo
    echo "============================================"
    echo "  [!] 检测到源码比编译产物新"
    echo "============================================"
    echo "    1. 用源码运行        （推荐）"
    echo "    2. 使用旧编译版"
    echo "    3. 重新编译后再运行"
    echo
    read -r -p "  请选择 [1/2/3，回车默认 1]: " CH
    case "$CH" in
        2) use_exe ;;
        3) do_rebuild ;;
        *) use_src ;;
    esac
fi

use_exe