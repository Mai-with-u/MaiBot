#!/bin/python3
# 这个程序的作用是辅助麦麦的WebUI更新配置文件，随core容器持续运行。
# 麦麦的配置文件存储于ConfigMap中，挂载进core容器后属于只读文件，无法直接修改。
# 此程序将core容器内的配置文件替换为可读写的中间层临时文件。启动时将实际配置文件写入，并在后台持续检测文件变化，实时同步到k8s apiServer，反向修改ConfigMap。
# 工作目录：/MaiMBot/webui-cm-sync

import os
import time
from datetime import datetime
from kubernetes import client, config
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

work_dir = '/MaiMBot/webui-cm-sync'
os.chdir(work_dir)

config.load_incluster_config()
core_api = client.CoreV1Api()
with open("/var/run/secrets/kubernetes.io/serviceaccount/namespace", "r") as f:
    namespace = f.read().strip()
release_name = os.getenv("RELEASE_NAME")
model_configmap_name = f'{release_name}-maibot-core-model-config'
bot_configmap_name = f'{release_name}-maibot-core-bot-config'

# 过滤列表，只监控指定文件
target_files = {
    os.path.abspath("model_config.toml"): (model_configmap_name, "model_config.toml"),
    os.path.abspath("bot_config.toml"): (bot_configmap_name, "bot_config.toml")
}


def get_configmap(configmap_name: str):
    """获取core的ConfigMap内容"""
    cm = core_api.read_namespaced_config_map(name=configmap_name, namespace=namespace)
    return cm.data


def set_configmap(configmap_name: str, configmap_data: dict[str, str]):
    """设置core的ConfigMap内容"""
    core_api.patch_namespaced_config_map(configmap_name, namespace, {'data': configmap_data})


class ConfigObserverHandler(FileSystemEventHandler):
    """配置文件变化的事件处理器"""
    def on_modified(self, event):
        if os.path.abspath(event.src_path) in target_files:
            print(f'[{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}] File `{event.src_path}` was modified. '
                  f'Start to sync...')
            with open(event.src_path, "r", encoding="utf-8") as _f:
                current_data = _f.read()
            _path = str(os.path.abspath(event.src_path))
            new_cm = {
                target_files[_path][1]: current_data
            }
            try:
                set_configmap(target_files[_path][0], new_cm)
                print(f'\tSync done.')
            except client.exceptions.ApiException as _e:
                print(f'\tError while setting configmap:\n'
                      f'\t\tStatus Code: {_e.status}\n'
                      f'\t\tReason: {_e.reason}')


if __name__ == '__main__':
    # 初始化配置文件
    print(f'[{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}] Initializing config files...')
    try:
        __initial_model_config = get_configmap(model_configmap_name)['model_config.toml']
        __initial_bot_config = get_configmap(bot_configmap_name)['bot_config.toml']
    except client.exceptions.ApiException as e:
        print(f'\tError while getting configmap:\n'
              f'\t\tStatus Code: {e.status}\n'
              f'\t\tReason: {e.reason}')
        exit(1)
    with open('model_config.toml', 'w') as f:
        f.write(__initial_model_config)
    with open('bot_config.toml', 'w') as f:
        f.write(__initial_bot_config)
    with open('ready', 'w') as f:
        f.write('true')
    print(f'[{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}] Initializing done. Ready to sync.')

    # 持续检测变化并同步
    observer = Observer()
    observer.schedule(ConfigObserverHandler(), work_dir, recursive=False)
    observer.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
