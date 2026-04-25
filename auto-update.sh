#!/bin/bash
# 自动拉取 GitHub 最新代码并重启服务
# v2: 代码通过卷挂载，更新只需 git pull + docker restart，不再重建镜像

REPO_PATH="${REPO_PATH:-/app}"
LOG_FILE="${REPO_PATH}/logs/auto-update.log"
CONTAINER_NAME="${CONTAINER_NAME:-a-share-etf-monitor}"

mkdir -p "$(dirname "$LOG_FILE")"

BACKGROUND_MODE="${1:-}"
if [ "$BACKGROUND_MODE" != "--background" ]; then
    nohup "$0" --background > /dev/null 2>&1 &
    echo "更新任务已在后台启动，日志: $LOG_FILE"
    exit 0
fi

cd "$REPO_PATH" || { echo "[ERROR] 无法进入目录: $REPO_PATH" >> "$LOG_FILE"; exit 1; }
exec >> "$LOG_FILE" 2>&1

echo ""
echo "=== $(date '+%Y-%m-%d %H:%M:%S') 开始检测更新 ==="

git fetch origin || { echo "[ERROR] git fetch 失败"; exit 1; }

LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse origin/main)

if [ "$LOCAL" = "$REMOTE" ]; then
    echo "[INFO] 已是最新版本 ($LOCAL)"
    echo "=== 检查完成 ==="
    exit 0
fi

echo "[INFO] 发现新版本: $LOCAL -> $REMOTE"
git pull origin main || { echo "[ERROR] git pull 失败"; exit 1; }
echo "[INFO] 代码更新完成"

# 重启容器（代码通过卷挂载，restart 即可生效，无需重建镜像）
echo "[INFO] 重启容器 $CONTAINER_NAME ..."
if docker restart "$CONTAINER_NAME"; then
    echo "[SUCCESS] 容器重启成功，新代码已生效"
else
    echo "[WARN] docker restart 失败，尝试 docker stop + start ..."
    docker stop "$CONTAINER_NAME" && docker start "$CONTAINER_NAME" && \
        echo "[SUCCESS] 容器重启成功" || \
        echo "[ERROR] 容器重启失败，请手动检查"
fi

echo "=== 更新完成 $(date '+%Y-%m-%d %H:%M:%S') ==="

if [ -n "$FEISHU_WEBHOOK" ]; then
    curl -s -X POST -H "Content-Type: application/json" \
        -d '{"msg_type":"text","content":{"text":"ETF监控已自动更新到最新版本"}}' \
        "$FEISHU_WEBHOOK" || true
fi
