"""
TDSQL SQL审核工具 - 认证与用户管理 API (V2.0)

- POST /api/v1/auth/login              登录（免认证）
- POST /api/v1/auth/logout             登出
- GET  /api/v1/auth/me                 当前用户信息
- POST /api/v1/auth/change-password    自助修改口令
- GET  /api/v1/auth/roles              角色清单
- 用户管理（仅admin，由中间件RBAC强制）:
  GET/POST /api/v1/auth/users
  PUT      /api/v1/auth/users/{username}
  DELETE   /api/v1/auth/users/{username}
  POST     /api/v1/auth/users/{username}/reset-password
  POST     /api/v1/auth/users/{username}/unlock
"""
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from backend.services import metrics_service
from backend.services.auth_service import (
    ROLES, auth_service, issue_token,
    get_all_roles, get_role_ids, create_custom_role, update_role, delete_role,
    get_role_permissions, set_role_permissions, get_visible_menus,
    ALL_MENU_KEYS, MENU_LABELS,
)

router = APIRouter(prefix="/api/v1/auth", tags=["认证与用户管理"])

ROLE_LABELS = {
    "admin": "系统管理员",
    "dba": "数据库管理员",
    "developer": "开发人员",
    "auditor": "审计员",
}


# ── 请求模型 ─────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=128)


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=1)


class UserCreateRequest(BaseModel):
    username: str = Field(..., min_length=2, max_length=64)
    password: str = Field(..., min_length=1)
    role: str = Field("developer")
    display_name: str = Field("")


class UserUpdateRequest(BaseModel):
    role: Optional[str] = None
    display_name: Optional[str] = None
    status: Optional[str] = None


class ResetPasswordRequest(BaseModel):
    new_password: str = Field(..., min_length=1)


def _operator(request: Request) -> str:
    return getattr(request.state, "username", "anonymous")


# ── 认证 ─────────────────────────────────────────────────

@router.post("/login", summary="用户登录")
def login(body: LoginRequest, request: Request):
    """登录成功返回访问令牌。请求头 Authorization: Bearer <token> 携带。"""
    client_ip = request.client.host if request.client else ""
    user, err = auth_service.authenticate(body.username, body.password, client_ip)
    if err:
        metrics_service.inc("tdsql_login_total", {"result": "failed"})
        raise HTTPException(status_code=401, detail=err)
    metrics_service.inc("tdsql_login_total", {"result": "success"})
    token = issue_token(user["username"], user["role"])
    return {
        "token": token,
        "token_type": "Bearer",
        "user": {
            "username": user["username"],
            "display_name": user["display_name"],
            "role": user["role"],
            "role_label": ROLE_LABELS.get(user["role"], user["role"]),
            "must_change_password": bool(user["must_change_password"]),
        },
    }


@router.post("/logout", summary="登出")
def logout(request: Request):
    """令牌为自包含短时效设计，登出由客户端丢弃令牌；服务端记录审计。"""
    from backend.services.database import log_operation
    log_operation(_operator(request), "logout", "user", _operator(request))
    return {"message": "已登出"}


@router.get("/me", summary="当前用户信息")
def me(request: Request):
    username = _operator(request)
    user = auth_service.get_user(username)
    if not user:
        # 认证关闭模式下的匿名用户
        return {"username": username,
                "display_name": username,
                "role": getattr(request.state, "role", "admin"),
                "role_label": ROLE_LABELS.get(getattr(request.state, "role", "admin"), ""),
                "must_change_password": False}
    return {
        "username": user["username"],
        "display_name": user["display_name"],
        "role": user["role"],
        "role_label": ROLE_LABELS.get(user["role"], user["role"]),
        "must_change_password": bool(user["must_change_password"]),
        "last_login_at": user.get("last_login_at"),
    }


@router.post("/change-password", summary="自助修改口令")
def change_password(body: ChangePasswordRequest, request: Request):
    username = _operator(request)
    err = auth_service.change_password(username, body.old_password, body.new_password)
    if err:
        raise HTTPException(status_code=400, detail=err)
    return {"message": "口令修改成功，请重新登录"}


@router.get("/roles", summary="角色清单")
def roles():
    """从DB动态获取角色列表"""
    db_roles = get_all_roles()
    if db_roles:
        return {"roles": [{"role_id": r["role_id"], "role_name": r["role_name"], "role": r["role_id"], "label": r["role_name"], "is_builtin": r["is_builtin"], "description": r.get("description","")} for r in db_roles]}
    return {"roles": [{"role": r, "label": ROLE_LABELS.get(r, r), "role_id": r, "role_name": ROLE_LABELS.get(r, r), "is_builtin": 1, "description": ""} for r in ROLES]}


# ── V3.0: 角色CRUD + 权限矩阵（admin only） ─────────────────────

class RoleCreateRequest(BaseModel):
    role_id: str = Field(..., min_length=2, max_length=32, description="角色ID")
    role_name: str = Field(..., description="角色名称")
    description: str = Field("", description="描述")

class RoleUpdateRequest(BaseModel):
    role_name: Optional[str] = None
    description: Optional[str] = None

class RolePermissionsRequest(BaseModel):
    permissions: dict = Field(..., description="{menu_key: 0|1}")

@router.get("/role-permissions", summary="获取角色权限矩阵")
def get_all_role_permissions():
    perms = get_role_permissions()
    roles_list = get_all_roles()
    return {
        "roles": [{"role_id": r["role_id"], "role_name": r["role_name"], "is_builtin": r["is_builtin"]} for r in roles_list],
        "menus": [{"key": mk, "label": MENU_LABELS.get(mk, mk)} for mk in ALL_MENU_KEYS],
        "permissions": perms,
    }

@router.put("/role-permissions/{role_id}", summary="设置角色权限")
def update_role_permissions(role_id: str, body: RolePermissionsRequest):
    set_role_permissions(role_id, body.permissions)
    return {"message": f"角色 {role_id} 权限已更新"}

@router.post("/roles", summary="创建角色")
def create_role(body: RoleCreateRequest):
    result = create_custom_role(body.role_id, body.role_name, body.description)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return {"message": "角色创建成功", "role": result}

@router.put("/roles/{role_id}", summary="编辑角色")
def edit_role(role_id: str, body: RoleUpdateRequest):
    update_role(role_id, body.role_name, body.description)
    return {"message": "角色已更新"}

@router.delete("/roles/{role_id}", summary="删除角色")
def remove_role(role_id: str):
    result = delete_role(role_id)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result

@router.get("/visible-menus", summary="获取当前用户可见菜单")
def visible_menus(request: Request):
    role = getattr(request.state, "role", "admin")
    menus = get_visible_menus(role)
    return {"menus": menus}


# ── 用户管理（admin only，中间件强制） ─────────────────────

@router.get("/users", summary="用户列表")
def list_users():
    return {"users": auth_service.list_users()}


@router.post("/users", summary="创建用户")
def create_user(body: UserCreateRequest, request: Request):
    user, err = auth_service.create_user(
        username=body.username, password=body.password, role=body.role,
        display_name=body.display_name, operator=_operator(request))
    if err:
        raise HTTPException(status_code=400, detail=err)
    return {"message": "用户创建成功（首次登录需修改口令）", "user": user}


@router.put("/users/{username}", summary="更新用户")
def update_user(username: str, body: UserUpdateRequest, request: Request):
    err = auth_service.update_user(
        username, role=body.role, display_name=body.display_name,
        status=body.status, operator=_operator(request))
    if err:
        raise HTTPException(status_code=400, detail=err)
    return {"message": "用户更新成功"}


@router.delete("/users/{username}", summary="删除用户")
def delete_user(username: str, request: Request):
    if username == _operator(request):
        raise HTTPException(status_code=400, detail="不能删除当前登录账户")
    err = auth_service.delete_user(username, operator=_operator(request))
    if err:
        raise HTTPException(status_code=400, detail=err)
    return {"message": "用户已删除"}


@router.post("/users/{username}/reset-password", summary="重置用户口令")
def reset_password(username: str, body: ResetPasswordRequest, request: Request):
    err = auth_service.reset_password(
        username, body.new_password, operator=_operator(request))
    if err:
        raise HTTPException(status_code=400, detail=err)
    return {"message": "口令已重置（该用户下次登录需修改口令）"}


@router.post("/users/{username}/unlock", summary="解锁用户")
def unlock_user(username: str, request: Request):
    err = auth_service.unlock_user(username, operator=_operator(request))
    if err:
        raise HTTPException(status_code=400, detail=err)
    return {"message": "账户已解锁"}
