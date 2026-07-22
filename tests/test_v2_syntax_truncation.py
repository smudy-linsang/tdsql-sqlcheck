"""
验证残缺截断 SQL 与语法解析错误防线 (E999_SYNTAX_ERROR)
"""
import pytest
from backend.engine.checker import RuleChecker

def test_truncated_sql_fails_with_syntax_error():
    checker = RuleChecker()
    # 模拟从元数据抽出来的截断残缺语句
    truncated_sql = "CREATE TABLE `account_no_mapping` (\n  `serialno` varchar(40) NOT NULL,\n  `oldaccount` varchar(40) COLLATE utf8mb4_"
    
    res = checker.audit_sql(truncated_sql)
    assert res.passed is False, "截断的残缺 SQL 绝不能被标记为通过"
    assert any(v.rule_id == "E999_SYNTAX_ERROR" for v in res.violations), "必须存在 E999_SYNTAX_ERROR 阻断违规"
    assert any(v.severity == "ERROR" for v in res.violations), "必须包含 ERROR 级别的严重告警"

def test_split_truncated_sql_file():
    checker = RuleChecker()
    file_content = """
-- SQL Object: CREATE TABLE
-- Table: t1
CREATE TABLE `t1` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `create_time` datetime DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `update_time` datetime DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 BROADCAST COMMENT='测试表';

-- SQL Object: CREATE TABLE
-- Table: t2
CREATE TABLE `t2` ( `id` int NOT NULL, `col2` varchar(20)

-- SQL Object: CREATE TABLE
-- Table: t3
CREATE TABLE `t3` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `create_time` datetime DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `update_time` datetime DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 BROADCAST COMMENT='测试表';
"""
    results = checker.audit_file(file_content, file_path="test.sql")
    assert len(results) == 3, f"应该成功分割为 3 条语句，实际为 {len(results)}"
    assert results[0].passed is True, "标准的 t1 语句应该审计通过"
    assert results[1].passed is False, "残缺截断的 t2 语句必须阻断报错"
    assert any(v.rule_id == "E999_SYNTAX_ERROR" for v in results[1].violations)
    assert results[2].passed is True, "标准的 t3 语句应该审计通过"
