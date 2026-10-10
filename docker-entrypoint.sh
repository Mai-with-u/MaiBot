#!/bin/sh
set -eu

mkdir -p /MaiMBot/plugins

/MaiMBot/.venv/bin/python /MaiMBot/src/plugin_runtime/docker_layout_migration.py

exec /MaiMBot/.venv/bin/python bot.py "$@"
