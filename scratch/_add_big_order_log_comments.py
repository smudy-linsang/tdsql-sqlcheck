"""给 big_order_log 表 + 12 个非 id 字段加中文注释。

分布式实例: shardkey=id, 表分布在 group_1782132247_10 下的 2 个 set。
ALTER TABLE 走 proxy 应广播到所有 set。
"""
import pymysql

CONN = dict(
    host="119.45.220.89",
    port=15005,
    user="tdsql_check_user",
    password="Abcd1234",
    connect_timeout=10,
    charset="utf8mb4",
    database="tdsql_check",
)

# 注释映射 (字段 -> 中文注释)
COL_COMMENTS = {
    "order_no":     "订单号",
    "user_id":      "用户ID",
    "product_name": "商品名称",
    "category":     "商品分类",
    "amount":       "订单金额(元)",
    "status":       "订单状态",
    "channel":      "下单渠道",
    "region":       "下单区域",
    "remark":       "订单备注",
    "extra_info":   "扩展信息",
    "create_time":  "创建时间",
    "update_time":  "更新时间",
}

# 表注释
TABLE_COMMENT = "大订单日志表"


def main():
    conn = pymysql.connect(**CONN)
    try:
        with conn.cursor() as cur:
            # 拿到当前表结构 (从 information_schema 拿权威定义, 不受当前 session 影响)
            cur.execute("""
                SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT, EXTRA
                FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA='tdsql_check' AND TABLE_NAME='big_order_log'
                ORDER BY ORDINAL_POSITION
            """)
            cols = cur.fetchall()
            print(f"原表共 {len(cols)} 个字段:")
            for c in cols:
                print(f"  {c[0]:<14} {c[1]:<22} null={c[2]:<3} default={c[3]} extra={c[4]}")

            # 拼 ALTER: 用 MODIFY COLUMN 重写每个字段定义 + COMMENT
            # 注意: 分布式表 (shardkey=id) 不能再 MODIFY id (auto_increment 不能改)
            # 所以 id 字段跳过, 其他 12 个字段都加 COMMENT
            parts = [f"ALTER TABLE `big_order_log` COMMENT = '{TABLE_COMMENT}'"]
            for c in cols:
                name = c[0]
                if name == "id":
                    # 跳过主键 id - 分布式表 DDL 不支持改 auto_increment 字段
                    continue
                col_type = c[1]
                nullable = "NULL" if c[2] == "YES" else "NOT NULL"
                # 提取 COLLATE (如果原类型里有)
                collate = " COLLATE utf8mb4_bin" if "varchar" in col_type.lower() or "char" in col_type.lower() else ""
                cm = COL_COMMENTS.get(name, name)
                parts.append(f"MODIFY COLUMN `{name}` {col_type}{collate} {nullable} COMMENT '{cm}'")
            sql = ",\n  ".join(parts)
            print("\n生成的 SQL:")
            print(sql)

            print("\n开始执行 ALTER...")
            cur.execute(sql)
            print("✓ ALTER 成功")
        conn.commit()
        print("✓ commit 成功")

        # 验证: 重新拿 SHOW CREATE TABLE 看注释
        print("\n验证 (查 information_schema):")
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COLUMN_NAME, COLUMN_COMMENT
                FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA='tdsql_check' AND TABLE_NAME='big_order_log'
                ORDER BY ORDINAL_POSITION
            """)
            for name, comment in cur.fetchall():
                print(f"  {name:<14} = {comment}")
            cur.execute("""
                SELECT TABLE_COMMENT FROM information_schema.TABLES
                WHERE TABLE_SCHEMA='tdsql_check' AND TABLE_NAME='big_order_log'
            """)
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
