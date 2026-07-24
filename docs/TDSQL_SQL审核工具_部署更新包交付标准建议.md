# TDSQL SQL审核工具 — 部署更新包交付标准建议

> **编制目的**：规范开发团队每次版本发布时部署更新包和部署文档的交付标准，确保运维/自动化部署人员能够一次性顺利部署，无需反复沟通修复。
>
> **适用版本**：v1.2.0.4 及以后
>
> **生产环境信息**：
> - 操作系统：银河麒麟 V10 SP3 (Halberd) / CentOS / RHEL
> - 系统架构：x86_64
> - 系统自带 Python：3.7.9（不足以运行新版本）
> - 便携 Python：3.11.11，安装路径 `/opt/python311/python/bin/python3.11`
> - 数据库：TDSQL 集中式实例（MySQL 协议），独立服务器
> - 部署方式：systemd 裸机部署，install.sh 一键安装
> - 网络环境：纯内网隔离环境，无外网

---

## 一、历史部署问题汇总

以下问题按出现频率和严重程度排序，均来源于 v1.2.0.4 ~ v1.2.0.6 实际部署经历。

### 问题 1：wheels 目录缺少 uvloop，导致依赖安装失败

**出现版本**：v1.2.0.4、v1.2.0.5、v1.2.0.6
**严重程度**：🔴 高（阻断部署流程）

**现象**：
- `requirements.txt` 中写的是 `uvicorn[standard]>=0.34.0`
- 该 extra 依赖 `uvloop`、`httptools`、`python-dotenv`、`pyyaml`
- wheels 目录中缺少 `uvloop` wheel 包
- `pip install --no-index --find-links=wheels -r requirements.txt` 最终失败

**影响**：安装脚本 `deploy/install.sh` 第 4 步直接退出，服务无法启动

**修复方式**：每次部署必须手动修改 `requirements.txt`，将 `uvicorn[standard]>=0.34.0` 改为 `uvicorn==0.51.0`（或确保 uvloop wheel 存在于 wheels 目录）

**建议**：
1. 要么在 `requirements.txt` 中写 `uvicorn==0.51.0`（不使用 extra），要么在 wheels 目录中完整提供 uvloop 包
2. 要么在 `deploy/env.template` 中添加说明：如果 wheels 缺少 uvloop，需将 requirements.txt 中的 `uvicorn[standard]` 改为 `uvicorn`
3. 安装脚本应在 `pip install` 失败时给出明确的解决方案提示

---

### 问题 2：便携 Python 未内置到发布包中

**出现版本**：v1.2.0.6（v1.2.0.4/0.5 使用手动部署方式，无此问题）
**严重程度**：🔴 高（阻断部署流程）

**现象**：
- `deploy/preflight_check.sh` 预检查找 `python3.9+`，仅扫描系统 PATH
- 本机 Python 3.7.9 不满足 FastAPI >= 0.115.0 要求
- 便携 Python 安装在 `/opt/python311/python/bin/python3.11`，不在 PATH 中
- install.sh 预检直接失败

**影响**：预检不通过，`install.sh` 无法继续执行

**修复方式**：每次必须在执行 install.sh 前手动 export PATH 加入便携 Python

**建议**：
1. **推荐方案**：使用 `make_release.sh --with-python` 将便携 Python 打包到发布包中的 `python/` 目录（v1.2.0.3 的备份中有 python-runtime，v1.2.0.6 的 tar 包中没有）
2. **备选方案**：在部署文档中明确写明"便携 Python 安装在 /opt/python311/python/bin/python3.11，部署前需将其加入 PATH"

---

### 问题 3：install.sh 中 VERSION 硬编码为 "1.2.0.0"

**出现版本**：v1.2.0.6
**严重程度**：🟡 中（不影响功能但影响版本管理）

**现象**：
- `deploy/install.sh` 第 12 行 `VERSION="1.2.0.0"` 写死为模板版本号
- 导致 `/opt/tdsql-sqlcheck/releases/v1.2.0.0/VERSION` 文件内容为 `1.2.0.0`
- 而 health 端点返回的是后端代码中的版本号（1.2.0.6），两者不一致

**修复方式**：部署后手动修正 VERSION 文件

**建议**：
1. install.sh 应从发布包版本号自动提取，而非硬编码
2. 或在部署文档中明确说明："部署后需手动修正 /opt/tdsql-sqlcheck/releases/v1.x.x.x/VERSION 文件中的版本号"

---

### 问题 4：前端文件打包后版本号未同步更新

**出现版本**：v1.2.0.6（已发现并修复）
**严重程度**：🟡 中（用户体验影响）

**现象**：
- 部署后首页标题仍显示 V1.2.0.5
- 经排查，`/opt/tdsql-sqlcheck/frontend/` 下存在旧版 index.html（V1.2.0.5）
- 但 `releases/v1.2.0.0/frontend/` 下 index.html 版本正确为 V1.2.0.6
- 最终通过排查发现是当前部署目录和 releases 目录分离导致

**影响**：前端版本标识与实际版本不一致，可能引起混淆

**建议**：
1. 发布前确保前端构建产物（frontend/static/js/app.js、frontend/index.html）中的版本号已正确更新
2. 打包时应使用干净的构建产物目录，避免旧版本文件残留

---

### 问题 5：部署文档缺少环境变量配置说明

**出现版本**：v1.2.0.6（v1.2.0.4/0.5 使用手动部署，无此问题）
**严重程度**：🟡 中（增加部署人工成本）

**现象**：
- install.sh 需要 `deploy/.env` 文件，其中必须包含数据库连接信息
- v1.2.0.6 的部署手册只写了"复制 env.template 为 deploy/.env"
- 但没有说明 .env 中必须填写哪些关键字段
- 部署文档（v1.2.0.6_upgrade_manual.md）中没有 .env 配置指引

**修复方式**：手动查看 env.template 和 install.sh 源码来推断配置项

**建议**：
1. 部署文档中必须包含一份已填写的 `.env` 配置示例（隐藏敏感信息）
2. 或明确列出 .env 中必填字段及其含义

---

### 问题 6：deploy/install.sh 依赖 rollback.sh 但未打包

**出现版本**：v1.2.0.6（install.sh 中引用了 rollback.sh）
**严重程度**：🟢 低（不影响正常部署，但影响回滚能力）

**现象**：
- `deploy/install.sh` 的日志输出中提到了回滚命令：`回滚: bash ${SCRIPT_DIR}/rollback.sh`
- 但解压后 `deploy/` 目录中不存在 `rollback.sh`
- 如果部署失败需要回滚，无现成脚本可用

**建议**：
1. 如果提供 install.sh 就应该同时提供 rollback.sh
2. 或移除安装脚本中对 rollback.sh 的引用

---

### 问题 7：v1.2.0.5 和 v1.2.0.6 的 AUTH_SECRET_KEY 不一致

**出现版本**：v1.2.0.6（install.sh 首次部署时自动生成新 key）
**严重程度**：🟡 中（影响已登录用户的 token 有效性）

**现象**：
- v1.2.0.4/0.5 手动部署时 .env 中有预置的 `AUTH_SECRET_KEY`
- v1.2.0.6 install.sh 检测到 AUTH_SECRET_KEY 为空时自动生成新 key
- 虽然不影响新登录用户，但会导致已有 token 的用户掉线

**建议**：
1. `deploy/env.template` 中 AUTH_SECRET_KEY 不应留空，应提供占位符说明
2. install.sh 保留 .env.new 供比对，但应优先使用已存在的 AUTH_SECRET_KEY（升级场景）

---

## 二、部署包交付检查清单

请开发团队在每次发布时确保以下内容完整：

### A. 更新包内容

| 项目 | 必填 | 说明 |
|------|------|------|
| `tdsql-sqlcheck-vX.Y.Z-linux-x86_64.tar.gz` | ✅ | 完整发布包，包含 backend/、frontend/、deploy/、wheels/ |
| `tdsql-sqlcheck-vX.Y.Z-linux-x86_64.tar.gz.sha256` | ✅ | SHA256 校验文件 |
| `deploy/install.sh` | ✅ | 一键安装脚本 |
| `deploy/preflight_check.sh` | ✅ | 预检脚本 |
| `deploy/verify_deploy.sh` | ✅ | 部署验证脚本 |
| `deploy/env.template` | ✅ | 环境变量模板 |
| `deploy/rollback.sh` | ⚠️ | 回滚脚本（建议提供） |
| `deploy/tdsql-sqlcheck.service` | ✅ | systemd 服务文件 |
| `wheels/` | ✅ | 完整 wheel 依赖，**必须包含 uvloop** |
| `python/` 或 `python-runtime/` | ⚠️ | 便携 Python（建议内置） |

### B. 代码与配置文件要求

| 项目 | 要求 |
|------|------|
| `requirements.txt` | uvicorn 版本必须与 wheels 目录一致，**不要使用 uvicorn[standard]** |
| `VERSION` 文件 | 与发布版本号一致（如 `1.2.0.6`） |
| `deploy/install.sh` 中的 VERSION | 与发布版本号一致，**不要硬编码为 "1.2.0.0"** |
| `frontend/index.html` | 版本标题与发布日期均已更新 |
| `frontend/static/js/app.js` | 版本号已更新 |
| `backend/config.py` | `APP_VERSION` 与发布版本号一致 |
| `deploy/env.template` | AUTH_SECRET_KEY 提供占位符说明，非必填项标注"留空则自动生成" |

### C. 部署文档要求

部署文档（`vX.Y.Z_upgrade_manual.md`）必须包含以下内容：

| 章节 | 必填 | 说明 |
|------|------|------|
| 本次核心变更说明 | ✅ | 修复了什么问题、新增了什么功能 |
| 升级路径 | ✅ | 从哪些版本可升级到本版本 |
| 部署包校验命令 | ✅ | SHA256 校验指令 |
| .env 配置示例 | ✅ | 列出必填字段及填写说明（数据库地址/端口/用户名/密码） |
| 脏数据清理 SQL | ✅ | 如存在需要清理的历史数据，提供完整 SQL |
| 完整部署指令 | ✅ | 从停止服务到健康检查的完整 bash 指令 |
| 预期健康检查输出 | ✅ | `curl localhost:8000/health` 的期望返回 |
| 便携 Python PATH 说明 | ✅ | 如果未内置便携 Python，必须说明如何加入 PATH |
| 已知问题与已知限制 | ⚠️ | 如有已知问题，在此说明 |
| 回滚步骤 | ⚠️ | 升级失败或版本异常时的回滚方法 |

---

## 三、给开发团队的具体建议

### 打包方面

1. **wheels 完整性**：每次 `make_release.sh` 打包时，必须确保 wheels 目录包含 `requirements.txt` 中所有包及其依赖的 wheel。特别注意 `uvicorn[standard]` 所依赖的 `uvloop`、`httptools`、`python-dotenv`、`pyyaml`。如果某个平台没有对应的 wheel，要么排除该 extra，要么手动补全。

2. **内置便携 Python**：强烈建议在 `make_release.sh` 中加入 `--with-python` 选项，将便携 Python 打包到发布包中（如 `python/` 目录）。这样安装脚本可以自动使用内置 Python，不再依赖服务器上的 PATH 配置。这符合"纯内网隔离环境"的部署场景。

3. **版本号一致性**：发布包中的版本号必须在以下位置保持一致：
   - tar.gz 包名中的版本号
   - 根目录 VERSION 文件
   - deploy/install.sh 中的 VERSION 变量
   - 后端 config.py 中的 APP_VERSION
   - 前端 index.html 中的版本标识

4. **前端构建产物**：确保前端打包使用的是最新构建产物。建议在 CI/CD 流水线中完成前端构建，然后自动打包到发布包中，避免手动替换时遗漏。

### 文档方面

1. **部署文档模板化**：建议统一部署文档的结构，每次更新只需填写变更部分。模板应包含：
   - 变更说明
   - 环境预检要求
   - 完整部署步骤（含 .env 配置）
   - 脏数据清理（如有）
   - 健康检查
   - 已知问题
   - 回滚步骤

2. **.env 配置说明**：部署文档中应提供 .env 的完整字段说明，标注哪些是必填、哪些是可选。如果环境有变化（如新增环境变量），必须在文档中说明。

3. **版本差异说明**：如果某版本的部署方式或配置要求有变化（如 v1.2.0.6 首次使用 install.sh），必须在文档中明确标注"**本次更新部署方式有变化**"。

### 测试方面

1. **发布前自测部署流程**：建议在内部环境完整跑一遍从解压到部署到健康检查的完整流程，确保所有文件齐全、所有脚本可执行、所有依赖可安装。

2. **自动化部署验证**：建议将部署步骤写入 CI 流水线，每次发布前自动执行。这比人工检查更可靠。

---

## 四、一次性顺利部署的期望交付格式

```
tdsql-sqlcheck-v1.2.0.7-linux-x86_64/          # 解压后根目录
├── VERSION                                     # 内容: 1.2.0.7
├── backend/                                    # 后端代码
├── frontend/                                   # 前端构建产物
│   ├── index.html                              # 版本号: V1.2.0.7
│   └── static/
│       ├── js/app.js                           # 版本号已更新
│       └── css/app.css
├── deploy/
│   ├── env.template                            # 含完整字段说明
│   ├── install.sh                              # VERSION=1.2.0.7
│   ├── preflight_check.sh
│   ├── verify_deploy.sh
│   ├── rollback.sh                             # 提供回滚脚本
│   └── tdsql-sqlcheck.service
├── wheels/
│   ├── fastapi-0.139.2-py3-none-any.whl
│   ├── uvicorn-0.51.0-py3-none-any.whl
│   ├── uvloop-0.15.1-cp311-cp311-manylinux*.whl  # ✅ 必须包含
│   └── ...
├── python/                                     # 可选：内置便携 Python
│   └── bin/python3
└── requirements.txt                            # uvicorn==0.51.0 (无 [standard])

docs/
├── v1.2.0.7_upgrade_manual.md                  # 部署手册
├── env.example                                 # .env 配置示例（可选）
└── rollback-guide.md                           # 回滚指南（可选）
```

---

## 五、总结

| 序号 | 问题 | 严重程度 | 建议优先级 |
|------|------|----------|-----------|
| 1 | wheels 缺少 uvloop | 🔴 高 | P0 — 下次发布必须修复 |
| 2 | 便携 Python 未内置 | 🔴 高 | P0 — 强烈建议内置 |
| 3 | install.sh VERSION 硬编码 | 🟡 中 | P1 — 建议修复 |
| 4 | 前端版本号未同步 | 🟡 中 | P1 — 建议修复 |
| 5 | 部署文档缺 .env 配置说明 | 🟡 中 | P1 — 必须补充 |
| 6 | rollback.sh 缺失 | 🟢 低 | P2 — 建议提供 |
| 7 | AUTH_SECRET_KEY 处理 | 🟡 中 | P1 — 建议优化 |

**核心目标**：每次发布后，运维/自动化人员拿到更新包应能**一次性完成部署**，无需人工干预修复配置、补充文件或修改代码。
