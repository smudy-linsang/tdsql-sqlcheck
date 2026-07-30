# -*- coding: utf-8 -*-
"""压力测试目标实例配置

两个云上 TDSQL 实例（开发环境）。凭据仅用于开发/测试环境压测，
生产环境请通过实例管理界面配置，勿硬编码。
"""

# 后端 API 地址与管理员账号
API_BASE = "http://127.0.0.1:8000"
API_USER = "admin"
API_PASSWORD = "Admin@1234"

# 已在实例管理注册的连接 ID（用于触发扫描任务）
DIST_CONN_ID = "5ea70d74"   # SIT-分布式实例A
CENT_CONN_ID = "f9ebc77a"   # SIT-集中式实例A

# 实例直连信息（用于建表与制造慢查询负载）
INSTANCES = {
    "DIST": {
        "conn_id": DIST_CONN_ID,
        "host": "119.45.220.89",
        "port": 15005,
        "user": "tdsql_check_user",
        "password": "Abcd@!#1234",
        "database": "tdsql_check",
        "type": "distributed",
    },
    "CENT": {
        "conn_id": CENT_CONN_ID,
        "host": "119.45.220.89",
        "port": 15002,
        "user": "tdsql_check_user",
        "password": "Abcd1234@!#",
        "database": "tdsql_check2",
        "type": "centralized",
    },
}

# 压测统一前缀，便于识别与清理
TABLE_PREFIX = "pt_"
# 慢查询标记注释，注入到 SQL 中便于在 digest/扫描结果里定位
MARKER = "PT_V152"
