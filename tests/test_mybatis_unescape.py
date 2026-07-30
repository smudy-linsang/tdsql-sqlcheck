"""
MyBatis XML HTML 实体与 CDATA 标签解码测试（防止 &gt;= 等转义引发语法解析失败与误报）
"""
from backend.engine.checker import RuleChecker


def test_mybatis_xml_entity_unescape():
    checker = RuleChecker()
    xml_content = """
    <mapper namespace="com.example.BankAccountMapper">
        <select id="findByBalance" resultType="Account">
            SELECT account_id, cust_id, account_no, account_type, currency, balance, available_balance, status, create_time 
            FROM t_account 
            WHERE status = 1 AND cust_id = #{custId} AND account_type = #{accountType} 
              AND balance &gt;= #{minBalance} AND balance &lt;= #{maxBalance} AND branch_id = #{branchId} 
            ORDER BY create_time DESC LIMIT #{limit} OFFSET #{offset}
        </select>
        
        <update id="deductBalance">
            UPDATE t_account 
            SET balance = balance - #{amount}, available_balance = available_balance - #{amount}, update_time = NOW() 
            WHERE account_no = #{accountNo} AND available_balance &gt;= #{amount} AND status = 1
        </update>
    </mapper>
    """

    sqls = checker._extract_sql_from_mybatis(xml_content)
    assert len(sqls) == 2, f"应当提取出 2 条 SQL，实际提取: {len(sqls)}"

    # 1. 验证解析提取后的 SQL 中 &gt;= / &lt;= 被成功转义回原始运算符
    select_sql, _ = sqls[0]
    update_sql, _ = sqls[1]

    assert "&gt;" not in select_sql and "&lt;" not in select_sql
    assert "balance >= ?" in select_sql
    assert "balance <= ?" in select_sql

    assert "&gt;" not in update_sql
    assert "available_balance >= ?" in update_sql

    # 2. 验证审核时不再触发 ParseError(E999) 也不再误报 R051 / R013 / R014 / R070
    select_res = checker.audit_sql(select_sql)
    select_rule_ids = [v.rule_id for v in select_res.violations]
    assert "E999_SYNTAX_ERROR" not in select_rule_ids
    assert "R051" not in select_rule_ids

    update_res = checker.audit_sql(update_sql)
    update_rule_ids = [v.rule_id for v in update_res.violations]
    assert "E999_SYNTAX_ERROR" not in update_rule_ids
    assert "R013" not in update_rule_ids
    assert "R014" not in update_rule_ids
    assert "R070" not in update_rule_ids
