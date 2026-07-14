# TDSQL 远程命令批量执行工具

## 功能说明

在**生产环境 SSH 被禁用**的场景下，复用 scheduler 提供的 `sshpass_pack.sh` 能力，按 `tdsql_hosts` 中定义的主机分组和顺序，逐台执行远程 shell 命令，并输出 **IP + 执行结果**。

该模块适合快速查看多台 TDSQL 服务器上的系统信息，例如磁盘、目录、进程、版本文件等。

## 推荐执行命令

```bash
# 查看可用分组
sh sshpass_pack_exec.sh --list-groups

# 方式1（推荐）：读取项目 tdsql_env.conf 的 [sshpass_pack] token
# 适合"一台管理节点管理多个集群"的场景，各集群工程各自维护 token
sh sshpass_pack_exec.sh -g tdsql_db -- "df -h | grep dev"

# 方式2：本机就是该集群的 scheduler 节点，直接读取 scheduler.xml
sh sshpass_pack_exec.sh -g tdsql_db -- "df -h | grep dev"

# 方式3：命令行显式传 token
sh sshpass_pack_exec.sh -t 'your_token' -g tdsql_proxy -- "hostname && uptime"

# 方式4：跨集群时显式指定另一份 conf
sh sshpass_pack_exec.sh -c /data/cluster-b/tdsql-toolkit/tdsql_env.conf \
    -g tdsql_db -- "hostname"

# 预览底层调用命令（不实际执行，token 打码）
sh sshpass_pack_exec.sh -g tdsql_db --dry-run "hostname"

# 查看版本号
sh sshpass_pack_exec.sh -V
```

## 适用场景

- 批量查看 DB / Proxy / Scheduler 等节点的系统信息
- 按分组顺序逐台排查机器状态
- 在无法直接 SSH 的生产环境中执行只读检查命令
- 复用已有 `tdsql_hosts` 清单，不重复维护主机列表
- **批量分析各机器的 CPU / 内存 / IO / 负载峰值**

## 目录说明

- `sshpass_pack_exec.sh`：主执行脚本，支持 `sh` 直接运行
- `tdsql_hosts`：主机清单，按分组维护 IP
- `changelist.txt`：模块变更记录

---

## 机器负载监控分析命令手册

> 以下命令均基于 `/data1/monitorlog` 下的监控日志文件，配合 `sshpass_pack_exec.sh` 可批量查看所有机器的运行峰值。
>
> **约定**：以下示例统一使用 `-g tdsql_db` 分组，实际使用时替换为你需要的分组名。

### 监控目录结构

```text
/data1/monitorlog/
├── dstatlog/       # dstat 综合监控（CPU/内存/磁盘/网络/负载）
│   └── dstatlog.YYYYMMDD
├── iostatlog/      # iostat 磁盘 IO 监控
│   └── iostatlog.YYYYMMDD
├── iotoplog/       # iotop 进程级 IO 监控
│   └── iotoplog.YYYYMMDD
├── meminfo/        # /proc/meminfo 内存快照
│   └── meminfo.log.YYYYMMDD
├── netstat/        # ping 网络延迟监控
│   └── ping_<ip>_<name>.log.YYYYMMDD
├── toplog/         # top 进程快照
│   └── toplog.YYYYMMDD
└── vmstat/         # vmstat 系统快照
    └── vmstat.log.YYYYMMDD
```

---

### 1. CPU 峰值（usr+sys 最大值）

从 dstatlog 中提取 CPU 使用率（usr+sys）的峰值时间点：

```bash
# 查看今天的 CPU 峰值
sh sshpass_pack_exec.sh -g tdsql_db -- "TODAY=\$(date +%Y%m%d) && awk -F'|' 'NR>2 && /^[0-9]/{split(\$2,a,\" \"); cpu=a[1]+a[2]; if(cpu>max){max=cpu; ts=\$1}} END{printf \"CPU峰值(usr+sys): %.0f%%  时间: %s\n\",max,ts}' /data1/monitorlog/dstatlog/dstatlog.\$TODAY"

# 查看指定日期的 CPU 峰值
sh sshpass_pack_exec.sh -g tdsql_db -- "awk -F'|' 'NR>2 && /^[0-9]/{split(\$2,a,\" \"); cpu=a[1]+a[2]; if(cpu>max){max=cpu; ts=\$1}} END{printf \"CPU峰值(usr+sys): %.0f%%  时间: %s\n\",max,ts}' /data1/monitorlog/dstatlog/dstatlog.20260325"

# CPU 峰值 + 完整 dstat 记录行
sh sshpass_pack_exec.sh -g tdsql_db -- "TODAY=\$(date +%Y%m%d) && awk -F'|' 'NR>2 && /^[0-9]/{split(\$2,a,\" \"); cpu=a[1]+a[2]; if(cpu>max){max=cpu; line=\$0}} END{printf \"CPU峰值(usr+sys): %.0f%%\n\",max; print line}' /data1/monitorlog/dstatlog/dstatlog.\$TODAY"

# CPU 使用率 TOP 10 时刻
sh sshpass_pack_exec.sh -g tdsql_db -- "TODAY=\$(date +%Y%m%d) && awk -F'|' 'NR>2 && /^[0-9]/{split(\$2,a,\" \"); printf \"%.0f%% %s\n\",a[1]+a[2],\$1}' /data1/monitorlog/dstatlog/dstatlog.\$TODAY | sort -rn | head -10"
```

---

### 2. 内存使用峰值

从 dstatlog 提取内存 used 列（字段10）的峰值：

```bash
# 查看今天的内存使用峰值
sh sshpass_pack_exec.sh -g tdsql_db -- "TODAY=\$(date +%Y%m%d) && awk -F'|' 'NR>2 && /^[0-9]/{gsub(/^[[:space:]]+|[[:space:]]+\$/,\"\",\$10); split(\$10,a,\" \"); v=a[1]+0; u=a[1]; if(u~/G/){v_mb=v*1024}else{v_mb=v}; if(v_mb>max){max=v_mb; ts=\$1}} END{if(max>=1024){printf \"内存使用峰值: %.1fG\",max/1024}else{printf \"内存使用峰值: %.0fM\",max}; printf \"  时间: %s\n\",ts}' /data1/monitorlog/dstatlog/dstatlog.\$TODAY"

# 查看指定日期的内存峰值
sh sshpass_pack_exec.sh -g tdsql_db -- "awk -F'|' 'NR>2 && /^[0-9]/{gsub(/^[[:space:]]+|[[:space:]]+\$/,\"\",\$10); split(\$10,a,\" \"); v=a[1]+0; u=a[1]; if(u~/G/){v_mb=v*1024}else{v_mb=v}; if(v_mb>max){max=v_mb; ts=\$1}} END{if(max>=1024){printf \"内存使用峰值: %.1fG\",max/1024}else{printf \"内存使用峰值: %.0fM\",max}; printf \"  时间: %s\n\",ts}' /data1/monitorlog/dstatlog/dstatlog.20260325"
```

从 meminfo 提取 MemFree 最小值（即内存使用最紧张时刻）：

```bash
# 查看今天 MemAvailable 最低值
sh sshpass_pack_exec.sh -g tdsql_db -- "TODAY=\$(date +%Y%m%d) && awk '/^[A-Z][a-z].*20[0-9][0-9]/{ts=\$0} /^MemAvailable:/{v=\$2+0; if(min==\"\"||v<min){min=v; min_ts=ts}} END{printf \"MemAvailable最低: %.1fG  时间: %s\n\",min/1024/1024,min_ts}' /data1/monitorlog/meminfo/meminfo.log.\$TODAY"
```

---

### 3. IO Wait 峰值

从 dstatlog 的 CPU 字段中提取 iowait（第4个子字段）的峰值：

```bash
# 查看今天的 IO Wait 峰值
sh sshpass_pack_exec.sh -g tdsql_db -- "TODAY=\$(date +%Y%m%d) && awk -F'|' 'NR>2 && /^[0-9]/{split(\$2,a,\" \"); wai=a[4]+0; if(wai>max){max=wai; ts=\$1}} END{printf \"IO Wait峰值: %.0f%%  时间: %s\n\",max,ts}' /data1/monitorlog/dstatlog/dstatlog.\$TODAY"

# 查看指定日期的 IO Wait 峰值
sh sshpass_pack_exec.sh -g tdsql_db -- "awk -F'|' 'NR>2 && /^[0-9]/{split(\$2,a,\" \"); wai=a[4]+0; if(wai>max){max=wai; ts=\$1}} END{printf \"IO Wait峰值: %.0f%%  时间: %s\n\",max,ts}' /data1/monitorlog/dstatlog/dstatlog.20260325"

# IO Wait TOP 10 时刻
sh sshpass_pack_exec.sh -g tdsql_db -- "TODAY=\$(date +%Y%m%d) && awk -F'|' 'NR>2 && /^[0-9]/{split(\$2,a,\" \"); printf \"%.0f%% %s\n\",a[4],\$1}' /data1/monitorlog/dstatlog/dstatlog.\$TODAY | sort -rn | head -10"
```

---

### 4. Load Average 峰值（1分钟负载）

从 dstatlog 的 load-avg 字段（字段9）中提取 1分钟负载峰值：

```bash
# 查看今天的 1 分钟负载峰值
sh sshpass_pack_exec.sh -g tdsql_db -- "TODAY=\$(date +%Y%m%d) && awk -F'|' 'NR>2 && /^[0-9]/{gsub(/^[[:space:]]+|[[:space:]]+\$/,\"\",\$9); split(\$9,a,\" \"); v=a[1]+0; if(v>max){max=v; ts=\$1}} END{printf \"1min负载峰值: %.2f  时间: %s\n\",max,ts}' /data1/monitorlog/dstatlog/dstatlog.\$TODAY"

# 查看指定日期的负载峰值
sh sshpass_pack_exec.sh -g tdsql_db -- "awk -F'|' 'NR>2 && /^[0-9]/{gsub(/^[[:space:]]+|[[:space:]]+\$/,\"\",\$9); split(\$9,a,\" \"); v=a[1]+0; if(v>max){max=v; ts=\$1}} END{printf \"1min负载峰值: %.2f  时间: %s\n\",max,ts}' /data1/monitorlog/dstatlog/dstatlog.20260325"

# 负载 TOP 10 时刻
sh sshpass_pack_exec.sh -g tdsql_db -- "TODAY=\$(date +%Y%m%d) && awk -F'|' 'NR>2 && /^[0-9]/{gsub(/^[[:space:]]+|[[:space:]]+\$/,\"\",\$9); split(\$9,a,\" \"); printf \"%.2f %s\n\",a[1],\$1}' /data1/monitorlog/dstatlog/dstatlog.\$TODAY | sort -rn | head -10"
```

---

### 5. 磁盘 IO 峰值（iostat — 重点关注 data/data1 所在磁盘）

> **说明**：生产环境一般 SSD + NVMe 混合部署，数据目录在 `/data` 或 `/data1`。
> 下面的命令会从 iostatlog 中提取磁盘的 `r_await`（读等待）、`w_await`（写等待）和 `%util`（IO利用率）峰值。
>
> **iostatlog 列对照表**（方便自行扩展）：
>
> | 列号 | 字段 | 说明 |
> |------|------|------|
> | $1 | Device | 设备名 |
> | $4 | r/s | 每秒读次数 |
> | $5 | w/s | 每秒写次数 |
> | $6 | rMB/s | 每秒读吞吐 |
> | $7 | wMB/s | 每秒写吞吐 |
> | $9 | avgqu-sz | 平均队列长度 |
> | $10 | await | 平均 IO 等待时间(ms) |
> | **$11** | **r_await** | **平均读等待时间(ms)** |
> | **$12** | **w_await** | **平均写等待时间(ms)** |
> | **$14** | **%util** | **磁盘利用率** |

#### 5.1 指定 Device 查看 r_await / w_await / %util 峰值

```bash
# === 指定 vda 磁盘 ===
sh sshpass_pack_exec.sh -g tdsql_db -- "TODAY=\$(date +%Y%m%d) && awk '/^[0-9]{4}-/{ts=\$1} /^vda[[:space:]]/{ra=\$11+0; wa=\$12+0; ut=\$14+0; if(ra>mr){mr=ra; mrt=ts} if(wa>mw){mw=wa; mwt=ts} if(ut>mu){mu=ut; mut=ts}} END{printf \"r_await峰值: %.2fms (%s)\nw_await峰值: %.2fms (%s)\n%%util峰值:   %.2f%% (%s)\n\",mr,mrt,mw,mwt,mu,mut}' /data1/monitorlog/iostatlog/iostatlog.\$TODAY"

# === 指定 nvme0n1 磁盘（NVMe）===
sh sshpass_pack_exec.sh -g tdsql_db -- "TODAY=\$(date +%Y%m%d) && awk '/^[0-9]{4}-/{ts=\$1} /^nvme0n1[[:space:]]/{ra=\$11+0; wa=\$12+0; ut=\$14+0; if(ra>mr){mr=ra; mrt=ts} if(wa>mw){mw=wa; mwt=ts} if(ut>mu){mu=ut; mut=ts}} END{printf \"r_await峰值: %.2fms (%s)\nw_await峰值: %.2fms (%s)\n%%util峰值:   %.2f%% (%s)\n\",mr,mrt,mw,mwt,mu,mut}' /data1/monitorlog/iostatlog/iostatlog.\$TODAY"

# === 指定 sda 磁盘（SATA/SSD）===
sh sshpass_pack_exec.sh -g tdsql_db -- "TODAY=\$(date +%Y%m%d) && awk '/^[0-9]{4}-/{ts=\$1} /^sda[[:space:]]/{ra=\$11+0; wa=\$12+0; ut=\$14+0; if(ra>mr){mr=ra; mrt=ts} if(wa>mw){mw=wa; mwt=ts} if(ut>mu){mu=ut; mut=ts}} END{printf \"r_await峰值: %.2fms (%s)\nw_await峰值: %.2fms (%s)\n%%util峰值:   %.2f%% (%s)\n\",mr,mrt,mw,mwt,mu,mut}' /data1/monitorlog/iostatlog/iostatlog.\$TODAY"

# === 查看指定日期（替换日期即可）===
sh sshpass_pack_exec.sh -g tdsql_db -- "awk '/^[0-9]{4}-/{ts=\$1} /^vda[[:space:]]/{ra=\$11+0; wa=\$12+0; ut=\$14+0; if(ra>mr){mr=ra; mrt=ts} if(wa>mw){mw=wa; mwt=ts} if(ut>mu){mu=ut; mut=ts}} END{printf \"r_await峰值: %.2fms (%s)\nw_await峰值: %.2fms (%s)\n%%util峰值:   %.2f%% (%s)\n\",mr,mrt,mw,mwt,mu,mut}' /data1/monitorlog/iostatlog/iostatlog.20260325"
```

#### 5.2 自动检测 data/data1 所在磁盘并查看 IO 峰值

```bash
# 自动查找 /data1 或 /data 所在磁盘，提取当天 r_await/w_await/%util 峰值
sh sshpass_pack_exec.sh -g tdsql_db -- "TODAY=\$(date +%Y%m%d) && DISK=\$(df /data1 2>/dev/null || df /data 2>/dev/null | awk 'NR==2{print \$1}' | sed 's#/dev/##; s/[0-9]*\$//; s/p[0-9]*\$//') && echo \"数据盘: \$DISK\" && awk -v disk=\"\$DISK\" '\$1==disk{ra=\$11+0; wa=\$12+0; ut=\$14+0; if(ra>mr){mr=ra; mrt=pt} if(wa>mw){mw=wa; mwt=pt} if(ut>mu){mu=ut; mut=pt}} /^[0-9]{4}-/{pt=\$1} END{printf \"r_await峰值: %.2fms (%s)\nw_await峰值: %.2fms (%s)\n%%util峰值:   %.2f%% (%s)\n\",mr,mrt,mw,mwt,mu,mut}' /data1/monitorlog/iostatlog/iostatlog.\$TODAY"
```

#### 5.3 所有磁盘设备的 IO 峰值汇总

```bash
# 汇总所有设备的 r_await / w_await / %util 峰值
sh sshpass_pack_exec.sh -g tdsql_db -- "TODAY=\$(date +%Y%m%d) && awk '/^[0-9]{4}-/{ts=\$1} /^[a-z]/ && !/^Device/ && !/^Linux/ && !/^avg/{d=\$1; ra=\$11+0; wa=\$12+0; ut=\$14+0; if(ra>mr[d]){mr[d]=ra; mrt[d]=ts} if(wa>mw[d]){mw[d]=wa; mwt[d]=ts} if(ut>mu[d]){mu[d]=ut; mut[d]=ts}} END{for(d in mr){printf \"%-12s r_await: %8.2fms (%s)  w_await: %8.2fms (%s)  util: %6.2f%% (%s)\n\",d,mr[d],mrt[d],mw[d],mwt[d],mu[d],mut[d]}}' /data1/monitorlog/iostatlog/iostatlog.\$TODAY"
```

#### 5.4 指定磁盘的 r_await / w_await / %util TOP 10 时刻

```bash
# vda 磁盘 r_await TOP 10
sh sshpass_pack_exec.sh -g tdsql_db -- "TODAY=\$(date +%Y%m%d) && awk '/^[0-9]{4}-/{ts=\$1} /^vda[[:space:]]/{printf \"%.2fms %s\n\",\$11,ts}' /data1/monitorlog/iostatlog/iostatlog.\$TODAY | sort -rn | head -10"

# vda 磁盘 w_await TOP 10
sh sshpass_pack_exec.sh -g tdsql_db -- "TODAY=\$(date +%Y%m%d) && awk '/^[0-9]{4}-/{ts=\$1} /^vda[[:space:]]/{printf \"%.2fms %s\n\",\$12,ts}' /data1/monitorlog/iostatlog/iostatlog.\$TODAY | sort -rn | head -10"

# vda 磁盘 %util TOP 10
sh sshpass_pack_exec.sh -g tdsql_db -- "TODAY=\$(date +%Y%m%d) && awk '/^[0-9]{4}-/{ts=\$1} /^vda[[:space:]]/{printf \"%.2f%% %s\n\",\$14,ts}' /data1/monitorlog/iostatlog/iostatlog.\$TODAY | sort -rn | head -10"
```

---

### 6. 综合概览（一次输出 CPU/内存/IO Wait/负载/磁盘 IO 峰值）

```bash
# 一条命令查看当天所有关键指标峰值
sh sshpass_pack_exec.sh -g tdsql_db -- "TODAY=\$(date +%Y%m%d) && echo '========== 当日指标峰值汇总 ==========' && awk -F'|' 'NR>2 && /^[0-9]/{split(\$2,a,\" \"); cpu=a[1]+a[2]; wai=a[4]+0; gsub(/^[[:space:]]+|[[:space:]]+\$/,\"\",\$9); split(\$9,b,\" \"); load1=b[1]+0; gsub(/^[[:space:]]+|[[:space:]]+\$/,\"\",\$10); split(\$10,c,\" \"); mv=c[1]+0; mu=c[1]; if(mu~/G/){mmb=mv*1024}else{mmb=mv}; if(cpu>mc){mc=cpu;mct=\$1} if(wai>mw){mw=wai;mwt=\$1} if(load1>ml){ml=load1;mlt=\$1} if(mmb>mm){mm=mmb;mmt=\$1}} END{printf \"CPU峰值(usr+sys): %5.0f%%   时间: %s\n\",mc,mct; if(mm>=1024){printf \"内存使用峰值:     %5.1fG   时间: %s\n\",mm/1024,mmt}else{printf \"内存使用峰值:     %5.0fM   时间: %s\n\",mm,mmt}; printf \"IO Wait峰值:      %5.0f%%   时间: %s\n\",mw,mwt; printf \"1min负载峰值:     %5.2f    时间: %s\n\",ml,mlt}' /data1/monitorlog/dstatlog/dstatlog.\$TODAY && DISK=\$(df /data1 2>/dev/null || df /data 2>/dev/null | awk 'NR==2{print \$1}' | sed 's#/dev/##; s/[0-9]*\$//; s/p[0-9]*\$//') && awk -v disk=\"\$DISK\" '\$1==disk{ra=\$11+0; wa=\$12+0; ut=\$14+0; if(ra>mr){mr=ra;mrt=pt} if(wa>mw){mw=wa;mwt=pt} if(ut>mu){mu=ut;mut=pt}} /^[0-9]{4}-/{pt=\$1} END{printf \"磁盘 %s r_await峰值: %.2fms (%s)\n磁盘 %s w_await峰值: %.2fms (%s)\n磁盘 %s util峰值:    %.2f%% (%s)\n\",disk,mr,mrt,disk,mw,mwt,disk,mu,mut}' /data1/monitorlog/iostatlog/iostatlog.\$TODAY"
```

---

### 7. vmstat 指标峰值

```bash
# vmstat 中 r（运行队列）最大值 — 反映 CPU 排队情况
sh sshpass_pack_exec.sh -g tdsql_db -- "TODAY=\$(date +%Y%m%d) && awk 'NR>2{r=\$1+0; if(r>max){max=r; ts=\$NF}} END{printf \"运行队列峰值(r): %d  时间: %s\n\",max,ts}' /data1/monitorlog/vmstat/vmstat.log.\$TODAY"

# vmstat 中 b（阻塞进程）最大值 — 反映 IO 阻塞情况
sh sshpass_pack_exec.sh -g tdsql_db -- "TODAY=\$(date +%Y%m%d) && awk 'NR>2{b=\$2+0; if(b>max){max=b; ts=\$NF}} END{printf \"阻塞进程峰值(b): %d  时间: %s\n\",max,ts}' /data1/monitorlog/vmstat/vmstat.log.\$TODAY"

# vmstat 中 wa（IO Wait）峰值
sh sshpass_pack_exec.sh -g tdsql_db -- "TODAY=\$(date +%Y%m%d) && awk 'NR>2{wa=\$(NF-2)+0; if(wa>max){max=wa; ts=\$NF}} END{printf \"vmstat wa峰值: %d%%  时间: %s\n\",max,ts}' /data1/monitorlog/vmstat/vmstat.log.\$TODAY"
```

---

### 8. 其他常用命令

```bash
# 查看磁盘使用率
sh sshpass_pack_exec.sh -g tdsql_db -- "df -h | grep -E 'Filesystem|/dev/'"

# 查看 data/data1 目录大小
sh sshpass_pack_exec.sh -g tdsql_db -- "du -sh /data /data1 2>/dev/null"

# 查看当前负载和 uptime
sh sshpass_pack_exec.sh -g tdsql_db -- "uptime"

# 查看内存使用情况
sh sshpass_pack_exec.sh -g tdsql_db -- "free -h"

# 查看 data/data1 所在磁盘设备名
sh sshpass_pack_exec.sh -g tdsql_db -- "df /data1 2>/dev/null | awk 'NR==2{print \$1}' && df /data 2>/dev/null | awk 'NR==2{print \$1}'"

# 查看 dstat 是否在采集
sh sshpass_pack_exec.sh -g tdsql_db -- "ps aux | grep dstat | grep -v grep"

# 查看 monitorlog 目录结构
sh sshpass_pack_exec.sh -g tdsql_db -- "ls -la /data1/monitorlog/"

# 查看某天的 monitorlog 文件列表
sh sshpass_pack_exec.sh -g tdsql_db -- "find /data1/monitorlog -name '*20260325*' -ls"

# 查看 toplog 中 CPU 最高的进程
sh sshpass_pack_exec.sh -g tdsql_db -- "TODAY=\$(date +%Y%m%d) && awk '/^[[:space:]]*[0-9]/ && NR>7{cpu=\$9+0; if(cpu>max){max=cpu; line=\$0}} END{printf \"CPU最高进程: %.1f%%\n%s\n\",max,line}' /data1/monitorlog/toplog/toplog.\$TODAY"
```

---

### 命令速查表

| 场景 | 关键指标 | 数据源 | 章节 |
|------|----------|--------|------|
| CPU 使用率峰值 | usr + sys | dstatlog | [1. CPU 峰值](#1-cpu-峰值usrsys-最大值) |
| 内存使用峰值 | used | dstatlog / meminfo | [2. 内存使用峰值](#2-内存使用峰值) |
| IO Wait 峰值 | wai | dstatlog | [3. IO Wait 峰值](#3-io-wait-峰值) |
| 系统负载峰值 | load 1min | dstatlog | [4. Load Average 峰值](#4-load-average-峰值1分钟负载) |
| 磁盘读等待峰值 | r_await (ms) — 列$11 | iostatlog | [5. 磁盘 IO 峰值](#5-磁盘-io-峰值iostat--重点关注-datadata1-所在磁盘) |
| 磁盘写等待峰值 | w_await (ms) — 列$12 | iostatlog | [5. 磁盘 IO 峰值](#5-磁盘-io-峰值iostat--重点关注-datadata1-所在磁盘) |
| 磁盘利用率峰值 | %util — 列$14 | iostatlog | [5. 磁盘 IO 峰值](#5-磁盘-io-峰值iostat--重点关注-datadata1-所在磁盘) |
| 一次查全部 | 综合 | dstatlog + iostatlog | [6. 综合概览](#6-综合概览一次输出-cpu内存io-wait负载磁盘-io-峰值) |
| 运行队列/阻塞 | r / b | vmstat | [7. vmstat 指标峰值](#7-vmstat-指标峰值) |

---

## 快速开始

```bash
cd sshpass_pack

# 1. 查看可用分组
sh sshpass_pack_exec.sh --list-groups

# 2. 在 scheduler 机器上直接执行（自动读取 scheduler.xml 中的 password_encrypt）
sh sshpass_pack_exec.sh -g tdsql_db -- "df -h | grep dev"

# 3. 如果不在 scheduler 机器上，也可以手工传 token
sh sshpass_pack_exec.sh -t 'your_token' -g tdsql_db -- "df -h | grep dev"
```

## 自动读取逻辑

### token 读取优先级

```text
1. 命令行 -t / --token
2. 环境变量 SSHPASS_PACK_TOKEN
3. tdsql_env.conf 的 [sshpass_pack] 段 token=
4. scheduler.xml 中 <sshpass ... password_encrypt="..." />
```

优先级从上到下，先命中先用。命令行显式传的 `--user`、`--port`、`--workdir` 永远优先。

### 多集群管理场景（推荐）

同一台管理节点上部署多份 tdsql-toolkit 工程时（例如 `/data/cluster-a/tdsql-toolkit`、`/data/cluster-b/tdsql-toolkit`），本机的 `scheduler.xml` 可能只对应其中一个集群，其它集群的 token 需要各自维护。

在**每份工程的 `tdsql_env.conf`** 中加入 `[sshpass_pack]` 段：

```ini
[sshpass_pack]
token=LGhVs0v5nVxcOLQie/k9bb2I    # 该集群的 password_encrypt 值
# user=tdsql
# port=8966
# oc_dir=/data/oc_agent/bin
# scheduler_xml=/data/application/scheduler/conf/scheduler.xml
```

之后在该工程根目录直接运行脚本即可，脚本会自动加载同目录/项目根的 `tdsql_env.conf`，无需再 `export SSHPASS_PACK_TOKEN`。

### 如何获取 token（password_encrypt）

```bash
# 方法A：本机是该集群的 scheduler 节点
grep 'password_encrypt=' /data/application/scheduler/conf/scheduler.xml | head -n1

# 方法B：跨集群时 SSH 到该集群 scheduler 节点抓取
ssh <该集群 scheduler 节点> \
    "grep password_encrypt /data/application/scheduler/conf/scheduler.xml | head -n1"
```

拿到形如 `password_encrypt="LGhVs0v5nVxcOLQie/k9bb2I"` 的值，把双引号里的字符串写入对应工程 `tdsql_env.conf` 的 `[sshpass_pack] token=`。

⚠ **集群升级或密码轮换后 token 会变**。如果批量报 `Send msg for authorize failed: Password vertify failed`，用上面命令重新取一次并更新 conf 即可。

### scheduler.xml 兜底

若本工程 `tdsql_env.conf` 没有配置 `[sshpass_pack]`，脚本会回退到解析本机 `/data/application/scheduler/conf/scheduler.xml`（可用 `--scheduler-xml` 覆盖路径）：

```xml
<sshpass user="tdsql" password_encrypt="n33URxp8TPl8K0QS3lo=" port="8966" oc_dir="/data/oc_agent/bin" />
```

从该行解析 `password_encrypt`、`port`、`oc_dir` 作为默认值。命令行显式指定的 `--port`、`--workdir` 优先级最高。

## 参数说明

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `-t, --token TOKEN` | 访问 token，可选 | 优先于自动读取 |
| `-c, --env-conf FILE` | 指定 tdsql_env.conf 路径（多集群管理专用） | 项目根 `../tdsql_env.conf` > 脚本同目录 |
| `-H, --hosts FILE` | 主机清单路径 | 同目录 `tdsql_hosts` |
| `-g, --group NAME` | 要执行的主机分组 | `tdsql_allmacforcheck` |
| `--bin FILE` | 底层 `sshpass_pack.sh` 路径 | `/data/application/scheduler/bin/sshpass_pack.sh` |
| `--scheduler-xml FILE` | `scheduler.xml` 路径（可被 `[sshpass_pack].scheduler_xml` 覆盖） | `/data/application/scheduler/conf/scheduler.xml` |
| `--user USER` | 远程用户 | CLI > `[sshpass_pack].user` > `scheduler.xml` > `tdsql` |
| `--port PORT` | 远程端口 | CLI > `[sshpass_pack].port` > `scheduler.xml` > `8966` |
| `--workdir DIR` | 远程工作目录 | CLI > `[sshpass_pack].oc_dir` > `scheduler.xml` > `/data/oc_agent/bin` |
| `--flag VALUE` | 底层固定参数 | `[sshpass_pack].flag` > `0` |
| `--timeout SEC` | 底层超时参数 | `[sshpass_pack].timeout` > `10` |
| `--list-groups` | 列出所有分组并退出 | - |
| `--dry-run` | 只打印底层命令，不实际执行 | - |
| `-q, --quiet` | 减少 stderr 提示信息 | - |
| `-h, --help` | 显示帮助信息 | - |
| `-V, --version` | 显示版本号 | - |

## 主机清单格式

脚本直接复用 `tdsql_hosts` 的分组格式，例如：

```ini
[tdsql_db]
tdsql_db1 ansible_ssh_host=10.206.0.15
tdsql_db2 ansible_ssh_host=10.206.0.16

[tdsql_proxy]
tdsql_proxy1 ansible_ssh_host=10.206.0.21
```

## 使用示例

```bash
# 查看所有分组
sh sshpass_pack_exec.sh --list-groups

# 在 scheduler 机器上直接执行
sh sshpass_pack_exec.sh -g tdsql_db -- "df -h | grep dev"

# 指定 scheduler.xml 路径
sh sshpass_pack_exec.sh --scheduler-xml /data/application/scheduler/conf/scheduler.xml -g tdsql_db -- "hostname && uptime"

# 在非 scheduler 机器上手工传 token
sh sshpass_pack_exec.sh -t 'your_token' -g tdsql_proxy -- "hostname && uptime"

# 使用环境变量传 token
export SSHPASS_PACK_TOKEN='your_token'
sh sshpass_pack_exec.sh "cat /etc/hosts"

# 预览底层实际调用（token 会被隐藏）
sh sshpass_pack_exec.sh -g tdsql_scheduler --dry-run "hostname"
```

## 输出说明

脚本会严格按照 `tdsql_hosts` 中的顺序执行，并按以下形式输出：

```text
============================================================
[1/2] 10.206.0.15 (tdsql_db1)
------------------------------------------------------------
状态: 成功
/dev/vda1        100G   60G   40G  60% /

============================================================
[2/2] 10.206.0.16 (tdsql_db2)
------------------------------------------------------------
状态: 成功
/dev/vda1        100G   58G   42G  58% /
```

## 注意事项

- 远程命令建议整体加双引号，例如 `"df -h | grep dev"`
- 本脚本默认**串行执行**，确保输出顺序和主机清单一致
- `token` 属于敏感信息，`dry-run` 模式下会自动隐藏，不会直接回显
- 推荐优先执行只读命令，避免批量写操作带来误操作风险
- 监控分析命令中的 `$` 符号在远程传递时需要用 `\$` 转义，避免被本地 shell 提前解释
