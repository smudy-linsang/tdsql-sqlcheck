# -*- coding: utf-8 -*-
"""v1.6.2.2 解析恢复链 —— 唯一 case manifest（第十一轮 BLOCK-11-07）。

本文件是**全部用例的唯一真源**。设计说明书 §7.1/§7.1a/§7.1b 的每一张用例表、
每一个计数，都由 `manifest_doc.py` 从这里生成；任何章节都不得再人工维护第二份。

字段
----
cid          稳定 ID（组名 + 序号），一经分配不再变更；新增只追加不插队
group        组名（A/B/C/D/E/F/T/N/X/Y/Z/W/H*/P*/M*/TY*/R11*）
label        中文标签
sql          完整 SQL（None 表示该例由 fixture 文件提供，见 extra['fixture']）
klass        分类，决定判据：
             pos                   必须恢复：plan=True、AST=Create、无 E999
             neg                   必须失败关闭：plan=False 且 AST≠Create
             pos_known             TDSQL 官方合法但 sqlglot 解析不了 →
                                   必须失败关闭，单独计入已知假阴性（KFN-A）
             unsupported_unproven  无 TDSQL/目标实例证据 → 必须失败关闭（KFN-B），
                                   既不冒充合法也不冒充非法
             characterization      用户已冻结的表征行为，锁定当前结论，不代表 TDSQL 合法
             ruleset               断言规则命中集合精确相等（生产 fixture 回放）
             spans                 断言剥离 span 的数量与越界字符数
             contract              断言 sqlglot AST 契约（升级破坏时必须显式失败）
prov         证据来源：
             OFFICIAL          腾讯 TDSQL 官方文档
             TARGET_INSTANCE   目标实例实测
             CORPUS            197 条语料 / 生产 14 表实证
             PROJECT_ACCEPTED  项目既有已接受用例
             SQLGLOT_LIMIT     sqlglot 自身能力边界（修复前后行为一致）
             USER_DECISION     用户冻结决策
             REVIEW_11         第十一轮复审报告 §4~§9 的反例
note         一句话理由
extra        判据参数（期望 span 数、期望规则集合、fixture 名、instance_type 等）
"""
from collections import namedtuple

CASE = namedtuple("CASE", "cid group label sql klass prov note extra")
CASES = []


def add(group, label, sql, klass, prov, note="", **extra):
    cid = "%s-%02d" % (group, sum(1 for c in CASES if c.group == group) + 1)
    CASES.append(CASE(cid, group, label, sql, klass, prov, note, extra))


# ══════════════════════════════════════════════════════════════════════════
# A 组 —— DEF-1 索引类型判据 + AST 契约
# ══════════════════════════════════════════════════════════════════════════
_A = ("CREATE TABLE `t` (`id` int NOT NULL, `sk` int NOT NULL, `%s` int NOT NULL, "
      "PRIMARY KEY (`id`,`sk`), %s) ENGINE=InnoDB shardkey=sk")
for lbl, col, idx, want in [
    ("普通索引，列名 list_unique_num", "list_unique_num", "KEY `k` (`list_unique_num`)", "NORMAL"),
    ("索引名 unique_lookup",           "c",               "KEY `unique_lookup` (`c`)",   "NORMAL"),
    ("列名 biz_primary_no",            "biz_primary_no",  "KEY `k` (`biz_primary_no`)",  "NORMAL"),
    ("列名 fulltext_body",             "fulltext_body",   "KEY `k` (`fulltext_body`)",   "NORMAL"),
    ("真 FULLTEXT KEY（反向鉴别）",     "c",               "FULLTEXT KEY `ft` (`c`)",     "FULLTEXT"),
]:
    add("A", lbl, _A % (col, idx), "pos", "PROJECT_ACCEPTED",
        "索引类型只认关键字，不得从名字/列名猜", index_type=want, needs_recovery=False)
add("A", "真 UNIQUE 不含分片键 → R054 命中",
    _A % ("c", "UNIQUE KEY `uk` (`c`)"), "pos", "PROJECT_ACCEPTED",
    "反向鉴别：真 UNIQUE 必须触发 R054", rule_hit="R054", needs_recovery=False)
add("A", "真 UNIQUE 含分片键 → R054 不命中",
    _A % ("c", "UNIQUE KEY `uk` (`sk`,`c`)"), "pos", "PROJECT_ACCEPTED",
    "含分片键的 UNIQUE 合规", rule_miss="R054", needs_recovery=False)
add("A", "诱饵列名 + 真 UNIQUE 不含分片键 → R054 命中",
    _A % ("list_unique_num", "UNIQUE KEY `uk` (`list_unique_num`)"), "pos", "PROJECT_ACCEPTED",
    "本组最重要：锁定漏报修复", rule_hit="R054", needs_recovery=False)
add("A", "sqlglot AST 契约", None, "contract", "PROJECT_ACCEPTED",
    "UNIQUE→UniqueColumnConstraint、PRIMARY→PrimaryKey、FULLTEXT/SPATIAL→IndexColumnConstraint")

# ══════════════════════════════════════════════════════════════════════════
# B 组 —— DEF-2 正向恢复
# ══════════════════════════════════════════════════════════════════════════
_B = "CREATE TABLE `t` (`id` int NOT NULL COMMENT 'i', `sk` int NOT NULL COMMENT 's'%s) %s"
for lbl, defs, tail in [
    ("单个 UNIQUE COMMENT",        ", UNIQUE KEY `uk` (`sk`) COMMENT 'u'", "ENGINE=InnoDB"),
    ("UNIQUE COMMENT 双引号值",     ', UNIQUE KEY `uk` (`sk`) COMMENT "u"', "ENGINE=InnoDB"),
    ("UNIQUE INDEX 写法",          ", UNIQUE INDEX `uk` (`sk`) COMMENT 'u'", "ENGINE=InnoDB"),
    ("裸 UNIQUE（无 KEY/INDEX）",   ", UNIQUE `uk` (`sk`) COMMENT 'u'", "ENGINE=InnoDB"),
    ("USING BTREE + COMMENT",     ", UNIQUE KEY `uk` (`sk`) USING BTREE COMMENT 'u'", "ENGINE=InnoDB"),
    ("两个 UNIQUE 各带 COMMENT",    ", UNIQUE KEY `u1` (`id`) COMMENT 'a', UNIQUE KEY `u2` (`sk`) COMMENT 'b'", "ENGINE=InnoDB"),
    ("UNIQUE COMMENT + 普通 KEY COMMENT", ", UNIQUE KEY `uk` (`sk`) COMMENT 'u', KEY `k` (`id`) COMMENT 'n'", "ENGINE=InnoDB"),
    ("COMMENT 值含转义单引号",       ", UNIQUE KEY `uk` (`sk`) COMMENT 'it''s'", "ENGINE=InnoDB"),
    ("COMMENT 值含中文与括号",       ", UNIQUE KEY `uk` (`sk`) COMMENT '唯一(索引)'", "ENGINE=InnoDB"),
    ("多列 UNIQUE + COMMENT",      ", UNIQUE KEY `uk` (`id`,`sk`) COMMENT 'u'", "ENGINE=InnoDB"),
    ("PRIMARY + UNIQUE COMMENT",   ", PRIMARY KEY (`id`), UNIQUE KEY `uk` (`sk`) COMMENT 'u'", "ENGINE=InnoDB"),
    ("UNIQUE COMMENT + 表选项全套",  ", UNIQUE KEY `uk` (`sk`) COMMENT 'u'",
     "ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin COMMENT='表'"),
]:
    add("B", lbl, _B % (defs, tail), "pos", "CORPUS", "正向恢复：raw_sql 必须逐字等于输入")

# ══════════════════════════════════════════════════════════════════════════
# C 组 —— DEF-2 产品边界（sqlglot 自身不支持，去掉 COMMENT 也 ParseError）
# ══════════════════════════════════════════════════════════════════════════
for lbl, frag in [
    ("函数键值 ((lower(a)))",  "UNIQUE KEY `uk` ((lower(`a`))) COMMENT 'x'"),
    ("VISIBLE",              "UNIQUE KEY `uk` (`a`) COMMENT 'x' VISIBLE"),
    ("KEY_BLOCK_SIZE",       "UNIQUE KEY `uk` (`a`) KEY_BLOCK_SIZE=8 COMMENT 'x'"),
    ("USING 前置于键值列表",     "UNIQUE KEY `uk` USING BTREE (`a`) COMMENT 'x'"),
]:
    add("C", lbl,
        "CREATE TABLE `t` (`a` int NOT NULL COMMENT 'x', PRIMARY KEY (`a`), %s) ENGINE=InnoDB" % frag,
        "pos_known", "SQLGLOT_LIMIT",
        "去掉 COMMENT 后 sqlglot 同样 ParseError → 非剥离器缺陷")

# ══════════════════════════════════════════════════════════════════════════
# D 组 —— 负向 / 防次生灾害（断言 span 数与越界改写字符数）
# ══════════════════════════════════════════════════════════════════════════
_D = "CREATE TABLE `t` (`a` int NOT NULL %s, UNIQUE KEY `uk` (`a`) COMMENT 'real') ENGINE=InnoDB %s"
for lbl, coldef, tail in [
    ("伪 SQL 藏在列 COMMENT",  "COMMENT 'UNIQUE KEY z (a) COMMENT ''fake'''", ""),
    ("伪 SQL 藏在表 COMMENT",  "COMMENT 'x'", "COMMENT='UNIQUE KEY z (a) COMMENT ''fake'''"),
    ("伪 SQL 藏在 DEFAULT 串", "DEFAULT 'UNIQUE KEY z (a) COMMENT ''fake''' COMMENT 'x'", ""),
]:
    add("D", lbl, _D % (coldef, tail), "spans", "PROJECT_ACCEPTED",
        "只允许抹掉真实索引 COMMENT，越界字符数必须为 0", spans=1)
add("D", "伪 SQL 藏在 -- 行注释",
    "CREATE TABLE `t` (`a` int NOT NULL COMMENT 'x', -- UNIQUE KEY z (a) COMMENT 'fake'\n"
    " UNIQUE KEY `uk` (`a`) COMMENT 'real') ENGINE=InnoDB", "spans", "PROJECT_ACCEPTED",
    "行注释内容不可见", spans=1)
add("D", "伪 SQL 藏在 /* */ 块注释",
    "CREATE TABLE `t` (`a` int NOT NULL COMMENT 'x', /* UNIQUE KEY z (a) COMMENT 'fake' */"
    " UNIQUE KEY `uk` (`a`) COMMENT 'real') ENGINE=InnoDB", "spans", "PROJECT_ACCEPTED",
    "块注释内容不可见", spans=1)
add("D", "伪 SQL 藏在反引号标识符内",
    "CREATE TABLE `t` (`UNIQUE KEY z (a) COMMENT ''fake''` int NOT NULL COMMENT 'x',"
    " UNIQUE KEY `uk` (`a`) COMMENT 'real') ENGINE=InnoDB", "spans", "PROJECT_ACCEPTED",
    "标识符内容不可见", spans=1)

# ══════════════════════════════════════════════════════════════════════════
# E 组 —— 失败关闭
# ══════════════════════════════════════════════════════════════════════════
for lbl, sql in [
    ("未闭合单引号",   "CREATE TABLE `t` (`a` int COMMENT 'x, UNIQUE KEY `uk` (`a`) COMMENT 'u') ENGINE=InnoDB"),
    ("未闭合括号",     "CREATE TABLE `t` (`a` int, UNIQUE KEY `uk` (`a` COMMENT 'u') ENGINE=InnoDB"),
    ("非 CREATE TABLE", "ALTER TABLE `t` ADD UNIQUE KEY `uk` (`a`) COMMENT 'u'"),
    ("缺右括号建表",   "CREATE TABLE `t` (`a` int, UNIQUE KEY `uk` (`a`) COMMENT 'u' ENGINE=InnoDB"),
]:
    add("E", lbl, sql, "neg", "PROJECT_ACCEPTED", "剥离器返回 None 或重试失败，仍报原错误")

# ══════════════════════════════════════════════════════════════════════════
# F 组 —— 生产回放（精确规则集合相等）
# ══════════════════════════════════════════════════════════════════════════
add("F", "report_6309_kcfb_list_info.sql（分布式）", None, "ruleset", "CORPUS",
    "精确相等，子集断言证明不了零新增",
    fixture="report_6309_kcfb_list_info.sql", instance_type="distributed",
    rules={"R011", "R018", "R019", "R036", "R037", "R061", "R065", "R067", "R104"})
add("F", "report_6311_biz_tx_log.sql（集中式）", None, "ruleset", "CORPUS",
    "精确相等；集中式上下文不得与分布式混用",
    fixture="report_6311_biz_tx_log.sql", instance_type="centralized",
    rules={"R036", "R037"})

# ══════════════════════════════════════════════════════════════════════════
# T 组 —— TDSQL 方言组合（每例额外断言：与"同表去掉索引 COMMENT"的规则集合相等）
# ══════════════════════════════════════════════════════════════════════════
_T = ("CREATE TABLE `t` (`a` int NOT NULL COMMENT 'x', `sk` int NOT NULL COMMENT 'y',\n"
      " `create_time` datetime NOT NULL COMMENT 'c', `update_time` datetime NOT NULL COMMENT 'u',\n"
      " `is_deleted` tinyint NOT NULL DEFAULT 0 COMMENT 'd', PRIMARY KEY (`a`,`sk`),\n"
      " UNIQUE KEY `uk` (`a`,`sk`) COMMENT '唯一索引说明'\n) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='表' %s")
for lbl, tail in [("T1 HASH", "TDSQL_DISTRIBUTED BY HASH(`sk`)"),
                  ("T2 RANGE", "TDSQL_DISTRIBUTED BY RANGE(`sk`)"),
                  ("T3 LIST", "TDSQL_DISTRIBUTED BY LIST(`sk`)"),
                  ("T4 BROADCAST", "BROADCAST"),
                  ("T6 shardkey=（对照）", "shardkey=sk")]:
    add("T", lbl, _T % tail, "pos", "OFFICIAL",
        "恢复不得引入自己的口径：规则集合须等于去掉 COMMENT 的同表",
        equal_ruleset_without_comment=True, instance_type="distributed")
add("T", "T5 HASH + 二级分区",
    "CREATE TABLE `t5` (`a` int NOT NULL COMMENT 'x', `sk` int NOT NULL COMMENT 'y',"
    " PRIMARY KEY (`a`,`sk`), UNIQUE KEY `uk` (`a`,`sk`) COMMENT 'z') ENGINE=InnoDB"
    " TDSQL_DISTRIBUTED BY HASH(`sk`) PARTITION BY RANGE(`a`) (PARTITION p0 VALUES LESS THAN (10))",
    "pos", "PROJECT_ACCEPTED", "D5/T5 既有用例不得回归",
    equal_ruleset_without_comment=True, instance_type="distributed")
_TT = ("CREATE TEMPORARY TABLE `tt` (`a` int NOT NULL COMMENT 'x', PRIMARY KEY (`a`),"
       " UNIQUE KEY `u` (`a`) COMMENT 'z') ENGINE=InnoDB")
add("T", "T9 TEMPORARY（集中式）", _TT, "pos", "PROJECT_ACCEPTED",
    "R032 仍命中；is_temporary_table 为真", instance_type="centralized", rule_hit="R032")
add("T", "T10 TEMPORARY（分布式）", _TT, "pos", "PROJECT_ACCEPTED",
    "R024+R032 仍命中", instance_type="distributed", rule_hit="R032")

# ══════════════════════════════════════════════════════════════════════════
# N 组 —— 作用域负向 / 已知保真缺口
# ══════════════════════════════════════════════════════════════════════════
add("N", "N1 CONSTRAINT ... UNIQUE",
    "CREATE TABLE `t` (`a` int NOT NULL COMMENT 'x',\n CONSTRAINT `uq` UNIQUE (`a`) COMMENT 'cc',\n"
    " UNIQUE KEY `uk` (`a`) COMMENT 'real'\n) ENGINE=InnoDB",
    "pos_known", "USER_DECISION",
    "Rev.P：CONSTRAINT UNIQUE 本期不扩支持，三条解析路径均由 KFN-6 + E999 阻断",
    kfn="KFN-6-CONSTRAINT-UNIQUE", e999=True)
for lbl, sql, n in [
    ("N2 列内联 UNIQUE",
     "CREATE TABLE `t` (`a` int NOT NULL UNIQUE COMMENT 'inline',\n `b` int COMMENT 'y',\n"
     " UNIQUE KEY `uk` (`b`) COMMENT 'real'\n) ENGINE=InnoDB", 1),
    ("N3 定义项中部 UNIQUE（整句非法，须拒绝）",
     "CREATE TABLE `t` (`a` int NOT NULL COMMENT 'x',\n KEY `k` (`a`) UNIQUE COMMENT 'mid',\n"
     " UNIQUE KEY `uk` (`a`) COMMENT 'real'\n) ENGINE=InnoDB", 0),
    ("N4 两条语句拼接（须拒绝）",
     "CREATE TABLE `t1` (`a` int NOT NULL COMMENT 'x', UNIQUE KEY `u1` (`a`) COMMENT 'first') ENGINE=InnoDB;\n"
     "CREATE TABLE `t2` (`b` int NOT NULL COMMENT 'y', UNIQUE KEY `u2` (`b`) COMMENT 'second') ENGINE=InnoDB", 0),
    ("N5 定义列表闭合后的表选项",
     "CREATE TABLE `t` (`a` int NOT NULL COMMENT 'x', UNIQUE KEY `uk` (`a`) COMMENT 'real') ENGINE=InnoDB"
     " COMMENT='tail UNIQUE KEY z (a) COMMENT ''fake'''", 1),
]:
    add("N", lbl, sql, "spans", "PROJECT_ACCEPTED", "抹除的必须正是那个真实目标", spans=n)

# ══════════════════════════════════════════════════════════════════════════
# X 组 —— 方言尾子句安全交叉矩阵（4 尾子句 × 5 诱饵 × 带/不带 UNIQUE COMMENT）
#         每例做字段级精确断言：列名序列、目标列注释、DEFAULT、raw_sql 逐字
# ══════════════════════════════════════════════════════════════════════════
_X_TAILS = [("HASH", "TDSQL_DISTRIBUTED BY HASH(`sk`)"), ("RANGE", "TDSQL_DISTRIBUTED BY RANGE(`sk`)"),
            ("LIST", "TDSQL_DISTRIBUTED BY LIST(`sk`)"), ("BROADCAST", "BROADCAST")]
_X_DECOYS = [
    ("列名为 `broadcast`", "`broadcast` varchar(20) DEFAULT NULL COMMENT 'bc'",
     ["id", "sk", "broadcast"], None),
    ("裸列名 broadcast", "broadcast varchar(20) DEFAULT NULL COMMENT 'bc'",
     ["id", "sk", "broadcast"], None),
    ("列注释含 broadcast", "`note` varchar(80) DEFAULT NULL COMMENT 'broadcast table info'",
     ["id", "sk", "note"], ("note", "broadcast table info")),
    ("列注释含伪 TDSQL 子句", "`note` varchar(80) DEFAULT NULL COMMENT 'TDSQL_DISTRIBUTED BY HASH(fake)'",
     ["id", "sk", "note"], ("note", "TDSQL_DISTRIBUTED BY HASH(fake)")),
    ("DEFAULT 值含 broadcast", "`note` varchar(80) DEFAULT 'broadcast' COMMENT 'n'",
     ["id", "sk", "note"], ("note", "n")),
]
for _tl, _tail in _X_TAILS:
    for _dl, _col, _cols, _cmt in _X_DECOYS:
        for _withuk in (True, False):
            _uk = (" UNIQUE KEY `uk` (`sk`) COMMENT 'x'," if _withuk
                   else " UNIQUE KEY `uk` (`sk`),")
            _s = ("CREATE TABLE `t` (`id` bigint NOT NULL COMMENT 'i',\n `sk` bigint NOT NULL COMMENT 's',\n %s,\n"
                  " PRIMARY KEY (`id`,`sk`),\n%s\n KEY `idx_k2` (`id`)\n) ENGINE=InnoDB %s"
                  % (_col, _uk, _tail))
            add("X", "%s × %s × %s" % (_tl, _dl, "带 UK COMMENT" if _withuk else "无 UK COMMENT"),
                _s, "pos", "CORPUS",
                "旧全局正则 _TDSQL_DIALECT_RE 会改写定义体，本组锁定它已被删除",
                columns=_cols, column_comment=_cmt, raw_verbatim=True)

# ══════════════════════════════════════════════════════════════════════════
# Y 组 —— 方言语法严格性与语句边界
# ══════════════════════════════════════════════════════════════════════════
_Y = ("CREATE TABLE `t` (`id` bigint COMMENT 'i', `sk` bigint COMMENT 's', "
      "PRIMARY KEY (`id`,`sk`)) ENGINE=InnoDB ")
for lbl, tail in [("Y1 缺 BY", "TDSQL_DISTRIBUTED (`sk`)"),
                  ("Y2 缺方法", "TDSQL_DISTRIBUTED BY (`sk`)"),
                  ("Y3 缺 BY 有方法", "TDSQL_DISTRIBUTED HASH(`sk`)"),
                  ("Y4 未知方法 FOO", "TDSQL_DISTRIBUTED BY FOO(`sk`)"),
                  ("Y5 缺括号", "TDSQL_DISTRIBUTED BY HASH")]:
    add("Y", lbl, _Y + tail, "neg", "OFFICIAL", "非法方言声明不得被修成合法", spans=0)
for lbl, tail in [("Y6 字符串 'TDSQL_DISTRIBUTED'", "'TDSQL_DISTRIBUTED' BY HASH(`sk`)"),
                  ("Y7 反引号 `TDSQL_DISTRIBUTED`", "`TDSQL_DISTRIBUTED` BY HASH(`sk`)"),
                  ("Y8 反引号 `broadcast`", "`broadcast`")]:
    add("Y", lbl, _Y + tail, "neg", "OFFICIAL", "字符串/标识符不得冒充关键字", spans=0)
add("Y", "Y9 COMMENT='TDSQL_DISTRIBUTED' + 真 HASH",
    _Y + "COMMENT='TDSQL_DISTRIBUTED' TDSQL_DISTRIBUTED BY HASH(`sk`)", "pos", "OFFICIAL",
    "表注释恰为方言词不得阻断真实尾子句", spans=1)
add("Y", "Y10 COMMENT='BROADCAST' + 真 BROADCAST",
    _Y + "COMMENT='BROADCAST' BROADCAST", "pos", "OFFICIAL",
    "同上", spans=1)
add("Y", "Y11 HASH + BROADCAST 双声明",
    _Y + "TDSQL_DISTRIBUTED BY HASH(`sk`) BROADCAST", "neg", "OFFICIAL",
    "一级分布至多一个", spans=0)
add("Y", "Y12 HASH + RANGE 双声明",
    _Y + "TDSQL_DISTRIBUTED BY HASH(`sk`) TDSQL_DISTRIBUTED BY RANGE(`sk`)", "neg", "OFFICIAL",
    "同上", spans=0)
add("Y", "Y13 CTAS（含函数括号）",
    "CREATE TABLE `t` AS\nSELECT CONCAT('a','b') AS c, broadcast\nFROM src\nTDSQL_DISTRIBUTED BY HASH(c)",
    "spans", "OFFICIAL", "CTAS 无定义列表，SELECT 列不得被改", spans=0)
add("Y", "Y14 CREATE TABLE ... LIKE", "CREATE TABLE `t` LIKE `src`", "spans", "OFFICIAL",
    "LIKE 无定义列表；sqlglot 原生即可解析，判据是剥离器不得改写", spans=0)
add("Y", "Y15 两条语句拼接",
    "CREATE TABLE `t` (`sk` bigint COMMENT 's', PRIMARY KEY (`sk`)) ENGINE=InnoDB TDSQL_DISTRIBUTED BY HASH(`sk`);\n"
    "CREATE TABLE `u` (`x` int COMMENT 'x') ENGINE=InnoDB BROADCAST",
    "spans", "OFFICIAL", "剥离器不得跨分号改写", spans=0)
for _m in ("HASH", "RANGE", "LIST"):
    add("Y", "Y1x 合法 %s" % _m, _Y + "TDSQL_DISTRIBUTED BY %s(`sk`)" % _m, "pos", "OFFICIAL",
        "防收紧过头：RANGE/LIST 在实现中回归过一次", spans=1)
add("Y", "Y19 合法 BROADCAST", _Y + "BROADCAST", "pos", "OFFICIAL", "防收紧过头", spans=1)
add("Y", "Y20 反引号列名 `broadcast` + 真 HASH",
    "CREATE TABLE `t` (`broadcast` int COMMENT 'b', `sk` bigint COMMENT 's', PRIMARY KEY (`sk`))"
    " ENGINE=InnoDB TDSQL_DISTRIBUTED BY HASH(`sk`)", "pos", "OFFICIAL",
    "诱饵列名不得阻断真实尾子句", spans=1)

# ══════════════════════════════════════════════════════════════════════════
# Z 组 —— 方法参数与表名精确形态
# ══════════════════════════════════════════════════════════════════════════
_Z = "CREATE TABLE `t` (`id` int NOT NULL COMMENT 'i', UNIQUE KEY `uk` (`id`) COMMENT 'u') ENGINE=InnoDB "
for lbl, tail in [("Z1 HASH() 空参", "TDSQL_DISTRIBUTED BY HASH()"),
                  ("Z1 HASH(,) 逗号", "TDSQL_DISTRIBUTED BY HASH(,)"),
                  ("Z1 HASH('id') 字符串", "TDSQL_DISTRIBUTED BY HASH('id')"),
                  ("Z1 HASH(id+1) 表达式", "TDSQL_DISTRIBUTED BY HASH(`id` + 1)"),
                  ("Z1 HASH(lower(id)) 函数", "TDSQL_DISTRIBUTED BY HASH(lower(`id`))"),
                  ("Z1 HASH(a,b) 多字段", "TDSQL_DISTRIBUTED BY HASH(`a`,`b`)"),
                  ('Z1 HASH("id") 双引号', 'TDSQL_DISTRIBUTED BY HASH("id")')]:
    add("Z", lbl, _Z + tail, "neg", "OFFICIAL",
        "括号内必须恰好一个标识符；带 UK COMMENT 路径须仍报 E999", spans=0, e999=True)
for _m in ("HASH", "RANGE", "LIST"):
    add("Z", "Z2 合法 %s(`id`) 反引号" % _m, _Z + "TDSQL_DISTRIBUTED BY %s(`id`)" % _m,
        "pos", "OFFICIAL", "防收紧过头", spans=1, e999=False)
    add("Z", "Z2 合法 %s(id) 裸名" % _m, _Z + "TDSQL_DISTRIBUTED BY %s(id)" % _m,
        "pos", "OFFICIAL", "防收紧过头", spans=1, e999=False)
add("Z", "Z2 合法 BROADCAST", _Z + "BROADCAST", "pos", "OFFICIAL", "防收紧过头", spans=1, e999=False)
add("Z", "Z2 BROADCAST COMMENT='x'（哨兵后接表选项）", _Z + "BROADCAST COMMENT='x'",
    "unsupported_unproven", "CORPUS",
    "BROADCAST 是终态原子：其后不再接任何表选项。语料 197 条与生产 14 表出现 0 次，"
    "无 TDSQL 官方证据 → 失败关闭（Rev.M 统一口径，撤销 Rev.L 正文的 pos 表述）",
    spans=0, e999=True)
for lbl, sql in [
    ("Z3 单引号表名", "CREATE TABLE 't' (`id` int NOT NULL COMMENT 'i', UNIQUE KEY `uk` (`id`) COMMENT 'u')"),
    ("Z3 双引号表名", 'CREATE TABLE "t" (`id` int NOT NULL COMMENT \'i\', UNIQUE KEY `uk` (`id`) COMMENT \'u\')'),
    ("Z3 单引号表名 + HASH",
     "CREATE TABLE 't' (`id` int NOT NULL COMMENT 'i', UNIQUE KEY `uk` (`id`) COMMENT 'u')"
     " ENGINE=InnoDB TDSQL_DISTRIBUTED BY HASH(`id`)")]:
    add("Z", lbl, sql, "neg", "OFFICIAL", "表名只接受裸标识符与反引号标识符", e999=True)
_ZT = "(id int NOT NULL COMMENT 'i', UNIQUE KEY uk (id) COMMENT 'u') ENGINE=InnoDB TDSQL_DISTRIBUTED BY HASH(id)"
for lbl, head in [("Z4 裸表名", "CREATE TABLE t "), ("Z4 反引号表名", "CREATE TABLE `t` "),
                  ("Z4 库限定 `db`.`t`", "CREATE TABLE `db`.`t` "),
                  ("Z4 IF NOT EXISTS", "CREATE TABLE IF NOT EXISTS `t` ")]:
    add("Z", lbl, head + _ZT, "pos", "OFFICIAL", "合法表名形态必须仍可恢复")

# ══════════════════════════════════════════════════════════════════════════
# W 组 —— 目标上下文完整性
# ══════════════════════════════════════════════════════════════════════════
_WB = ("CREATE TABLE `t` (`id` int NOT NULL COMMENT 'i', `sk` int NOT NULL COMMENT 's', "
       "PRIMARY KEY (`id`,`sk`)%s)")
_WUK = ", UNIQUE KEY `uk` (`sk`) COMMENT 'u'"
for _ctx in ("DEFAULT", "CHECKSUM", "INDEX DIRECTORY"):
    for _tgt, _tn in (("BROADCAST", "BROADCAST"), ("TDSQL_DISTRIBUTED BY HASH(`sk`)", "HASH")):
        for _uk, _ul in ((_WUK, "带 UK COMMENT"), ("", "无 UK COMMENT")):
            add("W", "W1 残缺 %s + %s（%s）" % (_ctx, _tn, _ul),
                (_WB % _uk) + " ENGINE=InnoDB %s %s" % (_ctx, _tgt), "neg", "OFFICIAL",
                "残缺表选项上下文必须失败关闭；两条路径分别断言最终 AST",
                spans=0, ast=("NoneType" if _uk else "Command"))
for lbl, opt in [("完整 DEFAULT CHARSET", "DEFAULT CHARSET=utf8mb4"),
                 ("AUTO_INCREMENT=100", "AUTO_INCREMENT=100"),
                 ("COLLATE=utf8mb4_bin", "COLLATE=utf8mb4_bin"),
                 ("COMMENT='x'", "COMMENT='x'"),
                 ("shardkey=sk", "shardkey=sk")]:
    add("W", "W2 %s + BROADCAST" % lbl, (_WB % _WUK) + " ENGINE=InnoDB %s BROADCAST" % opt,
        "pos", "OFFICIAL", "完整表选项正例不得误伤", spans=1, ast="Create")
add("W", "W2 生产形态 全套选项 + BROADCAST",
    (_WB % _WUK) + " ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin COMMENT='表' BROADCAST",
    "pos", "CORPUS", "生产同款组合", spans=1, ast="Create")
add("W", "W2 CHECKSUM=1 + BROADCAST（无 TDSQL 证据）",
    (_WB % _WUK) + " ENGINE=InnoDB CHECKSUM=1 BROADCAST", "unsupported_unproven", "CORPUS",
    "CHECKSUM 无 TDSQL 官方证据、语料 0 例 → 失败关闭", spans=0, ast="NoneType")
add("W", "W2 ROW_FORMAT=DYNAMIC + BROADCAST（官方 local_table_option）",
    (_WB % _WUK) + " ENGINE=InnoDB ROW_FORMAT=DYNAMIC BROADCAST", "pos", "OFFICIAL",
    "官方建表页明示 ROW_FORMAT 属 local_table_option（第十轮 BLOCK-J4 更正）",
    spans=1, ast="Create")
_W3 = "CREATE TABLE `t` (`id` int NOT NULL COMMENT 'i', UNIQUE KEY `uk` (`id`) %s) ENGINE=InnoDB"
for lbl, frag in [("W3 USING 缺类型 在 COMMENT 前", "USING COMMENT 'target'"),
                  ("W3 USING 缺类型 在 COMMENT 后", "COMMENT 'target' USING"),
                  ("W3 COMMENT 后非字符串", "COMMENT `x`")]:
    add("W", lbl, _W3 % frag, "neg", "OFFICIAL", "索引选项上下文残缺必须失败关闭", spans=0, e999=True)
for lbl, frag in [("W4 USING BTREE COMMENT", "USING BTREE COMMENT 'x'"),
                  ("W4 纯 COMMENT", "COMMENT 'x'")]:
    add("W", lbl, _W3 % frag, "pos", "OFFICIAL", "索引选项正例必须仍恢复", spans=1, ast="Create")
add("W", "W5 HASH + 二级 PARTITION BY（既有 D5 场景）",
    "CREATE TABLE t_hp (id BIGINT NOT NULL, sk BIGINT NOT NULL, dt DATETIME NOT NULL, "
    "PRIMARY KEY (id, sk)) ENGINE=InnoDB TDSQL_DISTRIBUTED BY HASH(`sk`) PARTITION BY RANGE (YEAR(dt)) ("
    "PARTITION p2025 VALUES LESS THAN (2026), PARTITION p2026 VALUES LESS THAN (2027))",
    "pos", "PROJECT_ACCEPTED", "D5 场景不得回归", ast="Create")
for _uk, _ul in ((_WUK, "带 UK COMMENT"), ("", "无 UK COMMENT")):
    add("W", "W6 INDEX DIRECTORY='/p' + BROADCAST（%s）" % _ul,
        (_WB % _uk) + " ENGINE=InnoDB INDEX DIRECTORY='/p' BROADCAST",
        "unsupported_unproven", "SQLGLOT_LIMIT",
        "sqlglot 本就不支持 INDEX DIRECTORY，两条路径均与主干一致",
        spans=0, ast="NoneType")

# ══════════════════════════════════════════════════════════════════════════
# H 组 —— TDSQL 规范符合性（key-part / 分区 / 表选项）
# ══════════════════════════════════════════════════════════════════════════
_HUKC = ", UNIQUE KEY `uk` (`sk`) COMMENT 'u'"
_H1 = "CREATE TABLE `t` (`id` INT, `sk` INT, %s) ENGINE=InnoDB"
_H2 = ("CREATE TABLE `t` (`id` INT, `sk` INT, `dt` DATETIME, PRIMARY KEY(`id`,`sk`)%s) "
       "ENGINE=InnoDB TDSQL_DISTRIBUTED BY HASH(`sk`) %s")
_H3 = ("CREATE TABLE `t` (`id` INT, `sk` INT, PRIMARY KEY(`id`,`sk`)%s) "
       "%s TDSQL_DISTRIBUTED BY HASH(`sk`)")
_H4 = "CREATE TABLE `t` (`id` INT, `sk` INT, PRIMARY KEY(`id`,`sk`)%s) %s"


def _hu(kp):
    return _H1 % ("UNIQUE KEY `uk` %s COMMENT 'x'" % kp)


for lbl, kp in [("空清单 ()", "()"), ("只有逗号 (,)", "(,)"), ("前导逗号 (,id)", "(,`id`)"),
                ("尾随逗号 (id,)", "(`id`,)"), ("连续逗号 (id,,sk)", "(`id`,,`sk`)"),
                ("字符串键 ('id')", "('id')"), ("数字键 (123)", "(123)"),
                ("函数键 (lower(id))", "(lower(`id`))"), ("表达式键 (id+1)", "(`id`+1)"),
                ("前缀长度非数字", "(`id`('x'))"), ("前缀括号未闭合", "(`id`(10)")]:
    add("H1", lbl, _hu(kp), "neg", "OFFICIAL", "key_part 必须是标识符[(正整数)][ASC|DESC]")
for lbl, kp in [("裸列名 (id)", "(id)"), ("反引号列 (`id`)", "(`id`)"),
                ("多列 (`id`,`sk`)", "(`id`,`sk`)"), ("前缀索引 (`id`(10))", "(`id`(10))"),
                ("前缀+多列", "(`id`(10),`sk`)")]:
    add("H2", lbl, _hu(kp), "pos", "OFFICIAL", "官方合法 key_part 必须恢复")
for lbl, kp in [("ASC (`id` ASC)", "(`id` ASC)"), ("DESC (`id` DESC)", "(`id` DESC)"),
                ("前缀+DESC+多列", "(`id`(10) DESC,`sk`)")]:
    add("H2b", lbl, _hu(kp), "pos", "OFFICIAL",
        "官方 key_part 含 [ASC|DESC]；sqlglot 30.x 对其 ParseError，由辅助掩码绕开")
for lbl, pt in [("裸 PARTITION BY", "PARTITION BY"),
                ("PARTITION BY DEFAULT", "PARTITION BY DEFAULT"),
                ("方法为字符串 'HASH'", "PARTITION BY 'HASH'(`sk`)"),
                ("空括号 HASH()", "PARTITION BY HASH()"),
                ("未闭合 HASH(`sk`", "PARTITION BY HASH(`sk`"),
                ("合法分区后尾随垃圾", "PARTITION BY HASH(`sk`) GARBAGE"),
                ("分区体内第二个方言声明",
                 "PARTITION BY LIST (`sk`) (PARTITION p1 VALUES IN (1) BROADCAST)"),
                ("分区体内藏分号",
                 "PARTITION BY LIST (`sk`) (PARTITION p1 VALUES IN (1); )")]:
    add("H3", lbl + " 带UK", _H2 % (_HUKC, pt), "neg", "OFFICIAL", "非法分区子句失败关闭")
    add("H3", lbl + " 无UK", _H2 % ("", pt), "neg", "OFFICIAL", "非法分区子句失败关闭")
for lbl, pt in [("RANGE+分区定义表",
                 "PARTITION BY RANGE (YEAR(`dt`)) (PARTITION p1 VALUES LESS THAN (2026), "
                 "PARTITION p2 VALUES LESS THAN (2027))"),
                ("LIST+分区定义表+partition ENGINE",
                 "PARTITION BY LIST (`sk`) (PARTITION p1 VALUES IN (1) ENGINE = InnoDB)"),
                ("LIST+VALUES IN 多值",
                 "PARTITION BY LIST (`sk`) (PARTITION p1 VALUES IN (1,2), PARTITION p2 VALUES IN (3,4))")]:
    add("H4", lbl + " 带UK", _H2 % (_HUKC, pt), "pos", "OFFICIAL", "官方二级分区 Range/List 必须恢复")
    add("H4", lbl + " 无UK", _H2 % ("", pt), "pos", "OFFICIAL", "官方二级分区 Range/List 必须恢复")
for lbl, pt in [("RANGE+MAXVALUE 兜底分区",
                 "PARTITION BY RANGE (`sk`) (PARTITION p1 VALUES LESS THAN (10), "
                 "PARTITION pm VALUES LESS THAN MAXVALUE)")]:
    add("H4c", lbl + " 带UK", _H2 % (_HUKC, pt), "pos_known", "SQLGLOT_LIMIT",
        "KFN-1（用户 2026-08-26 批准）：sqlglot 30.x 对 MAXVALUE ParseError，语料/生产 0 例")
    add("H4c", lbl + " 无UK", _H2 % ("", pt), "pos_known", "SQLGLOT_LIMIT",
        "KFN-1（用户 2026-08-26 批准）")
for lbl, pt in [("HASH+PARTITIONS n", "PARTITION BY HASH(`sk`) PARTITIONS 4"),
                ("LINEAR HASH", "PARTITION BY LINEAR HASH(`sk`)"),
                ("KEY(col)", "PARTITION BY KEY(`sk`)"),
                ("RANGE COLUMNS", "PARTITION BY RANGE COLUMNS(`sk`) (PARTITION p1 VALUES LESS THAN (10))")]:
    add("H4b", lbl + " 带UK", _H2 % (_HUKC, pt), "neg", "OFFICIAL",
        "官方二级分区只列 Range 与 List，其余保守失败关闭")
    add("H4b", lbl + " 无UK", _H2 % ("", pt), "neg", "OFFICIAL",
        "官方二级分区只列 Range 与 List，其余保守失败关闭")
for lbl, opt in [("ENGINE=123", "ENGINE=123"), ("ROW_FORMAT=123", "ENGINE=InnoDB ROW_FORMAT=123"),
                 ("ROW_FORMAT='x'", "ENGINE=InnoDB ROW_FORMAT='x'"),
                 ("ROW_FORMAT=UNKNOWN", "ENGINE=InnoDB ROW_FORMAT=UNKNOWN"),
                 ("SHARDKEY=123", "ENGINE=InnoDB shardkey=123"),
                 ("SHARDKEY='sk'", "ENGINE=InnoDB shardkey='sk'"),
                 ("AUTO_INCREMENT=abc", "ENGINE=InnoDB AUTO_INCREMENT=abc"),
                 ("COMMENT=123", "ENGINE=InnoDB COMMENT=123"),
                 ("PACK_KEYS=7", "ENGINE=InnoDB PACK_KEYS=7"),
                 ("STATS_PERSISTENT='1'", "ENGINE=InnoDB STATS_PERSISTENT='1'"),
                 ("CHARSET=123", "ENGINE=InnoDB DEFAULT CHARSET=123")]:
    add("H5", lbl + " 带UK", _H3 % (_HUKC, opt), "neg", "OFFICIAL", "表选项值谓词按类型校验")
    add("H5", lbl + " 无UK", _H3 % ("", opt), "neg", "OFFICIAL", "表选项值谓词按类型校验")
for lbl, opt in [("ENGINE=InnoDB", "ENGINE=InnoDB"), ("ENGINE='InnoDB'", "ENGINE='InnoDB'"),
                 ("AUTO_INCREMENT=100", "ENGINE=InnoDB AUTO_INCREMENT=100"),
                 ("STATS_AUTO_RECALC=1", "ENGINE=InnoDB STATS_AUTO_RECALC=1"),
                 ("STATS_SAMPLE_PAGES=8", "ENGINE=InnoDB STATS_SAMPLE_PAGES=8"),
                 ("生产同款全套", "ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin COMMENT='表'")]:
    add("H6", lbl + " 带UK", _H3 % (_HUKC, opt), "pos", "OFFICIAL", "官方/语料实证的合法取值必须恢复")
for lbl, opt in [("shardkey=sk", "ENGINE=InnoDB shardkey=sk"),
                 ("shardkey=noshardkey_allset", "ENGINE=InnoDB shardkey=noshardkey_allset"),
                 ("多列 shardkey=(id,sk)", "ENGINE=InnoDB shardkey=(id,sk)")]:
    add("H6", lbl + " 带UK", _H4 % (_HUKC, opt), "pos", "TARGET_INSTANCE",
        "shardkey 本身即一级分布声明，不能再拼 TDSQL_DISTRIBUTED")
for lbl, opt in [("ROW_FORMAT=DYNAMIC", "ENGINE=InnoDB ROW_FORMAT=DYNAMIC"),
                 ("ROW_FORMAT=DEFAULT", "ENGINE=InnoDB ROW_FORMAT=DEFAULT"),
                 ("ROW_FORMAT=FIXED", "ENGINE=InnoDB ROW_FORMAT=FIXED"),
                 ("ROW_FORMAT=COMPRESSED", "ENGINE=InnoDB ROW_FORMAT=COMPRESSED"),
                 ("STATS_PERSISTENT=1", "ENGINE=InnoDB STATS_PERSISTENT=1"),
                 ("STATS_PERSISTENT=DEFAULT", "ENGINE=InnoDB STATS_PERSISTENT=DEFAULT")]:
    add("H6", lbl + " 带UK", _H3 % (_HUKC, opt), "pos", "OFFICIAL",
        "官方建表页明示属 local_table_option（第十轮 BLOCK-J4 更正）")
for lbl, opt in [("PACK_KEYS=1", "ENGINE=InnoDB PACK_KEYS=1"),
                 ("PACK_KEYS=DEFAULT", "ENGINE=InnoDB PACK_KEYS=DEFAULT"),
                 ("CHECKSUM=1", "ENGINE=InnoDB CHECKSUM=1"),
                 ("KEY_BLOCK_SIZE=8", "ENGINE=InnoDB KEY_BLOCK_SIZE=8"),
                 ("AVG_ROW_LENGTH=100", "ENGINE=InnoDB AVG_ROW_LENGTH=100"),
                 ("MAX_ROWS=1000", "ENGINE=InnoDB MAX_ROWS=1000"),
                 ("MIN_ROWS=1", "ENGINE=InnoDB MIN_ROWS=1"),
                 ("DELAY_KEY_WRITE=1", "ENGINE=InnoDB DELAY_KEY_WRITE=1")]:
    add("H6b", lbl + " 带UK", _H3 % (_HUKC, opt), "unsupported_unproven", "CORPUS",
        "无 TDSQL / 目标实例证据 → 失败关闭，不冒充合法也不冒充非法")

# ══════════════════════════════════════════════════════════════════════════
# P 组 —— DEF-3：PRIMARY 索引 COMMENT（用户确认内网实际存在该形态）
# ══════════════════════════════════════════════════════════════════════════
_PUKC = ", UNIQUE KEY `uk` (`sk`) COMMENT 'u'"
_P = "CREATE TABLE `t` (`id` INT, `sk` INT%s) %s"
for lbl, defn, tail in [
    ("单列 PRIMARY COMMENT", ", PRIMARY KEY (`id`) COMMENT 'pk'", "ENGINE=InnoDB"),
    ("多列 PRIMARY COMMENT", ", PRIMARY KEY (`id`,`sk`) COMMENT 'pk'", "ENGINE=InnoDB"),
    ("PRIMARY USING BTREE COMMENT", ", PRIMARY KEY (`id`) USING BTREE COMMENT 'pk'", "ENGINE=InnoDB"),
    ("PRIMARY COMMENT + shardkey", ", PRIMARY KEY (`id`) COMMENT 'pk'", "ENGINE=InnoDB shardkey=id"),
    ("PRIMARY COMMENT + BROADCAST", ", PRIMARY KEY (`id`) COMMENT 'pk'", "ENGINE=InnoDB BROADCAST"),
    ("PRIMARY COMMENT + 方言 HASH", ", PRIMARY KEY (`id`,`sk`) COMMENT 'pk'",
     "ENGINE=InnoDB TDSQL_DISTRIBUTED BY HASH(`sk`)"),
    ("PRIMARY + UNIQUE 双 COMMENT", ", PRIMARY KEY (`id`) COMMENT 'pk'" + _PUKC, "ENGINE=InnoDB"),
    ("PRIMARY COMMENT + 普通索引 COMMENT",
     ", PRIMARY KEY (`id`) COMMENT 'pk', KEY `k` (`sk`) COMMENT 'idx'", "ENGINE=InnoDB"),
]:
    add("P1", lbl, _P % (defn, tail), "pos", "TARGET_INSTANCE",
        "DEF-3：用户确认内网实际存在 PRIMARY KEY … COMMENT 的表")
for lbl, defn in [
    ("PRIMARY 后带索引名", ", PRIMARY KEY `pk` (`id`) COMMENT 'x'"),
    ("PRIMARY 空键列", ", PRIMARY KEY () COMMENT 'x'"),
    ("PRIMARY COMMENT 非字符串", ", PRIMARY KEY (`id`) COMMENT `x`"),
    ("PRIMARY 重复 COMMENT", ", PRIMARY KEY (`id`) COMMENT 'a' COMMENT 'b'"),
    ("PRIMARY USING HASH", ", PRIMARY KEY (`id`) USING HASH COMMENT 'x'"),
    ("PRIMARY 前后置 USING", ", PRIMARY KEY USING BTREE (`id`) USING BTREE COMMENT 'x'"),
]:
    add("P2", lbl, _P % (defn, "ENGINE=InnoDB"), "neg", "OFFICIAL",
        "扩大恢复范围后的边界证明：非法近邻必须仍失败关闭")

# ══════════════════════════════════════════════════════════════════════════
# R11 组 —— 第十一轮复审报告 §4~§9、§11 的全部反例（BLOCK-11-07 第 5 条）
# ══════════════════════════════════════════════════════════════════════════
_RPK = ", PRIMARY KEY (`id`) COMMENT 'pk'"
_R1 = "CREATE TABLE `t` (`id` INT%s) ENGINE=InnoDB" % _RPK
_R2 = "CREATE TABLE `t` (`id` INT, `sk` INT%s) ENGINE=InnoDB" % _RPK

# —— BLOCK-11-01：MySQL 可执行注释 ——
add("R11-01", "/*!50100 PARTITION BY RANGE() 空方法参数 */",
    _R1 + "\n/*!50100 PARTITION BY RANGE() (PARTITION p0 VALUES LESS THAN (10)) */",
    "neg", "REVIEW_11", "可执行注释 payload 必须逐 token 验证，非法则整句失败关闭")
add("R11-01", "/*!50100 两条 PARTITION BY */",
    _R1 + "\n/*!50100 PARTITION BY LIST (`id`) (PARTITION p0 VALUES IN (1))"
          " PARTITION BY LIST (`id`) (PARTITION p1 VALUES IN (2)) */",
    "neg", "REVIEW_11", "payload 必须完整消费到末尾，多余 token 一律拒绝")
add("R11-01", "/*!50100 EVIL OPTION */", _R1 + "\n/*!50100 EVIL OPTION */",
    "neg", "REVIEW_11", "payload 首 token 必须是 PARTITION BY")
add("R11-01", "两个可执行注释", _R1 + "\n/*!50100 PARTITION BY LIST (`id`) (PARTITION p0 VALUES IN (1)) */"
    "\n/*!50100 PARTITION BY LIST (`id`) (PARTITION p1 VALUES IN (2)) */",
    "neg", "REVIEW_11", "至多一个可执行注释")
add("R11-01", "正例 /*!50100 PARTITION BY LIST 合法 */",
    _R1 + "\n/*!50100 PARTITION BY LIST (`id`) (PARTITION p0 VALUES IN (1) ENGINE = InnoDB) */",
    "pos", "OFFICIAL", "mysqldump 输出的官方二级分区形态必须恢复")
add("R11-01", "普通块注释内的伪分区（不得被当作可执行注释）",
    _R1 + "\n/* PARTITION BY RANGE() (PARTITION p0 VALUES LESS THAN (10)) */",
    "pos", "REVIEW_11", "普通注释仍保持不可见，不参与验证也不阻断恢复")

# —— BLOCK-11-02：表尾迁移图回环 ——
add("R11-02", "DIST → PARTITION → DIST",
    _R2 + " TDSQL_DISTRIBUTED BY HASH(`sk`) PARTITION BY RANGE(`id`) (PARTITION p0 VALUES LESS THAN (10))"
          " TDSQL_DISTRIBUTED BY HASH(`sk`)",
    "neg", "REVIEW_11", "一级分布至多一个；表尾图必须无环")
add("R11-02", "shardkey → PARTITION → DIST",
    _R2 + " shardkey=id PARTITION BY RANGE(`id`) (PARTITION p0 VALUES LESS THAN (10))"
          " TDSQL_DISTRIBUTED BY HASH(`sk`)",
    "neg", "REVIEW_11", "shardkey 与 TDSQL_DISTRIBUTED 同为一级分布，互斥")
add("R11-02", "PARTITION → DIST → PARTITION",
    _R2 + " PARTITION BY RANGE(`id`) (PARTITION p0 VALUES LESS THAN (10))"
          " TDSQL_DISTRIBUTED BY HASH(`sk`) PARTITION BY LIST(`id`) (PARTITION p1 VALUES IN (1))",
    "neg", "REVIEW_11", "二级分区至多一个")
add("R11-02", "正例 shardkey + PARTITION（官方二级分区原例）",
    _R2 + " shardkey=sk PARTITION BY RANGE(`id`) (PARTITION p0 VALUES LESS THAN (10))",
    "pos", "OFFICIAL", "LEGACY_PARTITION profile")
add("R11-02", "NEW_SECONDARY：DIST + TDSQL_PARTITION BY RANGE",
    _R2 + " TDSQL_DISTRIBUTED BY HASH(`sk`) TDSQL_PARTITION BY RANGE(`id`)"
          " (PARTITION p0 VALUES LESS THAN (10))",
    "unsupported_unproven", "CORPUS",
    "腾讯新版二级分区语法：无目标实例证据、语料 0 例 → 已具名登记为 NEW_SECONDARY profile 但不放行")
add("R11-02", "NEW_SECONDARY：shardkey + TDSQL_PARTITION BY LIST",
    _R2 + " shardkey=sk TDSQL_PARTITION BY LIST(`id`) (PARTITION p0 VALUES IN (1))",
    "unsupported_unproven", "CORPUS", "同上")
add("R11-02", "正例 PARTITION + DIST（官方原例 tb_sub_r_l）",
    _R2 + " PARTITION BY LIST(`id`) (PARTITION p0 VALUES IN (1)) TDSQL_DISTRIBUTED BY RANGE(`sk`)",
    "pos", "OFFICIAL", "LEGACY_PARTITION profile")

# —— BLOCK-11-03：广播哨兵混型 ——
add("R11-03", "哨兵 + PARTITION BY", _R1 + " shardkey=noshardkey_allset"
    " PARTITION BY LIST(`id`) (PARTITION p0 VALUES IN (1))",
    "neg", "REVIEW_11", "广播哨兵是终态原子，其后不得再有二级分区")
add("R11-03", "括号哨兵 shardkey=(noshardkey_allset)",
    _R1 + " shardkey=(noshardkey_allset)", "neg", "REVIEW_11",
    "哨兵只接受裸形态，括号形态无证据")
add("R11-03", "混合 shardkey=(noshardkey_allset,id)",
    _R1 + " shardkey=(noshardkey_allset,id)", "neg", "REVIEW_11",
    "哨兵不得与普通分片键混列，否则 R054/R077 边界可被伪造")
add("R11-03", "正例 裸哨兵 shardkey=noshardkey_allset",
    _R1 + " shardkey=noshardkey_allset", "pos", "TARGET_INSTANCE",
    "用户冻结：目标实例广播表哨兵形态")
add("R11-03", "BROADCAST 关键字 + shardkey（ADJ-6 表征）",
    _R1 + " shardkey=id BROADCAST", "characterization", "USER_DECISION",
    "ADJ-6：用户冻结的表征行为，不代表 TDSQL 合法", expect_pos=True)

# —— BLOCK-11-06：列属性 COLUMN_FORMAT / ENGINE_ATTRIBUTE / STORAGE ——
add("R11-06", "COLUMN_FORMAT DYNAMIC",
    "CREATE TABLE `t` (`id` INT COLUMN_FORMAT DYNAMIC%s) ENGINE=InnoDB" % _RPK,
    "pos", "OFFICIAL", "官方列属性；sqlglot 不认 → 作辅助掩码剥离后恢复")
add("R11-06", "ENGINE_ATTRIBUTE='x'",
    "CREATE TABLE `t` (`id` INT ENGINE_ATTRIBUTE='x'%s) ENGINE=InnoDB" % _RPK,
    "pos", "OFFICIAL", "同上")
add("R11-06", "SECONDARY_ENGINE_ATTRIBUTE='x'",
    "CREATE TABLE `t` (`id` INT SECONDARY_ENGINE_ATTRIBUTE='x'%s) ENGINE=InnoDB" % _RPK,
    "unsupported_unproven", "CORPUS",
    "腾讯官方建表页列级清单未列出（与列级 STORAGE 同处置）；语料 0 例 → 失败关闭")
add("R11-06", "列级 STORAGE DISK（NDB 专属，非 InnoDB 官方枚举）",
    "CREATE TABLE `t` (`id` INT STORAGE DISK%s) ENGINE=InnoDB" % _RPK,
    "unsupported_unproven", "CORPUS", "无 TDSQL/目标实例证据，语料 0 例 → 失败关闭")
add("R11-06", "COLUMN_FORMAT 非法取值 COLUMN_FORMAT=1",
    "CREATE TABLE `t` (`id` INT COLUMN_FORMAT=1%s) ENGINE=InnoDB" % _RPK,
    "neg", "REVIEW_11", "官方枚举只有 FIXED/DYNAMIC/DEFAULT，且不带等号")

# —— MAJOR-11-01：FULLTEXT / SPATIAL 裸形态 ——
add("R11-M1", "FULLTEXT KEY `f` (`a`)",
    "CREATE TABLE `t` (`id` INT, `a` VARCHAR(20), FULLTEXT KEY `f` (`a`)%s) ENGINE=InnoDB" % _RPK,
    "pos", "OFFICIAL", "带 KEY 的形态")
add("R11-M1", "FULLTEXT INDEX `f` (`a`)",
    "CREATE TABLE `t` (`id` INT, `a` VARCHAR(20), FULLTEXT INDEX `f` (`a`)%s) ENGINE=InnoDB" % _RPK,
    "pos", "OFFICIAL", "带 INDEX 的形态")
add("R11-M1", "FULLTEXT (`a`)（省略 KEY/INDEX）",
    "CREATE TABLE `t` (`id` INT, `a` VARCHAR(20), FULLTEXT (`a`)%s) ENGINE=InnoDB" % _RPK,
    "pos", "OFFICIAL", "官方语法 KEY/INDEX 可省略；入口判据必须与消费器同源")
add("R11-M1", "FULLTEXT `f` (`a`)（有名无 KEY/INDEX）",
    "CREATE TABLE `t` (`id` INT, `a` VARCHAR(20), FULLTEXT `f` (`a`)%s) ENGINE=InnoDB" % _RPK,
    "pos", "OFFICIAL", "同上")
add("R11-M1", "SPATIAL KEY `s` (`g`)",
    "CREATE TABLE `t` (`id` INT, `g` GEOMETRY NOT NULL, SPATIAL KEY `s` (`g`)%s) ENGINE=InnoDB" % _RPK,
    "pos", "OFFICIAL", "SPATIAL 索引按 NORMAL 处理（用户冻结决策 SPATIAL→NORMAL）")
add("R11-M1", "SPATIAL (`g`)（省略 KEY/INDEX）",
    "CREATE TABLE `t` (`id` INT, `g` GEOMETRY NOT NULL, SPATIAL (`g`)%s) ENGINE=InnoDB" % _RPK,
    "pos", "OFFICIAL", "同上")
add("R11-M1", "FULLTEXT 缺括号（非法）",
    "CREATE TABLE `t` (`id` INT, `a` VARCHAR(20), FULLTEXT `f`%s) ENGINE=InnoDB" % _RPK,
    "neg", "REVIEW_11", "缺键列列表必须失败关闭")
add("R11-M1", "列名恰为 `fulltext`（反向鉴别：不得误当索引）",
    "CREATE TABLE `t` (`id` INT, `fulltext` VARCHAR(20)%s) ENGINE=InnoDB" % _RPK,
    "pos", "REVIEW_11", "反引号标识符必须仍走列定义消费器")
add("R11-M1", "列名恰为 `spatial`（反向鉴别）",
    "CREATE TABLE `t` (`id` INT, `spatial` VARCHAR(20)%s) ENGINE=InnoDB" % _RPK,
    "pos", "REVIEW_11", "同上")

# ══════════════════════════════════════════════════════════════════════════
# TY 组 —— TDSQL 官方数据类型的双向闭合矩阵（BLOCK-11-04）
#          模板固定为"一列待测类型 + 一个必须恢复的 UNIQUE COMMENT"
# ══════════════════════════════════════════════════════════════════════════
_TY = "CREATE TABLE `t` (`c` %s, `sk` INT, UNIQUE KEY `uk` (`sk`) COMMENT 'u') ENGINE=InnoDB"
# KFN-3：sqlglot 30.14.0 / 29.0.0 / 30.17.0 三版一致 ParseError 的官方类型。
# 实测：去掉 UNIQUE COMMENT 的普通建表在**修复前后**都报 E999，行为完全一致，
# 本次修复既不改善也不恶化，仅登记能力边界。
_TY_KFN3 = ("CHAR(10) BINARY", "POINT", "LINESTRING", "POLYGON",
            "MULTIPOINT", "MULTILINESTRING", "MULTIPOLYGON", "GEOMETRYCOLLECTION")
_TY_LEGAL = [
    "TINYINT", "TINYINT(4)", "TINYINT UNSIGNED", "TINYINT(3) UNSIGNED ZEROFILL",
    "SMALLINT", "SMALLINT(6)", "MEDIUMINT", "MEDIUMINT(9)",
    "INT", "INT(11)", "INT UNSIGNED", "INT(10) UNSIGNED ZEROFILL", "INTEGER", "INTEGER(11)",
    "BIGINT", "BIGINT(20)", "BIGINT UNSIGNED",
    "DECIMAL", "DECIMAL(10)", "DECIMAL(10,2)", "DECIMAL(65,30)", "DECIMAL(10,2) UNSIGNED",
    "NUMERIC(10,2)", "FIXED(10,2)",
    "FLOAT", "FLOAT(10,2)", "REAL", "REAL(10,2)", "DOUBLE", "DOUBLE(10,2)",
    "DOUBLE PRECISION", "DOUBLE PRECISION(10,2)",
    "CHAR", "CHAR(0)", "CHAR(1)", "CHAR(255)", "CHAR(10) BINARY",
    "VARCHAR(0)", "VARCHAR(255)", "VARCHAR(65535)",
    "BINARY", "BINARY(16)", "VARBINARY(255)",
    "TINYTEXT", "TEXT", "TEXT(1000)", "MEDIUMTEXT", "LONGTEXT",
    "TINYBLOB", "BLOB", "BLOB(1000)", "MEDIUMBLOB", "LONGBLOB",
    "ENUM('a','b')", "SET('a','b')",
    "DATE", "YEAR", "YEAR(4)", "TIME", "TIME(6)", "DATETIME", "DATETIME(3)",
    "TIMESTAMP", "TIMESTAMP(6)",
    "BIT", "BIT(1)", "BIT(64)", "BOOL", "BOOLEAN", "JSON",
    "GEOMETRY", "POINT", "LINESTRING", "POLYGON",
    "MULTIPOINT", "MULTILINESTRING", "MULTIPOLYGON", "GEOMETRYCOLLECTION",
]
_TY_ILLEGAL = [
    ("DECIMAL(1,2)", "scale 不得大于 precision"),
    ("DECIMAL(66,0)", "precision 上限 65"),
    ("DECIMAL(65,31)", "scale 上限 30"),
    ("BIT(0)", "BIT 下限 1"),
    ("BIT(65)", "BIT 上限 64"),
    ("CHAR(256)", "CHAR 上限 255"),
    ("VARCHAR(65536)", "VARCHAR 声明长度上限 65535"),
    ("VARCHAR", "VARCHAR 长度必填"),
    ("YEAR(999)", "YEAR 只接受省略或 4"),
    ("YEAR(2)", "MySQL 5.7 起 YEAR(2) 已移除"),
    ("TIME(7)", "fsp 上限 6"),
    ("DATETIME(7)", "fsp 上限 6"),
    ("TIMESTAMP(7)", "fsp 上限 6"),
    ("ENUM", "ENUM 必须带括号值表"),
    ("SET", "SET 必须带括号值表"),
    ("ENUM()", "至少一个字符串值"),
    ("SET()", "至少一个字符串值"),
    ("ENUM(1,2)", "值必须是字符串字面量"),
    ("DATE UNSIGNED", "时间族不接受数值属性"),
    ("VARCHAR(10) UNSIGNED", "字符族不接受数值属性"),
    ("JSON BINARY", "JSON 不接受任何类型属性"),
    ("INT BINARY", "数值族不接受 BINARY"),
    ("TEXT ZEROFILL", "字符族不接受 ZEROFILL"),
    ("NOSUCHTYPE", "未登记类型名"),
    ("NOSUCHTYPE(3)", "未登记类型名"),
]
for _t in _TY_LEGAL:
    if _t in _TY_KFN3:
        add("TY-K", _t, _TY % _t, "pos_known", "SQLGLOT_LIMIT",
            "KFN-3：sqlglot 三版一致 ParseError，修复前后行为完全一致")
    else:
        add("TY-P", _t, _TY % _t, "pos", "OFFICIAL",
            "官方合法类型必须恢复；别名与展示属性在源侧规范化后与 AST 一致")
for _t, _why in _TY_ILLEGAL:
    add("TY-N", _t, _TY % _t, "neg", "REVIEW_11", _why)
# DEFAULT / ON UPDATE 时间函数精度
for _d, _k, _why in [
    ("DATETIME DEFAULT CURRENT_TIMESTAMP", "pos", "官方合法"),
    ("DATETIME(6) DEFAULT CURRENT_TIMESTAMP(6)", "pos", "官方合法"),
    ("DATETIME DEFAULT CURRENT_TIMESTAMP(7)", "neg", "时间函数精度上限 6"),
    ("TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP", "pos", "官方合法"),
    ("TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP(7)", "neg", "同上"),
]:
    add("TY-D" if _k == "pos" else "TY-N", _d, _TY % _d, _k,
        "OFFICIAL" if _k == "pos" else "REVIEW_11", _why)

# ══════════════════════════════════════════════════════════════════════════
# M 组 —— 候选 AST 结构守恒门禁的反向鉴别（BLOCK-11-05 白盒变异测试）
#         每条 (源 SQL, 正确候选 SQL, [变异候选 SQL...])
#         正确候选必须过门禁；每个变异候选必须被门禁拒绝。
# ══════════════════════════════════════════════════════════════════════════
MUTATIONS = []


def mut(title, src, good, muts):
    cid = "M-%02d" % (len(MUTATIONS) + 1)
    MUTATIONS.append({"cid": cid, "title": title, "src": src, "good": good, "muts": muts})


mut("UNIQUE 注释恢复",
    "CREATE TABLE `t` (`id` INT NOT NULL DEFAULT 7, `sk` VARCHAR(32), "
    "UNIQUE KEY `uk` (`sk`(8)) USING BTREE COMMENT 'u') ENGINE=InnoDB",
    "CREATE TABLE `t` (`id` INT NOT NULL DEFAULT 7, `sk` VARCHAR(32), "
    "UNIQUE KEY `uk` (`sk`(8)) USING BTREE) ENGINE=InnoDB",
    [("丢 NOT NULL", "CREATE TABLE `t` (`id` INT DEFAULT 7, `sk` VARCHAR(32), UNIQUE KEY `uk` (`sk`(8)) USING BTREE) ENGINE=InnoDB"),
     ("丢 DEFAULT", "CREATE TABLE `t` (`id` INT NOT NULL, `sk` VARCHAR(32), UNIQUE KEY `uk` (`sk`(8)) USING BTREE) ENGINE=InnoDB"),
     ("改列类型", "CREATE TABLE `t` (`id` BIGINT NOT NULL DEFAULT 7, `sk` VARCHAR(32), UNIQUE KEY `uk` (`sk`(8)) USING BTREE) ENGINE=InnoDB"),
     ("改类型长度", "CREATE TABLE `t` (`id` INT NOT NULL DEFAULT 7, `sk` VARCHAR(64), UNIQUE KEY `uk` (`sk`(8)) USING BTREE) ENGINE=InnoDB"),
     ("改列名", "CREATE TABLE `t` (`id` INT NOT NULL DEFAULT 7, `zz` VARCHAR(32), UNIQUE KEY `uk` (`sk`(8)) USING BTREE) ENGINE=InnoDB"),
     ("UNIQUE→KEY", "CREATE TABLE `t` (`id` INT NOT NULL DEFAULT 7, `sk` VARCHAR(32), KEY `uk` (`sk`(8)) USING BTREE) ENGINE=InnoDB"),
     ("UNIQUE→PRIMARY", "CREATE TABLE `t` (`id` INT NOT NULL DEFAULT 7, `sk` VARCHAR(32), PRIMARY KEY (`sk`)) ENGINE=InnoDB"),
     ("改索引名", "CREATE TABLE `t` (`id` INT NOT NULL DEFAULT 7, `sk` VARCHAR(32), UNIQUE KEY `vv` (`sk`(8)) USING BTREE) ENGINE=InnoDB"),
     ("改键列", "CREATE TABLE `t` (`id` INT NOT NULL DEFAULT 7, `sk` VARCHAR(32), UNIQUE KEY `uk` (`id`) USING BTREE) ENGINE=InnoDB"),
     ("丢前缀长度", "CREATE TABLE `t` (`id` INT NOT NULL DEFAULT 7, `sk` VARCHAR(32), UNIQUE KEY `uk` (`sk`) USING BTREE) ENGINE=InnoDB"),
     ("丢 USING", "CREATE TABLE `t` (`id` INT NOT NULL DEFAULT 7, `sk` VARCHAR(32), UNIQUE KEY `uk` (`sk`(8))) ENGINE=InnoDB"),
     ("少一个定义项", "CREATE TABLE `t` (`id` INT NOT NULL DEFAULT 7, UNIQUE KEY `uk` (`sk`(8)) USING BTREE) ENGINE=InnoDB"),
     ("多一个定义项", "CREATE TABLE `t` (`id` INT NOT NULL DEFAULT 7, `sk` VARCHAR(32), `x` INT, UNIQUE KEY `uk` (`sk`(8)) USING BTREE) ENGINE=InnoDB"),
     ("换表名", "CREATE TABLE `other` (`id` INT NOT NULL DEFAULT 7, `sk` VARCHAR(32), UNIQUE KEY `uk` (`sk`(8)) USING BTREE) ENGINE=InnoDB"),
     ("定义项换序", "CREATE TABLE `t` (`sk` VARCHAR(32), `id` INT NOT NULL DEFAULT 7, UNIQUE KEY `uk` (`sk`(8)) USING BTREE) ENGINE=InnoDB")])
mut("PRIMARY 注释恢复（后置 USING）",
    "CREATE TABLE `t` (`id` INT, `sk` INT, PRIMARY KEY (`id`) USING BTREE COMMENT 'pk') ENGINE=InnoDB",
    "CREATE TABLE `t` (`id` INT, `sk` INT, PRIMARY KEY (`id`) USING BTREE) ENGINE=InnoDB",
    [("丢 USING（PRIMARY）", "CREATE TABLE `t` (`id` INT, `sk` INT, PRIMARY KEY (`id`)) ENGINE=InnoDB"),
     ("改主键列", "CREATE TABLE `t` (`id` INT, `sk` INT, PRIMARY KEY (`sk`) USING BTREE) ENGINE=InnoDB"),
     ("PRIMARY→UNIQUE", "CREATE TABLE `t` (`id` INT, `sk` INT, UNIQUE KEY `id` (`id`) USING BTREE) ENGINE=InnoDB"),
     ("主键多一列", "CREATE TABLE `t` (`id` INT, `sk` INT, PRIMARY KEY (`id`,`sk`) USING BTREE) ENGINE=InnoDB")])
mut("无 USING 的 PRIMARY：不得凭空多出 USING",
    "CREATE TABLE `t` (`id` INT, `sk` INT, PRIMARY KEY (`id`) COMMENT 'pk') ENGINE=InnoDB",
    "CREATE TABLE `t` (`id` INT, `sk` INT, PRIMARY KEY (`id`)) ENGINE=InnoDB",
    [("凭空 USING（PRIMARY）", "CREATE TABLE `t` (`id` INT, `sk` INT, PRIMARY KEY (`id`) USING BTREE) ENGINE=InnoDB")])
mut("无 USING 的 KEY：不得凭空多出 USING",
    "CREATE TABLE `t` (`id` INT, `sk` INT, UNIQUE KEY `uk` (`id`) COMMENT 'u', KEY `k` (`sk`)) ENGINE=InnoDB",
    "CREATE TABLE `t` (`id` INT, `sk` INT, UNIQUE KEY `uk` (`id`), KEY `k` (`sk`)) ENGINE=InnoDB",
    [("凭空 USING（KEY 后置）", "CREATE TABLE `t` (`id` INT, `sk` INT, UNIQUE KEY `uk` (`id`), KEY `k` (`sk`) USING BTREE) ENGINE=InnoDB"),
     ("凭空 USING（KEY 前置）", "CREATE TABLE `t` (`id` INT, `sk` INT, UNIQUE KEY `uk` (`id`), KEY `k` USING BTREE (`sk`)) ENGINE=InnoDB")])
mut("二级分区保真",
    "CREATE TABLE `t` (`id` INT, `sk` INT, UNIQUE KEY `uk` (`id`) COMMENT 'u') "
    "ENGINE=InnoDB shardkey=sk PARTITION BY RANGE(`sk`) (PARTITION p0 VALUES LESS THAN (10))",
    "CREATE TABLE `t` (`id` INT, `sk` INT, UNIQUE KEY `uk` (`id`)) "
    "ENGINE=InnoDB PARTITION BY RANGE(`sk`) (PARTITION p0 VALUES LESS THAN (10))",
    [("分区被抹掉", "CREATE TABLE `t` (`id` INT, `sk` INT, UNIQUE KEY `uk` (`id`)) ENGINE=InnoDB")])

# 模糊测试参数（不变量：长度恒等 + 差异全部落在 span 内 + 不抛异常）
FUZZ = {"seed": 20260826, "n": 6000}

# ══════════════════════════════════════════════════════════════════════════
# R12-EC 组 —— 可执行注释：位置 × 主表尾 atom 的笛卡尔积（BLOCK-12-01）
#            期望值直接由 capability profile 表推导，不是抄实测
# ══════════════════════════════════════════════════════════════════════════
_EC = "/*!50100 PARTITION BY RANGE(`id`) (PARTITION p9 VALUES LESS THAN (99)) */"
_EC_HEAD = "CREATE TABLE `t` (`id` INT, `sk` INT, PRIMARY KEY (`id`) COMMENT 'p') "
# (标签, 主表尾, 注释落在表尾时的期望, 理由)
_EC_TAILS = [
    ("无表选项", "", "pos", "atom 序列 = [PARTITION] → LEGACY_PARTITION"),
    ("ENGINE", "ENGINE=InnoDB", "pos", "atom 序列 = [LOCAL, PARTITION] → LEGACY_PARTITION"),
    ("shardkey", "ENGINE=InnoDB shardkey=sk", "pos",
     "atom = [LOCAL, HASH_SHARDKEY, PARTITION] → LEGACY_PARTITION（官方二级分区原例）"),
    ("广播哨兵", "ENGINE=InnoDB shardkey=noshardkey_allset", "neg",
     "atom = [LOCAL, BROADCAST_SENTINEL, PARTITION] → 无 profile，哨兵是终态"),
    ("BROADCAST 关键字", "ENGINE=InnoDB BROADCAST", "neg",
     "atom = [LOCAL, BROADCAST_KEYWORD, PARTITION] → 无 profile"),
    ("TDSQL_DISTRIBUTED", "ENGINE=InnoDB TDSQL_DISTRIBUTED BY HASH(`sk`)", "pos",
     "atom = [LOCAL, DIST, PARTITION] → TARGET_CURRENT"),
    ("主流已有 PARTITION",
     "ENGINE=InnoDB PARTITION BY LIST(`sk`) (PARTITION p0 VALUES IN (1))", "neg",
     "atom = [LOCAL, PARTITION, PARTITION] → 二级分区计数 > 1"),
]
for _tl, _tail, _k, _why in _EC_TAILS:
    add("R12-EC", "尾部位置 × %s" % _tl,
        _EC_HEAD + "%s\n%s" % (_tail, _EC), _k, "REVIEW_12",
        "可执行注释按 owner_idx 并入表尾 atom 流：" + _why)
    add("R12-EC", "CREATE 之前 × %s" % _tl,
        "%s\n%s%s" % (_EC, _EC_HEAD, _tail), "neg", "REVIEW_12",
        "位置越界：owner_idx ≤ close_idx（挂在建表头）→ 失败关闭")
    add("R12-EC", "列定义内部 × %s" % _tl,
        "CREATE TABLE `t` (`id` INT %s, `sk` INT, PRIMARY KEY (`id`) COMMENT 'p') %s"
        % (_EC, _tail), "neg", "REVIEW_12",
        "位置越界：owner_idx ≤ close_idx（挂在定义列表内部）→ 失败关闭")
add("R12-EC", "两个可执行注释",
    _EC_HEAD + "ENGINE=InnoDB\n" + _EC + "\n" + _EC, "neg", "REVIEW_12",
    "至多一个可执行注释")
add("R12-EC", "payload 非分区内容（EVIL OPTION）",
    _EC_HEAD + "ENGINE=InnoDB\n/*!50100 EVIL OPTION */", "neg", "REVIEW_12",
    "payload 首 token 必须是 PARTITION BY")
add("R12-EC", "payload 分区方法空参 RANGE()",
    _EC_HEAD + "ENGINE=InnoDB\n/*!50100 PARTITION BY RANGE() (PARTITION p0 VALUES LESS THAN (10)) */",
    "neg", "REVIEW_12", "payload 必须完整通过二级分区消费器")
add("R12-EC", "payload 内两条 PARTITION BY",
    _EC_HEAD + "ENGINE=InnoDB\n/*!50100 PARTITION BY LIST (`id`) (PARTITION p0 VALUES IN (1))"
    " PARTITION BY LIST (`id`) (PARTITION p1 VALUES IN (2)) */",
    "neg", "REVIEW_12", "payload 必须被消费到末尾，多余 token 一律拒绝")
add("R12-EC", "普通块注释内的伪分区（不得被当作可执行注释）",
    _EC_HEAD + "ENGINE=InnoDB\n/* PARTITION BY RANGE(`id`) (PARTITION p0 VALUES LESS THAN (10)) */",
    "pos", "REVIEW_12", "普通注释保持不可见，既不参与验证也不阻断恢复")

# ══════════════════════════════════════════════════════════════════════════
# R12-SC 组 —— 语句终止符：数量 × 空白/注释/多语句（BLOCK-12-02）
#            全部走真实 `SQLParser.parse()`，断言最终 AST 与 E999
# ══════════════════════════════════════════════════════════════════════════
_SC = "CREATE TABLE `t` (`id` INT, `sk` INT, PRIMARY KEY (`id`) COMMENT 'p') ENGINE=InnoDB"
for _lbl, _suf, _k, _why in [
    ("无分号", "", "pos", "0 个终止符合法"),
    ("1 个分号", ";", "pos", "1 个终止符合法"),
    ("1 个分号 + 尾随空白", ";  \n", "pos", "空白不影响"),
    ("2 个分号", ";;", "neg", "至多一个终止符"),
    ("3 个分号", ";;;", "neg", "同上"),
    ("分号间有空白", "; ;", "neg", "同上"),
    ("分号间有换行", ";\n;", "neg", "同上"),
    ("分号后接第二条语句", "; CREATE TABLE `u` (`x` INT)", "neg", "多语句必须失败关闭"),
]:
    add("R12-SC", _lbl, _SC + _suf, _k, "REVIEW_12",
        "恢复链必须拿到未被 rstrip(';') 处理过的原串：" + _why)
for _lbl, _suf in [("分号后接行注释", "; -- tail"), ("分号后接块注释", "; /* tail */")]:
    add("R12-SC-K", _lbl, _SC + _suf, "pos_known", "SQLGLOT_LIMIT",
        "KFN-4：终止符后的普通注释是合法 MySQL，但三版 sqlglot 对整条语句一致 "
        "ParseError（掩码后得到 exp.Block，被守恒门禁拒绝）→ 失败关闭并具名登记")
add("R12-SC", "字符串字面量内的分号（不是终止符）",
    "CREATE TABLE `t` (`id` INT COMMENT 'a;b', `sk` INT, "
    "PRIMARY KEY (`id`) COMMENT 'p') ENGINE=InnoDB;", "pos", "REVIEW_12",
    "词法作用域内的分号不可见，不得被误当终止符")

# ══════════════════════════════════════════════════════════════════════════
# R12-TY 组 —— 官方类型产生式矩阵（BLOCK-12-03）
#            由产生式清单生成：下界 / 常规 / 上界 / 越界 / 别名 / 属性
# ══════════════════════════════════════════════════════════════════════════
_TY12 = "CREATE TABLE `t` (`c` %s, `sk` INT, UNIQUE KEY `uk` (`sk`) COMMENT 'u') ENGINE=InnoDB"
# (源拼写, 分类, 理由)
_TY12_CASES = [
    # FLOAT 的两条产生式：(p) 与 (M,D) —— Rev.M 把两者混成一条，双向失真
    ("FLOAT(0)", "pos", "FLOAT(p) 下界 p=0"),
    ("FLOAT(24)", "pos", "FLOAT(p) 单精度上界"),
    ("FLOAT(25)", "pos", "FLOAT(p) 转双精度下界"),
    ("FLOAT(53)", "pos", "FLOAT(p) 上界 p=53"),
    ("FLOAT(54)", "neg", "FLOAT(p) 越上界"),
    ("FLOAT(-1)", "neg", "FLOAT(p) 越下界"),
    ("FLOAT(10,2)", "pos", "FLOAT(M,D) 产生式"),
    ("FLOAT(256,2)", "neg", "FLOAT(M,D) M 越界"),
    ("FLOAT(10,31)", "neg", "FLOAT(M,D) D 越界"),
    ("FLOAT(1,2)", "neg", "scale 不得大于 precision"),
    # DECIMAL 系列的三条产生式与别名
    ("DEC(10,2)", "pos", "DEC 是 DECIMAL 官方同义词"),
    ("DEC(10)", "pos", "DECIMAL(M) 产生式"),
    ("DEC", "pos", "DECIMAL 无参产生式"),
    ("NUMERIC(65,30)", "pos", "DECIMAL 上界"),
    ("NUMERIC(66,0)", "neg", "precision 越界"),
    ("FIXED(10,31)", "neg", "scale 越界"),
    # 字符族别名
    ("NCHAR(10)", "pos", "NCHAR 是 CHAR 官方别名"),
    ("NVARCHAR(10)", "pos", "NVARCHAR 是 VARCHAR 官方别名"),
    ("CHARACTER(10)", "pos", "CHARACTER 是 CHAR 官方别名"),
    ("CHARACTER VARYING(10)", "pos", "CHARACTER VARYING 是 VARCHAR 官方别名"),
    ("NCHAR(256)", "neg", "别名沿用 CHAR 的 0..255 边界"),
    ("NVARCHAR", "neg", "VARCHAR 系列长度必填"),
    ("SERIAL", "pos_known", "SERIAL 隐含 UNIQUE/NOT NULL/AUTO_INCREMENT，本期 KFN-5 阻断"),
    ("SERIAL(10)", "neg", "SERIAL 不带参数"),
    # ENUM / SET 成员数上界
    ("SET(%s)" % ",".join("'m%d'" % i for i in range(64)), "pos", "SET 成员数上界 64"),
    ("SET(%s)" % ",".join("'m%d'" % i for i in range(65)), "neg", "SET 成员数越界 65"),
    ("ENUM('a')", "pos", "ENUM 下界 1 个成员"),
    # 腾讯官方列出的数值字面量
    ("INT DEFAULT .2", "pos", "官方字面量 `.2`，规范成 0.2 与候选一致"),
    ("DECIMAL(10,2) DEFAULT -.5", "pos", "带符号的官方字面量"),
    ("DECIMAL(10,2) DEFAULT 1e3", "pos", "科学计数法"),
    ("INT DEFAULT 0x1F", "pos", "hex 字面量"),
    ("BIT(8) DEFAULT b'101'", "pos", "bit 字面量"),
    ("INT DEFAULT TRUE", "pos", "布尔字面量"),
    ("INT DEFAULT .", "neg", "残缺小数点"),
]
for _t, _k, _why in _TY12_CASES:
    _extra = {}
    if _t == "SERIAL":
        _extra = {"kfn": "KFN-5-SERIAL", "e999": True}
    add("R12-TY", _t if len(_t) <= 40 else _t[:37] + "…", _TY12 % _t, _k,
        "OFFICIAL" if _k in ("pos", "pos_known") else "REVIEW_12", _why, **_extra)
# 官方合法但锁定版 sqlglot 解析不了 → KFN-A，必须失败关闭且**具名登记**
for _t, _why in [
    ("INT SIGNED", "SIGNED 属性：三版 sqlglot 一致 ParseError"),
    ("BIGINT SIGNED", "同上"),
    ("VARCHAR(20) BINARY", "字符族 BINARY 属性：三版一致 ParseError"),
    ("TEXT BINARY", "同上"),
    ("NATIONAL CHAR(10)", "NATIONAL 形态：三版一致 ParseError"),
    ("NATIONAL VARCHAR(10)", "同上"),
]:
    add("R12-TY-K", _t, _TY12 % _t, "pos_known", "SQLGLOT_LIMIT",
        "KFN-4：" + _why + "；已在类型表具名登记，不藏在普通 plan=False 里")

# ══════════════════════════════════════════════════════════════════════════
# R12-CN 组 —— 官方 `[CONSTRAINT [symbol]] PRIMARY KEY` （MAJOR-12-01）
# ══════════════════════════════════════════════════════════════════════════
_CN = "CREATE TABLE `t` (`id` INT, `sk` INT, CONSTRAINT `pk` PRIMARY KEY (`id`)%s) %s"
for _lbl, _extra, _tail in [
    ("带名 PK + HASH 方言", "", "ENGINE=InnoDB TDSQL_DISTRIBUTED BY HASH(`sk`)"),
    ("带名 PK + BROADCAST", "", "ENGINE=InnoDB BROADCAST"),
    ("带名 PK + UNIQUE COMMENT", ", UNIQUE KEY `uk` (`sk`) COMMENT 'u'", "ENGINE=InnoDB"),
    ("带名 PK + 普通索引 COMMENT", ", KEY `k` (`sk`) COMMENT 'n', UNIQUE KEY `uk` (`sk`) COMMENT 'u'",
     "ENGINE=InnoDB"),
    ("带名 PK + USING BTREE + UNIQUE COMMENT",
     ", UNIQUE KEY `uk` (`sk`) COMMENT 'u'", "ENGINE=InnoDB"),
]:
    add("R12-CN", _lbl, _CN % (_extra, _tail), "pos", "OFFICIAL",
        "官方建表语法含 `[CONSTRAINT [symbol]] PRIMARY KEY`；候选侧须解包 exp.Constraint")
add("R12-CN", "带名 PK + 哨兵（无恢复目标，sqlglot 原生可解析）",
    "CREATE TABLE `t` (`id` INT, `sk` INT, CONSTRAINT `pk` PRIMARY KEY (`id`)) "
    "ENGINE=InnoDB shardkey=noshardkey_allset", "pos", "OFFICIAL",
    "哨兵不是掩码目标、语句里也没有索引 COMMENT → 无主目标即不进恢复链（既定设计）",
    needs_recovery=False)
add("R12-CN", "无名 CONSTRAINT PRIMARY KEY",
    "CREATE TABLE `t` (`id` INT, `sk` INT, CONSTRAINT PRIMARY KEY (`id`), "
    "UNIQUE KEY `uk` (`sk`) COMMENT 'u') ENGINE=InnoDB", "pos_known", "SQLGLOT_LIMIT",
    "KFN-4：官方允许省略 symbol，但三版 sqlglot 一致 ParseError → 失败关闭并具名登记")
add("R12-CN", "CONSTRAINT symbol UNIQUE（NG-10 冻结，不作恢复目标）",
    "CREATE TABLE `t` (`id` INT, `sk` INT, CONSTRAINT `uq` UNIQUE (`id`), "
    "UNIQUE KEY `uk` (`sk`) COMMENT 'u') ENGINE=InnoDB", "pos_known", "USER_DECISION",
    "Rev.P：NG-10/ADJ-11 冻结；KFN-6 覆盖恢复路径并保留 E999",
    kfn="KFN-6-CONSTRAINT-UNIQUE", e999=True)

# ══════════════════════════════════════════════════════════════════════════
# R14 组 —— 第十四轮：UNIQUE 隔离通道、全路径 KFN、legacy 零漂移
# ══════════════════════════════════════════════════════════════════════════
_R14_BASE = ("CREATE TABLE t (id INT NOT NULL, sk INT NOT NULL%s, c INT, d INT, "
             "PRIMARY KEY(id,sk)%s) ENGINE=InnoDB %s")
_R14_UQ_RULES_BASE = {"R005", "R028", "R029", "R036", "R037"}
add("R14-UQ", "列级合规 + 两个表级（后一违规）",
    _R14_BASE % (" UNIQUE", ", UNIQUE KEY u1(sk,c), UNIQUE KEY u2(c,d)", "shardkey=sk"),
    "pos", "REVIEW_14", "完整 UNIQUE 只进隔离通道；R054 必须命中 u2",
    needs_recovery=False, unique_names=["sk", "u1", "u2"],
    unique_columns=[["sk"], ["sk", "c"], ["c", "d"]], legacy_unique_count=0,
    unique_complete=True, rules_exact=_R14_UQ_RULES_BASE | {"R054"})
add("R14-UQ", "列级与表级全部含 shardkey",
    _R14_BASE % (" UNIQUE", ", UNIQUE KEY u1(sk,c), UNIQUE KEY u2(sk,d)", "shardkey=sk"),
    "pos", "REVIEW_14", "R054 双向反例：全部合规时不得命中",
    needs_recovery=False, unique_names=["sk", "u1", "u2"],
    unique_columns=[["sk"], ["sk", "c"], ["sk", "d"]], legacy_unique_count=0,
    unique_complete=True, rules_exact=_R14_UQ_RULES_BASE)
add("R14-UQ", "表级前缀索引只保留基列",
    _R14_BASE % ("", ", UNIQUE KEY u1(sk,c(10))", "shardkey=sk"),
    "pos", "REVIEW_14", "前缀长度不是列名的一部分",
    needs_recovery=False, unique_names=["u1"], unique_columns=[["sk", "c"]],
    legacy_unique_count=0, unique_complete=True, rules_exact=_R14_UQ_RULES_BASE)
add("R14-UQ", "重复列级 UNIQUE 不得伪装语义完整",
    _R14_BASE % (" UNIQUE UNIQUE", "", "shardkey=sk"),
    "fail_closed", "REVIEW_14", "sqlglot 原生接受该异常结构时也必须标记 incomplete 并产生 E999",
    unique_complete=False, legacy_unique_count=0,
    rules_exact=_R14_UQ_RULES_BASE | {"E999_SYNTAX_ERROR"})

_KFN_RULES_NATIVE = {"E999_SYNTAX_ERROR", "R005", "R028", "R029", "R036", "R037"}
_KFN_RULES_COMMAND = {"E999_SYNTAX_ERROR", "R005", "R028"}
_KFN_RULES_EXCEPT = {"E999_SYNTAX_ERROR", "R003", "R004", "R005", "R028"}
for _group, _label, _definition, _kfn in [
    ("R14-KFN-CU", "CONSTRAINT UNIQUE", "CONSTRAINT uq UNIQUE(c)", "KFN-6-CONSTRAINT-UNIQUE"),
    ("R14-KFN-SE", "SERIAL", "id SERIAL", "KFN-5-SERIAL"),
]:
    if _label == "CONSTRAINT UNIQUE":
        _head = "id INT NOT NULL, sk INT NOT NULL, c INT, PRIMARY KEY(id,sk), " + _definition
    else:
        _head = _definition + ", sk INT NOT NULL, c INT, PRIMARY KEY(id,sk)"
    add(_group, _label + " / native Create",
        "CREATE TABLE t (%s) ENGINE=InnoDB shardkey=sk" % _head,
        "pos_known", "REVIEW_14", "原生成功路径也必须被 source preflight 阻断",
        kfn=_kfn, e999=True, plan_required=False, rules_exact=_KFN_RULES_NATIVE)
    add(_group, _label + " / dialect Command",
        "CREATE TABLE t (%s) ENGINE=InnoDB TDSQL_DISTRIBUTED BY HASH(sk)" % _head,
        "pos_known", "REVIEW_14", "Command 路径不得停在无 E999 的空结构",
        kfn=_kfn, e999=True, rules_exact=_KFN_RULES_COMMAND)
    add(_group, _label + " / UNIQUE COMMENT ParseError",
        "CREATE TABLE t (%s, UNIQUE KEY u2(sk) COMMENT 'x') ENGINE=InnoDB shardkey=sk" % _head,
        "pos_known", "REVIEW_14", "except 恢复路径必须被同一 KFN 阻断",
        kfn=_kfn, e999=True, rules_exact=_KFN_RULES_EXCEPT)

for _label, _frag, _pk in [
    ("列 COMMENT 中的关键字", "id INT COMMENT 'SERIAL CONSTRAINT uq UNIQUE(c)'", "id,sk"),
    ("DEFAULT 字符串中的关键字", "id VARCHAR(80) DEFAULT 'SERIAL CONSTRAINT uq UNIQUE(c)'", "id,sk"),
    ("反引号标识符中的关键字", "`SERIAL` INT COMMENT 'x'", "`SERIAL`,sk"),
]:
    add("R14-KFN-DECOY", _label,
        "CREATE TABLE t (%s, sk INT, PRIMARY KEY(%s)) ENGINE=InnoDB shardkey=sk" % (_frag, _pk),
        "pos", "REVIEW_14", "tokenizer 必须隔离字面量/标识符，preflight 不得误阻断",
        needs_recovery=False, kfn_absent=True)

# ── M-CREATE / M-TAIL / M-PARTITION：CreateShape 顶层与表尾的单点变异 ──────
#    第十二轮 BLOCK-12-04：Rev.M 的 M 组只覆盖定义列表，下面 13 种单点变异
#    在 Rev.M 上**全部返回 True**。
_MC_SRC = ("CREATE TEMPORARY TABLE IF NOT EXISTS `db1`.`t` (`id` INT, `sk` INT, "
           "UNIQUE KEY `uk` (`sk`) COMMENT 'u') ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 "
           "COLLATE=utf8mb4_bin COMMENT='表' ROW_FORMAT=DYNAMIC "
           "PARTITION BY RANGE(`id`) (PARTITION p0 VALUES LESS THAN (10), "
           "PARTITION p1 VALUES LESS THAN (20))")
_MC_GOOD = _MC_SRC.replace(" COMMENT 'u'", "")
mut("CreateShape 顶层语义（qname / TEMPORARY / IF NOT EXISTS）", _MC_SRC, _MC_GOOD,
    [("CREATE TEMPORARY → CREATE", _MC_GOOD.replace("CREATE TEMPORARY TABLE", "CREATE TABLE")),
     ("删除 IF NOT EXISTS", _MC_GOOD.replace("IF NOT EXISTS ", "")),
     ("schema db1 → db2", _MC_GOOD.replace("`db1`", "`db2`")),
     ("删除 schema", _MC_GOOD.replace("`db1`.`t`", "`t`"))])
mut("CreateShape 本地表选项", _MC_SRC, _MC_GOOD,
    [("ENGINE=InnoDB → MyISAM", _MC_GOOD.replace("ENGINE=InnoDB", "ENGINE=MyISAM")),
     ("CHARSET utf8mb4 → latin1", _MC_GOOD.replace("CHARSET=utf8mb4", "CHARSET=latin1")),
     ("COLLATE 改变", _MC_GOOD.replace("utf8mb4_bin", "utf8mb4_general_ci")),
     ("表 COMMENT 文本改变", _MC_GOOD.replace("COMMENT='表'", "COMMENT='别的'")),
     ("ROW_FORMAT DYNAMIC → COMPACT", _MC_GOOD.replace("ROW_FORMAT=DYNAMIC", "ROW_FORMAT=COMPACT")),
     ("删除全部本地表选项",
      _MC_GOOD.replace(" ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin"
                       " COMMENT='表' ROW_FORMAT=DYNAMIC", "")),
     ("凭空多出 AUTO_INCREMENT", _MC_GOOD.replace("ROW_FORMAT=DYNAMIC",
                                                 "ROW_FORMAT=DYNAMIC AUTO_INCREMENT=7"))])
mut("CreateShape 二级分区细节", _MC_SRC, _MC_GOOD,
    [("分区方法 RANGE → LIST",
      _MC_GOOD.replace("RANGE(`id`) (PARTITION p0 VALUES LESS THAN (10), "
                       "PARTITION p1 VALUES LESS THAN (20))",
                       "LIST(`id`) (PARTITION p0 VALUES IN (1), PARTITION p1 VALUES IN (2))")),
     ("分区键 id → sk", _MC_GOOD.replace("RANGE(`id`)", "RANGE(`sk`)")),
     ("分区名 p0 → p9", _MC_GOOD.replace("PARTITION p0", "PARTITION p9")),
     ("LESS THAN 边界 10 → 99", _MC_GOOD.replace("LESS THAN (10)", "LESS THAN (99)")),
     ("分区个数由 2 变 1",
      _MC_GOOD.replace(", PARTITION p1 VALUES LESS THAN (20)", "")),
     ("分区顺序交换",
      _MC_GOOD.replace("PARTITION p0 VALUES LESS THAN (10), PARTITION p1 VALUES LESS THAN (20)",
                       "PARTITION p1 VALUES LESS THAN (20), PARTITION p0 VALUES LESS THAN (10)")),
     ("整个分区被抹掉",
      _MC_GOOD[:_MC_GOOD.index(" PARTITION BY RANGE")])])
_CN_SRC = ("CREATE TABLE `t` (`id` INT, `sk` INT, CONSTRAINT `pk` PRIMARY KEY (`id`), "
           "UNIQUE KEY `uk` (`sk`) COMMENT 'u') ENGINE=InnoDB")
_CN_GOOD = _CN_SRC.replace(" COMMENT 'u'", "")
mut("具名 PRIMARY 约束（MAJOR-12-01）", _CN_SRC, _CN_GOOD,
    [("constraint symbol 改变", _CN_GOOD.replace("CONSTRAINT `pk`", "CONSTRAINT `zz`")),
     ("去掉 CONSTRAINT 包装", _CN_GOOD.replace("CONSTRAINT `pk` PRIMARY KEY (`id`)",
                                             "PRIMARY KEY (`id`)")),
     ("主键列改变", _CN_GOOD.replace("PRIMARY KEY (`id`)", "PRIMARY KEY (`sk`)"))])

# ── 字符集拼写的跨版本词法差异（Rev.N 自查发现，非 O 报告条目）─────────────
#   `CHARACTER SET` 在 30.17.0 上被拆成 `CHAR` + `SET` 两个 token；
#   只认 token 类型会让这条**官方合法**拼写在该版本上失败关闭。
for _lbl, _opt in [
    ("DEFAULT CHARSET=", "ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"),
    ("CHARSET=", "ENGINE=InnoDB CHARSET=utf8mb4"),
    ("DEFAULT CHARACTER SET=", "ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4"),
    ("CHARACTER SET=", "ENGINE=InnoDB CHARACTER SET=utf8mb4"),
    ("DEFAULT COLLATE=", "ENGINE=InnoDB DEFAULT COLLATE=utf8mb4_bin"),
    ("CHARACTER SET + COLLATE", "ENGINE=InnoDB CHARACTER SET=utf8mb4 COLLATE=utf8mb4_bin"),
]:
    add("R12-CS", _lbl,
        "CREATE TABLE `t` (`id` INT, `sk` INT, UNIQUE KEY `uk` (`sk`) COMMENT 'u') " + _opt,
        "pos", "OFFICIAL",
        "官方 local_table_option 的两种拼写；词法表现随 sqlglot 版本变化，必须按文本兜住")
