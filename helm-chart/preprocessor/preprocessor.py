#!/bin/python3
# 此脚本会被helm chart的post-install hook触发，在正式部署后通过k8s的job自动运行一次。
# 这个脚本的作用是在部署helm chart时迁移旧版ConfigMap到配置文件，调整adapter的配置文件中的服务监听和服务连接字段，调整core的配置文件中的maim_message_api_server和WebUI配置。
#
# - 迁移旧版ConfigMap到配置文件是因为0.11.6-beta之前版本的helm chart将各个配置文件存储在k8s的ConfigMap中，
#   由于功能复杂度提升，自0.11.6-beta版本开始配置文件采用文件形式存储到存储卷中。
#   从旧版升级来的用户会通过这个脚本自动执行配置的迁移。
#
# - 需要调整adapter的配置文件的原因是:
#   1. core的Service的DNS名称是动态的（由安装实例名拼接），无法在adapter的配置文件中提前确定。
#      用于对外连接的maibot_server.host和maibot_server.port字段，会被替换为core的Service对应的DNS名称和8000端口（硬编码，用户无需配置）。
#   2. 为了使adapter监听所有地址以及保持chart中配置的端口号，需要在adapter的配置文件中覆盖这些配置。
#      用于监听的napcat_server.host和napcat_server.port字段，会被替换为0.0.0.0和8095端口（实际映射到的Service端口会在Service中配置）。
#
# - 需要调整core的配置文件的原因是：
#   1. core的WebUI启停需要由helm chart控制，以便正常创建Service和Ingress资源。
#      配置文件中的webui.enabled、webui.allowed_ips将由此脚本覆盖为正确配置。
#   2. core的maim_message的api server现在可以作为k8s服务暴露出来。监听的IP和端口需要由helm chart控制，以便Service正确映射。
#      配置文件中的maim_message.enable_api_server、maim_message.api_server_host、maim_message.api_server_port将由此脚本覆盖为正确配置。

import os
import toml
import time
import base64
from kubernetes import client, config
from kubernetes.client.exceptions import ApiException
from datetime import datetime, timezone

config.load_incluster_config()
core_api = client.CoreV1Api()
apps_api = client.AppsV1Api()

# 读取部署的关键信息
with open("/var/run/secrets/kubernetes.io/serviceaccount/namespace", 'r') as f:
    namespace = f.read().strip()
release_name = os.getenv("RELEASE_NAME").strip()
is_webui_enabled = os.getenv("IS_WEBUI_ENABLED").lower() == "true"
is_maim_message_api_server_enabled = os.getenv("IS_MMSG_ENABLED").lower() == "true"
config_adapter_b64 = os.getenv("CONFIG_ADAPTER_B64")
config_core_env_b64 = os.getenv("CONFIG_CORE_ENV_B64")
config_core_bot_b64 = os.getenv("CONFIG_CORE_BOT_B64")
config_core_model_b64 = os.getenv("CONFIG_CORE_MODEL_B64")


def log(func: str, msg: str, level: str = 'INFO'):
    print(f'[{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}] [{level}] [{func}] {msg}')


def migrate_old_config():
    """迁移旧版配置"""
    func_name = 'migrate_old_config'
    log(func_name, 'Checking whether there are old configmaps to migrate...')
    old_configmap_version = None
    status_migrating = {  # 存储adapter的config.toml、core的bot_config.toml和model_config.toml三个文件的迁移状态
        'adapter_config.toml': False,
        'core_bot_config.toml': False,
        'core_model_config.toml': False
    }

    # 如果存储卷中已存在配置文件，则跳过迁移
    if os.path.isfile('/app/config/core/bot_config.toml') or os.path.isfile('/app/config/core/model_config.toml') or \
            os.path.isfile('/app/config/adapter/config.toml'):
        log(func_name, 'Found existing config file(s) in PV. Migration will be ignored. Done.')
        return

    def migrate_cm_to_file(cm_name: str, key_name: str, file_path: str) -> bool:
        """检测是否有指定名称的configmap，如果有的话备份到指定的配置文件里并删除configmap，返回是否已备份"""
        try:
            cm = core_api.read_namespaced_config_map(
                name=cm_name,
                namespace=namespace
            )
            log(func_name, f'\tMigrating `{key_name}` of `{cm_name}`...')
            with open(file_path, 'w', encoding='utf-8') as _f:
                _f.write(cm.data[key_name])
            core_api.delete_namespaced_config_map(
                name=cm_name,
                namespace=namespace
            )
            log(func_name, f'\tSuccessfully migrated `{key_name}` of `{cm_name}`.')
        except ApiException as e:
            if e.status == 404:
                return False
        return True

    # 对于0.11.5-beta版本，adapter的config.toml、core的bot_config.toml和model_config.toml均存储于不同的ConfigMap，需要依次迁移
    if True not in status_migrating.values():
        status_migrating['adapter_config.toml'] = migrate_cm_to_file(f'{release_name}-maibot-adapter-config',
                                                                     'config.toml',
                                                                     '/app/config/adapter/config.toml')
        status_migrating['core_bot_config.toml'] = migrate_cm_to_file(f'{release_name}-maibot-core-bot-config',
                                                                      'bot_config.toml',
                                                                      '/app/config/core/bot_config.toml')
        status_migrating['core_model_config.toml'] = migrate_cm_to_file(f'{release_name}-maibot-core-model-config',
                                                                        'model_config.toml',
                                                                        '/app/config/core/model_config.toml')
    if True in status_migrating.values():
        old_configmap_version = '0.11.5-beta'

    # 对于低于0.11.5-beta的版本，adapter的1个配置和core的3个配置位于各自的configmap中
    if True not in status_migrating.values():
        status_migrating['adapter_config.toml'] = migrate_cm_to_file(f'{release_name}-maibot-adapter',
                                                                     'config.toml',
                                                                     '/app/config/adapter/config.toml')
        status_migrating['core_bot_config.toml'] = migrate_cm_to_file(f'{release_name}-maibot-core',
                                                                      'bot_config.toml',
                                                                      '/app/config/core/bot_config.toml')
        status_migrating['core_model_config.toml'] = migrate_cm_to_file(f'{release_name}-maibot-core',
                                                                        'model_config.toml',
                                                                        '/app/config/core/model_config.toml')
    if True in status_migrating.values():
        old_configmap_version = 'before 0.11.5-beta'

    if old_configmap_version:
        log(func_name, f'Migrating status for version `{old_configmap_version}`:')
        for k, v in status_migrating.items():
            log(func_name, f'\t{k}: {v}')
        if False in status_migrating.values():
            log(func_name, 'There is/are config(s) that not been migrated. Please check the config manually.',
                level='WARNING')
        else:
            log(func_name, 'Successfully migrated old configs. Done.')
    else:
        log(func_name, 'Old config not found. Ignoring migration. Done.')


def write_config_files():
    """当注入了配置文件时（一般是首次安装或者用户指定覆盖），将helm chart注入的配置写入存储卷中的实际文件"""
    func_name = 'write_config_files'
    log(func_name, 'Detecting config files...')
    if config_adapter_b64:
        log(func_name, '\tWriting `config.toml` of adapter...')
        config_str = base64.b64decode(config_adapter_b64).decode("utf-8")
        with open('/app/config/adapter/config.toml', 'w', encoding='utf-8') as _f:
            _f.write(config_str)
        log(func_name, '\t`config.toml` of adapter wrote.')
    if True:  # .env直接覆盖
        log(func_name, '\tWriting .env file of core...')
        config_str = base64.b64decode(config_core_env_b64).decode("utf-8")
        with open('/app/config/core/.env', 'w', encoding='utf-8') as _f:
            _f.write(config_str)
        log(func_name, '\t`.env` of core wrote.')
    if config_core_bot_b64:
        log(func_name, '\tWriting `bot_config.toml` of core...')
        config_str = base64.b64decode(config_core_bot_b64).decode("utf-8")
        with open('/app/config/core/bot_config.toml', 'w', encoding='utf-8') as _f:
            _f.write(config_str)
        log(func_name, '\t`bot_config.toml` of core wrote.')
    if config_core_model_b64:
        log(func_name, '\tWriting `model_config.toml` of core...')
        config_str = base64.b64decode(config_core_model_b64).decode("utf-8")
        with open('/app/config/core/model_config.toml', 'w', encoding='utf-8') as _f:
            _f.write(config_str)
        log(func_name, '\t`model_config.toml` of core wrote.')
    log(func_name, 'Detection done.')


def reconfigure_adapter():
    """调整adapter的配置文件的napcat_server和maibot_server字段，使其Service能被napcat连接以及连接到core的Service"""
    func_name = 'reconfigure_adapter'
    log(func_name, 'Reconfiguring `config.toml` of adapter...')
    with open('/app/config/adapter/config.toml', 'r', encoding='utf-8') as _f:
        config_adapter = toml.load(_f)
    config_adapter.setdefault('napcat_server', {})
    config_adapter['napcat_server']['host'] = '0.0.0.0'
    config_adapter['napcat_server']['port'] = 8095
    config_adapter.setdefault('maibot_server', {})
    config_adapter['maibot_server']['host'] = f'{release_name}-maibot-core'  # 根据release名称动态拼接core服务的DNS名称
    config_adapter['maibot_server']['port'] = 8000
    with open('/app/config/adapter/config.toml', 'w', encoding='utf-8') as _f:
        _f.write(toml.dumps(config_adapter))
    log(func_name, 'Reconfiguration done.')


def reconfigure_core():
    """调整core的配置文件的webui和maim_message字段，使其服务能被正确映射"""
    func_name = 'reconfigure_core'
    log(func_name, 'Reconfiguring `bot_config.toml` of core...')
    with open('/app/config/core/bot_config.toml', 'r', encoding='utf-8') as _f:
        config_core = toml.load(_f)
    config_core.setdefault('webui', {})
    config_core['webui']['enabled'] = is_webui_enabled
    config_core['webui']['allowed_ips'] = '0.0.0.0/0'  # 部署于k8s内网，使用宽松策略
    config_core.setdefault('maim_message', {})
    config_core['maim_message']['enable_api_server'] = is_maim_message_api_server_enabled
    config_core['maim_message']['api_server_host'] = '0.0.0.0'
    config_core['maim_message']['api_server_port'] = 8090
    with open('/app/config/core/bot_config.toml', 'w', encoding='utf-8') as _f:
        _f.write(toml.dumps(config_core))
    log(func_name, 'Reconfiguration done.')


def _scale_statefulsets(statefulsets: list[str], replicas: int, wait: bool = False, timeout: int = 300):
    """调整指定几个statefulset的副本数，wait参数控制是否等待调整完成再返回"""
    statefulsets = set(statefulsets)
    for name in statefulsets:
        apps_api.patch_namespaced_stateful_set_scale(
            name=name,
            namespace=namespace,
            body={"spec": {"replicas": replicas}}
        )
    if not wait:
        return

    start_time = time.time()
    while True:
        remaining_pods = []

        pods = core_api.list_namespaced_pod(namespace).items

        for pod in pods:
            owners = pod.metadata.owner_references or []
            for owner in owners:
                if owner.kind == "StatefulSet" and owner.name in statefulsets:
                    remaining_pods.append(pod.metadata.name)

        if not remaining_pods:
            return

        elapsed = time.time() - start_time
        if elapsed > timeout:
            raise TimeoutError(
                f"Timeout waiting for Pods to be deleted. "
                f"Remaining Pods: {remaining_pods}"
            )
        time.sleep(5)


def _restart_statefulset(name: str, ignore_error: bool = False):
    """重启指定的statefulset"""
    now = datetime.now(timezone.utc).isoformat()
    body = {
        "spec": {
            "template": {
                "metadata": {
                    "annotations": {
                        "kubectl.kubernetes.io/restartedAt": now
                    }
                }
            }
        }
    }
    try:
        apps_api.patch_namespaced_stateful_set(
            name=name,
            namespace=namespace,
            body=body
        )
    except ApiException as e:
        if ignore_error:
            pass
        else:
            raise e


if __name__ == '__main__':
    log('main', 'Start to process data before install/upgrade...')
    log('main', 'Scaling adapter and core to 0...')
    _scale_statefulsets([f'{release_name}-maibot-adapter', f'{release_name}-maibot-core'], 0, wait=True)
    migrate_old_config()
    write_config_files()
    reconfigure_adapter()
    reconfigure_core()
    log('main', 'Scaling adapter and core to 1...')
    _scale_statefulsets([f'{release_name}-maibot-adapter', f'{release_name}-maibot-core'], 1)
    log('main', 'Process done.')
