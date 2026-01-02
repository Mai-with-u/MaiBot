# 此镜像用于在部署helm chart时动态生成adapter的配置文件
FROM python:3.13-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1

COPY . /app

RUN pip3 install --no-cache-dir -r requirements.txt

ENTRYPOINT ["python3", "preprocessor.py"]
