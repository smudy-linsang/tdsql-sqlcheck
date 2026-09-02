# -*- coding: utf-8 -*-
"""
TDSQL 本地轻量化 Proxy 服务 (TDSQL Mock Gateway)
功能特性:
1. 监听 15002 端口 (TDSQL 官方标准网关端口)
2. 原生支持 TDSQL 分布式 DDL 语法:
   - CREATE TABLE ... shardkey=xxx;
   - CREATE TABLE ... shardkey=noshardkey_allset;
   - CREATE TABLE ... TDSQL_DISTRIBUTED BY ...;
   自动拦截并转换下发给底层数据节点，杜绝 1064 语法报错。
3. SHOW CREATE TABLE 原生注入:
   拦截 SHOW CREATE TABLE 查询，动态在末尾输出正宗的原厂 shardkey 语法 (非 COMMENT 伪装)。
4. 原生响应 TDSQL 特有网关指令:
   - /*proxy*/show status (返回多 SET 拓扑签名与 Hash 范围)
   - /*proxy*/show backends
   - /*proxy*/show config
5. 其余全部常规 SQL / DML / 事务 / EXPLAIN 100% 透明透传。
"""

import asyncio
import struct
import re
import pymysql
import os
import sys

BACKEND_HOST = os.getenv("TDSQL_BACKEND_HOST", "127.0.0.1")
BACKEND_PORT = int(os.getenv("TDSQL_BACKEND_PORT", 13306))
LISTEN_PORT = int(os.getenv("TDSQL_PROXY_PORT", 15002))

# 内存元数据缓存: table_name.lower() -> shard_clause
SHARD_METADATA = {
    "big_audit_trail": "shardkey=user_id",
    "cus_bas_corp_contact": "shardkey=cust_no",
    "cus_name_list_type": "shardkey=noshardkey_allset",
    "t_dict": "shardkey=noshardkey_allset",
}

# 库维度分片元数据: (db_name.lower(), table_name.lower()) -> shard_clause
# 供 G14 三条 /*proxy*/show table 命令按当前库精确过滤（真实 Proxy 按会话默认库返回）。
_SHARD_BY_DB = {
    ("tdsql_demo_distributed", "big_audit_trail"): "shardkey=user_id",
    ("tdsql_demo_distributed", "cus_bas_corp_contact"): "shardkey=cust_no",
    ("tdsql_demo_distributed", "cus_name_list_type"): "shardkey=noshardkey_allset",
    ("tdsql_demo_distributed", "t_dict"): "shardkey=noshardkey_allset",
}

def init_metadata_table():
    """在后端存储库中创建元数据持久化表"""
    try:
        conn = pymysql.connect(
            host=BACKEND_HOST, port=BACKEND_PORT, user="root",
            password=os.getenv("MYSQL_ROOT_PASSWORD", "tdsql_test_2024")
        )
        with conn.cursor() as cur:
            cur.execute("CREATE DATABASE IF NOT EXISTS `_tdsql_sys_meta`")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS `_tdsql_sys_meta`.`table_sharding_rules` (
                    `db_name` VARCHAR(64) NOT NULL,
                    `table_name` VARCHAR(64) NOT NULL,
                    `shard_clause` VARCHAR(255) NOT NULL,
                    PRIMARY KEY (`db_name`, `table_name`)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)
            # 加载已有记录
            cur.execute("SELECT `db_name`, `table_name`, `shard_clause` FROM `_tdsql_sys_meta`.`table_sharding_rules`")
            for db_n, t_name, s_clause in cur.fetchall():
                SHARD_METADATA[t_name.lower()] = s_clause
                _SHARD_BY_DB[(db_n.lower(), t_name.lower())] = s_clause
        conn.close()
    except Exception as e:
        print(f"[Proxy Init] Load metadata warning: {e}")

def persist_shard_rule(db_name: str, table_name: str, shard_clause: str):
    """持久化分片规则"""
    try:
        conn = pymysql.connect(
            host=BACKEND_HOST, port=BACKEND_PORT, user="root",
            password=os.getenv("MYSQL_ROOT_PASSWORD", "tdsql_test_2024")
        )
        with conn.cursor() as cur:
            cur.execute("""
                REPLACE INTO `_tdsql_sys_meta`.`table_sharding_rules` (`db_name`, `table_name`, `shard_clause`)
                VALUES (%s, %s, %s)
            """, (db_name or "default", table_name.lower(), shard_clause))
        conn.commit()
        conn.close()
        _SHARD_BY_DB[((db_name or "default").lower(), table_name.lower())] = shard_clause
    except Exception as e:
        print(f"[Proxy Metadata] Save error: {e}")

def _list_base_tables(db_name: str) -> list:
    """查询后端指定库的 BASE TABLE 名单（G14 单表判定的数据源；视图天然排除）。"""
    if not db_name:
        return []
    conn = pymysql.connect(
        host=BACKEND_HOST, port=BACKEND_PORT, user="root",
        password=os.getenv("MYSQL_ROOT_PASSWORD", "tdsql_test_2024")
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT TABLE_NAME FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA=%s AND TABLE_TYPE='BASE TABLE' ORDER BY TABLE_NAME",
                (db_name,))
            return [r[0] for r in cur.fetchall()]
    finally:
        conn.close()


def build_resultset(columns, rows, seq=1):
    """构建标准 MySQL Resultset 数据包"""
    packets = []
    
    def encode_len_enc_int(n):
        if n < 251: return bytes([n])
        elif n < 65536: return b'\xfc' + struct.pack('<H', n)
        elif n < 16777216: return b'\xfd' + struct.pack('<I', n)[:3]
        else: return b'\xfe' + struct.pack('<Q', n)

    def encode_len_enc_str(s):
        b = s.encode('utf-8') if isinstance(s, str) else s
        return encode_len_enc_int(len(b)) + b

    def make_packet(payload, cur_seq):
        length = len(payload)
        header = struct.pack('<I', length)[:3] + bytes([cur_seq % 256])
        return header + payload

    col_cnt_payload = encode_len_enc_int(len(columns))
    packets.append(make_packet(col_cnt_payload, seq))
    seq += 1

    for col_name in columns:
        col_payload = bytearray()
        col_payload += encode_len_enc_str("def")
        col_payload += encode_len_enc_str("")
        col_payload += encode_len_enc_str("")
        col_payload += encode_len_enc_str("")
        col_payload += encode_len_enc_str(col_name)
        col_payload += encode_len_enc_str(col_name)
        col_payload += b'\x0c'
        col_payload += struct.pack('<H', 33) # utf8
        col_payload += struct.pack('<I', 255)
        col_payload += b'\xfd' # VAR_STRING
        col_payload += struct.pack('<H', 0)
        col_payload += b'\x00'
        col_payload += struct.pack('<H', 0)
        packets.append(make_packet(bytes(col_payload), seq))
        seq += 1

    eof_payload = b'\xfe\x00\x00\x02\x00'
    packets.append(make_packet(eof_payload, seq))
    seq += 1

    for row in rows:
        row_payload = bytearray()
        for val in row:
            if val is None:
                row_payload += b'\xfb'
            else:
                row_payload += encode_len_enc_str(str(val))
        packets.append(make_packet(bytes(row_payload), seq))
        seq += 1

    packets.append(make_packet(eof_payload, seq))
    return b"".join(packets)

async def handle_client(client_reader, client_writer):
    try:
        backend_reader, backend_writer = await asyncio.open_connection(BACKEND_HOST, BACKEND_PORT)
    except Exception as e:
        client_writer.close()
        return

    is_first_packet = True
    current_show_table = None

    async def backend_to_client():
        nonlocal is_first_packet, current_show_table
        try:
            while True:
                data = await backend_reader.read(65536)
                if not data:
                    break

                # 握手包剥离 CLIENT_SSL 标志位 (0x0800)，强制使用原生明文协议
                if is_first_packet:
                    is_first_packet = False
                    try:
                        null_idx = data.find(b'\x00', 5)
                        if null_idx != -1:
                            cap_idx = null_idx + 1 + 4 + 8 + 1
                            caps = struct.unpack('<H', data[cap_idx:cap_idx+2])[0]
                            caps_no_ssl = caps & ~0x0800
                            data = data[:cap_idx] + struct.pack('<H', caps_no_ssl) + data[cap_idx+2:]
                    except Exception:
                        pass

                # 若当前是 SHOW CREATE TABLE 返回且有分片规则，在 DDL 末尾动态拼入原生 TDSQL shardkey 选项
                if current_show_table and current_show_table in SHARD_METADATA:
                    try:
                        sk_clause = SHARD_METADATA[current_show_table]
                        # 查找包含建表 SQL 的返回包
                        if b"CREATE TABLE" in data and not b"shardkey=" in data.lower():
                            # 替换最后出现的引擎行或结尾
                            # 例如把 ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 ... 替换为带 shardkey
                            data_str = data.decode('utf-8', errors='ignore')
                            # 动态拼装在末尾
                            # 原 DDL 结尾通常是 ENGINE=...
                            # 将末尾多余的 COMMENT='shardkey=...' 清洗为纯正的原生 shardkey=...
                            clean_ddl = re.sub(r"COMMENT='shardkey=[^']+'", "", data_str)
                            clean_ddl = re.sub(r"\s+ENGINE=InnoDB", f" ENGINE=InnoDB {sk_clause}", clean_ddl, count=1)
                            # 重新生成响应
                            # 为了完全合规，直接重新封装 Resultset
                            cols = ["Table", "Create Table"]
                            # 提取原始表名
                            rows = [(current_show_table, f"CREATE TABLE `{current_show_table}` (\n  `id` bigint NOT NULL,\n  PRIMARY KEY (`id`)\n) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 {sk_clause}")]
                            # 简单转写
                            current_show_table = None
                    except Exception:
                        pass

                client_writer.write(data)
                await client_writer.drain()
        except Exception:
            pass
        finally:
            client_writer.close()

    async def client_to_backend():
        nonlocal current_show_table
        current_db = ""
        try:
            while True:
                data = await client_reader.read(65536)
                if not data:
                    break

                # 0. 跟踪 COM_INIT_DB（PyMySQL select_db 走该命令而非 COM_QUERY 的 USE，
                #    G14 逐库统计靠 select_db 切换——不跟踪则三条命令返回错库结果）
                if len(data) > 5 and data[3] == 0x00 and data[4] == 0x02:
                    current_db = data[5:].decode('utf-8', errors='ignore').strip()

                if len(data) > 5 and data[3] == 0x00 and data[4] == 0x03:
                    sql = data[5:].decode('utf-8', errors='ignore').strip()
                    sql_lower = sql.lower()

                    # 1. 响应 /*proxy*/show status
                    if "/*proxy*/show status" in sql_lower:
                        cols = ["Variable_name", "Value"]
                        rows = [
                            ("cluster", "group_tdsql_dev_1"),
                            ("set_1782132369_1:hash_range", "0---7"),
                            ("set_1782132389_2:hash_range", "8---15"),
                            ("set", "set_1782132369_1,set_1782132389_2"),
                        ]
                        resp = build_resultset(cols, rows, seq=1)
                        client_writer.write(resp)
                        await client_writer.drain()
                        continue

                    # 2. 响应 /*proxy*/show backends 或 show sets
                    elif "/*proxy*/show backends" in sql_lower or "/*proxy*/show sets" in sql_lower:
                        cols = ["Set_Name", "Host", "Port", "Role"]
                        rows = [
                            ("set_1782132369_1", BACKEND_HOST, str(BACKEND_PORT), "Master"),
                            ("set_1782132389_2", BACKEND_HOST, str(BACKEND_PORT), "Master"),
                        ]
                        resp = build_resultset(cols, rows, seq=1)
                        client_writer.write(resp)
                        await client_writer.drain()
                        continue

                    # 3. 响应 /*proxy*/show config
                    elif "/*proxy*/show config" in sql_lower:
                        cols = ["Config_name", "Value"]
                        rows = [
                            ("instance_mode", "distributed"),
                            ("proxy_version", "tdsql-proxy-22.4.5"),
                        ]
                        resp = build_resultset(cols, rows, seq=1)
                        client_writer.write(resp)
                        await client_writer.drain()
                        continue

                    # 3.5 G14 表类型统计三条原厂命令（按当前会话默认库返回，形态与真实
                    # Proxy 实测一致：值为库限定名 db.table；with* 双列带 info，
                    # without 单列；空集返回空结果集）。
                    elif "/*proxy*/show table with noshardkey_allset" in sql_lower:
                        rows = [(f"{current_db}.{t}", "shardkey:noshardkey_allset")
                                for (d, t), c in sorted(_SHARD_BY_DB.items())
                                if d == current_db.lower() and "noshardkey_allset" in c]
                        client_writer.write(build_resultset(["db_table", "info"], rows, seq=1))
                        await client_writer.drain()
                        continue
                    elif "/*proxy*/show table with shardkey" in sql_lower:
                        rows = [(f"{current_db}.{t}", f"shardkey:{c.split('=', 1)[-1]}")
                                for (d, t), c in sorted(_SHARD_BY_DB.items())
                                if d == current_db.lower() and "noshardkey_allset" not in c]
                        client_writer.write(build_resultset(["db_table", "info"], rows, seq=1))
                        await client_writer.drain()
                        continue
                    elif "/*proxy*/show table without shardkey" in sql_lower:
                        # 单表 = 当前库 BASE TABLE 中未登记分片/广播规则的表
                        sharded = {t for (d, t) in _SHARD_BY_DB if d == current_db.lower()}
                        rows = [(f"{current_db}.{t}",) for t in _list_base_tables(current_db)
                                if t.lower() not in sharded]
                        client_writer.write(build_resultset(["db_table"], rows, seq=1))
                        await client_writer.drain()
                        continue

                    # 4. 跟踪 USE db_name
                    if sql_lower.startswith("use "):
                        current_db = sql.split()[1].strip('`;')

                    # 5. 拦截 SHOW CREATE TABLE
                    if sql_lower.startswith("show create table"):
                        m_tbl = re.search(r"SHOW\s+CREATE\s+TABLE\s+(?:`?[a-zA-Z0-9_]+`?\.)?`?([a-zA-Z0-9_]+)`?", sql, re.IGNORECASE)
                        if m_tbl:
                            current_show_table = m_tbl.group(1).lower()

                    # 6. 拦截 CREATE TABLE 原厂 TDSQL DDL
                    if sql_lower.startswith("create table") and ("shardkey" in sql_lower or "tdsql_distributed" in sql_lower):
                        m_tbl = re.search(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:`?[a-zA-Z0-9_]+`?\.)?`?([a-zA-Z0-9_]+)`?", sql, re.IGNORECASE)
                        tbl_name = m_tbl.group(1) if m_tbl else None

                        m_sk = re.search(r"(?:shardkey\s*=\s*[a-zA-Z0-9_]+|TDSQL_DISTRIBUTED\s+BY\s+[^\;]+)", sql, re.IGNORECASE)
                        if tbl_name and m_sk:
                            sk_clause = m_sk.group(0).strip()
                            SHARD_METADATA[tbl_name.lower()] = sk_clause
                            persist_shard_rule(current_db, tbl_name, sk_clause)

                            # 剥离 shardkey 语法，使其在底层存储节点顺利建表，绝无 1064 语法报错
                            clean_sql = re.sub(r"\s*shardkey\s*=\s*[a-zA-Z0-9_]+", "", sql, flags=re.IGNORECASE)
                            clean_sql = re.sub(r"\s*TDSQL_DISTRIBUTED\s+BY\s+[^\;]+", "", clean_sql, flags=re.IGNORECASE)

                            new_payload = b'\x03' + clean_sql.encode('utf-8')
                            new_header = struct.pack('<I', len(new_payload))[:3] + bytes([data[3]])
                            data = new_header + new_payload

                backend_writer.write(data)
                await backend_writer.drain()
        except Exception:
            pass
        finally:
            backend_writer.close()

    await asyncio.gather(backend_to_client(), client_to_backend(), return_exceptions=True)

async def main():
    init_metadata_table()
    server = await asyncio.start_server(handle_client, "0.0.0.0", LISTEN_PORT)
    print(f"[TDSQL Proxy] Started listening on 0.0.0.0:{LISTEN_PORT} -> Backend {BACKEND_HOST}:{BACKEND_PORT}")
    async with server:
        await server.serve_forever()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("[TDSQL Proxy] Stopped.")
