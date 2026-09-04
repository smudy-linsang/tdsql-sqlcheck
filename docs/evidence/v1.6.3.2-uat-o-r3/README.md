# v1.6.3.2 第三轮 UAT 证据索引（智能体 O）

## 1. 基线

- 被测提交：`f54a63cbaa917c10115c15ca337e1f023d8396d0`
- 第二轮报告提交：`bf4f3c5`
- 测试日期：2026-09-04
- 产品代码：本轮 UAT 未修改。

## 2. 正式结果

```text
部署脚本契约：8 passed in 45.31s
明文凭据防复发：2 passed in 4.25s
全量 tests：1812 passed, 28 skipped, 0 failed, 11 warnings in 335.69s
三方 tests_3p：125 passed, 1 skipped, 0 failed, 2 warnings in 21.53s
manylinux2014_x86_64 / CPython 3.11 离线依赖 dry-run：exit 0
bash -n deploy/verify_deploy.sh：exit 0

真实服务 + Git Bash + Windows CPython 3.14：PASS=12 FAIL=0 SKIP=0，exit 0，TMP_CHILDREN=0
真实服务 + Debian/Linux CPython 3.11：PASS=12 FAIL=0 SKIP=0，exit 0
真实服务 + 错误口令：PASS=7 FAIL=1 SKIP=3，exit 1，无凭据/响应体/token/Authorization 泄漏，TMP_CHILDREN=0
服务不可达：PASS=0 FAIL=8 SKIP=3，exit 1，无任何 [PASS]，TMP_CHILDREN=0
```

第二轮缺陷 `UAT-O-1632-R2-01` 关闭。

## 3. 新增 P2 证据

测试使用 Bash 导出的慢速 fake curl，使脚本在 HTTP 调用中可被可靠发送 TERM；未创建或提交测试凭据、响应体与 token 文件。

```text
SIGNAL_TARGET_PID=450 TEMP_CREATED=1
[FAIL] 健康探针不可达
[FAIL] 版本号异常
[FAIL] 首页不可访问
EXITED_AFTER_TERM=false
TMP_CHILDREN=0
```

结论：`trap cleanup EXIT HUP INT TERM` 会清理临时目录，但捕获 TERM 后没有退出，脚本继续执行。缺陷编号 `UAT-O-1632-R3-01`，P2。

## 4. 浏览器证据摘要

- 真实 developer 登录成功，顶栏版本 v1.6.3.2；
- 规则库共 121 条，DDL 23、分布式 15、Oracle 42；
- 在线元数据审核扫描对比共 26 条、3 页；
- 第一页选择 09:00 记录，翻到第二页选择 08:51 记录，第一页选择保留；
- “开始对比”成功启用并产生结果：之前 2、现在 2、已修复 1、新增 1、遗留 1、整改率 50%；
- 快照分页接口与对比接口均 HTTP 200，无 500。

## 5. GATE 分工结论

- GATE-1：智能体 G 做内网实测与预填，DBA + 内网运维人类签字；
- GATE-2：智能体 A 整理决策材料，林桑作为 DBA/需求方本人裁决签字；
- GATE-3：智能体 A 牵头影响矩阵、智能体 G 跑内网预命中与流水线，DBA/管理员 + 真实流水线负责人签字；
- Q、O 均不代签；智能体产出不能替代组织责任人的风险接受。

## 6. 证据边界与安全处置

- Linux 证据为 Debian 容器，不冒充目标麒麟真机；
- 未连接内网目标 TDSQL，GATE-1 仍待真实环境证据；
- 未执行生产容量/性能测试；
- 隔离服务已停止，端口已释放；测试账号口令已随机重置，已签发 token 失效；
- 专用临时探针目录确认空后已删除；
- 本目录不保存口令、token、Authorization、登录响应体或内网连接信息。
