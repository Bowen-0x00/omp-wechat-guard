#!/usr/bin/env bash
set -e

echo "========================================================"
echo "正在安装 omp 企业微信守护扩展至用户目录 (~/.omp/agent/extensions)..."
echo "========================================================"

EXT_DIR="$HOME/.omp/agent/extensions"
mkdir -p "$EXT_DIR"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cp "$SCRIPT_DIR/omp-wechat-guard.ts" "$EXT_DIR/omp-wechat-guard.ts"

# 检查是否已有配置
CFG_PATH="$HOME/.omp/agent/wechat-guard.json"
if [ ! -f "$CFG_PATH" ]; then
    HOSTNAME_VAL=$(hostname)
    cat << EOF > "$CFG_PATH"
{
  "tag": "$HOSTNAME_VAL",
  "url": "http://your-server-domain.com/api/guard",
  "secret": "YOUR_CUSTOM_API_SECRET"
}
EOF
    echo "[配置] 已自动生成默认配置文件: $CFG_PATH (默认标签: $HOSTNAME_VAL)"
fi

echo "🎉 [成功] 扩展安装成功！"
echo "在 omp 中运行会话时将自动加载。在会话中可输入 /wechat test 测试发送连通性卡片。"
