# DETAIL-v1.3-扫描结果对比 (照图施工级详细设计说明书)

> **版本**：v1.3.0  
> **面向对象**：前后端开发工程师 / 测试工程师  
> **设计原则**：严格照图施工，无歧义架构落地

---

## 1. 数据库持久化表设计 (Database Schema)

为了统一存储“在线元数据审核”、“慢SQL扫描任务”、“大表治理”的三大历史扫描结果快照，新建 `scan_snapshots` 数据库表。

### 1.1 `scan_snapshots` 结构
```sql
CREATE TABLE IF NOT EXISTS scan_snapshots (
    id VARCHAR(64) PRIMARY KEY,
    domain VARCHAR(32) NOT NULL,              -- 领域: 'schema_audit' | 'slow_query' | 'bigtable'
    connection_id VARCHAR(64) NOT NULL,        -- 实例连接ID
    db_name VARCHAR(128) DEFAULT '',           -- 数据库名
    task_id VARCHAR(64) DEFAULT '',            -- 对应的关联任务ID
    snapshot_name VARCHAR(255) NOT NULL,       -- 快照名称 (如: 2026-07-01 元数据全量扫描)
    total_issues INT DEFAULT 0,                -- 本次扫描发现的问题总数
    summary_json TEXT,                         -- 摘要数据 JSON (包含各级别的统计)
    detail_json MEDIUMTEXT,                    -- 完整扫描明细 JSON (结构化保存)
    created_by VARCHAR(64) DEFAULT 'system',   -- 创建人
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_snapshot_lookup ON scan_snapshots(domain, connection_id, created_at);
```

---

## 2. 核心对比匹配算法公式 (Diff Algorithm)

假定选择的基线快照为 $S_{base}$ (旧节点, 如1号)，目标快照为 $S_{target}$ (新节点, 如15号)。

1. **集合构建**：
   从 $S_{base}$ 中构建条目映射 Map $M_{base} = \{ \text{key}_i \to \text{item}_i \}$；
   从 $S_{target}$ 中构建条目映射 Map $M_{target} = \{ \text{key}_j \to \text{item}_j \}$。

2. **分类判定公式与扩展状态**：
   - **已修复集合 (Fixed Set)**：
     $$S_{fixed} = \{ x \mid x \in M_{base} \land x \notin M_{target} \}$$
   - **新增问题集合 (New Set)**：
     $$S_{new} = \{ y \mid y \in M_{target} \land y \notin M_{base} \}$$
   - **残留/未修复集合 (Remaining Set)**：
     $$S_{remain} = \{ z \mid z \in M_{base} \land z \in M_{target} \}$$
     *对于 $S_{remain}$ 中的元素，需进一步执行指标变动判定：*
     - **恶化 (Degraded)**：目标快照中的指标值（如耗时、数据量）显著大于基线快照。
     - **改善 (Improved)**：目标快照中的指标值显著小于基线快照。
     - **无变化 (Unchanged)**：指标值波动在误差范围内。

3. **核心 KPI 指标计算**：
   - 基线问题总数：$N_{base} = |M_{base}|$
   - 目标问题总数：$N_{target} = |M_{target}|$
   - 修复率：$\text{FixRate} = \frac{|S_{fixed}|}{N_{base}} \times 100\%$
   - 净增减量：$\Delta N = N_{target} - N_{base} = |S_{new}| - |S_{fixed}|$

---

## 3. 三大场景指纹匹配规则矩阵与比对入口 (Entry Points)

| 治理场景 | 唯一 key 生成算法 | 匹配的扩展差异属性 | 推荐的前端比对入口位置 |
| :--- | :--- | :--- | :--- |
| **在线元数据审核** | `item.node + ":" + item.table_name + ":" + item.rule_id` | 严密级别 (ERROR / WARNING) 是否变更 | `schema-extractor-audit` 页面，“开始在线审核”按钮旁 |
| **慢SQL治理扫描** | `item.sql_fingerprint` | 执行次数增减、平均耗时(ms)变化量 (用于判定 Degraded/Improved) | `slow-tasks` 页面右上角操作栏 |
| **大表治理** | `item.db_name + ":" + item.table_name + ":" + item.issue_type` | 表行数增减、数据占用(MB)增减 (用于判定 Degraded/Improved) | `bigtable` 页面右上角操作栏 |

---

## 4. 前端组件与响应式状态设计 (Vue 3 / Element Plus)

### 4.1 新增响应式状态变量 (`app.js`)
```javascript
// 比对选择对话框状态
const compareDialog = reactive({
  visible: false,
  domain: '',                     // 'schema_audit' | 'slow_query' | 'bigtable'
  connection_id: '',              // 当前筛选的实例ID
  snapshots: [],                  // 当前实例下的历史快照列表
  selectedIds: [],                // 选中的快照 ID 数组 (严格控制 len == 2)
  loading: false
});

// 比对结果展示 Drawer / Modal 状态
const compareResultDrawer = reactive({
  visible: false,
  loading: false,
  result: null,                   // 后端返回的完整比对结果 JSON
  activeTab: 'summary',           // 'summary' | 'fixed' | 'new' | 'remaining'
  searchQuery: ''
});
```

### 4.2 强校验交互逻辑 (`app.js`)
```javascript
const handleSnapshotSelection = (selection) => {
  if (selection.length > 2) {
    ElementPlus.ElMessage.warning("比对分析每次仅支持选择 2 个历史扫描结果！");
    // 自动取消超过 2 项的选择
    return;
  }
  compareDialog.selectedIds = selection.map(row => row.id);
};

const runSnapshotCompare = async () => {
  if (compareDialog.selectedIds.length !== 2) {
    ElementPlus.ElMessage.warning("请恰好选择 2 个历史扫描记录进行对比分析！");
    return;
  }
  compareResultDrawer.loading = true;
  compareResultDrawer.visible = true;
  try {
    const resp = await apiFetch(`${API_BASE}/api/v1/compare/snapshots`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        base_snapshot_id: compareDialog.selectedIds[0],
        target_snapshot_id: compareDialog.selectedIds[1]
      })
    });
    if (resp.ok) {
      compareResultDrawer.result = await resp.json();
    }
  } finally {
    compareResultDrawer.loading = false;
  }
};
```

---

## 5. D1/D2/D4 缺陷修复与极端异常处置预案 (§11)

1. **D1: 快照明细为空的处理**：若某一快照的 `detail_json` 为空，后端比对逻辑自动退化为全量差集，不抛出异常。
2. **D2: 数据实例连接已删除**：当快照对应的 `connection_id` 已在系统中被删除时，保留历史快照在比对列表中的呈现，但标记 `(已归档实例)`，支持照常离线比对。
3. **D4: 跨版本规则 ID 兼容**：若规则库升级导致规则 ID 变更，比对算法基于 `rule_id` + `description` 联合匹配，防止误判为“新增问题”。
