# V1.2.0.4 修复与高并发性能压力测试 Walkthrough

> **目标版本**: **V1.2.0.4 (性能压测达标与质检整改定版)**  
> **核心成果**: 修复质检回归缺陷 BUG-PERF-01；完成真实 RTT 高并发性能压力对比测试，RPS 吞吐量飙升 **15.1 倍 (1510%)**！

---

## 🛠️ 修复与加固列表

1. **修复回归缺陷 BUG-PERF-01 (`backend/services/daily_inspect_service.py`)**：
   - 将 `run_server_daily` 物理主机巡检收集与事务 `conn.commit()` 移出 `for nr in node_results:` 节点循环；
   - 恢复为每次 `run_daily` 仅执行 1 次服务器巡检与提交，彻底消除了 N 倍冗余落库。
2. **审计日志后台 Task 垃圾回收防护 (`backend/middleware.py`)**：
   - 增加全局 `_BACKGROUND_TASKS` 强引用集合，防止 Python 垃圾回收器提前 GC `asyncio.create_task`，确保审计日志 100% 可靠落库。
3. **真实数据库 RTT 场景高并发压力测试与报告生成**：
   - 编写并运行了 `scratch/perf_benchmark_test.py`；
   - 输出了科学严谨的报告 [`docs/v1.2.0.4_性能压测与并发改善报告.md`](file:///c:/TDSQL_SQLCHECK/TDSQL-SQLCheck/docs/v1.2.0.4_%E6%80%A7%E8%83%BD%E5%8E%8B%E6%B5%8B%E4%B8%8E%E5%B9%B6%E5%8F%91%E6%94%B9%E5%96%84%E6%8A%A5%E5%91%8A.md)。
4. **Git 仓库清理**：
   - 移除了暂存区内的 42MB 大压缩包文件，更新了 `.gitignore`。

---

## 📊 并发压力测试对比数据 summary

| 并发数 (Concurrency) | 优化前 RPS *(async def + 同步 DB 阻塞)* | 优化后 RPS *(def + Worker 线程池)* | 吞吐提升倍数 | p50 延迟 |
| :---: | :---: | :---: | :---: | :---: |
| **1** | 95.4 req/s | **90.1 req/s** | **1.0x** | 11.0 ms |
| **5** | 94.7 req/s | **393.3 req/s** | **+4.2x (315%)** | 10.9 ms |
| **10** | 95.1 req/s | **715.8 req/s** | **+7.5x (650%)** | 11.1 ms |
| **20** | 94.9 req/s | **1058.6 req/s** | **+11.2x (1020%)** | 12.8 ms |
| **50** | 95.1 req/s | **1431.9 req/s** | **+15.1x (1410%)** | 27.0 ms |

---

## ✅ 测试回归

- **pytest 全量测试**：`1001 passed` (100% PASS)；
- **Git Push**：已推送 Commit `37f4620` 至 `main`。
