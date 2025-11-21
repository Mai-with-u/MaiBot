# 编译 LPMM
FROM python:3.13-slim AS lpmm-builder
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /MaiMBot-LPMM

# 同级目录下需要有 MaiMBot-LPMM
COPY MaiMBot-LPMM /MaiMBot-LPMM

# 安装编译器和编译依赖
RUN apt-get update && apt-get install -y build-essential
RUN uv pip install --system --upgrade pip
RUN cd /MaiMBot-LPMM && uv pip install --system -r requirements.txt

# 编译 LPMM
RUN cd /MaiMBot-LPMM/lib/quick_algo && python build_lib.py --cleanup --cythonize --install

# 运行环境
FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# 工作目录
WORKDIR /MaiMBot

# 复制依赖列表
COPY requirements.txt .

RUN apt-get update && apt-get install -y git

# 从编译阶段复制 LPMM 编译结果
COPY --from=lpmm-builder /usr/local/lib/python3.13/site-packages/ /usr/local/lib/python3.13/site-packages/

# 安装运行时依赖
RUN uv pip install --system --upgrade pip
RUN uv pip install --system -r requirements.txt

# 复制项目代码
COPY . .

EXPOSE 8000

ENTRYPOINT [ "python","bot.py" ]
