#!/usr/bin/env bash
# TapAnchor · 手机本地运行（不用插电脑）
#
#   ./run_local.sh doctor
#   ./run_local.sh dump
#   ./run_local.sh run flows/demo.yaml
#   TA_PORT=41297 ./run_local.sh doctor     # 首次或重启后指定端口
#
# 无线调试端口是随机的，手机重启后会变。成功连上一次就把端口记进 .ta_port，
# 之后直接跑，不用再输。
set -e
cd "$(dirname "$0")"

PORTFILE=".ta_port"
PORT="${TA_PORT:-}"

if [ -z "$PORT" ] && [ -f "$PORTFILE" ]; then
    PORT=$(cat "$PORTFILE" 2>/dev/null || true)
fi

pick_device() {
    adb devices 2>/dev/null \
        | awk 'NR>1 && $2=="device" && $1 ~ /^127\.0\.0\.1:/{print $1; exit}'
}

SERIAL=$(pick_device)

if [ -z "$SERIAL" ] && [ -n "$PORT" ]; then
    adb connect "127.0.0.1:$PORT" >/dev/null 2>&1 || true
    SERIAL=$(pick_device)
fi

if [ -z "$SERIAL" ]; then
    cat <<'EOF'
没有可用设备。

两件事：
  1. 设置 → 开发者选项 → 无线调试，打开开关（手机重启后会自动关闭）
  2. 打开那个页面，把显示的端口传给脚本：
       TA_PORT=<端口> ./run_local.sh doctor
     成功一次就会记到 .ta_port，之后不用再输。

注意：那个页面是安全窗口，程序读不到上面的数字，只能人看一眼。
EOF
    exit 1
fi

echo "${SERIAL##*:}" > "$PORTFILE"
echo "设备 $SERIAL"
exec python3 -m tapanchor --serial "$SERIAL" "$@"
