#!/usr/bin/env bash

# Store the shared web password as a local Docker/Compose secret. The value is
# never accepted as a command-line argument, echoed, or committed to Git.

set -Eeuo pipefail

SCRIPT_DIR="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(CDPATH='' cd -- "$SCRIPT_DIR/.." && pwd)"
SECRET_DIR="$REPO_ROOT/secrets"
SECRET_FILE="$SECRET_DIR/app_password"
TEMP_FILE=""
PASSWORD=""
CONFIRM=""
FORCE=false

cleanup() {
    [[ -z "$TEMP_FILE" || ! -e "$TEMP_FILE" ]] || rm -f -- "$TEMP_FILE"
    unset PASSWORD CONFIRM
}
trap cleanup EXIT HUP INT TERM

while (($# > 0)); do
    case "$1" in
        --force) FORCE=true ;;
        --help|-h)
            echo '用法：scripts/configure-app-password.sh [--force]' >&2
            exit 0
            ;;
        *)
            echo "未知参数：$1" >&2
            exit 2
            ;;
    esac
    shift
done

if [[ ! -t 0 || ! -t 2 ]]; then
    echo "请在交互式终端运行此脚本；不要通过命令行参数传递密码。" >&2
    exit 2
fi
if [[ -e "$SECRET_FILE" && "$FORCE" != true ]]; then
    printf 'secrets/app_password 已存在，覆盖它吗？[y/N] ' >&2
    IFS= read -r answer
    case "$answer" in y|Y|yes|YES) ;; *) echo '已取消。' >&2; exit 1 ;; esac
fi
if [[ -d "$SECRET_FILE" ]]; then
    echo "$SECRET_FILE 是目录，拒绝覆盖。" >&2
    exit 2
fi

umask 077
mkdir -p -- "$SECRET_DIR"
chmod 700 -- "$SECRET_DIR"
printf '服务访问密码（输入不回显，至少 8 位）： ' >&2
IFS= read -r -s PASSWORD
printf '\n' >&2
printf '再次输入密码： ' >&2
IFS= read -r -s CONFIRM
printf '\n' >&2
if [[ ${#PASSWORD} -lt 8 ]]; then
    echo '密码至少需要 8 个字符。' >&2
    exit 2
fi
if [[ "$PASSWORD" != "$CONFIRM" ]]; then
    echo '两次输入的密码不一致。' >&2
    exit 2
fi
if ((${#PASSWORD} > 512)); then
    echo '密码长度超过 512 个字符。' >&2
    exit 2
fi

TEMP_FILE="$(mktemp "$SECRET_DIR/.app_password.XXXXXX")"
chmod 600 -- "$TEMP_FILE"
printf '%s\n' "$PASSWORD" > "$TEMP_FILE"
mv -f -- "$TEMP_FILE" "$SECRET_FILE"
TEMP_FILE=""
if chgrp 10001 -- "$SECRET_FILE" 2>/dev/null; then
    chmod 640 -- "$SECRET_FILE"
    SECRET_MODE='640 (root + gid 10001)'
else
    chmod 600 -- "$SECRET_FILE"
    SECRET_MODE='600 (host owner only)'
fi
echo "已写入 $SECRET_FILE（权限 ${SECRET_MODE}）。" >&2
if [[ "$SECRET_MODE" == 600* ]]; then
    echo "提示：Docker 镜像使用 gid 10001；请执行 chown root:10001 $SECRET_FILE && chmod 640 $SECRET_FILE。" >&2
fi
echo '下一步：使用带认证配置的 Compose 启动服务。' >&2
