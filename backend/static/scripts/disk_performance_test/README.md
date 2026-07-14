# TDSQL 磁盘性能批量测试工具

批量对 TDSQL 所有节点服务器执行磁盘 I/O 性能基准测试，并生成美观的 HTML 可视化报告。

## 🚀 最佳推荐执行流程

> 以下是完整的推荐操作步骤，从配置到测试到查看报告一气呵成。

```bash
cd disk_performance_test

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Step 1: 配置主机列表（编辑 tdsql_hosts，填入实际 IP）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
vim tdsql_hosts

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Step 2: 配置 SSH 免密（首次使用时执行一次即可）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
./setup_ssh_keys.sh --password 'YourSSHPass' -p 36000

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Step 3: 执行磁盘性能测试（已配置免密，直接运行即可）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
./disk_perf_test.sh

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Step 4: 查看 HTML 报告
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ls reports/disk_perf_report_*.html
```

## 测试项目

| 测试类型 | 工具 | 参数 | 说明 |
|---------|------|------|------|
| 顺序写入 | dd | bs=16K, count=65536, oflag=direct | 测试磁盘顺序写入吞吐量 |
| 随机读 | fio | randread, bs=16k, iodepth=32, numjobs=8 | 测试磁盘随机读 IOPS、带宽和延迟 |
| 随机写 | fio | randwrite, bs=16k, iodepth=32, numjobs=8 | 测试磁盘随机写 IOPS、带宽和延迟 |

## 测试路径

- `/data` — 安装目录盘
- `/data1` — 数据盘

## 前置条件

- 目标服务器已安装 `fio` 工具（`yum install -y fio`）
- 已配置 SSH 免密登录（推荐，使用 `setup_ssh_keys.sh` 一键配置）
- 本机已安装 `sshpass`（仅未配置免密时需要）
- 目标服务器测试路径至少有 **16GB** 可用空间

---

## Step 1: 配置主机列表

将 TDSQL 的 `tdsql_hosts` 文件（Ansible inventory 格式）放到本目录下。脚本只读取 `[tdsql_allmacforcheck]` 段中的主机（自动去重）：

```ini
# tdsql_hosts — Ansible inventory 格式
# ⚠️ 脚本只从 [tdsql_allmacforcheck] 段提取测试目标主机
[tdsql_allmacforcheck]
tdsql_mac1 ansible_ssh_host=10.0.1.10
tdsql_mac2 ansible_ssh_host=10.0.1.11
tdsql_mac3 ansible_ssh_host=10.0.1.12
tdsql_mac4 ansible_ssh_host=10.0.1.13

# 以下其他段不会被测试脚本读取
[tdsql_db]
tdsql_db1 ansible_ssh_host=10.0.1.10
...
```

## Step 2: 配置 SSH 免密登录

使用 `setup_ssh_keys.sh` 一键配置所有节点的 SSH 免密登录（首次使用时执行一次即可）：

```bash
# ✅ 推荐：一键配置免密（指定密码和端口）
./setup_ssh_keys.sh --password 'YourSSHPass' -p 36000

# 仅检查当前免密状态（不做任何修改）
./setup_ssh_keys.sh --verify-only

# 指定 SSH 端口为 22
./setup_ssh_keys.sh --password 'YourSSHPass' -p 22

# 模拟运行（不做实际修改，仅查看将要执行的操作）
./setup_ssh_keys.sh --password 'YourSSHPass' --dry-run

# 强制重新生成密钥并分发
./setup_ssh_keys.sh --password 'YourSSHPass' --force
```

**setup_ssh_keys.sh 参数说明：**

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--password <密码>` | SSH 密码（非 verify-only 模式必填） | 无 |
| `-c <hosts文件>` | 指定 tdsql_hosts 文件 | `tdsql_hosts` |
| `-u <用户名>` | SSH 用户名 | `root` |
| `-p <端口>` | SSH 端口 | `36000` |
| `-k <密钥路径>` | SSH 密钥路径 | `~/.ssh/id_rsa` |
| `-b <密钥位数>` | RSA 密钥位数 | `2048` |
| `--force` | 强制重新生成密钥对 | 否 |
| `--verify-only` | 仅验证免密状态 | 否 |
| `--dry-run` | 模拟运行 | 否 |

## Step 3: 执行磁盘性能测试

```bash
# ✅ 推荐：免密模式，直接运行（默认端口 36000，全部主机并行）
./disk_perf_test.sh

# ✅ 推荐（快速测试）：缩小 fio 文件和时长，适合快速验证
./disk_perf_test.sh -s 1G -r 30

# 只测试 /data 盘
./disk_perf_test.sh --paths /data

# 只执行 dd 顺序写测试
./disk_perf_test.sh -t dd

# 串行测试（一台一台执行，避免同时对多台服务器施压）
./disk_perf_test.sh -j 1

# 最多 3 台主机并行测试
./disk_perf_test.sh -j 3

# SSH 端口非默认 36000 时才需要指定
./disk_perf_test.sh --port 22

# 未配置免密时，可通过 -p 传入密码
./disk_perf_test.sh -p 'YourSSHPass'

# 指定其他 tdsql_hosts 文件
./disk_perf_test.sh -c /path/to/other_tdsql_hosts
```

**disk_perf_test.sh 参数说明：**

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `-p, --password <密码>` | SSH 密码（可选，已配置免密时无需指定） | 无 |
| `--port <端口>` | SSH 端口（仅端口非默认时需指定） | `36000` |
| `-c <配置文件>` | 指定 tdsql_hosts 文件 | `tdsql_hosts` |
| `--paths <路径列表>` | 测试路径，逗号分隔 | `/data,/data1` |
| `-s <fio大小>` | fio 测试文件大小 | `10G` |
| `-r <fio时长>` | fio 运行时长（秒） | `120` |
| `-d <dd count>` | dd 测试 count 值 | `65536` |
| `-t <测试类型>` | `all` \| `dd` \| `fio_read` \| `fio_write` | `all` |
| `-o <输出目录>` | 结果输出目录 | `results` |
| `-j <并发数>` | 同时测试的主机数（`0`=全部并行，`1`=串行） | `0`（全部并行） |
| `--skip-cleanup` | 测试后不清理测试文件 | 否 |

## Step 4: 查看报告

测试完成后自动生成 HTML 报告，位于 `reports/` 目录：

```bash
ls reports/disk_perf_report_*.html
```

报告包含：
- **总览对比**：所有主机的 DD 顺序写速度、FIO 随机读/写 IOPS、带宽（MB/s）、延迟对比
- **性能评级**：根据 IOPS 和吞吐量自动评级（优秀/良好/一般/较差）
- **磁盘类型参考**：HDD / SSD / NVMe 标准 IOPS 和读写能力参考值
- **详细结果**：每台主机每个磁盘的完整测试数据
- **导航目录**：快速跳转到各主机/磁盘的详细结果

---

## 性能评级标准

| 指标 | 优秀 | 良好 | 一般 | 较差 |
|------|------|------|------|------|
| DD 顺序写 | ≥500 MB/s | ≥300 MB/s | ≥150 MB/s | <150 MB/s |
| FIO 随机读 IOPS | ≥50K | ≥20K | ≥5K | <5K |
| FIO 随机写 IOPS | ≥30K | ≥10K | ≥3K | <3K |

### 磁盘类型参考值

| 磁盘类型 | 顺序读写 | 随机读 IOPS | 随机写 IOPS | 随机读速度 (16K) | 随机写速度 (16K) |
|---------|---------|------------|------------|-----------------|-----------------|
| HDD（机械硬盘） | 100~200 MB/s | 75~150 | 75~150 | 0.8~1.9 MB/s | 0.8~1.6 MB/s |
| SATA SSD | 500~550 MB/s | 50K~100K | 30K~80K | 312~560 MB/s | 234~530 MB/s |
| NVMe SSD | 2000~7000 MB/s | 200K~1000K | 100K~500K | 1500~5000 MB/s | 780~3500 MB/s |

> **注意**：随机读写速度 = IOPS × BlockSize(16K)，但受接口带宽上限约束（SATA III ≤ 560MB/s，PCIe Gen3 ≤ 3500MB/s，Gen4 ≤ 7000MB/s）

## 文件结构

```
disk_performance_test/
├── disk_perf_test.sh        # 主测试脚本（支持并行测试）
├── setup_ssh_keys.sh        # SSH 免密配置工具
├── generate_report.sh       # HTML 报告生成器（测试完成后自动调用）
├── tdsql_hosts              # 主机配置文件（Ansible inventory 格式）
├── README.md                # 本文档
├── results/                 # 测试结果数据
│   └── <timestamp>/
│       ├── test_params.txt
│       ├── hosts_list.txt
│       └── <host_alias>_<ip>/
│           ├── host_info.txt
│           ├── system_info.txt
│           └── <path>/
│               ├── dd_result.txt
│               ├── fio_randread_result.txt
│               └── fio_randwrite_result.txt
└── reports/                 # HTML 报告
    └── disk_perf_report_<timestamp>.html
```

## 版本记录

### v1.5 (2026-04-22)
- `disk_perf_test.sh` SSH 密码参数改为可选，优先使用 SSH Key 免密认证
- 推荐流程调整为：先用 `setup_ssh_keys.sh` 配置免密 → 再用 `disk_perf_test.sh` 无需带密码直接测试
- 免密模式使用 `BatchMode=yes` 确保无交互式提示
- 未配置免密时仍可通过 `-p` / `--password` 传入密码回退到密码模式

### v1.4 (2026-04-22)
- 修复 `setup_ssh_keys.sh` 主机解析：只从 `[tdsql_allmacforcheck]` 段提取主机（与测试脚本一致）
- 修复 `setup_ssh_keys.sh` 在 `set -e` 下因 `((count++))` 导致静默退出的问题
- 增强 SSH 超时控制：`check_passwordless` 加 `timeout 10`，`distribute_key` 加 `timeout 30`
- 分发公钥前增加 TCP 端口连通性检测，快速排除不可达主机
- 优化错误提示：根据退出码给出具体失败原因（密码错误/超时/连接拒绝等）

### v1.3 (2026-04-21)
- SSH 密码改为通过 `-p` / `--password` 命令行参数传入，不再依赖 `tdsql_env.conf`
- 新增 `--port` 参数支持指定 SSH 端口（默认 36000）
- `setup_ssh_keys.sh` 新增 `--password` 参数传入 SSH 密码
- 移除对 `tdsql_env.conf` 的 SSH 配置依赖

### v1.2 (2026-04-21)
- 新增 `setup_ssh_keys.sh` SSH 免密配置工具
- 支持一键批量配置所有 TDSQL 节点免密登录
- 支持 `--verify-only` 仅检查免密状态
- 支持 `--dry-run` 模拟运行
- 支持 `--force` 强制重新生成密钥

### v1.1 (2026-04-21)
- 改为直接读取 `tdsql_hosts` 文件（Ansible inventory 格式）
- 自动从 `[tdsql_allmacforcheck]` 段提取所有唯一 IP 地址（去重）
- 移除 `tdsql_hosts.conf` 自定义格式

### v1.0 (2026-04-21)
- 初始版本
- 支持 dd 顺序写、fio 随机读/写三项测试
- 支持批量 SSH 远程执行
- 支持串行/并行测试模式
- 生成美观的 HTML 可视化报告（暗色主题、性能评级、对比图表）
