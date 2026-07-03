"""
TDSQL SQL审核工具 - 慢SQL API

提供慢SQL的查询、分析和管理接口。
"""
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from backend.engine.slow_analyzer import SlowQueryRecord
from backend.services.slow_query_service import SlowQueryService

router = APIRouter(prefix="/api/v1/slow-queries", tags=["慢SQL分析"])
service = SlowQueryService()


# ============ 请求/响应模型 ============

class SlowQueryCreateRequest(BaseModel):
    """创建慢SQL记录请求"""
    fingerprint: str = Field(..., description="SQL指纹（去除参数值后的SQL模板）")
    sql_text: str = Field(..., description="原始SQL文本")
    db_name: str = Field("", description="数据库名")
    exec_count: int = Field(0, description="执行次数")
    total_time_ms: float = Field(0, description="总耗时(ms)")
    avg_time_ms: float = Field(0, description="平均耗时(ms)")
    max_time_ms: float = Field(0, description="最大耗时(ms)")
    rows_examined: int = Field(0, description="扫描行数")
    rows_sent: int = Field(0, description="返回行数")
    lock_time_ms: float = Field(0, description="锁等待时间(ms)")


class ExplainRowRequest(BaseModel):
    """EXPLAIN行数据"""
    id: int = Field(0)
    select_type: str = Field("")
    table: str = Field("")
    type: str = Field("")
    possible_keys: Optional[str] = Field("")
    key: Optional[str] = Field("")
    key_len: Optional[str] = Field("")
    ref: Optional[str] = Field("")
    rows: int = Field(0)
    filtered: float = Field(100.0)
    extra: str = Field("")


class ExplainAnalyzeRequest(BaseModel):
    """EXPLAIN分析请求（JSON模式）"""
    explain_data: list[ExplainRowRequest] = Field(..., description="EXPLAIN输出数据")


class ExplainBySqlRequest(BaseModel):
    """直接用SQL语句分析EXPLAIN"""
    sql: str = Field(..., description="要分析的SQL语句")
    connection_id: str = Field(..., description="已保存的TDSQL连接ID")


class StatusUpdateRequest(BaseModel):
    """状态更新请求"""
    status: str = Field(..., description="新状态: pending/optimized/ignored")


# ============ API路由 ============

@router.post("", summary="添加慢SQL记录并自动分析")
async def create_slow_query(request: SlowQueryCreateRequest):
    """
    添加一条慢SQL记录，系统会自动进行分析并给出优化建议。
    """
    record = SlowQueryRecord(
        fingerprint=request.fingerprint,
        sql_text=request.sql_text,
        db_name=request.db_name,
        exec_count=request.exec_count,
        total_time_ms=request.total_time_ms,
        avg_time_ms=request.avg_time_ms,
        max_time_ms=request.max_time_ms,
        rows_examined=request.rows_examined,
        rows_sent=request.rows_sent,
        lock_time_ms=request.lock_time_ms,
    )
    result = service.add_slow_query(record)
    return result


@router.get("", summary="获取慢SQL列表")
async def list_slow_queries(
    db_name: Optional[str] = None,
    status: Optional[str] = None,
    severity: Optional[str] = None,
    scan_task_id: Optional[int] = None,
    set_id: Optional[str] = None,
    keyword: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
):
    """
    获取慢SQL列表，支持按数据库名、SET、状态、严重程度、扫描任务、关键词筛选。
    """
    return service.get_slow_queries(
        db_name=db_name,
        status=status,
        severity=severity,
        scan_task_id=scan_task_id,
        set_id=set_id,
        keyword=keyword,
        limit=limit,
        offset=offset,
    )


@router.get("/statistics", summary="获取慢SQL统计信息")
async def get_statistics():
    """
    获取慢SQL的统计概览，包括Top高耗时和高频次SQL。
    """
    return service.get_statistics()


@router.get("/scan-tasks", summary="获取扫描任务列表")
async def list_scan_tasks(limit: int = 50, offset: int = 0):
    """获取所有慢SQL扫描任务列表"""
    return service.get_scan_tasks(limit=limit, offset=offset)


@router.get("/scan-tasks/{task_id}", summary="获取扫描任务详情")
async def get_scan_task_detail(task_id: int):
    """获取指定扫描任务的详情，含统计摘要"""
    detail = service.get_scan_task_detail(task_id)
    if not detail:
        raise HTTPException(status_code=404, detail="扫描任务不存在")
    return detail


@router.delete("/scan-tasks/{task_id}", summary="删除扫描任务")
async def delete_scan_task(task_id: int, request: Request):
    """
    删除扫描任务及其关联的慢SQL记录。

    权限: 只有管理员或任务创建者可以删除。
    """
    operator = getattr(request.state, "username", "anonymous")
    role = getattr(request.state, "role", "")
    is_admin = role == "admin"
    success, err = service.delete_scan_task(task_id, operator, is_admin)
    if not success:
        raise HTTPException(status_code=403 if "无权" in err else 404, detail=err)
    return {"message": "扫描任务已删除"}


@router.get("/db-names", summary="获取数据库名列表")
async def list_db_names():
    """获取所有慢SQL记录中出现的数据库名列表（用于筛选下拉框）"""
    return {"db_names": service.get_db_names()}


@router.get("/set-ids", summary="获取SET ID列表")
async def list_set_ids():
    """获取所有慢SQL记录中出现的 SET ID 列表（用于筛选下拉框）"""
    return {"set_ids": service.get_set_ids()}


@router.get("/cross-set-analysis", summary="跨SET对比分析")
async def cross_set_analysis(scan_task_id: Optional[int] = None):
    """
    跨 SET 对比分析，需指定扫描任务ID。

    分析维度:
    - 各 SET 的慢 SQL 分布（总量/严重程度）
    - 热点 SET 识别（慢 SQL 远超平均水平的 SET）
    - 跨 SET 共现 SQL（在多个 SET 上都出现的慢 SQL）
    - 顾问建议
    """
    if not scan_task_id:
        raise HTTPException(status_code=400, detail="请指定 scan_task_id 进行跨SET分析")
    return service.get_cross_set_analysis(scan_task_id=scan_task_id)


@router.get("/{slow_id}", summary="获取慢SQL详情")
async def get_slow_query_detail(slow_id: int):
    """获取指定慢SQL的详细分析结果"""
    detail = service.get_slow_query_detail(slow_id)
    if not detail:
        raise HTTPException(status_code=404, detail="慢SQL记录不存在")
    return detail


@router.put("/{slow_id}/status", summary="更新慢SQL状态")
async def update_status(slow_id: int, request: StatusUpdateRequest):
    """更新慢SQL的处理状态"""
    if request.status not in ("pending", "optimized", "ignored"):
        raise HTTPException(status_code=400, detail="状态值无效，可选: pending/optimized/ignored")
    success = service.update_status(slow_id, request.status)
    if not success:
        raise HTTPException(status_code=404, detail="慢SQL记录不存在")
    return {"message": "状态更新成功", "status": request.status}


@router.post("/analyze-explain", summary="分析EXPLAIN执行计划（JSON模式）")
async def analyze_explain(request: ExplainAnalyzeRequest):
    """
    传入EXPLAIN输出数据（JSON格式），自动分析执行计划并给出优化建议。

    使用方式：
    1. 在MySQL中执行 EXPLAIN SELECT ... 
    2. 将结果填入explain_data数组
    3. 调用此接口获取分析报告
    """
    explain_data = [row.model_dump() for row in request.explain_data]
    return service.analyze_explain(explain_data)


@router.post("/analyze-explain-by-sql", summary="直接用SQL语句分析EXPLAIN")
async def analyze_explain_by_sql(request: ExplainBySqlRequest):
    """
    直接传入SQL语句，系统自动连接目标数据库执行EXPLAIN并分析。

    使用方式：
    1. 在TDSQL管理中保存一个数据库连接
    2. 选择该连接，输入要分析的SQL语句
    3. 系统自动执行 EXPLAIN 并返回分析报告
    """
    try:
        return service.analyze_explain_by_sql(
            sql=request.sql,
            connection_id=request.connection_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=_friendly_error(e))


def _friendly_error(e: Exception) -> str:
    """将MySQL/TDSQL错误转换为中文提示"""
    msg = str(e)
    errno = getattr(e, 'args', [None])[0] if e.args else None

    # pymysql错误: e.args = (errno, message)
    if isinstance(e.args, tuple) and len(e.args) >= 2:
        errno = e.args[0]
        msg = e.args[1]

    error_map = {
        1045: "数据库用户名或密码错误，请检查连接配置",
        1049: "数据库不存在，请检查连接配置中的数据库名",
        1146: "表不存在，请检查SQL中的表名或确认选择的数据库连接是否正确",
        1054: "SQL中存在未知的列名，请检查字段拼写",
        1064: "SQL语法错误，请检查SQL语句是否完整正确",
        1102: "数据库名称格式错误",
        1105: "目标数据库执行出错，请稍后重试",
        1142: "当前用户没有查询该表的权限",
        1227: "权限不足，请联系数据库管理员",
        2003: "无法连接到数据库服务器，请检查主机地址和端口是否正确",
        2006: "MySQL服务器已不可用，连接可能已断开",
        2013: "连接在查询期间丢失，数据库服务器可能已重启",
    }

    if errno in error_map:
        return error_map[errno]

    # 通配匹配
    if "doesn't exist" in msg or "not exist" in msg.lower():
        return "表或数据库不存在，请检查SQL中的表名及所选连接对应的数据库"
    if "Access denied" in msg:
        return "数据库访问被拒绝，请检查用户名和密码"
    if "syntax" in msg.lower():
        return "SQL语法错误，请检查SQL语句格式是否正确"
    if "Connection refused" in msg or "Can't connect" in msg:
        return "无法连接到数据库服务器，请检查地址和端口"
    if "timed out" in msg.lower() or "timeout" in msg.lower():
        return "连接数据库超时，请检查网络或稍后重试"
    if "Unknown column" in msg:
        return "SQL中引用了不存在的列，请检查字段名"

    return f"EXPLAIN执行失败: {msg}"
