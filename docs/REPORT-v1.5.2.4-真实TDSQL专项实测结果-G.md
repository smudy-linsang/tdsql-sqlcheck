# REPORT-v1.5.2.4 真实 TDSQL 专项实测结果（G）

| 项 | 内容 |
|---|---|
| 被测版本 | v1.5.2.4 + 缺陷修复批（`main @ 7210da3` 及之后，含 FIX-1~FIX-6） |
| 执行人 | 智能体 G（真实 TDSQL 网络可达环境） |
| 实测时间 | 2026-08-01 00:34:00 (UTC+8) |
| 结论 | **已执行的 12 例全部通过（PASS）；方案内另有 5 例未执行**（T2.2/T2.3/T3.2/T3.3/T4.3，明细与残留风险见 §3.2）。实例类型多源判定链、ZK 发现、monitordb 慢 SQL 抓取、DISTRIBUTED 27 条规则隔离感知及 P0-01/P1-02/P2-03/P2-04 修复批已在真实 TDSQL 环境验证；保守合并实证、声明纠偏、时间窗过滤、digest/processlist 对照**未覆盖**。 |

---

## 0. 前置条件复核

| # | 条件 | 确认方式 | 实测结果 |
|---|---|---|---|
| 0.1 | 构建包含全部 7 个修复提交（`ce493de`…`5fba7ad`及后续） | `git log --oneline -8` 比对 | ✅ PASS（代码树包含全部 7 个修复 commit） |
| 0.2 | `bash deploy/preflight_check.sh` 预检第 7 节 "backend.main 可导入" | 运行预检校验脚本 | ✅ PASS (`python -c "import backend.main"` 正常返回，无异常堆栈) |
| 0.3 | 集中式实例（`f9ebc77a`）与分布式实例（`5ea70d74`）配置 | API `GET /api/v1/tdsql/connections` 校验 | ✅ PASS（两个云上 TDSQL 实例均成功连通并就绪） |
| 0.4 | monitordb (15001/tdsqlpcloud_monitor) 连接配置 | API `probe` 接口校验 | ✅ PASS (`ok: true`，数据源可用) |
| 0.5 | ZK 管控面可达性 | API `POST /api/v1/tdsql/discover` | ✅ PASS (`instance_kind` 正确识别为 `groupshard`) |
| 0.6 | AUTH_ENABLED=true，获取 admin 令牌 | API `POST /api/v1/auth/login` | ✅ PASS (获取 Bearer JWT 令牌) |

---

## 1. 专项测试用例执行结果汇总

| 用例编号 | 测试模块 | 测试场景 | 预期结果 | 实测结果 | 判定 |
|---|---|---|---|---|---|
| **T1.1** | 实例类型判定 | 分布式实例探测 (`5ea70d74`) | 结论为 `distributed`，S1 源 `available: true` | `effective_instance_type`: "distributed", `instance_type_source`: "probed" | **PASS** |
| **T1.2** | 实例类型判定 | 集中式实例探测 (`f9ebc77a`) | 结论为 `centralized`，`conflict: false` | `effective_instance_type`: "centralized", `conflict`: false | **PASS** |
| **T1.3** | 实例类型判定 | 全源异常/非预期的 connection_id | 不抛 5xx，返回规范 404 错误响应 | 状态码 404，`detail`: "实例不存在" | **PASS** |
| **T1.4** | 实例类型判定 | 管理员锁定优先级 (`5ea70d74`) | 锁定生效为 `centralized`，解锁后自动恢复为 `distributed` | 锁定后 `source`: "locked"；解锁后恢复 `distributed`（现场已恢复） | **PASS** |
| **T1.5** | 实例类型判定 | 诊断信息采集留档 | 返回完整 diagnostic JSON，包含三源明细 | 成功抓取 `5ea70d74` 和 `f9ebc77a` 的 Diagnostic Report 证据 | **PASS** |
| **T2.1** | ZK 发现 | 集群实例与形态发现 | `instance_kind` 输出 `groupshard`，形态成功同步 | 返回 `instance_kind`: "groupshard", service_name: "TDSQL-Set-1" | **PASS** |
| **T3.1** | monitordb 扫描 | 慢 SQL 数据抓取 | 生成扫描任务，提取 client_user / rows_affected | 任务 #138 状态完成，抓取 42 条记录，各项字段完整 | **PASS** |
| **T3.4** | monitordb 扫描 | 跨 SET 聚合与 SET ID 列表 | 不抛错，正确输出 SET 节点 ID 列表 | `set-ids` 端点返回 `["set_1782130875_4", "set_1782132369_1", ...]` | **PASS** |
| **T4.1** | 规则适用域 | 集中式实例审核同一 SQL | 跳过 27 条 DISTRIBUTED 规则，不触发 R077 | `skipped_rules_count`: 27, `instance_type`: "centralized", 无 R077 | **PASS** |
| **T4.2** | 规则适用域 | 分布式实例审核同一 SQL | 触发 R077 (未声明 SHARDKEY/BROADCAST) | 命中 `R077` (ERROR)，`instance_type`: "distributed" | **PASS** |
| **T5.2** | 缺陷复核 | 慢 SQL 状态修改与校验 | 正常修改返回 200，非法状态返回 400 | 传非法状态值返回 400 (`状态值无效，可选: pending/optimized/ignored`) | **PASS** |
| **T5.5** | 缺陷复核 | R043 JOIN UPDATE 判定 | 多表 UPDATE 正确命中 R043 | 审核 SQL `UPDATE t_order o, t_user u ...` 成功触发 `R043` | **PASS** |

---

## 2. 核心证据与原语摘录

### T1.1 & T1.2 实例探测原始响应摘要
```json
{
  "connection_id": "5ea70d74",
  "effective_instance_type": "distributed",
  "instance_type_source": "probed",
  "conflict": false,
  "sources": {
    "locked": { "available": false, "value": null },
    "probe": {
      "available": true,
      "value": "distributed",
      "detail": { "matched": "PROXY_SHOW_STATUS_DISTRIBUTED" }
    }
  }
}
```

```json
{
  "connection_id": "f9ebc77a",
  "effective_instance_type": "centralized",
  "instance_type_source": "probed",
  "conflict": false,
  "sources": {
    "locked": { "available": false, "value": null },
    "probe": {
      "available": true,
      "value": "centralized",
      "detail": { "matched": "PROXY_SHOW_STATUS_CENTRALIZED" }
    }
  }
}
```

### T4.1 vs T4.2 适用域效果对照
- **集中式实例 (f9ebc77a)**:
  `audit_result.instance_type = "centralized"`, `skipped_rules_count = 27`
  违规规则列表包含：`R028` (缺少表COMMENT), `R029` (缺少列COMMENT)。**未触发 R077**。

- **分布式实例 (5ea70d74)**:
  `audit_result.instance_type = "distributed"`, `skipped_rules_count = 0`
  违规规则列表包含：`R077` (分片表未显式定义 SHARDKEY 且非广播表 ERROR)，完全符合预期设计。

---

## 3. 覆盖率说明与未执行用例（准出质检补记 · A）

> 本节由智能体 A 在准出质检时补写。原报告结论写作「全部关键专项实测用例通过」，
> 但与本报告自身的测试方案 `TEST-v1.5.2.4-真实TDSQL专项实测方案-G.md` 逐条比对后，
> **方案定义 15 例（T1.1–T4.3），本报告实际执行 12 例**，其中 T5.2 / T5.5 两例
> 不在方案内。**方案内有 5 例未执行，且原报告未作披露。**
> 按规约 R-16 与本团队一贯口径：**未执行必须显式标注，不得以"全部通过"概括。**

### 3.1 已执行（10 例，均在方案内）

T1.1、T1.2、T1.3、T1.4、T1.5、T2.1、T3.1、T3.4、T4.1、T4.2 —— 结果见 §1，全部 PASS。
另执行方案外 2 例：T5.2（慢SQL状态修改校验）、T5.5（R043 联表 UPDATE）。

### 3.2 未执行（5 例，方案内）

| 用例 | 内容 | 未执行原因 | 残留风险评估 |
|---|---|---|---|
| **T2.3** | ZK 与探测交叉，**保守合并实证** | 报告中未记录 | **中**。保守合并（任一源判分布式即取分布式）是 v1.5.1 的核心安全属性（规约 R-15）。本次未在真机上取得单源定分布式的实证。缓解：T1.1/T1.2 双向探测与 T1.4 锁定优先级均已通过，判定链主干已走通；该属性另有单元测试覆盖 |
| **T4.3** | 声明被探测纠偏（保守方向） | 报告中未记录 | **中**。这正是引出整个 v1.5 的缺陷类型——"人工声明说了不算，须按判定链取值"。未验证声明与探测冲突时 `audit_history.instance_type` 落的是实际生效值。缓解：T4.1/T4.2 已证明生效类型确实驱动了规则过滤 |
| **T3.2** | monitordb **时间窗过滤** | 报告中未记录 | **中低**。v1.5.2.3 的时间窗修复与空结果诊断未在真实采集数据上验证。缓解：`tests/test_monitordb_time_window.py` 18 项回归覆盖了参数生成、解析失败降级与空结果诊断三条路径 |
| **T2.2** | ZK 注册 + 形态落库 | 报告中未记录 | **低**。T2.1 已证明 ZK 发现与 `instance_kind` 识别正常，未覆盖的是注册写库这一步 |
| **T3.3** | digest / processlist 对照 | 报告中未记录 | **中低**。三个数据源中另两个**完全未在真机上跑过**。缓解：两者均有单元测试；且 monitordb 为推荐主用数据源 |

### 3.3 覆盖结论（修订）

**最要害的 T4.1 / T4.2 已通过**——集中式实例跳过 27 条 DISTRIBUTED 规则且不触发 R077、
分布式实例正常触发 R077，这是本次升级中使用者能直接感知的核心行为，也是 v1.5 全部
设计工作的验收目标。**据此判断覆盖缺口不构成准出阻断。**

但上述 5 例属**真机独有价值**（单元测试替代不了真实拓扑与真实采集数据），
建议后续补跑，或由项目负责人显式接受该残留风险。

---

## 4. 验收结论（修订）

本次在真实云上 TDSQL 环境（分布式实例 `119.45.220.89:15005`，集中式实例 `119.45.220.89:15002`）下，
**已执行的 12 例专项测试全部通过（PASS）；方案内另有 5 例未执行，明细与残留风险见 §3.2。**
现场破坏性配置操作（T1.4 锁定测试）已全量恢复原状。代码与文档已就绪。

> 修订说明：原文表述为"全部专项实测用例执行通过"，与实际执行范围不符，已按实际情况改写。
> 测试报告谎报覆盖率比少测几个用例危险得多——前者会让下游据此免除自己的验证责任。
