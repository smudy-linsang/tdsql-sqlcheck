# -*- coding: utf-8 -*-
"""
端到端集成测试脚本:
使用本地正在运行的 TDSQL 审核平台 (http://127.0.0.1:8000)
对新搭建的轻量 Docker TDSQL 靶场进行全流程实测验证:
1. 管理员身份登录平台
2. 测试靶场数据库连通性 (/api/v1/tdsql/test-connection)
3. 注册并保存分布式靶场实例 (/api/v1/tdsql/connections)
4. 激活实例连接并提取表元数据 (分片表、广播表、单表)
5. 执行结合元数据的深度 SQL 质量审核 (/api/v1/tdsql/audit/with-metadata)
6. 验证 ZooKeeper 自动发现服务 (/api/v1/tdsql/discover)
"""

import os
import sys
import requests
import json

sys.path.insert(0, os.path.abspath("."))
BASE_URL = "http://127.0.0.1:8000"

def run_integration_test():
    print("=" * 70)
    print("开始进行 TDSQL-SQLCheck 平台与本地 Docker TDSQL 靶场全链路联调实测")
    print("=" * 70)

    # 1. 登录平台 / 签发管理员凭证
    print("\n[步骤 1] 获取管理员会话凭证...")
    from backend.services.auth_service import auth_service, issue_token
    u = auth_service.get_user("admin")
    token = issue_token(u["username"], u["role"], u.get("token_version", 0))
    headers = {"Authorization": f"Bearer {token}"}
    print(f"  [OK] 凭证准备完毕: Bearer {token[:15]}... (token_version={u.get('token_version')})")

    # 2. 测试靶场连通性
    print("\n[步骤 2] 测试靶场连通性 (POST /api/v1/tdsql/test-connection)...")
    test_body = {
        "host": "127.0.0.1",
        "port": 13306,
        "user": "root",
        "password": "tdsql_test_2024",
        "database": "tdsql_demo_distributed",
        "monitor_host": "127.0.0.1",
        "monitor_port": 13306,
        "monitor_user": "root",
        "monitor_password": "tdsql_test_2024",
        "monitor_db": "tdsqlpcloud_monitor"
    }
    test_resp = requests.post(f"{BASE_URL}/api/v1/tdsql/test-connection", json=test_body, headers=headers)
    print(f"  HTTP 状态码: {test_resp.status_code}")
    if test_resp.status_code == 200:
        res = test_resp.json()
        print(f"  业务库连接: {'成功' if res.get('status') == 'connected' else '失败'}")
        print(f"  数据库版本: {res.get('server_version')}")
        print(f"  网络延迟:   {res.get('latency_ms')} ms")
        print(f"  监控库连接: {'成功' if res.get('monitor_status') == 'connected' else '失败'} (有效列数: {res.get('monitor_column_count')})")
    else:
        print(f"  错误信息: {test_resp.text}")

    # 3. 创建并保存分布式靶场实例
    print("\n[步骤 3] 创建并注册靶场实例 (POST /api/v1/tdsql/connections)...")
    conn_body = {
        "name": "TDSQL本地轻量分布式靶场",
        "host": "127.0.0.1",
        "port": 13306,
        "username": "root",
        "password": "tdsql_test_2024",
        "database": "tdsql_demo_distributed",
        "instance_type": "distributed",
        "set_list": "set_1782132369_1,set_1782132389_2",
        "monitor_host": "127.0.0.1",
        "monitor_port": 13306,
        "monitor_user": "root",
        "monitor_password": "tdsql_test_2024",
        "monitor_db": "tdsqlpcloud_monitor",
        "is_default": True
    }
    create_resp = requests.post(f"{BASE_URL}/api/v1/tdsql/connections", json=conn_body, headers=headers)
    print(f"  HTTP 状态码: {create_resp.status_code}")
    conn_id = None
    if create_resp.status_code in (200, 201):
        conn_data = create_resp.json()
        conn_id = conn_data.get("id")
        print(f"  [OK] 靶场实例创建成功！连接ID: {conn_id}，名称: {conn_data.get('name')}")
    else:
        print(f"  创建响应: {create_resp.text}")
        # 如果已存在，获取已有连接
        list_resp = requests.get(f"{BASE_URL}/api/v1/tdsql/connections", headers=headers)
        if list_resp.status_code == 200:
            for item in list_resp.json().get("connections", []):
                if item.get("name") == conn_body["name"]:
                    conn_id = item.get("id")
                    print(f"  [OK] 复用已有靶场连接ID: {conn_id}")
                    break

    # 4. 激活实例并提取元数据
    if conn_id:
        print(f"\n[步骤 4] 激活连接并提取元数据 (POST /api/v1/tdsql/connections/{conn_id}/connect)...")
        act_resp = requests.post(f"{BASE_URL}/api/v1/tdsql/connections/{conn_id}/connect", headers=headers)
        print(f"  激活状态码: {act_resp.status_code}")
        
        tables_resp = requests.get(f"{BASE_URL}/api/v1/tdsql/tables?connection_id={conn_id}", headers=headers)
        if tables_resp.status_code == 200:
            tables = tables_resp.json().get("tables", [])
            print(f"  [OK] 成功从靶场提取到 {len(tables)} 张数据表:")
            for t in tables:
                print(f"    - 表名: {t.get('TABLE_NAME'):24s} | 引擎: {t.get('ENGINE')} | 行数: {t.get('TABLE_ROWS')}")

    # 5. 结合元数据进行分布式 SQL 审核
    print("\n[步骤 5] 结合靶场元数据审核 SQL (POST /api/v1/tdsql/audit/with-metadata)...")
    # 测试一条未带分片键 user_id 的查询，期望命中 R020
    test_sql_1 = "SELECT * FROM big_audit_trail WHERE action = 'LOGIN'"
    audit_resp = requests.post(f"{BASE_URL}/api/v1/tdsql/audit/with-metadata", json={
        "sql": test_sql_1,
        "connection_id": conn_id
    }, headers=headers)
    if audit_resp.status_code == 200:
        ares = audit_resp.json()
        table_meta = ares.get("table_metadata", {}).get("big_audit_trail", {})
        print(f"  目标表: big_audit_trail")
        print(f"  元数据提取: is_shard_table={table_meta.get('is_shard_table')}, shard_key={table_meta.get('shard_key')}")
        violations = ares.get("audit_result", {}).get("violations", [])
        v_ids = [v.get("rule_id") for v in violations]
        print(f"  审核命中违规规则: {v_ids}")
        if "R020" in v_ids:
            print("  [OK] 成功依据靶场提取到的 ShardKey 准确触发 R020(分片表查询必须带分片键)！")

    # 6. 测试 ZooKeeper 自动发现
    print("\n[步骤 6] 测试 ZooKeeper 靶场自动发现...")
    # 保存 ZK 配置
    zk_cfg_body = {
        "servers": "127.0.0.1:2181",
        "root_path": "/tdsqlzk",
        "driver": "kazoo",
        "auth_username": "none",
        "auth_password": "",
        "default_database": "tdsql_demo_distributed"
    }
    zk_save_resp = requests.put(f"{BASE_URL}/api/v1/tdsql/discover/config", json=zk_cfg_body, headers=headers)
    print(f"  ZK 配置保存状态码: {zk_save_resp.status_code}")
    
    # 触发发现
    zk_disc_resp = requests.post(f"{BASE_URL}/api/v1/tdsql/discover", headers=headers)
    print(f"  ZK 实例发现状态码: {zk_disc_resp.status_code}")
    if zk_disc_resp.status_code == 200:
        disc_data = zk_disc_resp.json()
        items = disc_data.get("items", [])
        print(f"  [OK] ZooKeeper 自动发现成功找到 {len(items)} 个实例:")
        for itm in items:
            print(f"    - 实例ID: {itm.get('instance_id')} | 类型: {itm.get('instance_type')} | 名称: {itm.get('instance_name')} | 地址: {itm.get('host')}:{itm.get('port')}")
    else:
        print(f"  ZK 发现响应: {zk_disc_resp.text}")

    print("\n" + "=" * 70)
    print("全链路集成联调测试完成！")
    print("=" * 70)

if __name__ == "__main__":
    run_integration_test()
