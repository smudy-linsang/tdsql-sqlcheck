"""
Schema 自动迁移引擎：计算 SHA256 Checksum 并进行增量 DDL 迁移

v1.6.2.2-UAT-O-23：迁移必须失败关闭——
- 任一 DDL 语句失败（锁超时/权限/空间等）立即中止，绝不写版本键；
  上一次被错误标记 applied 的文件（结构验收不通过）会在下次启动自动补齐。
- 幂等不靠吞 Duplicate column：ADD COLUMN 前先查 information_schema.columns，
  列已存在则严格校验类型/可空性/默认值，不符即失败关闭。
- 两阶段：语句执行 + 最终结构验收，全部通过才写 schema_migrations；
  MySQL/TDSQL DDL 隐式提交，采用“逐项可恢复 + 最终结构验收”。
- 双 worker 并发启动：版本键唯一约束冲突视为幂等，但前提是记录确实存在。

v1.6.2.2-UAT-O-26：已登记迁移的启动路径同样失败关闭——
- 缺列（missing）→ 幂等补齐；结构与文件声明不符（mismatch）→ 启动失败关闭；
- 版本键与 checksum 一致时也必须逐列校验类型/可空/默认值，不得只验“列存在”；
- checksum 漂移（文件内容与版本记录漂移）默认失败关闭；仅对代码内精确三元组
  账本（version_key + 历史 checksum + 当前 checksum）中的已知历史变更自动一次性调和
  并审计留痕（v1.6.2.2-UAT-O-30，长期环境变量开关已移除）。
"""
import hashlib
import logging
import re

from backend.services.database import _get_connection
from backend.schema.loader import discover_schema_files

logger = logging.getLogger("tdsql.schema.migrator")

# v1.6.2.2-UAT-O-30：代码内精确调和账本（一次性闭环）。
# 键必须精确匹配 {version_key, 历史 checksum, 当前 checksum} 三元组；
# 未知组合、其他版本、任意未来漂移一律失败关闭——不存在可调用的长期开关。
_KNOWN_RECONCILIATIONS = {
    "v9_090_connection_unique": {
        "historical_checksum": "54ee2e97c804f5d8ec216d9f51600c19cc8463f2cede1de07fa67635abe6de28",
        "current_checksum": "c6cf33bb385456fef12af3d4888ea6b22dcfc2a64052d734adc4c37457915209",
        "reason": "v1.6.0.4 将 090 迁移改为 no-op（提交 08ce65c）：端点唯一约束改由 Python 层执行",
    },
}


class MigrationError(RuntimeError):
    """迁移失败（失败关闭：绝不写版本键，启动中止）"""


_ADD_COLUMN_RE = re.compile(
    r"^\s*ALTER\s+TABLE\s+`?(\w+)`?\s+ADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?`?(\w+)`?\s+(.+?)\s*$",
    re.IGNORECASE | re.DOTALL,
)


class SchemaMigrator:
    def ensure_migration_table(self, conn):
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version_key VARCHAR(128) PRIMARY KEY,
                checksum VARCHAR(64) NOT NULL,
                applied_at DATETIME DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        conn.commit()

    # ── 结构验收辅助 ─────────────────────────────────────

    @staticmethod
    def _column_info(cursor, table: str, column: str):
        cursor.execute(
            "SELECT COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s",
            (table, column))
        row = cursor.fetchone()
        if not row:
            return None
        if isinstance(row, dict):
            return {k.lower(): v for k, v in row.items()}
        return {"column_type": row[0], "is_nullable": row[1], "column_default": row[2]}

    @staticmethod
    def _expected_column_spec(definition: str) -> dict:
        """从 ADD COLUMN 定义解析期望的 类型/可空性/默认值（v1.6.2.2-UAT-O-29 三态化）。

        必须区分“DDL 未声明 DEFAULT”（has_default=False）与“显式 DEFAULT NULL”
        （has_default=True 且 default=None），不能用单个 None 承担两种语义——
        否则未声明 DEFAULT 的迁移完全不校验现存列默认值（O-29 复现的漏检）。
        """
        d = " ".join(str(definition).split())
        up = d.upper()
        m = re.match(r"(\w+(?:\(\d+(?:,\s*\d+)?\))?(?:\s+UNSIGNED)?)", d, re.IGNORECASE)
        expected_type = m.group(1).replace(" ", "").lower() if m else ""
        not_null = " NOT NULL" in f" {up}"
        dm = re.search(r"\bDEFAULT\s+('[^']*'|\"[^\"]*\"|[^\s,]+)", d, re.IGNORECASE)
        has_default = dm is not None
        expected_default = None
        if has_default:
            dv = dm.group(1)
            expected_default = dv.strip("'\"") if dv[:1] in ("'", '"') else dv.upper()
        return {"type": expected_type, "not_null": not_null,
                "has_default": has_default, "default": expected_default}

    @staticmethod
    def _normalize_default(value):
        """把 information_schema.COLUMNS.COLUMN_DEFAULT 归一化为可比较文本。
        MySQL/TDSQL 对关键字默认值（CURRENT_TIMESTAMP/TRUE/FALSE/NULL）大小写不定，
        布尔在整型列上可能物化为 1/0——关键词归一比较、引号字符串精确比较。"""
        if value is None:
            return None
        v = str(value).strip()
        vu = v.upper().rstrip("()")
        if vu in ("CURRENT_TIMESTAMP", "CURRENT_TIMESTAMP()", "NOW"):
            return "CURRENT_TIMESTAMP"
        if vu in ("TRUE", "1"):
            return "TRUE"
        if vu in ("FALSE", "0"):
            return "FALSE"
        if vu == "NULL":
            return None
        return v

    def _verify_column(self, cursor, key: str, table: str, column: str, definition: str):
        """严格校验既有列的类型/可空性/默认值；不符即失败关闭。"""
        info = self._column_info(cursor, table, column)
        if info is None:
            raise MigrationError(f"迁移结构验收失败 [{key}]: 列 {table}.{column} 不存在")
        exp = self._expected_column_spec(definition)
        problems = []
        actual_type = str(info.get("column_type") or "").replace(" ", "").lower()
        if exp["type"] and actual_type != exp["type"]:
            problems.append(f"类型不符: 期望 {exp['type']} 实际 {actual_type}")
        actual_not_null = str(info.get("is_nullable") or "").upper() == "NO"
        if actual_not_null != exp["not_null"]:
            problems.append(f"可空性不符: 期望 {'NOT NULL' if exp['not_null'] else 'NULL'}")
        # v1.6.2.2-UAT-O-29：默认值永远参与校验——未声明 DEFAULT 时按目标库规范化
        # 结果（I_S 中为 NULL）验收；显式 DEFAULT NULL 同样期望 NULL；
        # 有值时按关键字归一/字符串精确比较。
        actual_default = self._normalize_default(info.get("column_default"))
        if not exp["has_default"] or exp["default"] is None:
            if actual_default is not None:
                problems.append(
                    f"默认值不符: 期望 {'NULL' if exp['has_default'] else '无声明(规范化为 NULL)'} "
                    f"实际 {info.get('column_default')!r}")
        else:
            expected_default = self._normalize_default(exp["default"])
            if actual_default != expected_default:
                problems.append(
                    f"默认值不符: 期望 {expected_default!r} 实际 {info.get('column_default')!r}")
        if problems:
            raise MigrationError(
                f"迁移结构验收失败 [{key}]: 列 {table}.{column} " + "；".join(problems))

    # ── 单文件应用（失败关闭） ───────────────────────────

    def _split_statements(self, sql: str) -> list:
        clean_lines = []
        for line in sql.splitlines():
            if not line.strip().startswith("--"):
                clean_lines.append(line)
        return [s.strip() for s in "\n".join(clean_lines).split(";") if s.strip()]

    def _apply_file(self, cursor, conn, key: str, checksum: str, statements: list):
        declared_cols = []
        for stmt in statements:
            m = _ADD_COLUMN_RE.match(stmt)
            if m:
                table, column, definition = m.group(1), m.group(2), m.group(3)
                declared_cols.append((table, column, definition))
                if self._column_info(cursor, table, column) is not None:
                    # 幂等不靠吞 Duplicate column：存在即严格验收，通过则跳过
                    self._verify_column(cursor, key, table, column, definition)
                    logger.info("迁移幂等跳过（列已存在且结构相符）[%s]: %s.%s", key, table, column)
                    continue
            try:
                cursor.execute(stmt)
            except Exception as e:
                errno = getattr(e, "args", [None])[0]
                # 双 worker 并发启动的 DDL 竞态：另一进程已抢先 ADD COLUMN 成功时
                # 本进程收到 Duplicate column(1060)。这不是失败关闭场景——
                # 但必须严格复核对方写入的列结构与本迁移设计一致，否则仍是假成功。
                if errno == 1060 and m:
                    table, column, definition = m.group(1), m.group(2), m.group(3)
                    self._verify_column(cursor, key, table, column, definition)
                    logger.info("迁移并发竞态（列已由另一进程创建且结构相符）[%s]: %s.%s",
                                key, table, column)
                    continue
                raise MigrationError(
                    f"迁移语句执行失败 [{key}] errno={errno}: {e} | "
                    f"stmt={stmt[:120]}") from e
        # 最终结构验收：声明列全部存在且结构相符
        for table, column, definition in declared_cols:
            self._verify_column(cursor, key, table, column, definition)
        conn.commit()
        try:
            cursor.execute(
                "INSERT INTO schema_migrations (version_key, checksum) VALUES (%s, %s)",
                (key, checksum))
            conn.commit()
        except Exception as e:
            # 双 worker 并发启动的版本键竞争：仅当记录确实存在时视为幂等冲突
            cursor.execute("SELECT checksum FROM schema_migrations WHERE version_key = %s", (key,))
            row = cursor.fetchone()
            if row:
                conn.commit()
                logger.info("迁移版本键并发冲突（另一进程已登记，记录存在）: %s", key)
                return
            raise MigrationError(f"迁移版本键写入失败 [{key}]: {e}") from e

    def _structure_state(self, cursor, key: str, statements: list) -> str:
        """结构状态机（v1.6.2.2-UAT-O-26）：

        - 任一声明列缺失 → 'missing'（可进入幂等补齐）；
        - 声明列存在但类型/可空/默认值不符 → 抛 MigrationError（mismatch，失败关闭）；
        - 全部声明列存在且结构相符 → 'valid'（允许跳过）。
        """
        missing = False
        for stmt in statements:
            m = _ADD_COLUMN_RE.match(stmt)
            if not m:
                continue
            table, column, definition = m.group(1), m.group(2), m.group(3)
            info = self._column_info(cursor, table, column)
            if info is None:
                missing = True
                continue
            # 已登记路径同样严格验收：不只验“列存在”
            self._verify_column(cursor, key, table, column, definition)
        return "missing" if missing else "valid"

    def _needs_reapply(self, cursor, statements: list) -> bool:
        """已登记文件的结构验收：声明列缺失即视为假 applied，需要补齐（自愈）。
        保留为向后兼容包装；结构不符会抛 MigrationError（失败关闭）。"""
        return self._structure_state(cursor, "(reapply-check)", statements) == "missing"

    def _verify_090_invariants(self, cursor) -> list:
        """v9_090 调和前的业务结构不变量（O-30）：返回违反项清单（空=通过）。

        - 端点唯一约束 uq_conn_endpoint 已存在；
        - 名称唯一约束 uq_conn_name 已由后续迁移移除；
        - tdsql_connections 无重复端点（host,port,database）。
        """
        problems = []

        def _index_count(name: str) -> int:
            cursor.execute(
                "SELECT COUNT(*) AS c FROM information_schema.STATISTICS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'tdsql_connections' "
                "AND INDEX_NAME = %s", (name,))
            row = cursor.fetchone()
            row = dict(row) if not isinstance(row, dict) else row
            return int(row.get("c", row.get("C", 0)) or 0)

        if _index_count("uq_conn_endpoint") == 0:
            problems.append("端点唯一约束 uq_conn_endpoint 不存在")
        if _index_count("uq_conn_name") > 0:
            problems.append("名称唯一约束 uq_conn_name 未被移除")
        cursor.execute(
            "SELECT COUNT(*) AS c FROM (SELECT host, port, `database` FROM "
            "tdsql_connections GROUP BY host, port, `database` HAVING COUNT(*) > 1) t")
        row = cursor.fetchone()
        row = dict(row) if not isinstance(row, dict) else row
        if int(row.get("c", row.get("C", 0)) or 0) > 0:
            problems.append("tdsql_connections 存在重复端点")
        return problems

    def _auto_reconcile(self, cursor, conn, key: str, recorded: str, current: str):
        """checksum 漂移的一次性自动调和（O-30）。

        只接受代码内账本的精确三元组；调和前验证业务结构不变量；
        以条件 UPDATE（version_key + 旧 checksum）原子写入——双 worker 并发
        只有一个进程能中标，另一进程重读确认记录已是新值后视为幂等通过；
        调和写操作审计并以 ERROR 级日志留痕。未知组合一律失败关闭。
        """
        entry = _KNOWN_RECONCILIATIONS.get(key)
        if not entry or entry["historical_checksum"] != recorded \
                or entry["current_checksum"] != current:
            raise MigrationError(
                f"迁移版本记录与文件内容漂移 [{key}]：已登记 checksum={recorded}，"
                f"当前文件 checksum={current}，且不在已知调和账本中，启动失败关闭。"
                f"该漂移不属于已知历史变更，请人工核实文件是否被篡改。")
        problems = self._verify_090_invariants(cursor)
        if problems:
            raise MigrationError(
                f"迁移调和前的业务结构不变量不满足 [{key}]: " + "；".join(problems))
        # 原子调和：只有“记录仍为旧值”时本进程才写入，天然并发安全
        cur = cursor.execute(
            "UPDATE schema_migrations SET checksum = %s "
            "WHERE version_key = %s AND checksum = %s",
            (current, key, recorded))
        conn.commit()
        if getattr(cur, "rowcount", 0) != 1:
            cursor.execute(
                "SELECT checksum FROM schema_migrations WHERE version_key = %s",
                (key,))
            row = cursor.fetchone()
            row = dict(row) if row else {}
            if row.get("checksum") == current:
                logger.info("迁移调和并发幂等：另一进程已完成 %s 的基线重设", key)
                return
            raise MigrationError(f"迁移调和原子更新失败 [{key}]")
        logger.error(
            "迁移 checksum 一次性自动调和完成 [%s]：%s…→%s…（%s）",
            key, recorded[:12], current[:12], entry["reason"])
        try:
            from backend.services.database import log_operation
            log_operation(
                operator="system", operation_type="schema_checksum_reconcile",
                target_type="schema_migrations", target_id=key,
                detail=f"{recorded}→{current}; reason={entry['reason']}")
        except Exception:
            logger.warning("调和审计日志写入失败（不影响调和结果）", exc_info=True)

    def run_migrations(self):
        conn = _get_connection()
        try:
            self.ensure_migration_table(conn)
            cursor = conn.cursor()
            cursor.execute("SELECT version_key, checksum FROM schema_migrations")
            rows = cursor.fetchall()
            applied = {}
            for r in rows:
                if isinstance(r, dict):
                    applied[r["version_key"]] = r["checksum"]
                else:
                    applied[r[0]] = r[1]

            schema_files = discover_schema_files()
            for sf in schema_files:
                key = f"v{sf.version}_{sf.sequence:03d}_{sf.name}"
                checksum = hashlib.sha256(sf.sql.encode("utf-8")).hexdigest()
                statements = self._split_statements(sf.sql)
                if key in applied:
                    if applied[key] != checksum:
                        # v1.6.2.2-UAT-O-30：checksum 漂移走代码内精确账本的一次性调和，
                        # 未知漂移一律失败关闭；不存在可调用的长期环境开关。
                        self._auto_reconcile(cursor, conn, key, applied[key], checksum)
                        continue
                    # 已登记且 checksum 一致：仍需完整结构验收（不只验列存在）
                    state = self._structure_state(cursor, key, statements)
                    if state == "valid":
                        continue
                    # missing：历史假 applied 自愈补齐（幂等流程）
                    logger.warning(
                        "迁移 %s 已登记但结构不完整（可能被历史假成功污染），按幂等流程补齐", key)
                logger.info(f"应用增量数据库 Schema 迁移: {key}")
                self._apply_file(cursor, conn, key, checksum, statements)
        except MigrationError:
            logger.error("Schema 迁移失败关闭：启动不得继续", exc_info=True)
            raise
        except Exception as e:
            logger.error("Schema 迁移失败: %s", e, exc_info=True)
            raise MigrationError(f"Schema 迁移失败: {e}") from e
        finally:
            conn.close()


migrator = SchemaMigrator()
