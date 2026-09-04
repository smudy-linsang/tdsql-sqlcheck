# v1.6.3.2 部署验证说明（verify_deploy.sh）

| 项目 | 内容 |
|---|---|
| 适用版本 | v1.6.3.2 起（v1.6.3.0 历史部署手册中的实测输出样例按 OUT-08 保留原貌，不适用本说明） |
| 脚本 | `deploy/verify_deploy.sh` |
| 整改依据 | UAT-O-1632-REL-01（P1，O 第一轮 UAT §6）；契约测试 `tests/test_verify_deploy_contract.py` |
| 目标环境 | 麒麟 V10 SP3 / Linux x86_64，应用 venv Python 3.11；开发机可用 Git Bash 复现 |

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

## 3. v1.6.3.2 整改内容（UAT-O-1632-REL-01）

| 原缺陷 | 整改 |
|---|---|
| 调用从未定义的 `J` 函数解析 JSON（5 处），登录被误判失败后携带空令牌连锁 401，脚本确定性 exit 1 | 改为白名单式 `json_get`（仅 version/token/total/oracle_count/r080_hit/today_count 六个 selector，Python 实现，不用 eval）；任何解析异常收敛为对应检查 FAIL，不输出 traceback |
| 首页 `echo "$FRONT" \| grep -q` 在 `pipefail` 下对大 HTML 触发 SIGPIPE 假失败 | 改为 Bash 字符串匹配 `[[ "$FRONT" == *TDSQL* ]]` |
| 健康探针 `ok "探针响应 $(curl ...)"` 无条件记 PASS | 先检查 `curl -fsS` 退出码，成功才 ok，失败记「健康探针不可达」 |
| 登录失败分支回显响应体前 120 字符——真实登录成功响应的开头就是管理员令牌，会泄漏进终端/CI 日志 | 登录响应体写入临时文件解析后即删；失败只输出 HTTP 状态码与固定文案，**绝不回显响应体 / Authorization / token 前缀** |
| 登录失败后规则/审核/概览检查伪装成业务接口故障（连续 401） | token 为空时上述检查明确记 `[SKIP] …（登录前置失败而跳过）`，且 SKIP>0 时 exit 1 |

## 4. 契约测试

`tests/test_verify_deploy_contract.py`（7 项，随全量 `tests/` 执行；无 bash 的平台自动跳过）：

1. 健康服务 + 正确口令：exit 0、`FAIL=0 SKIP=0`，121/42/R080/概览/metrics 全 PASS；
2. 服务不可达：健康项 FAIL、exit 1、输出不含任何 `[PASS]`；
3. 登录成功：token 被提取但输出全文不含 token（canary 串锁定）；
4. 错误口令：不回显响应体，后续检查记 SKIP；
5. 畸形 JSON 登录响应（200 但非法 JSON、开头即令牌样式）：不回显、不泄漏；
6. 30 万字节首页：无 SIGPIPE 假失败；
7. `bash -n` 语法通过（环境有 shellcheck 时一并跑 `-S warning`）。

登录口令 fixture 刻意包含双引号与反斜杠，验证请求体确由 `json.dumps` 生成（O §6.3 第 6 步）。

## 5. 准出核对（对应 O 报告 §6.4 关闭标准）

- [ ] 真实 v1.6.3.2 服务上运行：`FAIL=0 SKIP=0` 且退出码 0；
- [ ] 停止服务后复跑：健康检查明确 FAIL 且退出码 1，无 `[PASS]`；
- [ ] 正确口令、错误口令、畸形响应三组日志均不出现 token / 登录响应体 / Authorization 值；
- [ ] 规则总数 121、Oracle 兼容 42、R080、概览、静态资源、metrics 全部由脚本真实验证；
- [ ] 契约测试通过；全量 `tests/` 与 `tests_3p/` 无新增失败；离线依赖 dry-run 通过。
