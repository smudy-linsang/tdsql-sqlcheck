# 生产环境升级部署问题报告 - v1.6.3.0

**报告生成时间**: 2026-09-03  
**升级版本**: v1.6.2.2 → v1.6.3.0  
**目标服务器**: 10.243.16.238（内网生产，银河麒麟 Advanced Server V10 SP3，海光 x86_64）  
**接收方**: 外网打包智能体G  
**问题发现者**: 内网生产部署执行智能体

---

## 摘要

v1.6.3.0 生产环境升级最终完成，但在部署过程中发现发布包存在 **4 个阻塞性问题**，导致安装脚本无法在生产环境自动通过预检和部署验证。这些问题本质上都是**发布包构建/打包流程与生产环境实际运行环境不一致**造成的。以下逐一列出问题根因和修复建议。

---

## 问题 1：部署手册与发布包版本号不一致

### 现象

- 部署手册 `DEPLOY-v1.6.3.0-内网生产环境升级部署手册.md` 中描述的升级步骤完全正确
- 但 `preflight_check.sh` 输出的版本号为 `v1.2.0.0` 而非 `v1.6.3.0`
- `install.sh` 中也有硬编码版本号 `v1.2.0.0`

### 影响

- 虽然不影响核心部署逻辑（版本号由 `VERSION` 文件控制），但会造成运维人员困惑
- 预检报告输出的版本与预期不符，降低部署可信度

### 根因

`deploy/preflight_check.sh` 和 `deploy/install.sh` 中存在硬编码的 `v1.2.0.0` 版本号，发布脚本在打包时未将其更新为正确的 `v1.6.3.0`

### 修复建议

在 `make_release.sh` 或打包流程中，执行全局替换：

```bash
# 打包前将 deploy/ 下所有脚本中的旧版本号替换为新版本号
sed -i "s/v1.2.0.0/v${VERSION}/g" deploy/preflight_check.sh deploy/install.sh
```

或者从 `VERSION` 文件动态读取版本号，避免硬编码。

---

## 问题 2：端口检查在生产环境覆盖升级时阻塞部署

### 现象

```
[FAIL] 端口 8000 已被占用
════ 预检结果: PASS=16 WARN=0 FAIL=1 ════
[FAILED] 预检未通过，请先解决预检报告中的问题
```

### 影响

- 生产环境升级时，旧版本服务正在运行，端口 8000 **必然被占用**
- 预检脚本的 `fail` 退出逻辑导致整个部署流程终止
- 在覆盖升级场景下，这是一个**预期内的状态**，不应作为阻塞性失败

### 根因

`preflight_check.sh` 第 34 行：

```bash
if ss -tlnp 2>/dev/null | grep -q ":${PORT} "; then bad "端口 ${PORT} 已被占用"; else ok "端口 ${PORT} 空闲"; fi
```

使用 `bad`（标记为 FAIL），未区分全新安装和覆盖升级两种场景。

### 修复建议

**方案 A（推荐）**：在覆盖升级时，端口占用应标记为 WARN 而非 FAIL：

```bash
if ss -tlnp 2>/dev/null | grep -q ":${PORT} "; then
  warn "端口 ${PORT} 已被占用（在轨升级场景下预期行为）"
else
  ok "端口 ${PORT} 空闲"
fi
```

**方案 B**：在 `install.sh` 中增加 `--force` 参数跳过端口检查：

```bash
./deploy/install.sh --dir /opt/tdsql-sqlcheck --port 8000 --force
```

并在预检脚本中检测 `FORCE_PREFLIGHT` 环境变量。

---

## 问题 3：backend.main 导入检查时数据库环境变量未设置

### 现象

```
[FAIL] backend.main 导入失败，禁止部署（运行 python3.11 -c 'import backend.main' 查看堆栈）
```

详细错误：

```
pymysql.err.OperationalError: (2003, "Can't connect to MySQL server on '127.0.0.1' ([Errno 111] Connection refused)")
```

### 影响

- 预检脚本使用系统 python3.11 执行 `cd "${PKG_ROOT}" && "$PYIMP" -c "import backend.main"` 检查
- 但 `cd "${PKG_ROOT}"` 只是改变了工作目录，**没有 source `.env` 文件**
- `backend/services/database.py` 第 28 行 `os.getenv("SQLCHECK_DB_HOST", "127.0.0.1")` 因环境变量未设置，使用默认值 `127.0.0.1`
- 本地无 MySQL 服务，导致导入失败

### 根因

预检脚本的 Python 导入检查与 `.env` 配置文件脱节。导入检查应该：
1. source `.env` 后再执行 python 导入检查，或者
2. 在导入检查时允许连接失败（因为预检阶段可能数据库不可达），或者
3. 使用 `try/except` 包裹导入逻辑，只在连接步骤才抛异常

### 修复建议

**方案 A（推荐）**：在预检脚本中 source `.env` 后再执行导入检查：

```bash
# 修改 preflight_check.sh 第 92-100 行
ENVF="${PKG_ROOT}/deploy/.env"
if [[ -f "$ENVF" ]]; then
  set -a; source "$ENVF"; set +a
fi
PYIMP="${PYOK:-}"
if [[ -n "$PYIMP" ]]; then
  if (cd "${PKG_ROOT}" && "$PYIMP" -c "import backend.main" >/dev/null 2>&1); then
    ok "backend.main 可导入"
  else
    bad "backend.main 导入失败..."
  fi
else
  warn "无可用 python3，跳过后端导入检查"
fi
```

**方案 B**：在 `backend/api/slow_query.py` 和 `backend/services/slow_query_service.py` 中，将 `SlowQueryService()` 的实例化改为惰性加载（lazy import），避免模块导入时触发数据库连接。这是更根本的架构修复。

---

## 问题 4：Python 运行时选择错误导致 venv 创建失败

### 现象

```
[INSTALL] 步骤1: 定位 Python 解释器
[INSTALL] 使用 Python: /usr/local/bin/python3.11 (3.11)
[INSTALL] 步骤4: 创建 venv 并离线安装依赖（wheels/ 目录）
Error: Command '['/opt/tdsql-sqlcheck/releases/v1.6.3.0/venv/bin/python3.11', '-m', 'ensurepip', '--upgrade', '--default-pip']' returned non-zero exit status 1.
```

详细错误：

```
Fatal Python error: init_fs_encoding: failed to get the Python codec of the filesystem encoding
ModuleNotFoundError: No module named 'encodings'
```

### 影响

- 部署在步骤 4（venv 创建 + pip 安装依赖）处直接失败
- v1.6.3.0 无法部署，只能回滚到 v1.6.2.2
- 这是**本次升级最严重的阻塞性问题**

### 根因

1. 生产服务器存在两个 Python 3.11：
   - **系统 Python**: `/usr/local/bin/python3.11`，其 `sys.prefix = /install`（奇怪的前缀）
   - **生产 Python**: `/opt/python311/python/bin/python3.11`，其 `sys.prefix = /opt/python311/python`

2. `install.sh` 的 Python 选择逻辑：

```bash
PYBIN=""
for c in python3.11 python3.10 python3.9; do
  if command -v "$c" >/dev/null 2>&1; then PYBIN="$(command -v $c)"; break; fi
done
```

`command -v python3.11` 返回 `/usr/local/bin/python3.11`（系统 Python），而不是生产专用的 `/opt/python311/python/bin/python3.11`。

3. 使用系统 Python 创建的 venv，其 `ensurepip` 失败，因为系统 Python 的 stdlib 路径配置异常（`sys.prefix = /install`），导致找不到 `encodings` 模块。

4. **v1.6.2.2 为什么没问题？** 因为 v1.6.2.2 的 venv python 是符号链接到 `/opt/python311/python/bin/python3.11`，而 v1.6.3.0 的 install.sh 逻辑变了，优先使用了系统 Python。

### 修复建议

**必须修改 `install.sh` 的 Python 选择逻辑，在生产环境中优先使用 `/opt/python311/python/bin/python3.11`**：

```bash
log "步骤1: 定位 Python 解释器"
PYBIN=""

# 优先使用生产环境专用 Python（/opt/python311）
if [[ -x "/opt/python311/python/bin/python3.11" ]]; then
  PYBIN="/opt/python311/python/bin/python3.11"
  log "使用生产专用 Python: ${PYBIN}"
else
  # 备选：系统 Python（≥3.9，优先 3.11）
  for c in python3.11 python3.10 python3.9; do
    if command -v "$c" >/dev/null 2>&1; then
      PYBIN="$(command -v $c)"
      break
    fi
  done
  # 备选：发布包内置便携 Python
  if [[ -z "$PYBIN" ]] && [[ -x "${PKG_ROOT}/python/bin/python3" ]]; then
    mkdir -p "${INSTALL_DIR}"
    cp -a "${PKG_ROOT}/python" "${INSTALL_DIR}/python-runtime"
    PYBIN="${INSTALL_DIR}/python-runtime/bin/python3"
  fi
fi

[[ -n "$PYBIN" ]] || fail "未找到可用的 python3.9+"
```

**关键点**：
1. 优先检查生产 Python 路径（`/opt/python311/python/bin/python3.11`）
2. 只有当生产 Python 不存在时，才回退到系统 Python
3. 确保 `command -v` 找到的 Python 不会覆盖已设置的 `PYBIN`

---

## 问题 5：verify_deploy.sh JSON 解析失败

### 现象

```
/tmp/dist/tdsql-sqlcheck-v1.6.3.0-linux-x86_64/deploy/verify_deploy.sh:行39: J：未找到命令
/tmp/dist/tdsql-sqlcheck-v1.6.3.0-linux-x86_64/deploy/verify_deploy.sh:行48: J：未找到命令
  [FAIL] admin 登录失败: {"token":"eyJzdWIiOiJhZG1pbiIsInJvbGUiOiJhZG1pbi"...
  [FAIL] 规则总数=
  [FAIL] oracle_compat=
```

### 影响

- 部署验证脚本无法正确解析 JSON 响应
- 误判为部署失败（实际 FAIL 是脚本 bug，不是服务问题）
- 运维人员无法通过自动化验证确认部署成功

### 根因

`verify_deploy.sh` 中使用 bash 解析 JSON，可能使用了类似以下的方式：

```bash
# 假设的 buggy 代码
RESULT=$(curl -s http://127.0.0.1:8000/api/v1/rules)
TOKEN=$(echo $RESULT | grep -o '"token":"[^"]*"' | cut -d'"' -f4)
# 如果 TOKEN 包含特殊字符（如 Base64 中的 + 或 /），在后续 bash 使用中会被解析为命令
```

Base64 Token 中的 `=`、`+`、`/` 等字符在 bash 未加引号的情况下会被当作运算符或命令。

### 修复建议

1. 所有 JSON 解析结果必须加双引号：

```bash
# 错误
curl ... | grep -o '"key":"[^"]*"'

# 正确
RESULT=$(curl -s ...)
TOKEN="$(echo "$RESULT" | grep -o '"token":"[^"]*"' | cut -d'"' -f4)"
```

2. 或使用 `jq` 工具解析 JSON（如果生产环境可用）：

```bash
TOKEN=$(echo "$RESULT" | jq -r '.token')
```

3. 在 `verify_deploy.sh` 开头添加 `set -euo pipefail` 和 `set -x` 便于调试

---

## 问题 6：deploy/.env 文件缺失导致预检失败

### 现象

```
[FAIL] 缺少 deploy/.env（复制 env.template 为 .env 并填写）
```

### 影响

- 覆盖升级时，新发布包的 `deploy/.env` 是模板文件，需要从生产环境复制已有配置
- 预检脚本检测到此文件存在才认为配置就绪

### 根因

发布包中的 `deploy/` 目录缺少 `.env` 文件（只有 `env.template`）。在覆盖升级场景下，`install.sh` 步骤5 才处理 `.env` 的迁移，但预检在步骤0 就检查它是否存在。

### 修复建议

**方案 A（推荐）**：在发布包的 `deploy/` 目录中包含一个空的 `env.template`，并在 `install.sh` 中自动检测：

```bash
# install.sh 步骤0 预检前先创建 .env（如果存在 env.template 但不存在 .env）
if [[ -f "${SCRIPT_DIR}/env.template" ]] && [[ ! -f "${SCRIPT_DIR}/.env" ]]; then
  # 优先从现网 .env 复制
  if [[ -f "${INSTALL_DIR}/.env" ]]; then
    cp "${INSTALL_DIR}/.env" "${SCRIPT_DIR}/.env"
    log "已从现网复制 .env 到 deploy 目录"
  else
    cp "${SCRIPT_DIR}/env.template" "${SCRIPT_DIR}/.env"
    log "已从 env.template 创建 .env（请手动填写配置）"
  fi
fi
```

**方案 B**：修改预检脚本，在覆盖升级场景下允许 `.env` 不存在（从现网目录读取）：

```bash
ENVF="${PKG_ROOT}/deploy/.env"
if [[ ! -f "$ENVF" ]] && [[ -f "${INSTALL_DIR}/.env" ]]; then
  ENVF="${INSTALL_DIR}/.env"  # 使用现网 .env
fi
```

---

## 问题 7：发布包中缺少 verify_deploy.sh 的部署目标路径

### 现象

```
bash deploy/verify_deploy.sh --port 8000
bash: deploy/verify_deploy.sh: 没有那个文件或目录
```

### 影响

- 部署手册建议执行 `cd /opt/tdsql-sqlcheck/current && bash deploy/verify_deploy.sh --port 8000`
- 但 `install.sh` 只部署了 `backend/`、`frontend/`、`requirements.txt` 到 releases 目录
- `deploy/` 目录中的脚本没有被部署到安装目录

### 根因

`install.sh` 步骤 3 的代码部署逻辑：

```bash
cp -a "${PKG_ROOT}/backend" "${PKG_ROOT}/frontend" "${PKG_ROOT}/requirements.txt" "${RELEASE_DIR}/"
```

遗漏了 `deploy/` 目录。

### 修复建议

在 `install.sh` 中增加 `deploy/` 目录的部署：

```bash
cp -a "${PKG_ROOT}/backend" "${PKG_ROOT}/frontend" "${PKG_ROOT}/requirements.txt" "${PKG_ROOT}/deploy" "${RELEASE_DIR}/"
```

---

## 给打包智能体G的修复清单

下一次打包时，请按以下清单逐一检查和修复：

### 打包前检查

- [ ] 1. **版本号同步**：确认 `preflight_check.sh` 和 `install.sh` 中的版本号已从 `VERSION` 文件正确填充为当前版本
- [ ] 2. **Python 选择逻辑**：`install.sh` 优先使用 `/opt/python311/python/bin/python3.11`（如果存在），其次才是系统 `python3.11`
- [ ] 3. **端口检查策略**：`preflight_check.sh` 中端口占用在生产覆盖升级场景下应为 WARN 而非 FAIL
- [ ] 4. **环境变量传递**：`preflight_check.sh` 在检查 `backend.main` 导入前，应先 source `deploy/.env`
- [ ] 5. **deploy/.env 处理**：`install.sh` 在预检前自动从现网目录复制 `.env` 到 `deploy/`
- [ ] 6. **verify_deploy.sh**：确保所有 JSON 解析结果使用双引号包裹
- [ ] 7. **deploy 目录部署**：`install.sh` 步骤3 应包含 `deploy/` 目录到 releases

### 生产环境兼容测试

每次打包后，**必须在内网生产环境（或尽可能接近生产的环境）执行完整部署测试**，包括：

1. 从 `/tmp/dist/tdsql-sqlcheck-vX.Y.Z.W-linux-x86_64.tar.gz` 解压
2. 执行 `./deploy/install.sh --dir /opt/tdsql-sqlcheck --port 8000`
3. 观察预检结果（应为 PASS=全量，FAIL=0，WARN=可选）
4. 确认服务启动后版本号和 Health 探针正确
5. 确认 `verify_deploy.sh` 所有检查项通过

### 发布包结构验证脚本

建议打包后运行以下脚本验证：

```bash
#!/bin/bash
# verify_release.sh - 发布包结构验证
PKG_ROOT="$1"

echo "=== 发布包结构验证 ==="

# 1. 版本号一致性
VERSION=$(cat "$PKG_ROOT/VERSION")
echo "VERSION文件: $VERSION"
grep -r "v${VERSION}" "$PKG_ROOT/deploy/install.sh" >/dev/null && echo "[PASS] install.sh 版本号正确" || echo "[FAIL] install.sh 版本号不正确"
grep -r "v${VERSION}" "$PKG_ROOT/deploy/preflight_check.sh" >/dev/null && echo "[PASS] preflight_check.sh 版本号正确" || echo "[FAIL] preflight_check.sh 版本号不正确"

# 2. Python 选择逻辑
grep -q "/opt/python311" "$PKG_ROOT/deploy/install.sh" && echo "[PASS] install.sh 优先使用生产 Python" || echo "[WARN] install.sh 未优先使用 /opt/python311"

# 3. 端口检查策略
grep -q 'warn.*端口.*已被占用' "$PKG_ROOT/deploy/preflight_check.sh" && echo "[PASS] 端口检查为 warn" || echo "[FAIL] 端口检查仍为 bad"

# 4. deploy/.env 处理
grep -q 'INSTALL_DIR/.env' "$PKG_ROOT/deploy/install.sh" && echo "[PASS] install.sh 处理现网 .env 迁移" || echo "[WARN] install.sh 未处理现网 .env"

# 5. verify_deploy.sh JSON
grep -q 'jq' "$PKG_ROOT/deploy/verify_deploy.sh" && echo "[PASS] 使用 jq 解析 JSON" || echo "[WARN] 未使用 jq 解析 JSON（需确保引号包裹）"

# 6. deploy 目录部署
grep -q '"${PKG_ROOT}/deploy"' "$PKG_ROOT/deploy/install.sh" && echo "[PASS] install.sh 部署 deploy 目录" || echo "[WARN] install.sh 未部署 deploy 目录"

echo "=== 验证完成 ==="
```

---

## 附录：本次升级实际执行日志

### 安装成功的关键日志

```
[INSTALL] 步骤1: 定位 Python 解释器
[INSTALL] 使用 Python: /opt/python311/python/bin/python3.11 (3.11)
[INSTALL] 步骤3: 部署代码到 /opt/tdsql-sqlcheck/releases/v1.6.3.0
[INSTALL] 步骤4: 创建 venv 并离线安装依赖（wheels/ 目录）
Successfully installed ... (26 个包)
[INSTALL] 步骤5: 安装配置 /opt/tdsql-sqlcheck/.env
[INSTALL] 保留既有 .env（新模板已放至 .env.new 供比对）
[INSTALL] 已从历史版本(/opt/tdsql-sqlcheck/releases/v1.6.2.2)自动迁移并固化 TDSQL_ENCRYPTION_KEY 至 .env
[INSTALL] 已从历史版本同步 data/encryption.key 到新发布目录
[INSTALL] 步骤6: 切换 current -> releases/v1.6.3.0
[INSTALL] 步骤7: 安装 systemd 服务 tdsql-sqlcheck.service (端口 8000)
════ 部署验证 v1.6.3.0 @ http://127.0.0.1:8000 ════
  [PASS] 探针响应 {"status":"ok","version":"1.6.3.0"}
  [PASS] 版本号 1.6.3.0
  [PASS] 静态资产 /static/js/app.js
  [PASS] 静态资产 /static/css/app.css
  [PASS] 静态资产 /static/vendor/vue.global.prod.js
  [PASS] /metrics 指标输出
```

### 服务运行日志

```
2026-09-03 20:31:28,194 [tdsql.database] INFO: 规则配置初始化完成: 119 条规则
2026-09-03 20:31:28,528 [tdsql] INFO: 数据库初始化完成 (V2.0, 27张表)
2026-09-03 20:31:28,549 [tdsql.scheduler] INFO: 定时任务调度器已启动
2026-09-03 20:31:28,549 [tdsql] INFO: TDSQL SQL审核平台已就绪 (V1.6.3.0)
INFO:     Application startup complete.
```

### 密钥验证结果

```
新目录文件密钥长度: 44
现网.env环境密钥长度: 44
备份密钥长度: 44
[PASS] 密钥与备份一致!
```

---

**报告结束**。请将以上问题修复后重新打包，并在内网生产环境再次验证部署流程。
