# TDSQL-SQLCheck ZooKeeper 真实环境测试手册

> 适用版本：v1.5.2.4 及包含 `deploy/tdsql_inventory.sh`、`ZKDiscoveryService` 的后续版本
> 适用人员：具备集群内网登录权限的测试、运维或 DBA 人员
> 测试性质：ZooKeeper 只读发现验证；默认不修改 ZooKeeper、TDSQL 或应用数据库

## 1. 目的与边界

本手册验证 TDSQL-SQLCheck 的 ZK 自动发现链路能否在**真实、受控的内网环境**中完成：

```text
健康 ZK 节点
  -> zkCli 只读访问 /tdsqlzk
  -> deploy/tdsql_inventory.sh 解析 setrun
  -> ZKDiscoveryService 解析 CSV 并映射实例形态
```

验证范围包括：集群角色、客户端连通性、认证后的只读目录访问、实例清单生成、`noshard` / `groupshard` 形态解析和 Python 服务层结果。不包含创建、删除、修改 ZK 节点，不包含启动/停止 ZooKeeper，也不包含业务库 DDL/DML。

`POST /api/v1/tdsql/discover` 虽然会执行同一发现链路，但它会尝试把发现到的形态同步回应用数据库中已经注册的连接。因此它是**应用数据写入操作**，不属于本手册默认的只读测试；仅可在隔离的 UAT 应用库中、完成备份和审批后另行执行。

## 2. 安全规则

1. 只从集群内网、健康 ZK 节点或已获准的管理跳板执行；不要为测试开放公网 2118 或 SSH 端口。
2. 不执行 `zkServer.sh start/stop/restart`，不执行 `create`、`set`、`delete`、`rmr` 等 ZK 写命令，也不手工修复任何集群节点。
3. 认证口令只通过终端的静默输入读入变量，不写进命令行、Shell 历史、截图、日志、测试报告或 Git 仓库。
4. 清单脚本的 CSV 含有发现到的 TDSQL 连接账户和口令。它只能落在当前受控主机的权限为 `600` 的临时文件中；不得 `cat`、上传、贴图或提交该文件。
5. 所有证据只记录脱敏统计：节点角色、退出码、发现数量、状态码分布、实例形态分布和断言结果；不记录 IP、端口、实例 ID、数据库账户或口令。

## 3. 前置条件

在任一已确认健康的 ZK 节点上准备：

- Linux Shell，且当前账号可运行 ZK 客户端；
- 项目工作副本，版本应与待验收版本一致；
- `bash`、`python3`、`awk`、`grep`、`ss`；
- ZooKeeper 客户端 `zkCli.sh` 和同目录的 `zkServer.sh`；
- 经授权的 ZK 认证用户名和口令；
- 一个可写、受控的临时目录，例如 `/tmp`。

不要在 Windows 上直接验证 `ZKDiscoveryService.discover(..., force_mock=False)`：当前实现检测到 Windows (`os.name == "nt"`) 时会有意回退为 Mock，不能证明物理 ZK 链路。

### 3.1 建立本次测试变量

以下变量均使用占位路径。先按现场实际情况替换，**不要把认证口令替换进脚本文字**。

```bash
export REPO_ROOT=/opt/tdsql-sqlcheck                 # 项目工作副本的绝对路径
export ZKCLI_PATH=/data/application/zookeeper/bin/zkCli.sh
export ZK_SERVER=127.0.0.1:2118                     # 在 ZK 本机测试时优先使用本地回环地址
export ZK_ROOT=/tdsqlzk

test -f "$REPO_ROOT/deploy/tdsql_inventory.sh"
test -x "$ZKCLI_PATH"
command -v python3

umask 077
export ARTIFACT_DIR="$(mktemp -d /tmp/tdsql-zk-test.XXXXXX)"
printf 'artifact_dir=%s\n' "$ARTIFACT_DIR"
```

若客户端路径未知，可先只读定位：

```bash
find /data /opt /usr/local -type f -name zkCli.sh 2>/dev/null
```

确认项目版本和工作区无意外改动：

```bash
git -C "$REPO_ROOT" rev-parse --short HEAD
git -C "$REPO_ROOT" status --short
```

再静默读取认证信息。下面命令不会回显口令；输入完成后不要打印这些变量：

```bash
read -r -p 'ZK auth user: ' ZK_AUTH_USER
read -r -s -p 'ZK auth password: ' ZK_AUTH_PASSWORD
printf '\n'
export ZK_AUTH_USER ZK_AUTH_PASSWORD
```

## 4. 测试用例与步骤

### ZK-01：确认节点身份、监听与角色

目的：确认测试点是健康节点，且集群具有多数派。对每一台 ZK 节点分别执行，仅采集状态，不做服务操作。

```bash
ZK_HOME="$(dirname "$(dirname "$ZKCLI_PATH")")"
"$ZK_HOME/bin/zkServer.sh" status
ss -lntp | grep -E ':(2118|2181|2288|2888)\b' || true
grep -nE '^(clientPort|secureClientPort|clientPortAddress|authProvider|requireClientAuthScheme|ssl)' \
  "$ZK_HOME/conf/zoo.cfg" || true
```

通过标准：

- 三节点集群正常时应看到一个 `Mode: leader` 和两个 `Mode: follower`；
- 至少两个成员在线才能维持多数派，但少于三台属于降级，不应把结果记为“全健康”；
- 测试节点的客户端端口应有监听；`clientPort` 必须与 `ZK_SERVER` 对应；
- 若状态命令显示服务未运行、端口无监听或角色未知，停止后续发现测试并交由集群运维按既定 SOP 处理。

### ZK-02：认证后的只读目录访问

目的：验证客户端会话、认证命令和核心目录读取。命令通过标准输入发送给 `zkCli.sh`，不会把口令写入 Shell 历史。

```bash
{
  printf 'addauth digest %s:%s\n' "$ZK_AUTH_USER" "$ZK_AUTH_PASSWORD"
  printf 'ls %s\n' "$ZK_ROOT"
  printf 'ls %s/sets\n' "$ZK_ROOT"
  printf 'getAcl %s\n' "$ZK_ROOT"
  printf 'quit\n'
} | "$ZKCLI_PATH" -server "$ZK_SERVER" \
  >"$ARTIFACT_DIR/zkcli-readonly.log" 2>&1

grep -E 'SyncConnected|ConnectionLoss|NoAuth|KeeperErrorCode|Mode:' \
  "$ARTIFACT_DIR/zkcli-readonly.log" || true
```

通过标准：出现 `SyncConnected`，且 `ls /tdsqlzk`、`ls /tdsqlzk/sets` 均有有效节点列表；日志中不得出现 `ConnectionLoss`、`NoAuth` 或其他 `KeeperErrorCode`。

注意：若目录 ACL 本身允许匿名读取，单靠 `ls` 成功不能证明 ACL 强制了认证；本用例的结论是“该认证信息已被客户端使用且当前账号具备所需读权限”。`getAcl` 输出只用于由授权人员核对权限策略，不应外传。

### ZK-03：主从一致性抽查

目的：排除只在单节点可见的临时异常。选一台 leader 和一台 follower 各执行一次 ZK-02，分别使用本机的 `127.0.0.1:2118`。

比较两份日志时，只记录以下脱敏结论：两端是否 `SyncConnected`、根目录是否可读、`sets` 是否非空。两端均成功才通过；一端失败时，先记录节点角色和错误码，再停止测试，不用跨网访问其他节点来绕过问题。

### ZK-04：真实清单脚本（全部状态）

目的：验证 `deploy/tdsql_inventory.sh` 从 ZK 读取根目录、`sets`、各 `setrun` 并生成 11 列清单。该清单包含下游 TDSQL 凭据，必须保存为受限文件。

```bash
export INVENTORY_ALL="$ARTIFACT_DIR/inventory-all.csv"
export INVENTORY_ALL_ERR="$ARTIFACT_DIR/inventory-all.stderr"

bash "$REPO_ROOT/deploy/tdsql_inventory.sh" \
  --zk-server "$ZK_SERVER" \
  --zkcli "$ZKCLI_PATH" \
  --zk-root "$ZK_ROOT" \
  --status-filter all \
  --with-status \
  --with-type \
  --proxy-mode first \
  --default-database ALL \
  -q \
  >"$INVENTORY_ALL" 2>"$INVENTORY_ALL_ERR"
printf 'inventory_all_exit=%s\n' "$?"
```

预期退出码为 `0`。请勿打开 CSV 内容；用下列脱敏校验器检查结构和分布：

```bash
python3 - "$INVENTORY_ALL" <<'PY'
import collections
import csv
import sys

path = sys.argv[1]
rows = []
with open(path, newline='', encoding='utf-8') as fp:
    for row in csv.reader(fp):
        if row and not row[0].startswith('#'):
            rows.append(row)

assert rows, '未发现任何实例记录'
assert all(len(row) == 11 for row in rows), '输出不是预期的 11 列格式'
allowed_kinds = {'noshard', 'groupshard'}
for row in rows:
    service_name, host, port, user, password, database, status, text, kind, instance_id, proxies = row
    assert host and port.isdigit() and user and password and database
    assert kind in allowed_kinds, f'未知实例形态: {kind!r}'
    assert instance_id and proxies
    assert f'{host}:{port}' in proxies.split(';'), '所选 proxy 不在 proxy_list 中'

print('record_count=', len(rows))
print('kind_counts=', dict(sorted(collections.Counter(row[8] for row in rows).items())))
print('status_counts=', dict(sorted(collections.Counter(row[6] for row in rows).items())))
print('schema_and_proxy_membership=passed')
PY
```

通过标准：脚本退出 `0`；记录数大于零；每条记录均为 11 列；形态只能是 `noshard` 或 `groupshard`；所选 `host:port` 必须包含在该条记录的 `proxy_list` 中。

### ZK-05：运行中实例过滤与确定性选择

目的：验证默认仅保留运行中实例，以及 `first` 选择策略可重复。该输出同样受限。

```bash
export INVENTORY_RUNNING="$ARTIFACT_DIR/inventory-running.csv"

bash "$REPO_ROOT/deploy/tdsql_inventory.sh" \
  --zk-server "$ZK_SERVER" \
  --zkcli "$ZKCLI_PATH" \
  --zk-root "$ZK_ROOT" \
  --status-filter 0 \
  --with-status \
  --with-type \
  --proxy-mode first \
  --default-database ALL \
  -q \
  >"$INVENTORY_RUNNING" 2>"$ARTIFACT_DIR/inventory-running.stderr"
printf 'inventory_running_exit=%s\n' "$?"

python3 - "$INVENTORY_RUNNING" <<'PY'
import csv
import sys
with open(sys.argv[1], newline='', encoding='utf-8') as fp:
    rows = [r for r in csv.reader(fp) if r and not r[0].startswith('#')]
assert rows, '运行中清单为空'
assert all(len(r) == 11 and r[6] == '0' for r in rows), '发现非运行中记录'
print('running_record_count=', len(rows))
print('status_filter_0=passed')
PY
```

通过标准：退出码为 `0`，记录数大于零，所有输出行的 `status_code` 为 `0`。`inventory-running.csv` 的记录数应小于或等于 ZK-04 的全量清单。

### ZK-06：Python 服务层物理发现与形态映射

目的：验证项目的 `ZKDiscoveryService` 调用真实脚本后正确解析 CSV，并把 ZK 原始形态映射为应用语义。

此用例必须在 Linux 上运行。它只读取 ZK 和在进程内处理结果，不调用 `sync_instance_kinds`，不写应用数据库。

```bash
export PYTHONPATH="$REPO_ROOT"

python3 - <<'PY'
import collections
import os
from backend.services.zk_discovery_service import ZKDiscoveryService

assert os.name != 'nt', 'Windows 会回退 Mock，不能用于物理发现验证'
items = ZKDiscoveryService().discover(
    zk_server=os.environ['ZK_SERVER'],
    zk_auth_user=os.environ['ZK_AUTH_USER'],
    zk_auth_password=os.environ['ZK_AUTH_PASSWORD'],
    zk_root=os.environ['ZK_ROOT'],
    zkcli_path=os.environ['ZKCLI_PATH'],
    proxy_mode='first',
    default_database='ALL',
    force_mock=False,
)

assert items, '服务层未返回实例'
for item in items:
    assert item['password'] != 'mock_password_set1', '错误回退到了 Mock'
    assert item['host'] and item['port'] and item['user'] and item['password']
    assert item['instance_id'] and item['proxy_list']
    if item['instance_kind'] == 'noshard':
        assert item['instance_type'] == 'centralized'
    elif item['instance_kind'] == 'groupshard':
        assert item['instance_type'] == 'distributed'
    else:
        raise AssertionError(f"未知实例形态: {item['instance_kind']!r}")

print('service_record_count=', len(items))
print('service_type_counts=', dict(sorted(
    collections.Counter(item['instance_type'] for item in items).items())))
print('physical_discovery_and_type_mapping=passed')
PY
```

通过标准：输出 `physical_discovery_and_type_mapping=passed`，服务层记录数大于零，且应与 ZK-05 的运行中记录数一致。映射规则固定为：`noshard -> centralized`、`groupshard -> distributed`。

### ZK-07：可选的应用 API 验证（隔离 UAT 专用）

只有在满足以下全部条件时才执行：

- 使用隔离的 UAT 应用数据库，或已经明确批准 `tdsql_connections` 的形态字段会被同步更新；
- 已备份/记录本次涉及的应用连接数据；
- HTTP 响应体不会写入终端、访问日志、CI 日志或截图，因为响应中带有发现到的连接凭据；
- 测试后会核对应用数据库写入的 `zk_instance_kind`、`zk_instance_id`、`zk_synced_at`。

`POST /api/v1/tdsql/discover` 成功返回只说明 API 调用完成；还应检查：返回的发现数量与 ZK-06 一致、已注册连接仅在其 `host:port` 位于 `proxy_list` 时被同步、`noshard`/`groupshard` 没有被反向映射。生产只读测试不执行本项。

## 5. 结果判定

| 级别 | 判定条件 | 结论 |
| --- | --- | --- |
| 通过 | ZK-01 健康；ZK-02/03 读目录成功；ZK-04/05/06 全部通过 | 真实 ZK 发现链路通过 |
| 条件通过 | 发现链路通过，但集群只有多数派在线、未满足全三节点健康 | 功能通过，集群健康性需单列整改 |
| 不通过 | 出现 `ConnectionLoss`、`NoAuth`、清单为空、字段/形态断言失败，或服务层回退 Mock | 不得以 Mock 或单一 TCP 连通替代真实通过 |
| 未执行 | 未在集群内网/Linux 真实节点执行 | 不得标记为已通过 |

## 6. 常见失败定位

| 现象 | 常见原因 | 安全处置 |
| --- | --- | --- |
| `ConnectionLoss` / 一直 `CONNECTING` | 地址指向错误、跨网路由不通、目标节点未运行、TCP 端口虽通但不是有效 ZK 会话 | 回到 ZK-01，在目标节点本机用 `127.0.0.1` 验证；交由网络或集群运维处理 |
| `NoAuth` | 认证用户名/口令不匹配，或 ACL 不授予目标目录读取权限 | 核对授权来源和 `getAcl` 结果；不要反复猜测口令 |
| 脚本退出 `2` | 未设置认证环境变量 | 重新使用静默 `read -s` 输入并 `export` 变量 |
| 脚本退出 `3` | `zkCli.sh` 不可执行或没有 `python3` | 修正本机客户端路径/依赖，不改 ZK 配置 |
| 脚本退出 `4` | 根目录、`sets` 或 `group_*` 中未解析到实例 | 先重跑 ZK-02，核对根路径与集群环境，不编造空清单为通过 |
| 脚本退出 `5` 或提示 `setrun` 缺失 | ZK 数据格式不完整或解析失败 | 保存脱敏错误摘要，附上项目提交号交由研发分析 |
| 提示无法取得实例口令 | 集群启用了密文口令，当前节点无 `manual_set` 或未配置受控解密通道 | 在获授权的 OSS/管理节点按项目 `oss_decrypt` 配置测试；不得手抄或外传密文/明文 |
| ZK-06 返回 Mock | 在 Windows 执行、端口预检失败，或错误传入 `force_mock=True` | 在 Linux 健康节点重新执行，并保持 `force_mock=False` |

## 7. 证据记录模板

测试报告中只填写下表，不附原始 CSV、命令历史、认证输入或敏感日志：

| 项目 | 记录内容 |
| --- | --- |
| 测试时间与操作者 | 日期时间、人员/智能体标识 |
| 项目提交 | `git rev-parse --short HEAD` |
| 环境 | 内网 SIT/UAT/生产只读窗口；节点用资产编号或脱敏名称表示 |
| ZK-01 | leader/follower 数量、是否降级 |
| ZK-02/03 | `SyncConnected`、根目录/sets 可读、错误码是否为零 |
| ZK-04 | 退出码、记录数、形态与状态分布、11 列断言 |
| ZK-05 | 退出码、运行中记录数、状态过滤断言 |
| ZK-06 | 服务层记录数、集中式/分布式数量、物理发现断言 |
| 可选 ZK-07 | 是否在隔离 UAT 执行、写入核对结果 |
| 最终结论 | 通过 / 条件通过 / 不通过 / 未执行，以及后续责任人 |

## 8. 收尾

```bash
unset ZK_AUTH_PASSWORD ZK_AUTH_USER
printf 'temporary_artifacts=%s\n' "$ARTIFACT_DIR"
```

由执行单位按本单位敏感数据留存和清理规范处理 `$ARTIFACT_DIR`。测试报告只保留第 7 节的脱敏摘要。
