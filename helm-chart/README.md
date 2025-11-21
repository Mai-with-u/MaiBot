# MaiBot Helm Chart

这是麦麦的Helm Chart，可以方便地将麦麦部署在Kubernetes集群中。

当前Helm Chart对应的麦麦版本可以在`Chart.yaml`中查看`appVersion`项。

详细部署文档：[Kubernetes 部署](https://docs.mai-mai.org/manual/deployment/mmc_deploy_kubernetes.html)

## 可用的Helm Chart版本列表

| Helm Chart版本   | 对应的MaiBot版本  | Commit SHA                               |
|----------------|--------------|------------------------------------------|
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

2. `adapter`: 麦麦的Adapter的部署配置。

3. `core`: 麦麦本体的部署配置。

4. `statistics_dashboard`: 麦麦的运行统计看板部署配置。

   麦麦每隔一段时间会自动输出html格式的运行统计报告，此统计报告可以部署为看板。

   出于隐私考虑，默认禁用。

5. `napcat`: Napcat的部署配置。

   考虑到复用外部Napcat实例的情况，Napcat部署已被解耦。用户可选是否要部署Napcat。

   默认会捆绑部署Napcat。

6. `sqlite_web`: sqlite-web的部署配置。

   通过sqlite-web可以在网页上操作麦麦的数据库，方便调试。不部署对麦麦的运行无影响。

   此服务如果暴露在公网会十分危险，默认不会部署。

7. `config`: 这里填写麦麦各部分组件的运行配置文件。

   这里填写的配置文件需要严格遵守yaml文件的缩进格式。

   - `adapter_config`: 对应adapter的`config.toml`。

     此配置文件中对于`host`和`port`的配置会被上面`adapter.service`中的配置覆盖，因此不需要改动。

   - `core_model_config`: 对应core的`model_config.toml`。

   - `core_bot_config`: 对应core的`bot_config.toml`。

## 部署说明

使用此Helm Chart的一些注意事项。

### 修改麦麦配置

麦麦的配置文件会通过ConfigMap资源注入各个组件内。

对于通过Helm Chart部署的麦麦，如果需要修改配置，不应该直接修改这些ConfigMap，否则下次Helm更新可能会覆盖掉所有配置。

最佳实践是重新配置Helm Chart的values，然后通过`helm upgrade`更新实例。

### 动态生成的ConfigMap

adapter的ConfigMap是每次部署/更新Helm安装实例时动态生成的。

动态生成的原因：

- core服务的DNS名称是动态的，无法在adapter服务的配置文件中提前确定。
- 一些与k8s现有资源冲突的配置需要被重置。

因此，首次部署时，ConfigMap的生成会需要一些时间，部分Pod会无法启动，等待几分钟即可。

### 运行统计看板与core的挂载冲突

如果启用了运行统计看板，那么statistics_dashboard会与core共同挂载statistics_dashboard存储卷，用于同步html文件。

如果k8s集群有多个节点，且statistics_dashboard与core未调度到同一节点，那么就需要statistics_dashboard的PVC访问模式具备`ReadWriteMany`访问模式。

不是所有存储卷的底层存储都支持`ReadWriteMany`访问模式。

如果你的存储底层无法支持`ReadWriteMany`访问模式，你可以通过`nodeSelector`配置将statistics_dashboard与core调度到同一节点来避免问题。

*如果启用了`sqlite-web`，那么上述问题也同样适用于`sqlite-web`与`core`，需要注意。*

### 麦麦的默认插件

麦麦的`core`容器提供了一些默认插件，以提升使用体验。但是插件目录存储在存储卷中，容器启动时挂载的存储卷会完全覆盖掉容器的默认插件目录，导致默认插件无法加载，也难以被用户感知。

为了解决这一问题，此Helm Chart中为`core`容器引入了初始化容器。此初始化容器用于为用户自动安装默认插件到存储卷中。可以选择启用（默认启用）。

*初始化容器使用与`core`主容器相同的镜像，且用后即销毁，因此不会消耗额外的带宽和存储成本。*

#### 触发插件安装的条件

- 首次部署时（此时没有任何插件处于安装状态）
- 默认插件更新（即默认插件内容发生变化）

#### 安装状态识别能力

初始化容器会记录安装过的默认插件，不会重复安装。为了实现这一点，初始化容器会将安装状态写入`/MaiMBot/data/plugins/.installed-setup-plugins`文件中。

基于上述状态识别能力，如果用户不需要某个插件，可以将其删除。由于此插件已自动安装过（记录在状态文件中），即使插件本体不存在也不会再次安装（除非插件更新）。

#### 插件更新

一旦在镜像中检测到新版本插件（即插件内容不同），初始化容器即会用新插件覆盖旧插件。

考虑到旧插件中可能存在用户自定义配置，因此旧插件在被覆盖前会备份到`/MaiMBot/data/plugins-backup`目录中，并以时间归档。

因此在升级麦麦后，请注意观察初始容器的日志并重新配置插件。
