# v1.6.4.0 AI Copilot 专家助手 · 第一轮用户验收测试报告

| 项 | 内容 |
|---|---|
| 版本 | v1.6.4.0 / CP-1 |
| 被测提交 | `52e270f`（`main`，开工前 `git pull origin main` 已确认与远端一致） |
| 测试性质 | 第一轮 UAT（真实浏览器用户视角 + 真实运行期链路） |
| 测试方 / 署名 | **智能体D**（独立于设计方 O、评审/SIT 方 A、开发方 Q） |
| 测试日期 | 2026-09-15 |
| 上游材料 | `GATE-v1.6.4.0-AI-Copilot-SIT放行结论与UAT准入清单-ClaudeA.md`、`DETAIL-…-O.md` Rev.D、`DEV-…-Q.md` |
| 证据目录 | `docs/evidence/v1.6.4.0-uat-d/`（脚本 50 份、JSON 证据 25 份、真实截图 21 张、已验证补丁 1 份） |
| 提交对象 | Mr.Linsang |

---

## 0. 结论

**本轮 UAT 判定：不通过（NO-GO），存在 5 项阻断级缺陷。**

核心结论一句话：**v1.6.4.0 的 Copilot 后端接口面与安全闸大体成形（SIT 结论可复现），
但"一个真实的人打开浏览器"这条路径整体不通——界面根本到不了，模型路径一旦配置就 100% 崩溃。**

| 维度 | 结果 |
|---|---|
| 独立复跑 `tests/copilot/` | **114 passed**，与 Q 自述、A 实测逐项吻合 ✅ |
| 浏览器可达性（第一轮 UAT 的首要问题） | **不可达**（会见 §3 D40-B01/B02/B05） |
| 模型路径端到端 | **不可用**（`NameError`，D40-B03） |
| 管理员能否启用模型 | **不可能**（自检接口缺失形成死锁，D40-B04） |
| 出站投影是否与预览一致 | **不一致**（D40-M01，实测报文为证） |
| 原审核主流程是否受影响 | **不受影响**（即时审核 200/passed，元数据审核 2.6 s 完成）✅ |
| GATE §5 四类必测项 | 4/4 已真跑，2 项通过、2 项暴露缺陷 |
| GATE §5.1 两条 | 2/2 通过 ✅ |

> 本报告的每一处判定都能在证据目录里找到对应文件；凡是我没能真跑到的，
> 都写成"未执行/证据边界"，没有用 SIT 结论顶替（A 在 GATE §5 里明确要求过这一点）。

---

## 1. 测试范围

### 1.1 按 GATE §5 指定的四类必测项（四轮 SIT 一次都没真跑过）

| # | 必测项 | 本轮是否真跑 | 结论 |
|---|---|---|---|
| 1 | 真实模型接入后 `projection_mode` 与实际出站载荷的一致性抽检（三种配置各抓一次报文） | ✅ 真跑，抓到了逐字节报文 | **不通过**，见 D40-M01 |
| 2 | 元数据库连接池与磁盘在 Copilot 并发下的占用；满并发时元数据审核是否被挤排队 | ✅ 真跑，双账号占满 runner 并发 2 | **通过**（P95 劣化 −0.8%/−3.9%，任务 2.6 s 完成） |
| 3 | 知识包实际检索质量（小黄金集 + 基线数） | ✅ 真跑，18 题黄金集 + 1 题越界探针 | **基本通过**（18/18 命中），但暴露 D40-N01 |
| 4 | `deploy/copilot_emergency_disable.sh` 实机演练 | ✅ 真跑（Git Bash + venv 垫片），含停用与恢复 | **部分通过**，暴露 D40-M05 |

### 1.2 按 GATE §5.1 顺手验的两条

| # | 项 | 结果 |
|---|---|---|
| 1 | N-05 是加载期闸不是运行期看门狗（换包必须重启） | ✅ 完全符合设计；手册须写明"必须重启" |
| 2 | B-01 跨平台换行：落地字节与 manifest 自洽 | ✅ `chunks.jsonl`/`index.json` sha256 与 manifest 逐字节一致，0 个 CRLF |

### 1.3 浏览器用户视角（本轮的主要投入）

以真实 Chrome（Playwright 驱动，headless）执行人类用户动作：登录 → 页头图标 →
抽屉 → 侧栏会话页 → 建会话 → 提问 → 预览 → 确认提交 → 轮询 → 查看结果 → 导出报告 →
反馈 → 归档；并逐菜单扫描业务解读入口、管理区"AI配置"页、操作审计页。

---

## 2. 测试环境与证据边界

### 2.1 专用夹具（与既有 v1.6.3.x 夹具、生产互不干扰）

| 资源 | 取值 |
|---|---|
| 元数据库 | `uat_d_1640_meta`（127.0.0.1:13306，docker `tdsql-mysql-test`） |
| 目标库 | `uat_d_1640_dist` / `uat_d_1640_cent` |
| 账号 | `uat_d_1640`(admin)、`uat_d_1640b`(admin，用于 N-09 双管理员)、`uat_d_1640dev`(developer)、`uat_d_1640aud`(auditor) |
| Web | 127.0.0.1:8025（`COPILOT_ENABLED=true`、`COPILOT_ALLOW_SCHEMA_IDENTIFIERS=true`） |
| Copilot runner | 独立进程，命名锁 `tdsql_copilot_runner`，accepting=true |
| 元数据执行器 | 独立进程 `backend.workers.metadata_runner` |
| 运行期目录 | `data/reports/uat_d_1640/`（`.gitignore` 已排除，含口令与密钥） |
| 一键复现 | `docs/evidence/v1.6.4.0-uat-d/start_all.ps1`、`stop_all.ps1` |

### 2.2 证据边界（必须先讲清楚，否则后面的结论会被误读）

1. **本机没有获准的内网模型网关。** 为完成 GATE §5 第 1 条，我起了一个**同协议的受控
   HTTPS 网关**（`mock_llm_gateway.py`），runner 走的是**真实**的端点策略解析 → 真实 DNS/TLS
   握手 → 真实 `httpx` 出站（`trust_env=False`、`verify=True`、Bearer 头），
   **唯一被替换的只有"供应商那一段"**；网关把**实际出站请求体逐字节**落盘，
   这正是抽检需要的证据。真实内网网关的接入仍需 G/CP-GATE-DATA 另行取证。
2. **为让 TLS 校验真实通过**，我把自签测试 CA 临时注入 `certifi` bundle；
   UAT 结束已用 `prepare_uat_d40.py untrust` **完整还原**（已复核无 `UAT-D40-TEST-CA` 残留）。
3. **本机无 systemd、无 Linux venv。** 应急停用脚本用 Git Bash 真实执行，
   并在沙箱 `INSTALL_DIR` 下放置 venv 垫片，使脚本的 **DB 侧停用分支真实跑通**（不是跳过）；
   `systemctl`/`pkill` 分支按平台实际结果如实记录，见 D40-M05。
4. **三处阻断缺陷让我"改一行才能继续测"。** 为把余下的验收做完，我**临时**施加了三个
   最小改动（`verified-fix.patch`），在**打过补丁的副本**上跑通了核心旅程；
   跑完**已 `git checkout` 完整回滚**，并在干净树上复跑 `tests/copilot/` 得 **114 passed**。
   产品代码零改动 —— 补丁作为"照图施工"交付物随报告给出，由 Q 施工。
5. 未改动任何既有夹具、未触碰生产、未发送任何真实模型请求、未出公网。

---

## 3. 缺陷清单

| 编号 | 级别 | 标题 | 影响面 |
|---|---|---|---|
| **D40-B01** | 阻断 | `index.html` 的 `<el-dialog>` 未闭合，Copilot 页面/抽屉/结果框被解析进对话框内部 | CP-F01/F02/F03/F04/F05/F12/F13/F14 全部前端不可达 |
| **D40-B02** | 阻断 | `app.js` 把"普通对象包 ref"直接交给模板，ref 未解包 | 修好 B01 后抽屉与对话框**常开并遮住登录按钮**，全站不可用 |
| **D40-B03** | 阻断 | `workflow.py` 使用未导入的 `ProviderRepo` | 配置了场景路由后，**模型路径 100% FAILED** |
| **D40-B04** | 阻断 | 自检接口 `providers/{id}/self-tests` 未实现，而启用强制要求自检通过 | CP-F11；**管理员永远无法启用模型**（死锁） |
| **D40-B05** | 阻断 | 管理区"AI配置"菜单无页面主体 | CP-F11 完全不可达 |
| **D40-M01** | 严重 | 实际出站载荷 = 原始 payload，不含 evidence/knowledge；预览与实发不一致；投影模式形同虚设 | CP-S02/§4.4 三闸被旁路 |
| **D40-M02** | 严重 | ContextBridge 是空壳，全前端 0 个业务解读入口 | CP-F03/F04/F05/F06/F07/F08/F09 不可达 |
| **D40-M03** | 严重 | 会话页不加载 capabilities/connections/sessions | 误显示"未启用"、实例下拉为空 |
| **D40-M04** | 严重 | `GET /copilot-admin/feedback-summary` 未实现 | CP-F14 / N-07 聚合口径缺失 |
| **D40-M05** | 严重 | 应急停用脚本在非 systemd 平台静默失败却报成功 | §16.6 停用演练的"真实阻断报告"要求 |
| D40-N01 | 一般 | 知识检索无相关性阈值，越界主题不承认未知 | §15.4 未知承认率 |
| D40-N02 | 一般 | `copilot-disabled.json` 只写不读 | §16.6 "禁止自动重启后恢复"不成立 |
| D40-N03 | 一般 | `editorBridge` 直接覆盖草稿，无差异/无版本比对 | §13.1 草稿保护 |
| D40-N04 | 一般 | 抽屉内无会话创建/选择入口 | §3.1 全局入口可用性 |
| D40-N05 | 一般 | 导出报告沿用全站 CSP（允许 `script-src 'unsafe-eval'`） | §13.3 报告专用 CSP |
| D40-N06 | 一般 | 前端未发送 `draft.revision` | §5.4/§13.1 草稿版本比对无数据 |

---

## 4. 阻断级缺陷（逐项：现象 / 复现 / 根因 / 证据 / 解决方案）

### D40-B01 `<el-dialog>` 未闭合，整个 Copilot 前端被解析进对话框内部

**现象（真实用户视角）**
点页头 Copilot 图标，**界面毫无反应**；点侧栏"Copilot专家助手"，内容区**空白**。

**复现**
1. `start_all.ps1` 起 Web → 浏览器登录 `uat_d_1640`；
2. 点页头 `data-testid="copilot-open"`；
3. 观察：网络面板**确实发出了** 3 个 Copilot 请求（capabilities/sessions/connections），
   但 DOM 里 `[class*=copilot]` 节点数为 **0**。

**根因（已用浏览器 HTML 解析规则取证）**

`frontend/index.html` 第 **2846** 行打开的
`<el-dialog v-model="snapshotDetailDialog.visible" title="单次扫描历史明细">`
**没有对应的 `</el-dialog>`**：它自己的内容在 2894 行就结束了，紧接着 2895 行就是 Copilot 页面块。

浏览器解析 in-DOM 模板时按标签栈嵌套，于是后面三块全部变成了这个对话框的**子节点**：

```
el-dialog(snapshotDetailDialog)        ← line 2846，直到 3025 行才被一个多余的 </el-dialog> 关掉
  ├─ div(v-loading) … 快照明细自身内容  ← 正常
  ├─ div[data-testid=copilot-page]      ← line 2896  ❌ 被吞
  ├─ el-drawer.copilot-drawer           ← line 2957  ❌ 被吞
  └─ el-dialog「Copilot 回答」           ← line 2988  ❌ 被吞
</el-dialog>
```

`snapshotDetailDialog.visible` 平时是 false → 该对话框不渲染 → **Copilot 的页面、抽屉、
结果框永远不渲染**，无论用户点什么、无论 `drawerVisible` 是什么。

**证据**
- `dbg_rootcause4.json`：浏览器真实祖先链
  `copilot-page → EL-DIALOG > DIV.page-content[copilot-page]`、
  `copilot-drawer → EL-DIALOG > EL-DRAWER.copilot-drawer`；
  而 `copilot-open` 在 `DIV.app-layout > HEADER.topbar`（所以图标能渲染、点击有反应）。
- `dbg_unclosed.json`：栈式解析给出的未闭合祖先 `el-dialog@2846`。
- `dbg_drawer4.json`：点击后 3 个 API 已发出，`drawer_after_click = 0`。
- 截图：`shots/dbg-copilot-after-click.png`、`shots/b3-ai-config.png`。
- 源码：`frontend/index.html` 第 2846 行 vs 第 2895 行（见 2.2 节边界说明）。

**解决方案（照图施工）**

在 Copilot 页面块**之前**补上第 2846 行对话框的闭合标签：

```diff
--- a/frontend/index.html
+++ b/frontend/index.html
@@ -2892,6 +2892,8 @@
         </el-table>
       </div>
     </div>
+  </el-dialog>
+
     <!-- ═══ v1.6.4.0 / CP-1：Copilot 专家助手页 ═══ -->
     <div v-if="currentPage==='copilot-page'" class="page-content" data-testid="copilot-page">
```

同时**必须删掉文件末尾那个多余的 `</el-dialog>`**（现为第 3026 行，紧跟在
「Copilot 回答」对话框的 `</el-dialog>` 之后），否则补完之后会变成"多一个闭合"。
即文件尾部应为：

```html
      </div>
    </el-dialog>      <!-- 关闭「Copilot 回答」（2988 行打开） -->
  </el-dialog>        <!-- 关闭「单次扫描历史明细」（2846 行打开） -->
</div>
```

> 改完请务必在浏览器里**重新加载**核对：`#app` 的直接子元素里应能看到
> `DIV.app-layout` 与各对话框**平级**，且 `document.querySelector('.copilot-drawer')`
> 的祖先链**不再**包含 `EL-DIALOG`。

**验收方式**：见 §6 "已用临时补丁验证"。

---

### D40-B02 模板里的 ref 未解包 —— 修好 B01 之后，全站反而不可用

**现象（修好 B01 后立刻出现）**
打开登录页（**尚未登录**），屏幕被一层遮罩挡住，遮罩上是一个「Copilot 回答」对话框；
登录按钮**点不动**（被遮罩拦截）。实测截图：`shots/dbg-refbind-anonymous.png`。

**复现**
1. 施加 D40-B01 的修复；
2. 浏览器打开 `http://127.0.0.1:8025/`（不登录）；
3. 观测到 2 个可见遮罩、1 个可见对话框「Copilot 回答」+ 1 个可见抽屉。

**根因**

`frontend/static/js/app.js` 第 55 行：

```js
const copilot = (typeof createCopilotState==='function') ? createCopilotState({...}) : {...};
```

`createCopilotState()` 返回的是**普通对象里装着一堆 ref**（`{drawerVisible: ref(false),
resultVisible: ref(false), submittingState: ref('DRAFT'), …}`）。
Vue 3 的 `proxyRefs` **只在 setup 返回值的顶层**做 ref 解包；模板里写 `copilot.drawerVisible`
拿到的是 **Ref 对象本身**（永远真值），而不是 `.value`。后果：

- `el-drawer v-model="copilot.drawerVisible"` / `el-dialog v-model="copilot.resultVisible"`
  → modelValue 恒真 → **首屏即弹开**，且 Element Plus 内部 `watch(modelValue)` 因为
  "值从未变化"永不触发，**关也关不掉**；
- `:disabled="copilot.submittingState!=='PREVIEW_READY'"` → Ref 对象 ≠ 字符串 → **恒 true**，
  "确认提交"按钮**永久禁用**；
- 所有 `v-if="copilot.xxx"` 恒真；
- `{{ copilot.copilotMode }}` 会渲染出 Ref 对象而不是 `READY`。

**证据**
- `dbg_refbind.json`：未登录态 `copilot_drawer_visible=true`、`visible_dialogs=["Copilot 回答"]`、
  `visible_overlays=2`、`blocked_by={overlay_label:"Copilot 回答"}`；
  施加修复后同一探针变为 `visible_overlays=0 / blocked_by=null / logged_in=true`。
- 截图：`shots/dbg-refbind-anonymous.png`（遮罩挡登录）与
  `shots/dbg-refbind-after_login.png`（修复后干净）。

**解决方案（照图施工）**

用 `reactive()` 包一层——同一个 `app.js` 里的 `cmpState` 就是这么用的，属于本项目既有写法：

```diff
--- a/frontend/static/js/app.js
+++ b/frontend/static/js/app.js
@@ -52,7 +52,7 @@ const app=createApp({
     const currentPage=ref('dashboard');
     const sidebarCollapsed=ref(false);
     // v1.6.4.0 / CP-1：Copilot 组合模块（依赖注入，不读任意 window 对象）
-    const copilot=(typeof createCopilotState==='function')?createCopilotState({
+    const copilotState=(typeof createCopilotState==='function')?createCopilotState({
       Vue:{ref,computed,reactive,watch,nextTick,onMounted},
       apiFetch:apiFetch,
       getIdentity:()=>{try{return (authState&&authState.user)||{}}catch(e){return{}}},
@@ -61,6 +61,7 @@ const app=createApp({
       editorBridge:{previewReplacement:(c)=>{try{if(c&&c.sql){sqlInput.value=c.sql;currentPage.value='audit-sql'}}catch(e){}}},
       contextBridge:{pageKey:''},
     }):{drawerVisible:ref(false),copilotMode:ref('DISABLED')};
+    const copilot=reactive(copilotState);
```

> 备选方案（二选一，不要同时做）：把 `copilot.js` 的返回值在 setup 里**逐个展开到顶层**
> （`return {..., ...copilot, copilot}`），模板改成顶层名。`reactive` 改动最小、且与
> `cmpState` 口径一致，推荐前者。
>
> 注意：`reactive()` 会把嵌套 ref 解包，因此 `copilot.capabilities` 等在模板中会直接拿到
> 值；`copilot.js` 内部逻辑用的是闭包里的原始 ref，不受影响。

**验收方式**：见 §6。

---

### D40-B03 模型路径 `NameError: name 'ProviderRepo' is not defined`

**现象**
一旦给场景配了 provider（即真实要用模型的场景），**每一个** turn 都以 `FAILED` 收场，
且**一次模型请求都没有发出去**（出站计数 0）。

**复现**
1. 用管理 API 建好主/备 provider 并（绕过 D40-B04）置为 enabled；
2. `PUT /copilot-admin/routes/SQL_ADVISE` 配好路由；
3. 走 preview → 提交 turn；
4. `GET /turns/{id}` 轮询 → 终态 `FAILED`；`data/reports/uat_d_1640/gateway-requests.jsonl` 无新增。

**根因**

`backend/services/copilot/workflow.py` 第 36 行只导入了三个 repo：

```python
from backend.services.copilot.repository import (
    AttemptRepo, AuditRepo, TurnRepo, new_id,
)
```

但第 245 行 `_load_provider_frozen()` 里用了 `ProviderRepo.get(...)`。
异常被 `_run_inner` 外层 `except` 捕获 → 直接落 `FAILED`，**不会 500、不会告警**，
属于"静默功能失效"。没有配置路由时 `_call_model` 会提前 `return None`（走 LOCAL_ONLY），
所以 SIT 用 mock 走的本地路径永远碰不到这一行 —— 这正是 GATE §5 说"必须真跑一次"的原因。

**证据**
- `data/reports/uat_d_1640/cprunner.err.log`：
  `NameError: name 'ProviderRepo' is not defined` 完整栈，重复 3 次；
- `s4_egress.json`：三个配置的 `terminal` 全为 `FAILED`、`outbound_count` 全为 0；
- 施加修复后同一脚本 `terminal` 全为 `SUCCEEDED`，`outbound_count` ≥ 1。

**解决方案（照图施工）**

```diff
--- a/backend/services/copilot/workflow.py
+++ b/backend/services/copilot/workflow.py
@@ -33,7 +33,7 @@ from backend.services.copilot.knowledge import store as knowledge_store
 from backend.services.copilot.policy import Limits, load_policy
 from backend.services.copilot.redaction import detect_sensitive, truncate_utf8
 from backend.services.copilot.repository import (
-    AttemptRepo, AuditRepo, TurnRepo, new_id,
+    AttemptRepo, AuditRepo, ProviderRepo, TurnRepo, new_id,
 )
```

**回归锁（建议随修复一起加进 `tests/copilot/`，这是本条缺陷真正的护栏）**

单纯补一行 import，下次仍可能被重构改回去。请补一条**真跑模型路径**的用例，
断言"配置了路由的 turn 不会 FAILED，且确有出站尝试"：

```python
# tests/copilot/test_workflow_model_path.py（示意，Q 可按现有 fixture 风格落地）
def test_routed_turn_reaches_provider_call(copilot_db, client, monkeypatch, ...):
    """配置了场景路由时，runner 必须能走到 provider 调用（防 ProviderRepo 类 NameError）。"""
    # 1) 造 provider（enabled、tested_revision=revision）+ route，指向 endpoints_file 里的端点
    # 2) monkeypatch prov_mod.call_provider，返回一个引用真实 E/K 编号的合规 answer
    # 3) 走 preview → admit → TurnExecutor.run()
    # 4) 断言：turn 终态 in ("SUCCEEDED","DEGRADED") 且 call_provider 被调用 ≥1 次
```

**为什么必须真跑而不是只断 import**：本缺陷的本质是"没有测试覆盖 `_call_model` 这条分支"，
只加一条 `hasattr(workflow, 'ProviderRepo')` 之类的断言，挡不住同类问题在别处复发。

---

### D40-B04 自检接口缺失，与"启用必须自检通过"形成死锁

**现象**
管理员按设计流程走：建 provider → 自检 → 启用。**第二步没有接口**，第三步因此永远失败。
即"**任何人在任何环境下都无法通过产品界面/API 启用一个模型 provider**"。

**复现**
```
POST /api/v1/copilot-admin/providers/{id}/self-tests   → 404 Not Found
PUT  /api/v1/copilot-admin/providers/{id}/enabled      → 422 PROVIDER_CONFIG_INVALID
                                                         "当前配置版本尚未通过自检，请先执行自检"
```

**根因**

- `DETAIL §12.5` 明确登记了 `POST /copilot-admin/providers/{id}/self-tests`（§12.6 给了完整合同：
  内部 `turn_kind=PROVIDER_SELFTEST`、固定合成问题、PUBLIC_HELP、最多 1 次尝试、不借 fallback）；
- 实现侧 `backend/api/copilot_admin.py` 里**没有这个路由**；
- 仓储层 `ProviderRepo.mark_tested()`（`repository.py:230`）**存在，但全仓唯一调用点是单元测试**
  （`tests/copilot/test_m01_m03.py:74`），没有任何生产代码路径会写 `tested_revision`；
- 而 `copilot_admin.py:250-256` 的启用逻辑**强制要求** `tested_revision == revision`。

三者叠加 = 死锁。这解释了为什么 SIT 四轮都只验到"判定逻辑"而没验过真发出去什么：
**根本没有合法路径把 provider 打开**。

**证据**
- `s1_routes.json`：对 `openapi.json` 逐条比对，`admin` 组唯一 `MISS` 项就是 self-tests
  （另一项 `MISS` 是 `feedback-summary`，见 D40-M04）；`registered_all` 里也没有该路径。
- `s2_admin_part1.json`：404 与 422 的原始响应体（含 `request_id`）。
- 全仓检索：`mark_tested` 命中 3 处，除定义外只有测试调用。

**解决方案（照图施工）**

**第一步：补 `ProviderRepo.mark_tested` 的唯一生产调用方。** 按 §12.6，自检必须走
**同一执行控制面**（不得在 Web 请求里同步发 HTTP、不得绕开并发/额度/出站/审计）：

在 `backend/api/copilot_admin.py` 增加（放在 `set_provider_enabled` 之前）：

```python
@router.post("/providers/{provider_id}/self-tests", status_code=202)
@guard_structural
def start_provider_self_test(request: Request, provider_id: str,
                             body: SelfTestRequest):
    """§12.5/§12.6：自检实现为 turn_kind=PROVIDER_SELFTEST 的内部 turn。

    不发业务资料；主备尝试上限 1，不借 fallback；同键幂等 202/200。
    """
    try:
        identity = _admin_identity(request)
    except CopilotError as e:
        return _err(e.code, e.message)
    conn = _get_connection()
    try:
        ensure_db()
        _require_ready(conn)
        provider = ProviderRepo.get(conn, provider_id, for_update=True)
        if provider is None:
            raise CopilotError("NOT_FOUND")
        if int(provider["revision"]) != body.expected_provider_revision:
            raise CopilotError("CONFIG_CHANGED")
        # 管理员须同时具备 copilot 与 copilot-admin 菜单（§12.6）
        from backend.services.copilot.authz import has_copilot_menu
        if not has_copilot_menu(identity.role):
            raise CopilotError("FORBIDDEN", message="自检需要同时具备助手使用权限")
        from backend.services.copilot.selftest import admit_self_test
        turn = admit_self_test(conn, identity, provider,
                              body.client_request_id)   # 复用 admit 的幂等与容量检查
        conn.commit()
        return JSONResponse(status_code=202, content={
            "request_id": _rid(), "turn_id": turn["id"], "state": "ACCEPTED",
            "status_url": f"/api/v1/copilot/turns/{turn['id']}",
            "result_url": f"/api/v1/copilot/turns/{turn['id']}/result",
            "poll_after_ms": 2000, "reused": bool(turn.get("reused")),
            "notice": "自检可能产生少量模型调用费用；不发送任何业务资料。"})
    finally:
        conn.close()
```

**第二步：`selftest` 模块与 runner 侧落地（合同要点，逐条对照 §12.6）**

| 合同 | 施工要点 |
|---|---|
| 内部 scene/turn_kind | `SCENE_PROVIDER_SELFTEST` / `TurnKind.PROVIDER_SELFTEST`（模型里已有常量），**不加入用户 scene 枚举与 scene_routes**；`POST /turns` 禁止指定 `turn_kind` |
| 资料级别 | 固定 `PUBLIC_HELP`、无 `source_refs`、固定合成问题、`route_snapshot` 只含待测 provider id/revision |
| 主备 | 该轮 `MAX_ATTEMPTS_PER_TURN = 1`，**不借 fallback** |
| 幂等 | hash 按 `owner + provider_id + provider_revision + 固定模板版本`；先查原键再新建 preview/session |
| 结果回写 | 用 `provider_id + revision` 条件更新 `tested_revision`（`ProviderRepo.mark_tested`），**迟到的旧配置结果不能批准新配置** |
| 输出 | 无来源卡/动作卡；校验项：TLS 与端点身份、认证、指定模型、输出 token 字段、JSON 能力、响应限长、usage 形状，分别标"可证实/未证实" |
| 失败关闭 | 输出不合规 → 自检不通过（`tested_revision` 不写） |

**第三步：把死锁变成"有出口"的回归锁**

```python
def test_provider_can_be_enabled_through_public_api(copilot_db, client, ...):
    """端到端：建 provider → 自检 → 启用，全流程只走公开 API，不得依赖直写 DB。"""
    # 断言 404→(补后)202；断言未自检时 enabled=true 被 422 拒绝；
    # 断言自检成功后 enabled=true 成功，且 tested_revision == revision
```

> 这条锁的价值：它会在**任何人**再次打断自检链路时立刻报红，而不是等到 UAT 才发现
> "模型根本打不开"。**SIT 四轮 + 114 条用例都没能发现它，就是因为缺这一条端到端锁。**

---

### D40-B05 管理区"AI配置"菜单没有页面

**现象（真实用户视角）**
管理员点侧栏「系统管理 → AI配置」：菜单项**高亮**了，内容区**一片空白**，
没有任何报错、没有任何提示。用户会以为"功能坏了"或"我没权限"。

**复现**
浏览器登录 admin → 展开"系统管理" → 点"AI配置" →
读取 `.page-content` 文本长度 = **0**；`el-menu-item.is-active` 文本 = "AI配置"。

**根因**

- `frontend/index.html` 第 128 行确实有菜单项 `<el-menu-item index="copilot-admin">AI配置</el-menu-item>`；
- `app.js:536` 的 `onMenuSelect` 会老老实实把 `currentPage` 置为 `'copilot-admin'`；
- 但 index.html 里**没有 `v-if="currentPage==='copilot-admin'"` 的页面块**
  （对全部菜单项做过枚举比对：只有 `copilot-admin` 这一个非分组项缺页面）。
  于是内容区什么都不渲染。

**证据**
- `b3_admin_page.json`：`menu_item_exists=true`、`menu_clicked=true`、
  `current_page_attr="AI配置"`、`page_content_len=0`；
- 截图 `shots/b3-ai-config.png`（菜单高亮 + 空白内容区）；
- 与 `s1_routes.json` 相互印证：13 个管理端点在**接口层全部存在且可用**，
  但**界面上一个都点不到** —— CP-F11（P0）事实上未交付。

**解决方案（照图施工）**

在 `frontend/index.html` 的 `currentPage==='copilot-admin'` 位置补一个"AI配置"页
（建议紧跟「Copilot 专家助手页」之后，与其它 `page-content` 同级），
按 §12.5 把接口暴露出来。**最小可用版本**（后续可迭代视觉）：

```html
<!-- ═══ v1.6.4.0 / CP-1：AI配置（管理区，DETAIL §3.1-4 / §12.5）═══ -->
<div v-if="currentPage==='copilot-admin'" class="page-content" data-testid="copilot-admin-page">
  <el-card>
    <template #header><span>AI 配置</span>
      <el-tag size="small" style="margin-left:8px">{{ copilotAdmin.health?.module_schema_state || '—' }}</el-tag>
    </template>

    <el-tabs v-model="copilotAdmin.tab">
      <!-- ① 端点（只读：部署批准清单，UI 不得编辑 base_url） -->
      <el-tab-pane label="端点" name="endpoints">
        <el-table :data="copilotAdmin.endpoints" size="small" stripe>
          <el-table-column prop="endpoint_id" label="端点ID" width="140"></el-table-column>
          <el-table-column prop="canonical_host" label="主机" width="180"></el-table-column>
          <el-table-column prop="port" label="端口" width="80"></el-table-column>
          <el-table-column prop="data_zone" label="数据域" width="100"></el-table-column>
          <el-table-column prop="privacy_profile" label="隐私档" width="150"></el-table-column>
          <el-table-column label="结构标识符" width="110">
            <template #default="{row}">{{ row.allows_schema_identifiers ? '允许' : '禁止' }}</template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <!-- ② 模型（掩码列表 + 自检 + 启用；凭据只写不读） -->
      <el-tab-pane label="模型" name="providers">
        <el-table :data="copilotAdmin.providers" size="small" stripe>
          <el-table-column prop="name" label="名称" width="160"></el-table-column>
          <el-table-column prop="model_id" label="模型" width="180"></el-table-column>
          <el-table-column label="状态" width="110">
            <template #default="{row}">
              <el-tag :type="row.enabled ? 'success' : 'info'" size="small">
                {{ row.enabled ? '已启用' : '未启用' }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="自检" width="140">
            <template #default="{row}">
              <span v-if="row.tested_revision===row.revision" style="color:#67c23a">已通过 r{{ row.revision }}</span>
              <span v-else style="color:#e6a23c">未通过 / 配置已变更</span>
            </template>
          </el-table-column>
          <el-table-column label="操作" min-width="200">
            <template #default="{row}">
              <el-button size="small" @click="copilotAdmin.runSelfTest(row)">连通性自检</el-button>
              <el-button size="small" type="primary"
                         :disabled="row.tested_revision!==row.revision"
                         @click="copilotAdmin.enableProvider(row, true)">启用</el-button>
              <el-button size="small" @click="copilotAdmin.enableProvider(row, false)">停用</el-button>
            </template>
          </el-table-column>
        </el-table>
        <div class="copilot-limit">自检会产生少量模型调用费用；只发固定合成问题，不发任何业务资料。</div>
      </el-tab-pane>

      <!-- ③ 场景与授权 ④ 配额与健康 ⑤ 调用审计（同构，略） -->
    </el-tabs>
  </el-card>
</div>
```

配套的 `copilotAdmin` 状态建议**独立成一个组合模块** `frontend/static/js/copilot_admin.js`
（`createCopilotAdminState({Vue, apiFetch})`），在 `app.js` 里以
`const copilotAdmin = reactive(createCopilotAdminState({...}))` 注入
（注意：必须走 B02 的 `reactive` 口径，不要重复踩坑）。它只负责调
`/api/v1/copilot-admin/*` 与 `/api/v1/copilot-audit/events`。

**为什么不能只补页面**：`runSelfTest` 依赖 D40-B04 的接口，
所以 **B04 与 B05 必须一起施工**，否则页面上的"连通性自检"按钮点下去就是 404。

---

## 5. 严重级缺陷

### D40-M01 实际出站载荷 ≠ 预览投影，方案甲三闸被旁路

**这是 GATE §5 第 1 条点名要验的东西，也是本轮最值得警惕的一条。**

**实测（受控网关逐字节抓包）**

| 配置 | 预览显示 `projection_mode` | 实际出站报文 | 报文大小 |
|---|---|---|---|
| A 仅主腿（端点允许结构标识符） | `SCHEMA_IDENTIFIERS` | `{question, public_question_id, source_refs, draft, page_key}` | **1260 B** |
| B 主腿允许 + 备用腿**不允许** | `ALIASED` | **与 A 逐字节结构相同**，同样含真实表名 `d40_tab_00` | **1260 B** |
| C 主腿 503 → 真实故障转移到备用腿 | `ALIASED` | 主腿 1260 B（503）+ **备用腿 1261 B，同样含真实表名** | 1260/1261 B |

三个配置的报文**顶层键完全一致**，`evidence`、`knowledge`、`allowed_rule_ids`
**全部不存在**；`draft` 是**未脱敏的原文**。

**根因（两层）**

1. **发错了对象。** `preview_service.build_preview()` 精心构造了
   `model_projection`（含 evidence/knowledge/allowed_rule_ids、按三闸做过别名化），
   `api/copilot.py:493-496` 也把它加密存进了 `model_projection_envelope`，
   **预览接口还把它回显给用户看**；但 runner 侧 `workflow.py:_payload()` 解的是
   **`payload_envelope`**（即 `record["payload"]`，原始 question/draft/source_refs），
   `_single_attempt()` 直接把它 `json.dumps` 进 `messages[1].content`。
   **`model_projection_envelope` 在全仓没有任何运行时读取方** —— 它只被写、被展示、从来没有被发送。
2. 因此 §4.4 的三闸（部署/逐实例/端点）只在**核算 `projection_mode` 这个字符串**，
   对**真正出站的内容零约束**：只要用户把真实表名写进问题或草稿，它就原样出站 ——
   而 §4.4 第 203 行写得很清楚："**自由问题/历史/草稿同样按本轮投影策略处理，
   不能从旁路带入未获准名称**"。配置 B/C 更是直接违反"主备两端均须满足本轮标识符能力"。

**证据**：`s4_egress.json`（三配置的 `preview_projection` 与 `outbound[].payload_keys` /
`contains_real_table` / `raw_body_head` 原文）、`data/reports/uat_d_1640/gateway-requests.jsonl`
（逐字节报文）、`s4_scene_matrix.json`（业务场景 422 SOURCE_REQUIRED）。

**影响**

- **合规**：内网脱敏承诺（CP-S02）与实际行为不符；用户已经在预览里被明确告知
  "本次将发送的资料（别名脱敏）"，实际发的是原文——这同时是**知情同意的失真**。
- **功能**：模型**收不到任何证据与知识**，只拿到问题与 ID 列表，
  所以它不可能给出"基于证据、可追溯引用"的回答；CP-F03/F06/F07/F08/F09 的设计目标
  从原理上无法达成；而输出校验里 `allowed_rule_ids` 闭集也随之落空。

**解决方案（照图施工）**

**第 1 步（必须）——让真正出站的是投影，而不是原始 payload。**
改 `backend/services/copilot/workflow.py`：

```diff
     def _payload(self) -> dict:
-        from backend.services.copilot import crypto as crypto_mod
-        raw = crypto_mod.decrypt(
-            self.preview["payload_envelope"], "copilot_previews",
-            self.preview["id"], "payload_envelope",
-            owner=self.preview["owner_subject_id"], keyring=self.keyring)
-        return json.loads(raw)
+        """返回**本轮封存的模型投影**（§12.3：runner 不得再悄悄加入新来源）。
+
+        payload_envelope 仅用于会话内展示/审计追溯，不得作为出站体；
+        出站必须使用 model_projection_envelope —— 它是预览时按三闸脱敏、
+        冻结预算裁剪后封存的那一份，也是用户在预览里看到的那一份。
+        """
+        from backend.services.copilot import crypto as crypto_mod
+        raw = crypto_mod.decrypt(
+            self.preview["model_projection_envelope"], "copilot_previews",
+            self.preview["id"], "model_projection_envelope",
+            owner=self.preview["owner_subject_id"], keyring=self.keyring)
+        return json.loads(raw)
```

> `preview_service` 侧的 `model_projection` 已经包含
> `question / history / evidence[{evidence_id,source_kind,availability,completeness,data}]
> / knowledge / allowed_rule_ids / output_schema`，**正好是 §4.3/§5.3 要求交给模型的有界证据**，
> 不需要再造结构；`_run_inner` 里本地模板分支用到的 `payload.get("question")`
> 需要相应改为从 `model_projection` 取（该键仍在）。

**第 2 步（必须）——把"预览即实发"变成可执行的锁。**
这是本条缺陷真正的护栏（只有断言"两者相等"才挡得住回归）：

```python
# tests/copilot/test_egress_matches_preview.py（示意）
def test_outbound_body_equals_frozen_projection(copilot_db, client, monkeypatch, ...):
    """铁律：真正发出去的 user 消息 == 预览封存的 model_projection（逐字节）。

    任何"绕过投影直接发 payload"的实现都必须在这里报红。
    """
    captured = {}
    async def fake_call(url, auth_mode, secret, body, *a, **kw):
        captured["body"] = body
        return ProviderCallResult(ok=True, http_status=200, body_text=json.dumps({
            "choices": [{"message": {"content": json.dumps(COMPLIANT_ANSWER)},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1}}))
    monkeypatch.setattr(prov_mod, "call_provider", fake_call)

    preview = make_preview(...)                 # 含真实表名的问题 + 草稿 + 证据
    run_turn(...)                               # 走到模型调用
    sent = json.loads(captured["body"]["messages"][1]["content"])
    assert sent == json.loads(decrypt(preview.model_projection_envelope))
    # 反例：ALIASED 模式下不得出现任何真实标识符
    if preview["projection_mode"] == "ALIASED":
        assert REAL_TABLE not in captured["body"]["messages"][1]["content"]
```

**第 3 步（必须）——把"三闸"从核算变成三处真实断言**，
按 A 在 §5 里建议的三种配置各留一条用例：
`主备都允许 / 备用腿不允许 / 逐实例未授权`，每条都断言**出站报文中真实标识符的有无**。

**第 4 步（建议）——预览与实发的对账留痕。** `EGRESS_START` 审计里已记
`identifiers_included`，建议同时记 `projection_sha256`（对实际出站体取哈希），
与 `preview.snapshot_hash` 一并入审计，使"预览=实发"在事后可核。

---

### D40-M02 ContextBridge 是空壳，业务解读入口一个都没有

**现象**
DETAIL §3.1 第 3 条要求"业务结果区出现『解读本次结果』『解释这条规则』『分析失败原因』
『生成修改建议』，只在拥有对应源模块权限时出现"。**实测：全前端一个都没有。**

**根因**

- `app.js:62` 的 ContextBridge 是 `{pageKey:''}` —— 只有这一个空属性，
  **没有 `getCurrentSelection()`，也没有 `openCopilot(selection)`**（§13.1 合同）；
- 于是 `copilot.sourceRefs` **没有任何写入点**：`index.html` 里根本绑定了 `copilot.draftText`，
  却从未绑定/填充 `sourceRefs`；也没有任何业务结果区调用 `copilot.openDrawer`；
- 结果：用户只能在一个**没有任何上下文**的通用聊天框里打字，
  业务场景（规则解释/审核解读/任务排障/慢SQL/对比/表统计/网关）全都拿不到 `source_refs`，
  后端按 §12.3 一律 `422 SOURCE_REQUIRED`。

**证据**
- `s11_entry_scan.json`：`index.html`/`app.js`/`copilot.js` 中
  「解读本次结果」「解释这条规则」「分析失败原因」「生成修改建议」「openCopilot」
  「getCurrentSelection」**命中数全部为 0**；`sourceRefs` 仅在 `copilot.js` 内部出现 3 次（自己的状态）；
- `s4_scene_matrix.json`：`RULE_EXPLAIN` + 无 refs → `422 SOURCE_REQUIRED
  "本场景需要选择 ['rule'] 类型来源"`；
- `b2_business_entry.json` / `b6_menu_coverage.json`：浏览器逐菜单扫描，
  Copilot 业务入口按钮**合计 0 个**。

**解决方案（照图施工）**

**(a) 在 `app.js` 里把 ContextBridge 做成真桥**（§13.1 合同，逐字段）：

```js
// app.js setup 内，注入给 createCopilotState 之前
const copilotContextBridge = {
  pageKey: '',                              // 由业务页写入，例如 'schema-extractor-audit'
  getCurrentSelection() {                   // 返回 {page_key, source_refs, draft, draft_revision}
    return {
      page_key: currentPage.value,
      source_refs: copilotSelection.value.source_refs || [],   // 由业务页登记
      draft: copilotSelection.value.draft || null,
      draft_revision: editorBridge.readRevision(),
    };
  },
};
const copilotSelection = ref({});           // 业务页用下面两个 API 写入
// 暴露给模板/业务页使用
function openCopilotWith(selection) {       // §13.1：openCopilot(selection) 只创建待预览内容，不提交
  copilotSelection.value = selection || {};
  copilot.openDrawer();
}
```

**(b) 逐个业务结果区挂入口**（§13.1 明确列出首期需接线的 8 个模块）。
以"在线元数据审核"为例，在任务结果卡的按钮组里加：

```html
<el-button v-if="visibleMenus.has('copilot')" size="small" type="primary" plain
           :disabled="!selectedJobRow"
           @click="openCopilotWith({
             page_key:'schema-extractor-audit',
             source_refs:[{kind:'metadata_job', job_id:selectedJobRow.job_id,
                           offset:0, limit:10}],
             draft:null })">
  让 Copilot 解读
</el-button>
```

八个模块的 `source_refs` 形状（严格按 §12.3 的判别联合）：

| 模块 | 入口文案 | source_refs |
|---|---|---|
| 即时审核 | 解读本次审核结果 | `[{kind:'audit_history', history_id, statement_indexes:[…]}]` |
| 文件审核 | 解读本次审核结果 | 同上 |
| 在线元数据审核 | 让 Copilot 解读 / 分析失败原因 | `[{kind:'metadata_job', job_id, offset:0, limit:10}]`（失败排查可加 `include_recent_jobs:true`） |
| 审核规则库 | 解释这条规则 | `[{kind:'rule', rule_ids:[…≤10]}]` |
| 慢SQL记录 / EXPLAIN | 解读这条慢SQL | `[{kind:'slow_query', slow_id}]` |
| 四类扫描对比 | 解读本次对比 | `[{kind:'scan_snapshot', snapshot_ids:[a,b]}]` |
| 表类型统计 | 解读本次统计 | `[{kind:'table_type_stat', stat_id}]` |
| 网关日志分析 | 解读这份报告 | `[{kind:'gateway_report', report_id}]` |
| SQL 编辑器 | 生成修改建议 | `source_refs:[]` + `draft:{kind:'SQL', text, revision}` |

**(c) 回归锁**：建议加一条静态锁，防止"再加模块时忘接线"：

```python
def test_required_modules_have_copilot_entry():
    """§13.1 首期需接线的 8 个模块，结果区必须存在 Copilot 入口。"""
    html = Path("frontend/index.html").read_text(encoding="utf-8")
    for module in ("schema-extractor-audit", "audit-sql", "file-audit",
                   "slow-records", "schema-check", "bigtable",
                   "deep-diag-tabletype", "deep-diag-gateway"):
        assert f"openCopilotWith" in html, "缺少上下文入口桥"
    assert "getCurrentSelection" in Path("frontend/static/js/app.js").read_text(encoding="utf-8")
```

---

### D40-M03 会话页不加载能力/实例/会话，误显示"未启用"

**现象**
管理员从侧栏直接进「Copilot专家助手」，页面顶部标签写着**"未启用"**，
"选择已授权实例"下拉**是空的**——而此时后端明明 `mode=READY`、
该管理员对 `d40-dist` 有 **APPROVED** 授权。

**根因**

`copilot.js` 里 `loadCapabilities()` / `loadConnections()` / `loadSessions()`
**只在 `openDrawer()` 里被调用**；侧栏页面只是把 `currentPage` 切到 `copilot-page`，
**没有任何初始化钩子**。于是：

- `capabilities` 仍为 `null` → `copilotMode` 计算属性落到 `'DISABLED'` → 页面显示"未启用"；
- `connections` 为空数组 → 实例下拉无选项 → 用户**无法创建实例会话**；
- `sessions` 为空 → 会话下拉也是空的。

**证据**
- `b5_copilot_page.json`：`page_mode_tag=["未启用"]` 而 `capabilities_api="READY"`；
  `instance_options=[]`；
- `s5_export_and_conn.json`：同一账号 `GET /api/v1/copilot/connections` 返回
  `{"items":[{"connection_id":"d40-dist","name":"D40-分布式测试库",...}]}`、
  `GET /copilot-admin/grants` 显示 `approval_state=APPROVED, enabled=true`。

**解决方案（照图施工）**

在 `copilot.js` 里补一个显式的页面进入初始化（并保持抽屉/页面共用同一份状态）：

```js
// copilot.js —— 新增
async function initPage() {
  await Promise.all([loadCapabilities(), loadConnections(), loadSessions()]);
}
// 返回值里导出
return { ..., initPage, ... };
```

`app.js` 里在菜单选择处触发（注意与抽屉互不干扰）：

```js
const onMenuSelect=(key)=>{
  if(key==='slow-raw-log'&&!rawSlowlogEnabled){currentPage.value='slow-tasks';return}
  currentPage.value=key;
  if(key==='copilot-page'){ try{ copilot.initPage(); }catch(e){} }   // ← 新增
};
```

另外，页面模板里"未启用"的判据建议收紧：`capabilities===null` 时显示
**"正在加载…"**而不是"未启用"，避免把"还没问"渲染成"不能用"：

```html
<el-tag v-if="!copilot.capabilities" type="info" size="small">正在加载…</el-tag>
<el-tag v-else-if="copilot.copilotMode==='UNAVAILABLE'" type="danger" size="small">模块结构不可用</el-tag>
<el-tag v-else-if="copilot.copilotMode==='LOCAL_ONLY'" type="warning" size="small">本地帮助模式</el-tag>
<el-tag v-else-if="copilot.copilotMode==='DISABLED'" type="info" size="small">未启用</el-tag>
<el-tag v-else type="success" size="small">已就绪</el-tag>
```

---

### D40-M04 `GET /copilot-admin/feedback-summary` 未实现

**现象**：`GET /api/v1/copilot-admin/feedback-summary` → **404**。
§12.5 要求它按 `scene + rule_id + rule_snapshot_hash` 聚合 INCORRECT 反馈，
并落实 N-07 的只读聚合口径（≤5000 行/3 秒、≤1000 分组、≥3 个不同 subject 才展示、
不返回正文/用户名/实例名/会话 ID）。当前**该能力完全不存在**（CP-F14）。

**证据**：`s1_routes.json`（设计清单比对 `MISS`）、`s2_admin_part1.json`（404 响应）。

**解决方案（照图施工）**

在 `backend/api/copilot_admin.py` 的 `health` 之前补路由，SQL 严格按 §12.5 收口：

```python
@router.get("/feedback-summary")
@guard_structural
def feedback_summary(request: Request, days: int = 30):
    """N-07 只读聚合：按 scene+rule_id+rule_snapshot_hash 聚合当前 INCORRECT 反馈。"""
    try:
        identity = _admin_identity(request)          # role=admin + copilot-admin
    except CopilotError as e:
        return _err(e.code, e.message)
    if not has_copilot_menu(identity.role):          # 设计 §12.5：admin+copilot-admin
        return _err("FORBIDDEN")
    conn = _get_connection()
    try:
        ensure_db()
        _require_ready(conn)
        days = max(1, min(30, days))
        from backend.services.copilot.repository import FeedbackRepo
        rows, truncated = FeedbackRepo.aggregate_incorrect(conn, days=days,
                                                          max_rows=5000, max_groups=1000)
        if truncated:
            return _err("CONTEXT_TOO_LARGE", "反馈数据过多，请缩短时间窗")
        # ≥3 个不同 subject 贡献才对外展示；不足者合并隐藏
        items = [r for r in rows if r["subject_count"] >= 3]
        hidden = len(rows) - len(items)
        return {"request_id": _rid(), "days": days, "items": items,
                "hidden_below_privacy_threshold": hidden,
                "notice": "关联质疑次数，不等于该规则已证实误报。"}
    finally:
        conn.close()
```

配套仓储方法要点（逐条对应 N-07）：
① 走 `feedback_at` 索引窄查，**单次 3 秒**，超 5000 行报 `CONTEXT_TOO_LARGE`，不扫全库；
② `feedback_rule_ids_json` 只由服务端从冻结证据提取，**用户/模型不得指定**；
③ 同 turn 重复提交不重复计数，改成其他 code 即退出 INCORRECT 计数；
④ 只读未过期 30 天正文对应的元数据，**不从 180 天审计恢复正文**；
⑤ 返回体**不含**正文/用户名/实例名/会话 ID。

> 与 B05 一样，这个接口补完还要在"AI配置 → 调用审计/反馈"页签里露出，否则仍然是"有接口没入口"。

---

### D40-M05 应急停用脚本在非 systemd 平台静默失败却报成功

**现象（实机演练）**
执行 `bash deploy/copilot_emergency_disable.sh --incident-id UATD40-EMG-001`：

- 退出码 **0**；日志打印 **"非 systemd：已按进程名停止 copilot_runner"**、
  **"result=disabled"**；
- 实测 **runner 进程（PID 70920）仍在运行**；
- 若只看脚本输出，运维会认为"已摘掉"，而实际上只摘掉了一半。

**根因**

```bash
else
  pkill -f "backend.workers.copilot_runner" 2>/dev/null || true
  log "非 systemd：已按进程名停止 copilot_runner"
fi
```

`pkill` 在本机的执行环境里**根本不存在**（Git Bash 不带 procps），
`2>/dev/null` 把 `command not found` 吞掉，`|| true` 让脚本继续往下走，
最后无条件打印成功文案、写 `result=disabled`、以 0 退出。
这与 §16.6 明确要求的"**失败报告真实阻断及人工升级，不宣称到点必成功**"直接冲突。

同一演练中**正确的部分**（不掩盖）：
DB 侧止血**真实生效**（`settings.enabled: true→false`、`accepting: 1→0`、
`GET /capabilities` → `DISABLED`）；主产品**不受影响**（即时审核 `200 / passed=true`、
`/health` 200）；恢复流程有效（删标记 + 恢复设置后 `mode=READY`、accepting=true、
新会话 201）。

**证据**：`s7_emergency_disable.json`（脚本 stdout、退出码、前后 flags、
`persist_file_content`、`event_log`、主产品探测）、
`s7b_emergency_recovery.json`（`runner_pids_after_disable=["70920"]`、
`runner_still_running=true`、恢复后 READY）。

**解决方案（照图施工）**

**第 1 步：停止动作必须"先探测、再执行、失败即报错退出"。** 把第 3 节整段替换为：

```bash
# ── 3. 停止并禁止自动启动 runner，回收其子进程 ─────────────────────────────
RUNNER_STOPPED="false"
if [[ -d /run/systemd/system ]] && command -v systemctl >/dev/null 2>&1; then
  if systemctl stop "${UNIT}" 2>/dev/null; then
    systemctl disable "${UNIT}" 2>/dev/null || true
    RUNNER_STOPPED="true"
    log "systemd: ${UNIT} 已 stop+disable"
  else
    log "systemd: ${UNIT} stop 失败"
  fi
fi

if [[ "${RUNNER_STOPPED}" != "true" ]]; then
  if command -v pkill >/dev/null 2>&1; then
    pkill -f "backend.workers.copilot_runner" && RUNNER_STOPPED="true" \
      || log "pkill 未匹配到 copilot_runner 进程"
    pkill -f "backend.workers.copilot_text_worker" 2>/dev/null || true
  else
    # 平台无 pkill/systemctl：必须显式报错，不能静默放过
    log "错误：本平台既无 systemd 也无 pkill，无法自动停止 ${UNIT}。"
    log "      请人工终止该进程后重跑本脚本；在此期间 DB 侧已停用，"
    log "      助手不会受理新任务，但已发出的模型请求无法撤回。"
    RUNNER_STOPPED="manual-required"
  fi
fi
```

**第 2 步：退出码与事件日志必须反映真实结果**（把结尾几行改为）：

```bash
EVENT_LOG="${INSTALL_DIR}/logs/copilot-emergency.log"
mkdir -p "${INSTALL_DIR}/logs" 2>/dev/null || true
printf '%s incident=%s db_side=%s runner_stopped=%s result=%s\n' \
  "${TS}" "${INCIDENT_ID}" "${DB_OK}" "${RUNNER_STOPPED}" \
  "$([[ "${RUNNER_STOPPED}" == "true" ]] && echo disabled || echo partial)" \
  >> "${EVENT_LOG}" 2>/dev/null || true

if [[ "${RUNNER_STOPPED}" != "true" ]]; then
  log "停用未完成：runner 仍在运行（${RUNNER_STOPPED}）。"
  log "请人工处理，并复核网关侧是否仍有出站。"
  exit 3            # ← 明确非零：调用方/演练脚本必须能判出"没摘干净"
fi
log "Copilot 已停用（事件 ${INCIDENT_ID}）。恢复须：查明原因 → 修复/撤销配置 → 重跑受影响安全/黄金集门禁 → 人工批准 → 删除 ${PERSIST_FILE} 并重启 ${UNIT}。"
```

**第 3 步：把"演练判定"写成可执行的锁**，避免下次又在"看着成功"里放过：

```python
def test_emergency_disable_reports_failure_when_runner_not_stopped(tmp_path):
    """无 systemd/pkill 时脚本必须非零退出并写 partial，不得报 disabled。"""
    # 用 PATH 剔除 pkill/systemctl 跑脚本，断言 rc != 0 且事件日志含 runner_stopped=false
```

**第 4 步：手册同步**（§16.6 要求"由部署手册列出实测命令"）：
把"本平台需人工终止 runner"写进部署手册的应急章节，并附本机演练记录作为样例。

---

## 6. 一般级缺陷

| 编号 | 现象与根因 | 解决方案（照图施工） |
|---|---|---|
| **D40-N01** | 知识检索**没有相关性阈值**：越界问题"Oracle 的 ROWNUM 分页在 TDSQL 里怎么写？"仍返回 **8 段**无关的 TDSQL 知识；本地模板只在 `knowledge==[]` 时才说"未命中"，于是**永远不会承认未知**。证据 `s6_knowledge_quality.json`。 | 在 `knowledge.py` 的 `KnowledgeBundle.search()` 末尾加分数下限（如 `score >= MIN_SCORE`，取值用现有 BM25 分布标定），并在 `render_usage_help()` 里对"检索为空 **或** 全部低于阈值"统一输出"本地知识库未收录该主题，请查阅使用手册"，同时把 `limitations` 写明。配套用例：越界问题断言 `knowledge==[]` 且答案含"未收录"。 |
| **D40-N02** | `copilot-disabled.json` **只写不读**：全仓唯一引用在 `deploy/copilot_emergency_disable.sh:36`，"禁止自动重启后恢复"实际不起作用（真正拦住的只有 DB `settings.enabled=false` + `accepting=0`）。 | 二选一：①在 `copilot_bootstrap_check()` 与 runner 启动处**读取该文件**，存在即强制 `_LOCAL_READY=False` 且 `accepting=false`；②承认它只是**审计留痕**，把脚本注释与 §16.6 表述改为"DB 侧停用才是强制手段，本文件用于人工恢复前的显式确认"，并在恢复步骤里要求删除。推荐 ①（与文件命名和设计意图一致）。 |
| **D40-N03** | `app.js:61` 的 `editorBridge.previewReplacement` 直接 `sqlInput.value=c.sql; currentPage.value='audit-sql'` —— **无差异预览、无 revision 比对、不拒绝覆盖**，与 §13.1"先显示差异，用户确认后再比较 revision 并写入；异步期间用户编辑了原草稿则拒绝覆盖"不符。 | 补 `editorBridge.readRevision()` / `previewReplacement(candidate)`（弹差异对话框，确认后才写）/ `applyDraftIfRevision(expected, text)`（revision 不等则拒绝并提示"草稿已变更，请重新比较"）三个方法；`sendToEditor` 走"复制 + 打开编辑器 + 提示粘贴"，不直接改用户输入框。 |
| **D40-N04** | 全局抽屉里只有"场景/问题/预览资料/确认提交"，**没有会话创建或选择控件**；用户点"预览资料"只会看到"请先创建或选择会话"。证据 `b1_local_help.json`（drawer_text 无会话控件）、`b5_copilot_page.json`（`preview_error`）。 | 抽屉顶部补一行：会话下拉（`copilot.sessions`）+「新建通用会话」按钮，与页面复用同一状态；或首屏无会话时**自动建一个通用会话**并明示"已为你新建会话"。 |
| **D40-N05** | 导出报告响应头用的是**全站 CSP**（`script-src 'self' 'unsafe-eval' …`），不是 §13.3 要求的报告专用限制性 CSP。报告本体已无 `<script>`、无远程引用（实测 `export_has_script=false`、`export_has_remote=false`、页首有"内部建议资料"），实际风险低。证据 `s5_export_and_conn.json`。 | 在 `export.html` 端点单独设置响应头：`Content-Security-Policy: default-src 'none'; style-src 'unsafe-inline'; img-src data:; sandbox`（报告为纯内联样式静态页），并加用例断言该响应头**不含** `script-src`。 |
| **D40-N06** | 前端 `copilot.js` 构造 draft 时只发 `{kind,text}`，**不发 `revision`**，而 §5.4/§13.1 的草稿版本比对需要它。我的探针传 `revision:1`（整数）时后端还返回 422（模型要求字符串）。 | `buildPreview()` 里改为 `body.draft = {kind:'SQL', text:…, revision: String(editorBridge.readRevision() ?? 0)}`；同时在后端模型注释里明确 `revision` 为字符串型，或改成 `Union[str,int]` 并统一转字符串，避免调用方踩坑。 |

---

## 7. GATE §5 四类必测项 · 实测结果

### 7.1 第 1 条：出站投影一致性抽检 → **不通过（D40-M01）**

见 §5 D40-M01。这里只强调方法学价值：**这条只有真发一次才能发现**。
SIT 四轮验的是"判定逻辑"（`identifiers_allowed` 怎么算、`projection_mode` 怎么取），
而真正发出去的是**另一个对象** —— 逻辑全对，行为全错。

### 7.2 第 2 条：并发资源与 P95 劣化 → **通过** ✅

方法：两个管理员账号各持 1 个活动轮（`COPILOT_MAX_ACTIVE_PER_USER=1`），
受控网关置 `slow(30s)` 让 2 个模型调用同时在途 = **占满 runner 并发 2**；
同机同库对主产品只读接口各采 3 轮 × 15 次。

| 接口 | 基线 P95（3 轮中位） | 满并发 P95（3 轮中位） | 劣化 | 门禁 |
|---|---|---|---|---|
| `/api/v1/audit/metadata-jobs?limit=5` | 26.4 ms | 26.2 ms | **−0.8%** | ≤10% ✅ |
| `/health` | 20.3 ms | 19.5 ms | **−3.9%** | ≤10% ✅ |

**元数据审核是否被挤排队**：满并发期间提交真实在线元数据审核任务
（`scopes=[TABLE,INDEX]`）→ `SUCCEEDED`，`created_at→finished_at` = **2.6 s**；
对照 Copilot 空闲时同任务 = **4.1 s**。**没有排队、没有失败**。
证据：`s8b_concurrency.json`、`s8c_concurrency_job.json`、`s8d_idle_job.json`。

> 附注（供 A/O 参考，不作为结论）：单主体活动轮上限固定为 1，
> 因此"满并发"必须用**两个账号**才能构造；用一个账号提交第二个 turn 会得到 429，
> 这是设计行为，不是缺陷。

### 7.3 第 3 条：知识包实际检索质量 → **基本通过**，附基线数 ✅

黄金集 18 题覆盖 4 个 authority（USER_GUIDE 8 / VERIFIED_CASE 4 / VENDOR_SYNTAX 4 / PUBLIC 2），
每题给出"必需命中的关键事实"，判定轨道用**预览封存的检索结果**（HTTP 计数对账）
+ **同一检索函数取回正文**核对事实。

- **检索命中率 = 18/18 = 100%**（逐题明细见 `s6_knowledge_quality.json`）；
- HTTP 对账一致（如"即时审核功能怎么使用？"离线 8 条 / 在线 `knowledge_count=8`）；
- 知识包 `READY`、`bundle_id=kb-1.6.4.0-5b8426f429c6a89a`、23 段；
- **越界探针失败**：见 D40-N01。

**基线数（供后续知识包迭代比较）**：`retrieval_hit_rate = 1.00`（18 题）、
`out_of_scope_false_positive = 8 段`。

### 7.4 第 4 条：应急停用实机演练 → **部分通过（D40-M05）**

| 演练要求 | 结果 |
|---|---|
| 摘掉后主产品审核全流程可用 | ✅ 即时审核 `200 / passed=true`；`/health` 200 |
| 立即停止新受理 | ✅ `settings.enabled=false`、`accepting=0`、`capabilities.mode=DISABLED` |
| 停止并禁止自动启动 runner | ❌ **静默失败**（脚本报成功，进程仍在）→ D40-M05 |
| 持久停用标记 | ⚠️ 写入成功，但无人读取 → D40-N02 |
| 恢复后 Copilot 回到 READY | ✅ 删标记 + 恢复设置后 `mode=READY`、`accepting=true`、新会话 201 |
| 失败时真实报告阻断 | ❌ 退出码 0 + "result=disabled" → D40-M05 |

---

## 8. GATE §5.1 两条 · 实测结果

### 8.1 N-05 是加载期闸，换包必须重启 → **完全符合设计** ✅

按真实运维动作走了一遍（在 `backend/copilot_knowledge/` 下多放一个包目录）：

| 步骤 | `knowledge_status` | `reason_code` | 说明 |
|---|---|---|---|
| 初始 | `READY` | — | `kb-1.6.4.0-5b8426f429c6a89a` |
| 放入第二个包目录，**不重启** | `READY` | — | **证明是加载期闸**（GATE 的提醒成立） |
| **重启 Web 后** | `INVALID` | `KNOWLEDGE_BUNDLE_AMBIGUOUS` | 加载期拦下，且管理 health 同步可见 |
| 删除多余包 + 重启 | `READY` | — | 自动恢复 |

证据：`s9_n05_b01.json`。**运维手册必须写明"现场换包后要重启 Web 才生效/才会拦"**。

### 8.2 B-01 跨平台换行回归 → **通过** ✅

| 检查 | 结果 |
|---|---|
| `chunks.jsonl` sha256 vs manifest | `951cb726…a26152` **一致** |
| `index.json` sha256 vs manifest | `bdd6cc3c…0eb3713` **一致** |
| 换行符 | `chunks.jsonl`：0 个 CRLF / 23 个 LF；`index.json`：0 个 CRLF |
| `test_shipped_bundle_ready` + `test_sources_rebuild_matches_shipped` | **2 passed** |

证据：`s10_b01.json`。

---

## 9. 通过项清单（含独立复跑数字）

| 验证项 | 实测结果 | 证据 |
|---|---|---|
| `tests/copilot/` 独立复跑（**干净树**，产品代码零改动） | **114 passed**，与 Q 自述 / A 实测吻合 | 本轮实跑输出 |
| `tests/copilot/` 独立复跑（打补丁副本） | **114 passed**（补丁不改变用例数） | 本轮实跑输出 |
| RBAC 四角色矩阵 | admin 200 / 第二 admin 200 / developer 200（LOCAL_ONLY）/ **auditor 403** —— 符合 §9.1"auditor 及自定义角色默认关闭" | `s0_smoke.json` |
| 匿名访问 `/copilot/capabilities` | **401**（拒绝匿名，符合"禁止匿名旁路"） | 启动脚本探测 |
| N-09 双管理员 + 申请/复核分离 | 甲申请→PENDING/enabled=0；**甲自批→403 FORBIDDEN**；乙复核→APPROVED/enabled=1；**旧 revision→409 CONFIG_CHANGED** | `s2_grants.json` |
| 实例授权闸 | 未授权实例不出现在 `/connections`；INSTANCE 会话必须显式授权 | `s2_grants.json` |
| 场景来源必填闸（§12.3） | `RULE_EXPLAIN` 缺 `rule` refs → **422 SOURCE_REQUIRED** | `s4_scene_matrix.json` |
| 限流闸（§12.2） | preview 超限 → **429 RATE_LIMITED**；单主体第二个活动轮 → **429** | `s8_concurrency.json`、`s4_scene_matrix.json` |
| 幂等与状态机 | 提交返回 202 + `deadline_at` + `poll_after_ms`；轮询到终态；USAGE_HELP 走通 `SUCCEEDED` | `b5_copilot_page.json` |
| 投影三闸**判定逻辑**（不是出站内容） | 主备都允许→`SCHEMA_IDENTIFIERS`；备用腿不允许→回落 `ALIASED` —— 判定逻辑正确（但见 M01：对实发无效） | `s4_egress.json` |
| 主备故障转移 | 主腿 503 → 真实切换备用腿并成功返回 | `s4_egress.json` |
| 出站安全 | TLS 校验开启、`trust_env=False`、Bearer 头、**无 tools/functions**、遵循批准端点清单 | `gateway-requests.jsonl` |
| 独立 HTML 建议报告（CP-F14 部分） | `200` + `Content-Disposition: attachment` + 文件名 `copilot-advice-<短ID>-<日期>.html` + **无 `<script>`/无远程引用** + 页首"内部建议资料" | `s5_export_and_conn.json`、`export_sample.html` |
| 反馈（CP-F14） | "有用/有误"可提交并即时生效 | `b5_copilot_page.json` |
| B 组故障隔离（结构性只读例外） | `health` 在模块态下仍可读，符合 §10.4 只读例外 | 本轮 SIT 结论复现，未另造故障 |

---

## 10. 已用临时补丁验证的修复（回滚前的实测）

为把余下验收做完，我**临时**施加了三个最小改动（D40-B01 / B02 / B03 的补丁），
在**打过补丁的副本**上复测，然后**完整回滚**：

| 验证 | 修补前 | 修补后 |
|---|---|---|
| 登录页是否可用 | 遮罩挡住登录按钮（B01 修好后） / 界面完全不可达（B01 修好前） | **无遮罩、登录成功**（`dbg_refbind.json`） |
| 侧栏「Copilot专家助手」页 | 空白 / 内容长度 0 | **正常渲染**：`Copilot 专家助手｜…｜新建通用会话｜新建实例会话｜…`（`b5_copilot_page.json`） |
| 全局抽屉 | 点击只发 API、无 UI | **正常打开**，10 个场景选项齐全（`b1_local_help.json`） |
| 模型路径 | 全部 `FAILED`、出站 0 次 | 全部 `SUCCEEDED`、出站 1~2 次（`s4_egress.json`） |
| 核心用户旅程 | 不可执行 | **会话 → 预览（别名脱敏，知识 8 条）→ 确认提交 → `SUCCEEDED` → 查看结果 → 导出报告 → 反馈** 全通（`b5_copilot_page.json` + `shots/b5-0*.png`） |

补丁文件：`docs/evidence/v1.6.4.0-uat-d/verified-fix.patch`（`git apply` 可直接用）。
**产品代码在本轮 UAT 中零改动**，回滚后 `git status` 仅剩本证据目录未跟踪，
并在干净树上复跑 `tests/copilot/` 得 **114 passed**。

---

## 11. 出口判定与后续建议

### 11.1 判定

**第一轮 UAT 不通过（NO-GO）。**

判据（按 A 在 GATE 里对 UAT 的要求，逐条对照）：

1. **准入清单里的四类必测项已全部真跑** —— 本轮完成了 A 交办的全部必测，没有用 SIT 结论替代；
2. **存在阻断级缺陷**：用户**在浏览器里根本到不了功能**（B01/B02/B05），
   **模型路径一旦启用就崩溃**（B03），**管理员无法启用模型**（B04）；
3. **存在严重级的安全口径缺口**：预览与实际出站不一致、投影三闸对实发无效（M01）；
4. **原审核主流程与共享资源未受损**，SIT 已锁行为未回退（114 passed）。

第 1、3、4 条满足，但第 2 条是硬伤：**本轮验收的对象"一个能用的人机界面"尚不存在**，
所以不能签通过。这不是"打磨问题"，是"交付物不完整"。

### 11.2 建议的整改与复验顺序

**必须同批施工**（有依赖关系，拆开会互相卡住）：

| 批次 | 内容 | 依赖 |
|---|---|---|
| **第 1 批（阻断）** | D40-B01 + B02 + B03 + B04（自检接口）+ B05（AI配置页） | B05 依赖 B04；B04 依赖 B03 的 import 修好，否则自检自己就会崩 |
| **第 2 批（安全口径）** | D40-M01 第 1~3 步（改 `_payload()` + 加"预览=实发"锁 + 三配置断言） | 独立 |
| **第 3 批（业务可达）** | D40-M02（ContextBridge + 8 个模块入口）+ D40-M03（页面初始化） | 独立 |
| **第 4 批（补齐）** | D40-M04（feedback-summary）、D40-M05（应急脚本）、D40-N01~N06 | 独立 |

**每一批都必须带"防回退锁"**（本报告已逐条给出用例骨架）——
本轮 5 个阻断缺陷里，**有 4 个是"没有任何测试覆盖那条路径"造成的**：
- B01 是纯 HTML 嵌套，单元测试天然看不见 → 需要一条 **DOM 结构锁**
  （断言 `#app` 下 `.copilot-drawer` / `[data-testid=copilot-page]` 的祖先链**不含** `el-dialog`）；
- B02 需要一条**首屏无遮罩锁**（未登录页 `document.querySelectorAll('.el-overlay:visible').length === 0`
  且登录按钮 `elementFromPoint` 不被遮挡）；
- B03 需要**真跑模型路径锁**；
- B04 需要**端到端启用锁**（只走公开 API）。

建议把上述四条放进 `tests/copilot/`，并在 UAT 复验时由 A 做一次防回退重跑
（13 条变异 + 全量回归受控对照）。

**第 2 轮 UAT 我会重点复验**：B01~B05 是否真闭合、M01 的出站报文是否等于冻结投影
（我会再抓一次逐字节报文对比）、以及 M02 接好线之后
**八个业务场景能否真的从界面走通**（这将是本轮之后最有价值的一次验收）。

### 11.3 对设计基线的一条提请

`DETAIL §12.5` 把 `self-tests` 与 `feedback-summary` 列在接口清单里，实现侧却没有 ——
说明**契约到实现的比对目前没有自动化**。建议补一条"设计接口清单 ↔ `openapi.json` 逐条比对"的门禁
（本轮正是用这个方法一次抓出两个缺失接口，脚本见 `uat40_scenarios.py::s1_routes`，可直接复用）。
另外 §2 里 O 要补的 `KNOWLEDGE_BUNDLE_AMBIGUOUS` 我已实测确认行为正确
（`s9_n05_b01.json`），**设计文本补登记后即可闭合**。

---

## 12. 证据索引

目录：`docs/evidence/v1.6.4.0-uat-d/`

| 类别 | 文件 |
|---|---|
| **夹具** | `_boot.py`、`start_all.ps1`、`stop_all.ps1`、`restart_web.ps1`、`harness_d40.py`、`prepare_uat_d40.py`、`mock_llm_gateway.py`、`uat_d40_api.py` |
| **已用临时补丁** | `verified-fix.patch`（D40-B01/B02/B03，已验证后回滚） |
| **接口与端点比对** | `s0_smoke.json`、`s1_routes.json`、`s11_entry_scan.json` |
| **管理端配置与权限** | `s2_admin_part1.json`、`s2_admin_part2.json`、`s2_grants.json` |
| **出站投影（GATE §5-1）** | `s4_egress.json`、`s4_scene_matrix.json`、`s5_export_and_conn.json`、`export_sample.html` |
| **知识质量（GATE §5-3）** | `s6_knowledge_quality.json` |
| **应急演练（GATE §5-4）** | `s7_emergency_disable.json`、`s7b_emergency_recovery.json` |
| **并发与 P95（GATE §5-2）** | `s8b_concurrency.json`、`s8c_concurrency_job.json`、`s8d_idle_job.json` |
| **N-05 / B-01（GATE §5.1）** | `s9_n05_b01.json`、`s10_b01.json` |
| **浏览器取证** | `b1_local_help.json`、`b2_business_entry.json`、`b3_admin_page.json`、`b5_copilot_page.json`、`b6_menu_coverage.json` |
| **根因取证** | `dbg_unclosed.json`、`dbg_rootcause3.json`、`dbg_rootcause4.json`、`dbg_refbind.json`、`dbg_drawer3.json`、`dbg_drawer4.json` |
| **真实截图（21 张）** | `shots/b1-*.png`（抽屉/本地帮助）、`shots/b3-ai-config.png`（AI配置空白页）、`shots/b5-0*.png`（会话页→预览→提交→结果）、`shots/dbg-refbind-*.png`（未登录被遮罩 / 修复后）、`shots/dbg-copilot-page.png`、`shots/dbg-copilot-after-click.png` |

### 一键复现

```powershell
# 1) 起夹具（网关 + Web + Copilot runner + 元数据执行器）
powershell -ExecutionPolicy Bypass -File docs/evidence/v1.6.4.0-uat-d/start_all.ps1
# 2) 端点清单比对 / 管理配置 / 出站抽检 / 知识黄金集 / 应急演练 / 并发门禁
python docs/evidence/v1.6.4.0-uat-d/uat40_scenarios.py s1_routes
python docs/evidence/v1.6.4.0-uat-d/uat40_admin.py s2_admin
python docs/evidence/v1.6.4.0-uat-d/uat40_egress.py
python docs/evidence/v1.6.4.0-uat-d/uat40_knowledge.py
python docs/evidence/v1.6.4.0-uat-d/uat40_emergency.py
python docs/evidence/v1.6.4.0-uat-d/uat40_concurrency2.py
# 3) 真实浏览器
python docs/evidence/v1.6.4.0-uat-d/browser_uat_d40.py b1_local_help
python docs/evidence/v1.6.4.0-uat-d/browser_uat_d40b.py b5_copilot_page
# 4) 收尾
powershell -ExecutionPolicy Bypass -File docs/evidence/v1.6.4.0-uat-d/stop_all.ps1
python docs/evidence/v1.6.4.0-uat-d/prepare_uat_d40.py untrust   # 还原 certifi
```

> 复现前请先施加 `verified-fix.patch`（否则 B01/B02/B03 未修，浏览器与模型路径都不通），
> 复现完再回滚 —— 本轮就是这么做的。

---

## 13. 本轮没有做到的事（如实声明）

1. **没有接真实内网模型网关**：用同协议受控网关承载出站并逐字节抓包，
   供应商侧的 TLS/认证/留存政策仍需 G 的 CP-GATE-DATA 取证。
2. **没有在 systemd/Linux 上跑应急停用**：本机无 systemd，`systemctl` 分支未实测；
   DB 侧分支与"非 systemd"分支是真实执行的。
3. **没有做真实模型黄金集的答案质量评测**（§15.4 的 100 题、必需事实正确率 ≥95% 等）：
   这需要真实获准模型 + 经批准脱敏黄金集，属 CP-GATE-TEST 的独立证据，
   本轮的 18 题**只测检索命中**，不测答案事实正确率，两者不能互相替代。
4. **没有做 200 轮合成压力与长稳测试**（§15.3）：本轮做的是双账号满并发 + P95 对照。
5. **没有验证五条发布链路**（全新安装/增量升级/补丁/回退/验证打包）：
   属 CP-GATE-RELEASE，需 G 的离线安装与同版本预演。
6. **没有改动任何产品代码**：所有修复都是"已验证的补丁 + 施工说明"，由 Q 施工。

---

**-智能体D**

*2026-09-15 · TDSQL-SQLCheck v1.6.4.0 AI Copilot 第一轮 UAT*
