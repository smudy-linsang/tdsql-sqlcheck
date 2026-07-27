"""
TDSQL SQL审核工具 - 测试全局配置 (V2.0)

存量测试（V0.4~V1.0）编写于认证/脱敏机制引入之前，不携带令牌。
为保持存量用例可执行，测试会话默认:
- AUTH_ENABLED=false          （V2.0安全配置在生产默认开启）
- DATA_MASKING_ENABLED=false  （存量用例断言原始SQL文本）
- GITLAB_WEBHOOK_ALLOW_INSECURE=true

V2.0 新增测试（test_v2_*.py）通过 monkeypatch 按用例显式开启
上述安全能力进行验证（配置为动态读取，支持运行期覆盖）。
"""
import os

# 必须在任何 backend 模块导入前设置
os.environ.setdefault("AUTH_ENABLED", "false")
os.environ.setdefault("DATA_MASKING_ENABLED", "false")
os.environ.setdefault("GITLAB_WEBHOOK_ALLOW_INSECURE", "true")
os.environ.setdefault("SCHEDULER_ENABLED", "false")

# V2.1: 系统元数据库为MySQL。测试使用独立的测试库(tdsql_sqlcheck_test)，
# 与部署库(tdsql_sqlcheck)隔离，避免测试数据污染。
os.environ.setdefault("SQLCHECK_DB_HOST", "127.0.0.1")
os.environ.setdefault("SQLCHECK_DB_PORT", "13306")
os.environ.setdefault("SQLCHECK_DB_USER", "root")
os.environ.setdefault("SQLCHECK_DB_PASSWORD", "tdsql_test_2024")
os.environ.setdefault("SQLCHECK_DB_NAME", "tdsql_sqlcheck_test")

import pytest

# 大表治理用例使用的合成实例ID。
# D03 整改后，/api/v1/bigtable/* 会校验实例是否存在、不存在返回 404，
# 因此这些用例必须先在 tdsql_connections 中登记实例，否则测的就不再是
# 业务逻辑而是那条校验本身。
_BIGTABLE_TEST_CONNS = (
    "sit_conn", "sit_conn2", "sit_conn3", "sit_conn4", "sit_conn5",
    "conn_test", "conn_report", "conn_classify", "e2e_conn",
    "uat_e2e_bigtable", "uat_e2e_inspection",
)


@pytest.fixture(scope="session", autouse=True)
def _register_bigtable_test_connections():
    """为大表治理用例登记合成实例（幂等）"""
    try:
        from backend.services.database import _get_connection, ensure_db
        ensure_db()
        conn = _get_connection()
        try:
            for cid in _BIGTABLE_TEST_CONNS:
                conn.execute(
                    "INSERT IGNORE INTO tdsql_connections "
                    "(id, name, host, port, username, password_encrypted, `database`) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (cid, f"测试实例-{cid}", "127.0.0.1", 3306, "test", "", "test_db"))
            conn.commit()
        finally:
            conn.close()
    except Exception:
        # 元数据库不可用时交由各用例自身报错，避免在收集阶段整体中断
        pass
    yield
