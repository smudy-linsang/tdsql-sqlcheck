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

ROLES = ("admin", "dba", "developer", "auditor")

PBKDF2_ITERATIONS = 240_000

_SECRET_FILE = Path(__file__).parent.parent.parent / "data" / "auth_secret.key"
_secret_cache: Optional[bytes] = None
_secret_lock = threading.Lock()

# 用户状态短缓存（降低每请求DB查询压力），30秒TTL
_user_cache: dict[str, tuple[float, dict]] = {}
_USER_CACHE_TTL = 30.0


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


def issue_token(username: str, role: str) -> str:
    """签发访问令牌: base64url(payload_json).base64url(hmac_sha256)"""
    now = int(time.time())
    payload = {
        "sub": username,
        "role": role,
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

# developer 允许的写操作（开发自助）
_DEVELOPER_WRITE_PREFIXES = (
    "/api/v1/audit/",                       # SQL/文件审核
    "/api/v1/gitlab/audit/",                # diff/仓库审核
    "/api/v1/slow-queries/analyze-explain", # EXPLAIN分析
    "/api/v1/auth/logout",
    "/api/v1/auth/change-password",
)

# 所有角色都允许的写操作（自助账户操作）
_SELF_SERVICE_PREFIXES = (
    "/api/v1/auth/logout",
    "/api/v1/auth/change-password",
)

# 仅 admin 允许的路径（读写）
_ADMIN_ONLY_PREFIXES = (
    "/api/v1/auth/users",
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


def check_permission(role: str, method: str, path: str) -> bool:
    """RBAC 权限判定"""
    method = method.upper()

    # 用户管理仅 admin
    if any(path.startswith(p) for p in _ADMIN_ONLY_PREFIXES):
        return role == "admin"

    if role == "admin":
        return True

    # 自助操作所有角色可用
    if method not in _READ_METHODS and any(path.startswith(p) for p in _SELF_SERVICE_PREFIXES):
        return True

    if role == "dba":
        return True  # 除admin-only外全部读写

    if role == "auditor":
        return method in _READ_METHODS

    if role == "developer":
        if method in _READ_METHODS:
            return True
        return any(path.startswith(p) for p in _DEVELOPER_WRITE_PREFIXES)

    return False


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
            conn.execute(
                "UPDATE users SET password_hash = ?, salt = ?, must_change_password = 0, "
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
        if role not in ROLES:
            return None, f"非法角色: {role}，可选: {', '.join(ROLES)}"
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

    def list_users(self) -> list[dict]:
        """用户列表（脱敏）"""
        ensure_db()
        conn = _get_connection()
        try:
            rows = conn.execute(
                "SELECT * FROM users ORDER BY created_at").fetchall()
            return [self._safe_user(dict(r)) for r in rows]
        finally:
            conn.close()

    def update_user(self, username: str, role: Optional[str] = None,
                    display_name: Optional[str] = None, status: Optional[str] = None,
                    operator: str = "") -> Optional[str]:
        """更新用户属性，返回错误信息或None"""
        if role is not None and role not in ROLES:
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
            conn.execute(
                "UPDATE users SET password_hash = ?, salt = ?, must_change_password = 1, "
                "failed_attempts = 0, locked_until = NULL, updated_at = NOW() "
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
