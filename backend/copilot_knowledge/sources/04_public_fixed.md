---
source_id: PUBLIC_FIXED_QUESTIONS
title: Copilot 公共固定问题（PUBLIC_HELP 正向来源）
authority: USER_GUIDE
doc_path: docs/copilot-public-faq
version_range: 1.6.x
section: 公共问题
product_families: [TDSQL-MySQL]
public_fixed: true
---

# PUB_Q1 这个工具能做什么

TDSQL SQL 审核工具提供 SQL 审核（一百余条规则）、慢 SQL 采集分析、扫描结果纵向对比、深度诊断、权限管理与报告导出。Copilot 专家助手用于解释功能、规则与已有记录，给出建议；它不代替正式审核结论，也不执行数据库变更。

# PUB_Q2 审核不通过怎么办

先看违规的规则号与级别，再看规则说明与修复建议。ERROR 级别必须处理。修改后需要重新执行正式审核；助手给出的候选 SQL 只是建议，需人工确认后送入审核编辑器复核。

# PUB_Q3 任务失败怎么排查

先看任务的状态、阶段与错误码，再核对执行器是否在线、实例授权是否有效、资料是否超上限。没有证据时不要直接断定是内存不足；可以按错误码在本助手中查询对应的故障处理条目。

# PUB_Q4 我的数据会被发送到哪里

默认情况下助手只使用本地知识库回答。启用模型后，只有经过脱敏且获得批准的资料才会发送到部署批准的内网模型端点；凭据、业务行数据、原始日志永远不会出站。每次提问前可以预览本次实际带入的资料。
