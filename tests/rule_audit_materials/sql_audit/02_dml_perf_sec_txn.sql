-- ============================================================================
-- 文件审核测试物料 02：DML / 性能 / 安全 / 事务规范
-- 覆盖规则：R012,R013,R014,R015,R016,R017,R020,R021,R022,R039,R040,R041,R042,
--           R043,R044,R045,R046,R047,R050,R051,R052,R053,R058,R059,R069,R070,
--           R071,R072,R074,R075,R076,R084,R092,R095,R096,R100,R107,R109,R114
-- 说明：分布式规则在文件审核（无表元数据）下走启发式分支；部分 Oracle/特殊
--       语法在 mysql 方言下解析失败会共触发 R051/R016 等，属引擎真实行为，
--       用 @rules.dist/@rules.cent 如实标注完整集合。
-- ============================================================================

-- @case: R012_01
-- @rules: R012
-- @note: SELECT * 未指定字段
SELECT * FROM t_customer WHERE cust_id = 1001;

-- @case: R013_R014_01
-- @rules: R013,R014,R070
-- @note: UPDATE 不带 WHERE，R013/R014 同时触发，无WHERE大事务触发 R070
UPDATE t_customer SET cust_level = 'gold';

-- @case: R015_01
-- @rules: R015
-- @rules.dist: R015,R020
-- @note: 子查询嵌套超过 3 层（分布式多表启发式共触发 R020）
SELECT cust_id FROM t_customer WHERE cust_id IN (
    SELECT cust_id FROM t_account WHERE account_no IN (
        SELECT account_no FROM t_transaction WHERE txn_id IN (
            SELECT txn_id FROM t_audit_log WHERE log_id IN (
                SELECT log_id FROM t_deposit WHERE deposit_no = 'D1'
            )
        )
    )
);

-- @case: R016_01
-- @rules: R016
-- @note: WHERE 条件中对字段使用函数，导致索引失效
SELECT cust_id, cust_name FROM t_customer WHERE DATE_FORMAT(create_time, '%Y%m%d') = '20240101';

-- @case: R017_01
-- @rules: R017
-- @note: ORDER BY RAND() 导致全表扫描
SELECT cust_id, cust_name FROM t_customer WHERE cust_level = 'gold' ORDER BY RAND() LIMIT 10;

-- @case: R020_01
-- @rules: R068
-- @rules.dist: R020,R053,R068
-- @note: 多表关联查询（R020/R053 分布式启发式；R068 JOIN关联字段建议通用）
SELECT a.account_no, c.cust_name
FROM t_account a
JOIN t_customer c ON a.cust_id = c.cust_id
WHERE a.balance > 1000;

-- @case: R021_01
-- @rules: R021
-- @scope: distributed
-- @note: SET 子句更新 shard_key 命名字段（启发式，仅分布式）
UPDATE t_shard_demo SET shard_key = 999 WHERE id = 1;

-- @case: R022_01
-- @rules: R022
-- @scope: distributed
-- @note: DELETE 带 WHERE 但无等值条件且无 LIMIT（启发式：可能全SET扫描）
DELETE FROM t_transaction WHERE txn_time > '2020-01-01';

-- @case: R039_01
-- @rules: R039,R051
-- @note: SELECT ... INTO OUTFILE（INTO 子句致解析无 WHERE，共触发 R051）
SELECT cust_id, cust_name FROM t_customer WHERE cust_id = 1 INTO OUTFILE '/tmp/cust.csv';

-- @case: R040_01
-- @rules: R040
-- @note: INSERT DELAYED 关键字
INSERT DELAYED INTO t_audit_log (log_id, cust_id) VALUES (1, 1001);

-- @case: R041_01
-- @rules: R041
-- @note: INSERT 未显式指定列名
INSERT INTO t_dict VALUES (1, 'k', 'v');

-- @case: R042_01
-- @rules: R042
-- @note: LOAD DATA INFILE
LOAD DATA INFILE '/tmp/data.csv' INTO TABLE t_dict;

-- @case: R043_01
-- @rules: R013,R014,R068,R070
-- @rules.dist: R013,R014,R043,R053,R068,R070
-- @note: 多表联表 UPDATE（JOIN 形式，R043 仅分布式；无WHERE共触发 R013/R014/R070）
UPDATE t_account a JOIN t_customer c ON a.cust_id = c.cust_id SET a.account_type = 'vip';

-- @case: R044_01
-- @rules: R044
-- @note: 使用 FORCE INDEX 索引提示
SELECT cust_id, cust_name FROM t_customer FORCE INDEX (idx_cust_level) WHERE cust_level = 'gold';

-- @case: R045_01
-- @rules: R045
-- @note: HANDLER 语句
HANDLER t_customer OPEN;

-- @case: R046_01
-- @rules: R046
-- @note: LOCK TABLES 语句
LOCK TABLES t_customer WRITE;

-- @case: R047_01
-- @rules: R013,R014,R047,R070
-- @note: 全表 DELETE（无WHERE）建议改 TRUNCATE，同时触发 R013/R014/R070
DELETE FROM t_audit_log;

-- @case: R050_01
-- @rules: R050
-- @note: IN 列表元素 205 个，超过建议的 200
SELECT cust_id, cust_name FROM t_customer WHERE cust_id IN (1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48,49,50,51,52,53,54,55,56,57,58,59,60,61,62,63,64,65,66,67,68,69,70,71,72,73,74,75,76,77,78,79,80,81,82,83,84,85,86,87,88,89,90,91,92,93,94,95,96,97,98,99,100,101,102,103,104,105,106,107,108,109,110,111,112,113,114,115,116,117,118,119,120,121,122,123,124,125,126,127,128,129,130,131,132,133,134,135,136,137,138,139,140,141,142,143,144,145,146,147,148,149,150,151,152,153,154,155,156,157,158,159,160,161,162,163,164,165,166,167,168,169,170,171,172,173,174,175,176,177,178,179,180,181,182,183,184,185,186,187,188,189,190,191,192,193,194,195,196,197,198,199,200,201,202,203,204,205);

-- @case: R051_01
-- @rules: R051
-- @note: SELECT 无 WHERE 也无 ORDER BY，全表扫描
SELECT cust_id, cust_name FROM t_customer;

-- @case: R052_01
-- @rules: R052
-- @note: 数值字段 cust_id 与字符串字面量比较，隐式类型转换
SELECT cust_id, cust_name FROM t_customer WHERE cust_id = '1001';

-- @case: R053_01
-- @rules: R068
-- @rules.dist: R020,R053,R068
-- @note: 显式 JOIN 多表（R053 仅分布式提示；R068 关联字段建议通用）
SELECT a.account_no, c.cust_name
FROM t_account a
INNER JOIN t_customer c ON a.cust_id = c.cust_id
WHERE a.balance > 0;

-- @case: R058_01
-- @rules:
-- @note: 分布式表批量 UPDATE 带 WHERE 等值但无 LIMIT。R058 需表元数据识别分片表
--        后才触发，文件审核（无元数据）下不命中，期望为空。真实触发在在线元数据
--        审核场景验证（见测试说明书）。本例同时演示 R058 的目标场景。
UPDATE t_customer SET cust_level = 'silver' WHERE cust_level = 'normal';

-- @case: R059_01
-- @rules: R069,R071
-- @note: BEGIN 事务。R059(分布式事务提示)需元数据，文件审核不触发；
--        R069(长事务)/R071(未见COMMIT)通用触发。R059 真实触发见在线元数据审核。
BEGIN;

-- @case: R069_R071_01
-- @rules: R069,R071
-- @note: START TRANSACTION 未见 COMMIT/ROLLBACK
START TRANSACTION;

-- @case: R072_01
-- @rules: R072
-- @note: SELECT ... FOR UPDATE 排他锁
SELECT balance FROM t_account WHERE account_no = 'A001' FOR UPDATE;

-- @case: R074_01
-- @rules: R051,R074
-- @note: GRANT 权限语句（非SELECT被引擎记 R051 全表扫描提示，共触发）
GRANT SELECT ON tdsql_check.t_customer TO 'appuser'@'%';

-- @case: R075_01
-- @rules: R075
-- @note: TRUNCATE TABLE 不可回滚
TRUNCATE TABLE t_audit_log;

-- @case: R076_01
-- @rules: R076
-- @note: MyBatis 美元符号花括号动态拼接（SQL 注入风险）
SELECT cust_id, cust_name FROM t_customer WHERE cust_name = '${custName}';

-- @case: R084_01
-- @rules: R084
-- @note: 双竖线运算符（MySQL/TDSQL 语义为逻辑OR，拼接应改 CONCAT）
SELECT cust_name || phone AS contact FROM t_customer WHERE cust_id = 1001;

-- @case: R092_01
-- @rules: R051,R092
-- @scope: distributed
-- @note: WITH AS (CTE)，分布式不支持（CTE 解析致 R051 共触发）
WITH recent_cust AS (SELECT cust_id, cust_name FROM t_customer WHERE cust_level = 'gold')
SELECT cust_id FROM recent_cust;

-- @case: R095_01
-- @rules: R051,R095
-- @note: MINUS 集合运算（解析致 R051 共触发）
SELECT cust_id FROM t_customer WHERE cust_level = 'gold'
MINUS
SELECT cust_id FROM t_account WHERE balance > 0;

-- @case: R096_01
-- @rules: R051,R068,R096
-- @rules.dist: R051,R053,R068,R096
-- @note: FULL JOIN（分布式额外 R053）
SELECT a.account_no, c.cust_name
FROM t_account a FULL JOIN t_customer c ON a.cust_id = c.cust_id;

-- @case: R100_01
-- @rules: R100
-- @scope: distributed
-- @note: DELETE 对被删表设置别名，分布式不支持
DELETE FROM t_audit_log a WHERE a.log_id = 1;

-- @case: R107_01
-- @rules: R001,R107
-- @note: INSERT INTO ... SELECT（解析器将 INSERT 列清单并入表名致 R001 共触发，属引擎真实行为）
INSERT INTO t_audit_record (log_id, cust_id) SELECT log_id, cust_id FROM t_deposit_src;

-- @case: R109_01
-- @rules: R109
-- @note: UPDATE 后续 SET 的 CASE WHEN 引用前面已赋值字段的新值
UPDATE t_customer SET cust_level = 'gold', cust_level = CASE WHEN cust_level = 'gold' THEN 'vip' ELSE cust_level END WHERE cust_id = 1001;

-- @case: R114_01
-- @rules: R114
-- @note: LIMIT 大偏移深分页
SELECT cust_id, cust_name FROM t_customer WHERE cust_level = 'gold' ORDER BY cust_id LIMIT 20000, 20;
