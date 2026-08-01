# v1.5.3 原始慢日志采集部署与实测手册

本手册用于受控 SIT/UAT 环境；不包含、也不得填写真实主机地址、账号私钥、口令或原始慢 SQL。实施前须完成变更审批，并与 [DESIGN-v1.5.3-原始慢日志采集.md](DESIGN-v1.5.3-原始慢日志采集.md) 和开发自测报告一并评审。

## 1. 角色分工与禁止项

| 角色 | 允许职责 | 不允许事项 |
|---|---|---|
| CheckSQL 管理员 | 配置引用名、执行 Probe、启停来源、查看受控运行记录 | 在页面录入口令、私钥文本或任意远端命令 |
| 目标主机运维 | 安装导出器、配置白名单、创建专用账户、设置 ForceCommand | 给账户 root/sudo/交互 shell/任意路径读取权限 |
| 网络运维 | 仅从 CheckSQL 部署机私网地址放通 SSH | 开放公网 SSH 或宽网段放通 |
| 测试人员 | 执行受控慢 SQL、轮转和吞吐场景并留存脱敏证据 | 提交原始日志、真实地址或凭据到 Git |

禁止使用：`root`、密码认证、`sshpass`、SCP/SFTP、端口转发、SSH agent forwarding、客户端传入远端 shell 命令、CheckSQL 自动上传/替换远端二进制。

## 2. 构建与完整性校验

在受控 Linux/CI 打包机执行，按目标机架构选择：

```bash
RAW_SLOWLOG_EXPORTER_OUT_DIR=/secure/staging/raw-slowlog \
  bash deploy/build_raw_slowlog_exporter.sh amd64
sha256sum -c /secure/staging/raw-slowlog/raw_slowlog_exporter-linux-amd64.sha256
```

`amd64` 对应 x86_64，`arm64` 对应 aarch64。将二进制和同名 `.sha256` 作为独立受控变更附件交付；CheckSQL 发布包会为其自身目标架构构建同一产物，但不得自动把它部署到日志主机。

## 3. 目标 Proxy/Gateway 主机准备

以下示例均为占位符，实际目录必须来自日志属主/运维审批清单。

1. 创建仅供导出器使用的本地组和账户（已有同名受控账户时复用并审计）。账户无登录 shell、无 sudo、无家目录写权限。
2. 安装二进制和配置：

```bash
install -d -o root -g tdsql_log_reader -m 0750 /usr/local/libexec /etc/tdsql-sqlcheck
install -o root -g tdsql_log_reader -m 0750 raw_slowlog_exporter-linux-amd64 \
  /usr/local/libexec/raw_slowlog_exporter
install -o root -g tdsql_log_reader -m 0640 raw-slowlog-exporter.json \
  /etc/tdsql-sqlcheck/raw-slowlog-exporter.json
/usr/local/libexec/raw_slowlog_exporter --version
```

3. `/etc/tdsql-sqlcheck/raw-slowlog-exporter.json` 只允许写已审批白名单；平台的“日志路径声明”只用于审计展示，**不会**下发为远端路径。

```json
{
  "sources": {
    "sit_proxy_slowlog": {
      "paths": ["/approved/proxy/slow/*.log"],
      "storage_identity": "approved-storage-identity"
    }
  }
}
```

4. 确认 `tdsql_log_reader` 仅能读取该 glob 实际匹配的文件和导出器配置，不能读取父目录中其他日志、配置或用户文件。

5. 将 CheckSQL 部署机的专用公钥写入该账户 `authorized_keys`。强制命令必须为以下形式（公钥以实际受控内容替换）：

```text
restrict,command="/usr/local/libexec/raw_slowlog_exporter --stdio --config /etc/tdsql-sqlcheck/raw-slowlog-exporter.json" ssh-ed25519 AAAA... checksql-raw-slowlog
```

同时确认 sshd 禁止该账户的端口转发、X11、agent forwarding 和 TTY；以实际 sshd 配置为准，不能只依赖一条 `authorized_keys` 限制。

## 4. CheckSQL 部署机 Secret 与网络准备

1. 在部署机以应用运行账户可读、其他账户不可读的方式挂载 Secret 根目录（容器默认 `/run/secrets/tdsql-sqlcheck`）。
2. 对页面中的两个引用名 `<credential_ref>`、`<known_hosts_ref>`，准备以下文件：

```text
/run/secrets/tdsql-sqlcheck/<credential_ref>.key
/run/secrets/tdsql-sqlcheck/<known_hosts_ref>.known_hosts
```

3. `known_hosts` 必须使用目标主机的批准密钥和页面配置的 `host_key_alias`；不得使用 `StrictHostKeyChecking=no`，不得共享用户家目录 `~/.ssh/known_hosts`。
4. 网络策略仅允许 CheckSQL 部署机私网地址到每台已审批日志主机的 SSH 端口；目标机不得对公网开放。
5. 应用容器需包含 OpenSSH >= 7.4。执行 `deploy/preflight_check.sh`，该项失败则不得启用来源。

## 5. 页面配置、Probe 与启用

1. 进入 **慢SQL治理 → 原始慢日志 → 采集源**，以管理员角色新建来源。填写关联实例 ID、时区、引用名和节点；不得填写私钥/口令。
2. 节点的 `remote_source_key` 必须和目标主机 JSON `sources` 键完全一致。路径声明须为审批 Linux 绝对路径，仅用于审计校验。
3. 保存后来源固定为停用。执行 Probe。
4. 以下全部通过才允许管理员启用：

   - 固定 OpenSSH 参数和 `known_hosts` 严格验证成功；
   - 导出器响应 `protocol=raw_slowlog_exporter_v1`、版本为 `1.x.y.z`；
   - 受控源键和至少一个日志文件存在；
   - 仅返回的格式签名确认 `# Time`、`# Query_time` 均存在；
   - 与其他已启用节点不存在相同主机/存储身份上的相同文件身份。

5. Probe 失败时保持停用。不要通过降低 SSH 校验、改用 root 或扩大目录权限来“修复”。

## 6. SIT/UAT 实测清单

| 场景 | 操作 | 验收证据 |
|---|---|---|
| 时间语义 | 执行批准的可识别慢 SQL，记录完成时刻 | 事件 `event_time` 与 Proxy `# Time` 一致；不标为 SQL 开始/采集时间 |
| 字段/脱敏 | 覆盖字符串、数值、JSON、跨行 SQL | 页面/导出仅有模板和指纹，不含原值 |
| 幂等 | 对同一游标重复触发采集 | 事件不重复，运行计数体现 duplicate |
| rename rotation | 日志重命名轮转后继续产生样本 | 新旧文件均读取且无漏项 |
| copytruncate | 截断后快速写入并超过旧偏移 | 触发锚点保护与新 generation，不从中间静默续读 |
| 多 Proxy | 每个 Proxy 各产生样本；另配置重复节点 | 覆盖完整；重复文件在启用前被拒绝 |
| 吞吐/积压 | 持续输入超过单批上限 | 单轮连续拉批；超过预算后页面 degraded、运行/指标/告警可见 |
| 失败方向 | 篡改 known_hosts 或发送协议错误 | 失败关闭、游标不推进、无真实 SQL/凭据泄漏 |

HTML/CSV 报告的零行只能说明“成功采集到的事件为零”，不是“目标时间内没有慢 SQL”。必须同时检查节点覆盖、运行状态和错误摘要。

## 7. 回滚与异常处置

1. 先在页面停用来源，确认没有运行中的源级租约。
2. 保留当前导出器、配置、SHA-256 和 Probe 运行记录；禁止直接删除证据。
3. 按目标主机变更流程原子切回上一版已校验二进制/配置，再执行 `--version` 和 Probe。
4. 出现 `E5021`、`E5022`、`E4223` 或格式签名失败时，不得绕过校验。先分析受控服务日志，再修正配置、版本或格式适配并重新走 Probe 与 SIT 用例。
5. 将原因、影响范围、事件数量、游标状态、回滚版本和复测结论记录到变更单；不写入原始 SQL、主机地址或任何 Secret。
