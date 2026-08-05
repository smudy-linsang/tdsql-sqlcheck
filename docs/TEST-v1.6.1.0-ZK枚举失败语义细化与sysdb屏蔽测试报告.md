# TEST-v1.6.1.0 ZK 枚举失败语义细化与 sysdb 屏蔽 测试报告

> 被测版本：v1.6.1.0　依据：`DESIGN-v1.6.0.8`、`DEV-v1.6.1.0`
> 结论：🟢 两项变更均按设计落地，全量回归 0 失败 0 跳过，可交智能体 A 复测

## 一、新增用例（tests/test_zk_v1608.py，6/6 通过）

| 用例 | 覆盖 | 结果 |
|---|---|---|
| 全端点 1045 → 富集来源 `NO_BUSINESS_USER` | 变更一富集路径 | ✅ |
| 混合 1045+2003 → 仍 `NO_AVAILABLE_PROXY` | 分类不越界（R-12 反向鉴别） | ✅ |
| 预检全 1045 → 逐行 `failure_code=NO_BUSINESS_USER` | 变更一预检路径；errno 沿 `_connect` 包装层 `__cause__` 追溯 | ✅ |
| 预检排除 sysdb；手工库填 sysdb 保留 | 变更二 + 显式意图反向鉴别 | ✅ |
| 富集排除 sysdb/mysql | 变更二富集路径 | ✅ |
| 前端结构守卫：扫描列表"未创建监控用户"短标签+tooltip、`zkFailureLabel` 映射齐备 | 变更一展示层 | ✅ |

## 二、既有用例适配

- `test_zk_v1605.py`：假目录含 sysdb 的用例断言更新为排除后结果（`["cap_gz"]`），
  该用例顺带成为 sysdb 排除的回归守卫；
- `test_zk_discovery.py`：app.js 缓存参数断言同步 `?v=20260806.2`。

## 三、回归

| 口径 | 结果 |
|---|---|
| 全量 `pytest tests/` | **1283 通过 / 0 失败 / 0 跳过**（较 v1.6.0.7 基线 1277 +6 新用例） |
| `node --check frontend/static/js/app.js` | 通过 |
| ZK 专项（v1605/v1606/v1608/discovery/import_commit） | 50 通过 |

## 四、给 A 的复测建议

1. 替身拓扑造"实例无 checksql 用户"：双 Proxy 全 1045 → 扫描列表应显示
   **未创建监控用户**（warning 色，tooltip 含处置建议），而非"枚举失败"；
2. 反向鉴别：造 2003 死 Proxy → 仍显示"枚举失败"（不得误标未建用户）；
3. 导入预览：含 sysdb 的实例不再生成 sysdb 候选行（截图 7 条→6 条口径）；
   手工业务库填 sysdb 仍保留；
4. 预览失败行状态列：`NO_BUSINESS_USER` 显示"未创建监控用户"warning 标签；
5. 日志核对：`ZK_ENRICH_PROXY_FAILED`/`ZK_IMPORT_BUSINESS_PROXY_FAILED` 带 `errno=1045`。

## 五、未覆盖声明

- 真实内网 TDSQL 的 1045 行为与本机 pymysql 模拟一致（协议层标准返回），但
  "未创建监控用户"文案的最终观感需内网浏览器确认；
- kazoo 驱动真实 ZK 路径仍未在本容器验证（沿用历轮声明）。
