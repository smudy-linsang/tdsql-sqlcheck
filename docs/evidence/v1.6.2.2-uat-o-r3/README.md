# 第三轮 UAT 证据索引与复现说明

测试人：智能体O；2026-08-28/29；被测main提交 `1596e8b4819d17beb6507914c4592b0be184a29c`。

[第三轮报告](../../UAT-v1.6.2.2-第三轮全项目用户验收测试报告-智能体O.md)是结论入口。此目录保留失败、成功和无法确认的结果；不能把目录内每个JSON/截图都当成一个“通过用例”。

## 证据导航

| 文件 | 内容 |
|---|---|
| `summary.json`、`rule_coverage_119.md` | 从原始记录计算的数量、116条实际命中、3条既有未验证缺口 |
| `full_regression.txt/xml` | 独立全量1417通过，10警告 |
| `implementation_matrix.txt` | 正式runner哈希门禁exit3的完整输出，未修改它绕过验证 |
| `independent_matrix.json`、`manifest_*.txt` | 正式门禁之外独立执行合同680+Q测试33；三版各713通过 |
| `rule_probe_current.json` | 本轮重新执行的1000条输入；3个原有精确判据差异保留 |
| `round3_diff.json` | 对比第二轮1000条留存基线及本轮重跑324条基线 |
| `edge_*.json` | 324条各版本及基线，252条KFN含全部业务规则禁用的独立门禁诊断 |
| `head_*.json`、`load_*.json` | 77条语句头边界、27条LOAD注释矩阵的改前改后结果；新BLOCK |
| `http_results.json` | 第一批614次HTTP；17条差异不隐藏 |
| `service_current/baseline.json` | 17条引擎/服务差异在新旧服务层完全相同 |
| `supplemental_core.json` | 5条规则补测 + 60次LOAD三入口HTTP |
| `explain_context_results.json`、`ephemeral_cleanup.json` | 默认/跨库500，以及只在诊断进程补缺失符号后的潜在清理缺陷 |
| `gateway_boundary.json/html`、`gateway_interaction.json` | 空/非法/并发日志及真实控件点击前后DOM |
| `gateway_mask_canary.*` | 明显合成、非真实秘密的原文脱敏范围检查 |
| `index_report_contract.json` | 本轮真实浏览器索引运行的数据库finding与大屏/PDF错位 |
| `report_zero_contract.json` | 明确标注的合成“已完成0问题”父记录，非真实扫描结果 |
| `daily_guard.json` | 实际日常巡检400与空compare200；和截图44共同证明错误成功提示 |
| `browser_steps.json` | 逐组实际浏览器操作、时间、URL；同名JPEG和DOM快照配对 |
| `browser_*download_observation.json` | 真实按钮点击但工具未观察到download事件；不是认定产品导出失败 |
| `*.pdf`、`*_pdf.txt`、`*_page-*.png`、`pdf_checks.json` | 实际应用导出4份PDF、5页渲染及文本核对 |
| `performance_*.json` | 三种列规模的小型解析微基准，非系统压力测试 |
| `official_basis.md` | 本轮核验的腾讯云TDSQL和W3C官方来源 |
| `validation.json`、`evidence_manifest.json` | 离线一致性检查和SHA256清单，不计作额外产品测试 |

## 安全与运行边界

- UAT服务为loopback8003，元数据库固定 `tdsql_uat_o_r3_1622_20260828`，鉴权开、调度关；未触碰原8000进程。
- 验收收尾已停止本轮启动的8003进程，测试库与合成fixture保留供复核；如需浏览器重跑，应按上述环境重新启动服务。
- pytest另用 `tdsql_uat_o_reg_r3_full_20260828`；矩阵另用 `tdsql_uat_o_reg_r3_matrix_20260828`。旧测试依约自行控制鉴权；不能把pytest环境当生产部署建议。
- MySQL是本地8.0.45，不是TDSQL。被审核DDL和LOAD绝不执行；目标表只有合成数据。Digest/上线/应急读取同一MySQL其他测试库指标，不是生产指标。
- 测试账号口令从 `UAT_O_PASSWORD` 环境变量提供；不要将口令、JWT、真实连接密码写入证据。读取已有合成目标的数据库凭据仅经项目正常配置，不复制到报告。
- DBA通过浏览器新增并编辑一个 `127.0.0.1:1/uat_o_r3_browser_only` 离线连接，无真实目标数据；保留以复核，没有删除既有实例/数据，没有修改默认连接。
- `prepare_*`、HTTP调用会在明确隔离的测试元库创建fixture/历史记录；不要对生产服务运行。不需要重跑时优先做下面的离线核验。
- 临时池探针是白盒诊断：只在本进程补缺失import，保留真实连接引用来观察close，再显式清理；没有改产品源文件，不能据其结果声称当前HTTP已经绕过NameError。
- 当前脚本中的临时venv/基线路径是本次环境证据，不是跨机器通用安装位置。异机应新建各版本的独立虚拟环境并安装项目依赖，将对应路径参数替换后运行；不得静默用一个版本代替三版本。

## 复现顺序

在相同被测提交、已配置本地MySQL及隔离库的前提下：

```powershell
python docs/evidence/v1.6.2.2-uat-o-r3/run_round3.py full
python docs/evidence/v1.6.2.2-uat-o-r3/run_round3.py matrix
python docs/evidence/v1.6.2.2-uat-o-r3/run_probes.py
```

第二条在本被测提交预期exit3，须保留失败。独立矩阵由 `independent_matrix.py` 执行（先核对其中三版venv路径），不修改正式runner或冻结manifest。

LOAD专项不执行SQL，仅解析和审核：

```powershell
python docs/evidence/v1.6.2.2-uat-o-r3/load_comment_matrix.py --repo C:/Codex/tdsql-sqlcheck --out C:/Temp/uat-load-recheck.json
```

分别使用29.0.0、30.14.0、30.17.0环境；改前对照来自 `git archive d9fad2a` 的临时解包，不需要新分支。不要把新结果覆盖此轮已签名证据。

`http_round3.py` 和其他HTTP探针使用自有登录会话，固定loopback8003；需先完成 `prepare_browser_fixture.py` 和第一轮 `prepare_uat.py` 的隔离fixture配置。`prepare_index_fixture.py` 仅创建本轮独立索引库。`supplemental_round3.py`、`explain_probe.py`、`report_*probe.py`、`gateway_*probe.py`各自产物见脚本，执行前核对目标。

浏览器需登录后按 `browser_steps.json` 重走。输入文件沿用第一轮 `uat_core_rule_corpus.sql`、`uat_mybatis.xml`、`uat_gateway_interf.txt`，新反例为本目录 `uat_r3_load_xml_guard.sql`。截图不代替真实TDSQL成功链路、原生下载落盘和全模块所有子功能的验收。

PDF渲染使用本地Poppler `pdftoppm -png`；文本检查用bundled Python的pypdf。字体回退到SimSun，五页均人工目视；内容错误原样保留。

## 离线一致性核验

```powershell
python docs/evidence/v1.6.2.2-uat-o-r3/validate_evidence.py
```

该命令不访问服务、不改库，只检查SHA256、JSON/文本断言、截图配对、报告本地链接。维护者首次封存使用 `--seal`；以后核验不得用重新seal来掩盖材料改变。`evidence_manifest.json` 不自我哈希，`validation.json` 是可重新生成的核验输出，也不纳入自身清单。

`.gitattributes` 的 `-text` 保留原始证据字节，JSON/PDF提取文本中的CRLF及生成HTML原始行尾空格不做格式化；作者编写的报告、说明和Python脚本单独通过识别CRLF的空白检查，不用清洗证据伪装成全目录格式检查通过。

## 记录纠偏

- 07点击折叠时内容仍可见，不算交互通过；37和 `gateway_interaction.json` 是正式反例。
- 15文件名虽然写default-success，实际是受控输入清空未成功的操作中间态，不算通过；键盘清空并成功的证据是16。
- 32上线检查尚未结束，34才是完成结果；39无日期仅覆盖表单保护，44才是真正选择日期并提交。
- 42记录提交中的状态，43含SQL统计monitordb不可用提示；后补HTTP也证明该环境限制。
- 开发角色扫描/大表下拉第一次按别的页面标签匹配失败，随后按实时DOM中的带圆点标签重试成功；57/58保存的是实际非空选项，不把定位器错误当产品故障。
