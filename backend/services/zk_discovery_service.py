"""G10 ZK 实例自动发现服务。

真实发现失败必须显式失败，绝不能以 Mock 结果伪装为真实集群清单。Mock 仅用于经部署
配置显式启用的开发联调，且调用方必须识别其来源并禁止写入实例形态权威源。
"""
import csv
import io
import json
import logging
import os
import random
import subprocess
from pathlib import Path

logger = logging.getLogger("tdsql.zk_discovery")


class ZKDiscoveryUnavailableError(RuntimeError):
    """真实 ZK 发现的前置条件或执行过程不可用。

    API 层将此类异常映射为 503；错误文本不得包含认证口令、命令行或脚本原始输出。
    """


class ZKDiscoveryService:
    """ZK 实例发现服务"""

    @staticmethod
    def is_zk_port_open(server_addr: str) -> bool:
        """快速探测 ZK connect string 中任一节点是否可达。"""
        import socket
        for endpoint in ZKDiscoveryService._split_servers(server_addr):
            try:
                host, port_text = endpoint.strip().rsplit(":", 1)
                port = int(port_text)
                if not host or not 1 <= port <= 65535:
                    continue
            except (AttributeError, ValueError):
                continue
            try:
                with socket.create_connection((host, port), timeout=2):
                    return True
            except OSError:
                continue
        return False

    @staticmethod
    def _split_servers(server_addr: str) -> list[str]:
        """Parse the comma-separated ZK candidates in deployment configuration."""
        return [endpoint.strip() for endpoint in str(server_addr or "").split(",") if endpoint.strip()]

    @staticmethod
    def is_real_discovery_runtime_supported() -> bool:
        """真实发现依赖 Linux 上的 bash 与 zkCli.sh。"""
        return os.name != "nt"

    @staticmethod
    def apply_endpoint_mapping(results: list[dict], host_mapping: dict[str, str] | None) -> list[dict]:
        """将 ZK 内网网关地址映射为 CheckSQL 实际可连接的地址。

        映射只替换 host，端口保持不变；同时处理主连接地址和 proxy_list，保证后续
        ``sync_instance_kinds`` 能与登记在公网/NAT 地址上的实例匹配。
        """
        mapping = {
            str(source).strip(): str(target).strip()
            for source, target in (host_mapping or {}).items()
            if str(source).strip() and str(target).strip()
        }
        if not mapping:
            logger.info("ZK_DISCOVERY_ENDPOINT_MAPPING mapping_rules=0 records=%s", len(results))
            return results

        def map_endpoint(endpoint: str) -> str:
            text = str(endpoint or "").strip()
            try:
                host, port = text.rsplit(":", 1)
            except ValueError:
                return text
            return f"{mapping.get(host, host)}:{port}"

        mapped_results = []
        primary_replacements = 0
        proxy_replacements = 0
        for result in results:
            item = dict(result)
            original_host = str(item.get("host", ""))
            item["host"] = mapping.get(original_host, item.get("host", ""))
            if item["host"] != original_host:
                primary_replacements += 1
            proxy_list = str(item.get("proxy_list", "") or "")
            if proxy_list:
                original_endpoints = [endpoint for endpoint in proxy_list.split(";") if endpoint.strip()]
                mapped_endpoints = [map_endpoint(endpoint) for endpoint in original_endpoints]
                proxy_replacements += sum(
                    old != new for old, new in zip(original_endpoints, mapped_endpoints)
                )
                item["proxy_list"] = ";".join(mapped_endpoints)
            mapped_results.append(item)
        logger.info(
            "ZK_DISCOVERY_ENDPOINT_MAPPING mapping_rules=%s records=%s primary_replacements=%s proxy_replacements=%s",
            len(mapping), len(results), primary_replacements, proxy_replacements,
        )
        return mapped_results

    def discover(
        self,
        zk_server: str,
        zk_auth_user: str,
        zk_auth_password: str,
        zk_root: str = "/tdsqlzk",
        zkcli_path: str = "/data/application/zookeeper/bin/zkCli.sh",
        proxy_mode: str = "random",
        default_database: str = "ALL",
        force_mock: bool = False,
        driver: str = "kazoo",
    ) -> list[dict]:
        """
        开始自动发现 TDSQL 实例。

        默认只执行真实发现。若运行环境、客户端、网络或脚本不可用，抛出
        :class:`ZKDiscoveryUnavailableError`，由 API 返回 503。Mock 必须由调用方显式
        指定，且返回结果带 ``is_mock=true``。
        """
        script_path = Path(__file__).parent.parent.parent / "deploy" / "tdsql_inventory.sh"
        if force_mock:
            logger.warning("ZK discovery is running in explicitly enabled Mock mode")
            return [
                {
                    "service_name": "Mock-TDSQL-Set-1",
                    "host": "192.0.2.10",
                    "port": 15005,
                    "user": "mock_user",
                    "password": "mock_password",
                    "database": default_database,
                    "status_code": "0",
                    "status_text": "运营中",
                    "instance_kind": "groupshard",
                    "instance_id": "group_mock_1",
                    "instance_type": "distributed",
                    "proxy_list": "192.0.2.10:15005",
                    "set_ids": ["set_mock_1"],
                    "is_mock": True,
                },
                {
                    "service_name": "Mock-TDSQL-Set-2",
                    "host": "192.0.2.11",
                    "port": 15006,
                    "user": "mock_user",
                    "password": "mock_password",
                    "database": default_database,
                    "status_code": "0",
                    "status_text": "运营中",
                    "instance_kind": "noshard",
                    "instance_id": "set_mock_2",
                    "instance_type": "centralized",
                    "proxy_list": "192.0.2.11:15006",
                    "set_ids": ["set_mock_2"],
                    "is_mock": True,
                },
                {
                    "service_name": "Mock-TDSQL-Set-3",
                    "host": "192.0.2.12",
                    "port": 15007,
                    "user": "mock_user",
                    "password": "mock_password",
                    "database": default_database,
                    "status_code": "1",
                    "status_text": "已隔离",
                    "instance_kind": "",
                    "instance_id": "",
                    "instance_type": None,
                    "proxy_list": "",
                    "set_ids": [],
                    "is_mock": True,
                }
            ]

        candidates = self._split_servers(zk_server)
        logger.info(
            "ZK_DISCOVERY_START driver=%s candidate_count=%s root=%s proxy_mode=%s",
            driver, len(candidates), zk_root, proxy_mode,
        )
        if len(candidates) > 1:
            # Do not pass a comma connect string to the inventory script: deployed
            # ZK client versions have shown incompatible behaviour with it. Probe
            # each member independently and return only a real successful result.
            failure_types: list[str] = []
            for position, candidate in enumerate(candidates, start=1):
                logger.info(
                    "ZK_DISCOVERY_CANDIDATE_START candidate=%s position=%s/%s driver=%s",
                    candidate, position, len(candidates), driver,
                )
                try:
                    results = self.discover(
                        zk_server=candidate,
                        zk_auth_user=zk_auth_user,
                        zk_auth_password=zk_auth_password,
                        zk_root=zk_root,
                        zkcli_path=zkcli_path,
                        proxy_mode=proxy_mode,
                        default_database=default_database,
                        driver=driver,
                    )
                    logger.info(
                        "ZK_DISCOVERY_CANDIDATE_SUCCESS candidate=%s position=%s/%s records=%s",
                        candidate, position, len(candidates), len(results),
                    )
                    return results
                except ZKDiscoveryUnavailableError as exc:
                    failure_types.append(type(exc.__cause__).__name__ if exc.__cause__ else type(exc).__name__)
                    logger.warning(
                        "ZK_DISCOVERY_CANDIDATE_FAILED candidate=%s position=%s/%s reason=%s",
                        candidate, position, len(candidates), str(exc),
                    )
            logger.error(
                "ZK_DISCOVERY_ALL_CANDIDATES_FAILED candidate_count=%s failure_types=%s",
                len(candidates), ",".join(failure_types) or "unknown",
            )
            raise ZKDiscoveryUnavailableError("所有 ZooKeeper 节点均无法完成真实实例发现")

        if not zk_auth_user or not zk_auth_password:
            raise ZKDiscoveryUnavailableError("ZK 认证配置不可用")
        candidates = self._split_servers(zk_server)
        if not candidates:
            raise ZKDiscoveryUnavailableError("ZooKeeper 服务地址无效")
        tcp_reachable = self.is_zk_port_open(candidates[0])
        logger.info(
            "ZK_DISCOVERY_TCP_PROBE candidate=%s reachable=%s timeout_seconds=2",
            candidates[0], tcp_reachable,
        )
        if not tcp_reachable:
            raise ZKDiscoveryUnavailableError("ZooKeeper 服务不可达")

        # 默认使用 Python 客户端：不依赖目标 TDSQL 节点的 zkCli/Java，也能在
        # Windows、Linux 和容器中以相同方式建立真实会话。Shell 仅为历史部署的
        # 兼容回退；驱动来自经过管理员保存并校验的运行配置。
        driver = str(driver or "kazoo").strip().lower()
        if driver == "kazoo":
            return self._discover_with_kazoo(
                zk_server=zk_server,
                zk_auth_user=zk_auth_user,
                zk_auth_password=zk_auth_password,
                zk_root=zk_root,
                proxy_mode=proxy_mode,
                default_database=default_database,
            )
        if driver != "shell":
            raise ZKDiscoveryUnavailableError("ZK 发现驱动配置无效")

        if not self.is_real_discovery_runtime_supported():
            raise ZKDiscoveryUnavailableError("Shell ZK 发现需要 Linux 运行环境")
        if not script_path.is_file():
            raise ZKDiscoveryUnavailableError("ZK 发现脚本不可用")
        zkcli = Path(zkcli_path)
        if not zkcli.is_file() or not os.access(zkcli, os.X_OK):
            raise ZKDiscoveryUnavailableError("ZK 客户端不可用或不可执行")

        logger.info(
            "ZK_DISCOVERY_SHELL_START candidate=%s root=%s zkcli_path=%s",
            zk_server, zk_root, zkcli_path,
        )
        cmd = [
            "bash", str(script_path),
            "--zk-server", zk_server,
            "--zk-root", zk_root,
            "--zkcli", zkcli_path,
            "--proxy-mode", proxy_mode,
            "--default-database", default_database,
            "--with-status",
            "--with-type",      # V1.5.1：取实例形态（规则适用域判定的权威源）
            "-q"  # 开启静默只输出 CSV
        ]

        env = os.environ.copy()
        # 不把认证口令放入命令行；脚本从环境读取后通过 zkCli 的标准输入认证。
        env["ZK_AUTH_USER"] = zk_auth_user
        env["ZK_AUTH_PASSWORD"] = zk_auth_password
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=180, env=env)
            if res.returncode != 0:
                logger.error("ZK_DISCOVERY_SHELL_FAILED candidate=%s exit_code=%s", zk_server, res.returncode)
                raise ZKDiscoveryUnavailableError("ZK 实例发现脚本执行失败")

            results = self.parse_csv(res.stdout)
            if not results:
                logger.warning("ZK_DISCOVERY_SHELL_EMPTY_RESULT candidate=%s", zk_server)
                raise ZKDiscoveryUnavailableError("ZK 实例发现未返回有效记录")
            for item in results:
                item["is_mock"] = False
            logger.info("ZK_DISCOVERY_SHELL_SUCCESS candidate=%s records=%s", zk_server, len(results))
            return results
        except subprocess.TimeoutExpired:
            logger.error("ZK_DISCOVERY_SHELL_TIMEOUT candidate=%s timeout_seconds=180", zk_server)
            raise ZKDiscoveryUnavailableError("ZK 实例发现执行超时")
        except ZKDiscoveryUnavailableError:
            raise
        except Exception as e:
            logger.error("zk_inventory execution failed: %s", type(e).__name__)
            raise ZKDiscoveryUnavailableError("ZK 实例发现执行异常") from e

    def _discover_with_kazoo(
        self,
        zk_server: str,
        zk_auth_user: str,
        zk_auth_password: str,
        zk_root: str,
        proxy_mode: str,
        default_database: str,
    ) -> list[dict]:
        """通过 Python ZK 客户端执行只读发现，避免非交互 zkCli 输出格式差异。"""
        try:
            from kazoo.client import KazooClient
        except ImportError as exc:
            raise ZKDiscoveryUnavailableError("Python ZooKeeper 客户端不可用") from exc

        client = None
        root = "/" + str(zk_root or "tdsqlzk").strip("/")
        stage = "client_create"
        status_text = {
            "0": "运营中", "1": "已隔离", "2": "未初始化", "-1": "删除中",
            "100": "垂直扩容中", "101": "回档中", "102": "水平扩容中",
        }

        def set_id_from_node(node_name: str) -> str:
            prefix = "set@"
            return node_name[len(prefix):] if node_name.startswith(prefix) else ""

        try:
            client = KazooClient(hosts=zk_server, timeout=10.0)
            stage = "session_start"
            logger.info("ZK_DISCOVERY_KAZOO_SESSION_START candidate=%s timeout_seconds=15", zk_server)
            client.start(timeout=15)
            logger.info("ZK_DISCOVERY_KAZOO_SESSION_CONNECTED candidate=%s", zk_server)
            stage = "digest_auth"
            client.add_auth("digest", f"{zk_auth_user}:{zk_auth_password}")
            logger.info("ZK_DISCOVERY_KAZOO_AUTH_SUBMITTED candidate=%s", zk_server)

            # (kind, instance_id, setrun parent path, representative set id, all set ids)
            # 分布式 group 不能只把代表 set 泄露给下游；代表 set 仅用于读取
            # setrun，完整 set_ids 是导入和慢 SQL 扫描的权威拓扑。
            records: list[tuple[str, str, str, str, list[str]]] = []
            stage = "read_centralized_sets"
            centralized_nodes = sorted(client.get_children(f"{root}/sets"))
            for node_name in centralized_nodes:
                set_id = set_id_from_node(node_name)
                if set_id:
                    records.append(("noshard", set_id, f"{root}/sets/{node_name}", set_id, [set_id]))

            stage = "read_root_groups"
            root_nodes = sorted(client.get_children(root))
            group_count = 0
            for group_name in root_nodes:
                if not group_name.startswith("group_"):
                    continue
                group_count += 1
                stage = "read_distributed_group_sets"
                set_nodes = sorted(client.get_children(f"{root}/{group_name}/sets"))
                set_ids = sorted({set_id_from_node(item) for item in set_nodes if set_id_from_node(item)})
                if set_ids:
                    set_id = set_ids[0]
                    records.append(("groupshard", group_name,
                                    f"{root}/{group_name}/sets/set@{set_id}", set_id, set_ids))
                else:
                    logger.warning(
                        "ZK_DISCOVERY_GROUP_WITHOUT_SETS candidate=%s instance=%s",
                        zk_server, group_name,
                    )
            logger.info(
                "ZK_DISCOVERY_KAZOO_STRUCTURE candidate=%s root_children=%s centralized_set_nodes=%s group_nodes=%s instance_candidates=%s",
                zk_server, len(root_nodes), len(centralized_nodes), group_count, len(records),
            )

            results: list[dict] = []
            skipped_unreadable = 0
            skipped_inactive = 0
            skipped_incomplete = 0
            for kind, instance_id, parent_path, set_id, set_ids in records:
                try:
                    stage = "read_setrun"
                    raw, _stat = client.get(f"{parent_path}/setrun@{set_id}")
                    setrun = json.loads(raw.decode("utf-8"))
                except Exception as exc:
                    skipped_unreadable += 1
                    logger.warning(
                        "ZK_DISCOVERY_KAZOO_SETRUN_UNREADABLE candidate=%s instance=%s error_type=%s",
                        zk_server, instance_id, type(exc).__name__,
                    )
                    continue
                if not isinstance(setrun, dict) or str(setrun.get("status", 0)) != "0":
                    skipped_inactive += 1
                    continue

                user = str(setrun.get("user") or "").strip()
                password = str(setrun.get("password") or "").strip()
                proxy_names = [
                    str(item.get("name") or "")
                    for item in (setrun.get("proxy") or [])
                    if isinstance(item, dict) and item.get("name")
                ]
                endpoints = []
                for proxy_name in proxy_names:
                    host, separator, port = proxy_name.rpartition("_")
                    if separator and host and port.isdigit():
                        endpoints.append(f"{host}:{port}")
                endpoints = sorted(set(endpoints))
                if not user or not password or not endpoints:
                    skipped_incomplete += 1
                    logger.warning(
                        "ZK_DISCOVERY_KAZOO_RECORD_INCOMPLETE candidate=%s instance=%s has_user=%s has_password=%s proxy_count=%s",
                        zk_server, instance_id, bool(user), bool(password), len(endpoints),
                    )
                    continue

                chosen_endpoint = endpoints[0] if proxy_mode == "first" else random.choice(endpoints)
                host, port_text = chosen_endpoint.rsplit(":", 1)
                results.append({
                    "service_name": instance_id,
                    "host": host,
                    "port": int(port_text),
                    "user": user,
                    "password": password,
                    "database": default_database,
                    "status_code": "0",
                    "status_text": status_text["0"],
                    "instance_kind": kind,
                    "instance_id": instance_id,
                    "instance_type": self._KIND_TO_TYPE[kind],
                    "proxy_list": ";".join(endpoints),
                    "set_ids": set_ids,
                    "is_mock": False,
                })

            logger.info(
                "ZK_DISCOVERY_KAZOO_RECORD_SUMMARY candidate=%s usable=%s skipped_unreadable=%s skipped_inactive=%s skipped_incomplete=%s",
                zk_server, len(results), skipped_unreadable, skipped_inactive, skipped_incomplete,
            )
            if not results:
                raise ZKDiscoveryUnavailableError("ZK 实例发现未返回有效记录")
            return results
        except ZKDiscoveryUnavailableError:
            logger.warning("ZK_DISCOVERY_KAZOO_STOPPED candidate=%s stage=%s", zk_server, stage)
            raise
        except Exception as exc:
            logger.error(
                "ZK_DISCOVERY_KAZOO_FAILED candidate=%s stage=%s error_type=%s",
                zk_server, stage, type(exc).__name__,
            )
            raise ZKDiscoveryUnavailableError("ZK 实例发现会话或读取失败") from exc
        finally:
            if client is not None:
                try:
                    client.stop()
                    client.close()
                except Exception:
                    logger.warning("ZK_DISCOVERY_KAZOO_CLEANUP_FAILED candidate=%s", zk_server)

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
            +--with-type    : ,instance_kind,instance_id,proxy_list,set_list         (12)

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
            # v1.6.0.1：完整 SET 列表是标准化导入的必需拓扑。旧 shell 输出没有
            # 第 12 列时，集中式可安全回退到自身 SET；分布式必须在预检阶段拒绝。
            raw_set_list = (row[11] if len(row) >= 12 else "") or ""
            item["set_ids"] = sorted({value.strip() for value in raw_set_list.split(";") if value.strip()})
            if not item["set_ids"] and item.get("instance_kind") == "noshard" and item.get("instance_id"):
                item["set_ids"] = [item["instance_id"]]
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
            logger.info("ZK_DISCOVERY_KIND_SYNC skipped=no_matchable_endpoints discovered_records=%s", len(discovered))
            return 0

        synced = 0
        ensure_db()
        conn = _get_connection()
        try:
            rows = conn.execute(
                "SELECT id, host, port FROM tdsql_connections").fetchall()
            logger.info(
                "ZK_DISCOVERY_KIND_SYNC_START discovered_records=%s matchable_endpoints=%s registered_connections=%s",
                len(discovered), len(index), len(rows),
            )
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
        logger.info("ZK_DISCOVERY_KIND_SYNC_COMPLETED synchronized=%s", synced)
        return synced

zk_discovery_service = ZKDiscoveryService()
