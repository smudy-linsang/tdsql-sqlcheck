# v1.6.3.7 在线元数据提取 SQL 文件命名规则还原——详细设计说明书

| 项目 | 内容 |
|---|---|
| **文档版本** | Rev.B，2026-09-12（根据智能体D的 SIT 测试报告 F1~F6 闭环整改修订） |
| **产品修复版本** | `v1.6.3.7` |
| **设计基线** | `v1.6.3.6`（Git 提交 `bb0cd34` / `1.6.3.6` 发布基线） |
| **提交对象** | Mr.Linsang；供决策施工派单与质量验收使用 |
| **设计性质** | **照图施工级定向修复设计（严格聚焦 SQL 产物命名规则还原、时间戳时基统一与生产历史脏数据校正）** |
| **执行约束** | **纯技术实现与工程规范设计，不预设具体智能体分工，由项目负责人统一调度实施** |
| **问题来源** | 内网生产环境（`10.243.16.238:8000`）升级至 v1.6.3.6 后，用户人工实测历史记录截图现场证据 |

---

## 一、 现场问题全景与现象确证

### 1.1 生产环境实测证据对比
在内网生产环境升级至 `v1.6.3.6` 后，对目标库 `ECIF-分布式-开发环境-15005-lzbj_ecif` 发起在线元数据拉取与审核，进入“SQL审核 / 在线元数据审核 / 历史元数据审核记录”查看，呈现如下现象：

| 报告序号 | 对应系统版本 | 提取生成的 SQL 文件 | 审计操作时间 | 命名格式特征 | 业务可读性与溯源性 |
|:---:|:---:|:---|:---:|:---|:---:|
| **#1377** | **v1.6.3.6** | `extracted_lzbj_ecif_a27123e1.sql` | `2026-09-12 02:51` | `库名_UUID前8位.sql` | ❌ **无时间语义，无法识别提取批次** |
| **#1369** | **v1.6.3.4** | `extracted_lzbj_ecif_20260910_224104.sql` | `2026-09-10 22:41` | `库名_YYYYMMDD_HHMMSS.sql` | ✅ **清晰直观，精确对应审计操作时间** |
| **#841** | 历史版本 | `extracted_lzbj_ecif_20260828_113413.sql` | `2026-08-28 11:34` | `库名_YYYYMMDD_HHMMSS.sql` | ✅ **与历史所有报告命名风格高度统一** |
| **#694 ~ #592** | 历史版本 | `extracted_lzbj_ecif_20260827_150551.sql` 等 | 2026-08 各时段 | `库名_YYYYMMDD_HHMMSS.sql` | ✅ **系统自建账以来既定的统一规范** |

### 1.2 用户核心诉求与底线要求
1. **严格恢复原始规范**：系统所有历史元数据审核记录产出报告的命名规范必须保持一致，不得随意篡改系统内部成熟的产物命名规则；
2. **拒绝无语义哈希**：禁止在最终用户可见的报告文件名中使用截断的任务 UUID、实例 ID 或其他内部技术标识，必须忠实呈现为“库名 + 提取操作时间戳”；
3. **时基一致性**：文件命名时间戳、SQL 文件头注释时间、数据库 `created_at` 入库时间与历史列表显示时间必须统一在**系统本地时间**，杜绝 UTC 与本地时间 8 小时错位；
4. **闭环修复历史数据**：对生产环境升级后已产生的异常记录（#1377 及所有同类记录）提供通用、幂等、可逆的 SQL 订正方案，保持历史元数据库的一致性。

---

## 二、 根因深度剖析与链路追踪

### 2.1 变异引入点（v1.6.3.5 引入异步 Worker 时的疏忽）
- **v1.6.3.4 及早期版本历史实现**：
  在同步提取接口 `backend/api/sql_audit.py:extract_and_audit` 中，命名规则为：
  ```python
  filename = f"extracted_{target_db}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.sql"
  ```
  该变量作为 `file_path` 传入审核引擎，并最终写入 `audit_history.source`。
- **v1.6.3.5 架构重构时的缺陷引入（Commit `480b873`）**：
  为了解决大库长耗时提取导致的 HTTP 504 超时问题，系统将提取审核过程解耦为独立子进程 `backend/workers/metadata_audit_worker.py`。开发人员在编写该 Worker 时，于第 149 行随手编写了：
  ```python
  filename = f"extracted_{db_name}_{job_id[:8]}.sql"
  ```
  **研发主观原因**：开发者意图用任务 ID（UUID 前缀）防止同名冲突，但**缺乏对系统既有业务契约与历史命名规约的充分核查**，擅自将具备业务时间语义的 `strftime('%Y%m%d_%H%M%S')` 篡改为了十六进制无意义字符 `job_id[:8]`（即截图中的 `a27123e1`）。同时在落库时机械使用了 `_utcnow()`，导致历史列表时间比本地操作时间早 8 小时。

### 2.2 数据流向与全域影响面
`filename` 与 `now_local` 变量在系统中的传递与消费路径如下：
```mermaid
flowchart TD
    W[metadata_audit_worker.py:149 生成 filename] --> A[传入 audit_streaming 作为 file_path]
    W --> B[写入 audit_history.source 与 created_at 本地时间]
    W --> C[写入 scan_snapshots.scan_label 快照字段]
    B --> D[前端界面渲染: 历史记录表格'提取生成的 SQL 文件'列 + '报告ID'列]
    B --> E[接口下载: GET /api/v1/audit/report/{id}/sql 返回 Content-Disposition 文件名]
    B --> F[HTML导出: GET /api/v1/audit/report/{id}/html 顶部'提取文件'元数据展示]
```
1. **数据库层**：`audit_history.source` 记录了文件名，`created_at` 统一本地时间；
2. **前端展示层**：`frontend/index.html` 表格直接渲染 `source`，并增加「报告ID」列便于同秒区分；
3. **文件下载层**：`backend/api/sql_audit.py` 使用 `source` 作为下载保存时的默认文件名；
4. **HTML 导出层**：`backend/api/sql_audit.py` 报告抬头元数据区显示“提取文件: <b>{source}</b>”；
5. **底层物理产物（安全边界）**：磁盘目录 `artifacts/{job_id}/` 下保存的文件固定为 `schema.sql`，并不依赖 `filename` 作为磁盘路径，因此**还原 `filename` 规则对物理文件存储和并发隔离完全零负面影响**。

---

## 三、 照图施工级技术设计方案

### 3.1 命名规范精确定义
元数据提取 SQL 文件的标准命名模板严格固化为：
$$\text{filename} = \text{"extracted\_"} + \text{db\_name} + \text{"\_"} + \text{YYYYMMDD\_HHMMSS} + \text{".sql"}$$

- **时间戳取值口径**：
  必须取**任务开始提取时的本地系统时间（Local System Time）**，格式化为 `%Y%m%d_%H%M%S`（例如 `20260912_025100`）。
- **与 SQL 文件头注释时序关系**：
  在 `backend/services/metadata_audit_pipeline.py:177` 中，SQL 文件头写入的提取日期为：
  `-- 提取日期: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`
  由于 `filename` 在任务开始时生成，而提取阶段建立连接与执行 DDL 读取存在毫秒至秒级时耗，实测两者时差一般在 0~1 秒内，标准规约为：**秒级一致（允许误差 $\le 2$ 秒）**，完全杜绝 8 小时跨时区错位。

---

### 3.2 源码修改蓝图（Precise Code Diff）

#### 改造点 1：[MODIFY] `backend/workers/metadata_audit_worker.py` (命名规则还原)
- **定位**：第 148-151 行
- **修改后代码**：
  ```python
  created_by = job["created_by"]
  now_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
  filename = f"extracted_{db_name}_{now_ts}.sql"
  ```

#### 改造点 2：[MODIFY] `backend/workers/metadata_audit_worker.py` (落库时间与快照本地时区对齐)
- **定位**：第 210-250 行
- **改动动机（F2）**：彻底解决 v1.6.3.5/v1.6.3.6 因 `_utcnow()` 导致历史列表显示早 8 小时的问题，统一为系统本地时间：
  ```python
  now_local = _local_now_str()
  audit_cols = (
      "extracted_schema", filename,
      summary["total_sql"], summary["passed"], summary["failed"],
      summary["error_count"], summary["warning_count"], summary["pass_rate"],
      results_json, created_by, "", None, "", now_local,
      ...
  )
  ```
  快照表中 `scan_started_at` 从 `job["started_at"]` 解析为本地时区：
  ```python
  st_dt = _jp.parse_utc(job.get("started_at"))
  local_started = st_dt.astimezone().strftime("%Y-%m-%d %H:%M:%S") if st_dt else now_local
  snapshot_id = _snap.safe_create_snapshot("schema_audit", {
      "biz_ref_id": str(report_id), "connection_id": connection_id,
      "connection_name": ctx.get("connection_name", ""), "db_name": db_name,
      "node": "", "scan_label": filename,
      "scan_started_at": local_started,
      "scan_finished_at": now_local, "created_by": created_by,
      ...
  ```

#### 改造点 3：[MODIFY] `backend/workers/metadata_audit_worker.py` (清理死代码)
- **改动说明（F5-1）**：移除未使用的 `_utcnow()` 函数，保持模块代码整洁度。

#### 改造点 4：[MODIFY] `backend/api/sql_audit.py` (稳定排序 Tiebreaker)
- **定位**：`get_extracted_reports` 查询语句
- **改动动机（F6）**：在按 `created_at DESC` 排序的基础上增加 `id DESC`，解决同秒并发或同秒重扫时的排序不确定性：
  ```python
  ORDER BY h.created_at DESC, h.id DESC
  ```

#### 改造点 5：[MODIFY] `frontend/index.html` (增加「报告ID」列)
- **定位**：第 450 行表格列定义
- **改动动机（F4/F6）**：在「提取生成的 SQL 文件」列之前增加「报告ID」列（如 `#1377`），为现场 DBA 审查提供绝对唯一的主键标识：
  ```html
  <el-table-column label="报告ID" width="90"><template #default="{row}"><span style="font-family:monospace;font-weight:600">#{{ row.id }}</span></template></el-table-column>
  ```

#### 改造点 6：[MODIFY] 版本号标识统一跃迁至 `v1.6.3.7`
- `VERSION`：`1.6.3.7`
- `backend/config.py`：`APP_VERSION = "1.6.3.7"`
- `frontend/index.html`：标题、左上角徽标及样式/脚本引用路径的 `?v=1.6.3.7`

---

## 四、 生产历史脏数据通用校正方案（Data Patch & Rollback）

根据智能体D在 SIT 报告中指出的 F3 问题，本方案从针对单条记录的硬编码提升为**自动备份、通用正则匹配、动态时区计算、幂等可回滚**的成熟企业级 SQL 方案。

### 4.1 通用修补脚本：`deploy/patch_v1637_source.sql`
```sql
-- 步骤 1：创建应急备份表（若已存在则不覆盖）
CREATE TABLE IF NOT EXISTS _bak_v1637_audit_history AS
SELECT id, db_name, source, created_at
FROM audit_history
WHERE audit_type = 'extracted_schema'
  AND (source REGEXP '^extracted_.+_[0-9a-f]{8}\\.sql$' OR id = 1377);

-- 步骤 2：预览待修复的存量异常记录（Dry-run 核验）
SELECT id, db_name, source, created_at
FROM audit_history
WHERE audit_type = 'extracted_schema'
  AND (source REGEXP '^extracted_.+_[0-9a-f]{8}\\.sql$' OR id = 1377);

-- 步骤 3：动态获取会话时区相对 UTC 的秒数偏移（例如东八区自动为 28800 秒）
SET @tz_offset := TIMESTAMPDIFF(SECOND, UTC_TIMESTAMP(), NOW());

-- 步骤 4：订正 created_at（补齐时区偏移，消除历史 8 小时错位）
UPDATE audit_history
SET created_at = DATE_ADD(created_at, INTERVAL @tz_offset SECOND)
WHERE audit_type = 'extracted_schema'
  AND (source REGEXP '^extracted_.+_[0-9a-f]{8}\\.sql$' OR id = 1377);

-- 步骤 5：依据订正后的本地时间 created_at 通用重算还原标准命名规范
UPDATE audit_history
SET source = CONCAT('extracted_', db_name, '_', DATE_FORMAT(created_at, '%Y%m%d_%H%M%S'), '.sql')
WHERE audit_type = 'extracted_schema'
  AND (source REGEXP '^extracted_.+_[0-9a-f]{8}\\.sql$' OR id = 1377);

-- 步骤 6：同步修正关联快照表的 scan_label 标签
UPDATE scan_snapshots s
JOIN audit_history h ON CAST(h.id AS CHAR) = s.biz_ref_id
SET s.scan_label = h.source
WHERE s.scan_label <> h.source;

-- 步骤 7：修补完成核验
SELECT id, db_name, source, created_at
FROM audit_history
WHERE id IN (SELECT id FROM _bak_v1637_audit_history);
```

### 4.2 应急回滚脚本：`deploy/rollback_v1637_source.sql`
```sql
-- 1. 从备份表回滚 audit_history
UPDATE audit_history h
JOIN _bak_v1637_audit_history b ON h.id = b.id
SET h.source = b.source,
    h.created_at = b.created_at;

-- 2. 同步回滚 scan_snapshots 的 scan_label
UPDATE scan_snapshots s
JOIN _bak_v1637_audit_history b ON s.biz_ref_id = CAST(b.id AS CHAR)
SET s.scan_label = b.source;

-- 3. 核验回滚结果
SELECT h.id, h.db_name, h.source, h.created_at
FROM audit_history h
JOIN _bak_v1637_audit_history b ON h.id = b.id;
```

---

## 五、 质量防护锁与自动化测试用例规约

在 `tests/test_v1637_filename_rule.py` 中建立完整 6 道防护锁，彻底杜绝变异逃逸（通过变异测试 MC 确证有效）：

1. **L1（格式与去哈希锁）**：断言 Worker 产出的 `filename` 与 `audit_history.source` 匹配正则 `^extracted_[a-zA-Z0-9_]+_\d{8}_\d{6}\.sql$`，且绝无 `job_id` 字符；
2. **L2（时间戳钟摆锁）**：断言文件名时间戳与本地系统时间一致（容差 $\le 2$ 秒）；
3. **L3（下载契约锁）**：模拟真实数据，断言 SQL 下载接口 `Content-Disposition` 完整透传文件名；
4. **L4（静态防倒退锁）**：静态扫描 `backend/` 全量代码，严禁 `job_id[:8]` 再次用于文件命名；
5. **L5（时区与落库时基防变异锁，针对 F1）**：断言真实 `publish` 写入的 `created_at` 与 `filename` 处于相同时基（容差 $\le 2$ 秒），在 UTC 变异下必定变红（已实测杀死 MC 变异）；
6. **L6（按天筛选端到端锁，针对 F5-2）**：验证本地时间落库记录在历史列表按当天日期筛选时精准命中。

---

## 六、 交付与升级风险评估

### 6.1 完整变更清单
本次升级共包含以下 8 项变更（无外延扩展）：
1. `backend/workers/metadata_audit_worker.py`：命名规则还原为本地时间戳、落库时基统一为本地、删除死代码 `_utcnow`；
2. `backend/api/sql_audit.py`：列表排序补充 `id DESC` 次级稳定排序键；
3. `frontend/index.html`：新增「报告ID」列展示、全局版本号跃迁与缓存穿透参数更新；
4. `VERSION`：跃迁为 `1.6.3.7`；
5. `backend/config.py`：`APP_VERSION` 跃迁为 `1.6.3.7`；
6. `deploy/patch_v1637_source.sql`：通用化历史数据修补脚本；
7. `deploy/rollback_v1637_source.sql`：配套历史数据修补回滚脚本；
8. `tests/test_v1637_filename_rule.py`：L1~L6 六道硬核回归防护锁。

### 6.2 风险矩阵
- **Python 依赖变动**：零（`requirements.txt` 完全不变）；
- **元数据库 Schema 变动**：零（不增删字段，仅修复历史脏数据）；
- **回滚难度**：极低（代码级 git checkout，数据级运行 rollback 脚本即可）。
