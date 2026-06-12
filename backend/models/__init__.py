"""
TDSQL SQL审核工具 - 数据模型
"""
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


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


class RuleCategory(str, Enum):
    """规则类别"""
    NAMING = "naming"
    DDL = "ddl"
    DML = "dml"
    PERFORMANCE = "performance"
    DISTRIBUTED = "distributed"


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


class AuditRequest(BaseModel):
    """审核请求"""
    sql: str = Field(..., description="待审核的SQL语句", min_length=1)
    db_type: str = Field("tdsql", description="数据库类型")


class AuditResponse(BaseModel):
    """审核响应"""
    passed: bool = Field(..., description="是否通过审核")
    violations: list[Violation] = Field(default_factory=list, description="违规列表")
    sql_type: str = Field("", description="SQL类型")


class FileAuditRequest(BaseModel):
    """文件审核请求"""
    content: str = Field(..., description="文件内容")
    file_path: str = Field("", description="文件路径")


class FileAuditResponse(BaseModel):
    """文件审核响应"""
    results: list[AuditResult] = Field(default_factory=list, description="审核结果列表")
    summary: AuditSummary = Field(..., description="审核汇总")
