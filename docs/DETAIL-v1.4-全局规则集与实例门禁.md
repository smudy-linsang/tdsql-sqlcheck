# TDSQL-SQLCheck V1.4 详细设计说明书
## 全局规则集 + 实例级质量门禁（照图施工）

| 项目 | 内容 |
|---|---|
| 文档类型 | 详细设计说明书 |
| 版本 | V1.4（基线代码 v1.3.3.1，commit `6fece52`） |
| 施工要求 | 本文档按「现状代码（文件:行）→ 修改后代码 → 兼容性 → 验证方法」组织，可照图施工 |
| 关联文档 | 《ARCHITECTURE-v1.4》《DB-v1.4》《API-v1.4》 |

> **行号说明**：所有行号基于基线 commit `6fece52`。施工时若行号已漂移，以文中给出的**代码片段原文**定位，不要机械按行号改。

---

## 目录

1. 施工顺序与依赖
2. S1 数据层
3. S2 全局规则集解析（核心）
4. S3 审核链路改造
5. S4 门禁改绑实例
6. S5 可追溯性与对比校验
7. S6 前端改造
8. S7 灰度与开关
9. 测试用例清单
10. 施工检查清单

---

## 1. 施工顺序与依赖

```
S1 数据层（建表/增列/迁移）
      ↓
S2 全局规则集解析 ──► S3 审核链路改造
      ↓                      ↓
S4 门禁改绑实例        S5 可追溯性与对比校验
      ↓                      ↓
            S6 前端改造
                  ↓
            S7 灰度与开关
```

**强制顺序**：S1 必须先行（S2/S4 依赖新表与新列）。S3 依赖 S2。其余可并行。

---

## 2. S1 数据层

### 2.1 迁移文件

新建 `backend/schema/v3/030_global_ruleset_gate.sql`，内容见《DB-v1.4》§6.2。

> `backend/schema/` 下现有 `v0` / `v1` / `v2`，新增 `v3` 目录即可，`loader.py` 按目录遍历，**无需改动加载代码**。

### 2.2 幂等增列（`backend/services/database.py`）

**现状**（v1.3.3 迁移段末尾）：

```python
    if "users" in table_names:
        # 会话吊销：令牌载荷携带 tv，与本列不符即视为已失效。
        _add_column_if_not_exists(conn, "users", "token_version", "INT NOT NULL DEFAULT 0")

    conn.commit()
```

**修改后**：

```python
    if "users" in table_names:
        # 会话吊销：令牌载荷携带 tv，与本列不符即视为已失效。
        _add_column_if_not_exists(conn, "users", "token_version", "INT NOT NULL DEFAULT 0")

    # ── V1.4 可追溯性：记录每次评估实际生效的规则集 ──
    # 规则集改为全局启用后，"这份报告用的哪把尺"必须落库，
    # 否则历史报告事后不可复现（V1.3 及以前只落 project_id，而项目的规则集可随时改）。
    if "audit_history" in table_names:
        _add_column_if_not_exists(conn, "audit_history", "rule_set_id",
                                  "VARCHAR(64) DEFAULT NULL")
    if "scan_snapshots" in table_names:
        _add_column_if_not_exists(conn, "scan_snapshots", "rule_set_id",
                                  "VARCHAR(64) DEFAULT NULL")
    if "gate_audit_logs" in table_names:
        # 门禁绑定对象由项目改为实例，判定依据需要落库
        _add_column_if_not_exists(conn, "gate_audit_logs", "connection_id",
                                  "VARCHAR(64) DEFAULT NULL")
        _add_column_if_not_exists(conn, "gate_audit_logs", "rule_set_id",
                                  "VARCHAR(64) DEFAULT NULL")

    conn.commit()
```

**不建索引**：这三列均无查询筛选场景（仅写入与展示），建索引是纯浪费。依据见《DB-v1.4》§8。

### 2.3 建表语句登记

在 `database.py` 的建表列表中追加 `instance_gate_rules`（语句见《DB-v1.4》§3.1），位置紧随 `gate_rules`（T06）之后，便于阅读时看到新旧对照。

### 2.4 默认配置初始化

在 `_init_default_data()` 中追加：

```python
    # V1.4：全局生效规则集，兜底指向内置 default
    conn.execute("""
        INSERT IGNORE INTO system_config(config_key, config_value)
        VALUES ('active_rule_set_id', 'default')
    """)
```

> ⚠ **施工陷阱**（v1.3 踩过）：`_init_default_data` 中存在
> `DELETE FROM role_permissions WHERE menu_key NOT IN (...)` 这类"硬编码白名单 + 删除不在白名单者"的逻辑。
> 本次若新增菜单键（见 §7.1），**必须同步加入该 `all_menus` 列表**，否则 INSERT 后会被立即删除。

### 2.5 验证

```sql
-- 新表存在且默认值正确
SHOW CREATE TABLE instance_gate_rules\G
-- 三处增列到位
SHOW COLUMNS FROM audit_history LIKE 'rule_set_id';
SHOW COLUMNS FROM scan_snapshots LIKE 'rule_set_id';
SHOW COLUMNS FROM gate_audit_logs LIKE 'connection_id';
-- 全局键已初始化
SELECT * FROM system_config WHERE config_key = 'active_rule_set_id';
```

---

## 3. S2 全局规则集解析（核心）

### 3.1 现状代码

`backend/services/ruleset_service.py:181-195`：

```python
    def get_overrides_for_project(self, project_id: Optional[str]) -> Optional[dict]:
        """按项目解析规则集覆盖（project → rule_set_id → overrides）"""
        if not project_id:
            return None
        ensure_db()
        conn = _get_connection()
        try:
            row = conn.execute(
                "SELECT rule_set_id FROM projects WHERE project_id = ?",
                (project_id,)).fetchone()
            if not row:
                return None
            return self.get_overrides(row["rule_set_id"])
        finally:
            conn.close()
```

### 3.2 新增：全局解析 + 缓存

在 `ruleset_service.py` 模块级新增（放在类定义之前）：

```python
import threading
import time

# ── V1.4 全局生效规则集的进程内缓存 ──
# 生产以 --workers 2 运行（deploy/tdsql-sqlcheck.service:13），进程内缓存在
# 多 worker 下不会互相失效，因此本缓存的实际语义是：
#   切换规则集后，最长 _ACTIVE_CACHE_TTL 秒全量生效。
# 对外表述一律按此口径，不得写成"即时生效"（v1.3.3 会话吊销已有同类教训）。
# 逐条审核都查库属浪费，而追求即时生效需跨进程失效通知，代价与收益不匹配。
_ACTIVE_CACHE_TTL = 30.0
_active_cache: dict = {"at": 0.0, "rule_set_id": None, "overrides": None}
_active_cache_lock = threading.Lock()

# 兜底规则集 ID（database.py 已 INSERT IGNORE 保证其存在）
DEFAULT_RULE_SET_ID = "default"
ACTIVE_CONFIG_KEY = "active_rule_set_id"
```

在 `RuleSetService` 类中新增三个方法：

```python
    def get_active_rule_set_id(self) -> str:
        """解析当前全局生效的规则集 ID（带兜底链）。

        兜底顺序（《ARCHITECTURE-v1.4》§3.2）：
            system_config.active_rule_set_id
              ↓ 键不存在 / 值为空 / 指向的规则集已被删除
            'default'
        任何异常均回落 'default'——尺度解析不允许抛异常打断审核主流程。
        """
        try:
            ensure_db()
            conn = _get_connection()
            try:
                row = conn.execute(
                    "SELECT config_value FROM system_config WHERE config_key = ?",
                    (ACTIVE_CONFIG_KEY,)).fetchone()
                rid = (dict(row).get("config_value") or "").strip() if row else ""
                if not rid:
                    return DEFAULT_RULE_SET_ID
                # 指向的规则集必须仍然存在，否则回落
                exists = conn.execute(
                    "SELECT 1 FROM rule_sets WHERE id = ?", (rid,)).fetchone()
                return rid if exists else DEFAULT_RULE_SET_ID
            finally:
                conn.close()
        except Exception as e:
            logger.warning(f"解析全局规则集失败，回落默认规则集: {e}")
            return DEFAULT_RULE_SET_ID

    def get_active_overrides(self) -> tuple[str, Optional[dict]]:
        """返回 (生效规则集ID, 规则覆盖字典)。带 30 秒进程内缓存。

        返回二元组而不只返回 overrides：调用方需要把规则集 ID 落库
        （audit_history / scan_snapshots / gate_audit_logs），
        若只返回 overrides，调用方要再查一次才知道用的是哪把尺。
        """
        now = time.time()
        with _active_cache_lock:
            if now - _active_cache["at"] < _ACTIVE_CACHE_TTL \
                    and _active_cache["rule_set_id"] is not None:
                return _active_cache["rule_set_id"], _active_cache["overrides"]

        rid = self.get_active_rule_set_id()
        overrides = self.get_overrides(rid)

        with _active_cache_lock:
            _active_cache.update({"at": now, "rule_set_id": rid, "overrides": overrides})
        return rid, overrides

    def set_active_rule_set(self, rule_set_id: str, operator: str = "") -> Optional[str]:
        """切换全局生效规则集。返回错误信息或 None（成功）。"""
        if not rule_set_id:
            return "必须指定规则集ID"
        ensure_db()
        conn = _get_connection()
        try:
            exists = conn.execute(
                "SELECT id FROM rule_sets WHERE id = ?", (rule_set_id,)).fetchone()
            if not exists:
                return f"规则集不存在: {rule_set_id}"
            conn.execute("""
                INSERT INTO system_config(config_key, config_value) VALUES (?, ?)
                ON DUPLICATE KEY UPDATE config_value = VALUES(config_value)
            """, (ACTIVE_CONFIG_KEY, rule_set_id))
            conn.commit()
        finally:
            conn.close()
        invalidate_active_cache()
        log_operation(operator or "system", "set_active_rule_set",
                      "rule_set", rule_set_id, f"全局生效规则集切换为 {rule_set_id}")
        return None
```

模块级再加一个失效函数：

```python
def invalidate_active_cache() -> None:
    """清空本进程的生效规则集缓存。

    仅对当前 worker 生效——其它 worker 仍最长 30 秒后自然过期。
    这是有意为之的取舍，见 _ACTIVE_CACHE_TTL 处的说明。
    """
    with _active_cache_lock:
        _active_cache.update({"at": 0.0, "rule_set_id": None, "overrides": None})
```

### 3.3 规则集内容被编辑时也要失效

`rule_set_items` 被修改后，缓存中的 overrides 会过期。在既有的规则集条目保存方法（`save_items` / `update_ruleset_items` 一类）末尾追加：

```python
        invalidate_active_cache()
```

**若不加**：管理员编辑当前生效规则集的条目后，最长 30 秒内仍按旧配置审核——虽不致命，但会让"我明明关了这条规则"变成一个说不清的现象。

### 3.4 `get_overrides_for_project` 的处置

**保留方法但标注废弃**，不删除——`api/quality_gate.py` 等处可能仍有引用，删除会引发连锁改动：

```python
    def get_overrides_for_project(self, project_id: Optional[str]) -> Optional[dict]:
        """DEPRECATED(V1.4)：规则集已改为全局启用，项目不再决定尺度。

        保留仅为兼容存量调用；新代码一律用 get_active_overrides()。
        为避免"看似按项目生效、实则不然"的误解，本方法直接返回全局结果。
        """
        return self.get_active_overrides()[1]
```

> 关键取舍：**不是让它继续按项目解析**，而是让它也返回全局结果。否则会出现"有的路径按全局、有的路径按项目"的双轨，正是本次要消灭的问题。

### 3.5 验证

```python
# 切换后立即生效（同进程）
svc.set_active_rule_set("rs_strict", operator="admin")
rid, ov = svc.get_active_overrides()
assert rid == "rs_strict"

# 指向不存在的规则集时回落
conn.execute("UPDATE system_config SET config_value='ghost' WHERE config_key='active_rule_set_id'")
svc_other_process.invalidate_active_cache()
assert svc.get_active_rule_set_id() == "default"
```

---

## 4. S3 审核链路改造

### 4.1 `audit_service._resolve_overrides`

**现状**（`backend/services/audit_service.py:81-90`）：

```python
    def _resolve_overrides(self, project_id: Optional[str]) -> Optional[dict]:
        """按项目解析规则集覆盖"""
        if not project_id:
            return None
        try:
            from backend.services.ruleset_service import ruleset_service
            return ruleset_service.get_overrides_for_project(project_id)
        except Exception as e:
            logger.warning(f"解析项目规则集失败(按默认规则执行): {e}")
            return None
```

**修改后**：

```python
    def _resolve_scale(self) -> tuple[str, Optional[dict]]:
        """解析当前全局生效的评估尺度，返回 (规则集ID, 规则覆盖)。

        V1.4：尺度由管理员全局启用，不再随调用方传入的 project_id 变化——
        这正是为了消除"换个项目再扫一次，问题就变少了"的伪命题。
        解析失败一律回落全默认，绝不因尺度解析异常打断审核主流程。
        """
        try:
            from backend.services.ruleset_service import ruleset_service
            return ruleset_service.get_active_overrides()
        except Exception as e:
            logger.warning(f"解析全局规则集失败(按引擎默认执行): {e}")
            return "default", None
```

保留旧方法名做薄封装，避免遗漏调用点：

```python
    def _resolve_overrides(self, project_id: Optional[str] = None) -> Optional[dict]:
        """DEPRECATED(V1.4)：保留仅为兼容；尺度已全局化，project_id 被忽略"""
        return self._resolve_scale()[1]
```

### 4.2 `audit_single_sql`

**现状**（`audit_service.py:104`）：

```python
        overrides = self._resolve_overrides(project_id)
```

**修改后**：

```python
        rule_set_id, overrides = self._resolve_scale()
```

后续 `_save_audit_history(...)` 调用处追加 `rule_set_id=rule_set_id`（见 §4.4）。

### 4.3 `audit_file_content`

**现状**（`audit_service.py:181`）：

```python
        overrides = self._resolve_overrides(project_id)
```

**修改后**：与 §4.2 相同。

### 4.4 `_save_audit_history` 落库补 `rule_set_id`

**现状**（`audit_service.py:52-62`，v1.3 已扩过一次参）：

```python
            cursor.execute("""
                INSERT INTO audit_history (audit_type, source, total_sql, passed, failed,
                    error_count, warning_count, pass_rate, results_json,
                    created_by, project_id, gate_passed, gate_detail, created_at,
                    connection_id, db_name)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
```

**修改后**：

```python
            cursor.execute("""
                INSERT INTO audit_history (audit_type, source, total_sql, passed, failed,
                    error_count, warning_count, pass_rate, results_json,
                    created_by, project_id, gate_passed, gate_detail, created_at,
                    connection_id, db_name, rule_set_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
```

函数签名同步增加 `rule_set_id: str = ""`，并在参数元组末尾追加 `rule_set_id or None`。

> **注意**：写 `None` 而非空串。`NULL` 的语义是"V1.4 之前的历史记录，尺度未知"（《DB-v1.4》§4.4），空串会污染这个语义。

### 4.5 在线元数据审核（F1）

`backend/api/sql_audit.py` 的 `extract_and_audit` 中，`_save_audit_history(...)` 调用处（v1.3 已补 `connection_id` / `db_name`）追加 `rule_set_id`。

同一函数内 V1.3 的快照旁路（`snapshot_extractors`）调用 `safe_create_snapshot` 时，meta 字典追加：

```python
                "rule_set_id": rule_set_id,
```

### 4.6 GitLab MR 审核

`backend/api/gitlab_hook.py` 中调用审核服务处，**移除**传入 `project_id` 作为尺度依据的逻辑（若有）。GitLab 的 `project_id` 是 GitLab 侧的项目号，与本系统 `projects.project_id` 无关，仅用于回帖定位，不受本次影响。

### 4.7 验证

| 验证点 | 方法 |
|---|---|
| 传不同 project_id，审核结果一致 | 同一段 SQL 分别带 `project_id=A` / `project_id=B` / 不传，三次 violation 集合必须完全相同 |
| 切换全局规则集后结果随之改变 | 关掉某规则 → 重扫 → 该规则不再命中 |
| 审核记录落了 rule_set_id | `SELECT rule_set_id FROM audit_history ORDER BY id DESC LIMIT 1` |

---

## 5. S4 门禁改绑实例

### 5.1 新增 `instance_gate_service`

新建 `backend/services/instance_gate_service.py`：

```python
"""
实例级质量门禁（V1.4）

门禁绑定对象由「项目」改为「实例」：同一把评估尺度（全局规则集）下，
不同实例可以有不同的放行标准——核心账务库与内部报表库本就不该一视同仁。
"""
import logging
from typing import Optional

from backend.services.database import _get_connection, ensure_db, log_operation

logger = logging.getLogger(__name__)

# 未配置实例的兜底默认值（决策：ERROR / WARNING 上限均为 0）
DEFAULT_MAX_ERROR = 0
DEFAULT_MAX_WARNING = 0
DEFAULT_MODE = "enforce"
VALID_MODES = ("enforce", "observe")


class InstanceGateService:

    def get_rule(self, connection_id: str) -> dict:
        """取实例门禁配置；未配置返回系统默认。

        不预先为每个实例插行——预插会在新增实例时产生同步负担，
        漏插即行为不一致；兜底逻辑只需这一处。
        """
        fallback = {
            "connection_id": connection_id,
            "max_error_count": DEFAULT_MAX_ERROR,
            "max_warning_count": DEFAULT_MAX_WARNING,
            "mode": DEFAULT_MODE,
            "is_default": True,
        }
        if not connection_id:
            return fallback
        try:
            ensure_db()
            conn = _get_connection()
            try:
                row = conn.execute(
                    "SELECT connection_id, max_error_count, max_warning_count, mode "
                    "FROM instance_gate_rules WHERE connection_id = ?",
                    (connection_id,)).fetchone()
                if not row:
                    return fallback
                d = dict(row)
                d["is_default"] = False
                return d
            finally:
                conn.close()
        except Exception as e:
            logger.warning(f"读取实例门禁配置失败，按系统默认判定: {e}")
            return fallback

    def save_rule(self, connection_id: str, max_error_count: int,
                  max_warning_count: int, mode: str = DEFAULT_MODE,
                  description: str = "", operator: str = "") -> Optional[str]:
        """保存实例门禁配置。返回错误信息或 None（成功）。"""
        if not connection_id:
            return "必须指定实例"
        if mode not in VALID_MODES:
            return f"非法判定模式: {mode}"
        for name, val in (("ERROR", max_error_count), ("WARNING", max_warning_count)):
            if val < -1:
                return f"{name} 上限非法：{val}（-1 表示不限，其余须 >= 0）"

        ensure_db()
        conn = _get_connection()
        try:
            exists = conn.execute(
                "SELECT id FROM tdsql_connections WHERE id = ?",
                (connection_id,)).fetchone()
            if not exists:
                return f"实例不存在: {connection_id}"
            conn.execute("""
                INSERT INTO instance_gate_rules
                    (connection_id, max_error_count, max_warning_count, mode,
                     description, updated_by)
                VALUES (?,?,?,?,?,?)
                ON DUPLICATE KEY UPDATE
                    max_error_count = VALUES(max_error_count),
                    max_warning_count = VALUES(max_warning_count),
                    mode = VALUES(mode),
                    description = VALUES(description),
                    updated_by = VALUES(updated_by)
            """, (connection_id, max_error_count, max_warning_count, mode,
                  description, operator))
            conn.commit()
        finally:
            conn.close()
        log_operation(operator or "system", "set_instance_gate_rule",
                      "instance_gate_rule", connection_id,
                      f"error<={max_error_count};warning<={max_warning_count};mode={mode}")
        return None


instance_gate_service = InstanceGateService()
```

### 5.2 门禁判定改造

**现状**（`backend/services/gate_service.py:27-38`）：

```python
    def evaluate(self, violations: list[Violation], gate_rule: Optional[GateRule] = None) -> GateResult:
        if gate_rule is None:
            gate_rule = self.get_gate_rule("default")
```

**修改后**：新增一个按实例判定的入口，**保留原方法不动**（`cli.py` 等仍在调用）：

```python
    def evaluate_for_instance(self, violations: list[Violation],
                              connection_id: str) -> GateResult:
        """按实例门禁判定（V1.4）。

        observe 模式下照常计算与记录，但 passed 恒为 true——
        用于收紧阈值前评估影响面，避免一刀切导致全量实例立即不通过。
        """
        from backend.services.instance_gate_service import instance_gate_service
        rule = instance_gate_service.get_rule(connection_id)

        error_count = sum(1 for v in violations
                          if v.severity == Severity.ERROR or str(v.severity) == "ERROR")
        warning_count = sum(1 for v in violations
                            if v.severity == Severity.WARNING or str(v.severity) == "WARNING")

        reasons = []
        passed = True
        # -1 表示不限，沿用既有语义（>= 0 才参与判定）
        if rule["max_error_count"] >= 0 and error_count > rule["max_error_count"]:
            passed = False
            reasons.append(f"ERROR违规{error_count}个，超过上限{rule['max_error_count']}")
        if rule["max_warning_count"] >= 0 and warning_count > rule["max_warning_count"]:
            passed = False
            reasons.append(f"WARNING违规{warning_count}个，超过上限{rule['max_warning_count']}")

        observed_passed = passed
        if rule["mode"] == "observe" and not passed:
            passed = True
            reasons.append("（观察模式：仅记录，不拦截）")

        detail = "；".join(reasons) if reasons else "门禁检查通过"
        return GateResult(
            passed=passed,
            gate_rule_id=connection_id,
            error_count=error_count,
            warning_count=warning_count,
            blocked_by=[],
            detail=detail,
            observed_passed=observed_passed,   # 见 §5.3
        )
```

### 5.3 `GateResult` 增加 `observed_passed`

`backend/models/__init__.py` 中 `GateResult` 增加可选字段：

```python
    observed_passed: Optional[bool] = None   # observe 模式下的"若正式生效会否通过"
```

**为什么需要**：observe 模式下 `passed` 恒为 true，若不单独记录真实判定，管理员就无法评估"收紧后会拦掉多少"——而这正是观察模式存在的唯一目的。

### 5.4 审核链路接入

**现状**（`audit_service.py:203-210`）：

```python
    def _evaluate_gate(self, violations, project_id: str) -> Optional[GateResult]:
        """门禁评估"""
        try:
            from backend.services.gate_service import GateService
            gate_service = GateService()
            gate_rule = gate_service.get_gate_rule(project_id or "default")
            return gate_service.evaluate(violations, gate_rule)
        except Exception as e:
            logger.warning(f"门禁评估失败: {e}")
            return None
```

**修改后**：

```python
    def _evaluate_gate(self, violations, connection_id: str = "") -> Optional[GateResult]:
        """门禁评估（V1.4：按实例，不再按项目）"""
        try:
            from backend.services.gate_service import GateService
            return GateService().evaluate_for_instance(violations, connection_id)
        except Exception as e:
            logger.warning(f"门禁评估失败: {e}")
            return None
```

调用处（`audit_service.py:150` 与 `:191`）把传入的 `project_id` 改为 `connection_id`。

> **施工注意**：`audit_single_sql` / `audit_file_content` 当前签名里没有 `connection_id`，需要增加该可选参数并由 API 层传入。即时 SQL 审核若未选实例，`connection_id` 为空 → 走系统默认（0/0）。

### 5.5 门禁审计落库

`gate_service.log_gate_audit(...)` 增加 `connection_id` 与 `rule_set_id` 两个入参并写入对应新列。`project_id` 入参保留，V1.4 起传空串。

### 5.6 验证

| 场景 | 期望 |
|---|---|
| 实例未配置门禁 | 按 0/0 判定 |
| 实例配置 error=0 / warning=-1 | 仅 ERROR 拦截，WARNING 不拦 |
| observe 模式 + 超限 | `passed=true`、`observed_passed=false`、detail 含"观察模式" |
| 实例被删除 | `instance_gate_rules` 对应行随外键级联删除 |
| 传入非法上限 -2 | `save_rule` 返回错误信息，不落库 |

---

## 6. S5 可追溯性与对比校验

### 6.1 快照补记规则集

`backend/services/scan_snapshot_service.py` 的 `create_snapshot` 中，INSERT 语句与 meta 解析追加 `rule_set_id`（取自 meta，缺省 `None`）。

### 6.2 对比强制同尺度（修复既有缺陷）

**现状**：`scan_compare_service.validate_pair` 校验了数量、自比、存在性、模块、实例、指纹算法版本，**没有规则集校验**——这意味着当前就能把两个不同尺度下的快照拿来算"整改率"。

**修改后**：在指纹算法版本校验（`E4005`）之后追加：

```python
    # 7. 同评估尺度（V1.4）
    # 尺度不同则"问题数变化"不可解释：规则集变了，事实没变，只是判断变了。
    r1, r2 = s1.get("rule_set_id"), s2.get("rule_set_id")
    if r1 and r2 and r1 != r2:
        raise CompareError(
            "E4007",
            f"两次扫描的评估尺度不同（{r1} vs {r2}），问题数变化不可比，已拒绝对比",
            status=409)
```

### 6.3 存量快照的处理

`rule_set_id` 为 NULL 表示 V1.4 之前的快照，尺度未知。**不拒绝对比，改为在结果中附加警告**：

```python
    if not r1 or not r2:
        warnings.append(
            "其中一次扫描产生于 V1.4 之前，评估尺度未知，整改率仅供参考")
```

> **取舍说明**：若一律拒绝，全部存量快照立即不可对比，等于废掉 V1.3 刚交付的能力。给出警告既保住可用性，又不掩盖不确定性。

### 6.4 报告标注尺度

三类 HTML 报告（单次扫描报告、对比报告、门禁报告）页眉统一增加一行：

```
评估尺度：{规则集名称}（{rule_set_id}）
```

规则集名称取自 `rule_sets.name`；`rule_set_id` 为空时显示「V1.4 前记录，尺度未知」。

**为什么必须做**：报告是要拿去汇报的交付物，脱离尺度的问题数没有意义。让报告自解释，才能防止"拿两份不同尺度的报告并排讲成效"这种线下的比较方式。

---

## 7. S6 前端改造

### 7.1 菜单调整

| 菜单 | 变更 |
|---|---|
| 规则集 | 提升为一级入口，页面标题改为「评估规则集（全局）」 |
| 项目管理 | 保留但降级，页面顶部加提示条：「V1.4 起规则集与门禁不再由项目决定；本页仅用于业务标签与 GitLab 绑定」 |
| 质量门禁 | 改为按实例配置（见 §7.3） |

若新增菜单键，**务必同步 `_init_default_data` 的 `all_menus` 列表**（见 §2.4 施工陷阱）。

### 7.2 规则集页面

新增「当前生效」标识与「设为生效」按钮：

```html
<el-table-column label="状态" width="110">
  <template #default="{row}">
    <el-tag v-if="row.is_active" type="success" size="small">当前生效</el-tag>
    <span v-else class="t-secondary">—</span>
  </template>
</el-table-column>
<el-table-column label="操作" width="200" fixed="right">
  <template #default="{row}">
    <el-button v-if="!row.is_active && canManagePlatform" type="primary" link size="small"
               @click="activateRuleset(row)">设为生效</el-button>
    <el-button type="primary" link size="small" @click="openRulesetConfig(row)">配置规则</el-button>
    <el-button v-if="!row.is_builtin && !row.is_active" type="danger" link size="small"
               @click="deleteRuleset(row)">删除</el-button>
  </template>
</el-table-column>
```

`activateRuleset` 的确认文案必须写明影响面与时延：

```js
const activateRuleset=async(row)=>{
  try{
    await ElementPlus.ElMessageBox.confirm(
      `确认将「${row.name}」设为全局生效规则集？`
      +`系统所有审核与扫描将统一使用该尺度，最长 30 秒内全量生效。`,
      '切换生效规则集',{type:'warning'});
  }catch(e){return}
  // …调用 POST /api/v1/rulesets/{id}/activate
};
```

> **30 秒必须写进提示**：否则管理员切换后立即验证、发现未生效，会误判为故障（《ARCHITECTURE-v1.4》风险 R-2）。

### 7.3 质量门禁页面改造

由「选项目 → 配门禁」改为「实例列表 → 每行配门禁」：

| 列 | 说明 |
|---|---|
| 实例 | `tdsql_connections.name` |
| ERROR 上限 | 数字，-1 显示为「不限」 |
| WARNING 上限 | 同上 |
| 模式 | `enforce` 正式 / `observe` 观察（Tag 区分） |
| 配置来源 | 「已配置」/「系统默认」（对应 `is_default`） |
| 操作 | 编辑 |

编辑弹窗需明确提示：**上限为 0 表示"一个都不允许"**，-1 表示不限。这两个值容易被理解反。

### 7.4 移除尺度选择入口

- 即时审核页的项目下拉框：commit `6fece52` 已移除，**但当前是"沿用全局选定项目"，仍可被切换**，需改为完全不参与尺度决策；
- 文件审核 `onFileChange`（`app.js:261`）中 `if(currentProjectId.value)body.project_id=currentProjectId.value;` **删除该行**；
- 其余任何把 `project_id` 作为尺度依据传给审核接口的位置，一并移除。

---

## 8. S7 灰度与开关

### 8.1 门禁收紧的三阶段

见《ARCHITECTURE-v1.4》§4.2 与《DB-v1.4》§3.4。迁移脚本默认走**模式 A（保守）**。

### 8.2 回滚开关

无需代码开关：回滚应用版本即可，新表新列不被旧代码读取（《DB-v1.4》§7）。

---

## 9. 测试用例清单

| 编号 | 用例 | 类型 |
|---|---|---|
| T01 | 全局规则集解析：正常返回配置值 | 单元 |
| T02 | 兜底：键不存在 → default | 单元 |
| T03 | 兜底：键指向已删除规则集 → default | 单元 |
| T04 | 兜底：元数据库异常 → default，且不抛异常 | 单元 |
| T05 | 缓存：30 秒内不重复查库 | 单元 |
| T06 | 切换后本进程立即生效 | 单元 |
| T07 | 编辑规则集条目后缓存失效 | 单元 |
| T08 | **传不同 project_id 审核结果完全一致**（核心反作弊） | 集成 |
| T09 | 切换全局规则集后审核结果随之改变 | 集成 |
| T10 | 审核记录落 rule_set_id；V1.4 前记录为 NULL | 集成 |
| T11 | 实例门禁：未配置走 0/0 | 单元 |
| T12 | 实例门禁：error=0/warning=-1 时 WARNING 不拦 | 单元 |
| T13 | observe 模式 passed=true 且 observed_passed=false | 单元 |
| T14 | 非法上限 -2 被拒 | 单元 |
| T15 | 实例删除后门禁配置级联清理 | 集成 |
| T16 | 对比：两快照尺度不同 → E4007 拒绝 | 单元 |
| T17 | 对比：任一快照尺度为 NULL → 允许但带警告 | 单元 |
| T18 | 对比：两快照同尺度 → 正常 | 单元 |
| T19 | 启用中的规则集不可删除 → 409 | 集成 |
| T20 | 内置 default 规则集不可删除 | 集成 |
| T21 | 非管理员调用切换接口 → 403 | 集成 |
| T22 | 报告页眉显示评估尺度 | 集成 |
| T23 | 兼容期：传 project_id 返回 deprecated 提示且不影响结果 | 集成 |
| T24 | 全量回归无新增失败 | 回归 |

**T08 是本次的核心验收用例**：同一段 SQL，分别带 `project_id=A`、`project_id=B`、不传，三次审核的 violation 集合必须完全一致。这条通过，才说明"换项目刷低问题数"的路被堵死了。

---

## 10. 施工检查清单

- [ ] S1 迁移文件已建于 `backend/schema/v3/`，`SHOW CREATE TABLE` 校验通过
- [ ] 三处增列到位且为 NULL 默认
- [ ] `_init_default_data` 已初始化 `active_rule_set_id`
- [ ] **若新增菜单键，已同步 `all_menus` 白名单**（v1.3 踩过的坑）
- [ ] `get_active_overrides` 返回二元组，调用方均已落 `rule_set_id`
- [ ] `get_overrides_for_project` 已改为返回全局结果（不是继续按项目解析）
- [ ] 规则集条目保存后调用了 `invalidate_active_cache`
- [ ] 审核链路中不再有任何"按 project_id 决定尺度"的分支
- [ ] 门禁判定已改为按实例，`cli.py` 的旧调用未被破坏
- [ ] `validate_pair` 已加 E4007 校验，NULL 走警告而非拒绝
- [ ] 前端切换规则集的确认文案写明「最长 30 秒生效」
- [ ] 前端已删除 `app.js:261` 的 `project_id` 传参
- [ ] 门禁配置弹窗已说明 0 与 -1 的语义差别
- [ ] T08 核心用例通过
- [ ] 全量回归无新增失败
- [ ] 存量门禁迁移模式已由负责人确认（A/B/C 三选一）
