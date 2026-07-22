"""
TDSQL SQL审核工具 - 慢SQL API

提供慢SQL的查询、分析和管理接口。
"""
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from backend.engine.slow_analyzer import SlowQueryRecord
from backend.services.slow_query_service import SlowQueryService
from backend.services.database import _get_connection

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
def create_slow_query(request: SlowQueryCreateRequest):
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
def list_slow_queries(
    db_name: Optional[str] = None,
    status: Optional[str] = None,
    severity: Optional[str] = None,
    scan_task_id: Optional[int] = None,
    set_id: Optional[str] = None,
    keyword: Optional[str] = None,
    created_by: Optional[str] = None,
    task_name: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
):
    """
    获取慢SQL列表，支持按数据库名、SET、状态、严重程度、扫描任务、关键词、操作者、任务名筛选。
    """
    return service.get_slow_queries(
        db_name=db_name,
        status=status,
        severity=severity,
        scan_task_id=scan_task_id,
        set_id=set_id,
        keyword=keyword,
        created_by=created_by,
        task_name=task_name,
        limit=limit,
        offset=offset,
    )


@router.get("/statistics", summary="获取慢SQL统计信息")
def get_statistics():
    """
    获取慢SQL的统计概览，包括Top高耗时和高频次SQL。
    """
    return service.get_statistics()


@router.get("/scan-tasks", summary="获取扫描任务列表")
def list_scan_tasks(limit: int = 50, offset: int = 0):
    """获取所有慢SQL扫描任务列表"""
    return service.get_scan_tasks(limit=limit, offset=offset)


@router.get("/scan-tasks/{task_id}", summary="获取扫描任务详情")
def get_scan_task_detail(task_id: int):
    """获取指定扫描任务的详情，含统计摘要"""
    detail = service.get_scan_task_detail(task_id)
    if not detail:
        raise HTTPException(status_code=404, detail="扫描任务不存在")
    return detail


@router.delete("/scan-tasks/{task_id}", summary="删除扫描任务")
def delete_scan_task(task_id: int, request: Request):
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


@router.delete("/orphan-records", summary="清理无任务关联的慢SQL记录")
def delete_orphan_records():
    """
    删除 scan_task_id 为 NULL 的慢SQL记录（手动录入或历史迁移数据）。
    仅管理员可操作。
    """
    conn = _get_connection()
    try:
        cursor = conn.execute(
            "DELETE FROM slow_queries WHERE scan_task_id IS NULL"
        )
        conn.commit()
        deleted = cursor.rowcount
        return {"message": f"已清理 {deleted} 条无任务关联的慢SQL记录", "deleted": deleted}
    finally:
        conn.close()


@router.get("/db-names", summary="获取数据库名列表")
def list_db_names():
    """获取所有慢SQL记录中出现的数据库名列表（用于筛选下拉框）"""
    return {"db_names": service.get_db_names()}


@router.get("/set-ids", summary="获取SET ID列表")
def list_set_ids():
    """获取所有慢SQL记录中出现的 SET ID 列表（用于筛选下拉框）"""
    return {"set_ids": service.get_set_ids()}


@router.get("/cross-set-analysis", summary="跨SET对比分析")
def cross_set_analysis(scan_task_id: Optional[int] = None):
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
def get_slow_query_detail(slow_id: int):
    """获取指定慢SQL的详细分析结果"""
    detail = service.get_slow_query_detail(slow_id)
    if not detail:
        raise HTTPException(status_code=404, detail="慢SQL记录不存在")
    return detail


@router.put("/{slow_id}/status", summary="更新慢SQL状态")
def update_status(slow_id: int, request: StatusUpdateRequest):
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


# ============ 扫描任务HTML报告 ============

@router.get("/scan-tasks/{task_id}/html", summary="下载扫描任务HTML报告")
async def export_scan_task_html(task_id: int):
    """生成并下载指定扫描任务的HTML报告"""
    import json
    from datetime import datetime
    from urllib.parse import quote
    from fastapi.responses import HTMLResponse
    from backend.services.database import ensure_db

    try:
        ensure_db()
        conn = _get_connection()
        try:
            task = conn.execute(
                "SELECT * FROM scan_tasks WHERE id = %s", (task_id,)
            ).fetchone()
            if not task:
                raise HTTPException(status_code=404, detail="扫描任务不存在")
            task = dict(task)
            # 获取该任务下的慢SQL记录
            rows = conn.execute(
                "SELECT * FROM slow_queries WHERE scan_task_id = %s ORDER BY avg_time_ms DESC",
                (task_id,),
            ).fetchall()
            slow_queries = []
            for row in rows:
                item = dict(row)
                if item.get("analysis_json"):
                    try:
                        item["analyses"] = json.loads(item["analysis_json"])
                    except Exception:
                        item["analyses"] = []
                slow_queries.append(item)
        finally:
            conn.close()

        created_at = str(task.get("created_at", ""))
        time_display = created_at[:19].replace("T", " ") if created_at else ""
        source_label = {"digest": "性能摘要", "processlist": "实时进程", "manual": "手动录入"}.get(task.get("source", ""), task.get("source", ""))

        # 严重级别统计
        sev_stats = {}
        for sq in slow_queries:
            sev = sq.get("severity", "INFO")
            sev_stats[sev] = sev_stats.get(sev, 0) + 1

        html_parts = []
        html_parts.append(f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>TDSQL慢SQL扫描报告 - {task.get('task_name', '')}</title>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family:"Microsoft YaHei","Segoe UI",Arial,sans-serif; background:#f0f2f5; color:#303030; padding:20px; }}
.container {{ max-width:1000px; margin:0 auto; background:#fff; border-radius:8px; box-shadow:0 2px 12px rgba(0,0,0,0.08); overflow:hidden; }}
.header {{ background:#1a1a2e; color:#fff; padding:24px 32px; }}
.header h1 {{ font-size:22px; margin-bottom:6px; }}
.header .sub {{ font-size:13px; color:#a0aec0; }}
.meta {{ display:flex; flex-wrap:wrap; gap:24px; padding:20px 32px; background:#f7f8fa; border-bottom:1px solid #ebeef5; }}
.meta-item {{ font-size:14px; }}
.meta-item .label {{ color:#909399; margin-right:6px; }}
.meta-item .value {{ font-weight:600; }}
.summary {{ display:flex; gap:16px; padding:24px 32px; flex-wrap:wrap; }}
.sc {{ flex:1; min-width:100px; text-align:center; padding:16px; border-radius:6px; }}
.sc.total {{ background:#e8f4fd; }} .sc.crit {{ background:#fde8e8; }} .sc.warn {{ background:#fdf6e8; }} .sc.info {{ background:#f0f4f8; }}
.sc .num {{ font-size:28px; font-weight:700; }} .sc .lbl {{ font-size:12px; color:#606266; margin-top:4px; }}
.stitle {{ padding:16px 32px 8px; font-size:16px; font-weight:600; border-top:1px solid #ebeef5; }}
.sql-item {{ margin:0 32px 16px; padding:16px; border:1px solid #ebeef5; border-radius:6px; }}
.sql-item .sh {{ display:flex; justify-content:space-between; align-items:center; margin-bottom:8px; }}
.sql-text {{ font-family:Consolas,Courier New,monospace; font-size:13px; background:#f5f7fa; padding:8px 12px; border-radius:4px; margin:8px 0; white-space:pre-wrap; word-break:break-all; }}
.badge {{ display:inline-block; padding:2px 8px; border-radius:10px; font-size:11px; font-weight:600; }}
.badge.ERROR {{ background:#fde8e8; color:#f56c6c; }} .badge.CRITICAL {{ background:#fde8e8; color:#f56c6c; }} .badge.WARNING {{ background:#fdf6e8; color:#e6a23c; }} .badge.INFO {{ background:#e8f4fd; color:#409eff; }}
.stats-row {{ display:flex; gap:16px; flex-wrap:wrap; margin:4px 0; }}
.stats-row span {{ font-size:13px; color:#606266; }}
.viol {{ margin:6px 0; padding:8px 12px; border-left:3px solid #f56c6c; background:#fef0f0; border-radius:0 4px 4px 0; font-size:13px; }}
.footer {{ padding:16px 32px; text-align:center; font-size:12px; color:#909399; border-top:1px solid #ebeef5; }}
.no-data {{ padding:32px; text-align:center; color:#909399; }}
</style></head><body>
<div class="container">
<div class="header"><h1>TDSQL SQL审核平台 - 慢SQL扫描报告</h1><div class="sub">TDSQL SQL Audit Platform / Slow Query Scan Report</div></div>
<div class="meta">
<div class="meta-item"><span class="label">任务名称:</span><span class="value">{task.get('task_name', '-')}</span></div>
<div class="meta-item"><span class="label">数据源:</span><span class="value">{source_label}</span></div>
<div class="meta-item"><span class="label">操作人:</span><span class="value">{task.get('created_by', '匿名')}</span></div>
<div class="meta-item"><span class="label">扫描时间:</span><span class="value">{time_display}</span></div>
<div class="meta-item"><span class="label">实例:</span><span class="value">{task.get('connection_name', '-')}</span></div>
<div class="meta-item"><span class="label">时间窗口:</span><span class="value">{task.get('time_window_start', '-')} ~ {task.get('time_window_end', '-')}</span></div>
<div class="meta-item"><span class="label">报告ID:</span><span class="value">#{task.get('id')}</span></div>
</div>
<div class="summary">
<div class="sc total"><div class="num">{len(slow_queries)}</div><div class="lbl">慢SQL总数</div></div>
<div class="sc crit"><div class="num" style="color:#f56c6c">{sev_stats.get('ERROR', 0) + sev_stats.get('CRITICAL', 0)}</div><div class="lbl">ERROR</div></div>
<div class="sc warn"><div class="num" style="color:#e6a23c">{sev_stats.get('WARNING', 0)}</div><div class="lbl">WARNING</div></div>
<div class="sc info"><div class="num" style="color:#409eff">{sev_stats.get('INFO', 0)}</div><div class="lbl">INFO</div></div>
</div>
<div class="stitle">逐条慢SQL详情（共 {len(slow_queries)} 条）</div>""")

        if not slow_queries:
            html_parts.append('<div class="no-data">本次扫描未抓取到慢SQL记录</div>')
        else:
            for i, sq in enumerate(slow_queries, 1):
                sev = sq.get("severity", "INFO")
                fingerprint = (sq.get("fingerprint") or "")[:500]
                sql_text = (sq.get("sql_text") or "")[:500]
                avg_time = sq.get("avg_time_ms", 0)
                exec_count = sq.get("exec_count", 0)
                rows_examined = sq.get("rows_examined", 0)
                db = sq.get("db_name", "")
                set_id = sq.get("set_id", "")
                analyses = sq.get("analyses", [])
                problem = sq.get("problem_type", "")

                html_parts.append(f'<div class="sql-item"><div class="sh"><span><strong>#{i}</strong> [{db}] SET:{set_id}</span><span class="badge {sev}">{sev}</span></div>')
                html_parts.append(f'<div class="stats-row"><span>平均耗时: <strong>{avg_time:.1f}ms</strong></span><span>执行次数: <strong>{exec_count}</strong></span><span>扫描行数: <strong>{rows_examined}</strong></span></div>')
                if problem:
                    html_parts.append(f'<div style="font-size:13px;color:#606266;margin:4px 0">问题类型: {problem}</div>')
                html_parts.append(f'<div class="sql-text">{sql_text}</div>')
                for a in analyses:
                    a_type = a.get("problem_type", a.get("type", ""))
                    a_msg = a.get("evidence", a.get("message", ""))
                    a_cause = a.get("root_cause", "")
                    a_sug = a.get("suggestion", "")
                    html_parts.append(f'<div class="viol"><strong>{a_type}</strong>:{a_msg}')
                    if a_cause:
                        html_parts.append(f'<br>根因: {a_cause}')
                    if a_sug:
                        html_parts.append(f'<br><span style="color:#67c23a">建议: {a_sug}</span>')
                    html_parts.append('</div>')
                html_parts.append('</div>')

        html_parts.append(f'<div class="footer">TDSQL SQL审核平台 V3.0 | 报告生成时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")} | 任务ID: #{task.get("id")}</div></div></body></html>')
        html = "\n".join(html_parts)

        filename = f"TDSQL慢SQL扫描报告_{task.get('task_name', 'task')}_{time_display[:10]}.html"
        encoded_filename = quote(filename)
        return HTMLResponse(
            content=html,
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}"}
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"HTML报告生成失败: {str(e)}")
