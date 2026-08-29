# v1.6.2.2 升级手册（历史 checksum 迁移闭环）

| 项 | 内容 |
|---|---|
| 适用版本 | 从 ≤v1.6.2.1 升级到 v1.6.2.2 |
| 涉及缺陷 | UAT 第七轮 O-30（BLOCK） |
| 运维动作 | **无需手工改库、无需设置任何环境变量**；调和由应用启动时自动一次性完成 |

---

## 一、背景

`v9_090_connection_unique` 迁移文件在 v1.6.0.4（提交 `08ce65c`）被有意改为 no-op（端点唯一约束改由 Python 层执行），导致**早于该版本应用过旧内容的元数据库**，其 `schema_migrations` 中登记的 checksum（历史值 `54ee2e97…`）与当前文件（`c6cf33bb…`）不一致。

v1.6.2.2 起，迁移器对 checksum 漂移**默认失败关闭**；对这一已知历史变更内置**精确三元组账本**（版本键 + 历史 checksum + 当前 checksum），启动时自动完成一次性调和。未知漂移（文件被篡改等）一律失败关闭。

## 二、升级前（检测 + 备份）

```bash
# 1. 预检（含 v9_090 漂移状态与遗留开关检查）
bash deploy/preflight_check.sh --port 8000 --pkg-root <发布包根目录>

# 2. 元数据库备份（升级前强制）
mysqldump -h <meta_host> -P <meta_port> -u <user> -p \
  --single-transaction --routines tdsql_sqlcheck > backup_pre_v1622_$(date +%Y%m%d_%H%M%S).sql

# 3. 手工确认漂移状态（可选，预检已自动提示）
mysql -h <meta_host> -P <meta_port> -u <user> -p -N -e \
  "SELECT version_key, checksum FROM tdsql_sqlcheck.schema_migrations \
   WHERE version_key='v9_090_connection_unique'"
```

预检输出含义：

| 状态 | 含义 | 动作 |
|---|---|---|
| `fresh` | 全新库 | 无需动作 |
| `current` | 已是当前 checksum | 无需动作 |
| `historical` | 历史 checksum（老库升级） | **启动时自动调和**，关注日志即可 |
| `unknown` | 与已知新旧值均不符 | **禁止升级**，先人工核实文件是否被篡改 |

## 三、升级执行

按既有 `deploy/install.sh` 流程升级（`install.sh` 会先跑 `preflight_check.sh`）。**不要**在生产 `.env` 中写入 `SCHEMA_CHECKSUM_RECONCILE`——该长期开关已在 v1.6.2.2 移除，预检发现残留会 FAIL。

启动后验证：

```bash
# 1. 调和完成日志（ERROR 级，审计留痕）
journalctl -u tdsql-sqlcheck | grep "一次性自动调和完成"
# 期望：迁移 checksum 一次性自动调和完成 [v9_090_connection_unique]：54ee2e97…→c6cf33bb…

# 2. 双 worker 均就绪
curl -fsS http://127.0.0.1:8000/health && curl -fsS http://127.0.0.1:8000/health

# 3. 调和幂等（第二次启动不再调和，仅一次）
journalctl -u tdsql-sqlcheck | grep -c "一次性自动调和完成"   # 期望 ≤ worker 数（并发幂等重读）

# 4. 审计记录落库
mysql -h <meta_host> -P <meta_port> -u <user> -p -N -e \
  "SELECT operation_type, target_id, created_at FROM tdsql_sqlcheck.operation_logs \
   WHERE operation_type='schema_checksum_reconcile' ORDER BY id DESC LIMIT 3"
```

## 四、回滚

调和只改 `schema_migrations` 一行（checksum），不触碰业务结构与数据：

```bash
# 若升级后需回滚到旧版本：先装回旧包，再把 checksum 记录改回历史值
mysql -h <meta_host> -P <meta_port> -u <user> -p -e \
  "UPDATE tdsql_sqlcheck.schema_migrations \
   SET checksum='54ee2e97c804f5d8ec216d9f51600c19cc8463f2cede1de07fa67635abe6de28' \
   WHERE version_key='v9_090_connection_unique' \
     AND checksum='c6cf33bb385456fef12af3d4888ea6b22dcfc2a64052d734adc4c37457915209'"
# 旧版本对该漂移仅告警不阻断，可正常启动；完整回滚以备份还原为准
```

## 五、安全不变量（调和的边界）

- 调和前必须满足：`uq_conn_endpoint` 已存在、`uq_conn_name` 已移除、`tdsql_connections` 无重复端点；任一不满足即失败关闭（不调和、不启动）；
- 调和写入是单条条件 UPDATE（`version_key` + 旧 checksum 同时匹配才写），双 worker 并发只有一个进程中标，另一进程重读确认后幂等通过；
- 调和成功后，同一版本文件再被篡改（任何新 checksum）→ 启动必然失败关闭；
- 本项目不存在可长期生效的调和环境变量/开关。
