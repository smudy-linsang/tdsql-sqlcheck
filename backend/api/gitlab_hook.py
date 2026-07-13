"""
TDSQL SQL审核工具 - GitLab Webhook API

集成GitLab实现开发阶段的SQL自动审核。

功能：
1. 接收GitLab Merge Request Webhook
2. 解析变更文件中的SQL（MyBatis XML、SQL脚本）
3. 自动执行SQL审核并返回结果
4. 支持GitLab Pipeline集成
"""
import hmac
import json
import os
import re
from typing import Optional

import httpx
from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

from backend.engine.checker import RuleChecker
from backend.models import AuditResult, AuditSummary

router = APIRouter(prefix="/api/v1/gitlab", tags=["GitLab集成"])

# 配置（从环境变量读取）
GITLAB_WEBHOOK_SECRET = os.getenv("GITLAB_WEBHOOK_SECRET", "")
GITLAB_API_URL = os.getenv("GITLAB_API_URL", "https://gitlab.com")
GITLAB_API_TOKEN = os.getenv("GITLAB_API_TOKEN", "")
checker = RuleChecker()


# ============ 数据模型 ============

class AuditWebhookResult(BaseModel):
    """Webhook审核结果"""
    merge_request_id: Optional[int] = None
    project_name: str = ""
    total_files: int = 0
    total_sql: int = 0
    passed: int = 0
    failed: int = 0
    pass_rate: float = 0.0
    has_critical: bool = False
    results: list = []
    summary: str = ""


# ============ 辅助函数 ============

def _verify_gitlab_token(token_header: Optional[str]) -> bool:
    """
    验证GitLab Webhook Token。

    V2.0 安全加固: 未配置 GITLAB_WEBHOOK_SECRET 时默认拒绝 webhook 请求
    （生产要求），仅当显式设置 GITLAB_WEBHOOK_ALLOW_INSECURE=true 时放行
    （仅限开发/测试环境）。
    """
    secret = os.getenv("GITLAB_WEBHOOK_SECRET", "") or GITLAB_WEBHOOK_SECRET
    if not secret:
        from backend import config as app_config
        return app_config.gitlab_webhook_allow_insecure()
    if not token_header:
        return False
    return hmac.compare_digest(token_header, secret)


def _is_sql_related_file(file_path: str) -> bool:
    """判断文件是否为SQL相关文件"""
    sql_patterns = [
        r"\.xml$",
        r"\.sql$",
        r"Mapper\.xml$",
    ]
    return any(re.search(p, file_path, re.IGNORECASE) for p in sql_patterns)


def _extract_sql_from_diff(diff_content: str, file_path: str) -> list:
    """
    从Git diff内容中提取新增/修改的SQL。

    对于XML文件，提取新增的SQL标签内容。
    对于SQL文件，提取新增的SQL语句。
    """
    results = []

    if not diff_content:
        return results

    # 提取新增行（以 + 开头，排除 +++ 行）
    added_lines = []
    for line in diff_content.split("\n"):
        if line.startswith("+") and not line.startswith("+++"):
            added_lines.append(line[1:])

    if not added_lines:
        return results

    added_content = "\n".join(added_lines)

    if file_path.lower().endswith(".xml"):
        # MyBatis XML: 提取SQL标签中的内容
        sqls = checker._extract_sql_from_mybatis(added_content)
        for sql_text, line_no in sqls:
            results.append({"sql": sql_text, "file": file_path, "line": line_no})
    elif file_path.lower().endswith(".sql"):
        # SQL文件: 按分号分割
        sqls = checker._split_sql_file(added_content)
        for sql_text, line_no in sqls:
            results.append({"sql": sql_text, "file": file_path, "line": line_no})

    return results


def _audit_sql_list(sql_items: list) -> tuple:
    """批量审核SQL"""
    results = []
    for item in sql_items:
        result = checker.audit_sql(
            sql=item.get("sql", ""),
            file_path=item.get("file", ""),
            line_number=item.get("line"),
        )
        results.append(result)
    summary = checker.compute_summary(results)
    return results, summary


def _format_mr_comment(results: list, summary: AuditSummary, project_name: str) -> str:
    """生成Merge Request评论内容"""
    status = "✅ 审核通过" if summary.failed == 0 else "❌ 审核未通过"

    lines = [
        f"## 🔍 SQL审核报告 {status}",
        "",
        f"**项目**: {project_name}",
        f"**SQL总数**: {summary.total_sql} | **通过**: {summary.passed} | **未通过**: {summary.failed} | **通过率**: {summary.pass_rate}%",
        "",
    ]

    if summary.error_count > 0:
        lines.append(f"### 🔴 ERROR级别问题 ({summary.error_count}个)")
        lines.append("")
        for result in results:
            for v in result.violations:
                if v.severity == "ERROR":
                    file_info = f"**{result.file_path}**" if result.file_path else ""
                    line_info = f"L{v.line_number}" if v.line_number else ""
                    lines.append(f"- [{v.rule_id}] {file_info}:{line_info} - {v.message}")
                    if v.suggestion:
                        lines.append(f"  > 💡 {v.suggestion}")
        lines.append("")

    if summary.warning_count > 0:
        lines.append(f"### 🟡 WARNING级别问题 ({summary.warning_count}个)")
        lines.append("")
        for result in results:
            for v in result.violations:
                if v.severity == "WARNING":
                    file_info = f"**{result.file_path}**" if result.file_path else ""
                    line_info = f"L{v.line_number}" if v.line_number else ""
                    lines.append(f"- [{v.rule_id}] {file_info}:{line_info} - {v.message}")
        lines.append("")

    lines.append("---")
    lines.append("*由 TDSQL SQL审核工具 自动生成*")

    return "\n".join(lines)


def _post_mr_comment(project_id: int, mr_iid: int, comment_body: str) -> dict:
    """
    将审核报告评论回写到 GitLab Merge Request。

    使用 GitLab API v4: POST /api/v4/projects/:id/merge_requests/:iid/notes

    Args:
        project_id: GitLab 项目 ID（从 Webhook payload 中提取）
        mr_iid: Merge Request 的 IID（从 Webhook payload 中提取）
        comment_body: 评论内容（Markdown）

    Returns:
        包含结果信息的字典
    """
    if not GITLAB_API_TOKEN:
        return {"posted": False, "reason": "GITLAB_API_TOKEN 未配置"}

    if not project_id or not mr_iid:
        return {"posted": False, "reason": "project_id 或 mr_iid 缺失"}

    api_url = f"{GITLAB_API_URL.rstrip('/')}/api/v4"
    headers = {
        "PRIVATE-TOKEN": GITLAB_API_TOKEN,
        "Content-Type": "application/json",
    }
    payload = {
        "body": comment_body,
    }

    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.post(
                f"{api_url}/projects/{project_id}/merge_requests/{mr_iid}/notes",
                headers=headers,
                json=payload,
            )
        if response.status_code in (200, 201):
            return {"posted": True, "comment_id": response.json().get("id")}
        elif response.status_code == 401:
            return {"posted": False, "reason": "GitLab API Token 无效或已过期"}
        elif response.status_code == 404:
            return {"posted": False, "reason": f"未找到 MR #{mr_iid}，请检查 project_id 和 mr_iid 是否正确"}
        else:
            return {
                "posted": False,
                "reason": f"GitLab API 返回错误: {response.status_code} - {response.text[:200]}",
            }
    except ImportError:
        return {"posted": False, "reason": "httpx 未安装，请执行: pip install httpx"}
    except Exception as e:
        return {"posted": False, "reason": f"评论回写失败: {str(e)}"}


def _fetch_mr_changes(project_id: int, mr_iid: int) -> list:
    """从 GitLab API 获取 Merge Request 的变更文件列表"""
    if not GITLAB_API_TOKEN:
        return []
    api_url = f"{GITLAB_API_URL.rstrip('/')}/api/v4"
    headers = {
        "PRIVATE-TOKEN": GITLAB_API_TOKEN,
    }
    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.get(
                f"{api_url}/projects/{project_id}/merge_requests/{mr_iid}/changes",
                headers=headers,
            )
        if response.status_code == 200:
            return response.json().get("changes", [])
        else:
            logger.warning("Failed to fetch MR changes: status_code=%d", response.status_code)
    except Exception as e:
        logger.warning("Failed to fetch MR changes from GitLab: %s", e)
    return []



# ============ API路由 ============

@router.post("/webhook/merge-request", summary="GitLab Merge Request Webhook")
async def handle_merge_request_webhook(
    request: Request,
    x_gitlab_token: Optional[str] = Header(None),
):
    """
    接收GitLab Merge Request Webhook事件。

    配置方式：
    1. 在GitLab项目 Settings > Webhooks 中添加此URL
    2. 设置Trigger为 "Merge request events"
    3. 可选设置Secret Token
    """
    body = await request.body()

    # 验证Token
    if not _verify_gitlab_token(x_gitlab_token):
        raise HTTPException(status_code=403, detail="Invalid webhook token")

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    # 只处理merge request事件
    event_type = payload.get("object_kind", "")
    if event_type != "merge_request":
        return {"message": f"Ignored event type: {event_type}"}

    # 提取Merge Request信息
    mr_attrs = payload.get("object_attributes", {})
    mr_id = mr_attrs.get("iid", 0)
    action = mr_attrs.get("action", "")
    project_name = payload.get("project", {}).get("name", "")
    project_id = payload.get("project", {}).get("id", 0) or payload.get("project_id", 0)

    # 只在创建或更新MR时审核
    if action not in ("open", "update", "reopen"):
        return {"message": f"Ignored MR action: {action}"}

    # 从changes中提取变更的SQL文件（支持多文件变更）
    sql_items = []

    # 如果有 API Token，主动在线拉取 MR 的变更详情；否则回退到 payload.get("changes")（V1.0兼容）
    changes_list = []
    if GITLAB_API_TOKEN:
        changes_list = _fetch_mr_changes(project_id, mr_id)
    if not changes_list:
        changes_list = payload.get("changes", [])

    if isinstance(changes_list, list):
        for change in changes_list:
            diff_content = change.get("diff", "")
            file_path = change.get("new_path", "")
            if diff_content and file_path and _is_sql_related_file(file_path):
                sql_items.extend(_extract_sql_from_diff(diff_content, file_path))
    elif isinstance(changes_list, dict):
        # 兼容单文件变更格式
        diff_content = changes_list.get("diff", "")
        file_path = changes_list.get("new_path", "unknown.sql")
        if diff_content and _is_sql_related_file(file_path):
            sql_items.extend(_extract_sql_from_diff(diff_content, file_path))

    if not sql_items:
        return {
            "message": "No SQL changes detected in this MR",
            "merge_request_id": mr_id,
        }

    # 执行审核
    results, summary = _audit_sql_list(sql_items)
    report = _format_mr_comment(results, summary, project_name)

    # 回写评论到 GitLab MR
    comment_result = _post_mr_comment(project_id, mr_id, report)

    return {
        "merge_request_id": mr_id,
        "project_name": project_name,
        "total_sql": summary.total_sql,
        "passed": summary.passed,
        "failed": summary.failed,
        "pass_rate": summary.pass_rate,
        "has_critical": summary.error_count > 0,
        "comment": report,
        "comment_posted": comment_result.get("posted", False),
        "comment_reason": comment_result.get("reason"),
    }


@router.post("/audit/diff", summary="审核Git Diff中的SQL")
async def audit_diff(request: Request):
    """
    审核Git Diff内容中的SQL变更。

    请求体格式:
    {
        "diff": "diff内容...",
        "file_path": "path/to/Mapper.xml"
    }
    """
    body = await request.json()
    diff_content = body.get("diff", "")
    file_path = body.get("file_path", "")

    if not diff_content:
        raise HTTPException(status_code=400, detail="diff内容不能为空")

    # 提取SQL
    sql_items = _extract_sql_from_diff(diff_content, file_path)

    if not sql_items:
        return {"message": "未检测到SQL变更", "file_path": file_path}

    # 执行审核
    results, summary = _audit_sql_list(sql_items)

    formatted_results = []
    for r in results:
        formatted_results.append({
            "sql": r.sql[:200] + "..." if len(r.sql) > 200 else r.sql,
            "sql_type": r.sql_type,
            "passed": r.passed,
            "violations": [
                {
                    "rule_id": v.rule_id,
                    "severity": v.severity,
                    "message": v.message,
                    "suggestion": v.suggestion,
                    "line_number": v.line_number,
                }
                for v in r.violations
            ],
        })

    return {
        "file_path": file_path,
        "total_sql": summary.total_sql,
        "passed": summary.passed,
        "failed": summary.failed,
        "pass_rate": summary.pass_rate,
        "results": formatted_results,
    }


@router.post("/audit/repository", summary="审核整个仓库的SQL文件")
async def audit_repository(request: Request):
    """
    审核仓库中所有SQL相关文件。

    请求体格式:
    {
        "files": [
            {"path": "mapper/UserMapper.xml", "content": "<mapper>...</mapper>"},
            {"path": "sql/init.sql", "content": "CREATE TABLE ..."}
        ]
    }
    """
    body = await request.json()
    files = body.get("files", [])

    if not files:
        raise HTTPException(status_code=400, detail="files列表不能为空")

    all_results = []
    file_summaries = []

    for f in files:
        file_path = f.get("path", "")
        content = f.get("content", "")

        if not _is_sql_related_file(file_path):
            continue

        results = checker.audit_file(content, file_path=file_path)
        if results:
            summary = checker.compute_summary(results)
            file_summaries.append({
                "file": file_path,
                "total_sql": summary.total_sql,
                "passed": summary.passed,
                "failed": summary.failed,
                "error_count": summary.error_count,
            })
            all_results.extend(results)

    total_summary = checker.compute_summary(all_results)

    report_lines = [
        "## 仓库SQL审核报告",
        "",
        f"**SQL总数**: {total_summary.total_sql} | **通过**: {total_summary.passed} | **未通过**: {total_summary.failed}",
        f"**通过率**: {total_summary.pass_rate}%",
        "",
    ]

    for fs in file_summaries:
        if fs["failed"] > 0:
            report_lines.append(f"- ❌ **{fs['file']}**: {fs['failed']}个问题 (ERROR: {fs['error_count']})")
        else:
            report_lines.append(f"- ✅ **{fs['file']}**: 通过 ({fs['total_sql']}条SQL)")

    return {
        "total_files": len(file_summaries),
        "total_sql": total_summary.total_sql,
        "passed": total_summary.passed,
        "failed": total_summary.failed,
        "pass_rate": total_summary.pass_rate,
        "has_critical": total_summary.error_count > 0,
        "file_summaries": file_summaries,
        "results": [
            {
                "sql": r.sql[:200],
                "sql_type": r.sql_type,
                "passed": r.passed,
                "file": r.file_path,
                "violations": [
                    {
                        "rule_id": v.rule_id,
                        "severity": v.severity,
                        "message": v.message,
                        "suggestion": v.suggestion,
                    }
                    for v in r.violations
                ],
            }
            for r in all_results if not r.passed
        ],
        "report": "\n".join(report_lines),
    }


@router.get("/config", summary="获取GitLab集成配置说明")
async def get_gitlab_config():
    """获取GitLab集成的配置说明"""
    return {
        "webhook_url": "POST /api/v1/gitlab/webhook/merge-request",
        "setup_steps": [
            "1. 打开GitLab项目 → Settings → Webhooks",
            "2. URL填写: http://<your-host>:8000/api/v1/gitlab/webhook/merge-request",
            "3. Secret Token填写（可选）",
            "4. Trigger勾选: Merge request events",
            "5. SSL verification根据实际情况配置",
        ],
        "supported_events": ["merge_request"],
        "supported_file_types": [".xml (MyBatis)", ".sql"],
        "api_endpoints": {
            "webhook": "POST /api/v1/gitlab/webhook/merge-request",
            "audit_diff": "POST /api/v1/gitlab/audit/diff",
            "audit_repo": "POST /api/v1/gitlab/audit/repository",
        },
    }
