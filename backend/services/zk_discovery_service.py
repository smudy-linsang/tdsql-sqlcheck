"""G10 ZK 实例自动发现服务

调用 deploy/tdsql_inventory.sh 脚本，从 ZooKeeper 的 /tdsqlzk 节点自动发现所有 TDSQL 实例，
并对无法连接或本地沙箱环境提供优雅的 Mock/回退机制，确保内网 Q 智能体 UAT 测试正常。
"""
import csv
import io
import logging
import os
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger("tdsql.zk_discovery")


class ZKDiscoveryService:
    """ZK 实例发现服务"""

    @staticmethod
    def is_zk_port_open(server_addr: str) -> bool:
        """快速探测 ZK 端口是否打开"""
        import socket
        try:
            host, port = server_addr.split(":")
            port = int(port)
        except ValueError:
            return False

        try:
            with socket.create_connection((host, port), timeout=2):
                return True
        except Exception:
            return False

    def discover(
        self,
        zk_server: str,
        zk_auth_user: str,
        zk_auth_password: str,
        zk_root: str = "/tdsqlzk",
        zkcli_path: str = "/data/application/zookeeper/bin/zkCli.sh",
        proxy_mode: str = "random",
        default_database: str = "ALL",
        force_mock: bool = False
    ) -> list[dict]:
        """
        开始自动发现 TDSQL 实例。

        在 Linux 且 ZK 可达时真实运行；其余情况（Windows / ZK 不通）回退为 Mock 列表。
        """
        # 1) 判断是否强制 Mock 或处于 Windows/无 ZKCLI 环境
        script_path = Path(__file__).parent.parent.parent / "deploy" / "tdsql_inventory.sh"
        use_mock = force_mock or os.name == "nt" or not script_path.exists()

        if not use_mock:
            # 2) 探测 ZK 端口是否可用，不可用也降级为 Mock
            if not self.is_zk_port_open(zk_server):
                logger.warning(f"ZooKeeper {zk_server} 端口不可达，使用 Mock 列表")
                use_mock = True

        if use_mock:
            logger.info("采用 Mock 实例发现列表返回")
            return [
                {
                    "service_name": "TDSQL-Set-1(合约库)",
                    "host": "127.0.0.1",
                    "port": 15005,
                    "user": "tdsqlsys_normal",
                    "password": "mock_password_set1",
                    "database": default_database,
                    "status_code": "0",
                    "status_text": "运营中",
                    # V1.5.1 Mock 形态字段（仅供 Windows/开发环境前端联调）
                    "instance_kind": "groupshard",
                    "instance_id": "group_mock_1",
                    "instance_type": "distributed",
                    "proxy_list": "127.0.0.1:15005",
                },
                {
                    "service_name": "TDSQL-Set-2(交易库)",
                    "host": "127.0.0.1",
                    "port": 15006,
                    "user": "tdsqlsys_normal",
                    "password": "mock_password_set2",
                    "database": default_database,
                    "status_code": "0",
                    "status_text": "运营中",
                    "instance_kind": "noshard",
                    "instance_id": "set_mock_2",
                    "instance_type": "centralized",
                    "proxy_list": "127.0.0.1:15006",
                },
                {
                    "service_name": "TDSQL-Set-3(已隔离)",
                    "host": "127.0.0.1",
                    "port": 15007,
                    "user": "tdsqlsys_normal",
                    "password": "mock_password_set3",
                    "database": default_database,
                    "status_code": "1",
                    "status_text": "已隔离",
                    "instance_kind": "",
                    "instance_id": "",
                    "instance_type": None,
                    "proxy_list": "",
                }
            ]

        # 3) 真实物理执行
        logger.info(f"开始在物理节点执行 ZK 实例扫描: server={zk_server}, root={zk_root}")
        cmd = [
            "bash", str(script_path),
            "--zk-server", zk_server,
            "--zk-auth", f"{zk_auth_user}:{zk_auth_password}",
            "--zk-root", zk_root,
            "--zkcli", zkcli_path,
            "--proxy-mode", proxy_mode,
            "--default-database", default_database,
            "--with-status",
            "--with-type",      # V1.5.1：取实例形态（规则适用域判定的权威源）
            "-q"  # 开启静默只输出 CSV
        ]

        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
            if res.returncode != 0:
                logger.error(f"zk_inventory 运行失败 (code={res.returncode}): {res.stderr}")
                raise RuntimeError(f"实例发现失败: {res.stderr or '未知错误'}")
            
            # 解析 CSV 输出
            return self.parse_csv(res.stdout)
        except subprocess.TimeoutExpired:
            logger.error("zk_inventory 运行超时 (180s)")
            raise RuntimeError("实例发现运行超时 (180s)")
        except Exception as e:
            logger.error(f"zk_inventory 运行异常: {e}")
            raise RuntimeError(f"实例发现运行异常: {e}")

    # kind → 本系统业务语义的映射。存原始 kind、映射在代码里做，
    # 便于与赤兔/ZK 对账；TDSQL 将来若增加形态，只改这张表。
    _KIND_TO_TYPE = {
        "noshard":    "centralized",   # 单 SET 实例 = 集中式
        "groupshard": "distributed",   # group 下多 SET = 分布式
    }

    def parse_csv(self, csv_content: str) -> list[dict]:
        """解析发现导出的 CSV。

        列布局（新列一律追加在末尾，保证旧消费方按前 N 列取值不受影响）：
            base            : service_name,host,port,user,password,database          (6)
            +--with-status  : ,status_code,status_text                               (8)
            +--with-type    : ,instance_kind,instance_id,proxy_list                  (11)

        V1.5.1：instance_kind 是实例类型判定的权威依据。
        脚本内部一直有这个字段，此前未导出。
        """
        results = []
        f = io.StringIO(csv_content.strip())
        for row in csv.reader(f):
            if not row or row[0].startswith("#"):
                continue
            if len(row) < 6:
                continue

            item = {
                "service_name": row[0],
                "host": row[1],
                "port": int(row[2]) if row[2].isdigit() else 15001,
                "user": row[3],
                "password": row[4],
                "database": row[5],
                "status_code": "0",
                "status_text": "运营中",
            }
            # 状态列（--with-status）
            if len(row) >= 8:
                item["status_code"] = row[6]
                item["status_text"] = row[7]
            # 形态列（--with-type）
            if len(row) >= 11:
                kind = (row[8] or "").strip()
                item["instance_kind"] = kind
                item["instance_id"] = (row[9] or "").strip()
                item["proxy_list"] = (row[10] or "").strip()
                item["instance_type"] = self._KIND_TO_TYPE.get(kind)
                if kind and item["instance_type"] is None:
                    # 未知形态不得静默映射成某一类——凭假设给结论正是
                    # 本次事故的成因。不给结论 + 告警，判定下沉至声明值。
                    logger.warning(
                        f"ZK 返回未知实例形态 kind={kind!r} "
                        f"(instance_id={item['instance_id']})，本条不参与类型判定")
            results.append(item)
        return results

    def sync_instance_kinds(self, discovered: list[dict]) -> int:
        """把 ZK 发现的实例形态回写到已注册实例（V1.5.1）。

        匹配规则：已注册实例的 host:port ∈ 该 ZK 实例的 proxy_list 全集。
        不用"等于 CSV 里的 host:port"—— 脚本按 --proxy-mode random 随机选一个
        网关输出，而系统里登记的可能是同实例的另一个网关
        （如 10.206.0.8:15002 vs 10.206.0.4:15002），只比选中项会漏配。

        Returns: 成功同步的实例数
        """
        from datetime import datetime
        from backend.services.database import _get_connection, ensure_db

        # 构建 "host:port" → (kind, instance_id) 索引
        index = {}
        for d in discovered:
            kind = d.get("instance_kind")
            if not kind:
                continue
            endpoints = [e.strip() for e in (d.get("proxy_list") or "").split(";") if e.strip()]
            # proxy_list 为空时退回 CSV 里选中的那一个
            if not endpoints:
                endpoints = [f"{d.get('host')}:{d.get('port')}"]
            for ep in endpoints:
                index[ep] = (kind, d.get("instance_id") or "")

        if not index:
            return 0

        synced = 0
        ensure_db()
        conn = _get_connection()
        try:
            rows = conn.execute(
                "SELECT id, host, port FROM tdsql_connections").fetchall()
            now = datetime.now().isoformat()
            for r in rows:
                r = dict(r)
                hit = index.get(f"{r.get('host')}:{r.get('port')}")
                if not hit:
                    continue
                kind, inst_id = hit
                conn.execute(
                    "UPDATE tdsql_connections SET zk_instance_kind = ?, "
                    "zk_instance_id = ?, zk_synced_at = ? WHERE id = ?",
                    (kind, inst_id, now, r["id"]))
                synced += 1
            conn.commit()
        finally:
            conn.close()

        if synced:
            from backend.services.instance_type_service import instance_type_service
            instance_type_service.invalidate()   # 全量失效，本进程立即生效
            logger.info(f"ZK 实例形态已同步 {synced} 个实例")
        return synced

    def register_discovered(self, connection_id: str, inst: dict) -> str:
        """
        将自动发现的实例批量写入数据库 (tdsql_connections)。
        与 connection_registry 中的保存逻辑对齐。
        """
        from backend.services.connection_registry import registry
        from backend.services.database import _get_connection, _execute_sql
        
        # 密码 AES 加密
        from backend.services.security_service import encrypt_password
        pwd_encrypted = encrypt_password(inst["password"])

        conn = _get_connection()
        try:
            # 检查连接名是否已存在
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id FROM tdsql_connections WHERE id = %s",
                (connection_id,)
            )
            exists = cursor.fetchone()

            if exists:
                # 更新
                _execute_sql(conn, """
                    UPDATE tdsql_connections 
                    SET host=?, port=?, username=?, password_encrypted=?, `database`=?,
                        name=?, updated_at=CURRENT_TIMESTAMP
                    WHERE id=?
                """, (
                    inst["host"], inst["port"], inst["user"], pwd_encrypted,
                    inst["database"], inst["service_name"], connection_id
                ))
            else:
                # 插入
                _execute_sql(conn, """
                    INSERT INTO tdsql_connections 
                    (id, host, port, username, password_encrypted, `database`, name, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """, (
                    connection_id, inst["host"], inst["port"], inst["user"], pwd_encrypted,
                    inst["database"], inst["service_name"]
                ))
            conn.commit()
            return connection_id
        finally:
            conn.close()


zk_discovery_service = ZKDiscoveryService()
