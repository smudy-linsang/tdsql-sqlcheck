"""
TDSQL SQL审核工具 - 数据模型 (V1.0)

包含所有 Pydantic 模型定义，供 API 层和服务层使用。
"""
from datetime import datetime
from enum import Enum
from typing import Optional, Any

from pydantic import BaseModel, Field


# ═══════════════════════════════════════════════════════════════════
# 枚举定义
# ═══════════════════════════════════════════════════════════════════

class Severity(str, Enum):
    """违规严重级别"""
    ERROR = "ERROR"
    WARNING = "WARNING"
    INFO = "INFO"


class AuditType(str, Enum):
    """审核类型"""
    SQL = "sql"
    DDL = "ddl"
    FILE = "file"
    UPLOAD = "upload"
    WEBHOOK = "webhook"
    CLI = "cli"


class RuleCategory(str, Enum):
    """规则类别"""
    NAMING = "naming"
    DDL = "ddl"
    DML = "dml"
    PERFORMANCE = "performance"
    DISTRIBUTED = "distributed"
    INDEX = "index"
    TRANSACTION = "transaction"
    SECURITY = "security"
    ORACLE_COMPAT = "oracle_compat"


# ═══════════════════════════════════════════════════════════════════
# 基础审核模型
# ═══════════════════════════════════════════════════════════════════

class Violation(BaseModel):
    """单条违规记录"""
    rule_id: str = Field(..., description="规则ID，如 R001")
    category: RuleCategory = Field(..., description="规则类别")
    severity: Severity = Field(..., description="违规严重级别")
    message: str = Field(..., description="违规描述")
    suggestion: Optional[str] = Field(None, description="修复建议")
    line_number: Optional[int] = Field(None, description="SQL所在行号")


class AuditResult(BaseModel):
    """单条SQL审核结果"""
    sql: str = Field(..., description="原始SQL")
    sql_type: str = Field(..., description="SQL类型: SELECT/INSERT/UPDATE/DELETE/CREATE/ALTER/DROP")
    passed: bool = Field(..., description="是否通过审核")
    violations: list[Violation] = Field(default_factory=list, description="违规列表")
    file_path: Optional[str] = Field(None, description="来源文件路径")
    line_number: Optional[int] = Field(None, description="行号")


class AuditSummary(BaseModel):
    """审核汇总"""
    total_sql: int = Field(0, description="SQL总数")
    passed: int = Field(0, description="通过数")
    failed: int = Field(0, description="未通过数")
    error_count: int = Field(0, description="ERROR级别数量")
    warning_count: int = Field(0, description="WARNING级别数量")
    pass_rate: float = Field(0.0, description="通过率")


# ═══════════════════════════════════════════════════════════════════
# 审核请求/响应模型
# ═══════════════════════════════════════════════════════════════════

class AuditRequest(BaseModel):
    """审核请求"""
    sql: str = Field(..., description="待审核的SQL语句", min_length=1)
    project_id: Optional[str] = Field(None, description="项目ID")
    connection_id: Optional[str] = Field(None, description="TDSQL连接ID")
    enable_metadata: bool = Field(False, description="是否启用元数据增强")


class AuditResponse(BaseModel):
    """审核响应"""
    passed: bool = Field(..., description="是否通过审核")
    violations: list[Violation] = Field(default_factory=list, description="违规列表")
    sql_type: str = Field("", description="SQL类型")
    gate_result: Optional["GateResult"] = Field(None, description="门禁结果")


class FileAuditRequest(BaseModel):
    """文件审核请求"""
    content: str = Field(..., description="文件内容")
    file_path: str = Field("", description="文件路径")
    project_id: Optional[str] = Field(None, description="项目ID")
    connection_id: Optional[str] = Field(None, description="TDSQL连接ID")


class FileAuditResponse(BaseModel):
    """文件审核响应"""
    results: list[AuditResult] = Field(default_factory=list, description="审核结果列表")
    summary: AuditSummary = Field(..., description="审核汇总")
    gate_result: Optional["GateResult"] = Field(None, description="门禁结果")


# ═══════════════════════════════════════════════════════════════════
# 质量门禁模型
# ═══════════════════════════════════════════════════════════════════

class GateRule(BaseModel):
    """门禁规则"""
    project_id: str = Field(..., description="项目ID")
    max_error_count: int = Field(0, description="ERROR级别违规上限(0=不允许)")
    max_warning_count: int = Field(-1, description="WARNING级别违规上限(-1=不限制)")
    required_rules: list[str] = Field(default_factory=list, description="必须通过的规则ID列表")
    blocked_rules: list[str] = Field(default_factory=list, description="出现即阻断的规则ID列表")
    description: str = Field("", description="门禁策略描述")


class GateResult(BaseModel):
    """门禁评估结果"""
    passed: bool = Field(..., description="门禁是否通过")
    gate_rule_id: str = Field("", description="门禁规则ID")
    error_count: int = Field(0, description="ERROR违规数")
    warning_count: int = Field(0, description="WARNING违规数")
    blocked_by: list[str] = Field(default_factory=list, description="阻断规则列表")
    detail: str = Field("", description="门禁详情")


# ═══════════════════════════════════════════════════════════════════
# 项目管理模型
# ═══════════════════════════════════════════════════════════════════

class Project(BaseModel):
    """项目"""
    id: Optional[int] = Field(None, description="自增ID")
    project_id: str = Field(..., description="项目ID")
    project_name: str = Field(..., description="项目名称")
    tdsql_connection_id: str = Field("", description="关联TDSQL连接")
    rule_set_id: str = Field("default", description="规则集ID")
    gate_rule_id: str = Field("default", description="门禁规则ID")
    gitlab_project_id: Optional[int] = Field(None, description="GitLab项目ID")
    gitlab_url: str = Field("", description="GitLab地址")
    description: str = Field("", description="项目描述")
    status: str = Field("active", description="状态")
    created_at: Optional[str] = Field(None, description="创建时间")


class ProjectCreate(BaseModel):
    """创建项目请求"""
    project_name: str = Field(..., description="项目名称")
    tdsql_connection_id: str = Field("", description="TDSQL连接ID")
    rule_set_id: str = Field("default", description="规则集ID")
    gate_rule_id: str = Field("default", description="门禁规则ID")
    gitlab_project_id: Optional[int] = Field(None, description="GitLab项目ID")
    gitlab_url: str = Field("", description="GitLab地址")
    description: str = Field("", description="项目描述")


# ═══════════════════════════════════════════════════════════════════
# TDSQL连接配置模型
# ═══════════════════════════════════════════════════════════════════

class TDSQLConnectionCreate(BaseModel):
    """创建TDSQL连接配置"""
    id: str = Field(..., description="连接ID")
    name: str = Field(..., description="连接名称")
    host: str = Field(..., description="主机地址")
    port: int = Field(15000, description="端口")
    username: str = Field(..., description="用户名")
    password: str = Field(..., description="密码(明文，存储时加密)")
    database: str = Field("", description="默认数据库")
    charset: str = Field("utf8mb4", description="字符集")
    is_default: bool = Field(False, description="是否默认连接")
    is_distributed: bool = Field(True, description="是否分布式实例")
    description: str = Field("", description="描述")


class TDSQLConnectionInfo(BaseModel):
    """TDSQL连接信息(脱敏)"""
    id: str
    name: str
    host: str
    port: int
    username: str
    password: str = "***"
    database: str
    charset: str
    is_default: bool
    is_distributed: bool
    description: str
    status: str
    last_connected_at: Optional[str] = None
    created_at: Optional[str] = None


# ═══════════════════════════════════════════════════════════════════
# 大表治理模型
# ═══════════════════════════════════════════════════════════════════

class BigTableInfo(BaseModel):
    """大表信息"""
    schema: str = ""
    table: str = ""
    size_gb: float = 0.0
    rows: int = 0
    level: str = "L1"
    level_label: str = ""
    is_partitioned: bool = False
    partition_count: int = 0
    shard_key: str = ""


class PartitionWatermarkInfo(BaseModel):
    """分区水位信息"""
    table: str = ""
    partition_count: int = 0
    status: str = "NORMAL"
    watermark_percent: float = 0.0


class PartitionAdvice(BaseModel):
    """分区改造建议"""
    table: str = ""
    conditions: list[Any] = Field(default_factory=list)
    ddl_example: str = ""


class TableClassification(BaseModel):
    """表类型分类"""
    connection_id: str
    schema: str
    table: str
    table_type: str
    table_type_label: str = ""
    retention_days: int = 0
    partition_key: str = ""
    partition_granularity: str = ""


# ═══════════════════════════════════════════════════════════════════
# 慢SQL分析模型
# ═══════════════════════════════════════════════════════════════════

class FingerprintStats(BaseModel):
    """SQL指纹统计"""
    fingerprint: str = ""
    sample_sql: str = ""
    exec_count: int = 0
    total_time_ms: float = 0
    avg_time_ms: float = 0
    max_time_ms: float = 0
    rows_examined: int = 0
    rows_sent: int = 0


class IndexRecommendation(BaseModel):
    """索引推荐"""
    type: str = ""
    table: str = ""
    index_name: str = ""
    columns: list[str] = Field(default_factory=list)
    ddl: str = ""
    reason: str = ""


class RewriteSuggestion(BaseModel):
    """SQL改写建议"""
    type: str = ""
    original_sql: str = ""
    rewritten_sql: str = ""
    reason: str = ""
    expected_benefit: str = ""


class SlowQueryProblem(BaseModel):
    """慢SQL问题"""
    type: str = ""
    severity: str = "WARNING"
    message: str = ""
    root_cause: str = ""


class DistributedExplainReport(BaseModel):
    """分布式EXPLAIN分析报告"""
    shard_key_in_where: bool = False
    warnings: list[dict] = Field(default_factory=list)


class SlowAnalysisReport(BaseModel):
    """慢SQL分析报告"""
    slow_query_id: Optional[int] = None
    sql_text: str = ""
    fingerprint: str = ""
    problems: list[SlowQueryProblem] = Field(default_factory=list)
    distributed_analysis: Optional[DistributedExplainReport] = None
    index_suggestions: list[IndexRecommendation] = Field(default_factory=list)
    rewrite_suggestions: list[RewriteSuggestion] = Field(default_factory=list)


class DeadlockReport(BaseModel):
    """死锁分析报告"""
    has_deadlock: bool = False
    deadlock_time: str = ""
    transaction_1: dict = Field(default_factory=dict)
    transaction_2: dict = Field(default_factory=dict)
    locked_resource: str = ""
    suggestions: list[str] = Field(default_factory=list)


class LongTransactionInfo(BaseModel):
    """长事务信息"""
    trx_id: str = ""
    started_at: str = ""
    run_seconds: int = 0
    state: str = ""
    rows_locked: int = 0
    rows_modified: int = 0
    query: str = ""
    severity: str = "WARNING"


class CharsetDiagnosticReport(BaseModel):
    """字符集诊断报告"""
    schema: str = ""
    instance_defaults: dict = Field(default_factory=dict)
    issues: list[dict] = Field(default_factory=list)
    summary: dict = Field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════════
# 监控告警模型
# ═══════════════════════════════════════════════════════════════════

class AlertInfo(BaseModel):
    """告警信息"""
    metric: str = ""
    value: float = 0
    level: str = "WARNING"
    connection_id: str = ""
    message: str = ""


class AlertRuleConfig(BaseModel):
    """告警规则配置"""
    metric_name: str
    warning_threshold: float
    urgent_threshold: float
    check_interval_sec: int = 60
    notify_webhook: str = ""
    notify_email: str = ""
    enabled: bool = True


# ═══════════════════════════════════════════════════════════════════
# 巡检模型
# ═══════════════════════════════════════════════════════════════════

class InspectionTaskInfo(BaseModel):
    """巡检任务信息"""
    id: int
    connection_id: str
    inspection_type: str
    status: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    error_message: str = ""
    report_path: str = ""
    created_at: str = ""


class InspectionResultInfo(BaseModel):
    """巡检结果"""
    category: str = ""
    severity: str = "INFO"
    schema_name: str = ""
    table_name: str = ""
    metric_name: str = ""
    metric_value: str = ""
    threshold: str = ""
    message: str = ""
    suggestion: str = ""


# ═══════════════════════════════════════════════════════════════════
# 统一响应模型
# ═══════════════════════════════════════════════════════════════════

class ApiResponse(BaseModel):
    """统一API响应"""
    code: int = 0
    message: str = "success"
    data: Any = None
    timestamp: str = ""


class PaginatedResponse(BaseModel):
    """分页响应"""
    items: list[Any] = Field(default_factory=list)
    total: int = 0
    page: int = 1
    page_size: int = 20


# 解决前向引用
AuditResponse.model_rebuild()
FileAuditResponse.model_rebuild()
