# TDSQL SQL审核工具 v1.6.3.7 内网测试环境增量更新部署手册

| 属性 | 内容 |
|---|---|
| **目标软件版本** | `v1.6.3.7`（在线元数据提取SQL文件命名规则还原、时基对齐精准筛选、防重排序增强） |
| **基线源版本** | `v1.6.3.6`（或 `v1.6.3.5` / `v1.6.3.4`，内网测试环境当前运行版本） |
| **升级方式** | **增量更新（Releases 增量平滑升级，直接复用既有虚拟环境 venv）** |
| **发布介质** | `tdsql-sqlcheck-v1.6.3.7-patch.tar.gz`（及其 `.sha256` 校验和文件） |
| **目标测试服务器** | `10.243.16.252`（银河麒麟 Advanced Server V10 SP3，海光 x86_64 CPU） |
| **操作执行端** | 内网 Windows 部署机（已与 `root@10.243.16.252` 建立 SSH 免密信任） |
| **测试元数据库** | `10.243.16.252` 本地 MySQL 8.0.28（端口 `3306`，数据库 `tdsql_sqlcheck`） |
| **安装包上传目录** | 测试服务器 `/tmp/dist/` 目录 |

---

## 零、 针对本次升级与历史踩坑的“10项铁律与安全防呆”（内网智能体必读）

本次增量部署方案全面继承历史部署实战经验与规约，内网智能体在执行部署时，**务必严格遵守以下 10 项铁律，切勿违规操作**：

> [!CAUTION]
> ### 铁律 1：严禁重新安装 venv 依赖或执行 pip install！必须直接复用既有虚拟环境！
> - **历史血泪教训**：银河麒麟系统自带的 `/usr/local/bin/python3.11` 存在环境缺陷（标准库缺失 `encodings` 模块）。一旦重新运行 `python -m venv` 或 `pip install`，会抛出致命错误：`ModuleNotFoundError: No module named 'encodings'`。
> - **安全防呆方案**：本次从 `v1.6.3.6` 到 `v1.6.3.7` 的 Python 依赖清单（`requirements.txt`）**完全零变更**！
> - 增量升级脚本已实现自动从既有运行目录（`current/venv`）完整拷贝继承虚拟环境，秒级完成且 100% 杜绝 Python 解释器崩溃陷阱。**绝对不可在测试机重新创建 venv 或执行 pip install！**

> [!CAUTION]
> ### 铁律 2：绝对严禁覆盖或重置 `.env`，必须严格保持本地测试元数据库连接！
> - **测试环境核心保护**：测试服务器使用的元数据库是本地 MySQL 8.0.28（`127.0.0.1:3306`，数据库 `tdsql_sqlcheck`）。
> - 部署过程中绝对不可覆盖 `/opt/tdsql-sqlcheck/.env`；
> - 严禁修改为外部生产 TDSQL 集群地址（`10.243.16.238`），严防测试操作与测试数据污染生产环境！

> [!IMPORTANT]
> ### 铁律 3：加密密钥 `encryption.key` 绝对延续传承！
> - 系统纳管的各 TDSQL 数据库实例连接密码均基于 AES 对称密钥加密存储；
> - 新发布目录 `/opt/tdsql-sqlcheck/releases/v1.6.3.7/data/encryption.key` 与测试环境现网 `.env` 中的 `TDSQL_ENCRYPTION_KEY` 必须保持 44 位完全一致；
> - 升级脚本会自动同步该文件，确保已有实例连接密码解密正常，绝不发生 `InvalidToken` 故障。

> [!IMPORTANT]
> ### 铁律 4：严格遵循 Releases 物理隔离与软链接原子切换！
> - 必须将新版本独立部署至 `/opt/tdsql-sqlcheck/releases/v1.6.3.7`；
> - 增量文件写入并校验无误后，通过 `ln -sfn /opt/tdsql-sqlcheck/releases/v1.6.3.7 /opt/tdsql-sqlcheck/current` 实现原子切换；
> - 旧版本必须原样完整保留，支持发生任何非预期异常时通过单行命令**秒级无损回滚**！

> [!IMPORTANT]
> ### 铁律 5：必须严格遵循“先 Runner、后 Web”启动契约！
> - 系统包含独立后台执行器服务 `tdsql-metadata-runner.service` 与 Web 服务 `tdsql-sqlcheck.service`；
> - **启动顺序铁律**：升级脚本会**先重启并校验 `tdsql-metadata-runner`** 处于 `active (running)` 状态后，**再重启 `tdsql-sqlcheck` Web 服务**；若 runner 启动失败，升级脚本强制报错退出，严防服务不同步。

> [!NOTE]
> ### 铁律 6：元数据库无需手工建表，表结构完全向前兼容！
> - 本次升级数据库结构零变更，无需执行任何 DDL 变更脚本；升级完成后两项后台服务将无缝读写现有元数据库。

> [!NOTE]
> ### 铁律 7：在轨升级 8000 端口占用属于正常现象，绝不作为阻断项！
> - 升级过程中，旧版 `tdsql-sqlcheck` 服务仍在监听 8000 端口属于正常现象，脚本平滑重启后将自动接管端口。

> [!WARNING]
> ### 铁律 8：旧同步接口 `/api/v1/audit/extract-and-audit` 严格退役，返回 410 Gone！
> - 旧的同步阻塞接口已彻底废弃，前端已全部调用新异步状态机接口。

> [!IMPORTANT]
> ### 铁律 9：`deploy/verify_deploy.sh` 自动化验证作为硬性放行门禁！
> - 部署完成后必须执行 `verify_deploy.sh`，以输出 `PASS`（无 FAIL/SKIP 报错）作为部署成功的标准。

> [!CAUTION]
> ### 铁律 10：【重要裁定】严禁/无需在测试环境执行任何存量历史数据订正 SQL 脚本！
> - **背景与架构裁决**：在 v1.6.3.7 开发阶段所编写的 `deploy/patch_v1637_source.sql`，经独立智能体 D 的 SIT 复测与 UAT 验证，指出其在存量转换的幂等性与时间格式上存在缺陷，判定为“生产存量订正脚本不可执行”；项目主管 Mr.Linsang 已正式签署批准该意见。
> - **测试环境执行规范**：本次测试环境增量部署为**纯应用代码与前端静态资源更新**，**绝对不要、也无需在测试机数据库上执行任何数据订正脚本**！
> - **业务预期**：测试环境此前在 v1.6.3.6 运行期间留存的历史旧记录将保留现状（纯只读）；从 v1.6.3.7 部署生效起，**所有新发起的在线元数据提取与审核任务，提取 SQL 文件名 100% 严格遵循经典规范，时间对齐本地，筛选与下载完全闭环**！

---

## 一、 本版核心变更与问题修复清单 (v1.6.3.6 ➔ v1.6.3.7)

本次 `v1.6.3.7` 为**本轮整改本体**（不称补丁，不递增小版本），重点解决以下用户反馈的核心问题：

### 1.1 在线元数据提取生成的 SQL 文件命名规则彻底还原
- **原故障现象**：在 v1.6.3.6 中对数据库扫描后，在“历史元数据审核记录”中提取生成的 SQL 文件名格式变成了 `extracted_{db}_{job_id[:8]}.sql`（例如 `extracted_lzbj_ecif_a27123e1.sql`），篡改了原有的业务时间戳命名规则，给用户排查报告与归档造成困扰。
- **修复方案**：彻底去除 `job_id[:8]` 哈希后缀，还原为经典标准的命名格式：
  $$\mathbf{extracted\_\{db\_name\}\_\{YYYYMMDD\_HHMMSS\}.sql}$$
  例如：`extracted_lzbj_ecif_20260912_161741.sql`。

### 1.2 时基全面对齐本地时间（解决列表早 8 小时错位）
- **原故障现象**：Worker 在将元数据审核记录持久化落库时，部分时间字段写入了 UTC 时间，导致在“历史元数据审核记录”列表上展示的提取时间比实际本地操作时间提前了 8 小时（如下午 16:00 提取的记录显示为上午 08:00），且按“开始日期 = 今天”无法筛出当天记录。
- **修复方案**：
  1. Worker 内部生成的文件名时间戳、`audit_history.created_at`、快照 `scan_snapshots.scan_started_at` 以及 HTML 报告头部的生成时间全面对齐为**系统本地时间（北京时间）**；
  2. 彻底解决时间倒错，在前端选择“今天”能 100% 精准筛选出当天全部操作记录。

### 1.3 列表稳定排序与前端新增显式「报告ID」列
- **原故障现象**：如果用户在同一秒内连续触发审核，同秒同名记录可能发生相对位置跳跃，且界面上缺乏便于口头沟通和工单索引的唯一标识。
- **修复方案**：
  1. 后端查询 SQL 增加 `ORDER BY h.created_at DESC, h.id DESC` 稳定排序二级键；
  2. 前端“历史元数据审核记录”表格新增 **`#报告ID`**（`#{{ row.id }}`）列，用户与运维可基于 `#ID`（如 `#1386`）绝对定位任何一条历史记录。

### 1.4 下载接口与文件名一致性闭环
- **修复效果**：点击历史记录操作栏的「下载 .sql」，后端响应头 `Content-Disposition: attachment; filename=extracted_...` 返回的文件名与列表中展示的文件名**逐字完全一致**；点击「下载 HTML 报告」返回 `Extracted_Schema_Report_{id}.html`。

### 1.5 代码健壮性加固与全量自动化回归
- 清理历史无用死代码 `_utcnow()`；
- 新增 L1~L6 六道回归防护锁，并经真实变异测试（杀死 UTC 与 UUID 变异）自证有效；
- 经智能体 D 独立复测，全量回归套件 **2146 passed / 0 failed / 30 skipped** 全部通过，UAT 真实浏览器 4 个业务场景 100% 准出。

---

## 二、 升级前检查与准备工作（内网测试机）

### 2.1 检查当前版本与运行状态
在测试服务器 `10.243.16.252` 终端上执行：
```bash
# 1. 检查当前运行版本
cat /opt/tdsql-sqlcheck/current/VERSION
# 预期输出: 1.6.3.6 (或 1.6.3.5 / 1.6.3.4)

# 2. 检查两项核心服务状态
systemctl status tdsql-metadata-runner.service --no-pager
systemctl status tdsql-sqlcheck.service --no-pager
# 预期状态均为: active (running)
```

### 2.2 上传增量更新包并校验完整性
在 Windows 部署机使用 SCP 或 WinSCP 将构建好的补丁介质上传至测试机 `/tmp/dist/`：
- `tdsql-sqlcheck-v1.6.3.7-patch.tar.gz`
- `tdsql-sqlcheck-v1.6.3.7-patch.tar.gz.sha256`

在测试服务器终端执行校验：
```bash
mkdir -p /tmp/dist
cd /tmp/dist

# 校验 SHA256 哈希值
sha256sum -c tdsql-sqlcheck-v1.6.3.7-patch.tar.gz.sha256
# 预期输出: tdsql-sqlcheck-v1.6.3.7-patch.tar.gz: OK
```
> [!CAUTION]
> 如果校验输出不是 `OK`，说明传输包损坏或不完整，严禁继续执行部署！

---

## 三、 增量更新升级实施步骤（一键秒级部署）

### 3.1 解压补丁介质包
```bash
cd /tmp/dist
tar -zxvf tdsql-sqlcheck-v1.6.3.7-patch.tar.gz
cd tdsql-sqlcheck-v1.6.3.7-patch
```

### 3.2 运行自动化增量更新脚本
```bash
# 赋权并执行升级脚本
chmod +x deploy/upgrade_incremental.sh
bash deploy/upgrade_incremental.sh /opt/tdsql-sqlcheck 8000
```

### 3.3 升级脚本执行关键过程确认
脚本执行过程中，请观察输出的关键步骤标识：
1. `[1/6] 部署 v1.6.3.7 增量应用代码、静态资产与运维脚本...`（创建 `releases/v1.6.3.7`）
2. `[2/6] 复用既有虚拟环境 (venv)...`（从既有目录拷贝 venv，**零依赖安装风险**）
3. `[3/6] 同步并校验加密密钥 (encryption.key)...`（继承现网 44 位加密密钥）
4. `[4/6] 切换 current 软链接 ➔ releases/v1.6.3.7...`（原子切换）
5. `[5a/6] 安装/启动元数据审核执行器 tdsql-metadata-runner...`（**先启动 runner 并确认 active**）
6. `[5/6] 重启 systemd 服务 tdsql-sqlcheck...`（**后启动 Web 服务**）
7. `[6/6] 等待服务就绪并执行部署后验证...`（调用 `verify_deploy.sh` 自动冒烟）

---

## 四、 部署后自动化检验与业务准出验收（傻瓜式指引）

### 4.1 执行一键自动化部署检验门禁
在测试服务器上确认自动检验输出，或手工再次执行：
```bash
bash /opt/tdsql-sqlcheck/current/deploy/verify_deploy.sh --port 8000
```
**放行门禁标准**：
```text
════ 验证结果: PASS=12 FAIL=0 SKIP=0 ════
部署验证全部通过
```
若出现任何 `FAIL` 或 `SKIP`，必须立即排查或执行回滚。

### 4.2 验证版本号与静态资产防缓存
在终端执行 curl 检查接口响应：
```bash
# 检查 API 版本
curl -s http://127.0.0.1:8000/api/version
# 预期输出包含: "version":"1.6.3.7"

# 检查前端静态页面标题版本
curl -s http://127.0.0.1:8000/ | grep -o 'V1\.6\.3\.[0-9]'
# 预期输出: V1.6.3.7
```

### 4.3 核心业务验收操作指引（内网智能体测试步骤）

请内网智能体在浏览器（建议使用无痕模式，或 `Ctrl+F5` 强制刷新）中打开测试环境：
`http://10.243.16.252:8000/`，登录后进行以下 4 项业务核验：

#### 【用例 1】发起在线元数据审核任务
1. 点击左侧导航栏 **「在线元数据审核」**；
2. 实例下拉框选择任意可用测试实例（例如：`D36-分布式库` 或现有测试实例），数据库选择任意测试库；
3. 点击右侧蓝色按钮 **「拉取元数据并执行文件审核」**；
4. 观察页面顶部的任务卡片，应经历 `SUBMITTED` ➔ `RUNNING` ➔ 最终平稳收敛至 **`SUCCEEDED`**（阶段为 `DONE`）。

#### 【用例 2】核验历史记录文件名与时间（核心验证点）
1. 页面向下滚动至 **「历史元数据审核记录」** 表格；
2. **核验表头**：确认在「提取生成的SQL文件」左侧存在 **`#报告ID`** 列（例如 `#1387`）；
3. **核验文件名**：确认刚刚生成的最新记录文件名格式为：
   `extracted_{库名}_{YYYYMMDD_HHMMSS}.sql`
   - **正确示范**：`extracted_uat_d_1636_dist_20260912_163512.sql`；
   - **绝对不应出现**：`extracted_..._a27123e1.sql` 等 8 位随机哈希命名；
4. **核验提取时间**：确认「提取时间」显示为**当前的系统本地时间**（如 `2026-09-12 16:35`），与文件名中的时间戳一致，**绝不提前 8 小时**。

#### 【用例 3】核验文件下载与命名一致性
1. 在最新一条历史记录的操作栏中，点击 **「下载 .sql」**；
2. 确认浏览器下载的文件名与表格中显示的文件名**逐字一致**；
3. 点击 **「下载 HTML 报告」**，确认报告能顺利打开且内容完整。

#### 【用例 4】核验按当天日期筛选
1. 在历史记录上方的筛选栏中，「开始日期」选择**今天**（`2026-09-12`），点击 **「查询」**；
2. 确认刚才执行的任务记录能够被**精准筛出并展示在列表中**（不再出现以前因 UTC 时差导致当天记录被筛漏的缺陷）。

---

## 五、 秒级无损应急回滚方案

如果在升级过程中或部署验证后发现任何非预期异常，可直接在测试服务器执行以下命令实现秒级无损回滚：

```bash
# 1. 查询升级前记录的上一个版本软链接路径
PREV_RELEASE=$(cat /opt/tdsql-sqlcheck/.previous_release)
echo "正在回滚至旧版本: ${PREV_RELEASE}"

# 2. 原子切回旧版本软链接
ln -sfn "${PREV_RELEASE}" /opt/tdsql-sqlcheck/current

# 3. 先重启后台 runner 执行器
systemctl restart tdsql-metadata-runner

# 4. 后重启 Web 服务
systemctl restart tdsql-sqlcheck

# 5. 验证回滚结果
cat /opt/tdsql-sqlcheck/current/VERSION
systemctl status tdsql-sqlcheck --no-pager
```
回滚完成后，系统将完全恢复至升级前的运行状态。

---

**手册编制**：Antigravity 交付团队  
**基线状态**：经 SIT 复测（4/4 变异击穿）、UAT 真实浏览器全链路准出  
**日期**：2026-09-12
