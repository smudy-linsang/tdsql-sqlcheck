# v1.6.3.2 第二轮 UAT 证据索引（智能体 O）

## 1. 基线

- 被测提交：`8cd734fbe83f8bf3e4c14b6d2df6ee3a88abc11c`
- 第一轮报告提交：`bc2a7f05d2e4fccfb89d544c9d9ca9fd254dfd15`
- 测试日期：2026-09-04
- 产品代码：本轮测试未修改。

## 2. 正式结果

```text
部署脚本契约：7 passed in 37.45s
全量 tests：1811 passed, 28 skipped, 0 failed, 11 warnings in 332.06s
三方 tests_3p：125 passed, 1 skipped, 0 failed, 2 warnings in 19.84s
manylinux2014_x86_64 / CPython 3.11 离线依赖 dry-run：exit 0
bash -n deploy/verify_deploy.sh：exit 0

真实服务 + Linux Python 3.11：PASS=12 FAIL=0 SKIP=0，exit 0
真实服务 + 错误口令：PASS=7 FAIL=1 SKIP=3，exit 1，无响应体/凭据/token/Authorization 泄漏
服务停止后：PASS=0 FAIL=8 SKIP=3，exit 1，无任何 [PASS]

真实服务 + Git Bash + Windows Python：PASS=10 FAIL=2 SKIP=0，exit 1
失败项：规则总数=<解析失败>、oracle_compat=<解析失败>
```

## 3. 浏览器证据摘要

- 真实登录成功，页面版本 v1.6.3.2；
- 规则库：总数 121，DDL 23，分布式 15，Oracle 42；
- R011 为 INFO/通用，R030 与 R032 为仅分布式，R035 文案只检查类型，R120 为 ERROR；
- 即时审核真实命中 R011 INFO 和 R120 ERROR；
- 在线元数据审核的扫描对比：第一页与第二页各选一条，按钮启用并成功生成对比结果；
- 相关真实接口均返回 HTTP 200，无 500。

## 4. P2 复现数据

真实 `/api/v1/rules` 响应：

```text
raw UTF-8 bytes: 44404
UTF-8 decoded characters: 32617
API direct parse: total=121, rules=121, oracle_compat=42
Git Bash stdin -> Windows Python characters: 36042（含代理字符）
json.load failure: JSONDecodeError near column 29604
```

验证替代读取方式：curl 直接写临时文件，再将文件路径传给 Windows Python，使用 `open(path, encoding='utf-8')` 解析，结果为：

```text
path_mode_total 121 rules 121 oracle 42
```

这证明失败点是 Git Bash/MSYS 标准输入字符转码边界，不是服务端 JSON、规则数量或 RBAC。

## 5. 证据边界与安全处置

- Linux 证据来自 Debian CPython 3.11 容器访问真实服务，不冒充麒麟 V10 SP3 真机；
- Git Bash 证据来自 Q 文档明确声明的开发机复现路径；
- 测试使用隔离元数据库；所有测试用户口令在结束时随机重置，令牌版本已递增，已签发 token 失效；
- 本目录不保存口令、token、Authorization 或登录响应体；
- 两个 UAT 服务均已停止，临时监听端口已释放。
