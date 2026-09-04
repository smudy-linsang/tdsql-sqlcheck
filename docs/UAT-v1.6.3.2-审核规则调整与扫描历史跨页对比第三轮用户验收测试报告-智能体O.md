# v1.6.3.2 审核规则调整与扫描历史跨页对比
## 第三轮用户验收测试报告（智能体 O）

| 项目 | 内容 |
|---|---|
| 测试版本 | v1.6.3.2 |
| 被测提交 | `f54a63cbaa917c10115c15ca337e1f023d8396d0` |
| 整改对象 | 第二轮缺陷 `UAT-O-1632-R2-01` |
| 测试日期 | 2026-09-04 |
| 测试方式 | 改动代码定向复测 + 真实认证服务双运行时验证 + 信号异常路径 + 真实浏览器冒烟 + 全量/三方/离线依赖回归 |
| 测试人 | 智能体 O（独立 UAT） |

---

## 1. 验收结论

Q 对第二轮 P2 的核心整改有效：大型中文 JSON 不再经 Git Bash/MSYS 标准输入传给 Windows Python，而是落入私有临时目录后按 UTF-8 文件读取。以下证据全部通过：

- 契约测试 **8/8**；
- Windows Git Bash + Windows CPython 3.14 对真实 v1.6.3.2 服务：**PASS=12 / FAIL=0 / SKIP=0 / exit 0**；
- Debian Linux + CPython 3.11 对同一真实服务：**PASS=12 / FAIL=0 / SKIP=0 / exit 0**；
- 正确、错误口令及服务不可达路径均未泄漏口令、登录响应体、token 或 Authorization；
- 正常、错误口令、不可达三条路径结束后，专用 `TMPDIR` 子项均为 **0**；
- 全量 `tests/`：**1812 passed, 28 skipped, 0 failed**；
- 三方 `tests_3p/`：**125 passed, 1 skipped, 0 failed**；
- manylinux2014 x86_64 / CPython 3.11 离线依赖解析：**exit 0**；
- 真实浏览器登录、版本、121 条规则及跨页两条记录对比：**通过**。

因此，第二轮缺陷 **`UAT-O-1632-R2-01` 已关闭**。

本轮同时在 Q 新增的信号处理代码中发现 **1 项 P2**：`trap cleanup EXIT HUP INT TERM` 捕获 `HUP/INT/TERM` 后只清理目录、不终止脚本。实测向运行中的脚本发送 `TERM`，临时目录被删除，但脚本继续执行后续 HTTP 检查，7 秒后进程仍存活。编号 **`UAT-O-1632-R3-01`**，须按 §6 整改。

第三轮裁决：

- **业务功能 UAT：通过**；
- **第二轮 P2：关闭**；
- **第三轮总体：通过（有条件）**；
- **新增缺陷：P2 × 1，P0/P1/P3 × 0**；
- **生产发布：不准出**。除新 P2 外，GATE-1/2/3 尚未由人类责任方签字，目标麒麟 V10 SP3 主机部署后验证也尚未发生。

---

## 2. 范围与证据边界

### 2.1 本轮严格测试范围

Q 本轮改动集中于：

- `deploy/verify_deploy.sh`：JSON 文件化解析、`cygpath` 路径转换、私有临时目录及信号清理；
- `tests/test_verify_deploy_contract.py`：新增大体量中文规则响应；
- `tests/test_no_hardcoded_secrets.py`：尖括号占位符识别；
- 部署验证说明及开发报告。

因此本轮对跨运行时 JSON、临时目录、失败语义、输出脱敏、进程信号和契约桩真实性逐项复测。

### 2.2 简单回归范围

规则引擎与四个跨页对比页面本轮没有产品代码改动。依据“未改模块简单校验后放行”，浏览器仅抽验：

1. developer 用户真实登录；
2. 页面版本 v1.6.3.2；
3. 规则库总数 121、DDL 23、分布式 15、Oracle 42；
4. 在线元数据审核扫描对比：第一页选一条、翻到第二页再选一条、按钮启用并产生真实对比结果。

其余行为由第一、二轮 UAT 与本轮全量回归共同覆盖，不重复做四模块全套浏览器遍历。

### 2.3 未冒充的生产证据

- Linux 证据来自 Debian CPython 3.11 容器，不冒充麒麟 V10 SP3 真机；
- 本轮没有访问内网目标 TDSQL 分布式实例，R058 的版本前提仍由 GATE-1 关闭；
- 本轮没有代表 DBA 接受集中式零覆盖，也没有代表流水线负责人接受门禁双向变化；
- 没有执行生产容量/性能测试；
- 测试使用隔离元数据库，结束后服务已停止、测试口令已随机重置使已签发 token 失效。

---

## 3. 第二轮 P2 关闭证据

### 3.1 契约测试

```text
python -m pytest tests/test_verify_deploy_contract.py -q
8 passed in 45.31s
```

新增桩在模块加载时自证：121 条规则、`oracle_compat=42`、UTF-8 编码体积不小于 64 KiB，并含中文 `name/description/spec_source/fix_suggestion`。这已覆盖第二轮小型 ASCII 桩失真的漏洞。

### 3.2 真实服务：Git Bash + Windows Python

显式指定 Windows CPython 3.14，脚本读取真实 `/api/v1/rules`：

```text
VERIFY_PYTHON=C:/Python314/python.exe
PASS=12 FAIL=0 SKIP=0
部署验证全部通过
VERIFY_EXIT=0
TMP_CHILDREN=0
```

规则总数 121、Oracle 兼容规则 42、R080、概览和 metrics 均真实通过。第二轮的 `JSONDecodeError` 与 10/2/0 已不再出现。

### 3.3 真实服务：Linux Python 3.11

同一 Windows 隔离认证服务由 `python:3.11-slim` 容器中的 Bash、curl、CPython 3.11 访问：

```text
PASS=12 FAIL=0 SKIP=0
部署验证全部通过
exit 0
```

这证明 `cygpath` 分支没有破坏 Linux 原生路径。

### 3.4 错误口令与不可达路径

错误口令：

```text
PASS=7 FAIL=1 SKIP=3
VERIFY_EXIT=1
TMP_CHILDREN=0
BAD_PASSWORD_LEAK=False
AUTH_HEADER_LEAK=False
```

服务不可达：

```text
PASS=0 FAIL=8 SKIP=3
VERIFY_EXIT=1
TMP_CHILDREN=0
HAS_PASS=False
```

失败分支无伪 PASS；登录前置失败后的三项检查明确为 SKIP；任一 FAIL/SKIP 均不会以 0 退出。

### 3.5 敏感信息与语法

```text
python -m pytest tests/test_no_hardcoded_secrets.py -q
2 passed in 4.25s

bash -n deploy/verify_deploy.sh
exit 0
```

本机没有 `shellcheck`，因此该可选项未执行，不伪报通过。

---

## 4. 真实浏览器冒烟

全部操作由浏览器界面真实点击完成：

| 编号 | 操作 | 实际结果 | 结论 |
|---|---|---|---|
| UI-R3-01 | 隔离 developer 用户登录 | 进入治理概览，无强制改密弹窗 | 通过 |
| UI-R3-02 | 查看顶栏版本 | `v1.6.3.2` | 通过 |
| UI-R3-03 | 平台治理 → 审核规则库 | 共 121 条；DDL 23、分布式 15、Oracle 42 | 通过 |
| UI-R3-04 | SQL审核 → 在线元数据审核 → 扫描对比 | 共 26 条，3 页 | 通过 |
| UI-R3-05 | 第 1 页选择 09:00 记录，翻到第 2 页选择 08:51 记录 | 第一页选择未丢失，“开始对比”由禁用变为可用 | 通过 |
| UI-R3-06 | 点击“开始对比” | HTTP 200；显示之前 2、现在 2、已修复 1、新增 1、遗留 1、整改率 50% | 通过 |

未发现规则页或跨页对比功能回归。

---

## 5. 自动化与发布依赖回归

| 测试项 | 结果 |
|---|---|
| 部署脚本契约 | **8 passed** |
| 明文凭据防复发 | **2 passed** |
| 全量 `tests/` | **1812 passed, 28 skipped, 0 failed, 11 warnings** |
| 三方 `tests_3p/` | **125 passed, 1 skipped, 0 failed, 2 warnings** |
| manylinux2014 x86_64 / CPython 3.11 离线依赖 dry-run | **exit 0** |
| Bash 语法 | **exit 0** |
| 真实 Git Bash 部署验证 | **12/0/0, exit 0** |
| 真实 Linux 部署验证 | **12/0/0, exit 0** |
| 错误口令 | **7/1/3, exit 1，无泄漏** |
| 服务不可达 | **0/8/3, exit 1，无假 PASS** |

`tests_3p` 的 1 项跳过及两条 warning 与第二轮一致，分别是自定义 HMAC token 无法按标准 JWT payload 离线解析、XSS 字符串原样存储观察、781 KiB 请求体可接受观察。本期产品 API 未改，不升级为新缺陷。

---

## 6. 新增缺陷 UAT-O-1632-R3-01（P2）

### 6.1 标题

`verify_deploy.sh` 捕获 HUP/INT/TERM 后只清理、不退出，取消发布任务后仍继续验证。

### 6.2 定位

- 文件：`deploy/verify_deploy.sh`
- 位置：第 77～78 行
- 当前实现：

```bash
cleanup() { rm -rf -- "$VERIFY_TMP_DIR"; }
trap cleanup EXIT HUP INT TERM
```

### 6.3 复现步骤

1. 为脚本设置一个专用空 `TMPDIR`；
2. 用导出的 Bash `curl` 测试函数模拟每次请求阻塞 4 秒后失败；
3. 后台启动 `verify_deploy.sh`；
4. 等待其私有临时目录创建后，对脚本 PID 执行 `kill -TERM`；
5. 等待 7 秒，检查进程与临时目录。

本轮实测摘要：

```text
SIGNAL_TARGET_PID=450 TEMP_CREATED=1
[FAIL] 健康探针不可达
[FAIL] 版本号异常
[FAIL] 首页不可访问
EXITED_AFTER_TERM=false
TMP_CHILDREN=0
```

可见 TERM 已触发 `cleanup`，但脚本继续从健康检查运行到首页检查；超过一次模拟 curl 的 4 秒后仍存活，只能由测试控制器强制结束。

### 6.4 根因

Bash 在捕获信号后执行自定义 trap。当前 trap 的处理函数仅 `rm -rf` 并正常返回，等于覆盖了 `HUP/INT/TERM` 原本的终止语义。脚本随后继续执行，而且其工作目录已被清掉，后续 JSON `curl -o` 还会因为目标目录不存在产生二次失败。

### 6.5 影响

- CI、systemd 或人工取消部署验证时，进程可能继续到全部 curl 超时结束；
- 上层流水线认为任务已经发出终止请求，但脚本仍访问应用接口并输出新的 PASS/FAIL；
- 信号后临时目录已删除，后续检查的错误信息不再代表真实服务状态；
- 不会造成数据库写入或令牌文件残留，故定为 **P2**，不是 P0/P1。

### 6.6 照图施工修复方案

Q 只改 `deploy/verify_deploy.sh` 的 trap 定义，不改业务检查顺序：

```bash
cleanup() {
  rm -rf -- "$VERIFY_TMP_DIR"
}

on_signal() {
  local exit_code="$1"
  trap - HUP INT TERM
  exit "$exit_code"
}

trap cleanup EXIT
trap 'on_signal 129' HUP
trap 'on_signal 130' INT
trap 'on_signal 143' TERM
```

说明：

1. 临时目录只由 `EXIT` trap 统一清理，避免信号 trap 先删目录、脚本却继续；
2. 信号 trap 显式 `exit`，分别保留常用退出码 129/130/143；
3. `exit` 会触发 `EXIT` trap，因此正常、FAIL、HUP、INT、TERM 都只走同一清理入口；
4. 先复位三个信号 trap，避免退出过程中重复进入处理函数；
5. 不要改回可预测 `/tmp/_vd_*` 文件，也不要在信号日志中输出 token、响应体或 Authorization。

### 6.7 必须新增的契约测试

在 `tests/test_verify_deploy_contract.py` 新增参数化测试，例如 `test_signal_exits_and_cleans_private_tmpdir`：

1. fake curl 至少阻塞 4 秒，保证脚本处于请求中；
2. 为每次用例注入独立 `TMPDIR`；
3. Git Bash 后台启动脚本，确认私有子目录已经创建；
4. 分别发送 `HUP`、`INT`、`TERM`；
5. 断言进程在当前阻塞请求返回后不再发起下一请求；
6. 断言退出码分别为 129、130、143；
7. 断言 `TMPDIR` 下无残留；
8. 断言输出无 token、登录响应体、Authorization 和 traceback。

### 6.8 关闭标准

- 上述新增契约测试通过；
- 本轮 TERM 复现从 `EXITED_AFTER_TERM=false` 变为 `true`；
- 正常、错误口令、不可达三条既有路径结果仍分别为 12/0/0 exit 0、7/1/3 exit 1、0/8/3 exit 1；
- Windows Git Bash 与 Linux Python 3.11 真实服务仍为 12/0/0；
- 全量 `tests/`、`tests_3p/` 与离线依赖门禁无新增失败。

---

## 7. GATE-1/2/3 应交给哪个智能体

先明确边界：三项表单写的是组织风险接受，**任何智能体都不能代替真实 DBA、内网运维或流水线负责人签字**。智能体可以执行、取证、整理和预填；最终“接受/不接受、姓名、日期”必须由对应人类责任方确认。

建议项目组按下表派单：

| 门禁 | 智能体承办 | 智能体要交付的材料 | 最终人类确认人 |
|---|---|---|---|
| GATE-1 | **智能体 G 主责** | 在内网目标分布式实例记录版本；按发起单做只读语法验证；附原始输出并预填支持结论 | 林桑/目标实例 DBA + 内网运维；必要时原厂专家书面确认 |
| GATE-2 | **智能体 A 主责** | 把集中式视图、存储过程、触发器、临时表零覆盖边界整理成一页决策摘要，预填“接受/不接受”选项及后果 | 林桑（DBA、需求方）本人裁决并签字 |
| GATE-3 | **智能体 A 牵头，智能体 G 配合** | A 整理 §10.2 双向变化矩阵和活动规则集影响；G 在内网跑存量 SQL 预命中统计及 strict/normal 隔离流水线，把实测数字回填 | 林桑/DBA 管理员 + 每条受影响流水线的真实负责人 |

不建议让 Q 或 O 回填确认栏：

- Q 是开发实施方，可以修代码、提供统计脚本，但不应自批自己的发布风险；
- O 是独立 UAT 方，可以核验证据是否完整，但不拥有业务风险接受权；
- A、G 也只能预填材料，不能把自己的智能体名称写进“确认人”冒充责任人。

最短执行顺序：**先让 G 完成 GATE-1 与 GATE-3 的内网实测，再让 A 汇总 GATE-2/GATE-3 决策材料，最后由你和相关人类责任方签字，O 做发布准出复核。**

---

## 8. 最终裁决与下一步

1. Q 按 §6.6 修复 `UAT-O-1632-R3-01` 并补信号契约测试；
2. O 做一次定点第四轮复测，关闭 P2；
3. G/A 按 §7 分工准备三项门禁材料，人类责任方完成签字；
4. 在目标麒麟 V10 SP3 主机部署后执行正式 `deploy/verify_deploy.sh`，必须 **PASS=12、FAIL=0、SKIP=0、exit 0**；
5. 上述四项全部完成后，方可申请 v1.6.3.2 最终生产准出。

证据索引：`docs/evidence/v1.6.3.2-uat-o-r3/README.md`。
