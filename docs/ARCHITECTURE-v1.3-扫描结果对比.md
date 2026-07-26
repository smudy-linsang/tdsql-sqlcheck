# ARCHITECTURE-v1.3-扫描结果对比 (需求分析 + 概要设计说明书)

> **版本**：v1.3.0  
> **状态**：架构设计完成 (待代码施工)  
> **核心领域**：在线元数据审核 (`schema-extractor-audit`) / 慢SQL治理扫描 (`slow-tasks`) / 大表治理 (`bigtable`)

---

## 1. 业务需求背景与目标 (Business Requirements)

### 1.1 业务背景
在日常 TDSQL 数据库治理中，DBA 和开发团队会在不同的时间节点（如 1日、15日、30日）对特定数据库实例发起定期扫描与体检。此前系统仅支持单次扫描结果查看，无法回答以下管理层与运维核心问题：
- 经过半个月的整改，**上次发现的缺陷是否已经修复？**
- **本次扫描是否引入了新的违规 SQL 或新增了无主键/无分区大表？**
- **整体质量趋势是改善了还是恶化了？净修复率是多少？**

### 1.2 领导原始需求抽象
领导对 v1.3 版本提出的核心能力要求：
1. **三大核心场景覆盖**：
   - 在线元数据审核 (`schema-extractor-audit`)
   - 慢SQL治理扫描任务 (`slow-tasks`)
   - 实例与体检-大表治理 (`bigtable`)
2. **多节点任意双向比对**：
   - 能够对同一个实例的历史任意两次扫描（如 1日 vs 15日、1日 vs 30日、15日 vs 30日）产出对比报告。
3. **对比报告核心指标矩阵**：
   - **之前问题总数** vs **现在问题总数**；
   - **已修复问题 (Fixed)**：前有后无；
   - **新增问题 (New)**：前无后有；
   - **遗留未修复问题 (Remaining)**：前后均有（指标变化）；
   - **净变化量与修复率**。
4. **数据库实例维度过滤与筛选**：
   - 快速根据“数据库实例信息 (connection_id / db_name)”筛选出属于该实例的历史扫描快照。
5. **强校验规则**：
   - 比对操作**必须且只能选择 2 个扫描结果**。选少（<2）或选多（>2）时触发前端与后端的强约束提示。

---

## 2. 总体架构设计与方案对比 (Architecture Design)

### 2.1 技术方案比对与选型

| 评估维度 | 方案 A：直接解析离线 HTML 文本比对 | 方案 B (推荐)：结构化 JSON 离线快照 + 智能二元算法比对 |
| :--- | :--- | :--- |
| **数据读取速度** | 极慢 (需正则/DOM树解析，体积兆级) | **极快 (<5ms 提取，JSON 极简内存映射)** |
| **比对精准度** | 差 (依赖 HTML 节点，容易因文本重构失效) | **极高 (基于 SQL指纹 / 规则ID / 表名精确索引)** |
| **可扩展性** | 低 (很难导出二次分析 JSON API) | **极高 (支持 API 返回 + 交互大屏 + 导出 HTML)** |
| **磁盘开销** | 较大 (完整 HTML 几兆至十几兆) | **极小 (JSON 格式化压缩，几十 KB)** |

**架构决策**：采纳 **方案 B (结构化 JSON 离线快照 + 智能二元算法比对)**。每次扫描完成时系统自动持久化一整份标准格式的 JSON 离线快照。

### 2.2 总体系统架构图 (Architecture Diagram)

```mermaid
graph TD
    User[DBA / 开发人员] --> UI[前端控制台]
    
    subgraph 1. 实例筛选与记录点选
        UI --> SelectInst[1. 选择目标数据库实例 Filter by Connection/DB]
        SelectInst --> ListSnapshots[2. 获取该实例的历史扫描快照列表]
        ListSnapshots --> PickTwo[3. 强校验勾选 恰好 2 次扫描历史 (Count == 2)]
    end

    subgraph 2. 后端二元智能比对引擎 (Snapshot Diff Engine)
        PickTwo --> API[POST /api/v1/compare/snapshots]
        API --> LoadS1[读取快照 1 (T1 Baseline JSON)]
        API --> LoadS2[读取快照 2 (T2 Target JSON)]
        
        LoadS1 & LoadS2 --> DiffEngine[二元 Diff 匹配算法]
        
        DiffEngine --> CalcFixed[求差集 S1 - S2: 已修复 Fixed]
        DiffEngine --> CalcNew[求差集 S2 - S1: 新增问题 New]
        DiffEngine --> CalcRemain[求交集 S1 ∩ S2: 仍留遗留 Remaining]
    end

    subgraph 3. 可视化报告与导出
        CalcFixed & CalcNew & CalcRemain --> JSONResp[比对分析结果 JSON]
        JSONResp --> UIView[页面对比大屏展示]
        JSONResp --> HTMLExport[导出离线 HTML 比对报告]
```

---

## 3. 核心比对领域与唯一指纹定义 (Unique Identifiers)

为了实现准确的“已修复、新增、遗留”算法判定，必须为三大领域的每一条扫描问题定义**全局唯一业务指纹 (Business Unique Key)**：

1. **在线元数据审核 (`schema-extractor-audit`)**：
   - 唯一键定义：`UniqueKey = {db_name}:{table_name}:{rule_id}`
2. **慢SQL治理扫描任务 (`slow-tasks`)**：
   - 唯一键定义：`UniqueKey = {sql_fingerprint}` (或 `checksum`)
3. **大表治理 (`bigtable`)**：
   - 唯一键定义：`UniqueKey = {db_name}:{table_name}:{issue_type}` (例如: `db1:t_order:NO_PRIMARY_KEY`)

---

## 4. 强约束与交互控制规范

1. **选择逻辑**：
   - 前端复选框数组 `selectedSnapshotIds` 必须维持 `length === 2`；
   - 用户尝试勾选第 3 项时：系统自动拦截，并弹窗警告 `“比对分析每次仅支持选择 2 个历史扫描结果进行对比！”`；
   - 点击“开始对比”按钮时：若 `selectedSnapshotIds.length !== 2`，按钮保持禁用 (`disabled`)，并提示 `“请选择 2 个历史结果进行对比”`。

## 5. 对现有系统(v1.2.0.9)的零侵入与兼容性设计 (Backward Compatibility & Zero Impact)

为了确保本次架构升级**绝对不影响当前 v1.2.0.9 版本所有已投产功能的正常运行**，特制定以下保底设计：

1. **快照生成的异步与异常隔离 (Zero-Impact Hook)**：
   - 现有核心链路（发起元数据审核、发起慢SQL扫描、发起大表采集）的业务逻辑保持不变。
   - **快照保存动作作为“后置旁路逻辑”触发**。在后端代码中，快照存储必须包裹在独立的 `try...except` 块或提交到后台线程中。即使快照保存失败（如磁盘满、JSON 序列化错误），也**绝对不能阻断**原有的扫描结果返回和展示逻辑。
2. **前端入口的低耦合嵌入**：
   - 不修改现有的视图路由和菜单结构。
   - 比对入口按钮（“历史对比分析”）将以不突兀的方式，仅追加在对应功能页面的操作栏右侧（如“开始扫描”按钮旁）。前端比对的弹窗、抽屉以及响应式变量采取独立命名域（如 `compareDialog`, `compareResultDrawer`），避免污染现有的 `app.js` 全局状态。
3. **历史数据兼容 (Old Data Compatibility)**：
   - v1.3 升级前的历史扫描由于没有生成 JSON 快照，将不在比对列表中出现。前端列表为空时需给予友好提示：“暂无快照数据，请执行新的扫描以生成比对基线”。
4. **存储控制与防膨胀 (Storage Retention)**：
   - 考虑到 `detail_json` 可能较大，系统需引入快照保留策略（如：每个实例每个领域默认仅保留最近 10 次或最近 30 天的快照），并在系统管理模块的“数据保留策略”中加入对 `scan_snapshots` 表的清理机制，防止 SQLite 文件无限膨胀影响当前系统性能。
