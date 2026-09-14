---
source_id: TDSQL_SYNTAX_NOTES
title: TDSQL MySQL 版官方语法摘要
authority: VENDOR_SYNTAX
url: https://cloud.tencent.com/document/product/557/8767
version_range: TDSQL-MySQL 分布式 10.3 / 集中式 8.0
section: 厂商语法
product_families: [TDSQL-MySQL]
---

# TDSQL 分布式建表与分片键

TDSQL MySQL 分布式版的建表语法支持分片键声明（如 DISTRIBUTED BY / shard key 相关写法），分片键一旦确定后修改代价高。分布式 DDL 经由 Proxy 下发到各 SET 执行。厂商语法允许某写法，不代表项目规范允许；本项目规则集（如 R121 等）可能在厂商能力之上另有限制，两层结论要分开说明。

# TDSQL 二级分区

TDSQL MySQL 版支持二级分区（见腾讯云官方文档 product/557/58907）。二级分区涉及主表与物理子表两层结构。在统计口径上，主表、物理子表、逻辑总表是不同概念，统计值不能直接相加。MAXVALUE 分区是否能创建属于厂商语法能力，与项目是否禁止（如项目规则 R121）是两个不同层级的事实。

# TDSQL 与原生 MySQL 差异

TDSQL-MySQL 与 TDSQL-C、Boundless、原生 MySQL 是不同产品族，语法能力不能混用。回答具体语法问题时必须限定在本知识包标注的产品族与版本范围；超出范围时明确说明未知，不要把其他产品族的经验套用到 TDSQL-MySQL。

# 审核规则的厂商语法与项目要求区分

平台规则号以 R 开头（如 R011、R030、R035、R043、R058、R121）。规则运行时定义以平台当前规则集为准；历史报告按当时冻结的规则版本解释。厂商支持某语法（事实层）与项目规范禁止某用法（规范层）可以同时成立，不应互相覆盖。
