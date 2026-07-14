# TDSQL Gateway 日志综合分析工具

解析 TDSQL Gateway 产生的多种日志文件，自动生成全面的分析报告。支持单节点分析、多节点数据整合、集中采集一键出报告。

**作者**: lynx,boogqwang

---

## 批量分析方案：ZK 节点批量分析多个 Proxy 节点的 interf 日志

使用 `interf_batch_analyze.sh` 从 ZK/调度节点通过 SSH（root 用户）批量分析多台 Proxy 节点的 interf 日志。脚本自动完成：**SCP 上传脚本/配置 → SSH 远程分析 → SCP 回收结果 → 清理敏感配置**。

### 1. 配置 `tdsql_env.conf`

在 `[gateway_proxies]` 中填写所有 Proxy 节点信息，在 `[ssh]` 中填写 root 密码：

```ini
[ssh]
host=119.45.190.87
user=root
password=你的SSH密码

[gateway_proxies]
# 格式: proxies_N=业务名称,Proxy节点IP,端口号,数据库用户名,数据库密码
# 同一 IP 多端口分多行写，脚本会按 IP 分组，只在对应 IP 上分析属于它的端口
proxies_1=合约管理,10.3.3.1,15001,dbuser,dbpassword
proxies_2=合约管理,10.3.3.1,15002,dbuser,dbpassword
proxies_3=交易中心,10.3.3.2,15003,dbuser,dbpassword
```

> **分组逻辑**：脚本按 Proxy IP 分组。上例中 `10.3.3.1` 只执行 15001、15002 的分析，`10.3.3.2` 只执行 15003 的分析，不会跨节点执行不属于该 IP 的端口。

### 2. 一键批量分析

```bash
cd /path/to/tdsql-toolkit/gateway_log_analysis

# 分析今天的日志（推荐）
bash interf_batch_analyze.sh --dates $(date +%Y-%m-%d)

# 分析指定日期
bash interf_batch_analyze.sh --dates 2026-04-01

# 分析指定时间段
bash interf_batch_analyze.sh --dates 2026-04-01 --time-range 14:00-16:00

# 分析多天
bash interf_batch_analyze.sh --dates 2026-03-30 2026-03-31

# 加大超时时间（日志量大时使用，默认 600 秒）
bash interf_batch_analyze.sh --dates 2026-04-01 --timeout 1200

# 预览模式（只打印操作，不实际执行）
bash interf_batch_analyze.sh --dates 2026-04-01 --dry-run

# ── 只分析指定实例（新增，支持 5 种写法，可多个并存）──

# 1）按 proxies_N 序号（支持 #N 或 N）
bash interf_batch_analyze.sh --dates 2026-04-01 --instances '#1' '#3'

# 2）按「业务名:端口」精确定位一个实例
bash interf_batch_analyze.sh --dates 2026-04-01 --instances 合约管理:15001

# 3）按「IP:端口」精确定位一个实例
bash interf_batch_analyze.sh --dates 2026-04-01 --instances 10.3.3.1:15002

# 4）按业务名 → 该业务名下的所有端口
bash interf_batch_analyze.sh --dates 2026-04-01 --instances 合约管理

# 5）按 IP → 该 IP 下的所有端口
bash interf_batch_analyze.sh --dates 2026-04-01 --instances 10.3.3.1

# 多 SPEC 混合（取并集）
bash interf_batch_analyze.sh --dates 2026-04-01 --instances 合约管理:15002 交易中心
```

> **未命中时的友好提示**：若 `--instances` 指定的 SPEC 一个都没匹配到，脚本会报错退出并打印当前配置文件中**所有可用实例清单**（含 `#序号`、业务名、`IP:端口`），方便直接复制修正。

脚本会自动完成以下操作：
1. 读取 `[gateway_proxies]` 配置，按 Proxy IP 分组
2. 对每个 Proxy IP：SCP 上传 `interf_deep_analysis.py` + `tdsql_env.conf` 到 `/tmp/`
3. SSH 远程逐个执行该 IP 下每个端口的 interf 分析
4. SCP 回收结果文件（tar 打包回传）到本地 `output/{日期}/`
5. 清空远端 `tdsql_env.conf`（含敏感信息）

### 3. 最终目录结构

```
gateway_log_analysis/output/
└── 2026-04-01/
    ├── 合约管理_10.3.3.1_15001_2026-04-01_143022_report.html
    ├── 合约管理_10.3.3.1_15001_2026-04-01_143022_sql_timecost_detail.csv
    ├── 合约管理_10.3.3.1_15001_2026-04-01_143022_sql_pattern_summary.csv
    ├── 合约管理_10.3.3.1_15001_2026-04-01_143022_sql_explain_schema.csv
    ├── 合约管理_10.3.3.1_15002_2026-04-01_143035_report.html
    ├── ...
    ├── 交易中心_10.3.3.2_15003_2026-04-01_143055_report.html
    ├── 交易中心_10.3.3.2_15003_2026-04-01_143055_sql_timecost_detail.csv
    ├── 交易中心_10.3.3.2_15003_2026-04-01_143055_sql_pattern_summary.csv
    └── 交易中心_10.3.3.2_15003_2026-04-01_143055_sql_explain_schema.csv
```

### 4. 完整参数说明

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--dates DATE [...]` | （必填） | 分析日期，格式 YYYY-MM-DD，可指定多个 |
| `--time-range HH:MM-HH:MM` | 无（全天） | 只分析指定时间段 |
| `--instances SPEC [...]` | 全部 | 只分析指定实例，支持 `#N`/`IP:PORT`/`业务名:PORT`/`业务名`/`IP` 五种写法，可多个并存（取并集） |
| `--config-file FILE` | 自动查找 | 配置文件路径 |
| `-o, --output-dir DIR` | `output/{日期}` | 本地输出目录 |
| `--ssh-user USER` | `root` | SSH 用户名 |
| `--ssh-port PORT` | `22` | SSH 端口号 |
| `--timeout SEC` | `600` | 远程分析命令超时 |
| `--keep-remote` | 否 | 保留远端配置文件（默认清理） |
| `--dry-run` | 否 | 预览模式，不实际执行 |

> **提示**：如果脚本和日志在同一台机器上（ZK 和 Proxy 同机部署），可以直接本地执行：
> ```bash
> cd /path/to/tdsql-toolkit/gateway_log_analysis
> 
> # 一键分析所有实例（推荐）
> python3 interf_deep_analysis.py --dates 2026-04-01 --config-all
> 
> # 只分析某个实例
> python3 interf_deep_analysis.py --dates 2026-04-01 --config-index 1
> ```

---

## 推荐执行命令

```bash
# 通过端口号快速分析（推荐）
python3 analyze_gateway_log.py -p 15001:15020 -o report.html

# 只分析 interf 日志（跳过其他日志类型，加速分析）
python3 analyze_gateway_log.py -p 15001:15020 --log-types interf -o report.html

# 指定具体的 interf 日志文件分析（单个文件）
python3 analyze_gateway_log.py --files /data/tdsql_run/15001/gateway/log/interf_instance_15001.2026-04-01.0 -o report.html

# 指定多个 interf 日志文件分析（空格分隔）
python3 analyze_gateway_log.py --files \
  interf_instance_15001.2026-03-25.0 \
  interf_instance_15001.2026-03-27.0 \
  -o report.html

# 使用通配符批量匹配 interf 文件
python3 analyze_gateway_log.py --files interf_instance_15001.2026-03-2*.0 -o report.html

# 分析指定日志目录
python3 analyze_gateway_log.py -d gateway_log_15001 -o report.html

# 多节点集中采集（使用 bash 执行）
bash gateway_collect.sh --dates 2026-03-01

# 合并多个节点的分析数据
python3 merge_gateway_reports.py collected_data/*.json.gz -o merged_report.html

# 查看版本号
python3 analyze_gateway_log.py -V
bash gateway_collect.sh -V

# interf 日志深度分析（SQL 耗时细分 + 去重聚合 + 数据库联动）
# 一键分析所有实例（读取 [gateway_proxies] 配置，推荐）
python3 interf_deep_analysis.py --dates 2026-04-01 --config-all

# ── 从 ZK 节点批量分析多台 Proxy 节点（推荐，自动 SCP + SSH + 回收）──

# 分析今天
bash interf_batch_analyze.sh --dates $(date +%Y-%m-%d)

# 分析指定日期 + 时间段
bash interf_batch_analyze.sh --dates 2026-04-01 --time-range 14:00-16:00

# 加大超时（日志量大时）
bash interf_batch_analyze.sh --dates 2026-04-01 --timeout 1200

# 预览模式
bash interf_batch_analyze.sh --dates 2026-04-01 --dry-run

# 只分析指定实例（按序号 / IP:PORT / 业务名:PORT / 业务名 / IP，可多选混用）
bash interf_batch_analyze.sh --dates 2026-04-01 --instances '#1' '#3'
bash interf_batch_analyze.sh --dates 2026-04-01 --instances 合约管理:15001
bash interf_batch_analyze.sh --dates 2026-04-01 --instances 10.3.3.1

# ── 本地直接分析（ZK 和 Proxy 同机部署时使用）──

# 分析单个实例
python3 interf_deep_analysis.py --dates 2026-04-01 --config-index 1

# 分析多天
python3 interf_deep_analysis.py --dates 2026-03-30 2026-03-31 --config-all

# 只分析指定时间段（如下午 2 点到 4 点）
python3 interf_deep_analysis.py --dates 2026-04-01 --config-all --time-range 14:00-16:00

# 组合使用：某天某时段 + 单个实例
python3 interf_deep_analysis.py --dates 2026-04-01 --config-index 1 --time-range 09:00-12:00

# 不连数据库（只做 SQL 统计）
python3 interf_deep_analysis.py --dates 2026-04-01 \
  --name 合约管理 --proxy-ip 10.206.0.16 --port 15002

# 输出文件说明（每个实例生成 4 个文件，统一输出到 output/{日期}/ 目录）:
#
# {业务名}_{IP}_{端口}_{日期}_{时间}_report.html
#   → HTML 报告：SQL 耗时区间 × SQL 类型（SELECT/INSERT/UPDATE/DELETE/REPLACE）交叉统计表
#
# {业务名}_{IP}_{端口}_{日期}_{时间}_sql_timecost_detail.csv
#   → 耗时细分 CSV：每个耗时区间（<0.5ms, 0.5-1ms, 1-2ms, ... >10s）各类型 SQL 数量
#
# {业务名}_{IP}_{端口}_{日期}_{时间}_sql_pattern_summary.csv
#   → SQL 去重聚合 CSV：归一化 SQL 模式、执行次数、平均/最大/最小耗时、库名、autocommit、涉及表
#
# {业务名}_{IP}_{端口}_{日期}_{时间}_sql_explain_schema.csv
#   → 执行计划+表结构 CSV：是否走索引、索引名称、扫描行数、EXPLAIN 详情、EXPLAIN问题标记、
#     索引详情、冗余索引、统计信息更新时间、统计信息是否过期、扫描效率、表数据量、CREATE TABLE

# ── 本地报告生成（读取从服务器拉回的 CSV，生成 SQL 性能风险分析 HTML）──

# 所有实例汇总到一个 HTML（默认）
python3 interf_report_generator.py -d output/2026-04-01 --mode merge

# 每个实例单独生成一个 HTML
python3 interf_report_generator.py -d output/2026-04-01 --mode split

# 指定输出目录
python3 interf_report_generator.py -d output/2026-04-01 --mode merge -o /tmp/reports/

# 报告内容:
#   - SQL 耗时区间 × 类型统计表（每个实例）
#   - 全表扫描 SQL（未走索引，风险最高）
#   - 高频 SQL Top 30（执行次数最多）
#   - 低效索引 SQL（走了索引但扫描行数 >1000）
```

## 文件说明

| 文件 | 说明 |
|------|------|
| `analyze_gateway_log.py` | Gateway 日志综合分析脚本（v3.4），解析日志并生成分析报告 |
| `interf_deep_analysis.py` | interf 日志深度分析脚本（v1.6），SQL 耗时细分 + 去重聚合 + 数据库联动（EXPLAIN/表结构/数据量/索引诊断/扫描效率），EXPLAIN 安全校验（UPDATE/DELETE 自动转写为 SELECT），在服务器上运行 |
| `interf_batch_analyze.sh` | interf 日志批量调度脚本（v1.1），从 ZK 节点通过 SSH 批量分析多台 Proxy 节点，按 IP 分组逐端口分析 |
| `interf_report_generator.py` | interf 性能风险报告生成器（v1.0），读取 CSV 生成 HTML 风险分析报告（全表扫描/高频SQL/低效索引），在本地运行 |
| `merge_gateway_reports.py` | 多节点数据整合分析报告脚本（v1.0），读取导出的 JSON/JSON.GZ 数据文件生成整合报告 |
| `gateway_collect.sh` | 集中采集脚本（v1.0），从多台 Gateway 服务器自动采集分析数据并生成整合报告 |
| `gateway_servers.conf.example` | 服务器配置文件模板（复制后填入实际服务器信息） |

## 工作流程

```
┌──────────────────────┐     ┌──────────────────┐     ┌──────────────────┐
│  手动方式              │     │  自动方式           │     │  整合分析          │
│                        │     │                    │     │                    │
│  analyze_gateway_log   │     │  gateway_collect   │     │  merge_gateway_   │
│  .py                   │     │  .sh               │     │  reports.py        │
│  → 单节点分析          │     │  → 多节点集中采集   │     │  → 多文件整合      │
└──────────────────────┘     └──────────────────┘     └──────────────────┘
         │                          │                          │
         ▼                          ▼                          ▼
   终端 / MD / HTML           collected_data/            终端 / MD / HTML
   / JSON / JSON.GZ          report_*.html              整合报告
```

### 详细工作流

```
                        手动方式                                  自动方式 (gateway_collect.sh)

生产服务器                              本地/办公机              汇总服务器
┌─────────────────────┐               ┌───────────────────┐    ┌────────────────────────────┐
│ analyze_gateway_log  │  手动 scp     │ merge_gateway_     │    │ gateway_collect.sh          │
│   -p 15001:15020    │ ────────────→ │   reports          │    │   读取 gateway_servers.conf │
│   -o {host}_{date}  │  .json.gz     │   *.json.gz        │    │   SSH → 远程分析 → 回收     │
│      .json.gz       │               │   -o report.html   │    │   → merge → report.html    │
└─────────────────────┘               └───────────────────┘    └────────────────────────────┘
```

## 进程锁保护

分析脚本内置**进程锁机制**，同一台服务器同时只允许运行一个分析进程，防止重复执行耗尽资源。

| 脚本 | 锁文件 | 锁机制 |
|------|--------|--------|
| `analyze_gateway_log.py` | `/tmp/tdsql_gateway_analyzer.lock` | `os.kill` PID 检测 + `atexit` + 信号处理自动清理 |

**特性**：
- 重复启动时自动检测并报错退出
- 进程正常退出或异常退出（`Ctrl+C`、`kill` 等）时自动释放锁
- 自动检测残留锁文件中的旧 PID 是否存活，避免永久锁死

## 支持的日志类型

| 日志类型 | 说明 |
|----------|------|
| `interf_instance` | SQL 接口层日志（主要分析对象） |
| `sql_instance` | SQL 执行层日志 |
| `slow_sql_instance` | 慢 SQL 日志 |
| `sys_instance` | 系统/错误日志 |
| `route_instance` | 路由日志 |
| `dbfw_instance` | 数据库防火墙日志 |

## 分析报告内容

报告包含 15 个分析章节：

| 章节 | 内容 |
|------|------|
| **一、日志概览** | 文件类型/数量/日期跨度/总大小 |
| **二、每日请求量趋势** | 每天请求量 + 平均耗时 |
| **三、每小时请求量分布** | 逐小时请求量柱状图 + 最繁忙分钟 Top 10 |
| **四、SQL 耗时分布** | <1ms / 1-10ms / 10-100ms / 100ms-1s / >1s 五档分布 |
| **五、每日平均耗时趋势** | 每日平均耗时变化 |
| **六、高耗时 SQL Top N** | 按耗时降序排列的 SQL 详情 |
| **七、高频 SQL 模式 Top N** | SQL 归一化后按频次排列 |
| **八、SQL 类型分布** | 查询/DML/连接/事务等类型占比 |
| **九、用户 & 数据库分布** | 按用户和数据库维度聚合 |
| **十、错误码分析** | 错误码统计 + 错误请求详情 |
| **十一、连接模式分析** | 短连接检测（连接建立/断开频率） |
| **十二、慢SQL日志分析** | 解析 slow_sql_instance，按耗时排序 |
| **十三、系统日志异常检测** | 每日错误数趋势、ZK错误、事件超时、SQL语法错误 |
| **十四、SQL 执行耗时火焰图** | 散点图展示 SQL 执行耗时与时间分布（仅 HTML） |
| **十五、核心结论与建议** | 自动生成发现项 + 严重程度 + 建议 |

## 前置条件

- Python >= 3.6（**无需安装任何第三方库**）

## 使用方式

```bash
# 查看帮助
python3 analyze_gateway_log.py --help

# 查看版本
python3 analyze_gateway_log.py -v
```

### 基本用法

```bash
# 通过端口号快速分析（推荐，自动展开为 /data/tdsql_run/{port}/gateway/log）
python3 analyze_gateway_log.py -p 15001
python3 analyze_gateway_log.py -p 15001:15020
python3 analyze_gateway_log.py -p 15001:15010,15012,15015~15020
python3 analyze_gateway_log.py -p 15001:15020 -o report.html

# 自定义路径模板（非默认安装路径时使用）
python3 analyze_gateway_log.py -p 15001:15020 --base-path /data1/tdengine/data/{port}/gateway/log

# 分析单个目录
python3 analyze_gateway_log.py -d /data/tdsql_run/15001/gateway/log

# 分析多个目录
python3 analyze_gateway_log.py -d /data/tdsql_run/15001/gateway/log /data/tdsql_run/15003/gateway/log
```

### 日期过滤

```bash
# 按日期过滤（只分析指定日期的日志）
python3 analyze_gateway_log.py -d /data/tdsql_run/15001/gateway/log --dates 2026-02-20

# 分析多个日期
python3 analyze_gateway_log.py -d /data/tdsql_run/15001/gateway/log --dates 2026-02-20 2026-02-21

# 按小时过滤（小时级精度）
python3 analyze_gateway_log.py -d /data/tdsql_run/15001/gateway/log --dates 2026-02-20T14 2026-02-20T15
```

### 报告输出

```bash
# 输出 Markdown 报告
python3 analyze_gateway_log.py -d /data/tdsql_run/15001/gateway/log -o report.md

# 输出 HTML 报告（内嵌 CSS，含火焰图和可折叠目录）
python3 analyze_gateway_log.py -d /data/tdsql_run/15001/gateway/log -o report.html

# 同时输出 HTML 报告 + gzip 压缩数据文件，文件名自动嵌入日期
python3 analyze_gateway_log.py -d /data/tdsql_run/15001/gateway/log --dates 2026-02-20 -o report_{date}.html data_{date}.json.gz
# → 生成 report_2026-02-20.html 和 data_2026-02-20.json.gz

# 使用 {host} 占位符，多服务器场景避免文件名冲突
python3 analyze_gateway_log.py -p 15001:15020 --dates 2026-03-01 -o data_{host}_{date}.json.gz
# → 生成 data_gw-node1_2026-03-01.json.gz
```

### 性能调优

```bash
# 限制采样行数（加速分析大文件）
python3 analyze_gateway_log.py -d /data/tdsql_run/15001/gateway/log --sample 100000

# 调整 Top N
python3 analyze_gateway_log.py -d /data/tdsql_run/15001/gateway/log -n 30
```

### 日志类型过滤

```bash
# 只分析 interf 日志（跳过 sql/slow_sql/sys 等，显著加速）
python3 analyze_gateway_log.py -p 15001:15020 --log-types interf

# 只分析 interf 和 sql 日志
python3 analyze_gateway_log.py -p 15001:15020 --log-types interf sql

# 只分析 interf 日志 + 指定日期 + 输出 HTML
python3 analyze_gateway_log.py -p 15001:15020 --log-types interf --dates 2026-04-01 -o report.html
```

### 指定具体文件分析


```bash
# 分析单个 interf 日志文件（绝对路径）
python3 analyze_gateway_log.py --files /data/tdsql_run/15001/gateway/log/interf_instance_15001.2026-04-01.0 -o report.html

# 分析多个 interf 日志文件（空格分隔，数量不限）
python3 analyze_gateway_log.py --files \
  /data/tdsql_run/15001/gateway/log/interf_instance_15001.2026-03-25.0 \
  /data/tdsql_run/15001/gateway/log/interf_instance_15001.2026-03-27.0 \
  -o report.html

# 如果已经 cd 到日志目录下，可以直接用文件名
python3 analyze_gateway_log.py --files \
  interf_instance_15001.2026-03-25.0 \
  interf_instance_15001.2026-03-27.0 \
  -o report.html

# 使用 shell 通配符批量匹配（shell 自动展开）
python3 analyze_gateway_log.py --files interf_instance_15001.2026-03-2*.0 -o report.html

# 分析当前目录下正在写入的 interf 文件（无日期后缀）
python3 analyze_gateway_log.py --files interf_instance_15001 -o report.html

# --files 和 --log-types 组合：指定多个文件，但只分析其中的 interf 类型
python3 analyze_gateway_log.py --files interf_instance_15001 sql_instance_15001 --log-types interf
```

### 跨节点远程分析（脚本在 ZK 节点，日志在 Proxy 节点）

当脚本部署在 ZK/调度节点上，而 interf 日志在 Proxy/Gateway 节点上时，有以下几种执行方式。

#### 配置段说明：`[gateway_servers]` vs `[gateway_proxies]`

`tdsql_env.conf` 中提供两个配置段，根据环境选择使用：

| 配置段 | 适用场景 | 执行方式 | 是否需要 SSH |
|--------|---------|---------|-------------|
| `[gateway_proxies]` | 有 SSH 权限 + interf 深度分析 | `interf_batch_analyze.sh`（SSH 直连，推荐） | **需要** |
| `[gateway_servers]` | 有 SSH 权限 + Gateway 综合分析 | `gateway_collect.sh`（SSH 直连） | **需要** |

**`[gateway_proxies]`** — interf 深度分析方式，按 Proxy 节点 + 端口配置：

```ini
[gateway_proxies]
# 格式: proxies_N=业务名称,Proxy节点IP,端口号,数据库用户名,数据库密码
# 同一 IP 多端口分多行写，脚本按 IP 分组，只在对应 IP 上分析属于它的端口
proxies_1=合约管理,10.3.3.1,15001,dbuser,dbpassword
proxies_2=合约管理,10.3.3.1,15002,dbuser,dbpassword
proxies_3=交易中心,10.3.3.2,15003,dbuser,dbpassword
```

**`[gateway_servers]`** — Gateway 综合分析方式，需要完整的连接信息：

```ini
[gateway_servers]
# 格式: server_N=别名,IP,SSH端口,用户名,认证方式,端口号表达式[,日志路径模板]
server_1=gw-node1,10.0.1.10,22,root,key:/root/.ssh/id_rsa,15001:15020
server_2=gw-node2,10.0.1.11,22,root,pass:MyPassword123,15001:15003
```

---

#### 方式一：interf_batch_analyze.sh 一键批量分析（推荐）

从 ZK 节点通过 SSH（root 用户）批量分析多台 Proxy 节点，自动完成推送、分析、回收全流程。

**前置条件**：
- ZK 节点上已安装 `sshpass`（用于 SSH 密码认证）
- ZK 节点可 SSH 到各 Proxy 节点（root 用户）
- `tdsql_env.conf` 中已配置 `[ssh]` 段密码和 `[gateway_proxies]` 段
- Proxy 节点上已有 Python >= 3.6

```bash
cd /path/to/tdsql-toolkit/gateway_log_analysis

# 一键分析今天所有 Proxy 的 interf 日志
bash interf_batch_analyze.sh --dates $(date +%Y-%m-%d)

# 指定时间段
bash interf_batch_analyze.sh --dates 2026-04-01 --time-range 14:00-16:00

# 加大超时（日志量大时）
bash interf_batch_analyze.sh --dates 2026-04-01 --timeout 1200

# 预览模式
bash interf_batch_analyze.sh --dates 2026-04-01 --dry-run

# 只分析指定实例（按序号 / IP:PORT / 业务名:PORT / 业务名 / IP）
bash interf_batch_analyze.sh --dates 2026-04-01 --instances 合约管理:15001
bash interf_batch_analyze.sh --dates 2026-04-01 --instances '#1' '#3'
```

> **核心特性**：按 Proxy IP 分组，`10.3.3.1` 上只执行 15001、15002 的分析，不会在 `10.3.3.1` 上执行 15003 的分析。

#### 方式二：SSH 远程执行（需要 SSH 权限，单节点手动操作）

先把脚本 scp 到 Proxy 节点，然后 SSH 执行：

```bash
# 1. 上传脚本到 Proxy 节点
scp analyze_gateway_log.py root@<PROXY_IP>:/tmp/

# 2. SSH 远程执行分析，输出 JSON 数据文件
ssh root@<PROXY_IP> "cd /data/tdsql_run/15001/gateway/log && python3 /tmp/analyze_gateway_log.py --files interf_instance_15001.2026-04-01.0 -o /tmp/report.json.gz"

# 3. 回收结果到本地
scp root@<PROXY_IP>:/tmp/report.json.gz ./

# 4. 本地生成 HTML 报告
python3 merge_gateway_reports.py report.json.gz -o report.html
```

#### 方式三：使用 gateway_collect.sh 自动采集（多节点推荐，需要 SSH 权限）

配置 `tdsql_env.conf` 中的 `[gateway_servers]` 段，然后一键采集：

```ini
# tdsql_env.conf
[gateway_servers]
server_1=proxy-node1,10.0.1.10,36000,root,pass:yourpassword,15001:15003
server_2=proxy-node2,10.0.1.11,36000,root,key:/root/.ssh/id_rsa,15001:15003
```

```bash
# 自动 SSH 到所有 Proxy 节点执行分析并回收结果，生成整合报告
bash gateway_collect.sh --dates 2026-04-01
```

#### 方式四：挂载远程日志目录（NFS/SSHFS，需要网络权限）

```bash
# 通过 sshfs 挂载 Proxy 节点的日志目录到本地
sshfs root@<PROXY_IP>:/data/tdsql_run/15001/gateway/log /mnt/proxy_log

# 直接分析挂载目录
python3 analyze_gateway_log.py -d /mnt/proxy_log --log-types interf -o report.html

# 或指定具体文件
python3 analyze_gateway_log.py --files /mnt/proxy_log/interf_instance_15001.2026-04-01.0 -o report.html
```

> **方式选择建议**：
> - interf 深度分析（SQL 性能诊断）→ **方式一**（`interf_batch_analyze.sh`，推荐）
> - 有 SSH 权限，单节点手动 → **方式二**（SSH 远程）
> - Gateway 综合分析（运行状况巡检）→ **方式三**（`gateway_collect.sh`）
> - 需要频繁分析 → **方式四**（挂载目录）

## 完整参数说明

### 分析脚本 (`analyze_gateway_log.py`)

| 短参数 | 长参数 | 默认值 | 说明 |
|--------|--------|--------|------|
| `-d` | `--dirs` | 无 | 日志目录路径，可指定多个（与 `-p` 二选一或组合使用） |
| `-p` | `--ports` | 无 | 端口号表达式，自动展开为日志目录路径。支持：单个 `15001`、逗号 `15001,15003`、范围 `15001:15010` 或 `15001~15010`、混合 `15001:15010,15012,15015~15020` |
| | `--base-path` | `/data/tdsql_run/{port}/gateway/log` | 端口号展开路径模板（配合 `-p` 使用），必须包含 `{port}` 占位符 |
| | `--dates` | 无（全部） | 按日期过滤，支持两种精度：`YYYY-MM-DD`（天级）或 `YYYY-MM-DDTHH`（小时级），可指定多个 |
| | `--log-types` | 无（全部） | 只分析指定类型的日志，可选值：`interf`、`sql`、`slow_sql`、`sys`，可指定多个 |
| | `--files` | 无 | 指定具体日志文件路径进行分析，可指定多个。使用此参数时可不指定 `-d` 或 `-p` |
| `-f` | `--format` | 自动识别 | 输出格式覆盖：`terminal` / `markdown` / `html` |
| `-o` | `--output` | 无（终端） | 输出文件，可指定多个，按扩展名自动识别格式。文件名支持占位符：`{date}` 替换为日期范围，`{host}` 替换为主机名 |
| `-n` | `--top-n` | `20` | Top N 显示数量 |
| | `--sample` | `0`（全量） | 每个文件的采样行数限制 |
| `-v` | `--version` | | 显示版本 |
| `-h` | `--help` | | 显示帮助 |

### 输出格式说明

| 格式 | 扩展名 | 特点 |
|------|--------|------|
| **终端** | `.txt` | 默认输出，ANSI 彩色表格 |
| **Markdown** | `.md` | 标准 Markdown 表格，适合文档归档 |
| **HTML** | `.html` | 内嵌 CSS + JS，含火焰图、可折叠目录，可直接浏览器打开 |
| **JSON** | `.json` | 数据导出，支持后续整合分析 |
| **JSON.GZ** | `.json.gz` | gzip 压缩数据导出，支持后续整合分析 |

### 资源保护（可安全在生产服务器运行）

脚本运行时自动应用以下资源保护机制，**不会影响生产业务**：

| 资源 | 保护措施 | 说明 |
|------|----------|------|
| 内存 | 最多占用 1GB | 通过 `RLIMIT_AS` 限制进程最大内存，超出自动终止 |
| CPU 优先级 | 降低 10 级 | 通过 `os.nice(10)` 调低优先级，让业务进程优先使用 CPU |
| CPU 占用 | 每 5 万行暂停 10ms | 避免长时间独占 CPU 导致其他服务卡顿 |
| 火焰图数据 | 超过 5 万点自动降采样 | 控制 HTML 文件大小和浏览器渲染性能 |
| 并发保护 | PID 锁文件单实例控制 | 同一服务器同时只允许运行一个分析进程 |
| 依赖 | 仅 Python >= 3.6 标准库 | 无需 `pip install`，拷贝到服务器直接运行 |

### 实际资源消耗参考

以下为在生产服务器上实际运行时的资源消耗数据，供评估参考：

**测试环境：**
- 服务器：15GB 内存，load average ~1.0
- 分析对象：3 个 Gateway 日志目录（15001/15002/15003），含 interf、sql、slow_sql、sys、route 共 5 类日志
- 日志总量约 9.5GB

**资源消耗：**

| 指标 | 实测值 | 说明 |
|------|--------|------|
| CPU 占用 | 单核 99%（nice=10） | 分析期间单核满跑，但 nice 值已降低，不影响业务进程调度 |
| 内存（RES） | ~389MB（2.6%） | 15GB 服务器上仅占 2.6%，远低于 1GB 上限 |
| interf 单文件耗时 | 12.5 ~ 14.4s / 500MB | 约 90 万行/文件，解析速度 ~6.4 万行/秒 |
| 总运行时间 | ~3 分 22 秒 | 3 个目录全量分析 + HTML 报告生成 |

> **结论**：脚本在生产服务器上运行时资源占用可控，对业务影响极小。如需进一步降低资源消耗，可使用 `--sample` 参数限制采样行数，或使用 `--dates` 参数缩小分析日期范围。

## 定时任务示例

```bash
# 编辑 crontab
crontab -e

# 每天凌晨 2 点分析前一天的 Gateway 日志，生成 HTML 报告
0 2 * * * python3 /path/to/analyze_gateway_log.py -p 15001:15020 --dates $(date -d yesterday +\%Y-\%m-\%d) -o /path/to/reports/report_{date}.html
```

---

## 整合分析工具 (`merge_gateway_reports.py`)

`merge_gateway_reports.py` 用于读取 `analyze_gateway_log.py` 导出的 JSON/JSON.GZ 数据文件，生成与原脚本格式一致的整合分析报告。

### 适用场景

- 从多台服务器收集数据文件后在本地统一生成报告
- 对比不同时段的分析数据
- 无需直接访问生产服务器即可生成报告

### 前置条件

- Python >= 3.6
- **无需安装任何第三方库**

### 基本用法

```bash
# 读取多个数据文件，终端输出报告
python3 merge_gateway_reports.py data_15001.json.gz data_15003.json.gz

# 生成 HTML 报告
python3 merge_gateway_reports.py data_15001.json.gz data_15003.json.gz -o report.html

# 生成 Markdown 报告
python3 merge_gateway_reports.py data_15001.json.gz data_15003.json.gz -o report.md

# 调整 Top N 数量
python3 merge_gateway_reports.py data_15001.json.gz -n 30 -o report.html
```

### 完整参数说明

| 短参数 | 长参数 | 默认值 | 说明 |
|--------|--------|--------|------|
| | `files` | （必填） | 数据文件路径（`.json` 或 `.json.gz`），可指定多个 |
| `-f` | `--format` | 自动识别 | 输出格式：`terminal` / `markdown` / `html` |
| `-o` | `--output` | 无（终端） | 输出文件路径，按扩展名自动识别格式 |
| `-n` | `--top-n` | `20` | Top N 显示数量 |
| `-v` | `--version` | | 显示版本 |

### 报告内容

整合报告包含与 `analyze_gateway_log.py` 一致的分析章节（除火焰图外）：

- 日志概览、每日请求量趋势、每小时请求量分布
- 最繁忙分钟 Top N、SQL 耗时分布、高耗时 SQL Top N
- 高频 SQL 模式、SQL 类型分布、用户 & 数据库分布
- 错误码分析、连接模式分析、SQL 执行层分析
- 慢 SQL 日志分析、系统日志异常检测、核心结论与建议

---

## 集中采集工具 (`gateway_collect.sh`)

`gateway_collect.sh` 用于从多台 Gateway 服务器集中采集日志分析数据，自动完成**分发 → 远程分析 → 回收 → 整合报告**的完整工作流。

### 工作流程

```
汇总服务器 (gateway_collect.sh)
    │
    ├── 1. 读取 gateway_servers.conf (服务器列表)
    │
    ├── 2. 并发 SSH 到各 Gateway 服务器
    │       ├── 上传 analyze_gateway_log.py
    │       ├── 执行分析: -p <ports> --dates <date> -o <alias>_<date>.json.gz
    │       └── SCP 回收 .json.gz 到本地
    │
    └── 3. 调用 merge_gateway_reports.py 生成整合报告
            └── collected_data/2026-03-01/report_2026-03-01.html
```

### 前置条件

| 环境 | 依赖 |
|------|------|
| 汇总服务器 | bash, ssh, scp, python3（运行 merge 脚本） |
| 汇总服务器（密码认证时） | sshpass |
| Gateway 服务器 | Python >= 3.6（标准库，无需 pip install） |

### 配置文件

复制模板文件并填入实际服务器信息：

```bash
cp gateway_servers.conf.example gateway_servers.conf
vim gateway_servers.conf
```

配置文件格式（每行一台服务器，逗号分隔）：

```
别名,IP,SSH端口,用户名,认证方式,端口号表达式[,日志路径模板]
```

| 字段 | 说明 |
|------|------|
| 别名 | 服务器标识（用于数据文件命名，如 `gw-node1`） |
| IP | 服务器 IP 地址 |
| SSH端口 | SSH 端口号（通常 22） |
| 用户名 | SSH 登录用户 |
| 认证方式 | `key:/path/to/id_rsa`（密钥认证，推荐）或 `pass:yourpassword`（密码认证，需安装 sshpass） |
| 端口号表达式 | 与 `-p` 格式一致，如 `15001:15020`。**注意**：多端口用分号(`;`)分隔（避免与逗号字段分隔符冲突），脚本会自动还原为逗号 |
| 日志路径模板 | （可选）覆盖默认路径，必须含 `{port}` |

配置示例：

```ini
# 密钥认证
gw-node1,10.0.1.10,22,root,key:/root/.ssh/id_rsa,15001:15020
gw-node2,10.0.1.11,22,root,key:/root/.ssh/id_rsa,15001:15010;15012

# 密码认证
gw-node3,10.0.1.12,22,root,pass:MyPassword123,15001:15003

# 自定义日志路径
gw-node4,10.0.1.13,22,root,key:/root/.ssh/id_rsa,15001:15010,/data1/tdengine/{port}/gateway/log
```

### 基本用法

```bash
# 赋予执行权限（首次使用）
chmod +x gateway_collect.sh

# 采集昨天的数据并生成报告（默认）
./gateway_collect.sh

# 指定日期采集
./gateway_collect.sh --dates 2026-03-01

# 多日期
./gateway_collect.sh --dates 2026-03-01 2026-03-02

# 自定义报告路径
./gateway_collect.sh --dates 2026-03-01 --report /tmp/report.html

# 只采集不合并（后续手动 merge）
./gateway_collect.sh --dates 2026-03-01 --collect-only

# 只合并已采集的数据
./gateway_collect.sh --merge-only --output-dir ./collected_data/2026-03-01

# 使用自定义配置文件
./gateway_collect.sh --config /etc/tdsql/gateways.conf --dates 2026-03-01

# 预览模式（不实际执行）
./gateway_collect.sh --dates 2026-03-01 --dry-run

# 采集后清理远程临时文件
./gateway_collect.sh --dates 2026-03-01 --cleanup
```

### 完整参数说明

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--config FILE` | `gateway_servers.conf` | 服务器配置文件路径 |
| `--dates DATE ...` | 昨天 | 分析日期，传递给 `analyze_gateway_log.py --dates` |
| `--output-dir DIR` | `./collected_data/{date}` | 本地数据汇总目录 |
| `--report FILE` | `{output-dir}/report_{date}.html` | 整合报告输出路径 |
| `--top-n N` | `20` | Top N 排行数量（传递给 merge 脚本） |
| `--sample LINES` | 无（全量） | 每文件采样行数（传递给分析脚本） |
| `--parallel N` | `5` | 最大并发采集数 |
| `--collect-only` | | 只采集数据，不生成整合报告 |
| `--merge-only` | | 只合并已有数据生成报告 |
| `--cleanup` | | 采集后清理远程临时文件 |
| `--dry-run` | | 预览模式，打印将执行的操作 |

### 输出目录结构

```
collected_data/
└── 2026-03-01/
    ├── gw-node1_2026-03-01.json.gz     # Gateway 节点 1 的分析数据
    ├── gw-node2_2026-03-01.json.gz     # Gateway 节点 2 的分析数据
    ├── gw-node3_2026-03-01.json.gz     # Gateway 节点 3 的分析数据
    └── report_2026-03-01.html          # 整合分析报告
```

### 定时采集（crontab）

```bash
# 编辑 crontab
crontab -e

# 每天凌晨 2 点采集前一天的日志数据
0 2 * * * /path/to/gateway_log_analysis/gateway_collect.sh --cleanup >> /var/log/tdsql_collect.log 2>&1
```

---

## 测试结果

基于生产服务器真实日志数据（3 个 Gateway 目录，日志总量 ~10GB），共 30 个测试用例，**全部通过**。

**测试环境：**
- 服务器：15GB 内存，CentOS
- 日志目录：15001（68 文件, 9.9G）、15002（85 文件, 248K）、15003（16 文件, 293M）
- 日期范围：2026-02-16 ~ 2026-02-27

**测试用例覆盖：**

| # | 类别 | 测试用例 | 结果 | 耗时 | 报告大小 |
|---|------|----------|------|------|----------|
| 1 | 基础格式 | 单目录全量 - terminal | ✅ | 202s | 60K |
| 2 | 基础格式 | 单目录全量 - markdown | ✅ | 201s | 56K |
| 3 | 基础格式 | 单目录全量 - HTML | ✅ | 201s | 5.9M |
| 4 | 日期过滤 | 单日过滤 (02-27) | ✅ | 29s | 28K |
| 5 | 日期过滤 | 双日过滤 (02-26~27) | ✅ | 48s | 32K |
| 6 | 日期过滤 | 三日过滤 HTML | ✅ | 66s | 7.5M |
| 7 | 跨日验证 | 跨日日志自动扫描 | ✅ | 29s | 28K |
| 8 | 跨日验证 | 跨日日志 HTML | ✅ | 29s | 7.5M |
| 9 | Top-N | Top-N = 5 | ✅ | 29s | 20K |
| 10 | Top-N | Top-N = 3 (markdown) | ✅ | 29s | 16K |
| 11 | Top-N | Top-N = 50 (HTML) | ✅ | 29s | 7.6M |
| 12 | 采样模式 | --sample 100 | ✅ | 0s | 16K |
| 13 | 采样模式 | --sample 500 (HTML) | ✅ | 1s | 860K |
| 14 | 多目录 | 双目录 (15001+15002) | ✅ | 201s | 72K |
| 15 | 多目录 | 双目录 HTML (15001+15003) | ✅ | 204s | 9.4M |
| 16 | 多目录 | 三目录 terminal | ✅ | 205s | 96K |
| 17 | 多目录 | 三目录 HTML | ✅ | 205s | 9.5M |
| 18 | 多目录+日期 | 三目录+单日 terminal | ✅ | 32s | 44K |
| 19 | 多目录+日期 | 三目录+单日 HTML | ✅ | 32s | 7.6M |
| 20 | 多目录+日期 | 三目录+单日 markdown | ✅ | 32s | 36K |
| 21 | 组合参数 | 日期+Top5+采样1000+HTML | ✅ | 10s | 340K |
| 22 | 组合参数 | 三目录+双日+Top10+markdown | ✅ | 52s | 44K |
| 23 | stdout | 不指定 -o 输出到终端 | ✅ | 29s | 28K |
| 24 | 版本信息 | -v 输出版本号 | ✅ | - | - |
| 25 | 容错 | 不存在的目录 | ✅ | - | - |
| 26 | 内容验证 | terminal 报告章节完整性 | ✅ | - | - |
| 27 | 内容验证 | HTML 结构完整性 | ✅ | - | - |
| 28 | 内容验证 | markdown 结构完整性 | ✅ | - | - |
| 29 | 内容验证 | 多目录报告包含所有目录 | ✅ | - | - |
| 30 | 内容验证 | 采样报告 < 全量报告 | ✅ | - | - |

> **30 / 30 测试通过，0 失败。**

---

## 版本更新记录

### analyze_gateway_log.py

| 版本 | 日期 | 说明 |
|------|------|------|
| v3.4 | 2026-04-01 | `--log-types` 日志类型过滤、`--files` 指定具体文件分析、高频 SQL 模式增加数据库名称 |
| v3.3 | 2026-04-01 | 高频 SQL 模式 Top N 增加数据库名称显示（从 `&db=` 提取） |
| v3.2 | 2026-03-02 | `{host}` 文件名占位符、JSON 元数据嵌入 hostname、整合报告来源标识优化 |
| v3.1 | 2026-03-02 | 端口号简写 `-p`/`--ports`、自定义路径模板 `--base-path`、目录/文件删除容错、heapq 内存优化 |
| v3.0 | 2026-03-02 | 小时级日期过滤、`{date}` 文件名占位符、JSON/JSON.GZ 数据导出 |
| v2.0 | 2026-03-02 | 日期过滤 `--dates`、SQL 执行耗时火焰图、资源限制、单实例保护、HTML 目录导航 |

### merge_gateway_reports.py

| 版本 | 日期 | 说明 |
|------|------|------|
| v1.0 | 2026-03-02 | 初始版本：多文件整合、三种输出格式、自动键名还原、完整 15 章节分析 |

### gateway_collect.sh

| 版本 | 日期 | 说明 |
|------|------|------|
| v1.0 | 2026-03-02 | 初始版本：配置文件驱动、SSH 分发/远程分析/数据回收/整合报告全流程、并发采集、灵活控制模式 |

### interf_deep_analysis.py

| 版本 | 日期 | 说明 |
|------|------|------|
| v1.6 | 2026-04-08 | CSV 新增 6 列诊断信息：EXPLAIN问题标记、索引详情、冗余索引、统计信息更新时间、统计信息是否过期、扫描效率 |
| v1.5 | 2026-04-08 | EXPLAIN 安全逻辑修复：UPDATE/DELETE 转写为等价 SELECT 后再 EXPLAIN，只允许 EXPLAIN SELECT，拒绝含分号 SQL |
| v1.4 | 2026-04-07 | SQL 完整性检测（`_is_sql_truncated`），被截断 SQL 自动跳过 EXPLAIN |
| v1.3 | 2026-04-07 | 重写 `extract_tables_from_sql`，修复子查询/UNION/别名误识别，归一化截取长度扩展 |
| v1.2 | 2026-04-02 | 修复 `--time-range` 预筛选误判、URL 编码引号语法错误，新增「原始SQL」列，增加 EXPLAIN 安全校验 |
| v1.1 | 2026-04-01 | `--time-range` 文件级预筛选 + 逐行提前终止，避免全量扫描大日志 |
| v1.0 | 2026-04-01 | 初始版本：SQL 耗时细分统计、SQL 去重聚合、数据库联动（EXPLAIN/表结构/数据量） |

### interf_batch_analyze.sh

| 版本 | 日期 | 说明 |
|------|------|------|
| v1.1 | 2026-04-27 | 新增 `--instances` 参数：支持只分析指定实例，兼容 `#N` 序号、`IP:PORT`、`业务名:PORT`、`业务名`、`IP` 五种 SPEC 写法，支持多个 SPEC 取并集；未命中时打印完整可用实例清单 |
| v1.0 | 2026-04-01 | 初始版本：从 ZK 节点通过 SSH(root) 批量分析多台 Proxy 节点 interf 日志，按 IP 分组逐端口分析，自动 SCP 推送/回收/清理 |