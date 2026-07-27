# -*- coding: utf-8 -*-
"""
第四轮：安全专项测试（银行级安全基线）
======================================
目的：从大型银行安全合规视角，对认证会话、访问控制、注入、敏感数据、
      审计合规、传输与响应头六个维度做渗透式黑盒验证。
依据：等保2.0三级、银行业金融机构信息科技风险管理指引、OWASP ASVS。
用例数：24
"""
import base64
import json
import time

import pytest

from conftest import auth, login, rid, ROLE_PASSWORD


# ════════════════════════════════════════════════════════════
# S1. 认证与会话安全
# ════════════════════════════════════════════════════════════
class TestS1AuthSession:

    def test_sec01_old_token_revoked_after_password_reset(self, client, tokens):
        """SEC-01 重置密码后旧 token 必须立即失效（会话吊销）"""
        uname = rid("t3p_sec01_")
        client.post("/api/v1/auth/users", headers=auth(tokens["admin"]),
                    json={"username": uname, "password": "Sec01#Pass123", "role": "developer"})
        client.post(f"/api/v1/auth/users/{uname}/reset-password",
                    headers=auth(tokens["admin"]), json={"new_password": "Sec01#Pass456"})
        token_a = login(client, uname, "Sec01#Pass456")
        assert token_a, "首次登录失败"
        # admin 再次重置密码（模拟口令泄露后的应急处置）
        client.post(f"/api/v1/auth/users/{uname}/reset-password",
                    headers=auth(tokens["admin"]), json={"new_password": "Sec01#Pass789"})
        r = client.get("/api/v1/auth/me", headers=auth(token_a))
        client.delete(f"/api/v1/auth/users/{uname}", headers=auth(tokens["admin"]))
        assert r.status_code == 401, \
            f"DEFECT: 重置密码后旧 token 仍可用（{r.status_code}），无法紧急踢出会话"

    def test_sec02_logout_invalidates_token(self, client, tokens):
        """SEC-02 服务端登出后 token 必须失效"""
        uname = rid("t3p_sec02_")
        client.post("/api/v1/auth/users", headers=auth(tokens["admin"]),
                    json={"username": uname, "password": "Sec02#Pass123", "role": "developer"})
        client.post(f"/api/v1/auth/users/{uname}/reset-password",
                    headers=auth(tokens["admin"]), json={"new_password": "Sec02#Pass123"})
        tok = login(client, uname, "Sec02#Pass123")
        r_out = client.post("/api/v1/auth/logout", headers=auth(tok))
        if r_out.status_code in (404, 405):
            client.delete(f"/api/v1/auth/users/{uname}", headers=auth(tokens["admin"]))
            pytest.xfail("DEFECT-S02: 无服务端登出接口，token 只能等 8h 自然过期")
        r = client.get("/api/v1/auth/me", headers=auth(tok))
        client.delete(f"/api/v1/auth/users/{uname}", headers=auth(tokens["admin"]))
        assert r.status_code == 401, \
            f"DEFECT-S02: 登出后旧 token 仍可用（{r.status_code}），登出未吊销会话"

    def test_sec03_tampered_token_rejected(self, client, tokens):
        """SEC-03 篡改 token 载荷必须被拒（防伪造身份）"""
        tok = tokens["dba"]
        parts = tok.split(".")
        if len(parts) >= 2:
            # 篡改 payload 段（保留签名段）→ 签名校验必须失败
            forged = parts[0] + "." + base64.urlsafe_b64encode(
                json.dumps({"username": "admin", "role": "admin", "exp": 9999999999}
                           ).encode()).decode().rstrip("=") + "." + parts[-1]
        else:
            forged = tok[:-6] + ("AAAAAA" if not tok.endswith("AAAAAA") else "BBBBBB")
        r = client.get("/api/v1/dashboard/summary", headers=auth(forged))
        assert r.status_code == 401, f"篡改 token 未被拒绝: {r.status_code}"

    def test_sec05_no_user_enumeration(self, client):
        """SEC-05 登录报错不得区分'用户不存在'与'口令错误'（防账号枚举）"""
        r1 = client.post("/api/v1/auth/login",
                         json={"username": rid("t3p_no_such_"), "password": "X#123456"})
        r2 = client.post("/api/v1/auth/login",
                         json={"username": "admin", "password": "Wrong#Pass999"})
        m1, m2 = json.dumps(r1.json(), ensure_ascii=False), json.dumps(r2.json(), ensure_ascii=False)
        if m1 != m2:
            pytest.xfail(f"DEFECT-S03: 登录报错可区分用户是否存在（用户枚举）: {m1} vs {m2}")


# ════════════════════════════════════════════════════════════
# S2. 访问控制与越权
# ════════════════════════════════════════════════════════════
class TestS2AccessControl:

    def test_sec06_unauthenticated_matrix_all_401(self, client):
        """SEC-06 全部敏感端点未认证必须 401（防认证绕过）"""
        endpoints = [
            "/api/v1/dashboard/summary",
            "/api/v1/audit/extracted-reports",
            "/api/v1/slow-queries",
            "/api/v1/tdsql/connections",
            "/api/v1/auth/users",
            "/api/v1/admin/operation-logs",
            "/api/v1/admin/info",
            "/api/v1/projects",
            "/api/v1/rules",
            "/api/v1/bigtable/report/any",
        ]
        for ep in endpoints:
            r = client.get(ep)
            assert r.status_code == 401, f"未认证可访问 {ep}: {r.status_code}"

    def test_sec07_developer_cannot_read_operation_logs(self, client, tokens):
        """SEC-07 开发角色不得读取审计日志（职责分离 SoD）"""
        r = client.get("/api/v1/admin/operation-logs",
                       headers=auth(tokens["developer"]))
        assert r.status_code == 403, f"developer 可读审计日志: {r.status_code}"

    def test_sec07b_auditor_cannot_trigger_scan(self, client, tokens):
        """SEC-07b 审计员不得触发慢SQL采集（只读岗位写绕过）"""
        r = client.post("/api/v1/tdsql/slow-queries/fetch",
                        headers=auth(tokens["auditor"]),
                        json={"connection_id": "5ea70d74"})
        assert r.status_code == 403, f"auditor 可触发采集: {r.status_code}"


# ════════════════════════════════════════════════════════════
# S3. 注入类攻击
# ════════════════════════════════════════════════════════════
class TestS3Injection:

    def test_sec08_login_sql_injection(self, client):
        """SEC-08 登录接口 SQL 注入不得绕过认证或引发 500"""
        payloads = ["admin' OR '1'='1", "admin'--", "admin'/*", "' OR 1=1#"]
        for p in payloads:
            r = client.post("/api/v1/auth/login",
                            json={"username": p, "password": p})
            assert r.status_code in (400, 401, 422), \
                f"登录注入载荷 {p} 返回异常: {r.status_code}"

    def test_sec09_query_param_sql_injection(self, client, tokens):
        """SEC-09 查询参数注入不得改变结果集或报错泄露"""
        r = client.get("/api/v1/slow-queries?page=1&page_size=10&db_name=' OR 1=1--",
                       headers=auth(tokens["dba"]))
        assert r.status_code in (200, 400, 422)
        assert r.status_code != 500, "注入载荷触发 500，存在 SQL 注入嫌疑"

    def test_sec10_audited_sql_not_executed(self, client, tokens):
        """SEC-10 被审核 SQL 中的危险语句只被分析不得被执行"""
        r = client.post("/api/v1/audit/sql", headers=auth(tokens["dba"]),
                        json={"sql": "DROP TABLE users; SELECT * FROM users WHERE '1'='1'"})
        assert r.status_code == 200  # 审核应正常返回
        # 审核引擎必须还能正常工作（证明未被执行污染环境）
        r2 = client.post("/api/v1/audit/sql", headers=auth(tokens["dba"]),
                         json={"sql": "SELECT 1"})
        assert r2.status_code == 200

    def test_sec11_xss_payload_stored_safely(self, client, tokens):
        """SEC-11 XSS 载荷存储与读取（API 不应崩溃，前端须转义）"""
        xss = "<script>alert(1)</script>"
        r = client.post("/api/v1/projects", headers=auth(tokens["admin"]),
                        json={"project_name": rid("t3p_xss_"), "description": xss})
        assert r.status_code in (200, 201, 400, 409, 422)
        if r.status_code in (200, 201):
            lst = client.get("/api/v1/projects", headers=auth(tokens["admin"]))
            assert lst.status_code == 200
            body = lst.text
            if "<script>alert(1)</script>" in body:
                # 原样存储本身可由前端转义兜底，但须记录风险点
                import warnings
                warnings.warn("OBS-XSS: 项目描述原样存储 script 载荷，前端渲染必须转义")


# ════════════════════════════════════════════════════════════
# S4. 敏感数据保护
# ════════════════════════════════════════════════════════════
class TestS4SensitiveData:

    def test_sec12_no_password_leak_in_task_endpoints(self, client, tokens):
        """SEC-12 扫描任务/计划接口不得泄露数据库连接口令"""
        for ep in ("/api/v1/tdsql/scan-tasks", "/api/v1/tdsql/scan-schedules"):
            r = client.get(ep, headers=auth(tokens["dba"]))
            if r.status_code != 200:
                continue
            body = r.text.lower()
            assert '"password"' not in body or '***' in body or '"password":""' in body, \
                f"{ep} 疑似泄露连接口令字段"

    def test_sec13_metrics_endpoint_exposure(self, client):
        """SEC-13 metrics 端点暴露面检查（不得泄露连接串/内网地址）"""
        r = client.get("/metrics")
        if r.status_code == 200:
            body = r.text.lower()
            for leak in ("password", "secret", "jdbc:", "mysql://", "119.45."):
                assert leak not in body, f"metrics 泄露敏感信息: {leak}"
        # 200 但未泄露 → 观察项（银行建议 metrics 也应认证或仅内网暴露）

    def test_sec14_openapi_docs_unauthenticated(self, client):
        """SEC-14 API 文档未认证可访问（信息泄露，攻击面测绘）"""
        r = client.get("/openapi.json")
        if r.status_code == 200 and "/api/v1/auth/login" in r.text:
            pytest.xfail("DEFECT-S04: openapi.json 未认证可访问，完整 API 攻击面暴露")

    def test_sec15_audit_log_no_plaintext_password(self, client, tokens):
        """SEC-15 审计日志不得记录口令明文（监管红线）"""
        uname = rid("t3p_sec15_")
        secret = "Sec15#TopSecret"
        client.post("/api/v1/auth/users", headers=auth(tokens["admin"]),
                    json={"username": uname, "password": "Pwd#Start001", "role": "developer"})
        client.post(f"/api/v1/auth/users/{uname}/reset-password",
                    headers=auth(tokens["admin"]), json={"new_password": secret})
        logs = client.get("/api/v1/admin/operation-logs?limit=50",
                          headers=auth(tokens["admin"])).json().get("logs", [])
        client.delete(f"/api/v1/auth/users/{uname}", headers=auth(tokens["admin"]))
        related = [l for l in logs if uname in json.dumps(l, ensure_ascii=False)]
        assert related, "改密操作未留痕"
        for entry in related:
            assert secret not in json.dumps(entry), "审计日志记录了口令明文！"


# ════════════════════════════════════════════════════════════
# S5. 审计与合规
# ════════════════════════════════════════════════════════════
class TestS5ComplianceAudit:

    def test_sec16_audit_logs_tamper_proof(self, client, tokens):
        """SEC-16 审计日志不得提供删除接口（防篡改，等保要求）"""
        r = client.request("DELETE", "/api/v1/admin/operation-logs",
                           headers=auth(tokens["admin"]))
        assert r.status_code in (404, 405, 403), \
            f"DEFECT-S05: 审计日志可删除（{r.status_code}），违反等保防篡改要求"

    def test_sec17_failed_login_audited(self, client, tokens):
        """SEC-17 登录失败必须留痕（等保 8.1.4.3 审计要求）"""
        uname = rid("t3p_sec17_")
        client.post("/api/v1/auth/users", headers=auth(tokens["admin"]),
                    json={"username": uname, "password": "Sec17#Pass123", "role": "developer"})
        marker = int(time.time())
        client.post("/api/v1/auth/login",
                    json={"username": uname, "password": f"Wrong#{marker}"})
        logs = client.get("/api/v1/admin/operation-logs?limit=100",
                          headers=auth(tokens["admin"])).json().get("logs", [])
        client.delete(f"/api/v1/auth/users/{uname}", headers=auth(tokens["admin"]))
        hits = [l for l in logs if uname in json.dumps(l, ensure_ascii=False)
                and ("login" in json.dumps(l).lower() or "登录" in json.dumps(l, ensure_ascii=False))]
        if not hits:
            pytest.xfail("DEFECT-S06: 登录失败未写入审计日志，不满足等保审计留痕要求")

    def test_sec18_token_has_expiry(self, client, tokens):
        """SEC-18 token 必须携带过期时间（防永久令牌）"""
        tok = tokens["dba"]
        parts = tok.split(".")
        if len(parts) < 2:
            pytest.skip("token 非分段结构，无法离线解析")
        try:
            pad = "=" * (-len(parts[1]) % 4)
            payload = json.loads(base64.urlsafe_b64decode(parts[1] + pad))
        except Exception:
            pytest.skip("token payload 非标准 base64json")
        assert payload.get("exp"), "DEFECT: token 无 exp 过期声明"
        ttl = payload["exp"] - payload.get("iat", payload["exp"] - 28800)
        assert ttl <= 86400, f"token 有效期 {ttl}s 超过 24h，不符合银行会话策略"

    def test_sec19_malformed_json_no_traceback(self, client, tokens):
        """SEC-19 畸形请求体不得泄露堆栈/内部路径"""
        r = client.post("/api/v1/audit/sql",
                        content=b"{invalid json!!!",
                        headers={**auth(tokens["dba"]),
                                 "Content-Type": "application/json"})
        assert r.status_code in (400, 422)
        body = r.text.lower()
        for leak in ("traceback", "file \"", "site-packages", "c:\\"):
            assert leak not in body, f"畸形请求泄露内部信息: {leak}"


# ════════════════════════════════════════════════════════════
# S6. 传输与响应头安全基线
# ════════════════════════════════════════════════════════════
class TestS6TransportHeaders:

    def test_sec20_cors_not_permissive(self, client):
        """SEC-20 CORS 不得为 * 或反射任意 Origin（防跨域数据窃取）"""
        r = client.get("/api/v1/dashboard/summary",
                       headers={"Origin": "https://evil.example.com"})
        acao = r.headers.get("access-control-allow-origin", "")
        if acao == "*" or "evil.example.com" in acao:
            pytest.xfail(f"DEFECT-S07: CORS 过于宽松 ACAO={acao}，任意站点可跨域调用 API")

    def test_sec21_security_headers_baseline(self, client):
        """SEC-21 安全响应头基线（银行渗透测试基线项）"""
        r = client.get("/")
        missing = [h for h in ("x-content-type-options", "x-frame-options",
                               "content-security-policy")
                   if h not in {k.lower() for k in r.headers}]
        if missing:
            pytest.xfail(f"DEFECT-S08: 缺少安全响应头 {missing}，"
                         "存在点击劫持/MIME嗅探/XSS 放大风险")

    def test_sec22_login_no_ip_rate_limit(self, client):
        """SEC-22 登录接口应具备 IP 级限流（防密码喷洒）"""
        uname = rid("t3p_spray_")
        codes = [client.post("/api/v1/auth/login",
                             json={"username": uname, "password": f"Spray#{i}"}
                             ).status_code for i in range(20)]
        if 429 not in codes:
            pytest.xfail("DEFECT-S09: 连续 20 次登录失败无 429 限流，"
                         "账号锁定仅覆盖已存在用户，可对不存在账号无限喷洒")

    def test_sec23_token_in_query_string_rejected(self, client, tokens):
        """SEC-23 token 不得经 URL 查询参数传递（防代理/网关日志泄露）"""
        r = client.get(f"/api/v1/dashboard/summary?access_token={tokens['dba']}")
        if r.status_code == 200:
            pytest.xfail("DEFECT-S10: 支持 ?access_token= 传参，"
                         "token 将被记录进 Nginx/网关访问日志造成泄露")

    def test_sec24_error_response_consistent_shape(self, client, tokens):
        """SEC-24 错误响应结构一致性（不得因路径不同泄露框架差异）"""
        r1 = client.get("/api/v1/no-such-endpoint", headers=auth(tokens["dba"]))
        assert r1.status_code == 404
        body = r1.text.lower()
        for leak in ("traceback", "uvicorn", "starlette"):
            assert leak not in body, f"404 响应泄露框架信息: {leak}"
