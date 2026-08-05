# RUNBOOK-v1.6.0.3 内网 name-diagnose 固化与富集扫描 傻瓜式操作手册

> 适用：v1.6.0.3 内网首测。目标：先用 `name-diagnose` 看"实例名称"在哪一级解析命中，
> 把命中级别固化成 `name_query_hint`，再打开富集扫描，让扫描列表直接带出实例名称+业务库。
> 全程只需 **curl**（或任意 HTTP 工具）+ 浏览器，不改代码。
> 约定：`$HOST` = CheckSQL 服务地址（如 `http://10.x.x.x:8000`），`$TOK` = 登录令牌。

---

## 0. 准备：拿令牌（一次性）

```bash
curl -s -X POST $HOST/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"<你的admin口令>"}'
```

从返回 JSON 里复制 `token` 字段，后面统一用：

```bash
TOK='<粘贴token>'
```

> 若返回 401：核对 admin 口令；首登需先改密。以下所有接口都要带 `-H "Authorization: Bearer $TOK"`。

---

## 1. 先保存 ZK 基础配置（不含富集，先关掉）

目的：让扫描能连上 ZK。此步**先不填** MonitorDB/业务账号、`enrich_enabled` 设为 0。

浏览器路径：平台治理 → 实例管理 → 「ZK发现配置」，填好后「启用富集」保持关闭 → 保存。
或 curl：

```bash
curl -s -X PUT $HOST/api/v1/tdsql/discover/config \
  -H "Authorization: Bearer $TOK" -H 'Content-Type: application/json' \
  -d '{
    "servers": "<zk1:2118,zk2:2118>",
    "root_path": "/tdsqlzk",
    "driver": "kazoo",
    "proxy_mode": "first",
    "auth_username": "<zk只读用户>",
    "auth_password": "<zk口令>",
    "endpoint_map": {"10.243.21.13":"10.243.20.13"},
    "octet_rules": [],
    "enrich_enabled": 0
  }'
```

> `endpoint_map`/`octet_rules` 二选一即可，作用都是把 ZK 内网地址换成 CheckSQL 可达地址。
> 内网常见是"同主机双段 IP、末位相同"，用 `octet_rules:[{"segment":3,"from":"21","to":"20"}]` 更省事。

---

## 2. 跑一次扫描，拿 2~3 个实例 ID

```bash
curl -s -X POST $HOST/api/v1/tdsql/discover \
  -H "Authorization: Bearer $TOK" -H 'Content-Type: application/json' -d '{}'
```

返回 `items[]`。记下 2~3 个 `instance_id`（一个 `group_*`、一个 `set_*` 各取一个最好），
以及整个 `discovery_id`。此步 `enrich_enabled=0`，列表名称/库为空属正常。

---

## 3. 跑 name-diagnose，看哪一级命中

```bash
curl -s -X POST $HOST/api/v1/tdsql/discover/name-diagnose \
  -H "Authorization: Bearer $TOK" -H 'Content-Type: application/json' \
  -d '{
    "instance_ids": ["<group_xxx>", "<set_yyy>"],
    "discovery_id": "<第2步的discovery_id>",
    "monitor": {"host":"<monitordb主机>","port":15001,
                "username":"<monitor用户>","password":"<monitor口令>",
                "database":"tdsqlpcloud_monitor"}
  }'
```

**看返回的 `items[].name_hits[]`**：

| 你看到 | 含义 | 下一步固化值 |
|---|---|---|
| `name_hits[0].level = "L1"` | 精确 mid（`/tdsqlzk/<id>` + f_type=1）命中 | `name_query_hint="L1"` |
| `level = "L2"` | 模糊 mid 命中 | `"L2"` |
| `level = "L3"` | 值像名称命中 | `"L3"` |
| `name_hits` 为空但 `meta_tables[].name_columns` 非空 | 名称在元数据表 | `"L4"` |
| 全空、只有 `zk_name_fields` 有值 | 仅 ZK 节点有名称 | `"L5"` |
| 全空且 `matched_mids` 也空 | MonitorDB 里没有该实例 | 保持自动（留空），导入时用手工命名兜底 |

> 同时可看 `matched_mids`/`available_keys` 确认 monitordb 里 mid/键的真实格式，便于核对。

---

## 4. 固化 hint + 打开富集，重新保存配置

把第 3 步得到的级别写进 `name_query_hint`，并补齐 MonitorDB/业务账号、`enrich_enabled=1`：

浏览器路径：「ZK发现配置」→ 填 MonitorDB 五项 + 业务用户名/口令 → 「名称解析固化」选第 3 步的级别 → 「启用富集」打开 → 保存。
或 curl（在第 1 步基础上加字段）：

```bash
curl -s -X PUT $HOST/api/v1/tdsql/discover/config \
  -H "Authorization: Bearer $TOK" -H 'Content-Type: application/json' \
  -d '{
    "servers": "<zk1:2118,zk2:2118>",
    "root_path": "/tdsqlzk", "driver": "kazoo", "proxy_mode": "first",
    "auth_username": "<zk只读用户>", "auth_password": "<zk口令>",
    "octet_rules": [{"segment":3,"from":"21","to":"20"}],
    "monitor_host": "<monitordb主机>", "monitor_port": 15001,
    "monitor_user": "<monitor用户>", "monitor_password": "<monitor口令>",
    "monitor_db": "tdsqlpcloud_monitor",
    "business_username": "<业务只读用户>", "business_password": "<业务口令>",
    "name_query_hint": "L2",
    "enrich_enabled": 1
  }'
```

> 口令留空=保留已存密文；MonitorDB/业务口令均加密入库、GET 不回显。

---

## 5. 再跑扫描，验收富集效果

```bash
curl -s -X POST $HOST/api/v1/tdsql/discover \
  -H "Authorization: Bearer $TOK" -H 'Content-Type: application/json' -d '{}'
```

验收（浏览器「从 ZK 自动发现」同样可见）：
- `items[].resolved_name` 非空、`name_source` 等于你固化的级别对应来源；
- `items[].business_dbs[]` 非空、`databases_source="proxy_show"`；
- `items[].enrich_status="ok"`（或 `name_only`/`dbs_only`，视哪路成功）；
- 列表出现「实例名称 / 业务库」列，分页与五维筛选可用。

随后「配置导入并生成预览」→ 勾选 → 「创建已选连接」，连接名应为 `实例名称-端口-库名`。

---

## 6. 常见排错

| 现象 | 原因 | 处理 |
|---|---|---|
| 扫描 503 `ZK 不可达` | ZK 协议被拦/地址错 | 核对 servers 与网络；本机"TCP通协议拦"属环境限制 |
| diagnose 返回 `error`/`matched_mids` 空 | MonitorDB 连不上或无该实例 | 核对 monitor 五项；或导入时用手工命名/手工库兜底 |
| 名称仍为空但 `enrich_status=name_only` | 业务库枚举失败 | 看 `dbs_failed:*`；核对业务账号 SHOW DATABASES 权限或 octet_rules |
| 预览某行 `NO_AVAILABLE_PROXY` | 适配后全部 Proxy 仍不可达（v1.6.0.6 起部分失败只标"部分 Proxy"降级不阻断） | 调整 octet_rules/endpoint_map，或该行手工填库 |

*本手册不改代码；所有配置经加密存储，口令不回显。*
