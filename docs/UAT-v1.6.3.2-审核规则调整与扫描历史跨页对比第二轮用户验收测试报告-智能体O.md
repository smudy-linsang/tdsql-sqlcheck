# v1.6.3.2 审核规则调整与扫描历史跨页对比第二轮用户验收测试报告

| 项目 | 内容 |
|---|---|
| 测试执行人 | 智能体 O |
| 测试日期 | 2026-09-04 |
| 被测版本 | v1.6.3.2 |
| 被测提交 | `8cd734fbe83f8bf3e4c14b6d2df6ee3a88abc11c` |
| 复测依据 | 第一轮 UAT `UAT-O-1632-REL-01`（P1）及报告 §6.4 关闭标准 |
| 测试方式 | 真实浏览器点击 + 真实认证服务 + Linux/Git Bash 双运行时部署脚本复测 + 契约/全量/三方回归 + 离线依赖门禁 |
| 总体结论 | **通过（有条件）：第一轮 P1 的生产 Linux 阻断已关闭；新增 1 项 P2，且三项生产书面门禁仍待回填** |

## 1. 管理结论

Q 对第一轮 UAT 指出的 P1 予以认可并完成整改。本轮独立复测确认：

- `verify_deploy.sh` 不再调用未定义的 `J`，健康探针、首页、登录失败短路和令牌保护均已修复；
- 在真实 v1.6.3.2 认证服务前，由 Linux CPython 3.11 + Bash + curl 运行脚本，得到 **PASS=12 / FAIL=0 / SKIP=0 / exit 0**；
- 错误口令场景安全失败，不回显登录响应体、口令、token 或 Authorization；
- 服务停止后的真实不可达场景为 **PASS=0 / FAIL=8 / SKIP=3 / exit 1**，健康检查没有假 PASS；
- 规则、前端跨页对比以及未改模块回归均通过。

同时发现一项新的 P2：整改文档声明“开发机可用 Git Bash 复现”，但真实规则接口响应约 44 KB 且包含中文时，Git Bash 把响应通过标准输入交给 Windows 原生 Python 会发生字符转码破坏，导致规则总数和 Oracle 分类两项解析失败，脚本在开发机得到 **PASS=10 / FAIL=2 / exit 1**。现有契约桩只返回较小 ASCII JSON，未覆盖真实响应特征，因此 7 项契约测试会全部通过但仍漏掉该问题。

本问题不影响本轮已验证的 Linux 生产运行路径，故定级 P2，不回退为 P1；但它直接影响 Q 文档承诺的开发机复现能力和部署脚本契约可信度，必须补测修正。

缺陷统计：

| 等级 | 新增 | 关闭 | 当前状态 |
|---|---:|---:|---|
| P0 | 0 | 0 | 无 |
| P1 | 0 | 1 | `UAT-O-1632-REL-01` 的 Linux 生产阻断已关闭 |
| P2 | 1 | 0 | `UAT-O-1632-R2-01`：Git Bash 大型中文 JSON 解析失败 |
| P3 | 0 | 0 | 无 |

最终裁决：

- 软件功能 UAT：**通过**；
- 第一轮 P1 定点复测：**通过（生产 Linux 路径）**；
- 开发机 Git Bash 复现：**不通过，P2**；
- 第二轮 UAT：**通过（有条件）**；
- 生产发布：**尚不准出**。必须先完成 GATE-1/2/3 书面回填，并在目标麒麟 V10 SP3 主机部署后运行正式脚本确认 12 项全 PASS；P2 建议在制作最终发布包前关闭。

## 2. 测试范围与证据边界

### 2.1 严格复测范围

Q 本轮代码变更仅涉及：

- `deploy/verify_deploy.sh`；
- `tests/test_verify_deploy_contract.py`；
- 部署验证说明与开发报告。

因此严格测试集中在部署脚本的正常、失败、安全和跨运行时行为，以及新增契约测试能否代表真实服务。

### 2.2 简单回归范围

规则实现和四个跨页对比页面本轮未改代码，按用户要求采用“真实浏览器关键路径 + 全量自动化”快速放行：

- 版本与登录；
- 规则总数、分类及 R011/R120 详情；
- 即时审核一组 TEXT + LONGBLOB；
- 在线元数据审核的一次跨页选择与真实对比；
- 全量 `tests/`、`tests_3p/` 和离线依赖门禁。

### 2.3 证据边界

- 浏览器证据来自本机隔离元数据库与真实运行的 v1.6.3.2 服务，不是截图模拟；
- Linux 部署脚本证据来自 Debian `python:3.11-slim` 容器中的真实 Bash/curl/Python，访问真实认证服务；它证明通用 Linux 运行时行为，但不能冒充目标麒麟 V10 SP3 真机证据；
- Git Bash 证据来自 Windows Git Bash 8.21.0 + Windows CPython 3.14，正是 Q 文档声明的开发机复现路径；
- 本轮没有访问内网目标 TDSQL 分布式实例；R058 的目标版本能力仍由 GATE-1 负责书面确认；
- 未执行专项性能压测，本轮改动为部署验证脚本，不以风险接受替代性能验证。

## 3. 第一轮 P1 关闭复测

### 3.1 契约测试

命令：

```powershell
$env:SQLCHECK_DB_NAME='tdsql_sqlcheck_test'
$env:AUTH_ENABLED='false'
python -m pytest tests/test_verify_deploy_contract.py -q
```

结果：**7 passed in 37.45s**。

覆盖结果：

| 场景 | 结果 |
|---|---|
| 正常服务、正确口令、全项 PASS | 通过 |
| 服务不可达必须失败且无假 PASS | 通过 |
| 登录成功不得打印 token | 通过 |
| 错误口令不得回显响应体，后续记 SKIP | 通过 |
| 畸形登录 JSON 不得泄漏 token canary | 通过 |
| 30 万字节首页不得产生 SIGPIPE 假失败 | 通过 |
| `bash -n` | 通过 |

### 3.2 真实服务：Linux 正常路径

隔离认证服务使用 121 条真实规则及真实元数据库。脚本在 Linux CPython 3.11 环境中运行，未指定伪造响应：

```text
[PASS] 健康探针 HTTP 成功
[PASS] 版本号 1.6.3.2
[PASS] 首页可访问
[PASS] 三项静态资产
[PASS] admin 登录成功
[PASS] 规则总数 121
[PASS] Oracle迁移兼容规则 42 条
[PASS] 审核引擎命中 R080
[PASS] 元数据库读写正常
[PASS] /metrics 指标输出
PASS=12 FAIL=0 SKIP=0
exit 0
```

结论：第一轮“脚本确定性失败、真实登录被误判、后续连锁 401”的 P1 在生产目标 Linux 路径已关闭。

### 3.3 真实服务：错误口令安全路径

结果：

```text
PASS=7 FAIL=1 SKIP=3
exit 1
```

登录固定输出为“HTTP=401；响应体不回显”，规则、审核、概览三项明确记为登录前置失败而跳过。对完整输出执行泄漏检查，未出现测试口令、登录响应体、token canary 或 Authorization 值。

### 3.4 真实服务：停止后不可达路径

关闭隔离服务后复跑：

```text
[FAIL] 健康探针不可达
PASS=0 FAIL=8 SKIP=3
exit 1
```

不存在任何 `[PASS]`，符合第一轮报告要求。

### 3.5 P1 关闭结论

第一轮 P1 的四个组成部分均已关闭：

1. 未定义 JSON 函数：已关闭；
2. 首页 `pipefail + grep -q` 假失败：已关闭；
3. 健康探针无条件 PASS：已关闭；
4. 登录失败回显响应体/令牌：已关闭。

## 4. 真实浏览器快速回归

执行路径均由浏览器界面真实点击完成。

| 编号 | 页面/场景 | 结果 |
|---|---|---|
| UI-01 | 登录页使用隔离 developer 用户登录 | 通过 |
| UI-02 | 顶部产品版本显示 v1.6.3.2 | 通过 |
| UI-03 | 审核规则库显示总数 121 | 通过 |
| UI-04 | 分类显示 DDL 23、分布式 15、Oracle 42 | 通过 |
| UI-05 | R011：INFO、“谨慎使用TEXT大对象字段”、通用 | 通过 |
| UI-06 | R030/R032：仅分布式；R035 文案不再检查长度；R120：ERROR | 通过 |
| UI-07 | 即时审核 TEXT + LONGBLOB：命中 R011 INFO 与 R120 ERROR | 通过 |
| UI-08 | 在线元数据审核 → 扫描对比：第一页选择一条、翻到第二页再选一条 | 通过 |
| UI-09 | 两条跨页记录选择后“开始对比”可用并生成结果 | 通过 |

对比结果实际显示：之前 2、现在 2、已修复 1、新增 1、遗留 1、整改率 50%。服务访问日志中相关接口均为 HTTP 200，无 500。

浏览器首次登录出现“必须修改口令”是夹具重置口令后的状态；清理隔离用户 `must_change_password` 后重新登录正常，不计产品缺陷。

## 5. 自动化与发布边界回归

| 测试项 | 结果 |
|---|---|
| `tests/test_verify_deploy_contract.py` | **7 passed** |
| 全量 `tests/` | **1811 passed, 28 skipped, 0 failed, 11 warnings** |
| 三方 `tests_3p/` | **125 passed, 1 skipped, 0 failed, 2 warnings** |
| manylinux2014 x86_64 / CPython 3.11 离线依赖 dry-run | **exit 0** |
| `bash -n deploy/verify_deploy.sh` | **exit 0** |
| 真实 Linux 部署验证 | **PASS=12 FAIL=0 SKIP=0, exit 0** |
| 真实不可达部署验证 | **PASS=0 FAIL=8 SKIP=3, exit 1** |

`tests_3p` 的 1 项跳过仍是自定义 HMAC 令牌无法按标准 JWT payload 离线解析；两项既有观察为 XSS 字符串原样存储和 781 KB 请求体可接受，本期未改产品 API，不升级为 v1.6.3.2 新缺陷。

## 6. 新增缺陷 UAT-O-1632-R2-01（P2）

### 6.1 标题

`verify_deploy.sh` 在 Git Bash + Windows Python 下无法解析真实大型中文规则 JSON，契约桩因数据失真漏检。

### 6.2 影响范围与定级

- 影响：Q 明确承诺的“开发机可用 Git Bash 复现”路径、开发/交付前自检和契约测试可信度；
- 不影响：已验证的 Linux Bash + Linux Python 生产运行路径；
- 严重度：P2；
- 建议：最终发布包制作前关闭；若决定不支持 Git Bash，必须删除文档承诺并把测试限定说明写清，但不推荐仅改文档规避。

### 6.3 复现步骤

1. 启动认证开启的真实 v1.6.3.2 服务，规则接口返回 121 条完整中文规则；
2. Windows Git Bash 中指定 Windows Python：

   ```bash
   export SQLCHECK_VERIFY_PYTHON=C:/Python314/python.exe
   export SQLCHECK_VERIFY_PASSWORD='<隔离测试口令>'
   bash deploy/verify_deploy.sh --host 127.0.0.1 --port 18834
   ```

3. 观察：

   ```text
   [PASS] admin 登录成功
   [FAIL] 规则总数=<解析失败>
   [FAIL] oracle_compat=<解析失败>
   PASS=10 FAIL=2 SKIP=0
   exit 1
   ```

4. 同一接口使用 HTTP 客户端直接解析，得到 `total=121`、`rules=121`；响应原始大小 **44,404 bytes**，UTF-8 解码后 **32,617 characters**；
5. 同一脚本在 Linux CPython 3.11 运行则 12 项全 PASS。

### 6.4 根因

当前实现：

- `deploy/verify_deploy.sh:66` 使用 `json.load(sys.stdin)`；
- `deploy/verify_deploy.sh:145-147` 把完整规则响应存入 Bash 变量，再用 `printf ... | json_get` 管道交给 Python；
- Git Bash/MSYS 向 Windows 原生 Python 的标准输入传递大体量中文文本时发生字符转码，Python 实际读取到 36,042 个字符并出现代理字符，最终在约第 29,604 列触发 `JSONDecodeError`；
- 把 curl 原始文件路径直接交给 Python，并使用 `open(path, encoding='utf-8')` 读取，可正确得到 121/121/42；
- `tests/test_verify_deploy_contract.py` 的规则桩仅包含 ASCII `rule_id/category`，响应远小于真实 44 KB，没有中文描述、规范来源和修复建议，因而无法复现。

这不是规则接口返回非法 JSON，也不是登录/RBAC 故障。

### 6.5 照图施工解决方案

只修改部署脚本、契约测试和部署说明，不改产品 API。

#### 第一步：建立统一临时目录并保证退出清理

在解释器探测成功后创建临时目录：

```bash
VERIFY_TMP_DIR=$(mktemp -d 2>/dev/null || true)
if [[ -z "$VERIFY_TMP_DIR" || ! -d "$VERIFY_TMP_DIR" ]]; then
  bad "无法创建部署验证临时目录"
  summary_and_exit
fi
cleanup() { rm -rf -- "$VERIFY_TMP_DIR"; }
trap cleanup EXIT HUP INT TERM
```

不得回退到可预测的共享 `/tmp/_vd_*` 文件名；临时目录内不得打印或保留登录响应和 token。

#### 第二步：`json_get` 改为按 UTF-8 文件路径解析

函数签名改成 `json_get <selector> <json_file>`：

```bash
json_get() {
  local selector="$1" json_file="$2"
  "$PY_BIN" -c '
import json, sys
try:
    with open(sys.argv[2], "r", encoding="utf-8") as f:
        d = json.load(f)
    s = sys.argv[1]
    if s == "version": value = d.get("version", "")
    elif s == "token": value = d.get("token", "")
    elif s == "total": value = d.get("total", "")
    elif s == "oracle_count": value = sum(r.get("category") == "oracle_compat" for r in d.get("rules", []))
    elif s == "r080_hit": value = any(v.get("rule_id") == "R080" for v in d.get("violations", []))
    elif s == "today_count": value = d["audit"]["today_count"]
    else: raise SystemExit(2)
    print(value)
except SystemExit:
    raise
except Exception:
    raise SystemExit(1)
' "$selector" "$json_file"
}
```

禁止继续通过 stdin/pipe 传入响应正文；禁止使用 `eval`。

#### 第三步：所有 JSON HTTP 响应直接落临时文件

健康、登录、规则、审核、概览分别使用固定在私有临时目录中的文件，例如：

```bash
RULES_FILE="${VERIFY_TMP_DIR}/rules.json"
RULES_HTTP=$(curl -sS -m "$TIMEOUT" "${AUTHH[@]}" \
  -o "$RULES_FILE" -w "%{http_code}" "${BASE}/api/v1/rules" 2>/dev/null) || RULES_HTTP="000"

if [[ "$RULES_HTTP" == "200" ]]; then
  TOTAL=$(json_get total "$RULES_FILE" 2>/dev/null || true)
  OC=$(json_get oracle_count "$RULES_FILE" 2>/dev/null || true)
else
  TOTAL=""; OC=""
fi
```

登录、审核、概览同理。错误信息只使用 HTTP 状态码和固定文案，不回显文件正文。首页和 metrics 不是 JSON，可以保留当前 Bash 字符串匹配。

#### 第四步：补一项真实特征契约测试

在 `_StubHandler` 增加可控的大型 UTF-8 规则响应：

- 121 条规则必须包含中文 `description/spec_source/fix_suggestion`；
- 编码后的响应至少 64 KB，防止测试又退化成小型 ASCII；
- Windows 测试必须继续显式指定 `SQLCHECK_VERIFY_PYTHON=sys.executable`，确保覆盖 Git Bash → Windows Python 边界；
- 新增 `test_large_utf8_rules_payload_on_git_bash`，断言 `PASS=12 FAIL=0 SKIP=0`、exit 0，且输出无 token/traceback；
- 原 7 项测试全部保留，新总数应为 8 项。

建议测试桩在发送前自断言：

```python
payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
assert len(payload) >= 64 * 1024
```

#### 第五步：同步文档

保留“开发机可用 Git Bash 复现”的前提是上述新增测试与真实 Git Bash 服务均通过；否则必须将该句改为“仅支持 Linux Bash + Linux Python”，并解释 Windows Git Bash 不在支持范围。推荐修代码保留跨平台复现能力。

### 6.6 关闭标准

1. `bash -n deploy/verify_deploy.sh` 通过；
2. 契约测试 **8/8** 通过；
3. Windows Git Bash + Windows Python 对真实 121 条中文规则服务运行：**PASS=12 FAIL=0 SKIP=0，exit 0**；
4. Linux Python 3.11 对同一服务运行结果保持 12/0/0；
5. 错误口令、畸形 JSON、服务不可达日志均不出现响应体、token、口令或 Authorization；
6. 临时目录在正常退出、失败退出和信号退出后均删除；
7. 全量 `tests/`、`tests_3p/` 与离线依赖 dry-run 无新增失败。

## 7. 三项生产书面门禁

截至本轮结束，`docs/GATE-v1.6.3.2-生产发布三项书面门禁发起.md` 仍为空白待回填：

| 门禁 | 状态 | 生产处置 |
|---|---|---|
| GATE-1：目标分布式实例 UPDATE/DELETE LIMIT 版本前提 | 待回填 | 不得发布 R058 新行为 |
| GATE-2：DBA 接受集中式对象类型零覆盖 | 待回填 | 不得发布 R030/R032 改域 |
| GATE-3：活动规则集及流水线接受门禁双向变化 | 待回填 | 不得发布 R011/R120/R121 新行为 |

它们不是本轮软件缺陷，也不阻断 UAT 执行；但设计已定义为生产硬门禁，不能用自动化全绿替代责任方签字。

## 8. 复测建议与最终裁决

Q 关闭 P2 后，仅需定点第三轮复测：

1. 契约测试 8/8；
2. 真实服务分别由 Git Bash + Windows Python、Linux Bash + Python 3.11 执行，均 12/0/0；
3. 错误口令与不可达两条失败路径复核无泄漏、退出码 1；
4. 全量 `tests/`、`tests_3p/`、离线依赖 dry-run；
5. 浏览器只需登录、规则页 121 条和一个跨页对比冒烟。

本轮最终裁决：

- **第一轮 P1：关闭**；
- **第二轮 UAT：通过（有条件）**；
- **新增 P2：1 项，需按 §6.5 施工**；
- **生产发布：当前不准出**，原因是 GATE-1/2/3 均未回填，且目标麒麟主机部署后验证尚未发生；
- P2 关闭并完成三项书面门禁后，可申请最终发布准出复核。

证据索引：`docs/evidence/v1.6.3.2-uat-o-r2/README.md`。
