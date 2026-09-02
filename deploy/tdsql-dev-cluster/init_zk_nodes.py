# -*- coding: utf-8 -*-
"""
初始化 TDSQL ZooKeeper /tdsqlzk 拓扑节点树
为 SQLCheck 平台的 ZK 实例自动发现功能提供真实、规范的节点数据
"""

import json
import time
from kazoo.client import KazooClient

def init_zk():
    zk_server = "127.0.0.1:2181"
    root_path = "/tdsqlzk"

    print(f"[ZK Init] 正在连接 ZooKeeper: {zk_server} ...")
    zk = KazooClient(hosts=zk_server, timeout=10.0)
    zk.start()

    print(f"[ZK Init] 连接成功，正在初始化根路径: {root_path} ...")
    zk.ensure_path(root_path)

    # 1. 构造分布式集群实例 (group_tdsql_dev_1)
    # 包含两个 SET (set_1782132369_1 与 set_1782132389_2)
    group_name = "group_tdsql_dev_1"
    dist_sets_path = f"{root_path}/{group_name}/sets"
    zk.ensure_path(dist_sets_path)

    set_ids = ["set_1782132369_1", "set_1782132389_2"]
    for s_id in set_ids:
        set_node_path = f"{dist_sets_path}/set@{s_id}"
        zk.ensure_path(set_node_path)
        
        # 写入 setrun 节点数据
        setrun_path = f"{set_node_path}/setrun@{s_id}"
        setrun_data = {
            "status": "0",  # 0 表示正常激活
            "name": "TDSQL分布式微集群靶场",
            "alias": "TDSQL-Dist-Dev",
            "comment": "用于SQL审核平台联调自测的分布式靶场",
            "user": "root",
            "password": "tdsql_test_2024",
            "proxy": [
                {"name": "127.0.0.1_13306"}
            ],
            "ip": "127.0.0.1",
            "port": 13306,
            "shard_type": "distributed"
        }
        raw_bytes = json.dumps(setrun_data, ensure_ascii=False).encode("utf-8")
        if zk.exists(setrun_path):
            zk.set(setrun_path, raw_bytes)
        else:
            zk.create(setrun_path, raw_bytes)
        print(f"  [+] 分布式节点写入: {setrun_path}")

    # 2. 构造集中式实例 (set_central_1)
    cent_sets_path = f"{root_path}/sets"
    zk.ensure_path(cent_sets_path)
    cent_set_id = "set_central_1"
    cent_node_path = f"{cent_sets_path}/set@{cent_set_id}"
    zk.ensure_path(cent_node_path)

    cent_setrun_path = f"{cent_node_path}/setrun@{cent_set_id}"
    cent_setrun_data = {
        "status": "0",
        "name": "TDSQL集中式测试实例",
        "alias": "TDSQL-Cent-Dev",
        "comment": "用于集中式单机模式审核自测",
        "user": "root",
        "password": "tdsql_test_2024",
        "proxy": [
            {"name": "127.0.0.1_13306"}
        ],
        "ip": "127.0.0.1",
        "port": 13306,
        "shard_type": "centralized"
    }
    raw_bytes = json.dumps(cent_setrun_data, ensure_ascii=False).encode("utf-8")
    if zk.exists(cent_setrun_path):
        zk.set(cent_setrun_path, raw_bytes)
    else:
        zk.create(cent_setrun_path, raw_bytes)
    print(f"  [+] 集中式节点写入: {cent_setrun_path}")

    # 验证读取
    print("\n[ZK Check] 验证读取当前 /tdsqlzk 树结构:")
    children = zk.get_children(root_path)
    print(f"  /tdsqlzk 子节点: {children}")
    dist_children = zk.get_children(dist_sets_path)
    print(f"  /tdsqlzk/{group_name}/sets 子节点: {dist_children}")
    cent_children = zk.get_children(cent_sets_path)
    print(f"  /tdsqlzk/sets 子节点: {cent_children}")

    zk.stop()
    print("\n[OK] ZooKeeper 靶场节点拓扑初始化成功！")

if __name__ == "__main__":
    init_zk()
