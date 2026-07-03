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
