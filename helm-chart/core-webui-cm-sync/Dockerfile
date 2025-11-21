# 此镜像用于辅助麦麦的WebUI更新配置文件，随core容器持续运行
FROM python:3.13-slim

WORKDIR /MaiMBot

COPY . /MaiMBot

RUN pip3 install --no-cache-dir -r requirements.txt

ENTRYPOINT ["python3", "core-webui-cm-sync.py"]
