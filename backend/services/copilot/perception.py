# -*- coding: utf-8 -*-
"""v1.6.4.1 / Copilot 全模块动态查询感知与本地私有知识注入引擎 (CP-PERCEPTION)

为 TDSQL SQLCheck 全功能模块提供统一的 Copilot 实时业务上下文感知：
1. 实例资产与拓扑架构 (Connections & Topology)
2. 审核规则库与规则集 (Rules & RuleSets)
3. SQL 审核历史与报告 (SQL Audit History & Reports)
4. 在线元数据审核 (Online Metadata Audit)
5. 慢 SQL 治理与诊断 (Slow Query Governance)
6. 实例体检与日常巡检 (Cluster Inspection & Health)
7. 表类型统计与分片分布 (Table Type Statistics)
8. 大表专项治理 (BigTable Inventory)
9. 扫描快照与纵向对比 (Scan Snapshots & Compare)
10. 网关日志与流量分析 (Gateway Log Reports)
11. 告警与应急预案 (Alerts & Emergency)
12. 跨模块全景健康摘要 (Cross-Module Health Digest)
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

from backend.services.connection_registry import registry

logger = logging.getLogger("tdsql.copilot.perception")

_RULE_ID_PATTERN = re.compile(r"(?i)(?<![a-zA-Z0-9])([rR]\d{3,4})(?!\d)")


class CopilotPerceptionEngine:
    """全功能模块动态感知路由引擎"""

    @classmethod
    def detect_scene(cls, query: str) -> str:
        """基于用户提问意图自动识别关联的业务场景编码 (匹配 copilot_scene_routes)"""
        q_lower = query.lower()
        if any(k in q_lower for k in ["规则", "规范", "rule", "解读", "dml", "ddl"]) and not any(k in q_lower for k in ["慢sql", "慢查"]):
            return "RULE_EXPLAIN"
        if any(k in q_lower for k in ["慢sql", "慢查", "慢查询", "slow query", "耗时最高", "优化建议", "explain"]):
            return "SLOW_EXPLAIN"
        if any(k in q_lower for k in ["改写", "修改建议", "sql建议", "怎么写", "sql优化"]):
            return "SQL_ADVISE"
        if any(k in q_lower for k in ["审核结果", "审核历史", "通过率", "元数据", "历史元数据", "工单审核"]):
            return "AUDIT_EXPLAIN"
        if any(k in q_lower for k in ["表类型", "分片表", "广播表", "单表", "二级分区"]):
            return "TABLETYPE_EXPLAIN"
        if any(k in q_lower for k in ["网关", "网关日志", "gateway", "接口日志", "interf"]):
            return "GATEWAY_EXPLAIN"
        if any(k in q_lower for k in ["快照", "对比", "纵向对比", "扫描记录"]):
            return "COMPARE_EXPLAIN"
        if any(k in q_lower for k in ["失败", "排查", "报错", "job", "任务失败"]):
            return "JOB_TROUBLESHOOT"
        if any(k in q_lower for k in ["诊断", "体检", "巡检", "健康分"]):
            return "DIAGNOSTIC_HELP"
        return "USAGE_HELP"

    @classmethod
    def gather_context(cls, conn, query: str, connection_id: Optional[str] = None, identity: Optional[Any] = None) -> list[str]:
        ctx_parts: list[str] = []
        q_lower = query.lower()

        # 1. 目标实例识别与权限过滤 (B-01a)
        saved_conns = []
        target_conn = None
        try:
            saved_conns = registry.list_saved()
        except Exception as e:
            logger.debug("目标实例识别异常: %s", e)

        # 严格权限过滤：非管理员必须按已获批准授权过滤，绝不枚举未授权实例 (B-01a)
        is_admin = bool(identity and getattr(identity, "role", "") in ("admin", "copilot_admin"))
        has_grant_filter = bool(identity and not is_admin)
        if has_grant_filter:
            try:
                from backend.services.copilot.repository import GrantRepo
                user_grants = GrantRepo.list_for_subject(conn, identity.subject_id)
                allowed_cids = {
                    g["connection_id"] for g in user_grants
                    if g.get("approval_state") == "APPROVED" and g.get("enabled")
                }
                saved_conns = [c for c in saved_conns if c.get("id") in allowed_cids]
            except Exception as ge:
                logger.warning("用户实例授权过滤异常: %s", ge)
                saved_conns = []

        if connection_id:
            target_conn = next((c for c in saved_conns if c.get("id") == connection_id), None)
            if target_conn:
                ctx_parts.append(
                    f"【当前会话选定的目标实例】：{target_conn.get('name')} "
                    f"(ID: {target_conn.get('id')}, 地址: {target_conn.get('host')}:{target_conn.get('port')}, "
                    f"架构类型: {'分布式实例' if target_conn.get('is_distributed') else '集中式实例'}, "
                    f"默认库: {target_conn.get('database') or '无'})"
                )
            elif has_grant_filter:
                ctx_parts.append(f"【实例访问受限】：您尚未获得实例 (ID: {connection_id}) 的 Copilot 访问授权。")
                return ctx_parts

        target_id = target_conn.get("id") if target_conn else (connection_id if is_admin else None)

        # 2. 判断是否为全局综合健康概况咨询
        is_general_health = any(k in q_lower for k in [
            "总体情况", "总体状态", "全貌", "整体情况", "有哪些问题", "健康状况",
            "概况", "实例详情", "体检报告", "现状", "基本情况"
        ])

        # 3. 模块感知分发

        # 模块 1: 实例资产拓扑清单
        if is_general_health or any(k in q_lower for k in [
            "所有库", "纳管", "资产", "清单", "多少个", "多少库", "多少实例",
            "实例连接", "有哪些库", "有哪些实例", "拓扑", "连接"
        ]):
            cls._perceive_connections(ctx_parts, saved_conns, has_grant_filter)

        # 模块 2: 审核规则库与规则集
        if any(k in q_lower for k in ["规则", "规范", "rule", "解读", "dml", "ddl", "治理规范", "规则集"]):
            cls._perceive_rules(conn, ctx_parts, query, q_lower)

        can_perceive_instances = (target_id is not None) or is_admin or (has_grant_filter and len(saved_conns) > 0)
        if not can_perceive_instances:
            return ctx_parts

        # 模块 3: SQL 审核历史记录
        if is_general_health or any(k in q_lower for k in [
            "sql审核", "sql 审核", "审核历史", "审核记录", "通过率", "工单审核", "文件审核", "sql历史"
        ]):
            cls._perceive_audit_history(conn, ctx_parts, target_id, saved_conns)

        # 模块 4: 在线元数据审核记录与任务
        if is_general_health or any(k in q_lower for k in [
            "元数据", "metadata", "历史元数据", "几条", "审核任务", "元数据任务"
        ]):
            cls._perceive_metadata_audit(conn, ctx_parts, target_id, target_conn, saved_conns)

        # 模块 5: 慢 SQL 治理与诊断
        if is_general_health or any(k in q_lower for k in [
            "慢sql", "慢查", "慢查询", "slow query", "耗时最高", "执行计划", "慢日志", "优化建议"
        ]):
            cls._perceive_slow_queries(conn, ctx_parts, target_id)

        # 模块 6: 实例体检与日常巡检
        if is_general_health or any(k in q_lower for k in [
            "体检", "巡检", "健康分", "评分", "隐患", "检查项", "异常项", "inspect"
        ]):
            cls._perceive_inspection(conn, ctx_parts, target_id)

        # 模块 7: 表类型统计与分布 (分片/广播/单表)
        if is_general_health or any(k in q_lower for k in [
            "表类型", "分片表", "广播表", "单表", "未拆分", "二级分区", "分表", "分库分表"
        ]):
            cls._perceive_table_types(conn, ctx_parts, target_id, saved_conns)

        # 模块 8: 大表专项治理
        if is_general_health or any(k in q_lower for k in [
            "大表", "超大表", "单表容量", "表行数", "存储排行", "表排行", "无拆分键大表"
        ]):
            cls._perceive_bigtables(conn, ctx_parts, target_id)

        # 模块 9: 扫描快照与纵向对比
        if any(k in q_lower for k in [
            "快照", "对比", "纵向对比", "版本对比", "扫描记录", "历史对比", "修复率", "新增违规"
        ]):
            cls._perceive_snapshots(conn, ctx_parts, target_id)

        # 模块 10: 网关日志与流量审计
        if any(k in q_lower for k in [
            "网关", "网关日志", "gateway", "接口日志", "interf", "流量分析"
        ]):
            cls._perceive_gateway_reports(conn, ctx_parts, target_id)

        # 模块 11: 告警与应急处置
        if is_general_health or any(k in q_lower for k in [
            "告警", "预警", "alert", "应急", "处置预案", "紧急"
        ]):
            cls._perceive_alerts(conn, ctx_parts, target_id)

        return ctx_parts

    # ─────────────────────────────────────────────────────────────
    # 各模块感知具体实现
    # ─────────────────────────────────────────────────────────────

    @classmethod
    def _perceive_connections(cls, ctx_parts: list[str], saved_conns: list[dict], has_grant_filter: bool = False) -> None:
        try:
            if not saved_conns:
                if has_grant_filter:
                    ctx_parts.append("【纳管实例访问受限】：您当前尚未获得任何数据库实例的 Copilot 访问授权，无法查看系统纳管实例清单。如需查询特定数据库，请联系管理员为您授予相关实例访问权限。")
                else:
                    ctx_parts.append("【系统纳管数据库实例全景】：当前系统暂无纳管的数据库实例配置。")
                return

            prefix = "【当前用户已获授权纳管实例清单" if has_grant_filter else "【系统纳管数据库实例全景"
            ctx_parts.append(f"{prefix}（当前共 {len(saved_conns)} 个实例）】:")
            for idx, c in enumerate(saved_conns, 1):
                ctx_parts.append(
                    f"{idx}. 实例名: {c.get('name')} | ID: {c.get('id')} | "
                    f"地址: {c.get('host')}:{c.get('port')} | "
                    f"架构: {'分布式实例' if c.get('is_distributed') else '集中式实例'} | "
                    f"默认库: {c.get('database') or '无'}"
                )
        except Exception as e:
            logger.debug("感知实例列表异常: %s", e)

    @classmethod
    def _perceive_rules(cls, conn, ctx_parts: list[str], query: str, q_lower: str) -> None:
        try:
            rule_ids = _RULE_ID_PATTERN.findall(query)
            matched_rules = []
            if rule_ids:
                for rid in rule_ids:
                    r_upper = rid.upper()
                    row = conn.execute("SELECT * FROM rule_configs WHERE rule_id = %s", (r_upper,)).fetchone()
                    if row:
                        matched_rules.append(dict(row))

            # 关键词语义匹配 (若未指定具体编号)
            if not matched_rules:
                for kw in ["多表", "update", "delete", "拆分键", "join", "select", "大表", "索引", "主键", "事务", "limit", "varchar", "drop", "truncate"]:
                    if kw in q_lower:
                        rows = conn.execute(
                            "SELECT * FROM rule_configs WHERE description LIKE %s OR category = %s LIMIT 3",
                            (f"%{kw}%", kw)
                        ).fetchall()
                        for r in rows:
                            rd = dict(r)
                            if not any(x["rule_id"] == rd["rule_id"] for x in matched_rules):
                                matched_rules.append(rd)

            # 规则集信息
            rs_row = conn.execute("SELECT id, name, description FROM rule_sets LIMIT 1").fetchone()
            total_rules_cnt = conn.execute("SELECT COUNT(*) as cnt FROM rule_configs WHERE enabled = 1").fetchone()

            if matched_rules:
                ctx_parts.append("【系统内置审核规则库精确匹配结果（官方权威定义）】：")
                for r in matched_rules:
                    ctx_parts.append(
                        f"- 规则编号: {r.get('rule_id')}\n"
                        f"  * 规则描述: {r.get('description')}\n"
                        f"  * 规范分类: {r.get('category')} (来源规范: {r.get('spec_source')})\n"
                        f"  * 阻断等级: {r.get('severity')}\n"
                        f"  * 官方修复建议: {r.get('fix_suggestion')}\n"
                        f"  * 启用状态: {'已启用' if r.get('enabled') else '未启用'}"
                    )
            else:
                ctx_parts.append(
                    f"【系统审核规则库概况】：平台当前共启用 {total_rules_cnt['cnt'] if total_rules_cnt else 121} 项 SQL 审核规范，"
                    f"默认激活规则集: 『{rs_row['name'] if rs_row else 'default'}』。"
                )
        except Exception as e:
            logger.debug("感知规则库异常: %s", e)

    @classmethod
    def _perceive_audit_history(cls, conn, ctx_parts: list[str], target_id: Optional[str], saved_conns: list[dict]) -> None:
        try:
            if target_id:
                cnt_row = conn.execute("SELECT COUNT(*) as c FROM audit_history WHERE connection_id = %s", (target_id,)).fetchone()
                recent = conn.execute(
                    "SELECT id, audit_type, total_sql, passed, failed, error_count, warning_count, pass_rate, created_at "
                    "FROM audit_history WHERE connection_id = %s ORDER BY created_at DESC LIMIT 3",
                    (target_id,)
                ).fetchall()
                c = cnt_row["c"] if cnt_row else 0
                ctx_parts.append(
                    f"【SQL 审核模块 - 目标实例历史审核统计】：\n"
                    f"- 目标实例 (ID: {target_id}) 累计执行 SQL 审核: 共 {c} 批次\n"
                )
                if recent:
                    ctx_parts.append("- 近期审核记录:")
                    for r in recent:
                        ctx_parts.append(
                            f"  * 记录 #{r['id']} ({r.get('created_at')}): 总SQL数 {r.get('total_sql')}, "
                            f"通过率 {r.get('pass_rate')}%, 阻断违规 {r.get('error_count')} 个, 告警 {r.get('warning_count')} 个"
                        )
            else:
                tot_row = conn.execute("SELECT COUNT(*) as c FROM audit_history").fetchone()
                ctx_parts.append(f"【SQL 审核模块 - 全平台历史审核统计】：平台全量 SQL 审核执行批次共计 {tot_row['c'] if tot_row else 0} 次。")
        except Exception as e:
            logger.debug("感知SQL审核历史异常: %s", e)

    @classmethod
    def _perceive_metadata_audit(cls, conn, ctx_parts: list[str], target_id: Optional[str], target_conn: Optional[dict], saved_conns: list[dict]) -> None:
        try:
            if target_id:
                m_rows = conn.execute(
                    "SELECT id, connection_id, db_name, state, progress_json, report_id, created_at "
                    "FROM metadata_audit_jobs WHERE connection_id = %s ORDER BY created_at DESC LIMIT 5",
                    (target_id,)
                ).fetchall()
                total_cnt = conn.execute(
                    "SELECT COUNT(*) as cnt FROM metadata_audit_jobs WHERE connection_id = %s",
                    (target_id,)
                ).fetchone()
                count_val = total_cnt["cnt"] if total_cnt else len(m_rows)
                inst_name = target_conn.get("name") if target_conn else target_id
                ctx_parts.append(
                    f"【在线元数据审核模块 - 当前选定实例历史审核记录（实时数据库统计）】：\n"
                    f"- 目标实例: {inst_name} (ID: {target_id})\n"
                    f"- 历史元数据审核记录总数: 共有 {count_val} 条记录"
                )
                if m_rows:
                    ctx_parts.append("- 历史审核记录明细:")
                    for idx, r in enumerate(m_rows, 1):
                        ctx_parts.append(
                            f"  * [第{idx}条] 任务ID: {r.get('id')} | 审核库名: {r.get('db_name')} | "
                            f"状态: {r.get('state')} | 对应报告编号: #{r.get('report_id')} | "
                            f"执行时间: {r.get('created_at')}"
                        )
            else:
                dist_rows = conn.execute(
                    "SELECT connection_id, COUNT(*) as cnt FROM metadata_audit_jobs GROUP BY connection_id"
                ).fetchall()
                total_cnt = conn.execute("SELECT COUNT(*) as cnt FROM metadata_audit_jobs").fetchone()
                tot = total_cnt["cnt"] if total_cnt else 0
                dist_map = {r["connection_id"]: r["cnt"] for r in dist_rows}

                ctx_parts.append(
                    f"【在线元数据审核模块 - 全局各实例历史审核记录统计（用户未选定特定实例）】：\n"
                    f"- 全平台历史元数据审核记录总数: 共计 {tot} 条\n"
                    f"- 各实例分布明细:"
                )
                for c in saved_conns:
                    cid = c.get("id")
                    c_cnt = dist_map.get(cid, 0)
                    ctx_parts.append(f"  * 实例 [{c.get('name')} (ID: {cid})]: {c_cnt} 条审核记录")
        except Exception as e:
            logger.debug("感知元数据审核异常: %s", e)

    @classmethod
    def _perceive_slow_queries(cls, conn, ctx_parts: list[str], target_id: Optional[str]) -> None:
        try:
            sql_where = "WHERE connection_id = %s" if target_id else ""
            args = (target_id,) if target_id else ()
            tot = conn.execute(f"SELECT COUNT(*) as c FROM slow_queries {sql_where}", args).fetchone()
            count = tot["c"] if tot else 0

            if count > 0:
                top_slow = conn.execute(
                    f"SELECT fingerprint, avg_time_ms, max_time_ms, exec_count, problem_type, suggestion "
                    f"FROM slow_queries {sql_where} ORDER BY avg_time_ms DESC LIMIT 3",
                    args
                ).fetchall()
                ctx_parts.append(
                    f"【慢SQL治理模块 - 实时统计数据】：\n"
                    f"- {'目标实例' if target_id else '系统全量'}慢查询记录数: 共计 {count} 条\n"
                    f"- Top 慢 SQL 摘要:"
                )
                for s in top_slow:
                    ctx_parts.append(
                        f"  * 指纹: {str(s.get('fingerprint'))[:60]}... | 平均耗时: {s.get('avg_time_ms')}ms | "
                        f"最大耗时: {s.get('max_time_ms')}ms | 诊断建议: {s.get('suggestion') or '待分析'}"
                    )
            else:
                ctx_parts.append(
                    f"【慢SQL治理模块 - 实时统计数据】：{'当前实例' if target_id else '当前系统'}暂无采集到慢查询记录（当前慢查询治理表无数据或尚未配置慢日志采集任务）。"
                )
        except Exception as e:
            logger.debug("感知慢SQL异常: %s", e)

    @classmethod
    def _perceive_inspection(cls, conn, ctx_parts: list[str], target_id: Optional[str]) -> None:
        try:
            sql_where = "WHERE connection_id = %s" if target_id else ""
            args = (target_id,) if target_id else ()
            row = conn.execute(
                f"SELECT id, inspect_date, total_issues, error_count, warning_count, node_count "
                f"FROM cluster_inspection {sql_where} ORDER BY id DESC LIMIT 1",
                args
            ).fetchone()
            if row:
                ctx_parts.append(
                    f"【实例体检与日常巡检模块 - 最新体检结果】：\n"
                    f"- 体检日期: {row.get('inspect_date')} | 检查节点数: {row.get('node_count')}\n"
                    f"- 发现隐患总数: {row.get('total_issues')} 个 (严重违规: {row.get('error_count')} 个, 警告: {row.get('warning_count')} 个)"
                )
            else:
                ctx_parts.append(
                    f"【实例体检与日常巡检模块】：{'当前实例' if target_id else '系统'}暂未留存自动化巡检任务快照，可在『实例体检』页面执行一次深度体检。"
                )
        except Exception as e:
            logger.debug("感知实例体检异常: %s", e)

    @classmethod
    def _perceive_table_types(cls, conn, ctx_parts: list[str], target_id: Optional[str], saved_conns: list[dict]) -> None:
        try:
            sql_where = "WHERE connection_id = %s" if target_id else ""
            args = (target_id,) if target_id else ()
            rows = conn.execute(
                f"SELECT id, connection_id, database_filter, total_tables, shard_tables, broadcast_tables, "
                f"single_tables, subpartition_tables, created_at FROM table_type_stat {sql_where} ORDER BY id DESC LIMIT 3",
                args
            ).fetchall()
            if rows:
                ctx_parts.append(f"【表类型统计与分布模块 - 实时统计数据】：")
                for r in rows:
                    ctx_parts.append(
                        f"- 库名: {r.get('database_filter') or '全部'} (实例ID: {r.get('connection_id')}) | 统计时间: {r.get('created_at')}\n"
                        f"  * 表总数: {r.get('total_tables')} | 分片表 (Shard): {r.get('shard_tables')} | "
                        f"广播表 (Broadcast): {r.get('broadcast_tables')} | 单表 (Single): {r.get('single_tables')} | "
                        f"二级分区表: {r.get('subpartition_tables')}"
                    )
            else:
                ctx_parts.append(f"【表类型统计与分布模块】：{'当前实例' if target_id else '系统'}暂无已生成的表类型统计留档。")
        except Exception as e:
            logger.debug("感知表类型异常: %s", e)

    @classmethod
    def _perceive_bigtables(cls, conn, ctx_parts: list[str], target_id: Optional[str]) -> None:
        try:
            sql_where = "WHERE connection_id = %s" if target_id else ""
            args = (target_id,) if target_id else ()
            tot = conn.execute(f"SELECT COUNT(*) as c FROM bigtable_inventory {sql_where}", args).fetchone()
            count = tot["c"] if tot else 0
            if count > 0:
                top_tabs = conn.execute(
                    f"SELECT table_name, size_gb, rows_count, shard_key, is_partitioned "
                    f"FROM bigtable_inventory {sql_where} ORDER BY size_gb DESC LIMIT 3",
                    args
                ).fetchall()
                ctx_parts.append(f"【大表专项治理模块 - 实时统计数据】：\n- 纳管大表总数: 共 {count} 张")
                for t in top_tabs:
                    ctx_parts.append(
                        f"  * 表名: {t.get('table_name')} | 容量: {t.get('size_gb')} GB | 行数: {t.get('rows_count')} | "
                        f"拆分键: {t.get('shard_key') or '未配置'} | 分区状态: {'已分区' if t.get('is_partitioned') else '未分区'}"
                    )
            else:
                ctx_parts.append(f"【大表专项治理模块】：{'当前实例' if target_id else '系统'}暂无记录大于阈值的大表资产。")
        except Exception as e:
            logger.debug("感知大表治理异常: %s", e)

    @classmethod
    def _perceive_snapshots(cls, conn, ctx_parts: list[str], target_id: Optional[str]) -> None:
        try:
            sql_where = "WHERE connection_id = %s" if target_id else ""
            args = (target_id,) if target_id else ()
            rows = conn.execute(
                f"SELECT id, module, db_name, scan_label, object_total, issue_total, error_count, warning_count, created_at "
                f"FROM scan_snapshots {sql_where} ORDER BY id DESC LIMIT 2",
                args
            ).fetchall()
            if rows:
                ctx_parts.append("【扫描快照与纵向对比模块 - 历史快照数据】：")
                for r in rows:
                    ctx_parts.append(
                        f"- 快照 #{r.get('id')} ({r.get('created_at')}): 模块: {r.get('module')} | 目标库: {r.get('db_name')} | "
                        f"检测对象: {r.get('object_total')} 个 | 发现问题: {r.get('issue_total')} 个 (阻断: {r.get('error_count')}, 警告: {r.get('warning_count')})"
                    )
            else:
                ctx_parts.append("【扫描快照与纵向对比模块】：暂无保存的扫描快照历史。")
        except Exception as e:
            logger.debug("感知快照异常: %s", e)

    @classmethod
    def _perceive_gateway_reports(cls, conn, ctx_parts: list[str], target_id: Optional[str]) -> None:
        try:
            sql_where = "WHERE connection_id = %s" if target_id else ""
            args = (target_id,) if target_id else ()
            row = conn.execute(
                f"SELECT id, connection_id, log_file_name, log_type, total_queries, slow_queries, max_time_ms, avg_time_ms, created_at "
                f"FROM gateway_log_reports {sql_where} ORDER BY id DESC LIMIT 1",
                args
            ).fetchone()
            if row:
                ctx_parts.append(
                    f"【网关日志与流量审计模块 - 最新分析报告】：\n"
                    f"- 报告 #{row.get('id')} ({row.get('created_at')}): 文件名: {row.get('log_file_name')} (类型: {row.get('log_type')})\n"
                    f"- 分析总查询数: {row.get('total_queries')} 条 | 慢查询: {row.get('slow_queries')} 条 | "
                    f"最大耗时: {row.get('max_time_ms')}ms | 平均耗时: {row.get('avg_time_ms')}ms"
                )
            else:
                ctx_parts.append("【网关日志与流量审计模块】：暂未生成网关分析报告。")
        except Exception as e:
            logger.debug("感知网关报告异常: %s", e)

    @classmethod
    def _perceive_alerts(cls, conn, ctx_parts: list[str], target_id: Optional[str]) -> None:
        try:
            tot = conn.execute("SELECT COUNT(*) as c FROM alerts WHERE status = 'ACTIVE'").fetchone()
            c = tot["c"] if tot else 0
            if c > 0:
                ctx_parts.append(f"【监控告警与应急处置模块】：当前系统处于活跃触发状态的告警共有 {c} 条，请重点防范。")
            else:
                ctx_parts.append("【监控告警与应急处置模块】：当前系统运行平稳，无活跃告警事件触发。")
        except Exception as e:
            logger.debug("感知告警异常: %s", e)
