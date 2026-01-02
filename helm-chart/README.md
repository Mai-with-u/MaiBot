# MaiBot Helm Chart

这是麦麦的Helm Chart，可以方便地将麦麦部署在Kubernetes集群中。

当前Helm Chart对应的麦麦版本可以在`Chart.yaml`中查看`appVersion`项。

详细部署文档：[Kubernetes 部署](https://docs.mai-mai.org/manual/deployment/mmc_deploy_kubernetes.html)

## 可用的Helm Chart版本列表

| Helm Chart版本   | 对应的MaiBot版本  | Commit SHA                               |
|----------------|--------------|------------------------------------------|
| 0.12.0         | 0.12.0       | baa6e90be7b20050fe25dfc74c0c70653601d00e |
| 0.11.6-beta    | 0.11.6-beta  | 0bfff0457e6db3f7102fb7f77c58d972634fc93c |
| 0.11.5-beta    | 0.11.5-beta  | ad2df627001f18996802f23c405b263e78af0d0f |
| 0.11.3-beta    | 0.11.3-beta  | cd6dc18f546f81e08803d3b8dba48e504dad9295 |
| 0.11.2-beta    | 0.11.2-beta  | d3c8cea00dbb97f545350f2c3d5bcaf252443df2 |
| 0.11.1-beta    | 0.11.1-beta  | 94e079a340a43dff8a2bc178706932937fc10b11 |
| 0.11.0-beta    | 0.11.0-beta  | 16059532d8ef87ac28e2be0838ff8b3a34a91d0f |
| 0.10.3-beta    | 0.10.3-beta  | 7618937cd4fd0ab1a7bd8a31ab244a8b0742fced |
| 0.10.0-alpha.0 | 0.10.0-alpha | 4efebed10aad977155d3d9e0c24bc6e14e1260ab |

## TL; DR

```shell
helm install maimai \
    oci://reg.mikumikumi.xyz/maibot/maibot \
    --namespace bot \
    --version <MAIBOT_VERSION> \
    --values maibot.yaml
```

## Values项说明

`values.yaml`分为几个大部分。

1. `EULA` & `PRIVACY`: 用户必须同意这里的协议才能成功部署麦麦。

2. `pre_processor`: 部署之前的预处理Job的配置。

3. `adapter`: 麦麦的Adapter的部署配置。

4. `core`: 麦麦本体的部署配置。

5. `statistics_dashboard`: 麦麦的运行统计看板部署配置。

   麦麦每隔一段时间会自动输出html格式的运行统计报告，此统计报告可以部署为看板。

   出于隐私考虑，默认禁用。

6. `napcat`: Napcat的部署配置。

   考虑到复用外部Napcat实例的情况，Napcat部署已被解耦。用户可选是否要部署Napcat。

   默认会捆绑部署Napcat。

7. `sqlite_web`: sqlite-web的部署配置。

   通过sqlite-web可以在网页上操作麦麦的数据库，方便调试。不部署对麦麦的运行无影响。

   此服务如果暴露在公网会十分危险，默认不会部署。

8. `config`: 这里填写麦麦各部分组件的运行配置。

   这里填写的配置仅会在初次部署时或用户指定时覆盖实际配置文件，且需要严格遵守yaml文件的缩进格式。

   - `override_*_config`: 指定本次部署/升级是否用以下配置覆盖实际配置文件。默认不覆盖。

   - `adapter_config`: 对应adapter的`config.toml`。

     此配置文件中对于`napcat_server`和`maibot_server`的`host`和`port`字段的配置会被上面`adapter.service`中的配置覆盖，因此不需要改动。

   - `core_model_config`: 对应core的`model_config.toml`。

   - `core_bot_config`: 对应core的`bot_config.toml`。

## 部署说明

使用此Helm Chart的一些注意事项。

### 麦麦的配置

要修改麦麦的配置，最好的方法是通过WebUI来操作。此处的配置只会在初次部署时或者指定覆盖时注入到MaiBot中。

`0.11.6-beta`之前的版本将配置存储于k8s的ConfigMap资源中。随着版本迭代，MaiBot对配置文件的操作复杂性增加，k8s的适配复杂度也同步增加，且WebUI可以直接修改配置文件，因此自`0.11.6-beta`版本开始，各组件的配置不再存储于k8s的ConfigMap中，而是直接存储于存储卷的实际文件中。

从旧版本升级的用户，旧的ConfigMap的配置会自动迁移到新的存储卷的配置文件中。

### 部署时自动重置的配置

adapter的配置中的`napcat_server`和`maibot_server`的`host`和`port`字段，会在每次部署/更新Helm安装实例时被自动重置。
core的配置中的`webui`和`maim_message`的部分字段也会在每次部署/更新Helm安装实例时被自动重置。

自动重置的原因：

- core的Service的DNS名称是动态的（由安装实例名拼接），无法在adapter的配置文件中提前确定。
- 为了使adapter监听所有地址以及保持Helm Chart中配置的端口号，需要在adapter的配置文件中覆盖这些配置。
- core的WebUI启停需要由helm chart控制，以便正常创建Service和Ingress资源。
- core的maim_message的api server现在可以作为k8s服务暴露出来。监听的IP和端口需要由helm chart控制，以便Service正确映射。

首次部署时，预处理任务会负责重置这些配置。这会需要一些时间，因此部署进程可能比较慢，且部分Pod可能会无法启动，等待一分钟左右即可。

### 跨节点PVC挂载问题

MaiBot的一些组件会挂载同一PVC，这主要是为了同步数据或修改配置。

如果k8s集群有多个节点，且共享相同PVC的Pod未调度到同一节点，那么就需要此PVC访问模式具备`ReadWriteMany`访问模式。

不是所有存储控制器都支持`ReadWriteMany`访问模式。

如果你的存储控制器无法支持`ReadWriteMany`访问模式，你可以通过`nodeSelector`配置将彼此之间共享相同PVC的Pod调度到同一节点来避免问题。

会共享PVC的组件列表：

- `core`和`adapter`：共享`adapter-config`，用于为`core`的WebUI提供修改adapter的配置文件的能力。
- `core`和`statistics-dashboard`：共享`statistics-dashboard`，用于同步统计数据的html文件。
- `core`和`sqlite-web`：共享`maibot-core`，用于为`sqlite-web`提供操作MaiBot数据库的能力。
- 部署时的预处理任务`preprocessor`和`adapter`、`core`：共享`adapter-config`和`core-config`，用于初始化`core`和`adapter`的配置文件。
