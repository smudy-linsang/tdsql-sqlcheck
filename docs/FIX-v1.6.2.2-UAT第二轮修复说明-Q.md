# v1.6.2.2 UAT 第二轮修复说明

| 项 | 内容 |
|---|---|
| 修复人 | 智能体 Q |
| 修复日期 | 2026-08-28 |
| 依据 | O 的第二轮 UAT 报告（`UAT-v1.6.2.2-第二轮全项目用户验收测试报告-智能体O.md`） |
| 修复提交 | 见本提交 |
| 处置口径 | 保持 O 的原缺陷 ID 不变（O-01~O-08），逐项给出"已修/证据/测试"映射 |

---

## 一、处置总览（原 ID 不变）

| 原 ID | 等级 | 问题 | 本轮处置 | 证据 |
|---|---|---|---|---|
| O-01 | BLOCK | KFN 强制失败在异常返回路径被绕开 | **已修复（结构化信号驱动）** | §二 |
| O-02 | MAJOR | 前端版本号硬编码 1.6.2.1 | **已修复（5 处全部更新）** | §三 |
| O-03 | MAJOR | 网关报告只有页脚 | **已修复（受控文件名+逐请求独立临时目录）** | §四 |
| O-04 | MAJOR | 完整 HTML 经 v-html 污染应用 CSS | **已修复（iframe+srcdoc+sandbox 文档隔离）** | §五 |
| O-05 | MAJOR | 未采集数据却给健康分和业务数据 | **已修复（no_data 语义贯穿后端+前端+PDF）** | §六 |
| O-06 | MAJOR | PDF 漏掉真实网关记录 | **已修复（生产者补 reports 契约字段）** | §七 |
| O-07 | MINOR | 点击"标记"误开详情抽屉 | **已修复（事件冒泡阻止）** | §八 |
| O-08 | MAJOR | 独立 EXPLAIN 复用已关闭详情的库名 | **已修复（独立表单字段+连接池隔离）** | §九 |

本轮 8 项全部完成修复，无延期项。

---

## 二、O-01（BLOCK）：KFN 结构化强制失败关闭

### 根因（按 O §4.2 的链路逐条确认）

1. `parser_legacy.py:2133` preflight 已把 `known_fidelity_failures` 写入 parsed（结构化信号可靠存在）；
2. 但 ParseError 且恢复失败的路径（2207-2217）写 `parse_error=str(e)` 后**提前 return**，跳过了 2254 行的 `KNOWN_FIDELITY_GAP[...]` 消息归一化 → parse_error 没有 marker；
3. `_regex_pre_parse` 的 LOAD DATA 检测（2273-2277）只剥注释、不剥字符串字面量，`COMMENT='LOAD DATA'` 使 `has_load_data=True`；
4. `checker.py` 只在 parse_error 字符串里找 marker（不看结构化信号），KFN 被当普通解析错误；
5. LOAD 豁免压掉 E999。

### 修复内容（按 O §4.4 五条要求逐条落实）

**（1）强制失败优先读结构化信号** — `backend/engine/checker.py`：

```python
is_kfn = bool(getattr(parsed, "known_fidelity_failures", None)) or bool(
    parsed.parse_error and any(m in parsed.parse_error for m in _KFN_MARKERS))
```

`known_fidelity_failures` 非空即判 KFN，**无条件产出 E999，不进入任何豁免**。消息 marker 保留仅为向后兼容的展示通道。

**（2）补齐 return 路径的归一化** — `parser_legacy.py` ParseError 提前 return 路径：

```python
if parsed.known_fidelity_failures:
    parsed.parse_error = "KNOWN_FIDELITY_GAP[%s]: %s" % (
        ",".join(parsed.known_fidelity_failures), e)   # 保留原始异常文本
else:
    parsed.parse_error = str(e)
```

结构化类别负责决策，消息负责展示；原始异常不覆盖，可诊断性不下降。

**（3）普通解析错误的可靠豁免** — 新增模块级 `_strip_comments_and_literals()`（单遍扫描，剥离注释/字符串字面量/反引号标识符，保长度替换为空格），checker 的豁免判定改为：

```python
_head = _strip_comments_and_literals(sql)
is_proc_or_trigger = bool(re.match(r"\s*create\s+(?:or\s+replace\s+)?(?:definer\s*=\s*\S+\s+)?"
                                   r"(?:view|procedure|function|trigger)\b", _head, re.IGNORECASE))
is_load_stmt = bool(re.match(r"\s*load\s+(?:data|xml)\b", _head, re.IGNORECASE))
```

不再信任 `sql_type`（UNKNOWN 路径来自全文正则回退）与 `has_load_data`（字符串诱饵可污染）。字符串诱饵如 `SELECT 'CREATE VIEW' FROM`、`COMMENT='LOAD DATA'` 不再启动豁免。

**（4）不扩大拒绝域**：`_regex_pre_parse` 的 LOAD DATA 检测改用同一剥离器——真实 `LOAD DATA INFILE...` 仍正常识别（语句头保留），R042 对真实 LOAD 照常触发。

**（5）新增消费者级回归测试** — `tests/test_kfn_fail_closed.py`（33 例）：
- KFN 五类样例（含全部诱饵组合）必须 E999 + passed=False
- 全业务规则关闭（rule_overrides 全 disabled）时 KFN 仍强制失败
- 结构化信号驱动决策 + 消息归一化（marker + 原始异常并存）
- 字符串/注释诱饵 10 例不得豁免
- 真实 VIEW/PROCEDURE/FUNCTION/TRIGGER/LOAD 9 例豁免保留
- marker 字面量存储程序 3 例不误判

### 验证结果

- O 的 `edge_probe.py` 324 样例复跑：**`kfn_without_e999` 从 60 → 0**；252 个 KFN 全部 E999；全业务规则关闭时仍全部强制失败
- 控制组 22 个命中集合变化全部经核实属修复目标（字符串诱饵豁免消除 + R042 误报消除），real-object/control/marker-literal 37 条零变化
- 原剩余 2 个（kfn_literal:19/20）已覆盖在 252 组合中归零
- **全量回归：1417 passed / 0 failed / 0 skipped**（基线 1384 + 新增 33）
- 修复过程中发现并根治一个潜伏环境 bug：分析器子进程 `normalize_sql()` 延迟导入 `backend.services.sql_masking`，子进程 sys.path 无仓库根目录，只要日志含 db 字段即 ModuleNotFoundError——已在 `gateway_log_service.py` 的子进程调用注入 PYTHONPATH

---

## 三、O-02（MAJOR）：前端版本号统一

`frontend/index.html` 5 处硬编码 1.6.2.1 → 1.6.2.2：
- L8 `<title>`、L16 `app.css?v=`、L18 `theme-dark-blue.css?v=`、L30 登录页版本文案、L2758 `app.js?v=`

全仓复查：除 docs/evidence 历史证据与测试注释外，无残留 1.6.2.1 运行时引用。

## 四、O-03（MAJOR）：网关报告空正文

**根因**：`gateway_log_service.py` 把上传文件写成 `uploaded_<pid>.log`，分析器 `_organize_specific_files()` 按 `<type>_instance_<port>.<date>.<seq>` 命名规范识别，文件名不匹配 → 文件被静默跳过 → 报告只有页脚。

**修复**：
- 按调用方已知的 `log_type` 与连接端口构造受控文件名 `<type>_instance_<port>.<date>.0`
- 改用 `tempfile.mkdtemp()` 逐请求独立临时目录（原共享目录 + pid 命名在同进程并发上传时会互相覆盖），finally 中整目录清理

**验证**：20 行合成 interf 日志（含 3 条超阈值慢查询）→ 报告 HTML 从 8349 字符（纯页脚）→ 37368 字符（完整正文），统计指标正确（total=20/slow=3/max=1520ms）。

## 五、O-04（MAJOR）：v-html CSS 注入污染

**修复**（`frontend/index.html` 网关报告抽屉）：
- `v-html="gatewayHtml"` → `<iframe :srcdoc="gatewayHtml" sandbox="allow-scripts">`
- iframe 提供文档级隔离：报告内的 `body{...}`/`*`/`:root` 全局样式与脚本全部限制在 iframe 内
- `sandbox="allow-scripts"`：允许报告自带交互脚本运行，禁同源访问/表单/弹窗
- 抽屉加 `destroy-on-close` + iframe `v-if`：关闭即销毁释放

## 六、O-05（MAJOR）：假数据 → no_data 语义

**后端**（`ppt_report_service.py`）：
- 5 个空数据分支（巡检/大表/索引/慢SQL/网关/结构对比）全部改为 `data_status: "no_data"` + 真实零值，删除全部演示数据（biz.t_transaction 892万行等）
- 真数据分支去掺假：索引 `total_indexes: 120+len(rows)`/`pk_count: 45` 等硬编码基数 → None（本查询无法获知即标未知）；网关 `timecost_distribution` 95%/4% 拍脑袋分布 → 只给可推导的二元分布（<阈值 / >=阈值）；`error_count` 不再拿慢查询数冒充
- `get_dashboard_data`：只对 `data_status=ok` 的模块计分；全部无数据时 `score=None` + `score_status=no_data`（无数据不代表健康）

**前端**（大屏四卡片）：`no_data` 模块显示"未采集"，score 无数据显示"未评估"。

**PDF**：score=None 时显示"未评估（无采集数据）"，不再崩溃/虚构。

**验证**：空数据实例全模块 `no_data`，score=None；PDF 正常生成。

## 七、O-06（MAJOR）：PDF 网关记录

**根因**：PDF 模板消费 `modules.gateway_analysis.reports`，生产者只提供 summary/daily_stats 而无 reports → 永远走"暂无记录"分支。

**修复**：`_get_gateway_analysis_data` 查询最近 5 条网关报告记录并提供 `reports` 列表（id/时间/总请求/慢查询/最大/平均耗时），空数据分支同步提供 `reports: []`。

**验证**：插入真实网关记录 → PDF 网关章节正确输出记录（不再显示"暂无"）；测试后清理数据。

## 八、O-07（MINOR）：标记按钮事件冒泡

**根因**：慢SQL列表 919 行有 `@row-click` 开详情；930 行 el-dropdown 的"标记"触发器未阻止冒泡。

**修复**：标记下拉外包 `<span @click.stop>`，点击标记不再冒泡到行级 row-click。详情/导出按钮原有 `@click.stop` 保持不变。

## 九、O-08（MAJOR）：EXPLAIN 独立表单上下文

**前端**（`app.js` + `index.html`）：
- EXPLAIN 页新增可见的"数据库名"输入框 `explainDbName`（可选，留空用连接默认库）
- `analyzeExplainBySql` 只读 `explainDbName`，不再读全局 `slowDetail.db_name`
- `goExplainFromSlow`（显式"从该慢 SQL 去分析"）原子复制来源上下文到表单（含 db_name），并清空 slowDetail
- 切换实例时清空库名；慢SQL详情抽屉关闭时 `slowDetail=null`（清除残留状态）

**后端**（`slow_query_service.py`）：
- 请求级 `db_name` 与连接默认库不同时，改用**独立临时连接池**（不注册进共享 registry，用完 finally 关闭）——修复了 `registry.register()` 替换共享连接配置的污染隐患
- 目标库不存在（1049）/USE 失败 → 抛 ValueError（API 层映 400 可理解错误），不再静默继续在默认库上 EXPLAIN

---

## 十、复测入口（给 O 第三轮）

| 项 | 复测要点 | 期望 |
|---|---|---|
| O-01 | 重跑 edge_probe.py（324 样例）+ 原 75 KFN | `kfn_without_e999` 为空；控制组变化均为豁免消除方向 |
| O-01 测试 | `pytest tests/test_kfn_fail_closed.py` | 33 passed |
| O-02 | 新会话登录页/系统信息/health 三处版本 | 均为 1.6.2.2 |
| O-03 | 上传任意命名 interf 日志 → 查看报告 | 报告有正文（统计+明细），非纯页脚 |
| O-04 | 打开网关报告 → 关闭 → 看慢SQL抽屉 | body 样式不被污染，刷新前后一致 |
| O-05 | 无采集数据实例的大屏/PDF | 显示"未采集/未评估"，无虚构数字 |
| O-06 | 有网关记录的实例导出 PDF | 网关章节含真实记录表格 |
| O-07 | 慢SQL列表点"标记" | 只弹下拉菜单，不开详情抽屉 |
| O-08 | 看慢SQL详情→关→独立EXPLAIN选另一库 | 使用表单库名，不串旧库；错误库名返回可理解 400 |
