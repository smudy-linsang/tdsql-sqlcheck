-- ============================================================================
-- 文件审核测试物料 05：Oracle 迁移兼容规范
-- 覆盖规则：R079,R080,R081,R082,R083,R085,R086,R087,R088,R089,R090,R091,R093,
--           R094,R099,R101,R102,R103,R104,R105,R106,R108,R110,R111,R112,R113,R119
-- 说明：R078/R097/R098/R115/R116/R117/R118 属 DDL 上下文，见 01_naming_ddl.sql；
--       R092/R100/R111(分布式) 见 02/04 文件。本文件均为 DML/查询级语法。
-- ============================================================================

-- @case: R079_01
-- @rules: R079
-- @note: Oracle 伪列 ROWNUM
SELECT cust_id, cust_name FROM t_customer WHERE ROWNUM <= 10;

-- @case: R080_01
-- @rules: R080
-- @note: NVL 函数
SELECT cust_id, NVL(email, 'none') AS email FROM t_customer WHERE cust_id = 1001;

-- @case: R081_01
-- @rules: R081
-- @note: DECODE 函数
SELECT cust_id, DECODE(cust_level, 'gold', '金', 'silver', '银', '其他') AS lvl FROM t_customer WHERE cust_id = 1001;

-- @case: R082_01
-- @rules: R082
-- @note: TO_CHAR 函数
SELECT cust_id, TO_CHAR(create_time, 'YYYYMMDD') AS dt FROM t_customer WHERE cust_id = 1001;

-- @case: R083_01
-- @rules: R016,R083
-- @note: TO_NUMBER 函数（WHERE 含函数共触发 R016）
SELECT cust_id FROM t_customer WHERE cust_id = TO_NUMBER('1001');

-- @case: R085_01
-- @rules: R016,R085
-- @note: TO_DATE 函数（WHERE 含函数共触发 R016）
SELECT cust_id FROM t_customer WHERE create_time > TO_DATE('20240101', 'YYYYMMDD');

-- @case: R086_01
-- @rules: R086
-- @note: TRUNC 函数
SELECT cust_id, TRUNC(balance, 2) AS bal FROM t_account WHERE account_no = 'A001';

-- @case: R087_01
-- @rules: R087
-- @note: LTRIM 双参数用法
SELECT cust_id, LTRIM(phone, '0') AS phone FROM t_customer WHERE cust_id = 1001;

-- @case: R088_01
-- @rules: R088
-- @note: ADD_MONTHS 函数
SELECT cust_id, ADD_MONTHS(create_time, -1) AS prev FROM t_customer WHERE cust_id = 1001;

-- @case: R089_01
-- @rules: R089
-- @note: SUBSTR 起始位置为 0
SELECT cust_id, SUBSTR(id_no, 0, 6) AS prefix FROM t_customer WHERE cust_id = 1001;

-- @case: R090_01
-- @rules: R090
-- @note: SYSDATE 裸用（未带括号）
SELECT cust_id FROM t_customer WHERE create_time < SYSDATE;

-- @case: R091_01
-- @rules: R013,R014,R070,R091
-- @note: MERGE INTO 语句（WHEN MATCHED UPDATE 无WHERE共触发 R013/R014/R070）
MERGE INTO t_customer c USING t_deposit d ON (c.cust_id = d.cust_id)
WHEN MATCHED THEN UPDATE SET c.cust_level = 'gold';

-- @case: R093_01
-- @rules: R016,R093
-- @note: LENGTH() 返回字节数（WHERE 含函数共触发 R016；中文场景需 CHAR_LENGTH）
SELECT cust_id FROM t_customer WHERE LENGTH(cust_name) > 10;

-- @case: R094_01
-- @rules: R094
-- @note: LISTAGG ... WITHIN GROUP
SELECT cust_level, LISTAGG(cust_name, ',') WITHIN GROUP (ORDER BY cust_name) AS names
FROM t_customer WHERE cust_level = 'gold' GROUP BY cust_level;

-- @case: R099_01
-- @rules: R099
-- @note: FROM 后派生表未指定别名
SELECT cust_id FROM (SELECT cust_id FROM t_customer WHERE cust_level = 'gold') WHERE cust_id > 0;

-- @case: R101_01
-- @rules: R101
-- @note: 使用 CONDITION 保留字作为标识符（未加反引号）
SELECT condition FROM t_rule_demo WHERE id = 1;

-- @case: R102_01
-- @rules: R051,R102
-- @note: LIKE ... ESCAPE 反斜杠转义符（解析致 R051 共触发）
SELECT cust_id FROM t_customer WHERE cust_name LIKE '%\_%' ESCAPE '\';

-- @case: R103_01
-- @rules: R051,R103
-- @note: 比较运算符中间含空格（解析致 R051 共触发）
SELECT cust_id FROM t_customer WHERE cust_id < = 1000;

-- @case: R104_01
-- @rules: R104
-- @note: 使用全角括号
SELECT COUNT（cust_id） FROM t_customer WHERE cust_level = 'gold';

-- @case: R105_01
-- @rules: R016,R105
-- @rules.dist: R016,R020,R053,R105
-- @note: Oracle (+) 外连接语法（分布式额外 R020/R053）
SELECT a.account_no, c.cust_name
FROM t_account a, t_customer c
WHERE a.cust_id = c.cust_id(+);

-- @case: R106_01
-- @rules: R051,R106
-- @note: START WITH ... CONNECT BY 层级查询（解析致 R051 共触发）
SELECT cust_id, cust_name FROM t_customer
START WITH cust_id = 1 CONNECT BY PRIOR cust_id = cust_id;

-- @case: R108_01
-- @rules: R108
-- @note: sequence 多行批量获取（非 from dual）
SELECT seq_order.nextval FROM t_order WHERE order_id < 100;

-- @case: R110_01
-- @rules: R110
-- @note: USERENV 系统上下文函数
SELECT USERENV('INSTANCE') AS inst FROM t_customer WHERE cust_id = 1;

-- @case: R111_01
-- @rules: R111
-- @scope: distributed
-- @note: 窗口函数 OVER()，分布式不支持（R111 仅分布式口径触发）
SELECT cust_id, ROW_NUMBER() OVER (ORDER BY create_time) AS rn
FROM t_customer WHERE cust_level = 'gold';

-- @case: R112_01
-- @rules: R051,R112
-- @scope: distributed
-- @note: 游标用法，分布式不支持（解析致 R051 共触发）
DECLARE cur_cust CURSOR FOR SELECT cust_id FROM t_customer;

-- @case: R113_01
-- @rules: R113
-- @scope: distributed
-- @note: DROP PARTITION 高并发风险提示
ALTER TABLE t_transaction DROP PARTITION p202401;

-- @case: R119_01
-- @rules: R016,R119
-- @note: 日期函数直接加减数字（WHERE 含函数共触发 R016）
SELECT cust_id FROM t_customer WHERE create_time > sysdate()-15;
