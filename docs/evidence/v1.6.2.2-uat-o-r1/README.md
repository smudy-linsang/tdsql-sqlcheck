# v1.6.2.2 智能体O 第一轮 UAT 证据

对应[主报告](../../UAT-v1.6.2.2-第一轮全项目用户验收测试报告-智能体O.md)。被测产品提交：`a698cfc1d5c8ffd0b11d7fb8f723fc5d16f02952`；旧产品基线：`0079300`。2026-08-28实测。本目录不是产品功能代码。

## 阅读顺序

1. 主报告§1、§5.1：本轮不准出，KFN的强制E999被原始SQL中的注释/字符串触发的豁免吞掉。
2. `49-kfn-complete-failopen.jpg`、`50-kfn-control-blocked.jpg`、`51-file-kfn-failopen.jpg`：真正浏览器单变量对照和文件选择上传。
3. `rule_coverage_119.md`：119条注册、114条命中及5条缺口；命中不等于所有边界通过。
4. `rule_probe_current.json`、`rule_probe_baseline.json`、`rule_diff.json`、`delta_classification.json`：1000条逐输入/逐规则差分和历史归因。
5. `browser_steps.json`及01–65同名JPG/TXT：操作时间、说明、页面URL、截图、DOM。不是HTTP模拟页面截图；也没有把只打开入口写成所有业务通过。原截图编码是JPEG，归档只修正扩展名并核对字节哈希未变；PDF渲染图仍为PNG。
6. `full_regression.txt/xml`、`implementation_matrix.txt`：两轮1384项回归及三依赖矩阵；`implementation_matrix_unseeded.txt`保留缺fixture的失败记录。
7. `supplemental_results.json`、`ops_report.pdf`及两页PNG：HTTP补证和PDF实际内容。浏览器下载最终落盘未被确认，不能拿HTTP文件冒充点击保存证据。

## 输入和脚本说明

| 文件 | 用途与边界 |
|---|---|
| `rule_probe.py` | 只解析/审核SQL，不执行SQL；1000条输入，保存75条独立判据失败，不会自动修产品或放宽期望 |
| `compare_baseline.py` | git archive旧提交到新临时目录，用同一probe比较；无分支切换；写当前目录结果，复跑前保护本轮档案 |
| `summarize_differences.py` | 将规则集合变化归类，保存相关产品文件基线SHA256 |
| `replay_user_reports.py` | 只读用户原HTML，提取SQL、校验与fixture相同并审核；源HTML不复制进Git |
| `original_gg77.sql` / `original_gg78.sql` | 原始报告提取的DDL，无行数据；摘要记录在original_*json |
| `make_browser_corpus.py` | 由引擎证据生成100条浏览器上传语料、KFN复现SQL、119账本 |
| `uat_kfn_comment.sql` | 一条带普通注释的CONSTRAINT UNIQUE；本轮页面错误通过，去注释正确E999 |
| `uat_mybatis.xml` | 三条合成SQL，覆盖正常参数、无WHERE DELETE、SELECT星号 |
| `uat_gateway_interf.txt` | 两条合成日志，12.5ms与1500.2ms，只含本地测试SQL |
| `prepare_uat.py` | 会创建隔离用户/连接/两张目标表；拒绝非tdsql_uat_o_前缀元数据库，不得连业务库 |
| `prepare_regression.py` | 为旧测试补显式admin与合成慢SQL前置数据；拒绝非tdsql_uat_o_reg前缀库 |
| `prepare_slow_fixture.py` | 为真实点击状态流程提供一条独立合成慢SQL记录；严格限制测试库 |
| `http_probe.py` | 仅loopback服务；会产生审核报告/操作日志，禁止向生产服务运行；密码从环境读取，不写token到证据 |
| `supplemental_probe.py` | 补充看板/导出/MonitorDB失败路径和本地DB版本、零巡检记录证据 |
| `service_pipeline_probe.py` | 对比单引擎与服务层的存储函数切分结果，基线和当前同样有旧问题 |
| `performance_probe.py` | 小型本地完整引擎审核成本探针（audit_sql，含解析及规则），不是并发压测或容量认证 |
| `validate_evidence.py` | 离线核对数量、链接、JSON/XML、截图配对和敏感信息；写validation及SHA256清单，不重跑UAT |

## 安全复跑指引

已签署证据不能原地覆盖。只读单引擎复跑可以把输出放入新的临时目录：

```powershell
# 在仓库根目录，使用已安装项目依赖的Python。
$uatReplayDir = Join-Path $env:TEMP ('uat-o-replay-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $uatReplayDir
$env:PYTHONUTF8 = '1'
C:/Python314/python.exe docs/evidence/v1.6.2.2-uat-o-r1/rule_probe.py --repo . --out (Join-Path $uatReplayDir 'rule_probe.json')
```

`rule_probe.py`把业务判据失败写入JSON；其进程正常结束不代表判据全部通过。检查 `failures`、`parse_error`、`fired`及`passed`。本次主版本有75条判据失败，其中72条必须E999缺失，不能忽略。

若要重跑写死当前证据目录的脚本，先保留此档案并使用完整仓库的独立测试副本；不要覆盖本轮结果再声称它仍对应a698cfc。无需新建Git分支。

数据库/HTTP/全量回归必须另配隔离测试库，使用正常配置的本地数据库凭据，不在命令、日志和报告打印口令。前置条件为：

- 数据库连接为本地测试服务；`SQLCHECK_DB_NAME` 使用相应UAT前缀。完整回归会写表、改鉴权设置，不得指向业务或共享开发元数据库。
- `AUTH_ENABLED=true`、`SCHEDULER_ENABLED=false`；bootstrap口令和 `UAT_O_PASSWORD` 由操作者通过安全环境提供。应用启动后用prepare_uat创建场景；本轮没有验收首次改口令流程。
- 全量/正式runner先在独立回归库运行prepare_regression，再运行下列命令。只补admin仍会使两个旧测试因缺慢SQL记录失败。

```powershell
C:/Python314/python.exe docs/evidence/v1.6.2.2-uat-o-r1/prepare_regression.py
C:/Python314/python.exe -m pytest tests -q
C:/Python314/python.exe -u docs/evidence/v1.6.2.2/run_all.py --mode implementation --matrix --keep
```

真实浏览器复测请照主报告矩阵逐项操作。尤其O-01必须做“有注释错误通过→删同一注释E999”的当前失败对照，修复后两者都应失败关闭。不能用脚本POST替代浏览器结果，也不能为复现而执行待审核DDL。

## 已知环境限制和残留

上午本机8002服务、四角色、两张本地MySQL表及合成日志完成测试；下午续接时8002已不可连接，浏览器控制能力不可用。65组原浏览器证据保留，ZK、下载保存、未做的删除/管理操作不补记为通过。8000原服务未改。

MySQL实际版本8.0.45，不是TDSQL。部分初始连接描述沿用了“MariaDB”测试描述，不能覆盖实际版本查询；本轮报告已按真实查询更正。目标库以外的Digest/上线检查可读到本地已有测试库元数据，未访问远程生产系统。

数据库fixture、DBA新建的离线连接、id51的“已优化”状态及临时archive均保留；未删除用户既有数据。密码、JWT和数据库连接密钥不在交付目录。`evidence_manifest.json`不包含自身，避免自引用哈希；主报告另有SHA256字段。

本目录局部`.gitattributes`禁止Git自动改变证据换行，保证清单SHA256在GitHub下载后仍可逐字节验证；不改变产品目录的LF策略。原始SQL及生成HTML/TXT保留原始空白，不能为了消除`git diff --check`的原始证据空白提示而改写测试输入或输出。新增说明文档和Python脚本单独执行格式检查。
