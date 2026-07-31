# -*- coding: utf-8 -*-
"""压力测试目标实例配置

两个云上 TDSQL 实例（开发环境）。凭据一律走环境变量注入，禁止硬编码（规约 R-08）：
- SQLCHECK_PRESSURE_DIST_PASSWORD  分布式实例口令（必填，缺失时 fail-fast）
- SQLCHECK_PRESSURE_CENT_PASSWORD  集中式实例口令（必填，缺失时 fail-fast）
- SQLCHECK_PT_API_PASSWORD         后端管理员口令（选填，默认本地测试值）
"""
import os

# 后端 API 地址与管理员账号
API_BASE = "http://127.0.0.1:8000"
API_USER = "admin"
API_PASSWORD = os.getenv("SQLCHECK_PT_API_PASSWORD", "Admin@1234")
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
        "password": os.getenv("SQLCHECK_PRESSURE_DIST_PASSWORD"),
        "database": "tdsql_check",
        "type": "distributed",
    },
    "CENT": {
        "conn_id": CENT_CONN_ID,
        "host": "119.45.220.89",
        "port": 15002,
        "user": "tdsql_check_user",
        "password": os.getenv("SQLCHECK_PRESSURE_CENT_PASSWORD"),
        "database": "tdsql_check2",
        "type": "centralized",
    },
}

# 压测统一前缀，便于识别与清理
TABLE_PREFIX = "pt_"
# 慢查询标记注释，注入到 SQL 中便于在 digest/扫描结果里定位
MARKER = "PT_V152"
