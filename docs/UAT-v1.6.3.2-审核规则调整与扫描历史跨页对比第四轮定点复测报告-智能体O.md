# v1.6.3.2 审核规则调整与扫描历史跨页对比
## 第四轮定点复测报告（智能体 O）

| 项目 | 内容 |
|---|---|
| 测试版本 | v1.6.3.2 |
| 被测提交 | `77cbb2534fdf6dda04fecd9e0f942cded8a193c7` |
| 复测缺陷 | `UAT-O-1632-R3-01`（P2） |
| 测试日期 | 2026-09-04 |
| 测试人 | 智能体 O（独立 UAT） |
| 测试结论 | **通过；缺陷关闭；已知软件缺陷 P0/P1/P2/P3 全部清零** |

---

## 1. 最终结论

Q 已按第三轮报告 §6.6 完成信号 trap 整改。O 独立复测确认：

```text
EXITED_AFTER_TERM=true
EXIT_CODE=143
TEMP_CREATED=1
TMP_CHILDREN=0
CURL_CALLS=1
LEAK=false
```

这六项共同证明：脚本收到 TERM 后明确退出，不再发起下一次 HTTP 请求；退出码保留信号语义；私有临时目录由 EXIT trap 清理；输出没有口令、token、Authorization 或 traceback。

HUP、INT、TERM 三类信号契约测试全部通过，退出码分别为 129、130、143。正常、错误口令、服务不可达、Windows Git Bash、Linux CPython 3.11、全量测试、三方测试和离线依赖门禁均无回归。

裁决如下：

- `UAT-O-1632-R3-01`：**关闭**；
- 第四轮定点复测：**通过**；
- v1.6.3.2 当前已知软件缺陷：**P0=0、P1=0、P2=0、P3=0**；
- 软件 UAT：**通过**；
- 生产总准出：仍需 GATE-1/2/3 人类责任方签字，以及目标麒麟 V10 SP3 主机部署后 12/0/0 验证。这些是外部发布前置，不再属于软件缺陷。

---

## 2. 改动核对

Q 将原实现：

```bash
trap cleanup EXIT HUP INT TERM
```

拆为：

```bash
trap cleanup EXIT
trap 'on_signal 129' HUP
trap 'on_signal 130' INT
trap 'on_signal 143' TERM
```

`on_signal` 先复位 HUP/INT/TERM trap，再以对应的 128+signo 退出码显式退出；`exit` 触发唯一的 EXIT trap 完成清理。实现与第三轮照图施工方案一致，没有改变正常业务检查顺序。

同时新增 `test_signal_exits_and_cleans_private_tmpdir[HUP/INT/TERM]` 三组契约测试，验证：

1. 信号前私有临时目录已经创建；
2. 信号后脚本退出而非继续；
3. curl 调用数保持 1，不发起下一请求；
4. 退出码为 129/130/143；
5. 临时目录无残留；
6. 输出无敏感信息和 traceback。

---

## 3. 缺陷关闭实测

### 3.1 自动化信号契约

```text
python -m pytest tests/test_verify_deploy_contract.py -q
11 passed in 55.99s
```

新增的 HUP、INT、TERM 三个参数化场景全部通过；原有大型中文 JSON、错误口令、畸形响应、超大首页、服务不可达等 8 项亦全部通过。

### 3.2 独立 TERM 人工控制器

O 未直接采信 Q 的自测结果，另用 Bash 导出的阻塞 fake curl 建立可控场景：

1. 创建隔离 `TMPDIR`；
2. fake curl 每次阻塞 4 秒并记录调用次数；
3. 后台启动真实 `deploy/verify_deploy.sh`；
4. 确认首个 curl 和私有临时目录已经出现；
5. 对脚本 PID 发送真实 `TERM`；
6. 等待进程并核对退出码、请求数、残留和泄漏。

有效实测原始摘要：

```text
════ 部署验证 v1.6.3.2 @ http://signal-test.invalid:18836 ════

EXITED_AFTER_TERM=true EXIT_CODE=143 TEMP_CREATED=1 TMP_CHILDREN=0 CURL_CALLS=1 LEAK=false
```

与第三轮的 `EXITED_AFTER_TERM=false`、后续检查继续执行形成明确对照，关闭标准达成。

第一次搭建控制器时遗漏导出计数变量，fake curl 未按预期阻塞；该次属于测试夹具错误，结果已作废，没有用于本报告结论。修正 `export COUNT` 后重新执行得到上述有效证据。

---

## 4. 正常与失败路径回归

### 4.1 Windows Git Bash + Windows CPython 3.14

真实 v1.6.3.2 隔离认证服务：

```text
PASS=12 FAIL=0 SKIP=0
VERIFY_EXIT=0
TMP_CHILDREN=0
```

规则总数 121、Oracle 兼容 42、R080、概览、静态资产和 metrics 均真实通过。

### 4.2 Debian/Linux CPython 3.11

```text
PASS=12 FAIL=0 SKIP=0
exit 0
```

证明 trap 拆分未破坏目标 Linux 正常路径。该证据来自 Debian 容器，不冒充目标麒麟真机。

### 4.3 错误口令

```text
PASS=7 FAIL=1 SKIP=3
VERIFY_EXIT=1
TMP_CHILDREN=0
LEAK=False
```

登录失败不回显响应体；规则、审核、概览明确跳过；不会误判部署通过。

### 4.4 服务不可达

```text
PASS=0 FAIL=8 SKIP=3
VERIFY_EXIT=1
TMP_CHILDREN=0
HAS_PASS=False
```

不可达路径没有任何假 PASS，失败语义保持正确。

---

## 5. 总回归结果

| 测试项 | 结果 |
|---|---|
| 部署验证契约 | **11 passed** |
| HUP/INT/TERM 信号退出 | **129/130/143，全部通过** |
| 独立 TERM 控制器 | **EXITED_AFTER_TERM=true，exit 143，curl=1，残留=0** |
| 明文凭据防复发 | **2 passed** |
| `bash -n deploy/verify_deploy.sh` | **exit 0** |
| 全量 `tests/` | **1815 passed, 28 skipped, 0 failed, 11 warnings** |
| 三方 `tests_3p/` | **125 passed, 1 skipped, 0 failed, 2 warnings** |
| manylinux2014 x86_64 / CPython 3.11 离线依赖 dry-run | **exit 0** |
| Git Bash 真实服务 | **12/0/0，exit 0** |
| Linux 真实服务 | **12/0/0，exit 0** |
| 错误口令 | **7/1/3，exit 1，无泄漏** |
| 服务不可达 | **0/8/3，exit 1，无假 PASS** |

本轮没有前端、规则引擎或扫描对比产品代码改动。第三轮真实浏览器已验证登录、v1.6.3.2、121 条规则以及跨页两条记录对比；本轮按定点复测原则不重复浏览器全流程，由全量 1815 项回归确认未发生相关代码回归。

`tests_3p` 的 1 项跳过与两条 warning 均为既有观察，本轮无新增失败或新缺陷。

---

## 6. 缺陷台账清零

| 缺陷 | 原级别 | 当前状态 | 关闭证据 |
|---|---|---|---|
| `UAT-O-1632-REL-01` | P1 | 已关闭 | 第一、二轮 Linux 真实服务及部署脚本契约 |
| `UAT-O-1632-R2-01` | P2 | 已关闭 | 第三轮 Git Bash/Windows Python 大型中文 JSON 12/0/0 |
| `UAT-O-1632-R3-01` | P2 | **本轮关闭** | HUP/INT/TERM 契约 + `EXITED_AFTER_TERM=true` + 全量回归 |

截至本轮，已知软件缺陷统计：

```text
P0=0  P1=0  P2=0  P3=0
```

没有遗留代码、脚本、测试或文档整改项需要 Q 继续处理。

---

## 7. 生产发布剩余前置

以下事项不改变“软件缺陷清零”结论，但仍是生产发布硬门禁：

1. GATE-1：G 在目标分布式实例完成版本与 UPDATE/DELETE LIMIT 只读语法验证，由 DBA + 内网运维签字；
2. GATE-2：A 已提供集中式零覆盖决策摘要，仍需林桑作为 DBA/需求方签字；
3. GATE-3：A 已提供门禁双向变化材料，仍需 G 完成内网预命中/流水线实测，并由 DBA/管理员和真实流水线负责人签字；
4. v1.6.3.2 部署到目标麒麟 V10 SP3 后运行 `deploy/verify_deploy.sh`，结果必须为 **PASS=12、FAIL=0、SKIP=0、exit 0**。

在上述外部前置关闭前，结论是“软件 UAT 通过，但生产发布尚未最终签批”；不得把自动化全绿替代组织责任人的风险接受。

---

## 8. O 的最终裁决

**第四轮定点复测通过，`UAT-O-1632-R3-01` 正式关闭，v1.6.3.2 达到已知软件缺陷全量清零。**

后续无需再交 Q 修复；项目可转入 GATE-1/2/3 人类签批和目标麒麟主机部署后验证阶段。完成后由 O 做生产最终准出核验即可。

证据索引：`docs/evidence/v1.6.3.2-uat-o-r4/README.md`。
