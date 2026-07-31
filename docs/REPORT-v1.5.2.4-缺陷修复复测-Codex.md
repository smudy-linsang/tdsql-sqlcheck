# v1.5.2.4 缺陷修复复测报告（Codex）

## 1. 结论摘要

**修复项复测结论：通过。** A 提出的 P0-01、P1-02/P1-02b、P2-03、P2-04、P1-05 均已逐项验证，定向回归和全量回归均为 0 失败。

**发布结论：条件通过。** 代码修复可以进入合并评审；但若以仓库根目录的 `docker-compose.yml` 作为交付启动方式，仍存在一个本次复测新发现的 P1 部署阻断项（见第 8 节），修正并复测前不建议将该 Compose 方案用于上线。

本报告不包含任何真实 TDSQL、管理员或本地测试库的凭据。全部主动测试均在本机 Docker MySQL 的独立 `tdsql_sqlcheck_retest_*` 数据库中完成，测试结束后已删除。

## 2. 测试基线、范围与研读材料

| 项目 | 内容 |
| --- | --- |
| 被测版本 | `main @ 39bbd5e`（`docs: add v1.5.2.4 real TDSQL test report (G)`） |
| 历史升级基线 | `a700de5`（v1.2.0.9） |
| 修复方案 | `docs/v1.5.2.4_缺陷修复方案.md`，含 As-Built 记录 |
| 缺陷来源 | `docs/v1.5.2.4_独立测试报告_A.md` |
| 关联证据 | G 的真实 TDSQL 专项方案与结果、团队施工规约、项目 `CONTEXT.md` |
| 测试执行环境 | Windows 本地协作环境；Python 3.14.6；Docker Desktop/MySQL 8；另按 Dockerfile 构建 Python 3.11 镜像 |

本轮完整研读了上述 A、Q、G 文档及团队规约，并以 Q 文档的修复清单为追踪基线。A/Q 文档中记录的相关修复提交包括：`ce493de`、`9b220d5`、`e632f31`、`8538a77`、`99c5ecc`、`5fba7ad`。

## 3. 执行概览

| 类别 | 结果 | 证据 |
| --- | --- | --- |
| 测试收集 | 通过 | `1185 tests collected in 11.10s` |
| 全量回归 | 通过 | `1153 passed, 32 skipped, 0 failed`，154.56 秒 |
| 修复定向回归 | 通过 | 124 通过，20.37 秒 |
| 新库首次初始化与认证闭环 | 通过 | 6 个缺列均存在；登录、改密、旧令牌失效、新口令重登均成功 |
| 旧库升级路径 | 通过（有观察项） | v1.2.0.9 升级后与新装库的字段集合、类型、约束、索引集合一致；仅 3 张表的物理列顺序不同 |
| P2 解析器逆向边界 | 通过 | R043 正/反向 3 例、`SQL Object`/`BEGIN...END` 分割边界均通过 |
| 凭据 fail-fast | 通过 | 未设置压力测试口令时，在建立任何云端连接之前失败退出 |
| Python 3.11 导入 | 通过 | 按 Dockerfile 构建镜像并显式注入隔离元库后，完整导入 `backend.main` 成功 |
| 实际 ASGI 启动 | 通过 | 独立 Uvicorn 进程启动后 `/health` 返回 HTTP 200 |
| Compose 部署配置 | 不通过 | 未配置 `SQLCHECK_DB_*` 元数据库变量，详见第 8 节 |

## 4. 按缺陷逐项复测

### P0-01：`StatusUpdateRequest` 缺失与状态更新请求退化

验证内容：

- 路由完整性测试确认请求模型存在，且 JSON Body 定义未退化为查询参数。
- 新库场景登录后创建慢查询记录，`PUT` 状态更新传入合法 JSON 状态返回 HTTP 200。
- 非法状态返回 HTTP 400。
- Python 3.11 容器中导入 `backend.api.slow_query.StatusUpdateRequest` 与 `backend.main` 成功。

结果：**通过**。该项同时覆盖了 A 报告中 Python 3.11 导入失败和较高 Python 版本下接口语义退化两种风险。

### P1-02 / P1-02b：首次初始化缺列、改密 500、`scan_snapshots.rule_set_id`

在新建的空元数据库中仅执行一次 `ensure_db()` 后，使用 `information_schema` 校验下列 6 列全部存在：

| 表 | 字段 |
| --- | --- |
| `users` | `token_version` |
| `audit_history` | `db_name`、`rule_set_id` |
| `gate_audit_logs` | `connection_id`、`rule_set_id` |
| `scan_snapshots` | `rule_set_id` |

随后执行管理员登录、修改口令、旧令牌访问 `/api/v1/auth/me`、使用新口令重登：结果依次为 **200 / 200 / 401 / 200**。

结果：**通过**。首次建库的迁移执行顺序与运行时 DDL 双保险均生效。

### P2-03：R043 联表 UPDATE 误判与漏判

除原定向测试外，补充执行了下列对抗样例：

- 目标表段为逗号联表、`SET` 值中含逗号：命中 R043。
- 单表 UPDATE 的字符串值同时包含 `SET`、`JOIN`、逗号：不命中 R043。
- 单表 UPDATE 的 `SET` 子查询包含 JOIN：不命中 R043。

结果：**通过**。规则仅根据 UPDATE 目标表段判断，不再由 `SET` 赋值区内容导致误判。

### P2-04：截断 SQL 吞噬后续对象、R036/R037 误建议

验证内容：

- 回归 `tests/test_v2_syntax_truncation.py`：截断语句产生 `E999_SYNTAX_ERROR`，对象标记可将后续语句分开审计。
- 补充 `CREATE PROCEDURE ... BEGIN ... END` 内部出现 `-- SQL Object:` 标记的场景：内部标记不切分过程体；过程结束后的对象标记可正常切分。
- 定向套件同时覆盖 R036/R037 在解析器未提供列定义时不应产生误建议的保护逻辑。

结果：**通过**。

### P1-05：真实云端凭据迁出与测试脚本保护

验证内容：

- `tests/test_no_hardcoded_secrets.py` 随全量回归通过。
- 清空压力测试所需的两个密码环境变量后运行单实例最小命令；脚本在 `connect()` 的口令前置校验处退出，未建立数据库连接。

结果：**通过**。本轮未使用或输出任何真实云端凭据；真实 TDSQL 连通性证据仅引用 G 的已提交专项结果，不计入本轮主动测试结果。

## 5. 回归与功能测试明细

### 5.1 全量与定向自动化回归

执行：

```text
python -m pytest tests --collect-only -q
python -m pytest tests -q
```

结果：收集 1185 项；执行 1153 通过、32 跳过、0 失败、10 条警告。

定向执行：

```text
python -m pytest tests/test_app_routes_integrity.py \
  tests/test_sit_v1_rules.py tests/test_v2_syntax_truncation.py \
  tests/test_no_hardcoded_secrets.py tests/test_sit_round2.py \
  tests/test_uat_round2.py -q
```

结果：124 通过、0 失败。

### 5.2 历史版本升级等价性

流程：以 `a700de5` 初始化空库，再用当前 `39bbd5e` 运行升级；另以当前版本初始化一套全新库。比较两个库的 `information_schema` 表、列、索引。

- 表：47 对 47。
- 列：568 对 568；字段名、类型、可空性、默认值、附加属性、键属性集合一致。
- 索引：165 对 165；索引名、列顺序、唯一性、类型、排序规则集合一致。
- 功能验证：升级库上完成登录、改密、旧令牌失效及新口令重登。

观察项：`users`、`audit_history`、`gate_audit_logs` 三张表的**物理列顺序**在“旧库升级”和“新库首次建库”之间不同。这不影响本系统基于列名的 SQL 和已验证的认证功能，但会影响依赖 `SELECT *` 列位置的外部非规范客户端。建议后续迁移规范明确是否需要统一列顺序；本轮不将其判为修复失败。

### 5.3 容器与真实启动验证

- `docker build -f Dockerfile` 成功构建 Python 3.11 镜像。
- 向容器显式注入独立 MySQL 元库连接后，`from backend.api.slow_query import StatusUpdateRequest; from backend.main import app` 成功。
- 使用独立端口启动 Uvicorn；`GET /health` 返回 HTTP 200。

## 6. 非阻断警告与说明

本轮未发现失败用例。以下为既有警告，未影响结论：

- 3 个 Pydantic 模型字段名 `schema` 遮蔽父类属性的用户警告。
- Starlette TestClient/httpx 的弃用提示。
- 少量测试夹具在未来 pytest 主版本中的弃用提示。
- 初始化期间 MySQL 对被引用表的 `1824` 建表警告；最终 schema 与功能校验均通过，仍建议后续清理迁移过程的该噪声。

## 7. 与 G 的真实 TDSQL 专项结果关系

`docs/REPORT-v1.5.2.4-真实TDSQL专项实测结果-G.md` 记录了真实 TDSQL 的专项实测。该文档已在本轮完整研读，用于理解真实实例、数据源和范围，但本报告不把它重复计为 Codex 的独立实测：本轮遵循共享本地环境约束，仅在隔离本地库中执行主动验证。这样可避免在未经本轮授权的情况下对云上真实集群施加负载或写入测试数据。

## 8. 新发现：Compose 元数据库注入缺陷（P1-NEW-01）

### 现象与证据

`docker compose config --format json` 的 `backend` 服务环境变量中没有任何 `SQLCHECK_DB_*` 项。代码中的元数据库默认值为：主机 `127.0.0.1`、端口 `13306`。

在 Docker 容器中，该地址指向 backend 容器自身，而非 Compose 的 `mysql` 服务。因此按 Dockerfile 构建的镜像在未显式注入元库变量时，导入应用即因元数据库连接被拒绝而失败；显式注入指向测试 MySQL 的独立元库后导入成功。这一对照验证了根因。

`/health` 路径本身已实测返回 HTTP 200，Compose 健康检查路径正确；问题只在元数据库连接配置。

### 影响

若用户按仓库当前 `docker-compose.yml` 启动 backend，服务无法连接其元数据库，Compose 交付链路不可用。这会阻断以该文件为准的开发/测试部署。

### 建议修复与复测准入

在 backend 服务中显式注入 `SQLCHECK_DB_HOST=mysql`、`SQLCHECK_DB_PORT=3306`、`SQLCHECK_DB_USER`、`SQLCHECK_DB_PASSWORD`、`SQLCHECK_DB_NAME`，其中口令应从环境文件或密钥管理注入，不应在仓库中新增明文。修复后应执行：

1. `docker compose up -d --build`；
2. 等待 backend 健康检查为 healthy；
3. 验证 `/health` 返回 200；
4. 完成首次元库初始化、管理员登录与一次改密；
5. 复跑本报告第 5 节的定向回归。

## 9. 最终判定与建议

| 门禁 | 判定 |
| --- | --- |
| A/Q 所列缺陷修复 | **PASS** |
| 自动化回归 | **PASS** |
| 新库初始化与旧库升级 | **PASS（列顺序观察项）** |
| 凭据防护 | **PASS** |
| Dockerfile Python 3.11 运行时 | **PASS（需显式元库配置）** |
| 根目录 Compose 部署 | **BLOCKED：P1-NEW-01** |

建议将本缺陷修复分支进入代码评审，同时创建并修复 P1-NEW-01。Compose 修复复测通过后，v1.5.2.4 才可按 Compose 交付路径放行。
