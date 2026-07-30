"""给 big_audit_trail 表 + 全部非 id 字段加中文注释。

复用了 big_order_log 的脚本结构, 表名和注释改一下。
"""
import pymysql
import sys

CONN = dict(
    host="119.45.220.89",
    port=15005,
    user="tdsql_check_user",
    password="Abcd1234",
    connect_timeout=10,
    charset="utf8mb4",
    database="tdsql_check",
)

TABLE_NAME = "big_audit_trail"
TABLE_COMMENT = "审计大表(分片键 id)"

# 字段注释映射 (字段 -> 中文注释) — 跟实际表结构对齐
COL_COMMENTS = {
    "trace_id":      "链路追踪ID",
    "operator":      "操作人",
    "module":        "操作模块",
    "action":        "操作类型",
    "target_type":   "操作对象类型",
    "target_id":     "操作对象ID",
    "detail":        "操作详情",
    "ip_address":    "客户端IP地址",
    "user_agent":    "客户端User-Agent",
    "request_body":  "请求体摘要",
    "response_code": "响应码",
    "duration_ms":   "处理耗时(毫秒)",
    "event_time":    "事件发生时间",
}


def main():
    conn = pymysql.connect(**CONN)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT, EXTRA
                FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA='tdsql_check' AND TABLE_NAME=%s
                ORDER BY ORDINAL_POSITION
            """, (TABLE_NAME,))
            cols = cur.fetchall()
            if not cols:
                print(f"✗ 表 {TABLE_NAME} 不存在或没权限")
                sys.exit(1)
            print(f"{TABLE_NAME} 共有 {len(cols)} 个字段:")
            for c in cols:
                print(f"  {c[0]:<14} {c[1]:<30} null={c[2]:<3} default={c[3]} extra={c[4]}")

            # 拼 ALTER: 跳过 id 字段 (分布式表 auto_increment 不能改)
            parts = [f"ALTER TABLE `{TABLE_NAME}` COMMENT = '{TABLE_COMMENT}'"]
            for c in cols:
                name = c[0]
                if name == "id":
                    continue
                col_type = c[1]
                nullable = "NULL" if c[2] == "YES" else "NOT NULL"
                collate = " COLLATE utf8mb4_bin" if "varchar" in col_type.lower() or "char" in col_type.lower() else ""
                # 优先用字典里定义的, 没定义就兜底用 "字段" (内网智能体或 DBA 可手工调整)
                cm = COL_COMMENTS.get(name, f"{name} 字段")
                parts.append(f"MODIFY COLUMN `{name}` {col_type}{collate} {nullable} COMMENT '{cm}'")
            sql = ",\n  ".join(parts)
            print("\n生成的 SQL:")
            print(sql)

            print("\n开始执行 ALTER...")
            cur.execute(sql)
            print("✓ ALTER 成功")
        conn.commit()
        print("✓ commit 成功")

        # 验证
        print("\n验证 (查 information_schema):")
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COLUMN_NAME, COLUMN_COMMENT
                FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA='tdsql_check' AND TABLE_NAME=%s
                ORDER BY ORDINAL_POSITION
            """, (TABLE_NAME,))
            for name, comment in cur.fetchall():
                print(f"  {name:<18} = {comment}")
            cur.execute("""
                SELECT TABLE_COMMENT FROM information_schema.TABLES
                WHERE TABLE_SCHEMA='tdsql_check' AND TABLE_NAME=%s
            """, (TABLE_NAME,))
            print(f"  --- 表注释 ---")
            print(f"  TABLE_COMMENT = {cur.fetchone()[0]}")
    except Exception as e:
        print(f"\n✗ 失败: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
    finally:
        conn.close()


if __name__ == "__main__":
    main()
