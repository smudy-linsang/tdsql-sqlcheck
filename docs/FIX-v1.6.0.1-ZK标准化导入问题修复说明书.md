# FIX-v1.6.0.1 ZK 标准化导入问题修复说明书

> 依据：`REPORT-v1.6.0.1-ZK标准化导入完整测试报告.md` 所列 P1~P8 共 8 项问题
> 修复版本：v1.6.0.1 修复批（commits `15fdcf8` / `f1b9e90` / `dd6ca73`）
> 复测结论见：`RETEST-v1.6.0.1-ZK标准化导入修复复测报告.md`

## 1. 修复总览

| 编号 | 级别 | 问题 | 修复方式 | 涉及文件 |
|---|---|---|---|---|
| P1 | 高 | G 报告"准予投产"结论失实 | 结论降级加注 + 准出标准回写（不改代码） | `docs/REPORT-...-G.md`、设计说明书 §13 |
| P2 | 高 | 提交路径零测试覆盖 | 新建 `tests/test_zk_import_commit.py`（9 用例，ZI-10/11/12 全覆盖） | tests/ |
| P3 | 中 | 错误码 `BUSINESS_PROXY_INCOMPLETE` 契约漂移 | 设计错误码表回写为从严口径 + 代码注释固化语义 | 设计说明书 §6.3/§12、import_service |
| P4 | 中 | 失败导入零审计留痕、candidate_count 语义偏差 | 独立短事务登记 `status='failed'` 批次；candidate_count 改取预览全量 | import_service、zk_discovery API |
| P5 | 中 | Mock 结果 UI 无醒目标识 | 发现弹窗红色警示条 + 每行"演示"标签 + 状态变量暴露 | index.html、app.js |
| P6 | 低 | FORCE_MOCK 占位地址回显配置页 | 配置读取接口在 mock 生效时返回空 servers | zk_discovery API |
| P7 | 低 | 导入成功后无批次明细展示 | 成功弹窗改为批次明细（批次号+逐条连接名/库名） | app.js |
| P8 | 提示 | 进程内存会话多副本限制 | 部署说明新增"多副本禁止直接启用"约束章节 | `V1.6.0.1全量更新部署说明.md` 三.§4 |

## 2. 逐项修复细节

### FIX-P2 提交路径测试收编（最高优先级）

新建 `tests/test_zk_import_commit.py`，9 个用例直打真实 API + 真实元库：

| 用例 | 对应设计准出项 |
|---|---|
| `test_commit_happy_path_encrypts_and_audits` | ZI-06/08/09：一库一连接、命名契约、口令仅加密落库、审计表零敏感数据 |
| `test_commit_candidate_count_is_preview_total` | P4：candidate_count = 预览候选总数（2 预览/1 选中 → candidate_count=2） |
| `test_preview_is_single_use` | ZI-12：提交后重放 → 410 |
| `test_commit_conflict_by_endpoint_rolls_back_and_records_failed_batch` | ZI-10/11 + P4：409、零写入、**失败批次留痕断言** |
| `test_commit_conflict_by_name_rolls_back` | ZI-10：同名冲突整体回滚 |
| `test_commit_rejects_non_ready_rows` | 非 ready 行拒绝 + ROW_NOT_READY 留痕 |
| `test_expired_preview_is_rejected` | ZI-12：过期 410 |
| `test_foreign_owner_preview_is_rejected` | ZI-12：他人属主 403 |
| `test_import_batches_query_returns_audit_without_secrets` | ZI-12：批次查询脱敏 |

保留"预置既有连接→提交→断言零写入"的反向鉴别结构（规约 R-12）。

### FIX-P3 错误码契约回写（从严口径胜出）

- 设计 §6.3/§12：`BUSINESS_CONNECT_FAILED` 条款替换为 `BUSINESS_PROXY_INCOMPLETE`——
  "任一在册 Proxy 无法以业务账号完成 SHOW DATABASES 枚举，整实例预检失败"，
  并注明从严理由（部分 Proxy 目录不可见时静默继续会产出"看起来完整"的错误连接，符合 R-15）。
- 代码 `zk_connection_import_service._list_business_databases` 抛错处补注释固化该语义，防止后续误改回宽松口径。

### FIX-P4 失败批次审计留痕 + candidate_count 语义

1. `ZKConnectionImportService.record_failed_batch()`（新增）：冲突/事务异常回滚后，
   在**独立短事务**写入 `status='failed'` 批次；`failure_summary` 仅含
   `code=xxx;selected=N;instances=...`（无口令/密文）；登记失败自身只记日志不上抛，
   不掩盖原始提交错误。
2. `commit()` 新增 `preview_total` 参数；`candidate_count = max(预览全量, 选中数)`，
   与 `created_count` 形成"预览全量 vs 实际创建"对账。
3. `zk_discovery.commit_import_preview` 在 except 分支调用失败登记，preview 总行数透传。

### FIX-P5 Mock 醒目标识

- `index.html` 发现弹窗：`zkDiscoveryIsMock` 为真时渲染红色 `el-alert`
  "当前为演示（Mock）数据，仅用于开发联调，禁止导入任何实例"；状态列追加红色"演示"标签。
- `app.js`：`zkDiscoveryIsMock` 补进 setup 返回对象（此前**模板引用了未暴露的变量**，
  属本次顺带发现并修复的隐患）。
- `tests/test_zk_discovery.py` 前端守卫新增三条断言（警示条、演示标签、状态暴露），防回退。

### FIX-P6 占位地址防回写

`GET /discover/config` 环境变量回退路径：`FORCE_MOCK` 生效时返回空 `servers`
与 `source='unconfigured'`，管理员不会在配置弹窗看到 `mock.invalid:2181`，
杜绝把占位值保存进数据库配置源。

### FIX-P7 导入结果批次明细

`commitZkImport` 成功路径由单行 ElMessage 改为 `ElMessageBox.alert` 批次明细：
批次号 + 逐条"连接名（库：xxx）"列表 + 追溯提示（zk_import_batch_id）。
数据来自提交响应既有的 `connections` 数组，零新增接口。

### FIX-P1 / FIX-P8 文档处置

- G 报告文件头加注独立复核结论："原'准予投产'结论不成立，降级为单元级验证通过；
  不具备投产放行资格"，原文保留供追溯。
- 设计说明书 §13：ZI-10/11/12 标注自动化用例映射；准出条件追加修复批复测注记
  （内网真实集群 UAT 闭环仍是投产前置）。
- 部署说明第三章新增 §4：多副本部署禁止直接启用（会话粘滞或共享会话存储二选一）。

## 3. 回滚方式

三个提交相互独立，均可单独 revert：
- `15fdcf8` 代码与测试（revert 后 P2 用例消失，属预期）；
- `f1b9e90` 文档回写；
- `dd6ca73` 前端守卫同步。
无数据库结构变更、无迁移脚本，回滚零数据风险。
