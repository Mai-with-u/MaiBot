# MaiBot News Server

首页资讯服务。`content/` 目录下每个 `.md` 文件对应一条资讯，保存文件后自动生效，不需要重启服务。

## Markdown 文件格式

文件顶部可以写可选的 front matter，使用简单的 `key: value` 格式：

```markdown
---
title: 资讯标题
source: MaiBot 官方
date: 2026-01-02T09:30:00+08:00
pinned: false
url: https://example.com/article
summary: 可选；不写时从正文自动生成
---

这里是资讯正文，支持完整 Markdown。
```

字段说明：

- `title`：标题；缺失时取正文第一个 Markdown 标题，再缺失时取文件名。
- `source`：来源，可留空。
- `date` / `published_at`：发布时间；缺失时使用文件修改时间。
- `pinned`：`true`、`1`、`yes`、`on` 或 `是` 表示置顶。
- `url`：原文链接，可留空。
- `summary`：列表摘要，可留空；缺失时由正文压缩生成。

置顶资讯优先展示，同组内按发布时间倒序排列。服务会按文件修改时间和大小缓存解析结果，编辑或替换文件后下一次读取即可生效。

## API

线上与插件统计服务共用 10059 端口：

- `GET /news?limit=20&include_content=true`
- `GET /news/{id}`
- `GET /news/health`

主程序通过 `/api/webui/news` 代理给控制台，浏览器无需直接访问资讯服务器。

## 服务器部署

当前部署位置：`/root/news_server`。

服务代码由 `/root/plugin_stats_server/app.py` 挂载到插件统计 FastAPI 应用，服务单元仍为 `plugin-stats-server.service`。新增或修改资讯只需操作 `/root/news_server/content/`，不需要重启；修改 `news_app.py` 后才需要重启该 systemd 服务。
