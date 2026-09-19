# -*- coding: utf-8 -*-
"""v1.6.4.0 AI Copilot 专家助手 — Pydantic 契约模型与枚举闭集（CP-W01）。

设计出处：docs/DETAIL-v1.6.4.0-AI-Copilot-专家助手详细设计说明书-O.md（Rev.D 冻结基线）
- §4.1 证据统一结构与 availability/completeness 闭集
- §5.1 场景枚举、§5.4 SQL 复核四态
- §8.1 模型输出 schema（extra='forbid'、条数与长度上限）
- §8.1.1 outcome_claims 结构化结果断言
- §8.3 动作卡闭集
- §11.1 turn 状态机闭集
- §12 请求/响应合同

本模块只定义契约，不含任何 IO。所有输入模型 extra='forbid'。
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ══════════════════════════════════════════════════════════════════
# 枚举闭集
# ══════════════════════════════════════════════════════════════════


class CopilotScene(str, Enum):
    """§5.1 用户场景枚举（PROVIDER_SELFTEST 仅服务端内部使用，不对用户开放）。"""
    USAGE_HELP = "USAGE_HELP"
    RULE_EXPLAIN = "RULE_EXPLAIN"
    SQL_ADVISE = "SQL_ADVISE"
    AUDIT_EXPLAIN = "AUDIT_EXPLAIN"
    JOB_TROUBLESHOOT = "JOB_TROUBLESHOOT"
    SLOW_EXPLAIN = "SLOW_EXPLAIN"
    COMPARE_EXPLAIN = "COMPARE_EXPLAIN"
    TABLETYPE_EXPLAIN = "TABLETYPE_EXPLAIN"
    GATEWAY_EXPLAIN = "GATEWAY_EXPLAIN"
    DIAGNOSTIC_HELP = "DIAGNOSTIC_HELP"


#: 服务端内部场景（自检），不进入用户 scene 枚举与 scene_routes 配置闭集
SCENE_PROVIDER_SELFTEST = "PROVIDER_SELFTEST"


class ScopeKind(str, Enum):
    GLOBAL_HELP = "GLOBAL_HELP"
    INSTANCE = "INSTANCE"


class DataClass(str, Enum):
    """§4.3 数据分级闭集。"""
    PUBLIC_HELP = "PUBLIC_HELP"
    INTERNAL_REDACTED = "INTERNAL_REDACTED"
    RESTRICTED = "RESTRICTED"


class Availability(str, Enum):
    AVAILABLE = "AVAILABLE"
    MISSING = "MISSING"
    STALE = "STALE"
    UNKNOWN = "UNKNOWN"


class Completeness(str, Enum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    UNKNOWN = "UNKNOWN"


class SourceKind(str, Enum):
    """§4.1 受信任来源枚举。"""
    RULE_RUNTIME = "RULE_RUNTIME"
    KNOWLEDGE_PACK = "KNOWLEDGE_PACK"
    AUDIT_HISTORY = "AUDIT_HISTORY"
    METADATA_JOB = "METADATA_JOB"
    SLOW_QUERY = "SLOW_QUERY"
    SCAN_SNAPSHOT = "SCAN_SNAPSHOT"
    TABLE_TYPE_STAT = "TABLE_TYPE_STAT"
    GATEWAY_REPORT = "GATEWAY_REPORT"
    USER_DRAFT = "USER_DRAFT"


class TurnState(str, Enum):
    """§11.1 状态机闭集。"""
    ACCEPTED = "ACCEPTED"
    RUNNING = "RUNNING"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    SUCCEEDED = "SUCCEEDED"
    LOCAL_ONLY = "LOCAL_ONLY"
    DEGRADED = "DEGRADED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    INTERRUPTED = "INTERRUPTED"


#: 活动态（占用并发/每用户槽位）
ACTIVE_TURN_STATES = frozenset({
    TurnState.ACCEPTED.value, TurnState.RUNNING.value, TurnState.CANCEL_REQUESTED.value,
})
#: 终态
TERMINAL_TURN_STATES = frozenset({
    TurnState.SUCCEEDED.value, TurnState.LOCAL_ONLY.value, TurnState.DEGRADED.value,
    TurnState.FAILED.value, TurnState.CANCELLED.value, TurnState.INTERRUPTED.value,
})


class TurnPhase(str, Enum):
    WAITING = "WAITING"
    EVIDENCE = "EVIDENCE"
    RETRIEVAL = "RETRIEVAL"
    MODEL = "MODEL"
    VALIDATING = "VALIDATING"
    PUBLISHING = "PUBLISHING"
    DONE = "DONE"


class TurnKind(str, Enum):
    USER_QUESTION = "USER_QUESTION"
    PROVIDER_SELFTEST = "PROVIDER_SELFTEST"


class SessionState(str, Enum):
    OPEN = "OPEN"
    ARCHIVED = "ARCHIVED"
    EXPIRED = "EXPIRED"


class SubjectState(str, Enum):
    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"


class GrantApprovalState(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REVOKED = "REVOKED"


class ProjectionMode(str, Enum):
    """§4.4 方案甲投影模式。"""
    ALIASED = "ALIASED"
    SCHEMA_IDENTIFIERS = "SCHEMA_IDENTIFIERS"


class SqlValidation(str, Enum):
    """§5.4 纯文本 SQL 复核四态（服务端生成，模型不能自填）。"""
    INCOMPLETE_TEMPLATE = "INCOMPLETE_TEMPLATE"
    TEXT_ONLY_CHECKED = "TEXT_ONLY_CHECKED"
    TEXT_ONLY_REJECTED = "TEXT_ONLY_REJECTED"
    UNKNOWN = "UNKNOWN"


class FindingKind(str, Enum):
    FACT = "FACT"
    HYPOTHESIS = "HYPOTHESIS"
    POLICY = "POLICY"


class StepRisk(str, Enum):
    READ_ONLY = "READ_ONLY"
    MANUAL_CHANGE = "MANUAL_CHANGE"


class OutcomeClaimType(str, Enum):
    """§8.1.1 结果断言类型闭集。"""
    AUDIT_STATUS = "AUDIT_STATUS"
    SCAN_STATUS = "SCAN_STATUS"
    REPAIR_STATUS = "REPAIR_STATUS"
    ACCEPTANCE_STATUS = "ACCEPTANCE_STATUS"
    PERFORMANCE_DELTA = "PERFORMANCE_DELTA"


class ActionType(str, Enum):
    """§8.3 动作卡闭集（无 RUN_SQL/APPLY_PATCH 等执行类动作）。"""
    OPEN_SOURCE = "OPEN_SOURCE"
    NAVIGATE = "NAVIGATE"
    COPY_SUGGESTION = "COPY_SUGGESTION"
    OPEN_AUDIT_EDITOR = "OPEN_AUDIT_EDITOR"


#: §8.3 导航路由键闭集
ROUTE_KEY_CLOSURE = frozenset({
    "audit-sql", "file-audit", "schema-extractor-audit", "slow-tasks", "slow-records",
    "explain", "schema-check", "bigtable", "deep-diag-tabletype", "deep-diag-gateway",
    "rules", "sys-info",
})


class AnswerSource(str, Enum):
    MODEL = "MODEL"
    LOCAL_TEMPLATE = "LOCAL_TEMPLATE"


class KnowledgeStatus(str, Enum):
    READY = "READY"
    STALE = "STALE"
    INVALID = "INVALID"
    MISSING = "MISSING"


class ModuleSchemaState(str, Enum):
    READY = "READY"
    UNAVAILABLE = "UNAVAILABLE"


class AttemptStatus(str, Enum):
    STARTED = "STARTED"
    OK = "OK"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


class UsageSource(str, Enum):
    PROVIDER = "PROVIDER"
    UPPER_BOUND = "UPPER_BOUND"
    UNKNOWN = "UNKNOWN"


class AuditEventType(str, Enum):
    """§10.2 审计事件闭集。"""
    CONFIG_CHANGE = "CONFIG_CHANGE"
    GRANT_CHANGE = "GRANT_CHANGE"
    GRANT_REQUEST = "GRANT_REQUEST"
    GRANT_APPROVE = "GRANT_APPROVE"
    EMERGENCY_DISABLE = "EMERGENCY_DISABLE"
    PREVIEW = "PREVIEW"
    ACCEPT = "ACCEPT"
    CANCEL = "CANCEL"
    EGRESS_START = "EGRESS_START"
    EGRESS_END = "EGRESS_END"
    PUBLISH = "PUBLISH"
    ACCESS_DENIED = "ACCESS_DENIED"
    EXPORT = "EXPORT"
    FEEDBACK = "FEEDBACK"
    RETENTION = "RETENTION"


class FeedbackCode(str, Enum):
    INCORRECT = "INCORRECT"
    MISSING_CONTEXT = "MISSING_CONTEXT"
    HELPFUL = "HELPFUL"
    OTHER = "OTHER"


# ══════════════════════════════════════════════════════════════════
# 证据与来源（§4.1）
# ══════════════════════════════════════════════════════════════════


class Evidence(BaseModel):
    """§4.1 证据统一结构（值为协议结构，不是实际实例）。"""
    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(..., max_length=8)
    source_kind: SourceKind
    source_id: str = Field(..., max_length=128)
    source_revision: str = Field("", max_length=128)
    connection_id: Optional[str] = Field(None, max_length=128)
    connection_name: Optional[str] = Field(None, max_length=255)
    name_source: str = Field("missing", max_length=24)
    database: Optional[str] = Field(None, max_length=128)
    instance_type: str = Field("unknown", max_length=16)
    engine_version: Optional[str] = Field(None, max_length=64)
    proxy_version: Optional[str] = Field(None, max_length=64)
    observed_at: Optional[str] = None
    captured_at: Optional[str] = None
    availability: Availability = Availability.UNKNOWN
    completeness: Completeness = Completeness.UNKNOWN
    selected_count: Optional[int] = None
    total_count: Optional[int] = None
    truncated: bool = False
    reason_code: str = Field("", max_length=64)
    fact_keys: list[str] = Field(default_factory=list, max_length=40)
    data: dict[str, Any] = Field(default_factory=dict)


class KnowledgeRef(BaseModel):
    """知识片段引用（检索结果，K 编号）。"""
    model_config = ConfigDict(extra="forbid")

    knowledge_id: str = Field(..., max_length=8)
    bundle_id: str = Field(..., max_length=128)
    source_id: str = Field(..., max_length=128)
    title: str = Field("", max_length=256)
    section: str = Field("", max_length=128)
    authority: str = Field("USER_GUIDE", max_length=32)
    content: str = Field("", max_length=1500)
    content_hash: str = Field("", max_length=80)


# ══════════════════════════════════════════════════════════════════
# 模型输出 schema（§8.1）
# ══════════════════════════════════════════════════════════════════


class OutcomeClaim(BaseModel):
    """§8.1.1 结构化结果断言；模型不得自填事实值，由服务端核对。"""
    model_config = ConfigDict(extra="forbid")

    claim_type: OutcomeClaimType
    evidence_id: str = Field(..., max_length=8)
    fact_key: str = Field(..., max_length=64)
    subject_ref: str = Field("", max_length=128)
    observed_at: Optional[str] = None


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: FindingKind
    text: str = Field(..., max_length=600)
    evidence_ids: list[str] = Field(default_factory=list, max_length=12)
    knowledge_ids: list[str] = Field(default_factory=list, max_length=12)

    @field_validator("evidence_ids", "knowledge_ids")
    @classmethod
    def _dedupe(cls, v):
        seen, out = set(), []
        for x in v:
            if x not in seen:
                seen.add(x)
                out.append(x)
        return out


class SuggestedStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(..., max_length=600)
    risk: StepRisk = StepRisk.READ_ONLY
    evidence_ids: list[str] = Field(default_factory=list, max_length=12)


class SqlCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sql: str = Field(..., max_length=8192)
    reason: str = Field("", max_length=600)
    evidence_ids: list[str] = Field(default_factory=list, max_length=12)


class ModelAnswer(BaseModel):
    """§8.1 模型输出 schema：extra='forbid'，条数/长度上限。"""
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    summary: str = Field("", max_length=800)
    outcome_claims: list[OutcomeClaim] = Field(default_factory=list, max_length=8)
    findings: list[Finding] = Field(default_factory=list, max_length=8)
    steps: list[SuggestedStep] = Field(default_factory=list, max_length=8)
    missing_evidence: list[str] = Field(default_factory=list, max_length=5)
    sql_candidates: list[SqlCandidate] = Field(default_factory=list, max_length=2)
    limitations: list[str] = Field(default_factory=list, max_length=8)

    @field_validator("missing_evidence", "limitations")
    @classmethod
    def _limit_item_len(cls, v):
        return [x[:300] for x in v]


# ══════════════════════════════════════════════════════════════════
# 动作卡（§8.3，服务端白名单生成）
# ══════════════════════════════════════════════════════════════════


class ActionCard(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_id: str = Field(..., max_length=16)
    type: ActionType
    label: str = Field(..., max_length=64)
    evidence_id: Optional[str] = Field(None, max_length=8)
    route_key: Optional[str] = Field(None, max_length=48)
    source_ref: Optional[str] = Field(None, max_length=128)
    candidate_id: Optional[str] = Field(None, max_length=16)


# ══════════════════════════════════════════════════════════════════
# API 请求模型（§12，全部 extra='forbid'）
# ══════════════════════════════════════════════════════════════════


class SourceRefRule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str = "rule"
    rule_ids: list[str] = Field(..., max_length=10)


class SourceRefAuditHistory(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str = "audit_history"
    history_id: str = Field(..., max_length=64)
    statement_indexes: list[int] = Field(default_factory=list, max_length=5)


class SourceRefMetadataJob(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str = "metadata_job"
    job_id: str = Field(..., max_length=64)
    offset: int = Field(0, ge=0)
    limit: int = Field(10, ge=1, le=20)


class SourceRefSlowQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str = "slow_query"
    slow_id: str = Field(..., max_length=64)


class SourceRefScanSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str = "scan_snapshot"
    snapshot_ids: list[str] = Field(..., min_length=1, max_length=2)


class SourceRefTableTypeStat(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str = "table_type_stat"
    stat_id: str = Field(..., max_length=64)


class SourceRefGatewayReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str = "gateway_report"
    report_id: str = Field(..., max_length=64)


SOURCE_REF_MODELS = {
    "rule": SourceRefRule,
    "audit_history": SourceRefAuditHistory,
    "metadata_job": SourceRefMetadataJob,
    "slow_query": SourceRefSlowQuery,
    "scan_snapshot": SourceRefScanSnapshot,
    "table_type_stat": SourceRefTableTypeStat,
    "gateway_report": SourceRefGatewayReport,
}


class DraftInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str = Field(..., pattern="^(SQL|EXPLAIN|DIAGNOSTIC)$")
    text: str = Field(..., max_length=32768)
    revision: Optional[str] = Field(None, max_length=64)


class PreviewRequest(BaseModel):
    """§12.3 资料预览请求。"""
    model_config = ConfigDict(extra="forbid")

    expected_session_revision: int = Field(..., ge=0)
    scene: CopilotScene
    page_key: str = Field("", max_length=64)
    question: str = Field("", max_length=8192)
    public_question_id: Optional[str] = Field(None, max_length=64)
    source_refs: list[dict[str, Any]] = Field(default_factory=list, max_length=4)
    draft: Optional[DraftInput] = None
    include_recent_jobs: bool = False

    @field_validator("source_refs")
    @classmethod
    def _check_refs(cls, v):
        for ref in v:
            kind = ref.get("kind") if isinstance(ref, dict) else None
            model = SOURCE_REF_MODELS.get(kind or "")
            if model is None:
                raise ValueError(f"未知来源类型: {kind!r}")
            model.model_validate(ref)
        return v


class TurnSubmitRequest(BaseModel):
    """§12.4 提交请求（幂等）。"""
    model_config = ConfigDict(extra="forbid")

    client_request_id: str = Field(..., min_length=32, max_length=32,
                                   pattern="^[0-9a-f]{32}$")
    preview_id: str = Field(..., min_length=32, max_length=32,
                            pattern="^[0-9a-f]{32}$")
    snapshot_hash: str = Field(..., min_length=64, max_length=64,
                               pattern="^[0-9a-f]{64}$")
    expected_session_revision: int = Field(..., ge=0)
    confirm_data_use: bool = False


class SessionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope_kind: ScopeKind
    connection_id: Optional[str] = Field(None, max_length=128)
    database: Optional[str] = Field(None, max_length=128)
    instance_type: str = Field("unknown", pattern="^(distributed|centralized|unknown)$")
    page_key: str = Field("", max_length=64)


class FeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rating: int = Field(..., ge=-1, le=1)
    code: FeedbackCode

    @field_validator("rating")
    @classmethod
    def _rating_sign(cls, v):
        if v not in (-1, 1):
            raise ValueError("rating 只允许 -1 或 1")
        return v


class ActionResolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_id: str = Field(..., max_length=16)
    view_session_id: Optional[str] = Field(None, max_length=32)
    draft_revision: Optional[str] = Field(None, max_length=64)


# ══════════════════════════════════════════════════════════════════
# 管理端请求模型（§12.5）
# ══════════════════════════════════════════════════════════════════


class ProviderCapabilities(BaseModel):
    """§7.1 provider 能力契约（必填闭集）。"""
    model_config = ConfigDict(extra="forbid")

    context_tokens: int = Field(..., ge=1024, le=10_000_000)
    supports_json_schema: bool = False
    supports_json_object: bool = False
    max_output_field: str = Field("max_tokens", pattern="^(max_tokens|max_completion_tokens)$")
    supports_temperature: bool = False
    supports_store_false: bool = False


class EndpointCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    endpoint_id: str = Field(..., min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]{1,64}$")
    scheme: str = Field("https", pattern=r"^(https|http)$")
    canonical_host: str = Field(..., min_length=1, max_length=253)
    port: int = Field(..., ge=1, le=65535)
    base_path: str = Field("/v1", pattern=r"^/[A-Za-z0-9/_-]*$")
    data_zone: str = Field("INTERNAL", pattern=r"^(INTERNAL|PUBLIC)$")
    privacy_profile: str = Field("INTERNAL_REDACTED", pattern=r"^(INTERNAL_REDACTED|PUBLIC_HELP)$")
    allows_schema_identifiers: bool = True
    allowed_resolved_cidrs: list[str] = Field(default_factory=list)
    tls_ca_ref: str = Field("internal", max_length=64)
    description: Optional[str] = Field("", max_length=256)
    allow_http: Optional[bool] = None


class EndpointUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scheme: Optional[str] = Field(None, pattern=r"^(https|http)$")
    canonical_host: Optional[str] = Field(None, min_length=1, max_length=253)
    port: Optional[int] = Field(None, ge=1, le=65535)
    base_path: Optional[str] = Field(None, pattern=r"^/[A-Za-z0-9/_-]*$")
    data_zone: Optional[str] = Field(None, pattern=r"^(INTERNAL|PUBLIC)$")
    privacy_profile: Optional[str] = Field(None, pattern=r"^(INTERNAL_REDACTED|PUBLIC_HELP)$")
    allows_schema_identifiers: Optional[bool] = None
    allowed_resolved_cidrs: Optional[list[str]] = None
    tls_ca_ref: Optional[str] = Field(None, max_length=64)
    description: Optional[str] = Field(None, max_length=256)
    allow_http: Optional[bool] = None


class ProviderCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=128)
    endpoint_id: str = Field(..., max_length=64)
    protocol: str = Field("OPENAI_COMPAT_CHAT", pattern="^OPENAI_COMPAT_CHAT$")
    model_id: str = Field(..., min_length=1, max_length=128)
    auth_mode: str = Field(..., pattern="^(BEARER|BEARER_KEY|NETWORK_IDENTITY)$")
    capabilities: ProviderCapabilities
    secret_action: str = Field("KEEP", pattern="^(KEEP|REPLACE|CLEAR)$")
    secret: Optional[str] = Field(None, max_length=512)

    @field_validator("auth_mode")
    @classmethod
    def _normalize_auth_mode(cls, v: str) -> str:
        return "BEARER" if v == "BEARER_KEY" else v


class ProviderUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(..., ge=1)
    name: Optional[str] = Field(None, max_length=128)
    endpoint_id: Optional[str] = Field(None, max_length=64)
    model_id: Optional[str] = Field(None, max_length=128)
    auth_mode: Optional[str] = Field(None, pattern="^(BEARER|BEARER_KEY|NETWORK_IDENTITY)$")
    capabilities: Optional[ProviderCapabilities] = None
    secret_action: str = Field("KEEP", pattern="^(KEEP|REPLACE|CLEAR)$")
    secret: Optional[str] = Field(None, max_length=512)

    @field_validator("auth_mode")
    @classmethod
    def _normalize_auth_mode_update(cls, v: Optional[str]) -> Optional[str]:
        if v == "BEARER_KEY":
            return "BEARER"
        return v


class ProviderEnabledRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(..., ge=1)
    enabled: bool


class RoutePutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    primary_provider_id: Optional[str] = Field(None, max_length=32)
    fallback_provider_id: Optional[str] = Field(None, max_length=32)
    privacy_profile: str = Field("INTERNAL_REDACTED", max_length=32)
    expected_revision: int = Field(..., ge=0)


class GrantPutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: Optional[str] = Field("", max_length=128)
    connection_id: Optional[str] = Field("", max_length=128)
    usernames: list[str] = Field(default_factory=list)
    connection_ids: list[str] = Field(default_factory=list)
    intent: str = Field("GRANT", pattern="^(REQUEST|REVOKE|GRANT|DELETE|RESTORE)$")
    approval_ref: str = Field("", max_length=128)
    allow_schema_identifiers: bool = False
    identifier_approval_ref: Optional[str] = Field(None, max_length=128)
    expected_revision: int = Field(0, ge=0)


class GrantApproveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject_id: str = Field(..., min_length=32, max_length=32)
    connection_id: str = Field(..., min_length=1, max_length=128)
    expected_revision: int = Field(..., ge=1)


class GrantBatchApproveItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject_id: str = Field(..., min_length=32, max_length=32)
    connection_id: str = Field(..., min_length=1, max_length=128)
    expected_revision: int = Field(..., ge=1)


class GrantBatchApproveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    grants: list[GrantBatchApproveItem] = Field(..., min_length=1)


class GrantActionItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject_id: Optional[str] = Field(None, min_length=32, max_length=32)
    connection_id: Optional[str] = Field(None, min_length=1, max_length=128)
    username: Optional[str] = Field(None, max_length=128)


class GrantBatchActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    grants: list[GrantActionItem] = Field(default_factory=list)
    usernames: list[str] = Field(default_factory=list)
    connection_ids: list[str] = Field(default_factory=list)
    clear_revoked_only: bool = False


class SettingsPutRequest(BaseModel):
    """§10.2 runtime.settings_json 闭集字段；不得改部署硬上限。"""
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(..., ge=1)
    enabled: Optional[bool] = None
    allow_schema_identifiers: Optional[bool] = None
    user_daily_tokens: Optional[int] = Field(None, ge=1, le=10**12)
    global_daily_tokens: Optional[int] = Field(None, ge=1, le=10**13)
    session_retention_days: Optional[int] = Field(None, ge=1, le=90)
    audit_retention_days: Optional[int] = Field(None, ge=30, le=365)


class SelfTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_request_id: str = Field(..., min_length=32, max_length=32,
                                   pattern="^[0-9a-f]{32}$")
    expected_provider_revision: int = Field(..., ge=1)
