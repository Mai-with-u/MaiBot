#!/bin/sh
# 此脚本用于覆盖core容器的默认启动命令，进行一些初始化
# 1
# 由于k8s与docker-compose的卷挂载方式有所不同，需要利用此脚本为一些文件和目录提前创建好软链接
# /MaiMBot/data是麦麦数据的实际挂载路径
# /MaiMBot/statistics是统计数据的实际挂载路径
# 2
# 此脚本等待辅助容器webui-cm-sync就绪后再启动麦麦
# 通过检测/MaiMBot/webui-cm-sync/ready文件来判断

set -e
echo "[K8s Init] Preparing volume..."

# 初次启动，在存储卷中检查并创建关键文件和目录
mkdir -p /MaiMBot/data/plugins
mkdir -p /MaiMBot/data/logs
if [ ! -d "/MaiMBot/statistics" ]
then
  echo "[K8s Init] Statistics volume is disabled."
else
  touch /MaiMBot/statistics/index.html
fi

# 删除默认插件目录，准备创建用户插件目录软链接
rm -rf /MaiMBot/plugins

# 创建软链接，从存储卷链接到实际位置
ln -s /MaiMBot/data/plugins /MaiMBot/plugins
ln -s /MaiMBot/data/logs /MaiMBot/logs
if [ -f "/MaiMBot/statistics/index.html" ]
then
  ln -s /MaiMBot/statistics/index.html /MaiMBot/maibot_statistics.html
fi

echo "[K8s Init] Volume ready."

# 如果启用了WebUI，则等待辅助容器webui-cm-sync就绪，然后创建中间层配置文件软链接
if [ "$MAIBOT_WEBUI_ENABLED" = "true" ]
then
  echo "[K8s Init] WebUI enabled. Waiting for container 'webui-cm-sync' ready..."
  while [ ! -f /MaiMBot/webui-cm-sync/ready ]; do
    sleep 1
  done
  echo "[K8s Init] Container 'webui-cm-sync' ready."
  mkdir -p /MaiMBot/config
  ln -s /MaiMBot/webui-cm-sync/model_config.toml /MaiMBot/config/model_config.toml
  ln -s /MaiMBot/webui-cm-sync/bot_config.toml /MaiMBot/config/bot_config.toml
  echo "[K8s Init] Config files middle layer for WebUI created."
else
  echo "[K8s Init] WebUI is disabled."
fi

# 启动麦麦
echo "[K8s Init] Waking up MaiBot..."
echo
exec python bot.py
