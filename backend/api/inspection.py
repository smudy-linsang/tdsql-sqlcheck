"""
TDSQL SQL审核工具 - 巡检API (V1.0)
"""
from fastapi import APIRouter, HTTPException, Request
from typing import Optional

from backend.models import InspectionResultInfo, ApiResponse, SchemaCheckRequest
from backend.services.inspection_service import InspectionService

router = APIRouter(prefix="/api/v1/inspection", tags=["巡检管理"])
_service = InspectionService()


@router.post("/tasks", response_model=ApiResponse)
async def create_task(connection_id: str, inspection_type: str):
    """创建巡检任务"""
    task_id = _service.create_task(connection_id, inspection_type)
    return ApiResponse(data={"task_id": task_id})


@router.get("/tasks", response_model=ApiResponse)
async def list_tasks(connection_id: str = "", limit: int = 20):
    """列出巡检任务"""
    tasks = _service.list_tasks(connection_id, limit)
    return ApiResponse(data=tasks)


@router.get("/tasks/{task_id}", response_model=ApiResponse)
async def get_task(task_id: int):
    """获取巡检任务详情"""
    task = _service.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="巡检任务不存在")
    return ApiResponse(data=task)


@router.post("/tasks/{task_id}/status", response_model=ApiResponse)
async def update_task_status(task_id: int, status: str, error_message: str = ""):
    """更新巡检任务状态"""
    _service.update_task_status(task_id, status, error_message)
    return ApiResponse(message="任务状态已更新")


@router.post("/tasks/{task_id}/results", response_model=ApiResponse)
async def save_result(task_id: int, result: InspectionResultInfo):
    """保存巡检结果"""
    _service.save_result(task_id, result)
    return ApiResponse(message="巡检结果已保存")


@router.post("/schema-check", response_model=ApiResponse)
async def run_schema_check(request: SchemaCheckRequest, http_request: Request):
    """执行数据库上线前Schema检查（12项）

    对目标实例的全部非系统库执行上线前检查：
    字符集/排序规则、表名规范、索引数量、主键、字段长度、
    注释完整性、字段数量、timestamp类型等12项检查。

    替代原有的 tdsql_12.sh 脚本，实现Web界面一键检查。
    """
    from backend.services.connection_registry import registry
    from backend.engine.schema_inspector import SchemaInspector

    # 获取连接池
    pool = registry.get(request.connection_id)
    if not pool:
        raise HTTPException(status_code=400, detail="未找到指定实例连接，请先在实例管理中连接实例")

    # 创建巡检任务
    task_id = _service.create_task(request.connection_id, "schema_check")
    _service.update_task_status(task_id, "running")

    try:
        inspector = SchemaInspector()
        results = inspector.inspect(pool, request.database_filter)
        summary = inspector.get_summary(results)

        # 将结果保存到数据库
        for check_result in results:
            if check_result["count"] > 0:
                for row in check_result["rows"][:100]:  # 每项最多保存100条明细
                    msg_parts = []
                    for k, v in row.items():
                        msg_parts.append(f"{k}: {v}")
                    _service.save_result(task_id, InspectionResultInfo(
                        category=check_result["id"],
                        severity=check_result["severity"],
                        schema_name=str(row.get("数据库", "")),
                        table_name=str(row.get("表名", "")),
                        metric_name=check_result["name"],
                        metric_value=str(check_result["count"]),
                        message=" | ".join(msg_parts),
                        suggestion=check_result["suggestion"],
                    ))

        _service.update_task_status(task_id, "completed")
        return ApiResponse(data={
            "task_id": task_id,
            "summary": summary,
            "results": results,
        })
    except Exception as e:
        _service.update_task_status(task_id, "failed", str(e))
        raise HTTPException(status_code=500, detail=f"Schema检查执行失败: {e}")


@router.post("/schema-check/report")
async def export_schema_check_report(request: SchemaCheckRequest):
    """执行上线检查并导出HTML报告"""
    from backend.services.connection_registry import registry
    from backend.engine.schema_inspector import SchemaInspector
    from fastapi.responses import HTMLResponse
    from datetime import datetime

    pool = registry.get(request.connection_id)
    if not pool:
        raise HTTPException(status_code=400, detail="未找到指定实例连接")

    inspector = SchemaInspector()
    results = inspector.inspect(pool, request.database_filter)
    summary = inspector.get_summary(results)

    # 获取实例名称
    conn_name = f"{pool.config.host}:{pool.config.port}"
    try:
        saved = registry.list_saved()
        for c in saved:
            if c.get("host") == pool.config.host and c.get("port") == pool.config.port:
                conn_name = c.get("name", conn_name)
                break
    except Exception:
        pass

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 生成 HTML 报告
    severity_colors = {"ERROR": "#f56c6c", "WARNING": "#e6a23c", "INFO": "#909399"}
    rows_html = ""
    for check in results:
        color = severity_colors.get(check["severity"], "#909399")
        status = "通过" if check["count"] == 0 else f'{check["count"]}个问题'
        status_color = "#67c23a" if check["count"] == 0 else color
        error_note = f'<div style="color:#f56c6c;font-size:12px;margin-top:4px">执行失败: {check["error"]}</div>' if check.get("error") else ""

        table_html = ""
        if check.get("rows") and check["count"] > 0:
            cols = check.get("columns", [])
            ths = "".join(f"<th>{c}</th>" for c in cols)
            trs = ""
            for row in check["rows"][:200]:
                tds = "".join(f"<td>{row.get(c, '')}</td>" for c in cols)
                trs += f"<tr>{tds}</tr>"
            table_html = f'<table><thead><tr>{ths}</tr></thead><tbody>{trs}</tbody></table>'

        rows_html += f"""
        <div class="check-item">
          <div class="check-header">
            <span class="severity-tag" style="background:{color}">{check['severity']}</span>
            <span class="check-name">{check['id']} {check['name']}</span>
            <span class="check-count" style="color:{status_color}">{status}</span>
          </div>
          <div class="suggestion">修复建议：{check['suggestion']}</div>
          {error_note}
          {table_html}
        </div>"""

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>TDSQL上线检查报告 - {conn_name}</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:'Microsoft YaHei','PingFang SC',sans-serif;background:#f5f7fa;color:#303133;line-height:1.6;padding:20px}}
.container{{max-width:1100px;margin:0 auto}}
.header{{background:linear-gradient(135deg,#0f3460,#16213e);color:#fff;padding:32px;border-radius:12px;margin-bottom:24px}}
.header h1{{font-size:22px;margin-bottom:6px}}
.header .meta{{opacity:.8;font-size:13px}}
.summary{{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin-bottom:24px}}
.summary .card{{background:#fff;border-radius:8px;padding:20px;text-align:center;box-shadow:0 2px 8px rgba(0,0,0,.06)}}
.summary .num{{font-size:32px;font-weight:700}}
.summary .label{{font-size:12px;color:#909399;margin-top:4px}}
.check-item{{background:#fff;border-radius:8px;padding:16px 20px;margin-bottom:12px;box-shadow:0 1px 4px rgba(0,0,0,.04)}}
.check-header{{display:flex;align-items:center;gap:10px;margin-bottom:8px}}
.severity-tag{{color:#fff;padding:2px 10px;border-radius:10px;font-size:11px;font-weight:600}}
.check-name{{font-weight:600;font-size:14px}}
.check-count{{margin-left:auto;font-size:13px;font-weight:600}}
.suggestion{{background:#f0f9eb;border-left:4px solid #67c23a;padding:6px 12px;font-size:13px;border-radius:4px;margin-bottom:8px}}
table{{width:100%;border-collapse:collapse;font-size:13px;margin-top:8px}}
th{{background:#f5f7fa;padding:6px 10px;text-align:left;border-bottom:2px solid #e4e7ed;font-weight:600}}
td{{padding:6px 10px;border-bottom:1px solid #ebeef5}}
tr:hover td{{background:#f5f7fa}}
footer{{text-align:center;color:#909399;font-size:12px;padding:20px 0;border-top:1px solid #ebeef5;margin-top:20px}}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>TDSQL数据库上线检查报告</h1>
    <div class="meta">实例：{conn_name} &nbsp;|&nbsp; 检查时间：{now} &nbsp;|&nbsp; 检查项：12项</div>
  </div>
  <div class="summary">
    <div class="card"><div class="num">{summary['total']}</div><div class="label">问题总数</div></div>
    <div class="card"><div class="num" style="color:#f56c6c">{summary['error']}</div><div class="label">ERROR</div></div>
    <div class="card"><div class="num" style="color:#e6a23c">{summary['warning']}</div><div class="label">WARNING</div></div>
    <div class="card"><div class="num" style="color:#909399">{summary['info']}</div><div class="label">INFO</div></div>
    <div class="card"><div class="num" style="color:#67c23a">{summary['checks_passed']}</div><div class="label">通过项</div></div>
  </div>
  {rows_html}
  <footer>TDSQL数据库SQL审核工具 V1.0.2 &nbsp;|&nbsp; 报告生成时间：{now}</footer>
</div>
</body>
</html>"""
    return HTMLResponse(content=html)
