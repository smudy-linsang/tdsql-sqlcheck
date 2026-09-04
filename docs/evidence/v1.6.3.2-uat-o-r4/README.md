# v1.6.3.2 第四轮定点复测证据索引（智能体 O）

## 1. 基线与结论

- 被测提交：`77cbb2534fdf6dda04fecd9e0f942cded8a193c7`
- 复测缺陷：`UAT-O-1632-R3-01`（P2）
- 测试日期：2026-09-04
- 结论：**通过；缺陷关闭；已知软件缺陷 P0/P1/P2/P3 全部为 0**
- 产品代码：O 本轮未修改。

## 2. 信号关闭证据

```text
部署脚本契约：11 passed in 55.99s
HUP exit=129, leftover=0, calls=1, leak=0
INT exit=130, leftover=0, calls=1, leak=0
TERM exit=143, leftover=0, calls=1, leak=0

独立 TERM 控制器：
EXITED_AFTER_TERM=true EXIT_CODE=143 TEMP_CREATED=1 TMP_CHILDREN=0 CURL_CALLS=1 LEAK=false
```

第三轮 `EXITED_AFTER_TERM=false` 已变为 true，信号后不再发起后续请求。

第一次人工控制器因计数变量未导出导致 fake curl 未按预期工作，已判为夹具无效并废弃；上列为修正后重新执行的有效结果。

## 3. 回归结果

```text
明文凭据防复发：2 passed
bash -n：exit 0
全量 tests：1815 passed, 28 skipped, 0 failed, 11 warnings in 352.95s
三方 tests_3p：125 passed, 1 skipped, 0 failed, 2 warnings in 21.03s
manylinux2014_x86_64 / CPython 3.11 离线依赖 dry-run：exit 0

Git Bash + Windows CPython 3.14 真实服务：PASS=12 FAIL=0 SKIP=0，exit 0，TMP_CHILDREN=0
Debian/Linux CPython 3.11 真实服务：PASS=12 FAIL=0 SKIP=0，exit 0
错误口令：PASS=7 FAIL=1 SKIP=3，exit 1，TMP_CHILDREN=0，LEAK=False
服务不可达：PASS=0 FAIL=8 SKIP=3，exit 1，TMP_CHILDREN=0，HAS_PASS=False
```

## 4. 未重复与未冒充的证据

- 本轮无前端及业务模块代码变更，不重复第三轮浏览器全流程；第三轮 UI 已通过，本轮全量测试无回归；
- Linux 证据为 Debian 容器，不冒充目标麒麟 V10 SP3；
- 未访问内网目标 TDSQL，GATE-1 仍需 G 在真实环境执行；
- GATE-2/GATE-3 已有 A 的决策材料，但人类确认栏仍未签字；
- 未执行生产容量/性能测试。

## 5. 安全清理

- 隔离认证服务已停止，测试端口已释放；
- 测试管理员口令已随机重置，已签发 token 失效；
- 人工信号测试的响应、计数文件已删除，脚本私有临时目录残留为 0；
- 本目录不保存口令、token、Authorization、登录响应体或内网连接信息。
