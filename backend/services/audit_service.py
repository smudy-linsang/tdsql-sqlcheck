"""
TDSQL SQL审核工具 - 审核服务 (V2.0)

封装审核引擎，提供业务层接口。

V2.0 变更:
- 审核历史记录操作用户（created_by）与项目ID
- 支持项目级规则集覆盖（多租户规则）
- 支持门禁评估联动
"""
import json
import logging
from datetime import datetime
from typing import Optional

from backend.engine.checker import RuleChecker
from backend.models import (
    AuditResult,
    AuditSummary,
    GateResult,
)

logger = logging.getLogger("tdsql.audit_service")

from backend.services.database import _get_connection, ensure_db


def _save_audit_history(audit_type: str, source: str, results: list[AuditResult],
                        summary: AuditSummary, created_by: str = "",
                        project_id: str = "",
                        gate_result: Optional[GateResult] = None,
                        connection_id: str = "", db_name: str = "",
                        rule_set_id: str = "",
                        instance_ctx=None, skipped_rules_count: int = 0):
    """保存审核历史到数据库

    V1.3(D1): 新增 connection_id / db_name，支撑扫描结果对比按实例筛选。
    V1.4: 新增 rule_set_id，记录本次审核实际生效的规则集（尺度可追溯）。
    V1.5: 新增 instance_type / instance_type_source / skipped_rules_count。
          instance_ctx 为 None 时三列写入 NULL/''/0，语义与 V1.5 前记录一致。
    均有默认值，既有调用方无需改动。
    """
    try:
        ensure_db()
        conn = _get_connection()
        try:
            results_json = json.dumps([{
                "sql": r.sql,
                "sql_type": r.sql_type,
                "passed": r.passed,
                "file_path": r.file_path,
                "line_number": r.line_number,
                "violations": [{
                    "rule_id": v.rule_id,
                    "severity": v.severity.value if hasattr(v.severity, 'value') else str(v.severity),
                    "message": v.message,
                    "suggestion": v.suggestion,
                    "line_number": v.line_number,
                } for v in r.violations],
            } for r in results], ensure_ascii=False)
            cursor = conn.cursor()
            # V1.5：占位符从 17 个增加到 20 个。三处 ? 与三个新值必须同步添加，
            # 漏改会静默错列（不报错但数据全错位）——本次改造最易翻车的一处。
            cursor.execute("""
                INSERT INTO audit_history (audit_type, source, total_sql, passed, failed,
                    error_count, warning_count, pass_rate, results_json,
                    created_by, project_id, gate_passed, gate_detail, created_at,
                    connection_id, db_name, rule_set_id,
                    instance_type, instance_type_source, skipped_rules_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                audit_type, source,
                summary.total_sql, summary.passed, summary.failed,
                summary.error_count, summary.warning_count, summary.pass_rate,
                results_json, created_by, project_id,
                (1 if gate_result.passed else 0) if gate_result else None,
                gate_result.detail if gate_result else "",
                datetime.now().isoformat(),
                connection_id or "", db_name or "",
                # NULL 语义为"V1.4 前历史记录，尺度未知"，故空串写 None 而非 ""
                rule_set_id or None,
                # V1.5 实例类型口径：NULL 语义同 rule_set_id（上线前记录，口径未知）
                instance_ctx.instance_type.value if instance_ctx else None,
                instance_ctx.source.value if instance_ctx else "",
                int(skipped_rules_count or 0),
            ))
            conn.commit()
            return getattr(cursor, "lastrowid", None)
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"保存审核历史失败: {e}")
        return None


class AuditService:
    """SQL审核业务服务"""

    def __init__(self):
        self.checker = RuleChecker(dialect="mysql")

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

    def _resolve_overrides(self, project_id: Optional[str] = None) -> Optional[dict]:
        """DEPRECATED(V1.4)：保留仅为兼容;尺度已全局化，project_id 被忽略"""
        return self._resolve_scale()[1]

    def _resolve_instance(self, connection_id: str = "",
                          requested: Optional[str] = None):
        """解析实例类型上下文（V1.5）。任何异常回落全局默认，绝不中断审核（INV-5）。"""
        try:
            from backend.services.instance_type_service import instance_type_service
            return instance_type_service.resolve(connection_id, requested)
        except Exception as e:
            logger.warning(f"实例类型解析异常(按分布式兜底): {e}")
            from backend.models import InstanceType, TypeSource
            from backend.services.instance_type_service import InstanceContext
            return InstanceContext(InstanceType.DISTRIBUTED, TypeSource.DEFAULT)

    def _apply_shard_key_check(self, result, sql: str, ictx,
                               table_metadata: Optional[dict] = None):
        """深度分布式检查（V1.5 重写）。

        两处修复（必须同时做，缺一不可）：
          1) 仅分布式实例执行 —— 集中式无分片概念，这些结论没有意义；
          2) 分片键取自真实表元数据，取不到就整段跳过 ——
             原实现硬编码 ["order_id","user_id"]，等于拿虚构的分片键
             审核真实 SQL，即使在分布式实例上结论也几乎总是错的。
        """
        from backend.models import InstanceType
        if ictx.instance_type != InstanceType.DISTRIBUTED:
            return

        shard_keys = []
        for meta in (table_metadata or {}).values():
            sk = (meta or {}).get("shard_key") or ""
            if sk:
                shard_keys.extend([k.strip() for k in sk.split(",") if k.strip()])
        if not shard_keys:
            # 拿不到真实分片键就不猜。宁可不报，也不拿虚构字段名产出错误结论。
            logger.debug("无真实分片键元数据，跳过深度分布式检查")
            return

        try:
            from backend.engine.parser.tdsql_auditor import TDSQLAuditor
            from backend.engine.parser.ast_parser import ASTParser
            from backend.models import Violation, Severity
            expr = ASTParser().parse(sql)
            for f in TDSQLAuditor().check_shard_key_presence(expr, shard_keys):
                sev = Severity.ERROR if f.severity == "ERROR" else Severity.WARNING
                result.violations.append(Violation(
                    rule_id=f.rule_id, severity=sev,
                    message=f.message, suggestion=f.suggestion))
                if f.severity == "ERROR":
                    result.passed = False
        except Exception as e:
            logger.debug(f"TDSQL 深度分布式规则检查跳过: {e}")

    def audit_single_sql(self, sql: str, created_by: str = "",
                         project_id: str = "",
                         evaluate_gate: bool = False,
                         connection_id: str = "",
                         instance_type: Optional[str] = None,
                         table_metadata: Optional[dict] = None
                         ) -> tuple[AuditResult, Optional[GateResult], "InstanceContext"]:
        """
        审核单条 SQL。

        V1.4：尺度取自全局生效规则集（project_id 不再决定尺度，仅兼容保留）；
        门禁按 connection_id 绑定的实例判定。
        V1.5.1：实例类型由解析器多源分级得出（A类按 锁定>探测>ZK>声明 保守合并，
        引擎按实例类型过滤适用域。

        Returns:
            (审核结果, 门禁结果或None, 实例类型上下文)
        """
        # R5-01（GATE-2）：审核入口用 tokenizer-aware 切分，例程 BEGIN...END 体内
        # 分号不拆，避免合法 CREATE PROCEDURE/FUNCTION 被拆成 BATCH 逐片误报。
        # R7-02：统一改用 DELIMITER-aware 的 split_audit_script（四入口一致），
        # 客户端 DELIMITER 指令与尾分隔符不再进入结果。
        from backend.engine.parser import split_audit_script
        statements = [s.strip() for s, _ln, _end in split_audit_script(sql) if s.strip()]

        rule_set_id, overrides = self._resolve_scale()
        ictx = self._resolve_instance(connection_id, instance_type)
        it = ictx.instance_type.value

        if len(statements) == 1:
            # R7-02（O 第八轮 §6/§7）：单段也必须审核 split_audit_script 清洗后的语句，
            # 而非原始 sql——否则标准 DELIMITER 脚本（$$...END$$）的客户端指令与尾分隔符
            # 会被送入 parser 触发 E999。result.sql 即实际被审核的清洗语句（不含 DELIMITER/$$）。
            audit_sql = statements[0]
            result = self.checker.audit_sql(audit_sql, rule_overrides=overrides,
                                            instance_type=it)
            # V1.5：深度分布式检查（仅分布式实例 + 真实分片键元数据）
            self._apply_shard_key_check(result, audit_sql, ictx, table_metadata=table_metadata)
        elif len(statements) == 0:
            # 空输入/纯 DELIMITER 指令/纯注释：保留既有失败关闭口径，审核原文，不扩展定义。
            result = self.checker.audit_sql(sql, rule_overrides=overrides,
                                            instance_type=it)
            self._apply_shard_key_check(result, sql, ictx, table_metadata=table_metadata)
        else:
            results = []
            all_violations = []
            for idx, stmt in enumerate(statements, 1):
                res = self.checker.audit_sql(stmt, rule_overrides=overrides,
                                             instance_type=it)
                for v in res.violations:
                    v.message = f"[第{idx}条语句] {v.message}"
                    all_violations.append(v)
                results.append(res)

            sql_types = {res.sql_type for res in results if res.sql_type}
            combined_type = "BATCH" if len(sql_types) > 1 else (list(sql_types)[0] if sql_types else "BATCH")

            result = AuditResult(
                sql=sql,
                sql_type=combined_type,
                passed=len(all_violations) == 0,
                violations=all_violations,
            )

        gate_result = None
        if evaluate_gate:
            gate_result = self._evaluate_gate(result.violations, connection_id)

        summary = self.checker.compute_summary([result])
        _save_audit_history("sql", "api", [result], summary,
                            created_by=created_by, project_id=project_id,
                            gate_result=gate_result, connection_id=connection_id,
                            rule_set_id=rule_set_id,
                            instance_ctx=ictx,
                            skipped_rules_count=self.checker.count_skipped_by_scope(it))
        try:
            from backend.services import metrics_service
            metrics_service.inc("tdsql_audit_sql_total")
            for v in result.violations:
                sev = v.severity.value if hasattr(v.severity, 'value') else str(v.severity)
                metrics_service.inc("tdsql_violations_total", {"severity": sev})
        except Exception:
            pass
        return result, gate_result, ictx

    def audit_sql_list(self, sql_list: list[str], created_by: str = "",
                       project_id: str = "",
                       instance_type: Optional[str] = None) -> list[AuditResult]:
        """审核多条 SQL"""
        rule_set_id, overrides = self._resolve_scale()
        ictx = self._resolve_instance("", instance_type)
        it = ictx.instance_type.value
        results = [self.checker.audit_sql(sql, rule_overrides=overrides, instance_type=it)
                   for sql in sql_list]
        summary = self.checker.compute_summary(results)
        _save_audit_history("sql_batch", "api", results, summary,
                            created_by=created_by, project_id=project_id,
                            rule_set_id=rule_set_id,
                            instance_ctx=ictx,
                            skipped_rules_count=self.checker.count_skipped_by_scope(it))
        return results

    def audit_file_content(self, content: str, file_path: str = "",
                           created_by: str = "", project_id: str = "",
                           evaluate_gate: bool = False,
                           save_history: bool = True,
                           connection_id: str = "",
                           instance_type: Optional[str] = None
                           ) -> tuple[list[AuditResult], AuditSummary,
                                      Optional[GateResult], "InstanceContext"]:
        """审核文件内容（V1.4：尺度全局、门禁按实例；V1.5：实例类型过滤适用域）。

        Returns:
            (结果列表, 汇总, 门禁结果或None, 实例类型上下文)
        """
        rule_set_id, overrides = self._resolve_scale()
        ictx = self._resolve_instance(connection_id, instance_type)
        it = ictx.instance_type.value
        results = self.checker.audit_file(content, file_path=file_path,
                                          rule_overrides=overrides,
                                          instance_type=it)
        summary = self.checker.compute_summary(results)

        gate_result = None
        if evaluate_gate:
            all_violations = [v for r in results for v in r.violations]
            gate_result = self._evaluate_gate(all_violations, connection_id)

        source = file_path if file_path else "file_upload"
        if save_history:
            _save_audit_history("file", source, results, summary,
                                created_by=created_by, project_id=project_id,
                                gate_result=gate_result, connection_id=connection_id,
                                rule_set_id=rule_set_id,
                                instance_ctx=ictx,
                                skipped_rules_count=self.checker.count_skipped_by_scope(it))
        return results, summary, gate_result, ictx

    def _evaluate_gate(self, violations, connection_id: str = "") -> Optional[GateResult]:
        """门禁评估（V1.4：按实例，不再按项目）"""
        try:
            from backend.services.gate_service import GateService
            return GateService().evaluate_for_instance(violations, connection_id)
        except Exception as e:
            logger.warning(f"门禁评估失败: {e}")
            return None

    def get_rule_list(self) -> list[dict]:
        """获取所有已启用的规则列表"""
        rules = self.checker.get_enabled_rules()
        return [
            {
                "rule_id": r.rule_id,
                "category": r.category.value,
                "severity": r.severity.value,
                "description": r.description,
                "enabled": r.enabled,
            }
            for r in rules
        ]
