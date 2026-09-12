#!/usr/bin/env bash

# Read an Alibaba Cloud IQS API key without echoing it and atomically write a
# Docker/Compose secret. The key is never placed in shell arguments, logs,
# image layers or the repository.
set -Eeuo pipefail

SCRIPT_DIR="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(CDPATH='' cd -- "$SCRIPT_DIR/.." && pwd)"
SECRET_DIR="$REPO_ROOT/secrets"
SECRET_FILE="$SECRET_DIR/aliyun_iqs_api_key"
TEMP_FILE=""
TOKEN=""
FORCE=false

cleanup() {
    if [[ -n "$TEMP_FILE" && -e "$TEMP_FILE" ]]; then rm -f -- "$TEMP_FILE"; fi
    unset TOKEN
}
trap cleanup EXIT HUP INT TERM

while (($# > 0)); do
    case "$1" in
        --force) FORCE=true ;;
        --help|-h)
            echo "用法：scripts/configure-aliyun-iqs-key.sh [--force]" >&2
            exit 0
            ;;
        *) echo "未知参数：$1" >&2; exit 2 ;;
    esac
    shift
done

if [[ ! -t 0 || ! -t 2 ]]; then
    echo "请在交互式终端运行此脚本；不要通过命令行参数传递 key。" >&2
    exit 2
fi
if [[ -e "$SECRET_FILE" && "$FORCE" != true ]]; then
    printf 'aliyun_iqs_api_key 已存在，覆盖它吗？[y/N] ' >&2
    IFS= read -r answer
    case "$answer" in y|Y|yes|YES) ;; *) echo "已取消，未修改密钥文件。" >&2; exit 1 ;; esac
fi
if [[ -d "$SECRET_FILE" ]]; then echo "$SECRET_FILE 是目录，拒绝覆盖。" >&2; exit 2; fi

umask 077
mkdir -p -- "$SECRET_DIR"
chmod 700 -- "$SECRET_DIR"
printf '阿里云 IQS API key（输入不回显）： ' >&2
IFS= read -r -s TOKEN
printf '\n' >&2
if [[ -z "$TOKEN" ]]; then echo "key 不能为空。" >&2; exit 2; fi
if [[ "$TOKEN" =~ [[:space:]] ]]; then echo "key 不能包含空格或换行。" >&2; exit 2; fi
if ((${#TOKEN} > 512)); then echo "key 长度超过 512 个字符。" >&2; exit 2; fi

TEMP_FILE="$(mktemp "$SECRET_DIR/.aliyun_iqs_api_key.XXXXXX")"
chmod 600 -- "$TEMP_FILE"
printf '%s\n' "$TOKEN" > "$TEMP_FILE"
mv -f -- "$TEMP_FILE" "$SECRET_FILE"
TEMP_FILE=""
if chgrp 10001 -- "$SECRET_FILE" 2>/dev/null; then
    chmod 640 -- "$SECRET_FILE"
    SECRET_MODE="640 (root + gid 10001)"
else
    chmod 600 -- "$SECRET_FILE"
    SECRET_MODE="600 (host owner only; run chown root:10001 before Docker)"
fi
echo "已写入 $SECRET_FILE（权限 ${SECRET_MODE}）。" >&2
if [[ "$SECRET_MODE" == 600* ]]; then
    echo "提示：Docker 镜像使用 gid 10001；请执行 chown root:10001 $SECRET_FILE && chmod 640 $SECRET_FILE。" >&2
fi
echo "下一步：make deploy-live。" >&2
