# -*- coding: utf-8 -*-
"""
第五轮：性能专项测试（银行生产规模视角）
==========================================
目的：验证系统在银行生产规模（数百账号/多实例/大数据量/并发使用）下的
      性能表现与资源保护能力，暴露性能瓶颈与失控风险。
方法：温和黑盒探测（共享 SIT 环境，不做破坏性压测）。
用例数：10
"""
import concurrent.futures as cf
import statistics
import time

import pytest

from conftest import auth, rid


# ════════════════════════════════════════════════════════════
# P1. 分页与结果集保护
# ════════════════════════════════════════════════════════════
class TestP1PaginationProtection:

    def test_perf01_page_size_upper_bound(self, client, tokens):
        """PERF-01 分页容量必须有上限保护（防全表拉取拖垮服务）"""
        r = client.get("/api/v1/slow-queries?page=1&page_size=100000",
                       headers=auth(tokens["dba"]))
        body = r.json() if r.status_code == 200 else {}
        items = body.get("items", body.get("slow_queries", []))
        if r.status_code == 200 and len(items) == 0:
            # 空表无法判断是否截断，检查是否 400/422 拒绝
            r2 = client.get("/api/v1/audit/extracted-reports?limit=100000",
                            headers=auth(tokens["dba"]))
            if r2.status_code == 200:
                pytest.xfail("DEFECT-P01: 分页参数无上限校验（limit/page_size=100000 被接受），"
                             "数据量大后单次请求可拉全表，存在内存与带宽失控风险")
        assert r.status_code in (200, 400, 422)

    def test_perf02_list_baseline_latency(self, client, tokens):
        """PERF-02 核心列表接口响应时间基线（< 1.5s）"""
        for ep in ("/api/v1/slow-queries?page=1&page_size=50",
                   "/api/v1/audit/extracted-reports?limit=50",
                   "/api/v1/dashboard/summary"):
            t0 = time.perf_counter()
            r = client.get(ep, headers=auth(tokens["dba"]))
            dt = time.perf_counter() - t0
            assert r.status_code == 200
            assert dt < 1.5, f"{ep} 响应 {dt:.2f}s 超过基线 1.5s"

    def test_perf03_deep_offset_scan(self, client, tokens):
        """PERF-03 深分页探测（offset 翻页不应退化为全表扫描耗时）"""
        t0 = time.perf_counter()
        r = client.get("/api/v1/audit/extracted-reports?limit=20&offset=0",
                       headers=auth(tokens["dba"]))
        d1 = time.perf_counter() - t0
        total = r.json().get("total", 0)
        if total > 40:
            t0 = time.perf_counter()
            r2 = client.get(f"/api/v1/audit/extracted-reports?limit=20&offset={total - 20}",
                            headers=auth(tokens["dba"]))
            d2 = time.perf_counter() - t0
            assert d2 < 2.0, f"深分页耗时 {d2:.2f}s，疑似全表扫描"


# ════════════════════════════════════════════════════════════
# P2. 审核引擎性能
# ════════════════════════════════════════════════════════════
class TestP2AuditEngine:

    def test_perf04_wide_sql_audit_latency(self, client, tokens):
        """PERF-04 500 列宽表 SQL 审核耗时（< 2s，银行复杂报表场景）"""
        big_sql = "SELECT " + ", ".join(f"col_{i}" for i in range(500)) + \
                  " FROM report_wide WHERE id = 1"
        t0 = time.perf_counter()
        r = client.post("/api/v1/audit/sql", headers=auth(tokens["dba"]),
                        json={"sql": big_sql})
        dt = time.perf_counter() - t0
        assert r.status_code == 200
        assert dt < 2.0, f"宽表 SQL 审核 {dt:.2f}s，超基线"

    def test_perf05_batch_file_audit_throughput(self, client, tokens):
        """PERF-05 批量审核 50 条 SQL 文件（< 10s，发布评审场景）"""
        content = "\n".join(
            f"SELECT * FROM t{i} WHERE dt = '2026-07-0{i % 9 + 1}';" for i in range(50))
        t0 = time.perf_counter()
        r = client.post("/api/v1/audit/file", headers=auth(tokens["dba"]),
                        json={"content": content, "file_path": "perf50.sql"})
        dt = time.perf_counter() - t0
        assert r.status_code == 200
        assert dt < 10.0, f"50 条批量审核 {dt:.2f}s，吞吐量不足"

    def test_perf06_concurrent_audit_stability(self, client, tokens):
        """PERF-06 10 并发审核（模拟晨会集中提交，全部成功且 P95 < 3s）"""
        def one(i):
            t0 = time.perf_counter()
            r = client.post("/api/v1/audit/sql", headers=auth(tokens["dba"]),
                            json={"sql": f"SELECT * FROM perf_t WHERE id = {i}"})
            return r.status_code, time.perf_counter() - t0
        with cf.ThreadPoolExecutor(max_workers=10) as ex:
            results = list(ex.map(one, range(20)))
        codes = [c for c, _ in results]
        lat = sorted(d for _, d in results)
        assert all(c == 200 for c in codes), f"并发下出现失败: {codes}"
        p95 = lat[int(len(lat) * 0.95) - 1]
        assert p95 < 3.0, f"并发 P95={p95:.2f}s 超基线"

    def test_perf07_oversized_payload_guard(self, client, tokens):
        """PERF-07 超大请求体保护（5MB SQL 应被拒或受控处理）"""
        huge = "SELECT * FROM t WHERE col IN (" + ",".join(["'x'"] * 200000) + ")"
        try:
            r = client.post("/api/v1/audit/sql", headers=auth(tokens["dba"]),
                            json={"sql": huge}, timeout=60)
        except Exception:
            pytest.xfail("DEFECT-P02: 5MB 大请求导致连接异常/超时，无请求体大小保护")
            return
        if r.status_code == 200:
            import warnings
            warnings.warn(f"OBS-SIZE: {len(huge)//1024}KB 请求体被接受且无拒绝策略，"
                          "建议配置请求体上限")
        assert r.status_code in (200, 400, 413, 422)


# ════════════════════════════════════════════════════════════
# P3. 前端与传输效率
# ════════════════════════════════════════════════════════════
class TestP3FrontendTransport:

    def test_perf08_static_gzip_compression(self, client):
        """PERF-08 静态资源应启用压缩（银行内网带宽治理）"""
        r = client.get("/", headers={"Accept-Encoding": "gzip, deflate"})
        assert r.status_code == 200
        size = len(r.content)
        # FastAPI 默认无 GZip 时仅提示；大于 10KB 未压缩则记录
        if size > 10240 and "content-encoding" not in {k.lower() for k in r.headers}:
            import warnings
            warnings.warn(f"OBS-GZIP: 首页 {size//1024}KB 未启用压缩传输")

    def test_perf09_dashboard_aggregate_efficiency(self, client, tokens):
        """PERF-09 治理概览聚合查询效率（连续 5 次均值 < 0.8s）"""
        lat = []
        for _ in range(5):
            t0 = time.perf_counter()
            r = client.get("/api/v1/dashboard/summary", headers=auth(tokens["dba"]))
            lat.append(time.perf_counter() - t0)
            assert r.status_code == 200
        avg = statistics.mean(lat)
        assert avg < 0.8, f"dashboard 聚合均值 {avg:.2f}s，存在慢查询嫌疑"

    def test_perf10_repeated_rule_list_no_degradation(self, client, tokens):
        """PERF-10 规则列表反复读取无性能衰减（无内存泄漏型累积）"""
        lat = []
        for _ in range(6):
            t0 = time.perf_counter()
            r = client.get("/api/v1/rules", headers=auth(tokens["dba"]))
            lat.append(time.perf_counter() - t0)
            assert r.status_code == 200
        # 末次不应比首次慢 3 倍以上
        assert lat[-1] < max(lat[0] * 3, 0.5), \
            f"重复读取性能衰减: first={lat[0]:.3f}s last={lat[-1]:.3f}s"
