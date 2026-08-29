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
- checksum 漂移（文件内容与版本记录漂移）默认失败关闭；仅当显式列入
  SCHEMA_CHECKSUM_RECONCILE 白名单且结构验收通过时，才重设基线并审计留痕。
"""
import hashlib
import logging
import os
import re

from backend.services.database import _get_connection
from backend.schema.loader import discover_schema_files

logger = logging.getLogger("tdsql.schema.migrator")

# checksum 漂移显式调和白名单（逗号分隔的版本键）：仅当 operator 人工确认后设置。
# 调和前仍先做结构验收；通过后重设基线记录并以 ERROR 级日志留痕。
_RECONCILE_ENV = "SCHEMA_CHECKSUM_RECONCILE"


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
        """从 ADD COLUMN 定义解析期望的 类型/可空性/默认值"""
        d = " ".join(str(definition).split())
        up = d.upper()
        m = re.match(r"(\w+(?:\(\d+(?:,\s*\d+)?\))?(?:\s+UNSIGNED)?)", d, re.IGNORECASE)
        expected_type = m.group(1).replace(" ", "").lower() if m else ""
        not_null = " NOT NULL" in f" {up}"
        dm = re.search(r"\bDEFAULT\s+('[^']*'|\"[^\"]*\"|[^\s,]+)", d, re.IGNORECASE)
        expected_default = None
        if dm:
            dv = dm.group(1)
            expected_default = dv.strip("'\"") if dv[:1] in ("'", '"') else dv.upper()
        return {"type": expected_type, "not_null": not_null, "default": expected_default}

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
        if exp["default"] is not None:
            actual_default = info.get("column_default")
            ad = "NULL" if actual_default is None else str(actual_default)
            ed = exp["default"]
            if ad.upper() != str(ed).upper():
                problems.append(f"默认值不符: 期望 {ed} 实际 {ad}")
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

    def _reconcile_checksum(self, cursor, conn, key: str, recorded: str, current: str,
                            statements: list):
        """checksum 漂移调和：默认失败关闭；仅在显式白名单内且结构验收通过时重设基线。"""
        allowed = {k.strip() for k in os.environ.get(_RECONCILE_ENV, "").split(",")
                   if k.strip()}
        if key not in allowed:
            raise MigrationError(
                f"迁移版本记录与文件内容漂移 [{key}]：已登记 checksum={recorded}，"
                f"当前文件 checksum={current}。启动不得宣称 schema ready。"
                f"请人工确认漂移合法后，设置环境变量 {_RECONCILE_ENV}={key} 重启调和，"
                f"或回滚文件变更。")
        # 显式调和：先做结构验收（声明列必须全部存在且结构相符），再重设基线
        state = self._structure_state(cursor, key, statements)
        if state != "valid":
            raise MigrationError(
                f"迁移调和被拒绝 [{key}]：结构验收未通过（状态={state}），"
                f"漂移文件与库结构不一致，禁止重设基线。")
        cursor.execute(
            "UPDATE schema_migrations SET checksum = %s WHERE version_key = %s",
            (current, key))
        conn.commit()
        logger.error(
            "迁移校验和漂移已显式调和重设基线 [%s]（结构验收通过；%s=%s 已审计）。",
            key, _RECONCILE_ENV, key)

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
                        # v1.6.2.2-UAT-O-26：checksum 漂移不再只告警，默认失败关闭
                        self._reconcile_checksum(cursor, conn, key, applied[key],
                                                 checksum, statements)
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
