# -*- coding: utf-8 -*-
"""v1.6.4.0 / CP-1：Copilot B组（助手模块组）schema 管理（CP-W02，DETAIL §10.4 方案乙）。

职责：
  · 专属发现：只扫描 backend/copilot_schema/vN/ 目录，生成 copilot_vN_NNN_name 键，
    与核心 loader 完全隔离（核心 run_migrations 不加载本组）；
  · 正式维护入口：python -m backend.services.copilot.schema --apply
    —— 由安装/升级/人工维护调用，独立于 Web 启动，无模型调用；
  · 完整结构验收：复用 backend/schema/contracts.py 原语（表/引擎/排序规则/列全集/
    类型/可空/默认/主键/索引名/列序/前缀长度），不把 CREATE 内列当成只验表存在；
  · 故障传播：B组结构状态写入 A组 runtime 行（module_schema_state/epoch/…），
    READY→UNAVAILABLE 需 epoch+1；READY 要求结构 hash 一致且对账代次一致；
  · 退出码合同（§16.3）：0=READY；20=核心/A可用但 B组结构/对账失败；
    1=核心/共享DB/未知错误。

共享既有 schema_migrations 台账，键前缀 copilot_ 明确区分；绝不把 B 文件放入
backend/schema/vN。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("tdsql.copilot.schema")

COPILOT_SCHEMA_DIR = Path(__file__).resolve().parent.parent.parent / "copilot_schema"

#: B组九表闭集（DDL 白名单：本组迁移只允许这些表名）
BUSINESS_TABLES = (
    "copilot_providers", "copilot_scene_routes", "copilot_instance_grants",
    "copilot_sessions", "copilot_previews", "copilot_turns",
    "copilot_daily_budgets", "copilot_provider_attempts", "copilot_audit_events",
)

#: 台账键前缀（与核心 vN_ 区分）
KEY_PREFIX = "copilot_"

#: 命名锁（§10.4）
RUNNER_LOCK = "tdsql_copilot_runner"
SCHEMA_LOCK = "tdsql_copilot_schema"

MODULE_STATE_READY = "READY"
MODULE_STATE_UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True)
class CopilotSchemaFile:
    version_key: str
    checksum: str
    path: Path
    sql: str


def discover_business_files() -> list[CopilotSchemaFile]:
    """发现 B 组版本文件：backend/copilot_schema/vN/NNN_*.sql → copilot_vN_NNN_name。"""
    import re
    files: list[CopilotSchemaFile] = []
    if not COPILOT_SCHEMA_DIR.exists():
        return files
    v_re = re.compile(r"^v(\d+)$")
    f_re = re.compile(r"^(\d{3})_(.+)\.sql$")
    for vdir in sorted(COPILOT_SCHEMA_DIR.iterdir()):
        vm = v_re.match(vdir.name)
        if not vm or not vdir.is_dir():
            continue
        for sf in sorted(vdir.iterdir()):
            fm = f_re.match(sf.name)
            if not fm:
                continue
            sql = sf.read_text(encoding="utf-8")
            key = f"{KEY_PREFIX}v{int(vm.group(1))}_{int(fm.group(1)):03d}_{fm.group(2)}"
            files.append(CopilotSchemaFile(
                version_key=key,
                checksum=hashlib.sha256(sql.encode("utf-8")).hexdigest(),
                path=sf, sql=sql))
    files.sort(key=lambda f: f.version_key)
    return files


def module_schema_revision() -> str:
    """本发布 B组迁移文件集合 + 表清单的结构合同 hash（跨进程一致性依据）。"""
    h = hashlib.sha256()
    h.update("|".join(BUSINESS_TABLES).encode())
    for f in discover_business_files():
        h.update(f.version_key.encode())
        h.update(f.checksum.encode())
    return h.hexdigest()


def _split_statements(sql: str) -> list[str]:
    """与 migrator 同规则的简单分号切分（B组 DDL 不含分号字符串字面量）。"""
    clean_lines = []
    for line in sql.splitlines():
        if not line.strip().startswith("--"):
            clean_lines.append(line)
    return [s.strip() for s in "\n".join(clean_lines).split(";") if s.strip()]


def _assert_table_whitelist(statements: list[str]):
    """B组 DDL 只允许闭集九表（§10.1：不增加对既有业务表的写接点）。"""
    import re
    ct_re = re.compile(r"^\s*CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?`?(\w+)`?",
                       re.IGNORECASE)
    allowed = set(BUSINESS_TABLES)
    for stmt in statements:
        m = ct_re.match(stmt)
        if m:
            if m.group(1) not in allowed:
                raise RuntimeError(
                    f"B组迁移包含白名单外表: {m.group(1)}（只允许 {sorted(allowed)}）")
            continue
        # CREATE 以外的任何语句形态一律拒绝（首期 B 组只交付建表）
        raise RuntimeError(f"B组迁移仅允许 CREATE TABLE，拒绝语句: {stmt[:60]}")


def verify_business_schema(conn) -> list[str]:
    """B组完整结构只读验收：返回违反项清单（空=READY 结构面）。"""
    from backend.schema.contracts import verify_statements_contracts
    problems: list[str] = []
    files = discover_business_files()
    if not files:
        return ["B组迁移文件缺失（copilot_schema 目录为空）"]
    keys = [f.version_key for f in files]
    if len(set(keys)) != len(keys):
        problems.append("B组迁移 version_key 重复")
    cursor = conn.cursor()
    # 台账核对：每个文件必须已登记且 checksum 一致
    cursor.execute(
        "SELECT version_key, checksum FROM schema_migrations WHERE version_key LIKE %s",
        (f"{KEY_PREFIX}%",))
    applied = {}
    for r in cursor.fetchall():
        r = dict(r)
        applied[r.get("version_key") or r.get("VERSION_KEY")] = \
            r.get("checksum") or r.get("CHECKSUM")
    for f in files:
        if f.version_key not in applied:
            problems.append(f"台账未登记: {f.version_key}")
        elif applied[f.version_key] != f.checksum:
            problems.append(f"台账校验和漂移: {f.version_key}")
    # 完整结构验收
    for f in files:
        statements = _split_statements(f.sql)
        problems.extend(verify_statements_contracts(cursor, statements, label=f.version_key))
    return problems


# ══════════════════════════════════════════════════════════════════
# A组 runtime 行的模块状态读写（锁序：仅持 runtime 短事务）
# ══════════════════════════════════════════════════════════════════

def read_module_state(conn) -> dict:
    """读取 A组 runtime 的模块结构状态。A组不可读属核心故障，直接抛异常。"""
    cursor = conn.cursor()
    cursor.execute(
        "SELECT module_schema_state, module_schema_revision, module_schema_epoch, "
        "module_reconciled_epoch, module_schema_checked_at, accepting, settings_json "
        "FROM copilot_runtime WHERE id = 1")
    row = cursor.fetchone()
    if not row:
        raise RuntimeError("copilot_runtime 控制行缺失（A组迁移未正确执行）")
    r = dict(row)
    return {
        "module_schema_state": r.get("module_schema_state") or MODULE_STATE_UNAVAILABLE,
        "module_schema_revision": r.get("module_schema_revision"),
        "module_schema_epoch": int(r.get("module_schema_epoch") or 1),
        "module_reconciled_epoch": int(r.get("module_reconciled_epoch") or 0),
        "module_schema_checked_at": r.get("module_schema_checked_at"),
        "accepting": int(r.get("accepting") or 0),
        "settings_json": r.get("settings_json") or "{}",
    }


def mark_unavailable(conn, reason: str = "") -> int:
    """READY→UNAVAILABLE 并在状态翻转时 epoch+1（短事务；持续故障不反复增代次）。"""
    cursor = conn.cursor()
    cursor.execute("SELECT module_schema_state, module_schema_epoch "
                   "FROM copilot_runtime WHERE id = 1 FOR UPDATE")
    row = cursor.fetchone()
    if not row:
        raise RuntimeError("copilot_runtime 控制行缺失")
    r = dict(row)
    state = r.get("module_schema_state") or MODULE_STATE_UNAVAILABLE
    epoch = int(r.get("module_schema_epoch") or 1)
    if state == MODULE_STATE_READY:
        epoch += 1
    cursor.execute(
        "UPDATE copilot_runtime SET module_schema_state = %s, module_schema_epoch = %s, "
        "accepting = 0, updated_at = UTC_TIMESTAMP(6) WHERE id = 1",
        (MODULE_STATE_UNAVAILABLE, epoch))
    conn.commit()
    logger.error("Copilot B组模块置为 UNAVAILABLE（epoch=%s）: %s", epoch, reason)
    return epoch


def mark_ready(conn, revision: str) -> None:
    """对账完成后 READY：reconciled_epoch=epoch、记录本版 hash（CAS 语义由调用方保证）。"""
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE copilot_runtime SET module_schema_state = %s, module_schema_revision = %s, "
        "module_reconciled_epoch = module_schema_epoch, "
        "module_schema_checked_at = UTC_TIMESTAMP(6), updated_at = UTC_TIMESTAMP(6) "
        "WHERE id = 1 AND module_schema_state = %s",
        (MODULE_STATE_READY, revision, MODULE_STATE_UNAVAILABLE))
    if cursor.rowcount != 1:
        raise RuntimeError("module_schema_state CAS 置 READY 失败（状态已被并发修改）")
    conn.commit()


def evaluate_ready(conn) -> tuple[bool, str, dict]:
    """评估 B组当前是否可用：(ready, reason_code, state)。

    READY 同时要求：A组状态 READY + 本版结构 hash 一致 + reconciled_epoch=epoch。
    A组不可读写属核心故障，异常直接上抛（失败关闭，不包装成模块降级）。
    """
    st = read_module_state(conn)
    if st["module_schema_state"] != MODULE_STATE_READY:
        return False, "COPILOT_SCHEMA_UNAVAILABLE", st
    if st["module_schema_revision"] != module_schema_revision():
        return False, "COPILOT_SCHEMA_UNAVAILABLE", st
    if st["module_reconciled_epoch"] != st["module_schema_epoch"]:
        return False, "COPILOT_SCHEMA_UNAVAILABLE", st
    return True, "", st


# ══════════════════════════════════════════════════════════════════
# 正式维护入口（--apply）
# ══════════════════════════════════════════════════════════════════

def apply_business_schema() -> int:
    """B组正式应用：命名锁→停用受理→逐文件应用→完整验收→（恢复）对账→READY。

    退出码：0=READY；20=核心/A可用但 B组结构/对账失败；1=核心/共享DB/未知错误。
    """
    from backend.services.database import _get_connection, ensure_db

    # 核心/A组必须先可用（ensure_db 失败关闭，A组异常上抛 → 退出码 1）
    ensure_db()

    conn = _get_connection()
    try:
        cursor = conn.cursor()
        # 1. 命名锁：先 runner 锁后 schema 锁（固定顺序）
        for lock_name in (RUNNER_LOCK, SCHEMA_LOCK):
            cursor.execute("SELECT GET_LOCK(%s, 60) AS got", (lock_name,))
            row = dict(cursor.fetchone() or {})
            if int(row.get("got") or 0) != 1:
                print(json.dumps({"status": "ERROR",
                                  "reason": f"命名锁获取超时: {lock_name}"},
                                 ensure_ascii=False))
                return 20
        try:
            # 2. DDL 前短事务置 UNAVAILABLE（不持 runtime 行锁跑 DDL）
            try:
                mark_unavailable(conn, "schema apply start")
            except Exception as e:
                logger.error("核心 runtime 状态写入失败: %s", e, exc_info=True)
                return 1

            files = discover_business_files()
            if not files:
                print(json.dumps({"status": "ERROR",
                                  "reason": "B组迁移文件缺失"}, ensure_ascii=False))
                return 20

            # 3. 逐文件应用（幂等 CREATE IF NOT EXISTS）+ 台账登记
            applied_ok = True
            for f in files:
                statements = _split_statements(f.sql)
                try:
                    _assert_table_whitelist(statements)
                except Exception as e:
                    print(json.dumps({"status": "ERROR", "reason": str(e)},
                                     ensure_ascii=False))
                    return 20
                cursor.execute(
                    "SELECT checksum FROM schema_migrations WHERE version_key = %s",
                    (f.version_key,))
                row = cursor.fetchone()
                recorded = None
                if row:
                    recorded = dict(row).get("checksum") or dict(row).get("CHECKSUM")
                if recorded and recorded != f.checksum:
                    print(json.dumps({
                        "status": "ERROR",
                        "reason": f"台账校验和漂移: {f.version_key}（拒绝自动调和）"},
                        ensure_ascii=False))
                    return 20
                for stmt in statements:
                    try:
                        cursor.execute(stmt)
                    except Exception as e:
                        logger.error("B组 DDL 失败 [%s]: %s", f.version_key, e)
                        applied_ok = False
                        break
                if not applied_ok:
                    break
                if recorded is None:
                    # 台账登记（并发同 key 异 checksum 绝不当幂等）
                    try:
                        cursor.execute(
                            "INSERT INTO schema_migrations (version_key, checksum) "
                            "VALUES (%s, %s)", (f.version_key, f.checksum))
                        conn.commit()
                    except Exception as e:
                        cursor.execute(
                            "SELECT checksum FROM schema_migrations "
                            "WHERE version_key = %s", (f.version_key,))
                        row2 = cursor.fetchone()
                        existing = dict(row2).get("checksum") if row2 else None
                        if existing == f.checksum:
                            conn.commit()
                        else:
                            logger.error("B组台账登记冲突 [%s]: %s", f.version_key, e)
                            applied_ok = False
                            break
            if not applied_ok:
                return 20

            # 4. 完整结构验收
            problems = verify_business_schema(conn)
            if problems:
                print(json.dumps({"status": "ERROR",
                                  "reason": "结构验收失败",
                                  "problems": problems[:20]}, ensure_ascii=False))
                return 20

            # 5. 恢复对账（同一 runner 命名锁下；含空库对账）
            from backend.services.copilot import recovery
            rec = recovery.reconcile_after_recovery(conn)
            if not rec.get("ok"):
                print(json.dumps({"status": "ERROR", "reason": "恢复对账失败",
                                  "detail": rec}, ensure_ascii=False))
                return 20

            # 6. READY
            mark_ready(conn, module_schema_revision())
            print(json.dumps({"status": "READY",
                              "module_schema_revision": module_schema_revision(),
                              "reconcile": rec}, ensure_ascii=False))
            return 0
        finally:
            for lock_name in (SCHEMA_LOCK, RUNNER_LOCK):
                try:
                    cursor.execute("SELECT RELEASE_LOCK(%s)", (lock_name,))
                except Exception:
                    pass
            conn.commit()
    except SystemExit:
        raise
    except Exception as e:
        logger.error("B组维护入口异常: %s", e, exc_info=True)
        print(json.dumps({"status": "ERROR", "reason": f"核心/共享错误: {type(e).__name__}"},
                         ensure_ascii=False))
        return 1
    finally:
        conn.close()


def status() -> int:
    """只读状态输出（不写、不修复）；退出码同 --apply 语义。"""
    from backend.services.database import _get_connection, ensure_db
    try:
        ensure_db()
    except Exception as e:
        print(json.dumps({"status": "ERROR", "reason": f"核心/A组不可用: {type(e).__name__}"},
                         ensure_ascii=False))
        return 1
    conn = _get_connection()
    try:
        st = read_module_state(conn)
        ready, reason, _ = evaluate_ready(conn)
        problems = [] if ready else verify_business_schema(conn)
        out = {"status": "READY" if ready else "UNAVAILABLE",
               "reason_code": reason,
               "module_schema_state": st["module_schema_state"],
               "module_schema_epoch": st["module_schema_epoch"],
               "module_reconciled_epoch": st["module_reconciled_epoch"],
               "module_schema_revision": st["module_schema_revision"],
               "expected_revision": module_schema_revision(),
               "structure_problems": problems[:20]}
        print(json.dumps(out, ensure_ascii=False))
        return 0 if ready else 20
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m backend.services.copilot.schema")
    parser.add_argument("--apply", action="store_true", help="应用 B组迁移并验收对账")
    parser.add_argument("--status", action="store_true", help="只读输出模块状态")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if args.apply:
        return apply_business_schema()
    if args.status:
        return status()
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
