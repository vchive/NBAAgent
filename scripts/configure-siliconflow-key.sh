#!/usr/bin/env bash

# Store a SiliconFlow token as a local Docker/Compose secret without putting it
# in shell history, command-line arguments, the repository, or an image layer.
# The script deliberately has no option to print the token.

set -Eeuo pipefail

SCRIPT_DIR="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(CDPATH='' cd -- "$SCRIPT_DIR/.." && pwd)"
SECRET_DIR="$REPO_ROOT/secrets"
SECRET_FILE="$SECRET_DIR/siliconflow_api_key"
TEMP_FILE=""
TOKEN=""
FORCE=false

cleanup() {
    if [[ -n "$TEMP_FILE" && -e "$TEMP_FILE" ]]; then
        rm -f -- "$TEMP_FILE"
    fi
    # Do not retain the credential in this shell longer than necessary.
    unset TOKEN
}
trap cleanup EXIT HUP INT TERM

usage() {
    cat >&2 <<'EOF'
Usage: scripts/configure-siliconflow-key.sh [--force]

Read a SiliconFlow API key without echoing it and atomically write
secrets/siliconflow_api_key. Docker Compose mounts the file into the
nbaagent container (gid 10001), so the final mode is 0640 with that group
when the host permits changing the numeric group.

  --force   overwrite an existing secret without asking
EOF
}

while (($# > 0)); do
    case "$1" in
        --force)
            FORCE=true
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        *)
            echo "未知参数: $1" >&2
            usage
            exit 2
            ;;
    esac
    shift
done

if [[ ! -t 0 || ! -t 2 ]]; then
    echo "请在交互式终端运行此脚本；不要通过命令行参数传递 key。" >&2
    exit 2
fi

if [[ -e "$SECRET_FILE" && "$FORCE" != true ]]; then
    printf 'secrets/siliconflow_api_key 已存在，覆盖它吗？[y/N] ' >&2
    IFS= read -r answer
    case "$answer" in
        y|Y|yes|YES)
            ;;
        *)
            echo "已取消，未修改密钥文件。" >&2
            exit 1
            ;;
    esac
fi
if [[ -d "$SECRET_FILE" ]]; then
    echo "$SECRET_FILE 是目录，拒绝覆盖。" >&2
    exit 2
fi

umask 077
mkdir -p -- "$SECRET_DIR"
chmod 700 -- "$SECRET_DIR"

printf 'SiliconFlow API key（输入不回显）： ' >&2
IFS= read -r -s TOKEN
printf '\n' >&2

if [[ -z "$TOKEN" ]]; then
    echo "key 不能为空。" >&2
    exit 2
fi
if [[ "$TOKEN" =~ [[:space:]] ]]; then
    echo "key 只能是单个 token，不能包含空格或换行。" >&2
    exit 2
fi
if ((${#TOKEN} > 512)); then
    echo "key 长度超过 512 个字符。" >&2
    exit 2
fi

# Write to a same-directory temporary file, then rename. This prevents a
# partially written secret if the terminal/process is interrupted.
TEMP_FILE="$(mktemp "$SECRET_DIR/.siliconflow_api_key.XXXXXX")"
chmod 600 -- "$TEMP_FILE"
printf '%s\n' "$TOKEN" > "$TEMP_FILE"
mv -f -- "$TEMP_FILE" "$SECRET_FILE"
TEMP_FILE=""
# Compose file-based secrets are bind mounts. The image deliberately runs as
# uid/gid 10001, so a host-side 0600 root-owned file would be unreadable in the
# container. Keep the directory private and grant read access only to the
# fixed application group. A numeric chgrp works even when the host has no
# named group with this id; if the invoking user cannot change groups, retain
# 0600 and print an actionable warning instead of silently weakening the file.
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
echo "下一步：make docker-up-silicon，或参见 docs/byok.md。" >&2
