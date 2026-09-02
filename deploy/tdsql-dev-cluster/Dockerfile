# ==============================================================================
# Python TDSQL 协议模拟靶场网关镜像 (tdsql-proxy:mock-v1.0)
# 作用: 封装固定依赖环境 (pymysql==1.1.1)，避免在内网或离线启动时在线拉取 pip 包
# ==============================================================================
FROM python:3.11-slim

WORKDIR /app

# 安装固定版本依赖（内网构建时可走阿里云镜像或内网源）
RUN pip install --no-cache-dir pymysql==1.1.1 -i https://mirrors.aliyun.com/pypi/simple/

# 拷贝网关协议服务脚本
COPY tdsql_proxy.py /app/tdsql_proxy.py

EXPOSE 15002

ENTRYPOINT ["python", "/app/tdsql_proxy.py"]
