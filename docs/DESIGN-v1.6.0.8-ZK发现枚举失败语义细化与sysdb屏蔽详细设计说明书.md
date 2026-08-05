# DESIGN-v1.6.0.8 ZK 发现枚举失败语义细化与 sysdb 屏蔽详细设计说明书

> 版本：v1.6.0.8（设计稿，待评审；评审通过后方可实施）
> 作者：智能体 Q
> 输入：项目负责人内网生产（v1.6.0.7）实测反馈三项 + 截图 8 张
> 基线：`main @ 53f7728`（v1.6.0.7）

---

## 1. 背景与问题定义

| # | 反馈 | 性质 |
|---|---|---|
| Q1 | 扫描后大量实例业务库显示"枚举失败"；实测原因为这些实例**未创建 `checksql` 监控用户**，创建后即能枚举成功。负责人认为该特性有价值（可反向核查哪些实例未建监控用户），但要求页面提示从"枚举失败"改为"**未创建监控用户**" | 语义细化 |
| Q2 | 导入预览中 `sysdb` 不应作为业务库生成候选连接——它是每个实例的**默认管理库**，不纳入 SQL 审核 | 过滤规则 |
| Q3 | 核查 Q1 的发现是否就是代码设计的原理 | 原理确认 |

## 2. 原理核查结论（Q3）：**属实，与代码设计原理一致**

业务库枚举的唯一实现口径（扫描富集与导入预检两条路径同构）：

```
以配置的"业务账号"（内网即 checksql）对适配后每个 Proxy 执行
pymysql.connect(user=业务账号) + SHOW DATABASES
```

- 实例上**已创建** checksql 且口令一致 → 连接成功 → 枚举出业务库（截图" N 个库"）；
- 实例上**未创建** checksql → 认证失败，TDSQL/MySQL 返回 **1045 Access denied** →
  逐端点异常被捕获（v1.6.0.5 起"≥1 成功即可"）→ 双 Proxy 全 1045 → 0 成功 →
  来源标 `NO_AVAILABLE_PROXY` → `enrich_status=dbs_failed:NO_AVAILABLE_PROXY` → 页面显示"枚举失败"。

即：**"枚举"这个动作本身就要求监控账号在实例上存在**，未建账号的实例必然失败——这正是
负责人观察到的现象。负责人创建 checksql 后复扫成功，反向证实了链路。该行为是设计使然
（枚举必须用真实账号登录，无法也不应绕过），且如负责人所言，可反向用于核查监控账号覆盖率。

**当前代码的不足**：失败原因只进了后端日志（`error_type=OperationalError`），页面来源串
不携带 errno，1045（鉴权失败）与 2003（网络不可达）在页面上同为"枚举失败"，无法区分
"未建用户"与"网络问题"两类完全不同的处置动作。本版即补齐这一语义。

## 3. 变更一：失败原因分类与"未创建监控用户"提示（Q1）

### 3.1 后端：捕获 errno 并分类

两处 `_list_business_databases`（`zk_scan_enrich_service.py` / `zk_connection_import_service.py`）
在逐端点 `except` 中提取 errno：

```python
errno_ = exc.args[0] if getattr(exc, "args", None) and isinstance(exc.args[0], int) else None
```

0 成功时的来源/错误码分类规则（**可预测、不猜测**）：

| 失败 errno 分布 | 来源/错误码 | 语义 |
|---|---|---|
| **全部为 1045** | `NO_BUSINESS_USER` | 业务账号在该实例全部 Proxy 上鉴权失败——通常为**未创建监控用户**或口令与配置不一致 |
| 含连接类（2003/2013/超时等）或混合 | `NO_AVAILABLE_PROXY`（现状） | 网络/可达性问题，保持原语义 |
| 池化超时 | `BUSINESS_PROXY_TIMEOUT`（现状） | 不变 |

> 说明：MySQL 协议层 1045 无法区分"用户不存在"与"口令错误"（安全设计使然）。
> 故 `NO_BUSINESS_USER` 的完整语义是"鉴权失败：未创建监控用户或口令不匹配"；
> 页面**短标签**按负责人要求显示"未创建监控用户"，**tooltip/详情**给出完整语义与处置建议，
> 不牺牲准确性。

- 富集路径：`enrich_status` 记 `dbs_failed:NO_BUSINESS_USER`；逐端点 WARN 日志补 `errno=` 字段；
- 预检路径：0 成功时按上表抛 `ZKImportPreparationError("NO_BUSINESS_USER", …)`，
  文本带端点、不带口令（沿用 A-P2-02 脱敏口径）。

### 3.2 前端：扫描列表与导入预览的展示

- 扫描列表「业务库」列（现 `dbs_failed` 一律"枚举失败"）：
  - `dbs_failed:NO_BUSINESS_USER` → 显示 **未创建监控用户**（warning 色），tooltip：
    "业务账号在本实例鉴权失败（access denied）：通常未创建监控用户（如 checksql）或口令与
    ZK 发现配置不一致；创建用户或更正口令后重新扫描即可。"
  - 其余 `dbs_failed:*` → 仍显示"枚举失败"，tooltip 附原始来源串；
- 导入预览「状态」列：`failure_code` 增加可读标签映射：
  `NO_BUSINESS_USER→未创建监控用户`、`NO_AVAILABLE_PROXY→Proxy 不可达`，
  其余保持原 code + tooltip 显示 `failure_detail`。

### 3.3 不做什么

- **不自动创建用户**：发现链路保持只读（GUIDE-v1.6.0.0 安全口径），创建动作仍在 TDSQL 管控侧完成；
- 不改"≥1 成功即可"口径、不改降级标记（`proxy_show_partial`）逻辑。

## 4. 变更二：屏蔽 sysdb（Q2）

- `sysdb` 为 TDSQL 实例默认管理库，非业务库，不纳入 SQL 审核。将其加入系统库排除集：
  - **统一常量源**：`zk_connection_import_service.py` 现自带一份 `SYSTEM_DATABASES`，
    与 `zk_scan_enrich_service.py` 重复且易漂移；改为后者定义、前者 import（顺带消除双份维护）；
  - 排除集变为 `{information_schema, mysql, performance_schema, sys, sysdb}`；
- 生效面（两路径共用排除集，口径天然一致）：
  - 扫描富集：`business_dbs` 不含 sysdb（列表"N 个库"计数同步减少）；
  - 导入预检：不再生成 sysdb 候选连接行（截图 7 条→6 条）；
- **手工业务库（manual_databases）不过滤**：操作者显式填写即显式意图，保留；
- **不追溯**：历史已导入的 sysdb 连接不做自动清理（如需清理由运维侧处置）。

## 5. 错误码契约补充

| 码 | 语义 | 处置 |
|---|---|---|
| `NO_BUSINESS_USER`（新增） | 业务账号在实例全部 Proxy 鉴权失败（1045）：未创建监控用户或口令不匹配 | 在实例上创建监控用户（如 checksql）或更正 ZK 配置口令后重扫 |
| `NO_AVAILABLE_PROXY` | 全部 Proxy 连接类失败/混合失败 | 核对网络、地址映射、段替换 |

同步回写：`DESIGN-v1.6.0.1` §12 错误码表、`RUNBOOK-v1.6.0.3` 排错表。

## 6. 测试计划

1. 既有用例适配：`tests/test_zk_v1605.py` 的假 `SHOW DATABASES` 含 `sysdb`，
   排除后断言更新（sysdb 不再出现）；
2. 新增用例（`tests/test_zk_v1608.py`）：
   - 全端点 1045 → 富集 `dbs_failed:NO_BUSINESS_USER`、预检 `failure_code=NO_BUSINESS_USER`；
   - 混合 1045+2003 → 仍 `NO_AVAILABLE_PROXY`（分类不越界，反向鉴别）；
   - 假目录含 sysdb → 富集与预检均不含 sysdb；手工库填 sysdb → 保留（反向鉴别）；
   - 前端结构守卫：index.html 含"未创建监控用户"短标签与 tooltip 文案；
3. 全量回归 R-18 零跳过口径。

## 7. 版本与交付

- 版号 **v1.6.0.8**（生产现 v1.6.0.7）；VERSION / config / 前端标题同步；
- 升级无 DDL、无配置迁移，纯逻辑与展示变更，回滚直接退包。
