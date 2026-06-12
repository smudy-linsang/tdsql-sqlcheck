"""
TDSQL SQL审核工具 - 慢SQL分析引擎

基于 TDSQL-MySQL慢查询发现与优化方案 进行慢SQL诊断和优化建议生成。

分析维度：
1. EXPLAIN执行计划分析
2. 索引使用分析
3. SQL改写建议
4. 字符集/类型一致性检查
"""
import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ExplainRow:
    """EXPLAIN输出单行"""
    id: int = 0
    select_type: str = ""
    table: str = ""
    partitions: str = ""
    type: str = ""          # 访问类型: system/const/eq_ref/ref/range/index/ALL
    possible_keys: str = ""
    key: str = ""
    key_len: str = ""
    ref: str = ""
    rows: int = 0
    filtered: float = 100.0
    extra: str = ""


@dataclass
class SlowQueryRecord:
    """慢SQL记录"""
    fingerprint: str = ""
    sql_text: str = ""
    db_name: str = ""
    exec_count: int = 0
    total_time_ms: float = 0.0
    avg_time_ms: float = 0.0
    max_time_ms: float = 0.0
    rows_examined: int = 0
    rows_sent: int = 0
    lock_time_ms: float = 0.0


@dataclass
class AnalysisResult:
    """分析结果"""
    problem_type: str = ""        # 问题类型
    severity: str = "INFO"        # 严重程度
    description: str = ""         # 问题描述
    evidence: str = ""            # 证据
    root_cause: str = ""          # 根因
    suggestion: str = ""          # 优化建议
    optimized_sql: str = ""       # 优化后SQL示例
    reference: str = ""           # 参考文档


@dataclass
class SlowAnalysisReport:
    """慢SQL分析报告"""
    sql_text: str = ""
    fingerprint: str = ""
    problem_type: str = ""
    severity: str = "INFO"
    analyses: list[AnalysisResult] = field(default_factory=list)
    summary: str = ""


class SlowSQLAnalyzer:
    """慢SQL分析器"""

    # EXPLAIN type 从优到劣
    TYPE_RANK = {
        "system": 0, "const": 1, "eq_ref": 2, "ref": 3,
        "range": 4, "index": 5, "ALL": 6
    }

    # Extra 中的告警信号
    EXTRA_WARNINGS = {
        "Using filesort": "使用了文件排序，未利用索引排序",
        "Using temporary": "使用了临时表，常见于GROUP BY/DISTINCT",
        "Using join buffer": "JOIN未走索引，使用了连接缓冲",
    }

    # Extra 中的良好信号
    EXTRA_GOOD = {
        "Using index": "覆盖索引，性能良好",
        "Using index condition": "索引条件下推(ICP)，性能良好",
    }

    def analyze_explain(self, explain_rows: list[dict]) -> SlowAnalysisReport:
        """
        分析EXPLAIN执行计划。

        Args:
            explain_rows: EXPLAIN输出的字典列表

        Returns:
            SlowAnalysisReport 分析报告
        """
        report = SlowAnalysisReport()
        analyses = []

        # 字段默认值映射（处理None值）
        _defaults = {
            "id": 0, "select_type": "", "table": "", "partitions": "",
            "type": "", "possible_keys": "", "key": "", "key_len": "",
            "ref": "", "rows": 0, "filtered": 100.0, "extra": "",
        }
        for row_dict in explain_rows:
            safe = {}
            for k, d in _defaults.items():
                v = row_dict.get(k)
                safe[k] = v if v is not None else d
            row = ExplainRow(**safe)

            # 1. 检查访问类型
            type_analysis = self._check_access_type(row)
            if type_analysis:
                analyses.append(type_analysis)

            # 2. 检查索引使用
            index_analysis = self._check_index_usage(row)
            if index_analysis:
                analyses.append(index_analysis)

            # 3. 检查Extra信息
            extra_analyses = self._check_extra(row)
            analyses.extend(extra_analyses)

            # 4. 检查扫描行数
            rows_analysis = self._check_rows_examined(row)
            if rows_analysis:
                analyses.append(rows_analysis)

        report.analyses = analyses
        report.summary = self._generate_summary(analyses)
        return report

    def analyze_slow_query(self, record: SlowQueryRecord) -> SlowAnalysisReport:
        """
        分析慢SQL记录，给出综合诊断。

        Args:
            record: 慢SQL记录

        Returns:
            SlowAnalysisReport 分析报告
        """
        report = SlowAnalysisReport(
            sql_text=record.sql_text,
            fingerprint=record.fingerprint,
        )
        analyses = []

        # 1. 基于执行频次和耗时分析
        if record.exec_count > 1000 and record.avg_time_ms > 100:
            analyses.append(AnalysisResult(
                problem_type="高频慢SQL",
                severity="ERROR",
                description=f"该SQL执行{record.exec_count}次，平均耗时{record.avg_time_ms:.1f}ms",
                evidence=f"总耗时: {record.total_time_ms:.0f}ms, 最大耗时: {record.max_time_ms:.0f}ms",
                root_cause="高频+高耗时，影响面广",
                suggestion="优先优化此SQL，建议检查索引使用情况并考虑SQL改写",
            ))

        # 2. 扫描行数 vs 返回行数
        if record.rows_sent > 0 and record.rows_examined > 0:
            scan_ratio = record.rows_examined / record.rows_sent
            if scan_ratio > 100:
                analyses.append(AnalysisResult(
                    problem_type="索引使用不充分",
                    severity="ERROR",
                    description=f"扫描行数({record.rows_examined})远大于返回行数({record.rows_sent})",
                    evidence=f"扫描/返回比: {scan_ratio:.0f}:1",
                    root_cause="WHERE条件未能有效利用索引，导致大量无效扫描",
                    suggestion="检查WHERE条件字段是否有索引，考虑添加覆盖索引",
                ))

        # 3. 锁等待分析
        if record.lock_time_ms > 0 and record.total_time_ms > 0:
            lock_ratio = record.lock_time_ms / record.total_time_ms
            if lock_ratio > 0.3:
                analyses.append(AnalysisResult(
                    problem_type="锁等待严重",
                    severity="WARNING",
                    description=f"锁等待时间占总耗时{lock_ratio*100:.1f}%",
                    evidence=f"锁等待: {record.lock_time_ms:.0f}ms, 总耗时: {record.total_time_ms:.0f}ms",
                    root_cause="可能存在锁竞争或长事务",
                    suggestion="检查是否存在长事务、死锁，考虑缩短事务范围或优化热点行更新",
                ))

        # 4. SQL文本分析
        sql_analyses = self._analyze_sql_text(record.sql_text)
        analyses.extend(sql_analyses)

        report.analyses = analyses
        report.summary = self._generate_summary(analyses)
        report.severity = self._get_max_severity(analyses)
        report.problem_type = self._get_primary_problem(analyses)
        return report

    def _check_access_type(self, row: ExplainRow) -> Optional[AnalysisResult]:
        """检查EXPLAIN的type字段"""
        if not row.type:
            return None

        type_lower = row.type.lower()
        rank = self.TYPE_RANK.get(type_lower, 6)

        if rank >= 5:  # index 或 ALL
            return AnalysisResult(
                problem_type="全表扫描",
                severity="ERROR" if rank == 6 else "WARNING",
                description=f"访问类型为 {row.type}（{'全表扫描' if rank == 6 else '索引全扫描'}）",
                evidence=f"type={row.type}, rows={row.rows}, table={row.table}",
                root_cause="WHERE条件未命中索引，导致全表扫描",
                suggestion=self._suggest_index_fix(row),
            )
        return None

    def _check_index_usage(self, row: ExplainRow) -> Optional[AnalysisResult]:
        """检查索引使用情况"""
        if row.key and row.key.upper() != "NULL":
            return None

        if row.type and row.type.lower() in ("all", "index"):
            return AnalysisResult(
                problem_type="缺失索引",
                severity="ERROR",
                description=f"未使用任何索引（key=NULL）",
                evidence=f"possible_keys={row.possible_keys or 'NULL'}, rows={row.rows}",
                root_cause="WHERE条件中的字段没有可用索引",
                suggestion=f"建议为表 {row.table} 的WHERE条件字段创建索引",
            )
        return None

    def _check_extra(self, row: ExplainRow) -> list[AnalysisResult]:
        """检查Extra字段中的告警信号"""
        results = []
        if not row.extra:
            return results

        for signal, desc in self.EXTRA_WARNINGS.items():
            if signal in row.extra:
                severity = "ERROR" if signal == "Using temporary" else "WARNING"
                results.append(AnalysisResult(
                    problem_type=signal,
                    severity=severity,
                    description=desc,
                    evidence=f"Extra={row.extra}",
                    root_cause=self._get_extra_root_cause(signal),
                    suggestion=self._get_extra_suggestion(signal, row),
                ))

        return results

    def _check_rows_examined(self, row: ExplainRow) -> Optional[AnalysisResult]:
        """检查扫描行数是否过大"""
        if row.rows > 100000:
            return AnalysisResult(
                problem_type="扫描行数过多",
                severity="WARNING",
                description=f"预估扫描行数为 {row.rows}，扫描范围过大",
                evidence=f"rows={row.rows}, table={row.table}",
                root_cause="WHERE条件过滤效果差或缺少有效索引",
                suggestion="考虑添加更精确的索引或优化WHERE条件",
            )
        return None

    def _analyze_sql_text(self, sql: str) -> list[AnalysisResult]:
        """基于SQL文本的静态分析"""
        results = []
        sql_upper = sql.upper()

        # SELECT * 检查
        if re.search(r'SELECT\s+\*\s+FROM', sql_upper):
            results.append(AnalysisResult(
                problem_type="SELECT *",
                severity="WARNING",
                description="使用了 SELECT * 返回所有字段",
                root_cause="SELECT * 无法使用覆盖索引，增加网络和IO开销",
                suggestion="明确列出需要的字段，避免 SELECT *",
            ))

        # LIKE '%xxx' 检查
        if re.search(r"LIKE\s+'%", sql, re.IGNORECASE):
            results.append(AnalysisResult(
                problem_type="左模糊查询",
                severity="ERROR",
                description="LIKE以%开头，导致索引失效",
                root_cause="前缀模糊匹配无法利用B+Tree索引",
                suggestion="改用前缀匹配 LIKE 'xxx%'，或考虑全文索引/搜索引擎",
            ))

        # 子查询检查
        subquery_count = sql_upper.count("SELECT") - 1
        if subquery_count > 2:
            results.append(AnalysisResult(
                problem_type="多层子查询",
                severity="WARNING",
                description=f"SQL包含{subquery_count}层子查询",
                root_cause="多层子查询影响可读性和优化器选择",
                suggestion="建议将子查询改写为JOIN",
            ))

        # ORDER BY 无索引覆盖的检查
        if "ORDER BY" in sql_upper and "LIMIT" in sql_upper:
            # 大偏移量分页
            limit_match = re.search(r'LIMIT\s+(\d+)\s*,\s*(\d+)', sql_upper)
            if limit_match:
                offset = int(limit_match.group(1))
                if offset > 10000:
                    results.append(AnalysisResult(
                        problem_type="深度分页",
                        severity="ERROR",
                        description=f"LIMIT偏移量为{offset}，深度分页性能极差",
                        root_cause=f"MySQL需要扫描offset+limit行后丢弃前offset行",
                        suggestion="改用游标分页: WHERE id > last_id ORDER BY id LIMIT N",
                        optimized_sql=f"SELECT ... WHERE id > <last_id> ORDER BY id LIMIT {limit_match.group(2)}",
                    ))

        # JOIN 检查
        join_count = sql_upper.count(" JOIN ")
        if join_count >= 3:
            results.append(AnalysisResult(
                problem_type="多表JOIN",
                severity="WARNING",
                description=f"SQL包含{join_count}个JOIN操作",
                root_cause="多表JOIN增加优化器选择复杂度，可能产生次优执行计划",
                suggestion="关键交易链路JOIN不超过2个表，非关键链路不超过3个表",
            ))

        # 检查 LIKE '%xxx%' 全模糊
        if re.search(r"LIKE\s+'%.*%'", sql, re.IGNORECASE):
            results.append(AnalysisResult(
                problem_type="全模糊查询",
                severity="ERROR",
                description="LIKE '%xxx%' 全模糊查询导致索引完全失效",
                root_cause="全模糊匹配无法利用B+Tree索引的任何前缀",
                suggestion="1) 改为前缀匹配; 2) 使用全文索引 FULLTEXT; 3) 引入搜索引擎如ES",
            ))

        # OR 条件检查
        if re.search(r'\bWHERE\b.*\bOR\b', sql_upper):
            results.append(AnalysisResult(
                problem_type="OR条件",
                severity="INFO",
                description="WHERE中使用了OR条件，可能导致索引失效",
                root_cause="OR条件中如果部分字段无索引，会导致全表扫描",
                suggestion="考虑将OR改写为UNION ALL（前提：各字段有独立索引）",
            ))

        return results

    def _suggest_index_fix(self, row: ExplainRow) -> str:
        """根据EXPLAIN行生成索引建议"""
        if row.possible_keys and row.possible_keys != "NULL":
            return (
                f"表 {row.table} 有可选索引 ({row.possible_keys}) 但未使用，"
                f"建议检查WHERE条件字段类型是否一致，或使用 FORCE INDEX 测试"
            )
        return f"建议为表 {row.table} 的WHERE条件字段创建合适的索引"

    def _get_extra_root_cause(self, signal: str) -> str:
        """获取Extra信号的根因"""
        causes = {
            "Using filesort": "ORDER BY字段未被索引覆盖，需要额外排序操作",
            "Using temporary": "GROUP BY或DISTINCT操作需要创建临时表",
            "Using join buffer": "JOIN的关联字段缺少索引",
        }
        return causes.get(signal, "")

    def _get_extra_suggestion(self, signal: str, row: ExplainRow) -> str:
        """获取Extra信号的优化建议"""
        suggestions = {
            "Using filesort": f"为表 {row.table} 添加覆盖ORDER BY字段的复合索引",
            "Using temporary": f"优化GROUP BY/DISTINCT，确保分组字段有索引覆盖",
            "Using join buffer": f"为表 {row.table} 的JOIN关联字段创建索引",
        }
        return suggestions.get(signal, "")

    def _generate_summary(self, analyses: list[AnalysisResult]) -> str:
        """生成分析摘要"""
        if not analyses:
            return "未发现明显问题"

        error_count = sum(1 for a in analyses if a.severity == "ERROR")
        warning_count = sum(1 for a in analyses if a.severity == "WARNING")

        parts = []
        if error_count > 0:
            parts.append(f"{error_count}个严重问题")
        if warning_count > 0:
            parts.append(f"{warning_count}个警告")

        problem_types = list(set(a.problem_type for a in analyses if a.severity == "ERROR"))
        if problem_types:
            parts.append(f"主要问题: {', '.join(problem_types[:3])}")

        return "；".join(parts)

    def _get_max_severity(self, analyses: list[AnalysisResult]) -> str:
        """获取最高严重级别"""
        if any(a.severity == "ERROR" for a in analyses):
            return "ERROR"
        if any(a.severity == "WARNING" for a in analyses):
            return "WARNING"
        return "INFO"

    def _get_primary_problem(self, analyses: list[AnalysisResult]) -> str:
        """获取主要问题类型"""
        for a in analyses:
            if a.severity == "ERROR":
                return a.problem_type
        if analyses:
            return analyses[0].problem_type
        return "无明显问题"
