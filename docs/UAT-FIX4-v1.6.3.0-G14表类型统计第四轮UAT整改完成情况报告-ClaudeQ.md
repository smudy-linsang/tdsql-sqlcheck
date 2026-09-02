# UAT-FIX4-v1.6.3.0 深度诊断·表类型统计（G14）第四轮 UAT 整改完成情况报告

| 项 | 内容 |
|---|---|
| 整改对象 | `UAT4-v1.6.3.0-G14表类型统计第四轮用户验收测试报告-智能体O.md`（结论：有条件通过；功能 UAT 通过、真实六数字对账通过、T20 风险接受免测；新增 1 项 P1 发布依赖阻断） |
| 整改基线 | `main` / `02c64ff`（第三轮 UAT 整改提交） |
| 整改人 | 智能体 Q |
| 整改日期 | 2026-09-02 |
| 设计定版 | `DESIGN-v1.6.3.0-…详细设计说明书.md` **Rev.R → Rev.S** |
| **整改结论** | **P1 已关闭：playwright 从生产依赖清单移至 dev extra，离线发布校验通过，行为用例不受影响。可以进入人工测试与打包发布环节。** |

---

## 1. 缺陷与整改

### UAT4-O-REL-01（P1）：测试专用 Playwright 污染生产依赖并阻断离线发布

**根因（如实承认）**：第三轮整改时我把 `playwright==1.62.0` 加进了根
`requirements.txt`——但该文件是**生产**依赖清单（`Dockerfile` 构建、
`deploy/install.sh` 内网安装、`make_release` 发布包共用），而离线 wheel 仓
`dist/wheels_tmp` 没有 playwright，发布校验会确定性失败。且即便补 wheel 绕过，
生产环境也会装入 100+ MiB 用不到的测试框架（含 pyee/greenlet 传递依赖）。
这是我上一轮引入的次生灾害，与"依赖最小化"原则相违。

**整改（按报告 §7 六步逐项）**：

| # | 报告要求 | 落实 |
|---|---|---|
| 1 | 从根 `requirements.txt` 删除 Playwright 及 UAT 注释 | ✅ 已删（该文件恢复纯生产依赖） |
| 2 | 二选一固定测试依赖 | ✅ 选 **pyproject.toml `[project.optional-dependencies].dev`**（项目既有机制，报告方案①）：精确追加 `playwright==1.62.0` 并注释说明用途与浏览器要求 |
| 3 | CI 显式安装测试依赖 | ✅ dev extra 即 CI 安装口径（`pip install -e ".[dev]"`）；浏览器要求已在注释与测试文件头写明 |
| 4 | 更新第三轮整改报告与设计文档的依赖说明 | ✅ FIX3 报告两处更正（标注 Rev.S 更正）；设计文档 Rev.R→Rev.S（头部/状态/§9.1/§11 Rev.R 段末补更正/修订记录） |
| 5 | 依赖边界自动化门禁 | ✅ 新增 `tests/test_release_dependency_boundary.py`（3 项）：生产清单不含 pytest/playwright/pyee/greenlet 等测试框架；dev extra 精确固定 `playwright==1.62.0`；dev extra 含既有测试栈 pytest/pytest-asyncio/httpx |
| 6 | 干净环境验证 | ✅ 见 §2 验证证据 |

## 2. 验证证据（逐项对报告关闭标准）

| 关闭标准 | 实测 |
|---|---|
| 根 requirements.txt 不再含 Playwright，生产 Docker/离线安装不装测试框架 | ✅ `requirements.txt` 已恢复纯生产依赖；门禁钉住 |
| dev/test 依赖固定 `playwright==1.62.0`，行为用例仍实际执行 4/4 | ✅ `test_g14_request_ownership_browser.py` 复跑 **4 passed**（真实浏览器） |
| `dist/wheels_tmp` 对整改后生产清单的 `--no-index` 满足性校验通过 | ✅ `pip install --dry-run --no-index --find-links dist/wheels_tmp --platform manylinux2014_x86_64 --python-version 3.11 --implementation cp --abi cp311 --only-binary=:all: -r requirements.txt` → **Would install 31 包，无 playwright，无 ERROR**（注：该仓为 manylinux 目标平台 wheels，本机 Windows 直接校验会因平台标签误报，故按发布目标平台模拟——这与 `make_release.sh` 的目标环境一致） |
| 全量 1765 条或更多全部通过；发布脚本离线校验阶段不再因 Playwright 失败 | ✅ 全量 **1768 passed, 0 failed, 0 skipped**（1765 + 3 新门禁） |

**关于一次中间态的解释（非回归）**：全量回归曾出现 28 skipped——查证为
`test_sit_rules.py`/`test_uat_rules.py` 是打 `localhost:8000` 真实服务的集成测试，
彼时 8000 无服务在跑，按其既定 skip 语义跳过。临时起 8000 服务复跑该两文件
**36 passed 0 skipped**，再跑全量 1768 全绿零跳过。与本次整改无关，如实记录。

## 3. 变更清单

| 文件 | 变更 |
|---|---|
| `requirements.txt` | −5 行（删除 playwright 及注释，恢复纯生产依赖） |
| `pyproject.toml` | +5 行（dev extra 精确固定 `playwright==1.62.0` + 用途注释） |
| `tests/test_release_dependency_boundary.py` | 全新 3 项依赖边界门禁 |
| `docs/UAT-FIX3-…` | 两处依赖落点表述更正（标注 Rev.S） |
| `docs/DESIGN-…-Rev.S` | 头部/状态/§9.1/§11 更正注记/修订记录 |

**冻结面核查**：`backend/**`、`frontend/**`、`backend/schema/**`、119 条规则、
`auth_service.py`——**本轮全部零改动**（报告确认：此 P1 只要求调整测试依赖落点
与发布验证，不要求改动 G14 业务代码）。

## 4. 结论

第四轮 UAT 唯一 P1 已关闭：测试依赖落点更正为 dev extra、生产发布链路不再含
Playwright、离线 wheel 仓满足性校验通过、行为级用例 4/4 不受影响、全量 1768 全绿。

**G14 全部测试阶段（设计评审 6 轮 + SIT 2 轮 + UAT 4 轮）至此闭环，可进入人工测试与打包发布环节。**
