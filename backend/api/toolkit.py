"""G13 运维工具箱 API 路由"""
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pathlib import Path

router = APIRouter(prefix="/api/v1/toolkit", tags=["Ops Toolkit"])

SCRIPTS_ROOT = Path(__file__).parent.parent / "static" / "scripts"


@router.get("/scripts")
def get_available_scripts():
    """获取所有可用的运维脚本工具列表及使用指南"""
    return [
        {
            "id": "disk_perf_test",
            "name": "TDSQL 磁盘性能测试脚本 (fio/dd)",
            "category": "主机与磁盘诊断",
            "description": "用于物理或虚拟机节点上自动化进行磁盘 IO/吞吐量/延时的压测工具，支持一键配置免密并生成测试对比报告 HTML。",
            "files": [
                {"name": "disk_perf_test.sh", "path": "disk_performance_test/disk_perf_test.sh"},
                {"name": "generate_report.sh", "path": "disk_performance_test/generate_report.sh"},
                {"name": "setup_ssh_keys.sh", "path": "disk_performance_test/setup_ssh_keys.sh"}
            ],
            "command": "bash disk_perf_test.sh --hosts tdsql_hosts --type fio --duration 60",
            "note": "执行前请在 tdsql_hosts 文件中按格式配置所有待测物理节点的 IP 和凭证。"
        },
        {
            "id": "sshpass_pack",
            "name": "sshpass 批量执行运维包",
            "category": "批量命令执行",
            "description": "基于 sshpass 的集群多节点批量并行执行 Shell 命令/传输文件的便利工具，防挂死，支持密码/Key 认证方式。",
            "files": [
                {"name": "sshpass_pack_exec.sh", "path": "sshpass_pack/sshpass_pack_exec.sh"}
            ],
            "command": "bash sshpass_pack_exec.sh -f tdsql_hosts -c 'df -h'",
            "note": "用于运维人员快速下发指令、检查配置状态，请遵循企业内部安全合规规范。"
        }
    ]


@router.get("/download")
def download_script(file_path: str):
    """下载特定运维脚本"""
    # 严格的安全校验，防路径穿越
    safe_path = SCRIPTS_ROOT / file_path
    try:
        # resolve() 消除 .. 等相对路径符号，以防越界
        resolved_path = safe_path.resolve()
        if not str(resolved_path).startswith(str(SCRIPTS_ROOT.resolve())):
            raise HTTPException(status_code=403, detail="非法访问")
        
        if not resolved_path.exists() or not resolved_path.is_file():
            raise HTTPException(status_code=404, detail="脚本文件不存在")
            
        return FileResponse(
            path=resolved_path,
            filename=resolved_path.name,
            media_type="application/octet-stream"
        )
    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/run")
def trigger_tool_run(payload: dict, http_request: Request = None):
    """提交工具箱任务"""
    from backend.services.tool_bridge_service import tool_bridge_service
    tool_name = payload.get("tool_name", "generic_tool")
    conn_id = payload.get("connection_id", "")
    params = payload.get("params", {})
    operator = (getattr(http_request.state, "username", None) if http_request and hasattr(http_request, "state") else None) or payload.get("operator", "system")
    if isinstance(operator, dict):
        operator = operator.get("username") or "system"
    run_id = tool_bridge_service.create_run_task(tool_name, conn_id, params, str(operator))
    return {"status": "SUCCESS", "run_id": run_id, "message": "任务已提交调度"}


@router.get("/history")
def get_tool_run_history(limit: int = 20):
    """查询工具箱运行历史"""
    from backend.services.tool_bridge_service import tool_bridge_service
    return {"items": tool_bridge_service.get_run_history(limit)}

