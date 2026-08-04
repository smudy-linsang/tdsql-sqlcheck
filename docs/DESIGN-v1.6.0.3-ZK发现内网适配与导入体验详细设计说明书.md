# DESIGN-v1.6.0.3 ZK 发现内网适配与导入体验详细设计说明书

> 版本：v1.6.0.3（设计稿）
> 作者：智能体 Q（依据内网部署实测反馈）
> 输入：内网部署实测截图与问题清单（4 项）、`v1.6.0.1_质检验收报告_A.md`、O 的 v1.6.0.1 设计说明书
> 状态：待评审；评审通过后方可实施

---

## 1. 背景与问题定义

内网真实集群部署 v1.6.0.1 后实测发现四个问题：

| # | 问题 | 截图证据 | 性质 |
|---|---|---|---|
| Q1 | ZK 扫描列表只有实例 ID / 主 Proxy / 形态 / SET，**没有"实例名称"和"业务库"**；导入预览里两列全空、连接名无法生成，导致无法导入 | 截图 1/2/3 | 功能缺口 |
| Q2 | 扫描结果数百条、列显示不全、无分页无筛选，用户体验差 | 截图 1 | 体验缺陷 |
| Q3 | ZK 返回的 Proxy 地址为 `10.243.21.x` 段，checksql 受网络策略限制只能访问 `10.243.20.x`；同一台 Proxy 主机两段 IP 末位相同。现有"内外网地址映射"只支持**整主机精确映射**，无法覆盖数百台主机 | 截图 1/2 | 内网适配缺口 |
| Q4 | O 的"实例 ID → 实例名称/业务库"解析在内网**全部落空**：预览报 `BUSINESS_PROXY_INCOMPLETE`、实例名称为空 | 截图 2/3 | 解析策略与真实环境不匹配 |

### 1.1 根因排查结论（代码级）

**实例名称落空**：`zk_connection_import_service._resolve_instance_name` 当前查询为

```sql
SELECT f_key, f_val FROM m_data_cur
WHERE f_type = 1 AND f_mid = '/tdsqlzk/<instance_id>'
  AND f_key IN ('instance_name','clientName')
```

三重硬约束（精确 f_mid 路径格式 + `f_type=1` + 固定两个 key）只要一个与内网实际不符即全空。
而本系统其他模块读 `m_data_cur` 的既有实践（`cluster_inspect_service`、`daily_inspect_service`）
一律使用 `f_mid LIKE '%<mid>%' OR f_pmid LIKE '%<mid>%'` 的**模糊匹配**，说明真实 monitordb 的
`f_mid` 格式并不保证等于 ZK 路径。O 的精确等值查询是未经内网实证的假设。

**业务库落空**：`_list_business_databases` 用 ZK 原始 Proxy 地址（`10.243.21.x`）直连做
`SHOW DATABASES`，内网不可达 → 任一 Proxy 失败即整实例 `BUSINESS_PROXY_INCOMPLETE`
（v1.6.0.1 的从严口径本身正确，错在地址未做内网适配）。

**扫描阶段无名称/库**：v1.6.0.1 把名称/库解析放在"导入预览"阶段，扫描列表不解析；
且解析所需的 MonitorDB/业务凭据只在导入弹窗临时输入，扫描时不可用。

---

## 2. 目标与非目标

**目标**：

1. 扫描列表直接展示**实例名称、业务库**（可解析时），并据此生成连接名、完成导入（Q1）；
2. 扫描列表与预览列表支持**分页 + 五维筛选**（实例名称/业务库/Proxy 地址/Proxy 端口/实例形态）（Q2）；
3. 支持 **IP 段替换规则**：对发现地址的四个八位段中任一段做"原值→新值"替换（如第 3 段 21→20），批量生效于全部发现地址（Q3）；
4. 实例名称/业务库解析改为**多源自适应 + 可诊断 + 可手工兜底**，在内网真实环境可落地（Q4）。

**非目标**：不改变 v1.6.0.1 的安全模型（Mock 闸门、凭据加密、唯一约束、失败留痕）；
不实现 ZK 节点写操作；不替代内网真实集群 UAT。

---

## 3. 总体流程（v1.6.0.3）

```text
管理员保存 ZK 配置（含可选：MonitorDB 配置、业务账号、IP 段替换规则）
        │
        ▼
POST /discover（扫描）
  ① kazoo 读 ZK 拓扑（现状不变）
  ② 应用地址适配：IP 段替换规则 → 精确主机映射（顺序固定）
  ③ 富集（配置了 MonitorDB/业务账号时）：
     - 实例名称：解析链（§4）逐实例解析，带 name_source 溯源
     - 业务库：  以业务账号对适配后 Proxy 做 SHOW DATABASES（§5）
     - 并发池化 + 单项超时 + 失败不阻断扫描，状态入 enrich_status
        │
        ▼
扫描列表（分页 + 筛选）：实例ID | 实例名称 | 业务库 | Proxy(适配后) | 端口 | 形态 | SET | 状态
        │ 勾选若干实例
        ▼
导入弹窗（预填配置中的 MonitorDB/业务账号，可改；可临时调整段替换规则）
        │ 生成预览（复用扫描期富集结果；未富集的在此补解析）
        ▼
预览列表（分页 + 筛选）：每业务库一行，连接名 = 实例名称-端口-库名
        │ 确认提交（现状事务/审计/唯一约束不变）
```

---

## 4. 实例名称解析：多源自适应解析链（核心）

### 4.1 设计原则

- **不假设 monitordb 的 mid/key 格式**：用"候选模式串"逐级试探，命中即停，全程记录 `name_source`；
- **与本系统既有实践对齐**：模糊匹配优先于 O 的精确等值（其他模块已证实 LIKE 才打得中）；
- **可诊断**：新增诊断接口返回"这个实例在 monitordb 里到底长什么样"，内网一次实测即可固化最优模式；
- **可兜底**：解析失败允许手工命名或显式选用实例 ID，但 UI 明确标注来源。

### 4.2 解析链（按优先级，命中即停）

候选 ID 序列：`[group_id（分布式）, 代表 set_id, 全部 set_id（集中式即自身）]`，
对每个候选 ID 依次执行：

| 级 | 查询模式 | 说明 |
|---|---|---|
| L1 | `f_mid = '/tdsqlzk/<id>'` 且 `f_key IN ('instance_name','clientName')` 且 `f_type=1` | 保留 O 的原查询（赤兔标准环境可命中） |
| L2 | `(f_mid LIKE '%<id>%' OR f_pmid LIKE '%<id>%')` 且 `f_key IN ('instance_name','clientName','name','set_name','instance_alias')`，不限 f_type | 与本系统巡检模块同款模糊口径，key 集扩宽 |
| L3 | 同 L2 的 mid 条件，`f_key` 不限，取**值看起来像名称**的行：`f_val` 非空、长度≤64、不含 `%/{}` 且非纯数字 | 兜底捞名称类 KV |
| L4 | 元数据表探针：`SHOW TABLES LIKE '%instance%'` / `'%meta%'`；对命中的表查 `information_schema.columns` 找名称类列（`name/alias/client_name/instance_name`），以 `<id>` 对 id 类列做 LIKE 查询 | 覆盖名称不在 m_data_cur 的环境 |
| L5 | ZK 侧：setrun JSON 及 group 节点 data 中的名称类字段（`name/set_name/comment/alias`，键集可配置扩展） | 零额外依赖 |
| L6 | 手工兜底：导入弹窗逐行覆盖 / 命名模板 / 显式"使用实例 ID"（UI 标注"手工命名"或"ID 代名"） | 最终保底，name_source=manual/id |

每级命中后写入 `resolved_name` + `name_source`（`monitor_exact/monitor_like/monitor_value/meta_table/zk_node/manual/id`），
扫描响应、预览行、导入批次明细表均携带 `name_source`，事后可审计"名字哪来的"。

### 4.3 名称解析诊断接口（新增）

```text
POST /api/v1/tdsql/discover/name-diagnose
Body: { "instance_ids": ["group_xxx", "set_yyy"],   # ≤10 个
        "monitor": {host,port,username,password,database}  # 可选，缺省用 ZK 配置中已保存的
      }
Resp: { "items": [ {
          "instance_id", 
          "matched_mids":   ["m_data_cur 中 LIKE 命中的 f_mid 样本(≤10)"],
          "available_keys": ["命中 mid 下的 DISTINCT f_key 样本(≤50)"],
          "name_hits":      [{"level":"L2","f_mid","f_key","f_val"}],
          "meta_tables":    [{"table","name_columns","sample_rows(≤3,脱敏)"}],
          "zk_name_fields": {"setrun_keys":[...], "hit": {...}}
        } ] }
```

用途：内网首测时由管理员对 2~3 个实例跑一次，即可确认 L1~L5 哪级命中、
并把命中模式固化为配置项 `name_query_hint`（可选，跳过未命中级别、降低扫描开销）。
响应不含口令；`sample_rows` 仅取名称类列。

### 4.4 性能与并发

- 名称解析共用**单条** MonitorDB 连接（串行 SQL，单次查询 ≤200ms 级）；
- 扫描富集整体用线程池（默认 8 线程）做"业务库枚举"（网络 IO 为主），名称解析在池外串行；
- 单实例富集超时 3s（可配），整体扫描富集上限默认 500 实例（超出部分 `enrich_status=skipped_limit`，导入预览时再补）；
- 富集结果缓存于 discovery session（10 分钟 TTL，现状一致），预览不重复查询已富集项。

---

## 5. 业务库获取：段替换 + SHOW DATABASES + 手工兜底

### 5.1 IP 段替换规则（新增配置，Q3）

配置模型（ZK 配置页"地址段替换"编辑器，随配置加密表持久化，非敏感明文存储）：

```json
{ "octet_rules": [ {"segment": 3, "from": "21", "to": "20"} ] }
```

- `segment` ∈ {1,2,3,4}（从左至右）；`from/to` ∈ 0~255 的十进制串；多条规则**按序**应用；
- 应用顺序（固定，可预测）：**先段替换，后精确主机映射**（`endpoint_map` 保留，用于个别例外主机）；
- 作用于发现结果的 `host` 与 `proxy_list` 全量端点（与 v1.6.0.1 映射行为同口径）；
- 扫描列表展示**适配后**地址，悬浮显示原始地址（可追溯）；
- 导入弹窗提供"本次临时调整"入口（预填配置值，仅本会话生效）。

用户场景验证：规则 `{segment:3, from:21, to:20}` 使 `10.243.21.13:15001 → 10.243.20.13:15001`，
与"同主机双段 IP 末位相同"的网络事实匹配；亦支持 `{segment:2, from:243, to:244}` 等任意段。

### 5.2 业务库枚举

- 以业务账号对**适配后**的每个 Proxy 执行 `SHOW DATABASES`（沿用 v1.6.0.1 精确排除系统库与 MonitorDB 库、跨 Proxy 一致性校验、任一失败即 `BUSINESS_PROXY_INCOMPLETE` 的从严口径）；
- 新增兜底（仅当适配后仍不可达时，UI 明确标注"手工库列表"）：
  导入弹窗允许对该实例**手工填写业务库列表**（逗号分隔），`databases_source=manual`；
  手工库同样一库一连接生成候选，连接名规则不变；
- `databases_source` ∈ {`proxy_show`, `manual`}，随预览行与批次明细留痕。

---

## 6. 扫描列表与预览：分页 + 筛选（Q2）

前端（数据已在浏览器，客户端实现，零新增后端压力）：

| 控件 | 行为 |
|---|---|
| 分页 | `el-pagination`，默认 50/页，可选 20/50/100；扫描列表与预览列表各自独立分页 |
| 筛选-实例名称 | 文本模糊（含未解析项的"未解析"标记可筛） |
| 筛选-业务库 | 文本模糊（匹配任一业务库即命中） |
| 筛选-Proxy 地址 | 文本模糊（适配后地址） |
| 筛选-Proxy 端口 | 精确数字 |
| 筛选-实例形态 | 下拉：全部/分布式/集中式 |
| 对话框宽度 | 扫描弹窗 800px → 1280px（90vw 上限），预览弹窗 1080px → 1400px，列全显示 |

扫描列表新增列：**实例名称（含来源角标）**、**业务库（多库折叠+悬浮全览）**、**富集状态**；
保留：实例 ID、主 Proxy（适配后）、形态、SET 列表、Proxy 数、状态。

---

## 7. 配置与数据库变更

### 7.1 `zk_discovery_config` 增列（迁移 `backend/schema/v10/100_zk_scan_enrich.sql`）

| 列 | 类型 | 说明 |
|---|---|---|
| `octet_rules_json` | TEXT | 段替换规则数组（明文，非敏感） |
| `monitor_host` / `monitor_port` / `monitor_user` / `monitor_db` | VARCHAR/INT | 扫描富集用 MonitorDB（可选） |
| `monitor_password_encrypted` | TEXT | Fernet 密文 |
| `business_username` | VARCHAR(128) | 扫描富集用业务账号（可选） |
| `business_password_encrypted` | TEXT | Fernet 密文 |
| `name_query_hint` | VARCHAR(64) | 可选：固化的解析级别（L1~L5），由诊断接口结论填入 |
| `enrich_enabled` | TINYINT | 扫描富集总开关，默认 1（凭据齐备时生效） |

`public_config` 只回显用户名与 `*_configured` 标志，口令一律不回显（沿用 v1.6.0.1 口径）；
导入弹窗提交的一次性口令仍只存会话、提交即焚（不变）。

### 7.2 扫描响应增量字段（`DiscoveredInstance`）

`resolved_name`、`name_source`、`business_dbs: list[str]`、`databases_source`、
`enrich_status`（`ok | name_only | dbs_failed:<code> | skipped_limit | disabled`）、
`original_host`（适配前地址）。

---

## 8. 接口变更汇总

| 接口 | 变更 |
|---|---|
| `GET/PUT /discover/config` | 增 §7.1 字段的读写（脱敏口径不变） |
| `POST /discover` | 扫描后执行 §3 ②③ 富集；响应增 §7.2 字段 |
| `POST /discover/name-diagnose` | **新增**（§4.3，admin/dba） |
| `POST /discover/import-preview` | 请求增可选 `octet_rules`（本次临时）、`manual_databases: {instance_id: [db,...]}`、逐实例 `name_override`；未富集项在此补解析 |
| `POST /discover/import-commit` | 不变；批次明细增 `name_source`、`databases_source` 留痕（items 表加两列，同迁移脚本） |

---

## 9. 错误码增补（设计 §12 表追加）

| 错误码 | 用户提示 | 处理 |
|---|---|---|
| `OCTET_RULE_INVALID` | 地址段替换规则无效（段号 1-4、值 0-255） | 修正规则 |
| `NAME_UNRESOLVED` | 实例名称五级解析均未命中 | 跑 name-diagnose 固化模式，或手工命名 |
| `ENRICH_PARTIAL` | 部分实例富集失败（不阻断） | 列表按 enrich_status 筛选后逐个处置 |

---

## 10. 测试设计

### 10.1 自动化（新增用例，入 `tests/test_zk_discovery.py` / `test_zk_import_commit.py`）

| 编号 | 用例 | 断言 |
|---|---|---|
| ZE-01 | 段替换：规则 {3,21,20} 作用于 host 与 proxy_list | `10.243.21.13→10.243.20.13`，端口不变；精确映射仍可覆盖个别主机 |
| ZE-02 | 名称解析链 L2：mock m_data_cur 中 f_mid 为 `xxx/set_1` 形态（非精确路径） | L1 落空、L2 命中，name_source=monitor_like |
| ZE-03 | 名称解析链 L3/L4：仅值像名称的 KV / 仅元数据表有名称 | 分别命中且来源正确 |
| ZE-04 | 全链落空 → enrich_status 标记 + 手工命名可导入，name_source=manual | 连接名按手工名生成 |
| ZE-05 | 富集并发与超时：mock 3s 延迟 Proxy | 单实例超时不阻断整次扫描 |
| ZE-06 | 扫描列表分页/筛选（前端守卫用例：模板含分页组件与五筛选绑定） | 结构断言 |
| ZE-07 | 配置脱敏：GET config 不含 monitor/business 口令明文或密文 | 断言 |
| ZE-08 | 回归：v1.6.0.1 既有 11 用例 + 并发/唯一约束用例全绿 | 0 失败 |

### 10.2 内网 UAT 清单（放行前置，人工执行）

1. 对 2~3 个实例跑 `name-diagnose`，确认命中级别并固化 `name_query_hint`；
2. 配置段替换规则后扫描：列表显示适配后 `10.243.20.x` 地址、实例名称、业务库；
3. 分页/筛选在 ≥200 条结果下可用；
4. 选一个 group + 一个 set 完成"扫描→预览→导入→测试连接→慢SQL扫描"闭环；
5. 去掉 `-q` 记录 `共发现 N 个实例 (noshard X + groupshard Y)`（A 报告未覆盖项闭环）。

---

## 11. 安全与审计

- 新增两类存储口令（monitor/business）一律 Fernet 加密、读取接口零回显，与 ZK 认证口令同口径；
- 扫描富集对生产 Proxy 仅执行 `SHOW DATABASES`（只读），对 MonitorDB 仅 SELECT；
- `name-diagnose` 限 admin/dba，响应脱敏（无口令、样本行仅名称列），调用写操作审计；
- 段替换规则非敏感，但变更写 `updated_by` 审计；
- 手工命名/手工库列表在批次明细留痕（`name_source/databases_source`），合规可追溯。

---

## 12. 实施顺序与回滚

1. 迁移 v10 + 配置服务扩展（向后兼容：凭据缺省时扫描行为等同 v1.6.0.1）；
2. 解析链 + 段替换 + 富集（后端）+ name-diagnose；
3. 前端分页/筛选/新列/规则编辑器；
4. 自动化用例 + 内网 UAT。

回滚：revert 代码即回 v1.6.0.1 行为；新增列保留（无破坏性）。

---

## 13. 对四个问题的直接回答

| 问题 | v1.6.0.3 方案 |
|---|---|
| Q1 扫描无名称/库 | 扫描期富集：名称走 §4 解析链、库走 §5；列表直接展示并据此生成连接名 |
| Q2 列表体验 | 客户端分页（50/页）+ 五维筛选 + 弹窗加宽列全显 |
| Q3 网段不可达 | 段替换规则（任一段原值→新值，批量生效）+ 保留精确映射；导入弹窗可临时调整 |
| Q4 解析落空 | 根因=精确等值假设不成立；改为五级自适应解析链 + 诊断接口固化模式 + 手工兜底 |
