# v1.6.3.2 第一轮 UAT 证据索引（智能体 O）

## 1. 证据范围

- `prepare_uat.py`：隔离浏览器 UAT 用户、分布式/集中式连接和四模块三页快照夹具。
- `r035_same_type.sql`：R035 同类型不同长度文件审核样例。
- `r035_different_type.sql`：R035 不同基础类型文件审核样例。
- `verify_r058_metadata.py`：真实 HTTP + 真实表元数据的 R058 16 组合边界矩阵。
- `verify_upgrade_preservation.py`：R120/R121 幂等补插与既有规则目录/规则集覆盖保留。
- `prepare_third_party.py`：`tests_3p` 的隔离认证服务夹具。

脚本全部拒绝非指定 UAT 元数据库，口令只从环境变量读取，不包含生产凭据。

## 2. 正式结果

```text
规则与适用域专项：183 passed
扫描对比/请求所有权/迁移专项：70 passed
全量 tests：1804 passed, 28 skipped, 0 failed
三方 tests_3p：125 passed, 1 skipped, 0 failed
规则物料：121 = 109 + 7 + 5，断言失败 0
R058 元数据矩阵：16/16 PASS
升级保留：3/3 PASS
进程内 smoke_test.py：90/90 PASS
manylinux2014_x86_64 / CPython 3.11 离线依赖 dry-run：exit 0
浏览器控制台：0 error / 0 warning
deploy/verify_deploy.sh：PASS=6 FAIL=6，exit 1（UAT-O-1632-REL-01）
```

## 3. 浏览器证据摘要

- 四模块跨页 ID：`schema_audit [1,11]`、`slow_scan [26,36]`、`launch_check [51,61]`、`bigtable [76,86]`。
- 覆盖刷新保留、查询/重置清空、第三条回滚、实例/数据源不兼容回滚、模块切换清空、迟到响应丢弃、退出后跨用户隔离。
- R011/R120、R030/R032、R035 文件与在线两入口、R121 CREATE/ALTER 均通过真实页面操作核对。

## 4. 排除的无效执行

- 自定义空元数据库上的第一次全量测试：存量套件明确只批准 `tdsql_sqlcheck_test` 且假设预置 admin，故不计产品结果；使用正式基线重跑后 1804/1804 通过。
- 未设置 `T3P_BASE_URL` 的第一次三方执行：访问默认 8899 端口，属前置配置错误；改指向隔离 18833 服务后 125 通过、1 条按设计跳过。
- WSL 首次运行部署脚本时 `127.0.0.1` 受 WSL NAT 影响不可达；最终缺陷证据来自 Git Bash 对 Windows 隔离服务的可达实跑，不使用 WSL 网络失败作为结论。

## 5. 安全处置

部署脚本在 JSON 解析失败后会打印登录响应前缀。本轮使用的是隔离测试管理员，发现后已立即重置口令递增 token version，使该响应中的令牌失效；报告与证据文件均不保存令牌内容。
