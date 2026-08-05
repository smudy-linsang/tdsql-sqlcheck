# DEV-v1.6.1.0 ZK 枚举失败语义细化与 sysdb 屏蔽 开发记录

> 依据设计：`docs/DESIGN-v1.6.0.8-ZK发现枚举失败语义细化与sysdb屏蔽详细设计说明书.md`
> 基线：v1.6.0.7（`main @ 53f7728`）→ 交付版号 **v1.6.1.0**（按负责人要求）
> 性质：纯逻辑与展示变更，无 DDL、无配置迁移

## 一、变更清单

| 文件 | 变更 |
|---|---|
| `backend/services/zk_scan_enrich_service.py` | ① `SYSTEM_DATABASES` 增加 `sysdb`（单源定义）；② 新增 `_errno_of()`：沿 `__cause__` 链提取数据库 errno；③ `_list_business_databases` 逐端点记录 errno，0 成功且**全部 1045** → 返回 `NO_BUSINESS_USER`，含连接类/混合 → 仍 `NO_AVAILABLE_PROXY`；④ WARN 日志补 `errno=` 字段 |
| `backend/services/zk_connection_import_service.py` | ① 删除本地重复 `SYSTEM_DATABASES`，改从富集侧 import（含 `_errno_of`），消除双份漂移；② 预检枚举同口径分类，全 1045 抛 `ZKImportPreparationError("NO_BUSINESS_USER", …)`（文本带端点不带口令） |
| `frontend/index.html` | ① 扫描列表「业务库」列：`dbs_failed:NO_BUSINESS_USER` → warning 色"未创建监控用户"+tooltip 处置建议；其余 `dbs_failed:*` → "枚举失败"+tooltip 原始来源；② 导入预览「状态」列：失败码经 `zkFailureLabel` 映射为可读标签，`NO_BUSINESS_USER` 用 warning 色；③ 标题/登录页版本位 → V1.6.1.0；app.js 缓存参数 → `?v=20260806.2` |
| `frontend/static/js/app.js` | 新增 `zkFailureLabel()`：`NO_BUSINESS_USER→未创建监控用户`、`NO_AVAILABLE_PROXY→Proxy 不可达`、`BUSINESS_PROXY_TIMEOUT→枚举超时`，其余原码兜底；并暴露给模板 |
| `backend/config.py` / `VERSION` | 版号 → 1.6.1.0 |

## 二、关键实现决策

1. **分类规则可预测、不猜测**：仅当失败端点 errno **全部**为 1045 才判 `NO_BUSINESS_USER`；
   混合（如 1045+2003）归 `NO_AVAILABLE_PROXY`——避免把网络问题误报成"未建用户"。
2. **errno 沿包装层追溯**：A-P2-02 的 `_connect` 会把 pymysql 异常包成
   `ZKImportPreparationError`（原始异常在 `__cause__`），`_errno_of` 统一处理两路径差异。
3. **协议层诚实性**：1045 无法区分"用户不存在/口令错误"，短标签按负责人要求显示
   "未创建监控用户"，tooltip 给完整语义"通常未创建监控用户或口令与配置不一致"。
4. **sysdb 排除两路径共用单源常量**，扫描列表计数与预览行天然一致；
   手工业务库（manual_databases）不过滤（显式意图）；历史已导入连接不追溯。
5. 保持只读原则：不自动创建用户。

## 三、自测摘要（详见 TEST 文档）

- 新增 `tests/test_zk_v1608.py` 6 用例：全 1045 两路径、混合反向鉴别、sysdb 两路径排除+手工库反向、前端结构守卫；
- 适配 `tests/test_zk_v1605.py`（sysdb 现被排除）与缓存版本断言；
- 全量回归 **1283 通过 / 0 失败 / 0 跳过**（R-18）；`node --check app.js` 通过。
