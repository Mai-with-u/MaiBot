#!/bin/sh
set -eu

# 配置迁移会改写原有的 Bot 配置，必须先确保宿主配置和迁移备份能跨容器重建保留。
host_dir=$(realpath -m /MaiMBot/host)
if ! awk -v target="$host_dir" '
    $5 != "/" && ($5 == target || index(target, $5 "/") == 1) && length($5) >= length(mount) {
        mount = $5
        for (i = 7; i <= NF && $i != "-"; i++) {}
        filesystem = $(i + 1)
    }
    END { exit !(mount != "" && filesystem != "tmpfs" && filesystem != "ramfs") }
' /proc/self/mountinfo; then
    cat >&2 <<'EOF'
[Docker] /MaiMBot/host 未持久化，已停止启动，旧配置尚未迁移。
如果已经用旧挂载运行过 1.4.x，请先从旧容器备份 /MaiMBot/host，并将数据恢复到新挂载的宿主机目录，再重建容器。
如果一直使用仓库默认 Compose，请下载与目标镜像版本对应的新版 docker-compose.yml，替换项目中的旧文件：
  https://github.com/Mai-with-u/MaiBot/releases
如果改过 Compose，请对照新版模板合并挂载，保留原有数据目录和端口。新版默认挂载如下：
  - ./data/MaiMBot-host:/MaiMBot/host
  - ./data/MaiMBot-bots:/MaiMBot/bots
  - ./data/MaiMBot-plugin-data:/MaiMBot/data/plugins
更新 Compose 后，如有旧数据备份，先确认已恢复，再运行 docker compose up -d core 重建容器。
EOF
    exit 1
fi

mkdir -p /MaiMBot/plugins

/MaiMBot/.venv/bin/python /MaiMBot/src/plugin_runtime/docker_layout_migration.py

exec /MaiMBot/.venv/bin/python bot.py "$@"
