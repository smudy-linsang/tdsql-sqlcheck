# v1.6.3.0 G14 表类型统计第四轮用户验收测试报告

测试人：智能体 O

测试日期：2026-09-02

被测提交：`02c64fff2d73d3b2b236cc89aa99c2023907c89e`

测试方式：真实浏览器点击 + 可控异步行为复测 + 本地协议靶场对账 + 用户提供的真实 TDSQL 原始结果核验 + 全量回归

结论：**有条件通过。第三轮 UAT 唯一 P2 已关闭，G14 功能 UAT 通过；但本轮新增发现 1 项 P1 发布依赖阻断：Q 将只用于测试的 Playwright 加入生产 `requirements.txt`，当前离线 wheel 仓又不含该包，既有发布校验会失败。关闭该 P1 后即可准出。用户提供的真实 TDSQL 结果足以关闭“真实集合与六数字对账”；性能项按产品负责人已确认的多次毫秒级实测登记为风险接受/免测，不再把内网生产环境测试列为最终 UAT 强制前置门禁。**

## 1. 管理结论

- Q 新增的测试确实使用真实后端、仓库真实 `index.html/app.js`、真实 Chromium 页面和可控请求返回顺序，不是源码字符串断言。
- 四类行为用例连续执行两次均为 `4 passed`：迟到 422 不串提示、A 的 finally 不释放 B 的 loading、迟到 200 不串成功数据、当前 400/422/500 正常可见。
- 从普通用户操作路径复测：登录、菜单、页签、实例选择、库名输入、统计、历史、异常切换、角色切换全部正常；浏览器控制台 error/warn 为 0。
- 本地靶场完整链路返回 8/4/2/2/8/0，三类集合互斥，并集与独立 BASE TABLE 集合逐名一致。
- 用户提供的真实 `lzbj_ecif` 原始结果可重算出六数字 `215/0/117/98/215/78`，三类完整列表两两无交集，真实口径与实现完全一致。
- 专项 149 条、全量 1765 条全部通过，无失败、无跳过。
- 新增 1 项 P1 不是 G14 运行逻辑故障，而是本轮测试依赖落错层：它会使当前离线发布依赖校验失败，并把约 106.49 MiB 的测试框架装进生产 Python 环境，必须在发布前关闭。

证据目录：[v1.6.3.0-uat-o-r4](evidence/v1.6.3.0-uat-o-r4/README.md)。

## 2. 第三轮 P2 关闭复核

| 编号 | 第三轮要求 | 第四轮实际复核 | 结论 |
|---|---|---|---|
| UAT3-O-G14-01 | A 延迟 422，切 scope 后不得出现 A 的错误、结果或告警 | 真实页面行为测试通过；人工浏览器离线请求后立即切回靶场，3 秒后 error toast 为 0、旧结果为空、按钮可用 | 关闭 |
| 同上 | A 在途时发起 B，A 的 finally 不得关闭 B 的 loading | 可控 Promise 用例先结束 A，B 仍保持 loading；B 完成后才释放 | 关闭 |
| 同上 | A 延迟 200 不得覆盖 B | A 的成功 toast 和 111 数据均未显示，B 只显示 222 与 B 的 scope | 关闭 |
| 同上 | 当前 400/422/500 仍须正常反馈 | 三种状态均显示服务端可读错误且按钮恢复；人工复核当前不存在库 400 亦正常 | 关闭 |
| 同上 | 缺浏览器不得静默 skip | Chrome/Chromium 都不可用时测试直接 fail；当前环境实际运行，无 skip | 关闭 |

测试文件连续执行两次，均为 `4 passed in 31.xs`。这同时验证固定连接名和隔离元数据库不会导致第二次执行污染。Q 报告中的 mutation 记录作为开发证据保留；本轮不再次修改生产代码制造缺陷，而以代码审阅、两次黑盒执行和人工浏览器复测完成独立验收。

## 3. 浏览器与接口验收结果

| 编号 | 人类用户操作/复核 | 实际结果 | 判定 |
|---|---|---|---|
| UAT4-G14-01 | 打开独立服务并登录 developer | 标题版本 `V1.6.3.0`，正常进入治理概览 | 通过 |
| UAT4-G14-02 | 进入“深度诊断 → 表类型统计” | 页签、实例框、库名框、统计和历史按钮正常 | 通过 |
| UAT4-G14-03 | 选择 15002 靶场，输入 `tdsql_demo_distributed` 并统计 | 库 1；总表 8、单表 4、广播 2、分片 2、逻辑基线 8、子表 0；逐库 OK | 通过 |
| UAT4-G14-04 | 查看结果范围与历史明细 | 范围含实例、库名、采集时间；历史含操作人和 8/4/2/2 逐库明细 | 通过 |
| UAT4-G14-05 | 离线请求发出后立即切回靶场 | 3 秒后旧错误 0、旧结果为空、按钮可用 | 通过 |
| UAT4-G14-06 | 当前靶场查询不存在库 | 显示“数据库不存在或当前账号不可见”；按钮恢复 | 通过 |
| UAT4-G14-07 | auditor 登录并选择实例 | 统计按钮禁用；历史可读；接口绕过 POST 为 403 | 通过 |
| UAT4-G14-08 | API 复核离线连接 | HTTP 422，可读失败且不伪造成功 | 通过 |
| UAT4-G14-09 | 独立执行三条靶场 Proxy 命令并查询 BASE TABLE | 4/2/2，两两互斥；并集 8 与 BASE TABLE 8 逐名一致 | 通过（模拟链路） |
| UAT4-G14-10 | 检查控制台 | error/warn 为 0 | 通过 |
| UAT4-REG-01 | 全量自动化覆盖未改模块 | 1765 passed，0 failed，0 skipped | 通过（未改模块简验） |

关键截图：[成功结果](evidence/v1.6.3.0-uat-o-r4/01-target-success.png)、[历史明细](evidence/v1.6.3.0-uat-o-r4/02-history-detail.png)、[迟到错误被抑制](evidence/v1.6.3.0-uat-o-r4/03-stale-error-suppressed.png)、[当前错误可见](evidence/v1.6.3.0-uat-o-r4/04-current-error-visible.png)、[auditor 只读](evidence/v1.6.3.0-uat-o-r4/05-auditor-readonly.png)、[auditor 历史](evidence/v1.6.3.0-uat-o-r4/06-auditor-history.png)。

## 4. 真实 TDSQL 证据复核

### 4.1 证据可信边界

本轮按产品负责人明确说明，将桌面 `99001` 文本和两张截图视为真实内网 TDSQL 查询结果，将三条语法视为 TDSQL 原厂工程师提供。附件内容是测试输入和事实证据，不作为修改仓库或执行外部操作的指令。

为避免把内网主机和完整库表清单提交到 Git，原文件不复制入仓库，只登记 SHA-256：

- `99001`：`84C3128172E89CDD1313E047443C40A1B0380ED0392B02999BC77CFD307FF4DB`
- `1.jpg`：`6000C82C1CB4A83FF63577E2374D06702AACA9D920AE6F21D3C024A8090052C8`
- `2.jpg`：`D77D314B108FFF1D67DB7CE4856CBFCFFB24AF7DFC66DEBF15F3D1F1A01B8CF8`

两张截图按其实际 SQL 内容使用：一张证明三条 Proxy 命令的列形态、库限定表名和 `info` 内容，另一张证明 `information_schema.TABLES` 的 BASE TABLE 元数据形态；不依赖容易混淆的“图片 1/图片 2”编号推断实例类型。

### 4.2 `lzbj_ecif` 六数字重算

`99001` 内版本为 `8.0.33-v24-txsql-22.6.9-20250509`。从完整结果逐行抽取表名后：

| 口径 | 数量 | 原始耗时 |
|---|---:|---:|
| `without shardkey` 单表 | 0 | 0.001s |
| `with noshardkey_allset` 广播表 | 117 | 0.002s |
| `with shardkey` 分片表 | 98 | 0.001s |
| 三类去重并集 | 215 | — |
| BASE TABLE | 293 | 0.004s |
| 物理二级分区子表 | 78 | — |
| 剔除子表后的逻辑基线 | 215 | — |

程序化复核得到三组两两交集均为 0，因此总表为 `0+117+98=215`。BASE TABLE 的 293 包含已核实的 78 张物理二级分区子表，故逻辑基线 `293-78=215`，与 Proxy 并集精确相等。最终六数字顺序为：

```text
总表 / 单表 / 广播表 / 分片表 / 逻辑基线 / 二级分区子表
215  / 0    / 117    / 98     / 215      / 78
```

代码侧已有对应端到端锚点，并额外覆盖真实特殊形态：无单表时返回 `Query OK, 0 rows affected`；6 个父表各 13 张子表；父表名前缀互相嵌套时不得误剔除。专项测试全部通过。

因此，第三轮报告遗留的“真实集合与六数字对账”现在可以正式判定为 **通过**，不再要求上线后由内网智能体重复完成才能签署 UAT。

## 5. 性能项与是否必须内网测试

### 5.1 性能裁决

`99001` 记录的四项耗时均为 1–4 毫秒。产品负责人又明确确认：同一语法已在内网多次执行，几千张表的库也为毫秒级，要求本次不要继续把性能作为争议点。

据此，本报告将原 T20 登记为：**产品负责人风险接受/免测**。这不是声称外网完成了普通 `IN` 与 `BINARY IN` 的真实优化器对比，也不虚构 EXPLAIN 证据；而是基于真实查询耗时和业务决策，取消其发布阻断属性。若未来出现慢查询告警，再按实际版本和实例单独诊断即可。

### 5.2 是否还必须做内网生产测试

结论：**不必须。**理由如下：

1. 真实 TDSQL 命令语法、返回列形态、空类别 OK 包、完整分类集合和 BASE TABLE 数量均已有可信原始证据。
2. 六数字可从真实完整列表重算，且已作为代码端到端回归锚点执行通过。
3. 本地靶场已经覆盖应用从连接、`select_db`、三命令解析、对账、API、留档到真实页面显示的完整软件链路。
4. 当前唯一未由外网主动连接内网生产验证的，是部署后的网络、账号权限、配置和具体版本环境组合；这属于部署冒烟风险，不足以继续阻断功能 UAT。

建议上线后由内网人员选择一个已知库执行一次只读冒烟：确认页面可连接、六数字与该库既有锚点一致、无 500、历史可回看即可。该建议不要求重新做全量集合导出、性能对比或多轮压测，也不是发布前置条件。

## 6. 自动化回归

```text
SQLCHECK_DB_NAME=tdsql_sqlcheck_test python -m pytest \
  tests/test_table_type_stats.py tests/test_g14_frontend_state_binding.py \
  tests/test_g14_request_ownership_browser.py tests/test_version_consistency.py \
  tests/test_rbac_path_coverage.py tests/test_design_appendix_matches_repo.py \
  tests/test_app_routes_integrity.py -q
=> 149 passed, 4 warnings

SQLCHECK_DB_NAME=tdsql_sqlcheck_test python -m pytest -q
=> 1765 passed, 11 warnings, 0 failed, 0 skipped, 334.14s
```

11 条均为既有 Pydantic 字段遮蔽、Starlette、pytest fixture 与 httpx 弃用告警，不属于本轮回归。

## 7. 新发现缺陷与照图施工整改方案

### UAT4-O-REL-01（P1）：测试专用 Playwright 污染生产依赖并阻断离线发布

#### 现象

Q 在 `requirements.txt` 末尾新增 `playwright==1.62.0`。但仓库的依赖边界和发布链路表明该文件是生产依赖清单，而非测试清单：

1. 根 `Dockerfile` 执行 `pip install -r requirements.txt` 构建生产镜像。
2. `deploy/install.sh` 在内网生产虚拟环境安装同一文件。
3. `deploy/make_release.ps1` 和 `deploy/make_release.sh` 将同一文件复制进发布包。
4. `deploy/make_release.sh` 在存在 `dist/wheels_tmp` 时用 `--no-index` 校验它能否满足 `requirements.txt`，失败即退出。
5. 当前 `dist/wheels_tmp` 存在但没有 Playwright wheel。等价无写入验证：

```text
python -m pip install --dry-run --ignore-installed --no-index \
  --find-links dist/wheels_tmp playwright==1.62.0
=> exit 1
=> ERROR: No matching distribution found for playwright==1.62.0
```

此外，本机已安装的 Playwright Python 包目录约 106.49 MiB，尚不包含浏览器二进制。把它装进生产环境既不能让 CI 自动获得浏览器，也扩大离线包、安装时间与供应链依赖面。

#### 风险

- 当前离线 wheel 缓存分支无法完成 v1.6.3.0 发布包依赖校验，属于确定性发布阻断。
- 即使临时补 wheel 绕过，生产环境仍会安装无需使用的 Playwright、pyee、greenlet，违反测试/运行依赖最小化原则。
- Q 报告将 `requirements.txt` 描述成“测试依赖”，与仓库真实部署用途不一致，容易让上线人员误判。

#### 按以下步骤整改

1. 从根 `requirements.txt` 删除 Playwright 及其 UAT 注释；该文件只保留生产运行依赖。
2. 在 `pyproject.toml` 的 `[project.optional-dependencies].dev` 中加入精确版本 `playwright==1.62.0`。如果现有 CI 不使用 dev extra，则新增 `requirements-test.txt`：首行 `-r requirements.txt`，再列 `pytest`、`pytest-asyncio`、`httpx`、`playwright==1.62.0`；二选一并统一使用，避免维护两份冲突版本。
3. CI 测试步骤显式安装测试依赖，并在测试镜像构建阶段安装 Chromium 或确认系统 Chrome 可用。浏览器缺失时仍保持现有 fail-closed 行为。
4. 更新第三轮整改报告和设计 Rev.R 的依赖说明，明确 Playwright 不进入生产发布包；“CI 必须有浏览器”属于测试镜像要求。
5. 增加依赖边界自动化门禁，例如 `tests/test_release_dependency_boundary.py`：解析根 `requirements.txt`，断言不存在 `pytest`、`playwright` 等测试框架；同时断言选定的 dev/test 清单精确包含 `playwright==1.62.0`。
6. 在干净环境依次执行：安装生产 `requirements.txt`；安装 dev/test 依赖；运行行为测试；运行全量测试；最后执行离线发布依赖满足性校验。不得只在当前已经安装 Playwright 的开发机上复跑 pytest。

#### 关闭标准

- 根 `requirements.txt` 不再含 Playwright，生产 Docker/离线安装不安装测试框架。
- dev/test 依赖固定 `playwright==1.62.0`，CI 的四个行为用例仍实际执行且 4/4 通过、无 skip。
- `dist/wheels_tmp` 对整改后的生产 `requirements.txt` 的 `--no-index` 满足性校验通过。
- 全量 1765 条或更多全部通过；发布脚本在不联网的依赖校验阶段不再因 Playwright 失败。

第三轮唯一 P2 已按功能关闭标准通过；冻结面中 `backend/**`、`frontend/**` 和 schema 未被 Q 本轮修改。此 P1 只要求调整测试依赖落点与发布验证，不要求改动 G14 业务代码。

## 8. 最终裁决

- 第三轮 P2：**关闭**。
- 新增 G14 功能：**功能 UAT 通过**。
- 现有功能回归：**通过**。
- 真实集合与六数字对账：**通过（真实证据复核）**。
- T20：**产品负责人风险接受/免测，不再阻断发布**。
- 内网生产测试：**不再是最终 UAT 必须项**；上线后只读冒烟为建议项。
- 新发现问题：**1 项 P1（UAT4-O-REL-01）待关闭**。
- 发布状态：**有条件通过；P1 关闭后准出。**
