---
source_id: FAULT_ENTRIES
title: 已批准故障处理条目
authority: VERIFIED_CASE
doc_path: docs/fault-handling
version_range: 1.6.x
section: 故障处理
product_families: [TDSQL-MySQL]
---

# 执行器未就绪 EXECUTOR_UNAVAILABLE

现象：提交在线元数据审核任务返回 EXECUTOR_UNAVAILABLE 或任务长时间停在待执行。处理：确认 tdsql-metadata-runner 服务已启动且有最新心跳；检查 metadata_audit_slot 表中的心跳时间是否新鲜；Web 与 runner 的版本必须一致。

# 状态未知与恢复 REQUIRED

现象：任务状态显示 RECOVERY_REQUIRED 或中断。含义：执行器曾失去对子进程的跟踪，平台选择保守标记而不是猜测成功。处理：人工核对实际产物是否完整，必要时重新发起任务；不要把中断记录当作失败原因已查明。

# 资源上限 RESOURCE_LIMIT

现象：任务以 RESOURCE_LIMIT 失败。含义：执行过程触达了平台设定的内存或时间上限。处理：缩小单次审核范围（按库/按对象分批），或联系管理员评估提升限额；没有 RSS 峰值等证据时不能把原因进一步归结为某一具体泄漏点。

# 权限错误

现象：提取或读取资料时报权限拒绝。处理：核对账号在目标实例的权限、Copilot 实例授权是否获批且处于 APPROVED 状态、审批单号是否填写。撤销立即生效，已发送给模型的数据无法撤回。

# 资料过大 CONTEXT_TOO_LARGE

现象：预览返回资料超过上限。处理：减少一次带入的来源数量（最多 4 个），缩小分页范围，或改为分多次提问。不要为了绕过限制把大段日志直接粘进问题。

# 网关摘要质量与 PARTIAL

现象：网关报告解读提示 PARTIAL 或摘要缺失。含义：结构化分析摘要不存在或超出读取上限。处理：回到网关报告页面确认原报告是否生成完整；摘要缺失时助手只能给出一般性说明，不能编造明细。

# PARTIAL 统计

现象：表类型统计或扫描对比显示 PARTIAL/UNKNOWN。含义：采集不完整或口径未知，不是 0 也不是正常。处理：查看缺失原因说明，补齐采集后再解读；不要把 PARTIAL 当作全量结论使用。
