# -*- coding: utf-8 -*-
"""
第三方独立测试套件（tests_3p）公共设施
=========================================
编写方：独立第三方测试角色（不参考既有 tests/ 实现）
测试对象：运行中的真实服务 http://127.0.0.1:8899（真实 MySQL 元数据库）

设计原则：
- 纯黑盒 HTTP 调用，不 import 被测系统任何代码；
- 所有自建数据使用 t3p_ 前缀，便于识别与清理；
- 每个测试类/函数独立登录，避免共享 token 互相污染。
"""
import os
import time
import uuid

import httpx
import pytest

BASE_URL = os.getenv("T3P_BASE_URL", "http://127.0.0.1:8899")
ADMIN_PASSWORD = os.getenv("T3P_ADMIN_PASSWORD", "Admin@1234")

# ⚠ 复跑约束（S09 整改后）：
# 登录接口已按 SEC-22 的要求加上 IP 级失败限流（默认 15 次 / 60 秒）。
# 本套件自身包含大量故意失败的登录（SIT-01 锁定 5 次、SEC-08 注入 4 次、
# SEC-22 喷洒 20 次…），单轮冷启动跑得完，但**60 秒内连续跑第二轮**会因上一轮
# 残留的失败计数直接被 429 拦住，表现为大批 fixture 报错。
# 处理方式（任选其一）：
#   1) 两轮之间间隔 60 秒以上；
#   2) 测试环境启动服务时放宽：LOGIN_IP_FAIL_LIMIT=500 —— 但此时 SEC-22
#      会因为等不到 429 而转为 xfail，验证限流本身请务必用默认阈值。

# 各角色测试账号口令（由本测试套件自行创建）
ROLE_PASSWORD = "T3p#Passw0rd2026"


def _client() -> httpx.Client:
    return httpx.Client(base_url=BASE_URL, timeout=30.0)


@pytest.fixture(scope="session")
def client():
    with _client() as c:
        yield c


def login(client: httpx.Client, username: str, password: str) -> str:
    """登录并返回 access token；失败返回空串"""
    r = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    if r.status_code != 200:
        return ""
    return r.json().get("token", "")


@pytest.fixture(scope="session")
def admin_token(client):
    tok = login(client, "admin", ADMIN_PASSWORD)
    assert tok, "admin 登录失败，服务不可用"
    return tok


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _force_password(client, admin_token, uname, final_pwd):
    """把用户口令强制收敛为 final_pwd 并清除首登改密标记。"""
    client.post(f"/api/v1/auth/users/{uname}/unlock", headers=auth(admin_token))
    # 管理员重置（字段名 new_password）
    client.post(f"/api/v1/auth/users/{uname}/reset-password",
                headers=auth(admin_token), json={"new_password": final_pwd})
    # 登录后自助改密一次，清除 must_change_password 标记
    tok = login(client, uname, final_pwd)
    if tok:
        client.post("/api/v1/auth/change-password", headers=auth(tok),
                    json={"old_password": final_pwd, "new_password": final_pwd})


@pytest.fixture(scope="session")
def ensure_role_users(client, admin_token):
    """确保三个非管理员角色的测试用户存在且可直接登录（幂等）。"""
    created = {}
    for role in ("dba", "developer", "auditor"):
        uname = f"t3p_{role}"
        r = client.post("/api/v1/auth/users", headers=auth(admin_token), json={
            "username": uname, "password": ROLE_PASSWORD, "role": role,
            "display_name": f"三方测试-{role}",
        })
        # 已存在(400/409)或创建成功都继续收敛口令
        _force_password(client, admin_token, uname, ROLE_PASSWORD)
        created[role] = uname
    return created


@pytest.fixture(scope="session")
def tokens(client, admin_token, ensure_role_users):
    """四个角色的 token 字典（均已完成首登改密，可直接访问业务接口）"""
    out = {"admin": admin_token}
    for role, uname in ensure_role_users.items():
        out[role] = login(client, uname, ROLE_PASSWORD)
        assert out[role], f"{uname} 登录失败"
    return out


def rid(prefix: str) -> str:
    """生成唯一资源 ID"""
    return f"{prefix}{uuid.uuid4().hex[:10]}"
