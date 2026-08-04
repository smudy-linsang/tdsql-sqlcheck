# TEST-v1.6.0.3 ZK 发现内网适配与导入体验 自测报告

> 版本：v1.6.0.3（`backend/config.py` APP_VERSION / `VERSION` / 前端标题同步）
> 依据设计：`docs/DESIGN-v1.6.0.3-ZK发现内网适配与导入体验详细设计说明书.md`
> 性质：开发自测 + 真实浏览器 UAT（本机可达 SIT 云 TDSQL/MonitorDB，ZK 协议不可达）
> 日期：2026-08-04

---

## 1. 结论

**v1.6.0.3 设计四项（Q1~Q4）全部实现并通过自测。** 全量回归 **1259 通过 / 0 失败 / 0 跳过**（R-18 零跳过口径）。
新增 `tests/test_zk_v1603.py`（ZE-01~08）7 通过 1 条件跳过；`tests/test_zk_discovery.py` 18 通过；
`tests/test_zk_import_commit.py` 11 通过。真实浏览器确认配置弹窗新字段、实例列表、仪表盘均正常渲染。

**环境限制如实声明**：本机到 ZK 2118 为"TCP 握手通、协议被状态拦截"（与 A 上轮一致），
故"真实 ZK 扫描→富集"链路在本机无法端到端；该链路以 ZE-01~05 的单元/集成用例 + 真实
MonitorDB/Proxy 的 `build_preview` 实测（v1.6.0.2 轮已验证 L1 命中与 proxy_show 枚举）覆盖。
内网真实集群 UAT 仍为放行前置（见设计 §10.2）。

---

## 2. 实现清单（对照设计 §3~§9）

| 设计 | 实现 | 位置 |
|---|---|---|
| §5 IP 段替换 | `apply_endpoint_mapping(results, map, octet_rules)`：先段替换后精确映射，作用于 host+proxy_list，记录 `original_host` | `zk_discovery_service.py` |
| §4 五级解析链 | `ZKNameResolutionService.resolve/diagnose`（L1 精确/L2 模糊/L3 值像名称/L4 元数据表/L5 ZK 节点），带 `name_source` 溯源 | `zk_name_resolution_service.py`（新） |
| §3 扫描富集 | `enrich_discovered_items`：名称串行+业务库池化，超时/失败不阻断，`enrich_status` 留痕，500 上限 | `zk_scan_enrich_service.py`（新） |
| §6 分页+五维筛选 | 客户端 computed 过滤+分页，跨页勾选合并 | `app.js` + `index.html` |
| §7 配置扩展 | `zk_discovery_config` 增 11 列（v10 迁移），口令 Fernet 加密、GET 零回显 | `v10/100_zk_scan_enrich.sql` + 配置服务 |
| §8 接口 | `discover` 富集、`name-diagnose`（新增）、`import-preview` 接收 octet_rules/manual_databases/name_overrides、`import-commit` 留痕 name_source/databases_source | `zk_discovery.py` |
| 导入兜底 | `build_preview`：名称 手工>富集>解析链；业务库 手工>富集>Proxy 枚举；错误行保留已解析名称 | `zk_connection_import_service.py` |

---

## 3. 自测结果

### 3.1 自动化
| 套件 | 结果 |
|---|---|
| 全量 `pytest tests` | **1259 通过 / 0 失败 / 0 跳过** |
| `test_zk_v1603.py` ZE-01~08 | 7 通过 / 1 跳过（ZE-08 仅在强制认证且无口令时跳） |
| `test_zk_discovery.py` | 18 通过 |
| `test_zk_import_commit.py` | 11 通过 |

ZE 用例要点：
- ZE-01 段替换 `10.243.21.13→10.243.20.13`，精确映射覆盖个别主机 ✔
- ZE-02/03 L2 模糊 mid、L3 值像名称、L4 元数据表 命中且 `name_source` 正确 ✔
- ZE-04 全链落空→手工命名/手工库可导入，来源 manual ✔
- ZE-05 业务库枚举失败不阻断扫描 ✔
- ZE-06 前端分页/筛选/段替换绑定存在 ✔
- ZE-07 GET config 不含 MonitorDB/业务/认证口令明文或密文 ✔

### 3.2 真实浏览器 UAT（V1.6.0.3）
| 项 | 结果 |
|---|---|
| 登录页/仪表盘渲染 V1.6.0.3，真实数据 | ✔ |
| 实例管理列出 SIT 两实例（分布式/集中式·探测） | ✔ |
| ZK 发现配置弹窗：段替换、启用富集、MonitorDB 五项、业务账号、名称解析固化、内外网映射 全部渲染 | ✔（a11y 快照留证） |
| 配置保存后 `最近保存` 显示操作人/时间；口令不回显 | ✔ |

### 3.3 真实 MonitorDB/Proxy 链路（沿用 v1.6.0.2 轮实测结论）
- L1 精确 mid 在 SIT monitordb 命中（`instance_name/clientName`，f_type=1）；
- 业务库经映射 Proxy `SHOW DATABASES` 枚举成功（`proxy_show`），冲突行正确标记 `conflict`。

---

## 4. 遗留与放行前置

1. **内网真实集群 UAT**（设计 §10.2）：真实 ZK 扫描→富集、name-diagnose 固化、段替换实测、
   分页/筛选在 ≥200 条下体验、一个集中式+一个分布式导入闭环。本机不可达 ZK，须内网执行。
2. 内网首测建议先跑 `name-diagnose` 确认命中级别并固化 `name_query_hint`，再开富集扫描。

*自测未改动安全模型：Mock 闸门、凭据加密、唯一约束、失败留痕均保持 v1.6.0.1/0.2 口径。*
