"""
TDSQL SQL审核工具 - 认证与授权服务 (V2.0)

银行级用户认证与RBAC权限管理：
- 口令: PBKDF2-HMAC-SHA256 (240,000轮 + 随机盐)，符合等保口令存储要求
- 令牌: HMAC-SHA256 签名的自包含令牌（payload.signature），无第三方依赖
- 登录保护: 连续失败锁定（默认5次锁15分钟）
- 角色: admin(系统管理员) / dba(数据库管理员) / developer(开发人员) / auditor(审计员)

权限矩阵（方法+路径前缀）:
- admin:     全部操作，含用户管理
- dba:       除用户管理外的全部读写（连接/规则/规则集/门禁/扫描/治理）
- developer: 全部只读 + SQL审核/EXPLAIN分析等开发自助操作
- auditor:   全部只读（审计合规岗）
"""
import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from backend import config
from backend.services.database import _get_connection, ensure_db, log_operation

logger = logging.getLogger("tdsql.auth")

# ══════════════════════════════════════════════════════════════════
# 常量
# ══════════════════════════════════════════════════════════════════

# 内置角色（DB中roles表也是数据源，这里作为回退默认值）
_BUILTIN_ROLES = ("admin", "dba", "developer", "auditor")
ROLES = _BUILTIN_ROLES  # 兼容旧代码引用

PBKDF2_ITERATIONS = 240_000

_SECRET_FILE = Path(__file__).parent.parent.parent / "data" / "auth_secret.key"
_secret_cache: Optional[bytes] = None
_secret_lock = threading.Lock()

# 用户状态短缓存（降低每请求DB查询压力），30秒TTL
_user_cache: dict[str, tuple[float, dict]] = {}
_USER_CACHE_TTL = 30.0

# ── 登录失败的 IP 级滑动窗口限流 ──
# 既有的账户锁定只保护"已存在且被反复尝试"的单个账号，攻击者对大量账号
# 各试几次（锁定阈值之下）即可完成密码喷洒。此处按来源 IP 兜底。
#
# 计数源为 operation_logs 中的 login_failed 记录（authenticate 本就会写，带 ip_address）：
# 生产以 --workers 2 运行（deploy/tdsql-sqlcheck.service），若用进程内字典，
# 两个 worker 各算各的，配 15 实际约等于 30，运维按配置值评估 NAT 出口误伤阈值会算错；
# 走元数据库则天然跨 worker、跨副本一致，也不必额外引入 Redis。
# 登录本就有 PBKDF2 24 万次迭代，一次带索引的 COUNT 查询在其面前可忽略。
#
# 内存字典仅作元数据库不可用时的降级兜底（限流属纵深防御，不应因此阻断登录）。
_ip_fail: dict[str, list[float]] = {}
_ip_fail_lock = threading.Lock()


def _count_recent_failures(ip: str, window: float) -> Optional[int]:
    """统计该 IP 在窗口内、且在最近一次成功登录之后的失败次数。

    只数"上次成功之后"的失败，等价于成功即清零：
    NAT 出口后面有正常用户在陆续登录成功，计数就不会持续累积，
    避免早高峰把整个出口 IP 误封；而纯喷洒流量没有成功记录，照常触发限流。
    元数据库不可用时返回 None，由调用方降级为进程内计数。
    """
    try:
        ensure_db()
        conn = _get_connection()
        try:
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM operation_logs "
                "WHERE operation_type = 'login_failed' AND ip_address = ? "
                "AND created_at >= DATE_SUB(NOW(), INTERVAL ? SECOND) "
                "AND created_at > COALESCE(("
                "    SELECT MAX(created_at) FROM operation_logs "
                "    WHERE operation_type = 'login_success' AND ip_address = ? "
                "    AND created_at >= DATE_SUB(NOW(), INTERVAL ? SECOND)"
                "), '1970-01-01')",
                (ip, int(window), ip, int(window))).fetchone()
            return dict(row).get("cnt", 0) if row else 0
        except Exception as e:
            logger.warning(f"读取登录失败计数失败，降级为进程内计数: {e}")
            return None
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"读取登录失败计数失败，降级为进程内计数: {e}")
        return None


def ip_rate_limited(ip: str) -> bool:
    """同一来源 IP 在窗口内登录失败达阈值即限流（阈值 0 表示关闭）"""
    limit = config.login_ip_fail_limit()
    if not ip or limit <= 0:
        return False
    window = config.login_ip_fail_window()

    shared = _count_recent_failures(ip, window)
    if shared is not None:
        return shared >= limit

    now = time.time()
    with _ip_fail_lock:
        hits = [t for t in _ip_fail.get(ip, []) if now - t < window]
        _ip_fail[ip] = hits
        return len(hits) >= limit


def record_ip_failure(ip: str) -> None:
    """记录一次来源 IP 的登录失败（降级路径用；正常计数由 login_failed 审计日志承担）"""
    if not ip or config.login_ip_fail_limit() <= 0:
        return
    window = config.login_ip_fail_window()
    now = time.time()
    with _ip_fail_lock:
        hits = [t for t in _ip_fail.get(ip, []) if now - t < window]
        hits.append(now)
        _ip_fail[ip] = hits
        # 防止长期运行下字典无界增长
        if len(_ip_fail) > 10000:
            for k in [k for k, v in _ip_fail.items()
                      if not v or now - v[-1] > window]:
                _ip_fail.pop(k, None)


def reset_ip_failures(ip: str) -> None:
    """登录成功后清空该 IP 的失败计数"""
    if not ip:
        return
    with _ip_fail_lock:
        _ip_fail.pop(ip, None)


# ══════════════════════════════════════════════════════════════════
# 密钥与口令
# ══════════════════════════════════════════════════════════════════

def _get_secret() -> bytes:
    """令牌签名密钥: AUTH_SECRET_KEY 环境变量 > data/auth_secret.key 文件（自动生成）"""
    global _secret_cache
    if _secret_cache:
        return _secret_cache
    with _secret_lock:
        if _secret_cache:
            return _secret_cache
        env_key = os.getenv("AUTH_SECRET_KEY", "").strip()
        if env_key:
            _secret_cache = env_key.encode()
            return _secret_cache
        if _SECRET_FILE.exists():
            key = _SECRET_FILE.read_bytes().strip()
            if key:
                _secret_cache = key
                return _secret_cache
        key = secrets.token_urlsafe(48).encode()
        _SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
        _SECRET_FILE.write_bytes(key)
        try:
            os.chmod(_SECRET_FILE, 0o600)
        except OSError:
            pass
        logger.warning(
            "未配置 AUTH_SECRET_KEY，已自动生成令牌签名密钥文件 %s。"
            "多副本部署时必须通过环境变量注入统一密钥。", _SECRET_FILE)
        _secret_cache = key
        return _secret_cache


def reset_secret_cache():
    """清空密钥缓存（测试/密钥轮换用）"""
    global _secret_cache
    _secret_cache = None


def hash_password(password: str, salt: Optional[str] = None) -> tuple[str, str]:
    """PBKDF2-HMAC-SHA256 口令哈希，返回 (hash_hex, salt_hex)"""
    if salt is None:
        salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(salt), PBKDF2_ITERATIONS)
    return dk.hex(), salt


def verify_password(password: str, password_hash: str, salt: str) -> bool:
    """常量时间比较验证口令"""
    calc, _ = hash_password(password, salt)
    return hmac.compare_digest(calc, password_hash)


def validate_password_strength(password: str) -> Optional[str]:
    """口令强度校验，返回错误信息或None（通过）"""
    if len(password) < 8:
        return "口令长度不能少于8位"
    checks = [
        any(c.islower() for c in password),
        any(c.isupper() for c in password),
        any(c.isdigit() for c in password),
        any(not c.isalnum() for c in password),
    ]
    if sum(checks) < 3:
        return "口令必须至少包含大写字母、小写字母、数字、特殊字符中的三类"
    return None


# ══════════════════════════════════════════════════════════════════
# 令牌
# ══════════════════════════════════════════════════════════════════

def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def issue_token(username: str, role: str, token_version: int = 0) -> str:
    """签发访问令牌: base64url(payload_json).base64url(hmac_sha256)

    tv 为签发时的用户令牌版本号，校验侧与 users.token_version 比对，
    不一致即视为已吊销（改密/重置/登出会递增该版本号）。
    """
    now = int(time.time())
    payload = {
        "sub": username,
        "role": role,
        "tv": int(token_version or 0),
        "iat": now,
        "exp": now + config.auth_token_ttl_hours() * 3600,
        "jti": secrets.token_hex(8),
    }
    body = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode())
    sig = _b64url_encode(hmac.new(_get_secret(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


def verify_token(token: str) -> Optional[dict]:
    """验证令牌：签名+有效期。返回payload或None。"""
    if not token or "." not in token:
        return None
    try:
        body, sig = token.rsplit(".", 1)
        expected = _b64url_encode(hmac.new(_get_secret(), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            return None
        payload = json.loads(_b64url_decode(body))
        if payload.get("exp", 0) < time.time():
            return None
        return payload
    except Exception:
        return None


# ══════════════════════════════════════════════════════════════════
# RBAC 权限矩阵
# ══════════════════════════════════════════════════════════════════

# 免认证路径（精确或前缀）
# /metrics 供Prometheus抓取（生产环境应通过网络策略限制抓取来源）
PUBLIC_PATHS = {"/health", "/metrics", "/", "/favicon.ico", "/api/v1/auth/login"}
PUBLIC_PREFIXES = ("/static/",)
# GitLab Webhook 由 Secret Token 机制鉴权（非用户令牌）
WEBHOOK_PATHS = ("/api/v1/gitlab/webhook/",)

_READ_METHODS = ("GET", "HEAD", "OPTIONS")

# developer 允许的写操作（开发与测试等业务自助操作）
_OPERATIONAL_WRITE_PREFIXES = (
    "/api/v1/audit/",                       # SQL/文件审核/即时元数据提取与审核
    "/api/v1/gitlab/audit/",                # diff/仓库审核
    "/api/v1/scan-compare/",                # 扫描结果对比/归档
    "/api/v1/slow-queries/analyze-explain", # EXPLAIN分析
    "/api/v1/slow-queries/analyze-explain-by-sql", # EXPLAIN分析(SQL直连)
    "/api/v1/tdsql/slow-queries",           # 慢SQL抓取/触发扫描
    "/api/v1/inspection/schema-check",      # 上线检查(12项Schema检查+报告导出)
    "/api/v1/bigtable/",                    # 大表清单采集
    "/api/v1/auth/logout",
    "/api/v1/auth/change-password",
    "/api/v1/gateway-log/",                 # 网关日志上传分析
    "/api/v1/ppt-report/",                  # PPT汇报导出
    "/api/v1/cluster-inspect/",             # 深度诊断-集群体检
    "/api/v1/daily-inspect/",               # 深度诊断-日常巡检
    "/api/v1/index-audit/",                 # 深度诊断-索引画像
    "/api/v1/schema-diff/",                 # 深度诊断-表结构对比
    "/api/v1/emergency/",                   # 深度诊断-应急工具
    "/api/v1/sql-stats/",                   # 深度诊断-SQL统计
    "/api/v1/toolkit/",                     # 深度诊断-工具箱
)
_DEVELOPER_WRITE_PREFIXES = _OPERATIONAL_WRITE_PREFIXES

# 所有角色都允许的写操作（自助账户操作）
_SELF_SERVICE_PREFIXES = (
    "/api/v1/auth/logout",
    "/api/v1/auth/change-password",
)

# 仅 admin 允许的路径（读写）
_ADMIN_ONLY_PREFIXES = (
    "/api/v1/auth/users",
)

# 仅 admin 和 auditor 允许的路径（禁止 dba 和 developer）
_ADMIN_AUDITOR_ONLY_PREFIXES = (
    "/api/v1/admin/operation-logs",
)

# developer 禁止访问的路径（即使读操作也不允许）
_DEVELOPER_DENIED_PREFIXES = (
    "/api/v1/admin/operation-logs",
)


def is_public_path(path: str) -> bool:
    if path in PUBLIC_PATHS:
        return True
    if any(path.startswith(p) for p in PUBLIC_PREFIXES):
        return True
    if any(path.startswith(p) for p in WEBHOOK_PATHS):
        return True
    if config.docs_public() and path in ("/docs", "/openapi.json", "/redoc", "/docs/oauth2-redirect"):
        return True
    return False


# API路径前缀 -> 菜单key映射（用于role_permissions二级校验）
_PATH_TO_MENU = {
    "/api/v1/dashboard": "dashboard",
    "/api/v1/audit/sql": "audit-sql",
    "/api/v1/audit/file": "file-audit",
    "/api/v1/audit/upload": "file-audit",
    "/api/v1/audit/extract-and-audit": "schema-extractor-audit",
    # V1.5：审核历史列表/删除。batch-delete 为 admin 独占（处理函数 _require_admin 兜底）
    "/api/v1/audit/extracted-reports": "schema-extractor-audit",
    "/api/v1/audit/file-reports": "file-audit",
    "/api/v1/audit/slow-report": ("slow-tasks", "slow-records"),
    "/api/v1/rules": "rules",
    "/api/v1/slow-queries": ("slow-tasks", "slow-records"),
    "/api/v1/tdsql/slow-queries": ("slow-tasks", "slow-records"),
    "/api/v1/tdsql/scan-schedules": "slow-tasks",
    "/api/v1/slow-queries/analyze-explain": "explain",
    "/api/v1/tdsql/connections": "instances",
    "/api/v1/tdsql/discover": "instances",
    "/api/v1/bigtable": "bigtable",
    "/api/v1/projects": "projects",
    "/api/v1/rulesets": "rulesets",
    "/api/v1/gate": "gate",
    "/api/v1/monitor": "monitor",
    "/api/v1/inspection": "schema-check",
    "/api/v1/auth/users": "sys-users",
    "/api/v1/admin/retention": "sys-retention",
    "/api/v1/admin/operation-logs": "sys-auditlog",
    "/api/v1/admin/info": "sys-info",
    "/api/v1/admin/config": "sys-info",
    "/api/v1/admin/logo": "sys-info",
    "/api/v1/auth/roles": "sys-roles",
    "/api/v1/auth/role-permissions": "sys-perms",
    # V1.5：全局默认实例类型。GET 需全角色可访问（前端 B 类选择器），故映射到全员可见的
    # dashboard 菜单；PUT 的真实权限（仅 admin）由处理函数内显式校验保障（双保险）。
    "/api/v1/config": "dashboard",
    # 深度诊断子模块 API 映射
    "/api/v1/cluster-inspect": "deep-diag-cluster",
    "/api/v1/daily-inspect": "deep-diag-daily",
    "/api/v1/index-audit": "deep-diag-index",
    "/api/v1/schema-diff": "deep-diag-diff",
    "/api/v1/emergency": "deep-diag-emergency",
    "/api/v1/sql-stats": "deep-diag-sqlstats",
    "/api/v1/gateway-log": "deep-diag-gateway",
    "/api/v1/ppt-report": "deep-diag-ppt",
    "/api/v1/toolkit": "deep-diag-toolkit",
    # V1.3 扫描结果纵向对比（接口层另按 module 二次校验模块自身权限）
    "/api/v1/scan-compare": ("schema-check", "schema-extractor-audit", "slow-tasks", "bigtable"),
    # ── R01：补齐此前未登记、靠"未映射默认放行"兜底的写端点 ──
    # 兜底放行意味着新端点忘登记就对所有登录角色敞开（fail-open）。
    # 这里把它们全部显式登记，使兜底分支不再覆盖任何实际端点；
    # tests/test_rbac_path_coverage.py 会在新增写端点漏登记时直接失败。
    "/api/v1/tdsql/connect": "instances",
    "/api/v1/tdsql/connect-from-config": "instances",
    "/api/v1/tdsql/test-connection": "instances",
    "/api/v1/tdsql/disconnect": "instances",
    "/api/v1/tdsql/audit": "audit-sql",
    "/api/v1/tdsql/scheduler": "slow-tasks",
    "/api/v1/audit/batch-stream": "file-audit",
    "/api/v1/gitlab/audit": "file-audit",
}

# 独立于特定菜单的已认证只读端点（已登录任意有效角色均可读取，但必须携带有效Token，非 PUBLIC_PATHS）
_MENU_INDEPENDENT_READ_ENDPOINTS = {
    ("GET", "/api/v1/tdsql/connections/options"),
    ("GET", "/api/v1/admin/logo"),
}

def check_permission(role: str, method: str, path: str) -> bool:
    """RBAC 权限判定（含role_permissions二级校验）"""
    method = method.upper()

    # 用户管理仅 admin
    if any(path.startswith(p) for p in _ADMIN_ONLY_PREFIXES):
        return role == "admin"

    # 操作/审计日志仅 admin 和 auditor
    if any(path.startswith(p) for p in _ADMIN_AUDITOR_ONLY_PREFIXES):
        return role in ("admin", "auditor")

    if role == "admin":
        return True

    # 自助操作所有角色可用
    if method not in _READ_METHODS and any(path.startswith(p) for p in _SELF_SERVICE_PREFIXES):
        return True

    # 独立于菜单的已认证只读端点（如全平台下拉选择用的实例精简数据）
    if (method, path) in _MENU_INDEPENDENT_READ_ENDPOINTS:
        return True

    # 第一级：原有角色权限检查
    allowed = False
    if role == "dba":
        allowed = True
    elif role == "auditor":
        allowed = method in _READ_METHODS
    elif role == "developer":
        if any(path.startswith(p) for p in _DEVELOPER_DENIED_PREFIXES):
            return False
        if method in _READ_METHODS:
            allowed = True
        elif method == "PUT" and path.startswith("/api/v1/slow-queries/") and path.endswith("/status"):
            allowed = True
        else:
            allowed = any(path.startswith(p) for p in _DEVELOPER_WRITE_PREFIXES)
    else:
        # 自定义角色（如测试人员角色）：
        # 1) 拒绝敏感审计路径
        if any(path.startswith(p) for p in _DEVELOPER_DENIED_PREFIXES):
            return False
        # 2) 读操作允许（进入二级 role_permissions 校验）
        if method in _READ_METHODS:
            allowed = True
        elif method == "PUT" and path.startswith("/api/v1/slow-queries/") and path.endswith("/status"):
            allowed = True
        else:
            # 3) 业务操作性写端点允许（进入二级 role_permissions 校验）
            allowed = any(path.startswith(p) for p in _DEVELOPER_WRITE_PREFIXES)

    if not allowed:
        return False

    # 第二级：role_permissions菜单可见性校验
    # 对于所有非 admin 角色，检查是否有对应菜单权限
    if role != "admin":
        # 按前缀长度降序排序，结合边界匹配（精确或以/开头），防止部分单词前缀冲突（如gate与gateway-log）及嵌套子路径屏蔽
        sorted_prefixes = sorted(_PATH_TO_MENU.keys(), key=len, reverse=True)
        for prefix in sorted_prefixes:
            if path == prefix or path.startswith(prefix + "/"):
                menu_key = _PATH_TO_MENU[prefix]
                try:
                    visible = get_visible_menus(role)
                    if isinstance(menu_key, (tuple, list, set)):
                        return any(m in visible for m in menu_key)
                    return menu_key in visible
                except Exception:
                    return False
        # 兜底放行（fail-open）。经 R01 整改后此分支不应再覆盖任何业务写端点，
        # 命中即说明有新端点漏登记 _PATH_TO_MENU，打日志便于及时发现。
        if method not in _READ_METHODS:
            logger.warning(
                "写端点未登记 _PATH_TO_MENU，已按兜底放行：%s %s (role=%s)",
                method, path, role)
        return True  # 无映射的路径默认放行

    return True


# ══════════════════════════════════════════════════════════════════
# V3.0: 角色管理 + 权限矩阵
# ══════════════════════════════════════════════════════════════════

# 全部实际展示的菜单key清单（仅包含在前端侧边栏菜单展示的功能项）
ALL_MENU_KEYS = [
    'dashboard', 'audit-sql', 'file-audit', 'schema-extractor-audit',
    'slow-tasks', 'slow-records', 'explain',
    'schema-check', 'bigtable', 'deep-diag',
    'deep-diag-cluster', 'deep-diag-daily', 'deep-diag-index', 'deep-diag-diff',
    'deep-diag-emergency', 'deep-diag-sqlstats', 'deep-diag-gateway', 'deep-diag-ppt',
    'deep-diag-toolkit',
    'instances', 'rules', 'rulesets',
    'sys-users', 'sys-roles', 'sys-perms', 'sys-retention', 'sys-auditlog', 'sys-info',
]

# 菜单中文标签
MENU_LABELS = {
    'dashboard': '治理概览', 'audit-sql': '即时审核', 'file-audit': '文件审核', 'schema-extractor-audit': '在线元数据审核',
    'rules': '审核规则库', 'slow-tasks': '扫描任务', 'slow-records': '慢SQL记录',
    'slow-schedule': '扫描计划', 'explain': 'EXPLAIN分析', 'instances': '实例管理',
    'schema-check': '上线检查', 'bigtable': '大表治理', 'deep-diag': '深度诊断',
    'deep-diag-cluster': '深度诊断-集群巡检', 'deep-diag-daily': '深度诊断-日常巡检与对比报告',
    'deep-diag-index': '深度诊断-索引体检', 'deep-diag-diff': '深度诊断-结构比对',
    'deep-diag-emergency': '深度诊断-应急诊断', 'deep-diag-sqlstats': '深度诊断-SQL分析',
    'deep-diag-gateway': '深度诊断-网关日志分析', 'deep-diag-ppt': '深度诊断-PDF报告与大屏',
    'deep-diag-toolkit': '深度诊断-运维工具箱',
    'rulesets': '评估规则集', 'monitor': '监控告警',
    'inspection': '巡检管理', 'sys-users': '用户管理', 'sys-retention': '数据保留',
    'sys-auditlog': '操作审计', 'sys-info': '系统信息',
    'sys-roles': '角色管理', 'sys-perms': '权限矩阵',
    'scan-compare': '扫描结果对比',
}

def get_all_roles() -> list[dict]:
    """从DB获取全部角色"""
    ensure_db()
    conn = _get_connection()
    try:
        rows = conn.execute("SELECT * FROM roles ORDER BY is_builtin DESC, role_id").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

def get_role_ids() -> tuple[str, ...]:
    """获取全部角色ID元组（替代原ROLES常量）"""
    try:
        roles = get_all_roles()
        if roles:
            return tuple(r['role_id'] for r in roles)
    except Exception:
        pass
    return _BUILTIN_ROLES

def create_custom_role(role_id: str, role_name: str, description: str = "") -> dict:
    """创建自定义角色"""
    ensure_db()
    conn = _get_connection()
    try:
        existing = conn.execute("SELECT role_id FROM roles WHERE role_id = ?", (role_id,)).fetchone()
        if existing:
            return {"error": f"角色ID '{role_id}' 已存在"}
        conn.execute("""
            INSERT INTO roles(role_id, role_name, is_builtin, description)
            VALUES (?, ?, 0, ?)
        """, (role_id, role_name, description))
        # 默认全部菜单不可见，管理员后续配置
        for mk in ALL_MENU_KEYS:
            conn.execute("INSERT IGNORE INTO role_permissions(role_id, menu_key, visible) VALUES(?, ?, 0)", (role_id, mk))
        conn.commit()
        log_operation("system", "create_role", "role", role_id)
        return {"role_id": role_id, "role_name": role_name, "is_builtin": 0, "description": description}
    finally:
        conn.close()

def update_role(role_id: str, role_name: str = None, description: str = None) -> bool:
    """编辑角色"""
    ensure_db()
    conn = _get_connection()
    try:
        sets = []
        params = []
        if role_name is not None:
            sets.append("role_name = ?")
            params.append(role_name)
        if description is not None:
            sets.append("description = ?")
            params.append(description)
        if not sets:
            return False
        params.append(role_id)
        conn.execute(f"UPDATE roles SET {', '.join(sets)} WHERE role_id = ?", params)
        conn.commit()
        return True
    finally:
        conn.close()

def delete_role(role_id: str) -> dict:
    """删除自定义角色（内置不可删）"""
    ensure_db()
    conn = _get_connection()
    try:
        row = conn.execute("SELECT is_builtin FROM roles WHERE role_id = ?", (role_id,)).fetchone()
        if not row:
            return {"error": "角色不存在"}
        if row["is_builtin"]:
            return {"error": "内置角色不可删除"}
        # 检查是否有用户使用该角色
        users = conn.execute("SELECT COUNT(*) AS c FROM users WHERE role = ?", (role_id,)).fetchone()
        if users["c"] > 0:
            return {"error": f"该角色下还有 {users['c']} 个用户，请先调整用户角色"}
        conn.execute("DELETE FROM role_permissions WHERE role_id = ?", (role_id,))
        conn.execute("DELETE FROM roles WHERE role_id = ?", (role_id,))
        conn.commit()
        log_operation("system", "delete_role", "role", role_id)
        return {"message": f"角色 {role_id} 已删除"}
    finally:
        conn.close()

def get_role_permissions() -> list[dict]:
    """获取全部角色权限矩阵"""
    ensure_db()
    conn = _get_connection()
    try:
        rows = conn.execute("""
            SELECT rp.role_id, rp.menu_key, rp.visible, r.role_name, r.is_builtin
            FROM role_permissions rp
            JOIN roles r ON rp.role_id = r.role_id
            ORDER BY rp.role_id, rp.menu_key
        """).fetchall()
        return [dict(r) for r in rows if r["menu_key"] in ALL_MENU_KEYS]
    finally:
        conn.close()

def set_role_permissions(role_id: str, permissions: dict) -> bool:
    """批量设置某角色的菜单可见性。permissions = {menu_key: 0|1, ...}"""
    ensure_db()
    conn = _get_connection()
    try:
        # 仅允许当前交付的菜单键。已下线功能的历史权限行不会展示，也不能被
        # 旧页面或手工请求重新写回为有效权限。
        for mk, visible in permissions.items():
            if mk not in ALL_MENU_KEYS:
                continue
            conn.execute("""
                INSERT INTO role_permissions(role_id, menu_key, visible)
                VALUES (?, ?, ?)
                ON DUPLICATE KEY UPDATE visible = VALUES(visible)
            """, (role_id, mk, 1 if visible else 0))
        _bump_permission_version(conn)
        conn.commit()
        _VISIBLE_MENUS_CACHE.clear()
        log_operation("system", "set_role_permissions", "role", role_id)
        return True
    finally:
        conn.close()

def _bump_permission_version(conn):
    """递增权限配置版本号，通知所有 worker 实时失效本地缓存"""
    try:
        ver = str(int(time.time() * 1000))
        conn.execute("REPLACE INTO system_config(config_key, config_value) VALUES('permission_version', ?)", (ver,))
    except Exception:
        pass

_LOCAL_PERM_VERSION = None
_VISIBLE_MENUS_CACHE = {}  # {role: (timestamp, menus_list)}

def get_visible_menus(role: str) -> list[str]:
    """获取某角色可见的菜单key列表 (支持跨 Worker 实时失效)"""
    global _LOCAL_PERM_VERSION
    now = time.time()
    
    ensure_db()
    conn = _get_connection()
    try:
        # 检查数据库中是否存在最新的权限版本变更
        db_ver = None
        try:
            row = conn.execute("SELECT config_value FROM system_config WHERE config_key = 'permission_version'").fetchone()
            if row:
                db_ver = row["config_value"]
        except Exception:
            pass

        # 若权限版本发生变化，清空本地缓存
        if db_ver and db_ver != _LOCAL_PERM_VERSION:
            _VISIBLE_MENUS_CACHE.clear()
            _LOCAL_PERM_VERSION = db_ver
        elif role in _VISIBLE_MENUS_CACHE:
            ts, cached_menus = _VISIBLE_MENUS_CACHE[role]
            if now - ts < 30:
                return cached_menus

        rows = conn.execute(
            "SELECT menu_key FROM role_permissions WHERE role_id = ? AND visible = 1",
            (role,)
        ).fetchall()
        if rows:
            res = [r["menu_key"] for r in rows if r["menu_key"] in ALL_MENU_KEYS]
        elif role == "admin":
            res = ALL_MENU_KEYS
        else:
            res = ['dashboard', 'audit-sql', 'file-audit', 'schema-extractor-audit', 'rules']
        _VISIBLE_MENUS_CACHE[role] = (now, res)
        return res
    except Exception:
        if role == "admin":
            return ALL_MENU_KEYS
        return ['dashboard', 'audit-sql', 'file-audit', 'schema-extractor-audit', 'rules']
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════
# 用户管理
# ══════════════════════════════════════════════════════════════════

class AuthService:
    """用户认证与管理服务"""

    # ── 引导 ─────────────────────────────────────────────

    def ensure_bootstrap_admin(self) -> Optional[str]:
        """
        系统无任何用户时创建初始 admin 账户。

        口令来源: ADMIN_INITIAL_PASSWORD 环境变量；未配置则随机生成，
        打印到日志一次并强制首次登录修改。

        Returns:
            生成的随机口令（仅当自动生成时返回，供日志输出），否则 None
        """
        ensure_db()
        conn = _get_connection()
        try:
            count = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
            if count > 0:
                return None
            initial = config.admin_initial_password()
            generated = None
            if not initial:
                generated = secrets.token_urlsafe(12)
                initial = generated
            pw_hash, salt = hash_password(initial)
            conn.execute("""
                INSERT INTO users(username, display_name, role, password_hash, salt,
                                  status, must_change_password, created_by)
                VALUES ('admin', '系统管理员', 'admin', ?, ?, 'active', ?, 'system')
            """, (pw_hash, salt, 1 if generated else 0))
            conn.commit()
            if generated:
                logger.warning(
                    "已创建初始管理员账户 admin，随机初始口令: %s "
                    "（仅本次显示，首次登录必须修改口令）", generated)
            else:
                logger.info("已按 ADMIN_INITIAL_PASSWORD 创建初始管理员账户 admin")
            return generated
        finally:
            conn.close()

    # ── 认证 ─────────────────────────────────────────────

    def authenticate(self, username: str, password: str,
                     ip_address: str = "") -> tuple[Optional[dict], Optional[str]]:
        """
        用户登录认证。

        Returns:
            (user_dict, None) 成功；(None, 错误信息) 失败
        """
        ensure_db()
        conn = _get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM users WHERE username = ?", (username,)).fetchone()
            if not row:
                log_operation(username, "login_failed", "user", username,
                              "用户不存在", ip_address)
                return None, "用户名或口令错误"

            user = dict(row)

            if user["status"] != "active":
                log_operation(username, "login_failed", "user", username,
                              "账户已禁用", ip_address)
                return None, "账户已禁用，请联系管理员"

            # 锁定检查
            if user["locked_until"]:
                locked_until = datetime.fromisoformat(user["locked_until"])
                if locked_until > datetime.now():
                    remain = int((locked_until - datetime.now()).total_seconds() // 60) + 1
                    return None, f"账户已锁定，请{remain}分钟后重试"

            if not verify_password(password, user["password_hash"], user["salt"]):
                failures = user["failed_attempts"] + 1
                locked_until = None
                if failures >= config.auth_max_login_failures():
                    locked_until = (datetime.now() + timedelta(
                        minutes=config.auth_lock_minutes())).isoformat()
                conn.execute(
                    "UPDATE users SET failed_attempts = ?, locked_until = ?, "
                    "updated_at = NOW() WHERE username = ?",
                    (failures, locked_until, username))
                conn.commit()
                log_operation(username, "login_failed", "user", username,
                              f"口令错误(第{failures}次)", ip_address)
                if locked_until:
                    return None, f"连续失败{failures}次，账户已锁定{config.auth_lock_minutes()}分钟"
                return None, "用户名或口令错误"

            # 认证成功：清零失败计数
            conn.execute(
                "UPDATE users SET failed_attempts = 0, locked_until = NULL, "
                "last_login_at = NOW(), updated_at = NOW() "
                "WHERE username = ?", (username,))
            conn.commit()
            _user_cache.pop(username, None)
            log_operation(username, "login_success", "user", username, "", ip_address)
            return self._safe_user(user), None
        finally:
            conn.close()

    def get_user(self, username: str, use_cache: bool = True) -> Optional[dict]:
        """按用户名查询用户（带短TTL缓存，供每请求校验）"""
        now = time.time()
        if use_cache:
            cached = _user_cache.get(username)
            if cached and now - cached[0] < _USER_CACHE_TTL:
                return cached[1]
        ensure_db()
        conn = _get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM users WHERE username = ?", (username,)).fetchone()
            if not row:
                return None
            user = self._safe_user(dict(row))
            _user_cache[username] = (now, user)
            return user
        finally:
            conn.close()

    def bump_token_version(self, username: str) -> None:
        """递增用户令牌版本号，使其此前签发的全部令牌立即失效。

        用于改密 / 管理员重置口令 / 主动登出。失败仅告警：吊销属安全增强，
        不应因元数据库抖动阻断主流程（口令本身已改，旧口令无法再登录）。
        """
        try:
            ensure_db()
            conn = _get_connection()
            try:
                conn.execute(
                    "UPDATE users SET token_version = token_version + 1 "
                    "WHERE username = ?", (username,))
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            logger.warning(f"递增令牌版本号失败 username={username}: {e}")
        finally:
            # 必须清缓存，否则中间件仍读到旧版本号，吊销不生效
            _user_cache.pop(username, None)

    def change_password(self, username: str, old_password: str,
                        new_password: str) -> Optional[str]:
        """用户自助修改口令，返回错误信息或None（成功）"""
        err = validate_password_strength(new_password)
        if err:
            return err
        ensure_db()
        conn = _get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM users WHERE username = ?", (username,)).fetchone()
            if not row:
                return "用户不存在"
            if not verify_password(old_password, row["password_hash"], row["salt"]):
                return "原口令错误"
            pw_hash, salt = hash_password(new_password)
            # token_version 与口令同事务递增：改密即吊销此前签发的全部令牌
            conn.execute(
                "UPDATE users SET password_hash = ?, salt = ?, must_change_password = 0, "
                "token_version = token_version + 1, "
                "updated_at = NOW() WHERE username = ?",
                (pw_hash, salt, username))
            conn.commit()
            _user_cache.pop(username, None)
            log_operation(username, "change_password", "user", username)
            return None
        finally:
            conn.close()

    # ── 用户管理（admin） ─────────────────────────────────

    def create_user(self, username: str, password: str, role: str,
                    display_name: str = "", operator: str = "") -> tuple[Optional[dict], Optional[str]]:
        """创建用户，返回 (user, None) 或 (None, 错误信息)"""
        valid_roles = get_role_ids()
        if role not in valid_roles:
            return None, f"非法角色: {role}，可选: {', '.join(valid_roles)}"
        if not username or not username.replace("_", "").replace(".", "").isalnum():
            return None, "用户名只能包含字母、数字、下划线和点"
        err = validate_password_strength(password)
        if err:
            return None, err
        ensure_db()
        conn = _get_connection()
        try:
            exists = conn.execute(
                "SELECT 1 FROM users WHERE username = ?", (username,)).fetchone()
            if exists:
                return None, "用户名已存在"
            pw_hash, salt = hash_password(password)
            conn.execute("""
                INSERT INTO users(username, display_name, role, password_hash, salt,
                                  status, must_change_password, created_by)
                VALUES (?, ?, ?, ?, ?, 'active', 1, ?)
            """, (username, display_name, role, pw_hash, salt, operator))
            conn.commit()
            log_operation(operator, "create_user", "user", username, f"role={role}")
            return self.get_user(username, use_cache=False), None
        finally:
            conn.close()

    def list_users(self, limit: int = 50, offset: int = 0,
                   keyword: str = "") -> dict:
        """用户列表（脱敏、分页）

        返回 {"users": [...], "total": N}。此前全量返回，银行数百账号场景下
        管理端一次性拉全表、页面卡顿。
        """
        ensure_db()
        conn = _get_connection()
        try:
            where, args = "", []
            if keyword:
                where = "WHERE username LIKE ? OR display_name LIKE ?"
                args = [f"%{keyword}%", f"%{keyword}%"]
            total_row = conn.execute(
                f"SELECT COUNT(*) AS cnt FROM users {where}", args).fetchone()
            total = dict(total_row).get("cnt", 0) if total_row else 0
            rows = conn.execute(
                f"SELECT * FROM users {where} ORDER BY created_at LIMIT ? OFFSET ?",
                (*args, limit, offset)).fetchall()
            return {"users": [self._safe_user(dict(r)) for r in rows],
                    "total": total}
        finally:
            conn.close()

    def update_user(self, username: str, role: Optional[str] = None,
                    display_name: Optional[str] = None, status: Optional[str] = None,
                    operator: str = "") -> Optional[str]:
        """更新用户属性，返回错误信息或None"""
        if role is not None:
            valid_roles = get_role_ids()
            if role not in valid_roles:
                return f"非法角色: {role}"
        if status is not None and status not in ("active", "disabled"):
            return f"非法状态: {status}"
        ensure_db()
        conn = _get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM users WHERE username = ?", (username,)).fetchone()
            if not row:
                return "用户不存在"
            # 保护：不允许禁用/降级最后一个active admin
            if (role and role != "admin") or status == "disabled":
                if row["role"] == "admin":
                    admins = conn.execute(
                        "SELECT COUNT(*) AS c FROM users WHERE role='admin' AND status='active'"
                    ).fetchone()["c"]
                    if admins <= 1:
                        return "系统必须保留至少一个可用的管理员账户"
            sets, params = [], []
            if role is not None:
                sets.append("role = ?"); params.append(role)
            if display_name is not None:
                sets.append("display_name = ?"); params.append(display_name)
            if status is not None:
                sets.append("status = ?"); params.append(status)
            if not sets:
                return None
            sets.append("updated_at = NOW()")
            params.append(username)
            conn.execute(f"UPDATE users SET {', '.join(sets)} WHERE username = ?", params)
            conn.commit()
            _user_cache.pop(username, None)
            log_operation(operator, "update_user", "user", username,
                          f"role={role} status={status}")
            return None
        finally:
            conn.close()

    def reset_password(self, username: str, new_password: str,
                       operator: str = "") -> Optional[str]:
        """管理员重置用户口令（强制下次登录修改）"""
        err = validate_password_strength(new_password)
        if err:
            return err
        ensure_db()
        conn = _get_connection()
        try:
            row = conn.execute(
                "SELECT 1 FROM users WHERE username = ?", (username,)).fetchone()
            if not row:
                return "用户不存在"
            pw_hash, salt = hash_password(new_password)
            # token_version 同事务递增：管理员重置口令即踢掉该用户所有在途会话
            conn.execute(
                "UPDATE users SET password_hash = ?, salt = ?, must_change_password = 1, "
                "failed_attempts = 0, locked_until = NULL, "
                "token_version = token_version + 1, updated_at = NOW() "
                "WHERE username = ?", (pw_hash, salt, username))
            conn.commit()
            _user_cache.pop(username, None)
            log_operation(operator, "reset_password", "user", username)
            return None
        finally:
            conn.close()

    def unlock_user(self, username: str, operator: str = "") -> Optional[str]:
        """管理员解锁账户"""
        ensure_db()
        conn = _get_connection()
        try:
            row = conn.execute(
                "SELECT 1 FROM users WHERE username = ?", (username,)).fetchone()
            if not row:
                return "用户不存在"
            conn.execute(
                "UPDATE users SET failed_attempts = 0, locked_until = NULL, "
                "updated_at = NOW() WHERE username = ?", (username,))
            conn.commit()
            _user_cache.pop(username, None)
            log_operation(operator, "unlock_user", "user", username)
            return None
        finally:
            conn.close()

    def delete_user(self, username: str, operator: str = "") -> Optional[str]:
        """删除用户（保留最后一个admin）"""
        ensure_db()
        conn = _get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM users WHERE username = ?", (username,)).fetchone()
            if not row:
                return "用户不存在"
            if row["role"] == "admin":
                admins = conn.execute(
                    "SELECT COUNT(*) AS c FROM users WHERE role='admin' AND status='active'"
                ).fetchone()["c"]
                if admins <= 1:
                    return "系统必须保留至少一个可用的管理员账户"
            conn.execute("DELETE FROM users WHERE username = ?", (username,))
            conn.commit()
            _user_cache.pop(username, None)
            log_operation(operator, "delete_user", "user", username)
            return None
        finally:
            conn.close()

    @staticmethod
    def _safe_user(user: dict) -> dict:
        """移除口令哈希等敏感字段"""
        return {k: v for k, v in user.items() if k not in ("password_hash", "salt")}


# 全局单例
auth_service = AuthService()
