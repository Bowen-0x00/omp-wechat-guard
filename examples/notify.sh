#!/usr/bin/env bash
#
# 通用微信推送命令行客户端 (基于 omp_wechat_guard 通用接口)
#
# 用法:
#   ./notify.sh "通知标题" "详细内容 (支持 Markdown)"
#   ./notify.sh "H100 训练完成" "Epoch 50 训练已结束，Loss: 0.123" "success" "Redhat"
#

TITLE="${1:-系统通用通知}"
CONTENT="${2:-没有附加内容}"
LEVEL="${3:-info}"
HOST_TAG="${4:-$(hostname)}"

API_URL="${OMP_GUARD_URL:-http://your-server-domain.com/api/guard/notify}"
API_SECRET="${OMP_GUARD_SECRET:-YOUR_CUSTOM_API_SECRET}"

curl -s -X POST "$API_URL" \
  -H "Content-Type: application/json" \
  -H "X-API-Secret: $API_SECRET" \
  -d "{
    \"title\": \"$TITLE\",
    \"content\": \"$CONTENT\",
    \"level\": \"$LEVEL\",
    \"host\": \"$HOST_TAG\"
  }" | grep -q '"code":0' && echo "✅ 微信通知推送成功" || echo "❌ 推送失败"
