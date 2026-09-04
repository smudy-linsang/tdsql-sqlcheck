# v1.6.3.2 部署验证说明（verify_deploy.sh）

| 项目 | 内容 |
|---|---|
| 适用版本 | v1.6.3.2 起（v1.6.3.0 历史部署手册中的实测输出样例按 OUT-08 保留原貌，不适用本说明） |
| 脚本 | `deploy/verify_deploy.sh` |
| 整改依据 | UAT-O-1632-REL-01（P1，第一轮 §6）+ UAT-O-1632-R2-01（P2，第二轮 §6）；契约测试 `tests/test_verify_deploy_contract.py` |
| 目标环境 | 麒麟 V10 SP3 / Linux x86_64，应用 venv Python 3.11；**开发机可用 Git Bash 复现**（P2 整改后契约测试在 Windows Git Bash + Windows CPython 实测 8/8 通过，含真实 121 条大型中文规则响应） |

---

## 1. 用途与定位

部署手册指定的**正式准出入口**：部署/升级完成后对真实服务做一键冒烟，覆盖健康探针、版本一致性、首页与静态资产、admin 登录、规则库数量（121 / Oracle 兼容 42）、审核引擎链路（R080 命中）、元数据库读写（Dashboard 概览）与 Prometheus 指标。

**退出码语义**：

| 退出码 | 含义 |
|---|---|
| 0 | `FAIL=0` 且 `SKIP=0`，部署验证全部通过 |
| 1 | 存在 FAIL，或存在 SKIP（登录前置失败——认证后的检查项被明确跳过，**不能视为通过**） |

## 2. 用法

```bash
cd /opt/tdsql-sqlcheck/current
./deploy/verify_deploy.sh --port 8000            # 默认 --host 127.0.0.1 --timeout 10
./deploy/verify_deploy.sh --port 8000 --host 127.0.0.1 --timeout 15
```

口令来源（按优先级）：

1. 环境变量 `SQLCHECK_VERIFY_PASSWORD`（**admin 口令已改后的推荐方式**）；
2. `deploy/.env` 或 `/opt/tdsql-sqlcheck/.env` 中的 `ADMIN_INITIAL_PASSWORD`（仅首次部署初始口令未改时有效）。

**口令临时注入并在执行后立即清除**（避免进入 shell 历史与部署留痕）：

```bash
read -rs SQLCHECK_VERIFY_PASSWORD && export SQLCHECK_VERIFY_PASSWORD
bash deploy/verify_deploy.sh --port 8000
unset SQLCHECK_VERIFY_PASSWORD
```

JSON 解析解释器按以下顺序选择：`SQLCHECK_VERIFY_PYTHON`（显式指定，契约测试用）→ 应用 `venv/bin/python` → 系统 `python3.11/3.10/3.9/python3/python`。找不到解释器时脚本记 FAIL 并中止（不产出误导性结论）。

## 3. v1.6.3.2 整改内容（P1: UAT-O-1632-REL-01 + P2: UAT-O-1632-R2-01）

### 3.1 P1（第一轮）

| 原缺陷 | 整改 |
|---|---|
| 调用从未定义的 `J` 函数解析 JSON（5 处），登录被误判失败后携带空令牌连锁 401，脚本确定性 exit 1 | 改为白名单式 `json_get`（仅 version/token/total/oracle_count/r080_hit/today_count 六个 selector，Python 实现，不用 eval）；任何解析异常收敛为对应检查 FAIL，不输出 traceback |
| 首页 `echo "$FRONT" \| grep -q` 在 `pipefail` 下对大 HTML 触发 SIGPIPE 假失败 | 改为 Bash 字符串匹配 `[[ "$FRONT" == *TDSQL* ]]` |
| 健康探针 `ok "探针响应 $(curl ...)"` 无条件记 PASS | 先检查 `curl -fsS` 退出码，成功才 ok，失败记「健康探针不可达」 |
| 登录失败分支回显响应体前 120 字符——真实登录成功响应的开头就是管理员令牌，会泄漏进终端/CI 日志 | 登录响应体写入临时文件解析后即删；失败只输出 HTTP 状态码与固定文案，**绝不回显响应体 / Authorization / token 前缀** |
| 登录失败后规则/审核/概览检查伪装成业务接口故障（连续 401） | token 为空时上述检查明确记 `[SKIP] …（登录前置失败而跳过）`，且 SKIP>0 时 exit 1 |

### 3.2 P2（第二轮）：Git Bash 大型中文 JSON 解析失败

| 原缺陷 | 整改 |
|---|---|
| `json_get` 用 `json.load(sys.stdin)`，脚本以 `printf '%s' "$BODY" \| json_get` 管道传响应正文；Git Bash/MSYS 向 Windows 原生 Python 的 stdin 传递大体量中文（真实规则响应约 44KB）发生字符转码破坏，`JSONDecodeError` 致规则总数/Oracle 分类误判失败（开发机 PASS=10 FAIL=2 exit 1） | `json_get <selector> <json_file>` 改为按 UTF-8 **文件路径**解析（`open(..., encoding="utf-8")`），禁止 stdin/pipe；所有 JSON 响应（health/login/rules/audit/dashboard）先 `curl -o` 落文件再解析；首页/metrics 非 JSON 保留 Bash 字符串匹配 |
| （跨运行时）`mktemp -d` 在 Git Bash 产出 POSIX 路径 `/tmp/...`，Windows 原生 Python `open()` 无法识别 | `json_get` 内经 `cygpath -w` 把路径转 Windows 形式再交 Python；Linux 无 cygpath 时原样透传，两端一致 |
| 临时文件曾回退到可预测的共享 `/tmp/_vd_*` 名 | 统一 `mktemp -d` 私有临时目录（由 `trap cleanup EXIT` 统一清理，见 §3.3）；创建失败即 FAIL 中止；目录内不留存登录响应与 token |

### 3.3 P2（第三轮 UAT-O-1632-R3-01）：信号捕获后只清理不退出

| 原缺陷 | 整改 |
|---|---|
| 上一轮 `trap cleanup EXIT HUP INT TERM` 把 HUP/INT/TERM 与 EXIT 合用同一 `cleanup`——`cleanup` 只 `rm -rf` 后正常返回，覆盖了信号的终止语义：实测向运行中脚本发 `TERM`，临时目录被删但脚本继续跑后续 HTTP 检查（`EXITED_AFTER_TERM=false`、curl 调用 7 次、7 秒后仍存活），且工作目录已删致后续 `curl -o` 二次失败 | 拆分：`trap cleanup EXIT`（唯一清理入口）+ `on_signal()` 显式退出。`on_signal` 先 `trap - HUP INT TERM` 复位（避免退出过程重入），再 `exit` 以 128+signo 约定码结束（HUP=129 / INT=130 / TERM=143）；`exit` 触发 EXIT trap 完成清理。上层 CI/systemd/人工取消可据退出码区分「被信号中止」与「验证失败(exit 1)」 |

## 4. 契约测试

`tests/test_verify_deploy_contract.py`（11 项，随全量 `tests/` 执行；无 bash 的平台自动跳过）：

1. 健康服务 + 正确口令：exit 0、`FAIL=0 SKIP=0`，121/42/R080/概览/metrics 全 PASS；
2. 服务不可达：健康项 FAIL、exit 1、输出不含任何 `[PASS]`；
3. 登录成功：token 被提取但输出全文不含 token（canary 串锁定）；
4. 错误口令：不回显响应体，后续检查记 SKIP；
5. 畸形 JSON 登录响应（200 但非法 JSON、开头即令牌样式）：不回显、不泄漏；
6. 30 万字节首页：无 SIGPIPE 假失败；
7. `bash -n` 语法通过（环境有 shellcheck 时一并跑 `-S warning`）；
8. **大型中文规则响应（P2）**：契约桩返回 ≥64KB、121 条含中文 `description/spec_source/fix_suggestion` 的真实特征响应，在 Windows Git Bash + Windows CPython 下运行须 `PASS=12 FAIL=0 SKIP=0`、exit 0、无 traceback/JSONDecodeError、无令牌泄漏（`test_large_utf8_rules_payload_on_git_bash`）；
9. **信号退出（R3-01）**：参数化 HUP/INT/TERM，用 `export -f` 导出阻塞 4s 的假 curl 使脚本处于请求中，经 bash 内部 `kill` 投递真实信号（规避 Windows Python 对子进程 SIGTERM 退化为 TerminateProcess、无法触发 bash trap），并以 `set -m` 作业控制避免后台脚本预忽略 SIGINT（POSIX 规定非交互 shell 的 `&` 异步命令忽略 INT/QUIT）；断言退出码 **129/130/143**、私有 TMPDIR 无残留、信号后不再发起下一请求（curl 仅 1 次）、输出无 token/口令/Authorization/traceback（`test_signal_exits_and_cleans_private_tmpdir`）。

登录凭据 fixture 刻意包含双引号与反斜杠，验证请求体确由 `json.dumps` 生成（O 第一轮 §6.3 第 6 步）；契约桩规则响应在模块加载时自证 ≥64KB 且 `oracle_compat=42`，防止退化成小型 ASCII 而漏检 P2（O 第二轮 §6.5 第四步）。

## 5. 准出核对（对应 O 第一轮 §6.4 + 第二轮 §6.6 + 第三轮 §6.8 关闭标准）

- [ ] 真实 v1.6.3.2 服务上运行：`FAIL=0 SKIP=0` 且退出码 0；
- [ ] 停止服务后复跑：健康检查明确 FAIL 且退出码 1，无 `[PASS]`（实测 PASS=0 FAIL=8 SKIP=3 exit 1）；
- [ ] 正确口令、错误口令、畸形响应三组日志均不出现 token / 登录响应体 / Authorization 值；
- [ ] 规则总数 121、Oracle 兼容 42、R080、概览、静态资源、metrics 全部由脚本真实验证；
- [ ] **P2 双运行时**：Windows Git Bash + Windows Python 对真实 121 条中文规则服务 `PASS=12 FAIL=0 SKIP=0` exit 0；Linux Python 3.11 对同一服务保持 12/0/0；
- [ ] **临时目录与信号（R3-01）**：正常/失败退出经 `trap cleanup EXIT` 清理；HUP/INT/TERM 经 `on_signal` 显式以 129/130/143 退出并触发 EXIT 清理，信号后不再发起后续请求（`EXITED_AFTER_TERM=true`）；
- [ ] 契约测试 **11/11** 通过；全量 `tests/` 与 `tests_3p/` 无新增失败；离线依赖 dry-run 通过。
