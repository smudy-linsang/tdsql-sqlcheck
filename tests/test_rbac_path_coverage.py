"""
RBAC 路径覆盖自检（R01）

check_permission 对未登记 _PATH_TO_MENU 的路径是"兜底放行"（fail-open）：
新端点忘记登记就会对所有登录角色敞开。

运行期改成 fail-closed 是不可行的——/api/v1/tdsql/connect、
/api/v1/audit/batch-stream 等写端点本就应对 dba/developer 开放，
一刀切仅 admin 会直接打断这些角色的正常作业。

因此把"闭"提前到开发期：本用例扫描所有写端点，发现未登记即失败。
新增写端点时，请在 auth_service._PATH_TO_MENU 中登记其菜单归属；
确属无需鉴权的（公开路径/Webhook）则加入下方豁免集合并写明理由。
"""
import re
from pathlib import Path

import pytest

from backend.services.auth_service import (PUBLIC_PATHS, WEBHOOK_PATHS,
                                           _PATH_TO_MENU,
                                           _SELF_SERVICE_PREFIXES)

_API_DIR = Path(__file__).resolve().parent.parent / "backend" / "api"

# 无需菜单登记的写端点及理由
_EXEMPT = {
    "/api/v1/auth/login": "免认证公开路径（PUBLIC_PATHS）",
    "/api/v1/auth/logout": "自助操作（_SELF_SERVICE_PREFIXES）",
    "/api/v1/auth/change-password": "自助操作（_SELF_SERVICE_PREFIXES）",
    "/api/v1/gitlab/webhook/merge-request": "由 Secret Token 鉴权（WEBHOOK_PATHS）",
}


def _write_endpoints():
    """扫描 backend/api 下所有 POST/PUT/DELETE/PATCH 端点的完整路径"""
    found = []
    for f in sorted(_API_DIR.glob("*.py")):
        src = f.read_text(encoding="utf-8")
        m = re.search(r'APIRouter\(prefix="([^"]+)"', src)
        if not m:
            continue
        prefix = m.group(1)
        for method, sub in re.findall(
                r'@router\.(post|put|delete|patch)\("([^"]*)"', src):
            full = (prefix + sub).rstrip("/") or prefix
            found.append((method.upper(), full, f.name))
    return found


def _is_mapped(path: str) -> bool:
    for p in sorted(_PATH_TO_MENU.keys(), key=len, reverse=True):
        if path == p or path.startswith(p + "/"):
            return True
    return False


def test_all_write_endpoints_are_mapped():
    """所有写端点必须登记 _PATH_TO_MENU，或在 _EXEMPT 中说明豁免理由"""
    endpoints = _write_endpoints()
    assert endpoints, "未扫描到任何写端点，检查扫描逻辑是否失效"

    unmapped = [
        f"{method} {path}  ({fname})"
        for method, path, fname in endpoints
        if path not in _EXEMPT and not _is_mapped(path)
    ]
    assert not unmapped, (
        "以下写端点未登记 auth_service._PATH_TO_MENU，将走 fail-open 兜底放行，"
        "对所有登录角色敞开：\n  " + "\n  ".join(unmapped))


def test_exempt_paths_are_genuinely_exempt():
    """豁免清单里的路径必须确实由公开路径/自助/Webhook 机制覆盖，防止随手加豁免"""
    for path in _EXEMPT:
        covered = (path in PUBLIC_PATHS
                   or path.startswith(tuple(_SELF_SERVICE_PREFIXES))
                   or path.startswith(WEBHOOK_PATHS))
        assert covered, f"{path} 在豁免清单中，但并未被任何既有豁免机制覆盖"


def test_exempt_paths_still_exist():
    """豁免清单不应残留已删除的端点"""
    actual = {path for _, path, _ in _write_endpoints()}
    stale = [p for p in _EXEMPT if p not in actual]
    assert not stale, f"豁免清单中的端点已不存在，请清理：{stale}"
