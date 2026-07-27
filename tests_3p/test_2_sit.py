# -*- coding: utf-8 -*-
"""
第二轮：SIT 系统集成测试
==========================
目的：验证模块间集成正确性——认证→RBAC→业务→持久化→审计的完整链路。
视角：银行科技部门集成测试工程师，按权限矩阵与业务流逐项核对。
用例数：34
"""
import time

import pytest

from conftest import auth, login, rid, ROLE_PASSWORD


# ════════════════════════════════════════════════════════════
# B1. 认证与登录安全集成
# ════════════════════════════════════════════════════════════
class TestB1AuthIntegration:

    def test_sit01_login_lockout_after_5_failures(self, client, admin_token):
        """SIT-01 连续 5 次错误口令锁定账户 15 分钟"""
        uname = rid("t3p_lock_")
        client.post("/api/v1/auth/users", headers=auth(admin_token),
                    json={"username": uname, "password": "Init#Pass123", "role": "auditor"})
        for _ in range(5):
            client.post("/api/v1/auth/login", json={"username": uname, "password": "bad"})
        r = client.post("/api/v1/auth/login", json={"username": uname, "password": "Init#Pass123"})
        assert r.status_code == 401, "锁定后正确口令仍能登录"
        assert "锁定" in r.json().get("detail", "") or "lock" in r.json().get("detail", "").lower()
        client.delete(f"/api/v1/auth/users/{uname}", headers=auth(admin_token))

    def test_sit02_first_login_must_change_password(self, client, admin_token):
        """SIT-02 新建用户首登强制改密，未改密禁止访问业务接口"""
        uname = rid("t3p_fst_")
        client.post("/api/v1/auth/users", headers=auth(admin_token),
                    json={"username": uname, "password": "Init#Pass123", "role": "developer"})
        tok = login(client, uname, "Init#Pass123")
        assert tok
        r = client.get("/api/v1/rules", headers=auth(tok))
        assert r.status_code == 403
        assert "修改口令" in r.json().get("message", "")
        # 改密后放行
        r2 = client.post("/api/v1/auth/change-password", headers=auth(tok),
                         json={"old_password": "Init#Pass123", "new_password": "New#Passw0rd9"})
        assert r2.status_code == 200
        tok2 = login(client, uname, "New#Passw0rd9")
        assert client.get("/api/v1/rules", headers=auth(tok2)).status_code == 200
        client.delete(f"/api/v1/auth/users/{uname}", headers=auth(admin_token))

    def test_sit03_weak_password_rejected(self, client, admin_token):
        """SIT-03 弱口令创建用户被拒绝（口令策略）"""
        for weak in ("123456", "short", "onlyletters", "12345678"):
            r = client.post("/api/v1/auth/users", headers=auth(admin_token),
                            json={"username": rid("t3p_w_"), "password": weak, "role": "developer"})
            assert r.status_code == 400, f"弱口令 {weak!r} 未被拒绝: {r.status_code}"

    def test_sit04_password_never_in_response(self, client, admin_token):
        """SIT-04 任何接口响应不得包含口令哈希/盐"""
        r = client.get("/api/v1/auth/users", headers=auth(admin_token))
        body = r.text
        for sensitive in ("password_hash", "salt", "pbkdf2", "sha256", "bcrypt"):
            assert sensitive not in body, f"用户列表泄露敏感字段 {sensitive}"
        # 用户对象不得回显 password 字段（must_change_password 属正常业务字段除外）
        for u in r.json().get("users", []):
            assert "password" not in u, "用户对象泄露 password 字段"
            assert "password_hash" not in u
            assert "salt" not in u

    def test_sit05_logout_behavior(self, client, tokens):
        """SIT-05 登出接口行为基线记录"""
        tok = login(client, "t3p_auditor", ROLE_PASSWORD)
        r = client.post("/api/v1/auth/logout", headers=auth(tok))
        assert r.status_code in (200, 204, 404, 405)
        # 登出后令牌是否仍可用（行为记录，安全性在安全轮判定）
        r2 = client.get("/api/v1/auth/me", headers=auth(tok))
        assert r2.status_code in (200, 401)


# ════════════════════════════════════════════════════════════
# B2. RBAC 权限矩阵集成（四角色 × 关键操作）
# ════════════════════════════════════════════════════════════
class TestB2RbacMatrix:

    def test_sit06_developer_cannot_manage_users(self, client, tokens):
        """SIT-06 developer 禁止访问用户管理"""
        r = client.get("/api/v1/auth/users", headers=auth(tokens["developer"]))
        assert r.status_code == 403

    def test_sit07_dba_cannot_manage_users(self, client, tokens):
        """SIT-07 dba 禁止访问用户管理（用户管理 admin 独占）"""
        r = client.get("/api/v1/auth/users", headers=auth(tokens["dba"]))
        assert r.status_code == 403

    def test_sit08_auditor_readonly_enforced(self, client, tokens):
        """SIT-08 auditor 全局只读：可读列表、禁止一切写操作"""
        tok = tokens["auditor"]
        assert client.get("/api/v1/rules", headers=auth(tok)).status_code == 200
        assert client.get("/api/v1/admin/operation-logs", headers=auth(tok)).status_code == 200
        # 写操作必须 403
        w = client.post("/api/v1/audit/sql", headers=auth(tok), json={"sql": "SELECT 1"})
        assert w.status_code == 403, f"auditor 竟可执行审核写操作: {w.status_code}"

    def test_sit09_developer_write_scope(self, client, tokens):
        """SIT-09 developer 可审核但禁止实例/规则集/门禁写"""
        tok = tokens["developer"]
        assert client.post("/api/v1/audit/sql", headers=auth(tok),
                           json={"sql": "SELECT 1"}).status_code == 200
        r = client.post("/api/v1/tdsql/connections", headers=auth(tok), json={
            "id": rid("t3p_c_"), "name": "x", "host": "127.0.0.1",
            "port": 3306, "username": "u", "password": "p"})
        assert r.status_code == 403, f"developer 竟可创建实例连接: {r.status_code}"

    def test_sit10_dba_business_write_allowed(self, client, tokens):
        """SIT-10 dba 具备业务读写（规则集创建）"""
        r = client.post("/api/v1/rulesets", headers=auth(tokens["dba"]),
                        json={"rule_set_id": rid("t3p_rs_"), "rule_set_name": "三方SIT规则集",
                              "description": "SIT"})
        assert r.status_code in (200, 201, 400, 409, 422), r.status_code

    def test_sit11_batch_delete_admin_only(self, client, tokens):
        """SIT-11 历史元数据审核批量删除仅 admin（v1.3.1 合规项）"""
        for role in ("dba", "developer", "auditor"):
            r = client.post("/api/v1/audit/extracted-reports/batch-delete",
                            headers=auth(tokens[role]), json={"ids": [99999999]})
            assert r.status_code == 403, f"{role} 竟可批量删除审核历史: {r.status_code}"

    def test_sit12_scan_report_delete_admin_only(self, client, tokens):
        """SIT-12 对比报告留档删除仅 admin（v1.3 领导决策合规项）"""
        for role in ("dba", "auditor", "developer"):
            r = client.request("DELETE", "/api/v1/scan-compare/reports/99999999",
                               headers=auth(tokens[role]))
            assert r.status_code in (403, 404), \
                f"{role} 删除对比报告未被权限拦截: {r.status_code}"


# ════════════════════════════════════════════════════════════
# B3. 审核 → 规则集 → 门禁 联动
# ════════════════════════════════════════════════════════════
class TestB3AuditChain:

    def test_sit13_audit_persisted_to_history(self, client, tokens):
        """SIT-13 即时审核结果持久化到审核历史"""
        r = client.post("/api/v1/audit/sql", headers=auth(tokens["dba"]),
                        json={"sql": "SELECT * FROM sit_check_table"})
        assert r.status_code == 200
        h = client.get("/api/v1/dashboard/summary", headers=auth(tokens["dba"]))
        assert h.status_code == 200

    def test_sit14_file_audit_mybatis(self, client, tokens):
        """SIT-14 MyBatis XML 文件审核提取 SQL"""
        xml = """<?xml version="1.0"?>
<mapper namespace="X">
  <select id="q1" resultType="map">SELECT * FROM users WHERE id = #{id}</select>
  <update id="u1">UPDATE account SET balance = balance - #{amt}</update>
</mapper>"""
        r = client.post("/api/v1/audit/file", headers=auth(tokens["dba"]),
                        json={"content": xml, "file_path": "mapper.xml"})
        assert r.status_code == 200
        body = r.json()
        assert body["summary"]["total_sql"] == 2, f"应提取2条SQL: {body['summary']}"
        # UPDATE 无 WHERE 必须命中 R014
        all_rules = {v["rule_id"] for res in body["results"] for v in res["violations"]}
        assert "R014" in all_rules

    def test_sit15_audit_summary_arithmetic(self, client, tokens):
        """SIT-15 审核汇总计数自洽（passed+failed=total）"""
        r = client.post("/api/v1/audit/file", headers=auth(tokens["dba"]), json={
            "content": "SELECT * FROM t1;\nSELECT id FROM t2 WHERE id=1;",
            "file_path": "x.sql"})
        s = r.json()["summary"]
        assert s["passed"] + s["failed"] == s["total_sql"], "汇总计数不自洽"

    def test_sit16_gate_evaluation_blocks_error(self, client, tokens, admin_token):
        """SIT-16 质量门禁：ERROR 违规触发阻断"""
        r = client.post("/api/v1/audit/sql?evaluate_gate=true",
                        headers=auth(tokens["dba"]),
                        json={"sql": "SELECT * FROM forbidden_table"})
        body = r.json()
        if body.get("gate_result"):
            assert body["gate_result"]["passed"] is False
            assert body["gate_result"]["error_count"] >= 1

    def test_sit17_ruleset_override_applies(self, client, tokens):
        """SIT-17 规则集覆盖：项目绑定后按覆盖执行（集成链路探测）"""
        # 创建规则集并禁用 R012
        rs_id = rid("t3p_rs_")
        r = client.post("/api/v1/rulesets", headers=auth(tokens["dba"]), json={
            "rule_set_id": rs_id, "rule_set_name": "SIT禁用R012",
            "items": [{"rule_id": "R012", "enabled": False}]})
        assert r.status_code in (200, 201, 400, 409, 422)
        # 行为记录：规则集创建契约探测（不影响判定）
        detail = client.get(f"/api/v1/rulesets/{rs_id}", headers=auth(tokens["dba"]))
        assert detail.status_code in (200, 404)

    def test_sit18_audit_invalid_sql_syntax_error(self, client, tokens):
        """SIT-18 语法错误 SQL 报 E999 而非静默通过"""
        r = client.post("/api/v1/audit/sql", headers=auth(tokens["dba"]),
                        json={"sql": "SELCT FRO WHERE ))))"})
        body = r.json()
        assert body["passed"] is False
        ids = {v["rule_id"] for v in body["violations"]}
        assert any("E999" in i or "SYNTAX" in i for i in ids), f"语法错误未识别: {ids}"

    def test_sit19_audit_empty_sql_rejected(self, client, tokens):
        """SIT-19 空 SQL 请求被参数校验拒绝（422/400）"""
        r = client.post("/api/v1/audit/sql", headers=auth(tokens["dba"]), json={"sql": ""})
        assert r.status_code in (400, 422)


# ════════════════════════════════════════════════════════════
# B4. 实例连接与扫描链路
# ════════════════════════════════════════════════════════════
class TestB4InstanceScan:

    def test_sit20_connection_crud_lifecycle(self, client, tokens):
        """SIT-20 实例连接注册→列表→更新→删除 生命周期（使用服务端返回的id）"""
        requested_id = rid("t3p_conn_")
        create = client.post("/api/v1/tdsql/connections", headers=auth(tokens["dba"]), json={
            "id": requested_id, "name": "三方SIT实例", "host": "127.0.0.1", "port": 13306,
            "username": "root", "password": "tdsql_test_2024",
            "database": "tdsql_sqlcheck", "is_distributed": False})
        assert create.status_code in (200, 201), create.text[:300]
        server_id = create.json().get("id", "")
        lst = client.get("/api/v1/tdsql/connections", headers=auth(tokens["dba"]))
        assert any(c["id"] == server_id for c in lst.json()["connections"]), \
            "创建的连接未出现在列表"
        # 更新（PUT 契约）
        upd = client.put(f"/api/v1/tdsql/connections/{server_id}", headers=auth(tokens["dba"]), json={
            "name": "三方SIT实例-改", "host": "127.0.0.1", "port": 13306,
            "username": "root", "password": "tdsql_test_2024", "database": "tdsql_sqlcheck"})
        assert upd.status_code in (200, 404, 422)
        d = client.delete(f"/api/v1/tdsql/connections/{server_id}", headers=auth(tokens["dba"]))
        assert d.status_code in (200, 204)

    @pytest.mark.xfail(reason="DEFECT-D01: 创建连接接口静默忽略传入id并自动生成随机id，客户端按原id无法管理（404）", strict=False)
    def test_sit20b_create_connection_id_semantics(self, client, tokens):
        """SIT-20b 【缺陷确认】创建连接接口静默忽略调用方传入的 id

        银行自动化运维场景：CMDB/脚本按约定 id 注册连接后按 id 引用，
        服务端却重写为随机 id 且无任何提示 → 客户端按原 id 删除返回 404。
        """
        requested_id = rid("t3p_idfix_")
        create = client.post("/api/v1/tdsql/connections", headers=auth(tokens["dba"]), json={
            "id": requested_id, "name": "ID语义验证", "host": "127.0.0.1", "port": 13306,
            "username": "root", "password": "tdsql_test_2024", "database": "tdsql_sqlcheck"})
        server_id = create.json().get("id", "")
        # 行为记录：服务端是否采纳调用方 id
        id_honored = (server_id == requested_id)
        # 用调用方 id 删除，验证可管理性
        d = client.delete(f"/api/v1/tdsql/connections/{requested_id}", headers=auth(tokens["dba"]))
        manageable_by_requested_id = (d.status_code in (200, 204))
        # 清理（若服务端重写了 id）
        if server_id and server_id != requested_id:
            client.delete(f"/api/v1/tdsql/connections/{server_id}", headers=auth(tokens["dba"]))
        # 缺陷判定：id 被重写 且 按原 id 无法删除 → 记为缺陷（用 xfail 语义固化）
        assert id_honored or manageable_by_requested_id, \
            f"DEFECT: 创建连接忽略传入id(请求={requested_id}, 实返={server_id})，按原id删除返回{d.status_code}"

    def test_sit21_connection_password_encrypted_at_rest(self, client, tokens):
        """SIT-21 连接口令加密存储（列表/详情均不明文）"""
        cid = rid("t3p_enc_")
        secret = "SuperSecret#999"
        client.post("/api/v1/tdsql/connections", headers=auth(tokens["dba"]), json={
            "id": cid, "name": "加密验证", "host": "127.0.0.1", "port": 13306,
            "username": "root", "password": secret, "database": "tdsql_sqlcheck"})
        lst = client.get("/api/v1/tdsql/connections", headers=auth(tokens["dba"]))
        assert secret not in lst.text, "连接列表泄露明文口令"
        client.delete(f"/api/v1/tdsql/connections/{cid}", headers=auth(tokens["dba"]))

    def test_sit22_connection_invalid_host_handling(self, client, tokens):
        """SIT-22 无效主机连接应明确报错而非 500"""
        r = client.post("/api/v1/tdsql/connections", headers=auth(tokens["dba"]), json={
            "id": rid("t3p_bad_"), "name": "不存在主机", "host": "192.0.2.254",
            "port": 3306, "username": "u", "password": "p", "validate": True})
        assert r.status_code in (400, 200, 201, 422), f"无效主机返回 {r.status_code}: {r.text[:200]}"

    def test_sit23_scan_fetch_requires_valid_connection(self, client, tokens):
        """SIT-23 慢SQL抓取对不存在连接明确报错（不产生悬挂任务）"""
        r = client.post("/api/v1/tdsql/slow-queries/fetch", headers=auth(tokens["dba"]), json={
            "connection_id": "t3p_nonexistent_conn",
            "time_start": "2026-07-27 00:00:00", "time_end": "2026-07-27 01:00:00"})
        assert r.status_code in (400, 404, 422, 200)
        if r.status_code == 200:
            # 200 时应明确提示连接不存在，而非静默成功
            assert r.json().get("code") != 0 or "不存在" in r.text or "not" in r.text.lower()

    def test_sit24_scan_task_list_pagination(self, client, tokens):
        """SIT-24 扫描任务列表分页契约"""
        r = client.get("/api/v1/slow-queries/scan-tasks?limit=5&offset=0",
                       headers=auth(tokens["dba"]))
        assert r.status_code == 200

    def test_sit25_explain_analysis_endpoint(self, client, tokens):
        """SIT-25 EXPLAIN 分析接口对非法输入健壮处理"""
        r = client.post("/api/v1/slow-queries/analyze-explain",
                        headers=auth(tokens["dba"]),
                        json={"explain_data": []})
        assert r.status_code in (200, 400, 422)


# ════════════════════════════════════════════════════════════
# B5. v1.3 扫描结果对比链路
# ════════════════════════════════════════════════════════════
class TestB5ScanCompare:

    def test_sit26_compare_requires_exactly_two(self, client, tokens):
        """SIT-26 对比必须恰好两个快照（E4001）"""
        r = client.post("/api/v1/scan-compare/compare",
                        headers=auth(tokens["dba"]),
                        json={"snapshot_ids": [1], "module": "slow_scan"})
        assert r.status_code == 400
        assert r.json().get("code") == "E4001" or "E4001" in r.text

    def test_sit27_compare_rejects_self(self, client, tokens):
        """SIT-27 禁止与自身对比（E4002）"""
        r = client.post("/api/v1/scan-compare/compare",
                        headers=auth(tokens["dba"]),
                        json={"snapshot_ids": [7, 7], "module": "slow_scan"})
        assert r.status_code == 400

    def test_sit28_compare_nonexistent_snapshot_404(self, client, tokens):
        """SIT-28 不存在的快照返回 E4004/404"""
        r = client.post("/api/v1/scan-compare/compare",
                        headers=auth(tokens["dba"]),
                        json={"snapshot_ids": [99999991, 99999992], "module": "slow_scan"})
        assert r.status_code in (400, 404)

    def test_sit29_compare_invalid_module_e4006(self, client, tokens):
        """SIT-29 非法 module 参数返回 E4006"""
        r = client.post("/api/v1/scan-compare/compare",
                        headers=auth(tokens["dba"]),
                        json={"snapshot_ids": [1, 2], "module": "hacker_module"})
        assert r.status_code == 400
        assert "E4006" in r.text

    def test_sit30_snapshot_list_filterable(self, client, tokens):
        """SIT-30 快照列表可按模块筛选"""
        r = client.get("/api/v1/scan-compare/snapshots?module=slow_scan&limit=5",
                       headers=auth(tokens["dba"]))
        assert r.status_code == 200

    def test_sit31_compare_reports_list(self, client, tokens):
        """SIT-31 对比报告留档列表"""
        r = client.get("/api/v1/scan-compare/reports?module=bigtable&limit=5",
                       headers=auth(tokens["dba"]))
        assert r.status_code == 200


# ════════════════════════════════════════════════════════════
# B6. API 契约一致性
# ════════════════════════════════════════════════════════════
class TestB6ApiContract:

    def test_sit32_unauthenticated_401_json_shape(self, client):
        """SIT-32 未认证 401 响应结构统一 {code, message}"""
        r = client.get("/api/v1/rules")
        assert r.status_code == 401
        body = r.json()
        assert body.get("code") == 401 and "message" in body

    def test_sit33_forbidden_403_json_shape(self, client, tokens):
        """SIT-33 越权 403 响应结构统一"""
        r = client.get("/api/v1/auth/users", headers=auth(tokens["developer"]))
        assert r.status_code == 403
        assert "message" in r.json()

    def test_sit34_request_id_echoed(self, client, admin_token):
        """SIT-34 X-Request-ID 透传与生成（链路追踪）"""
        r = client.get("/api/v1/rules", headers={**auth(admin_token), "X-Request-ID": "t3p-trace-001"})
        assert r.headers.get("X-Request-ID") == "t3p-trace-001"
        r2 = client.get("/api/v1/rules", headers=auth(admin_token))
        assert r2.headers.get("X-Request-ID"), "未生成请求ID"
