#!/bin/bash
# 自动拉取 GitHub 最新代码并重启服务
# 作者: ZacharyJiang
# 日期: 2026-04-05

cd /root/.openclaw/workspace_coder/a_share_market_monitor || exit 1

echo "=== $(date) 开始检测更新 ==="

# 拉取最新代码
git fetch origin

# 检查是否有更新
LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse origin/main)

if [ "$LOCAL" = "$REMOTE" ]; then
    echo "当前已是最新版本，无需更新"
    echo "=== 检查完成 ==="
    exit 0
fi

echo "发现新版本，开始更新..."

# 拉取更新
git pull origin main

# 重新构建 Docker 镜像
echo "重新构建 Docker 镜像..."
docker build -t a-share-etf-monitor .

# 停止并删除旧容器
if [ "$(docker ps -aq -f name=a-share-etf-monitor)" ]; then
    echo "停止旧容器..."
    docker stop a-share-etf-monitor
    docker rm a-share-etf-monitor
fi

# 启动新容器
echo "启动新容器..."
docker run -d \
    --name a-share-etf-monitor \
    --restart unless-stopped \
    -p 127.0.0.1:8081:8080 \
    --env-file .env \
    -e USE_MOCK=false \
    a-share-etf-monitor

echo "更新完成，服务已重启"
echo "=== 更新完成 ==="

# 发送通知（可选，可以配置飞书通知）
if [ -n "$FEISHU_WEBHOOK" ]; then
    curl -X POST -H "Content-Type: application/json" \
        -d '{"msg_type":"text","content":{"text":"ETF监控已自动更新到最新版本"}}' \
        "$FEISHU_WEBHOOK"
fi
