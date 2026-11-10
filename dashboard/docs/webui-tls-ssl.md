# MaiBot WebUI TLS/SSL 配置指南

本文档基于当前仓库实现整理，目标是让 WebUI 通过 HTTPS 提供访问能力，并保持登录、Cookie、WebSocket 和 Let's Encrypt 续期正常工作。

## 1. 先说结论

MaiBot 当前最合适的 TLS/SSL 方案是让反向代理终止 HTTPS，然后把请求转发到 WebUI 的 HTTP 服务。

推荐顺序如下：

1. Caddy 反向代理 + Let's Encrypt 自动签发与续期
2. 宝塔面板反向代理 + Let's Encrypt
3. 1Panel 反向代理 + Let's Encrypt
4. 不建议直接让 WebUI 自己监听 HTTPS，当前仓库没有现成的 WebUI 原生 TLS 配置入口

## 2. 当前项目的部署特征

当前仓库里，WebUI 的前后端是同源部署思路：

1. 后端是独立的 FastAPI WebUI 服务，直接部署默认监听 127.0.0.1:8001；Docker Compose 会通过 HTTP_HOST=0.0.0.0 让容器内 WebUI 可被宿主机端口映射访问
2. 前端构建产物由这个 FastAPI 服务直接托管
3. 浏览器生产模式下默认按同源访问 API
4. 页面如果通过 HTTPS 打开，前端会自动把 WebSocket 协议切到 WSS

这意味着最稳妥的方式是：

1. MaiBot WebUI 继续在本机或容器内网跑 HTTP
2. 让 Caddy、宝塔 Nginx 或 1Panel OpenResty 对外暴露 443
3. 由代理把所有请求和 WebSocket 都转发到 WebUI

## 3. 配置前的准备工作

正式启用 HTTPS 之前，先确认下面几项：

1. 已准备一个已经解析到服务器公网 IP 的域名，例如 maibot.example.com
2. 80 和 443 端口可以从公网访问
3. 服务器没有其他程序占用 80 和 443
4. WebUI 可以在本机正常打开，例如 http://127.0.0.1:8001

如果采用 Docker Compose 部署，还要确认：

1. 容器已经能正常启动
2. 根目录的 docker-compose.yml 当前可以正常运行
3. HTTPS 入口将统一由反向代理接管

## 4. WebUI 自身配置

无论采用 Caddy、宝塔还是 1Panel，都建议先把 WebUI 配成生产模式。

修改 config/bot_config.toml 里的 webui 配置段，建议值如下：

```toml
[webui]
enabled = true
mode = "production"
anti_crawler_mode = "loose"
allowed_ips = "127.0.0.1"
trusted_proxies = "127.0.0.1"
trust_xff = true
secure_cookie = true
```

各项的意义：

1. mode = "production"
   让 WebUI 按生产环境运行，并倾向启用更严格的安全行为。
2. secure_cookie = true
   让登录 Cookie 仅在 HTTPS 下传输。
3. trust_xff = true
   允许从反向代理传入的 X-Forwarded-For 获取真实来源 IP。
4. trusted_proxies = "127.0.0.1"
   表示只有来自本机反向代理的 X-Forwarded-For 才被信任。

注意：

1. 如果使用 Docker 内部的反向代理，trusted_proxies 不应固定写 127.0.0.1，而应填写反向代理容器到 MaiBot 的实际来源地址或所在网段。
2. 如果尚未切换到 HTTPS，不要提前开启 secure_cookie = true，否则可能出现登录 Cookie 不生效或握手异常的问题。

## 5. 直接部署方式如何配置 TLS/SSL

这里的“直接部署”指的是：

1. MaiBot 直接跑在宿主机上
2. WebUI 监听本机 127.0.0.1:8001
3. 宿主机安装 Caddy
4. 由 Caddy 负责申请证书和 HTTPS 反代

### 5.1 推荐的网络结构

```text
浏览器
  -> https://maibot.example.com
  -> Caddy :443
  -> 127.0.0.1:8001
  -> MaiBot WebUI
```

### 5.2 宿主机直装 Caddy

以 Debian 或 Ubuntu 为例，参考步骤如下：

```bash
sudo apt update
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update
sudo apt install -y caddy
```

macOS Homebrew 参考：

```bash
brew install caddy
```

### 5.3 Caddyfile 示例

仓库已提供两份可复制的示例文件，请按部署方式选择：

1. 非 Docker 宿主机部署：dashboard/docs/Caddyfile.host.example
2. Docker Compose 部署：dashboard/docs/Caddyfile.docker.example

宿主机直连部署可使用以下最简配置：

```caddyfile
maibot.example.com {
    reverse_proxy 127.0.0.1:8001
}
```

如需显式添加安全头，可以使用增强版：

```caddyfile
maibot.example.com {
    encode zstd gzip

    header {
        Strict-Transport-Security "max-age=31536000; includeSubDomains; preload"
        X-Content-Type-Options "nosniff"
        X-Frame-Options "SAMEORIGIN"
        Referrer-Policy "strict-origin-when-cross-origin"
    }

    reverse_proxy 127.0.0.1:8001
}
```

非 Docker 直接部署建议直接从 dashboard/docs/Caddyfile.host.example 开始修改域名并投入使用。

### 5.4 HSTS 是否启用

可以启用，而且当前推荐由反向代理统一下发 HSTS 响应头，而不是让 WebUI 自己在 FastAPI 层单独处理。

当前仓库提供的两份 Caddy 示例都已经带了 HSTS：

1. dashboard/docs/Caddyfile.host.example
2. dashboard/docs/Caddyfile.docker.example

示例配置中的这一行就是 HSTS：

```caddyfile
Strict-Transport-Security "max-age=31536000; includeSubDomains; preload"
```

这行配置的含义如下：

1. max-age=31536000
   浏览器在 1 年内记住该站点只能使用 HTTPS。
2. includeSubDomains
   所有子域名也必须强制使用 HTTPS。
3. preload
   表示该域名计划提交到浏览器内置的 HSTS preload 列表。

HSTS 建议按下面的节奏启用：

1. 初次上线 HTTPS 时，可以先使用不带 preload 的版本。
2. 确认主域名和所有相关子域名都长期稳定支持 HTTPS 后，再考虑是否加入 preload。
3. 如果无法确认所有子域名都支持 HTTPS，不要轻易保留 includeSubDomains。

更稳妥的起步版本如下：

```caddyfile
Strict-Transport-Security "max-age=31536000"
```

如果所有子域名都已经稳定支持 HTTPS，可以使用：

```caddyfile
Strict-Transport-Security "max-age=31536000; includeSubDomains"
```

只有在满足下面条件时，才建议使用 preload：

1. 主域名始终可通过 HTTPS 访问。
2. 所有子域名都始终可通过 HTTPS 访问。
3. 已明确理解 preload 是长期约束，而不是临时开关。

HSTS 的风险点主要有这些：

1. 一旦浏览器记住该域名只能用 HTTPS，后续临时切回 HTTP 会直接失败。
2. 如果开启 includeSubDomains，而某个子域名并没有部署 HTTPS，该子域名会被浏览器直接拦截。
3. 如果开启 preload 并提交到浏览器列表，撤销成本会比较高，生效和移除都不是即时的。

因此，本文档里的 Caddy 示例更适合作为“完整增强版示例”参考。首次部署时，建议先按实际域名情况，将 HSTS 调整成更合适的版本后再正式上线。

### 5.5 启动与验证

```bash
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl restart caddy
sudo systemctl status caddy
```

检查项：

1. 浏览器访问 https://maibot.example.com 能正常打开登录页
2. 登录后 Cookie 正常写入
3. 日志页和聊天页的 WebSocket 可以正常连接
4. 证书是 Let's Encrypt 或所选颁发机构签发的有效证书

### 5.6 直接部署方式的 Let's Encrypt 申请与续期

Caddy 默认会自动处理证书签发和续期，前提如下：

1. 域名已正确解析到服务器
2. 80 和 443 可从公网访问
3. 没有 CDN、WAF 或安全组拦截 ACME 验证请求

Caddy 的自动续期通常无需手工干预，只需确保：

1. 保持 Caddy 常驻运行
2. 不要阻断 80 和 443
3. 定期关注 Caddy 日志是否存在 ACME 失败记录

常用检查命令：

```bash
sudo journalctl -u caddy -n 200 --no-pager
sudo journalctl -u caddy -f
```

如果续期失败，优先检查：

1. 域名是否仍然解析到当前服务器
2. 80 和 443 是否被防火墙、面板或云安全组拦截
3. 是否存在另一个程序抢占了 80 或 443

## 6. 宝塔面板如何配置 SSL

宝塔适合已经习惯图形化管理 Nginx 站点的部署方式。思路仍然是：由宝塔的站点反向代理到 MaiBot WebUI。

### 6.1 推荐网络结构

```text
浏览器
  -> 宝塔站点 HTTPS
  -> 宝塔 Nginx/OpenResty 反向代理
  -> 127.0.0.1:8001
  -> MaiBot WebUI
```

### 6.2 宝塔站点创建步骤

1. 登录宝塔面板。
2. 进入网站。
3. 添加站点。
4. 域名填写实际使用的 WebUI 域名，例如 maibot.example.com。
5. PHP 版本可以选纯静态或关闭运行环境，重点是站点存在即可。

### 6.3 反向代理配置步骤

1. 进入对应站点。
2. 打开反向代理。
3. 新增反向代理。
4. 目标 URL 填写 http://127.0.0.1:8001。
5. 将发送给 MaiBot 的 `Host` 设为 `$http_host`，保留浏览器访问的主机名和端口，供桌宠连接页设置 WebSocket 的访问策略。

如果使用的是宝塔站点配置文件，也可以手动补这一段：

```nginx
location / {
    proxy_pass http://127.0.0.1:8001;
    proxy_http_version 1.1;
    proxy_set_header Host $http_host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection $connection_upgrade;
}
```

如果宝塔环境没有现成的 connection_upgrade 变量，可以改成：

```nginx
proxy_set_header Upgrade $http_upgrade;
proxy_set_header Connection "upgrade";
```

### 6.4 宝塔中申请 Let's Encrypt 证书

1. 进入站点设置。
2. 打开 SSL。
3. 选择 Let's Encrypt。
4. 勾选对应域名。
5. 申请证书。
6. 开启强制 HTTPS。

### 6.5 宝塔中续期证书

宝塔一般会自动续期，但仍然需要检查：

1. 面板计划任务是否正常运行
2. 80 端口是否在验证时可达
3. 域名解析是否未被改动

建议定期查看：

1. 宝塔站点 SSL 到期时间
2. 宝塔计划任务执行日志
3. 站点错误日志和 Nginx 错误日志

### 6.6 宝塔模式下 WebUI 配置建议

建议保持：

```toml
[webui]
mode = "production"
secure_cookie = true
trust_xff = true
trusted_proxies = "127.0.0.1"
```

如果宝塔和 MaiBot 不在同一台机器上，trusted_proxies 需要换成宝塔所在服务器到 MaiBot 的来源地址。

## 7. 1Panel 如何配置 SSL

1Panel 的逻辑和宝塔类似，本质上也是由面板管理的网关或站点反向代理到 MaiBot WebUI。

### 7.1 推荐网络结构

```text
浏览器
  -> 1Panel 网站/反向代理 HTTPS
  -> OpenResty/Nginx 反向代理
  -> 127.0.0.1:8001 或 core:8001
  -> MaiBot WebUI
```

### 7.2 1Panel 配置步骤

1. 登录 1Panel。
2. 打开网站或反向代理管理。
3. 新建网站，域名填 maibot.example.com。
4. 添加反向代理规则，目标地址指向 http://127.0.0.1:8001。
5. 开启 WebSocket 支持。
6. 保存并重载站点配置。

如果是在 Docker 环境里通过 1Panel 管理容器，目标地址也可以填写容器服务名，例如 http://core:8001，但前提是 1Panel 管理的网关容器与 MaiBot 在同一个 Docker 网络内。

### 7.3 在 1Panel 申请 Let's Encrypt 证书

1. 打开证书管理。
2. 选择 Let's Encrypt。
3. 绑定域名。
4. 选择 HTTP-01 或面板默认验证方式。
5. 完成签发后，把证书绑定到对应网站。
6. 启用 HTTPS。

### 7.4 1Panel 中续期证书

1Panel 通常会自动续期，但需要确认：

1. 自动续期开关处于启用状态
2. 面板的任务调度正常
3. 80 和 443 端口验证时不被拦截
4. 域名始终指向正确服务器

### 7.5 1Panel 模式下的反代头

请确认面板生成的配置会向后端传递：

1. Host
2. X-Real-IP
3. X-Forwarded-For
4. X-Forwarded-Proto
5. Upgrade
6. Connection

缺少 X-Forwarded-Proto 时，WebUI 可能误判为 HTTP，进而影响 secure cookie 与登录行为。

## 8. Docker Compose 下如何配置 TLS/SSL

根目录 docker-compose.yml 已补充默认注释的 Caddy 示例块，用于容器化部署时启用 HTTPS。

Docker 模式下请使用：dashboard/docs/Caddyfile.docker.example

非 Docker 宿主机模式下请使用：dashboard/docs/Caddyfile.host.example

详细步骤请看另一份专项文档：dashboard/docs/webui-tls-ssl-compose.md

这里只先给结论：

1. 启用 Caddy 反向代理时，不应再把 core 的 8001 直接映射到公网
2. 应由 Caddy 容器暴露 80 和 443
3. Caddy 通过容器网络访问 core:8001

## 9. 常见问题

### 9.1 开了 HTTPS 后无法登录

优先检查：

1. webui.secure_cookie 是否在 HTTPS 环境下开启
2. 代理是否正确传递 X-Forwarded-Proto
3. 浏览器访问的是否确实是 https:// 域名而不是 http:// IP
4. Cookie 是否被浏览器策略、扩展或跨站配置拦截

### 9.2 页面能打开，但日志页或聊天页 WebSocket 失败

优先检查：

1. 代理是否支持 WebSocket Upgrade
2. 是否使用了 HTTPS 页面去连接 ws:// 明文地址
3. Caddy、Nginx、宝塔、1Panel 是否有单独的 WebSocket 开关或升级头配置

### 9.3 Let's Encrypt 申请失败

优先检查：

1. 域名解析是否正确
2. 80 端口是否可访问
3. 是否开启了 CDN 代理但没有正确放通验证流量
4. 面板或防火墙是否拦截 ACME 请求

### 9.4 是否能直接用 IP 申请 Let's Encrypt

不能。Let's Encrypt 只为域名签发公开可信证书，不为裸 IP 签发。

### 9.5 内网环境如何测试 HTTPS

可以使用 Caddy 的 tls internal 进行测试，但客户端必须手工信任内部 CA 根证书。正式对外服务仍建议使用有效公网域名和 Let's Encrypt。

## 10. 推荐实践

普通 Linux 服务器部署的推荐顺序如下：

1. 宿主机直装 Caddy
2. WebUI 绑定 127.0.0.1:8001
3. 域名指向服务器
4. 用 Caddy 反代并自动管理 Let's Encrypt

如果已经使用面板管理服务器，则：

1. 宝塔用户直接用宝塔反向代理和 Let's Encrypt
2. 1Panel 用户直接用 1Panel 网站或网关反代和证书管理

如果采用 Docker Compose 部署，则：

1. 使用根目录 compose 中提供的默认注释 Caddy 示例块
2. 注释掉 core 服务里直接暴露 WebUI 的端口映射
3. 由 Caddy 统一对外暴露 80 和 443
