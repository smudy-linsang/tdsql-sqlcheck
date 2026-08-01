# 原始慢日志远端导出器

该二进制只部署在已获准的 Proxy/Gateway 日志主机，由 `tdsql_log_reader` 的 SSH
`ForceCommand` 调用。它不监听端口、不常驻、不执行调用者传入的命令或路径。

部署前须完成设计文档 §16 的格式与容量准入。将 `root:tdsql_log_reader` 拥有、权限 `0640` 的配置放在
`/etc/tdsql-sqlcheck/raw-slowlog-exporter.json`；`sources` 的 key 必须与 CheckSQL
页面中节点的 `remote_source_key` 一致，`paths` 只能填写已审批的日志文件 glob。

示例 `authorized_keys` 约束（按本机实际二进制、配置路径和公钥替换）：

```text
command="/usr/local/libexec/raw_slowlog_exporter --stdio --config /etc/tdsql-sqlcheck/raw-slowlog-exporter.json",no-agent-forwarding,no-port-forwarding,no-X11-forwarding,no-pty ssh-ed25519 AAAA... tdsql-sqlcheck
```

验证仅在受控内网从部署机执行：`ssh -T tdsql_log_reader@<approved-host>`，然后输入
一条 `{"op":"version","protocol":"raw_slowlog_exporter_v1","source_key":"..."}`。
不得用 root、密码认证、`sshpass` 或通过此账号取得交互 shell。
