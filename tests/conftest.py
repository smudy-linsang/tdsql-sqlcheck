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
