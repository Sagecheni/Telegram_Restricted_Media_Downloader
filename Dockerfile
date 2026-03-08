FROM python:3.13.2-slim

ARG APT_MIRROR=mirrors.tuna.tsinghua.edu.cn
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ARG PIP_TRUSTED_HOST=pypi.tuna.tsinghua.edu.cn

# 设置工作目录。
WORKDIR /app

# 设置环境变量。
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    XDG_CONFIG_HOME=/app \
    TERM=xterm-256color

# 安装系统依赖+Python依赖，编译后清理，全部合并为一个RUN层以减小镜像体积。
COPY requirements.txt .

RUN set -eux; \
    if [ -n "$APT_MIRROR" ]; then \
        if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
            sed -i "s|http://deb.debian.org/debian|https://${APT_MIRROR}/debian|g; s|http://security.debian.org/debian-security|https://${APT_MIRROR}/debian-security|g" /etc/apt/sources.list.d/debian.sources; \
        elif [ -f /etc/apt/sources.list ]; then \
            sed -i "s|http://deb.debian.org/debian|https://${APT_MIRROR}/debian|g; s|http://security.debian.org/debian-security|https://${APT_MIRROR}/debian-security|g" /etc/apt/sources.list; \
        fi; \
    fi; \
    apt-get update && apt-get install -y --no-install-recommends \
        libmediainfo0v5 \
        gcc \
        g++ \
    && pip install --no-cache-dir -i "$PIP_INDEX_URL" --trusted-host "$PIP_TRUSTED_HOST" -r requirements.txt \
    && apt-get purge -y gcc g++ \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

# 创建配置目录、下载目录、会话目录、临时目录、可执行程序目录。
RUN mkdir -p /app/TRMD /app/downloads /app/sessions /app/temp /res/bin

# 复制项目文件。
COPY main.py .
COPY module/ ./module/

# 复制可执行程序。
COPY res/bin/ttyd* ./res/bin/
COPY res/bin/tmux* ./res/bin/

# 添加可执行程序执行权限。
RUN chmod +x ./res/bin/ttyd* ./res/bin/tmux* 2>/dev/null || true

# 设置挂载点。
VOLUME ["/app/TRMD", "/app/downloads", "/app/sessions", "/app/temp"]

# 运行应用。
# --config: 用户配置存到挂载目录，容器重启不丢失。
# session_directory和temp_directory可在config.yaml中自行配置。
CMD ["python", "main.py", "--config", "/app/TRMD/config.yaml"]
