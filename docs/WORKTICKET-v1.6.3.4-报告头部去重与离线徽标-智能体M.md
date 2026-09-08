# WORKTICKET-v1.6.3.4 — UAT M 级三项整改工单（面向 Q）

| 项 | 内容 |
|---|---|
| 出单人 | 智能体 M（UAT 测试） |
| 收单人 | 智能体 Q（开发） |
| 触发来源 | `docs/UAT-v1.6.3.4-用户验收测试报告-智能体M.md` §9 |
| 修复目标 | v1.6.3.4 |
| 施工约束 | **不动既有规则编号与总数（121 条不变）；不动 R121 策略；不动 parser/checker 解析路径；不破坏 GW-C1 / REP-12 既有变异锁** |
| 期望完成 | Q 改完后智能体 M 重跑 UAT §3/§4/§6 三套核心用例 + §9 三项 M 级目标；通过后由 Mr.Linsang 决定是否进入发布审批 |

---

## 总览：三张工单的依赖与拆批建议

| 工单 | 范围 | 依赖 | 风险 | 建议批次 |
|---|---|---|---|---|
| **TKT-M01 H08 报告头部去重** | 单文件 + 单注入函数 + 1 个测试函数 | 无 | 低（仅改 H08 注入逻辑；其余 13 入口渲染路径不动） | 独立一批（半天） |
| **TKT-M02 离线徽标 badge** | 1 个渲染函数 + 14 个 generator 改 1 行参数 + 2 个 CSS 文件 | 无 | 低（display-only；有/无徽标视觉差异不影响功能） | 独立一批（半天） |
| **TKT-M03 登录页 automation-friendly** | 1 个 el-input 标签 + 1 个监听 | 无 | 极低（前端不破坏现有用户路径） | 独立一批（半小时） |

**总工作量：≤ 1.5 个工作日。** 全部独立可验证、可单批 ship，建议 Q 按 M01 → M02 → M03 顺序处理。

> M 建议**不**塞入 v1.6.3.4 patch（应独立打包 v1.6.3.5 或 v1.6.3.4-patch1），理由：
> 1. v1.6.3.4 已 SIT 整改通过（A 第二轮"通过-有条件"），三项 M 都是显示层/UX 改进，不属于功能修复；
> 2. 改完后必须重跑全量 1995+3 个用例并做 4 项 UAT 复验，工单量虽小但验证量与 patch 相当；
> 3. 业务侧更关心 PAR-21 真实容量回填与 71 MiB 真实样本验收，先把这些"硬骨头"啃完再叠 M 级小修更稳。
> 决策权在 Mr.Linsang。

---

## TKT-M01 — H08 网关报告头部"双块"展示去重

### M1.1 问题

`id=8`（UAT 上传 20000 行新报告）HTML 头部同时存在两行"实例连接名称"块：

```text
实例连接名称：SIT-分布式实例A  库：tdsql_check   ← D02 H08 服务时注入（v1.6.3.4）
实例连接名称：未关联实例（网关日志分析）        ← 旧 analyze_gateway_log.py 模板自带占位（v1.6.3.2 之前）
```

设计意图（`DETAIL-v1.6.3.4` §3.4 H08）："新网关模板预留唯一 `data-report-context-version="1"` 来源区……不得为显示名称放松 CSP"——预留"唯一"，但旧模板的"未关联实例"占位并未被设计清掉，导致 D02 注入后两者并存。

### M1.2 现象保留路径

- 历史报告（v1.6.3.4 之前生成的 `report_html` 已被新注入覆盖一层）：可能仍共存
- **新报告**（UAT 上传 20000 行那条 `id=8`）：必然共存
- 影响：报告头部视觉重复，混淆"哪个是权威"

### M1.3 修改文件清单

| 文件 | 变更 | 备注 |
|---|---|---|
| `backend/services/gateway_log_analysis/analyze_gateway_log.py` | 删除旧模板中"未关联实例"占位 div（约 30—50 行，搜索"未关联实例"可定位） | 一次删，不再生成 |
| `backend/api/gateway_log.py::get_report_html` | 调 `inject_context_into_html` 之前先**清理**第一个未带 `data-report-context-version` 的"实例连接名称"块 | 防御性去重，防历史报告再被注入 |
| `tests/test_v1634_report_context.py` | 新增 `test_rep_h08_header_no_duplicate` 回归锁 + 变异测试 | 必跑 |
| `tests/test_v1634_r043_dml_target.py` 等已有 53 测试 | 不动 | — |

### M1.4 照图施工级代码

#### M1.4.1 旧模板侧（`analyze_gateway_log.py`）

搜索占位段（v1.6.3.4 中该段在主 HTML 模板 `<body>` 与 `<h1>` 之间），删除以下整段（示意性，**Q 须以本机代码为准**）：

```html
<!-- 删除 BEGIN -->
<div class="meta-legacy" style="...">
  实例连接名称：<strong>未关联实例（网关日志分析）</strong>
</div>
<!-- 删除 END -->
```

替换为一行注释占位（避免回归）：

```html
<!-- v1.6.3.4 D02 H08: 实例连接名称 来源块由 get_report_html 服务时注入；本模板不再自渲染占位 -->
```

#### M1.4.2 注入侧（`gateway_log.py::get_report_html`）

定位当前调用：

```python
html = inject_context_into_html(html, context_html)
```

替换为：

```python
# v1.6.3.4 / TKT-M01: 清理旧 report_html 的"实例连接名称"占位块（无 data-report-context-version 属性），
# 避免与本次新注入的来源块在头部并存。
html = _strip_legacy_context_block(html)
html = inject_context_into_html(html, context_html)
```

并在 `gateway_log.py` 顶部新增私有工具函数：

```python
import re

_LEGACY_CONTEXT_BLOCK_RE = re.compile(
    r'<\s*div[^>]*>\s*实例连接名称\s*[：:][^<]*(?:<[^>]+>[^<]*)*未关联实例[^<]*(?:<[^>]+>[^<]*)*</\s*div\s*>',
    re.IGNORECASE | re.DOTALL,
)


def _strip_legacy_context_block(html: str) -> str:
    """去除旧模板的'实例连接名称'占位 div（无 data-report-context-version 属性）。

    仅清理首部至首个 <h1> 之间的占位，避免误伤报告正文里'实例连接名称'字样。
    设计依据: TKT-M01 (UAT-v1.6.3.4 §9.1)。
    """
    if 'data-report-context-version=' in html[:html.find('<h1', 0) if '<h1' in html else len(html)]:
        # 已注入了新块（理论上不会到这，但防御性 return）
        return html
    h1_idx = html.find('<h1')
    if h1_idx < 0:
        return html
    head = html[:h1_idx]
    body = html[h1_idx:]
    head = _LEGACY_CONTEXT_BLOCK_RE.sub('', head, count=1)
    return head + body
```

### M1.5 回归锁（`tests/test_v1634_report_context.py` 新增）

```python
def test_rep_h08_header_no_duplicate():
    """TKT-M01: 网关新报告 HTML 头部仅出现 1 次"实例连接名称"块。
    设计: UAT-v1.6.3.4 §9.1；DETAIL-v1.6.3.4 §3.4 H08 '唯一预留'。
    """
    from backend.api.gateway_log import _strip_legacy_context_block
    legacy_html = (
        '<html><body>'
        '<div class="meta-legacy">实例连接名称：<strong>未关联实例（网关日志分析）</strong></div>'
        '<h1>TDSQL Gateway 日志分析报告 - x.log</h1>'
        '<p>...</p></body></html>'
    )
    # 单跑 _strip：应移除旧占位
    out = _strip_legacy_context_block(legacy_html)
    assert out.count('实例连接名称') == 1
    assert '未关联实例（网关日志分析）' not in out
    assert '<h1' in out

    # 模拟 D02 注入流程：先 strip 再 inject，注入后仍只 1 次
    from backend.services.report_context import render_for_record
    new_block = render_for_record(
        {'report_context_json': '{"version":1,"connections":[{"connection_id":"5ea70d74","connection_name":"SIT-分布式实例A"}]}'},
        scene='网关日志分析',
    )
    from backend.api.gateway_log import inject_context_into_html  # 见 D02
    final = inject_context_into_html(out, new_block)
    assert final.count('实例连接名称') == 1
    assert 'SIT-分布式实例A' in final
```

变异测试（Q 自行跑，建议合并进 DML-18 套件外的 `--mutant` 段）：

```text
变异 M-M01: 把 _strip_legacy_context_block 改为 return html（不退化）
期望: test_rep_h08_header_no_duplicate 变红（count==2）
恢复: 变绿
```

### M1.6 验证步骤（Q 提交后由智能体 M 跑）

```bash
# 1) 跑既有 1998 用例 + 1 新增 = 1999 全绿
pytest tests/ -q

# 2) 重跑 UAT 脚本
python _uat_req04_upload.py        # 上传 1000 行新报告 → id=N+1
python -c "
import urllib.request
with open('_uat_token.txt') as f: token = f.read().strip()
html = urllib.request.urlopen(urllib.request.Request(
    f'http://127.0.0.1:8003/api/v1/gateway-log/reports/{N+1}/html',
    headers={'Authorization':'Bearer '+token})).read().decode()
# M1.6 关键断言
assert html.count('实例连接名称') == 1
assert '未关联实例（网关日志分析）' not in html
assert 'SIT-分布式实例A' in html
print('TKT-M01 UAT 复验通过')
"
```

---

## TKT-M02 — 离线/降级"实例连接名称"块加徽标

### M2.1 问题

H01 文件审核报告"实例连接名称：未关联实例（离线文件审核）"使用普通文字，与已关联的强样式（`SIT-分布式实例A` 蓝色）对比，多报告并列时难以一眼区分。

### M2.2 修改文件清单

| 文件 | 变更 | 备注 |
|---|---|---|
| `backend/services/report_context.py::render_report_context` | 加 `badge: bool = False` 参数 | 默认 false，不破坏既有调用 |
| `backend/api/sql_audit.py`、`backend/api/slow_query.py`、`backend/api/inspection.py`、`backend/api/gateway_log.py`、`backend/api/raw_slowlog.py`、`backend/services/scan_compare_report.py`、`backend/services/daily_inspect_service.py`（共 14 个 generator 入口） | 调 `render_for_record(..., badge=True)` | 一次 PR |
| `frontend/static/css/app.css` 与 `theme-dark-blue.css` | 增 `.badge-offline` 浅/深主题样式 | display-only |
| `tests/test_v1634_report_context.py` | 加 `test_rep_badge_offline_render` | — |

### M2.3 照图施工级代码

#### M2.3.1 `render_report_context`（`report_context.py`）

定位函数签名（约第 464 行）：

```python
def render_report_context(
    context: Optional[ReportContext],
    role: Optional[str] = None,
    scene: str = "",
) -> str:
```

替换为：

```python
def render_report_context(
    context: Optional[ReportContext],
    role: Optional[str] = None,
    scene: str = "",
    badge: bool = False,
) -> str:
    """v1.6.3.4 TKT-M02: badge=True 时对离线/降级块加 .badge-offline 徽标样式。
    默认 False，不破坏既有调用方。
    """
    # 既有渲染逻辑保持不变
    html = _render_report_context_impl(context, role, scene)
    if badge and ("未关联实例" in html or "历史未记录名称" in html):
        # 包裹已渲染的 <span class="...实例连接名称..."> 为带徽标版
        html = _wrap_offline_with_badge(html)
    return html
```

把原函数体改名 `_render_report_context_impl`（Q 在本机直接重命名），新增：

```python
def _wrap_offline_with_badge(html: str) -> str:
    """将含'未关联实例'或'历史未记录名称'的 <span> 替换为带 .badge-offline 的徽标版。
    仅在 '实例连接名称' 来源块内做替换，避免误伤。
    """
    import re
    badge_open = '<span class="badge-offline" style="background:#fff3cd;color:#856404;padding:2px 8px;border-radius:3px;">'
    badge_close = '</span>'
    # 匹配 '实例连接名称：<strong>...</strong>' 段，把 <strong>...</strong> 包到徽标里
    pattern = re.compile(
        r'(实例连接名称[：:]\s*)<strong>([^<]*(?:未关联实例|历史未记录名称)[^<]*)</strong>',
        re.UNICODE,
    )
    return pattern.sub(
        lambda m: f'{m.group(1)}{badge_open}{m.group(2)}{badge_close}',
        html,
    )
```

> 设计细节：保留 `实例连接名称：<strong>...</strong>` 的 DOM 结构，**仅替换 strong 内部**为徽标 `<span>`；不引入新的外层标签，不破坏既有 CSS 与 H07/H08 CSP/nonce 链。

#### M2.3.2 14 个 generator 渲染调用方

将：

```python
context_html = render_for_record(record, scene=scene, role=role)
```

改为：

```python
context_html = render_for_record(record, scene=scene, role=role, badge=True)
```

涉及函数（Q 以本机 `Select-String` 查 `render_for_record` 定位）：

- `backend/api/sql_audit.py::export_file_report_html` (H01)
- `backend/api/sql_audit.py::export_extracted_report_html` (H02)
- `backend/api/slow_query.py::export_scan_task_html` (H03)
- `backend/api/inspection.py::export_schema_check_report` (H04)
- `backend/services/scan_compare_report.py::render_single_snapshot_html` (H05a-d)
- `backend/services/scan_compare_report.py::render_compare_html` (H06a-d)
- `backend/services/daily_inspect_service.py::generate_comparison_html_report` (H07)
- `backend/api/gateway_log.py::get_report_html` (H08, 服务时注入)
- `backend/api/raw_slowlog.py::export_events(format='html')` (H09)
- H10—H14（CLI/磁盘脚本，badge 在 CLI 输出里也加 `badge=True`，但 `report_context.py` 不应被离线脚本 import，故 CLI 侧建议直接调用 `_wrap_offline_with_badge`）

#### M2.3.3 CSS（`app.css` 与 `theme-dark-blue.css` 各加 5 行）

```css
/* v1.6.3.4 TKT-M02: 离线/降级"实例连接名称"徽标 */
.badge-offline {
  background: #fff3cd;
  color: #856404;
  padding: 2px 8px;
  border-radius: 3px;
  font-weight: 600;
}
html[data-theme="dark"] .badge-offline,
body[data-theme="dark"] .badge-offline {
  background: #3d3520;
  color: #f0d68c;
}
```

### M2.4 回归锁（`tests/test_v1634_report_context.py` 新增）

```python
def test_rep_badge_offline_render():
    """TKT-M02: 离线/降级块带 .badge-offline 徽标；已关联块保持 strong 样式不变。"""
    from backend.services.report_context import render_report_context, ReportContext
    # 离线块
    ctx_off = ReportContext(version=1, origin='offline', connections=[])
    out = render_report_context(ctx_off, scene='离线文件审核', badge=True)
    assert 'class="badge-offline"' in out
    assert '未关联实例（离线文件审核）' in out
    # 已关联块不变
    from backend.services.report_context import ConnectionContext
    ctx_on = ReportContext(version=1, origin='bound', connections=[ConnectionContext(connection_id='c1', connection_name='SIT-分布式实例A')])
    out_on = render_report_context(ctx_on, badge=True)
    assert 'class="badge-offline"' not in out_on
    assert '<strong>SIT-分布式实例A</strong>' in out_on
    # badge=False 不影响
    out_off_nobadge = render_report_context(ctx_off, scene='离线文件审核', badge=False)
    assert 'class="badge-offline"' not in out_off_nobadge
```

### M2.5 验证步骤

```bash
pytest tests/ -q                                  # 1999 + 1 = 2000 全绿
python _uat_req01.py                              # 跑 H01 文件审核
python -c "
import urllib.request
with open('_uat_token.txt') as f: token = f.read().strip()
items = __import__('json').loads(urllib.request.urlopen(urllib.request.Request(
    'http://127.0.0.1:8003/api/v1/audit/file-reports?limit=1',
    headers={'Authorization':'Bearer '+token})).read().decode()).get('items', [])
html = urllib.request.urlopen(urllib.request.Request(
    f'http://127.0.0.1:8003/api/v1/audit/file-reports/{items[0][\"id\"]}/html',
    headers={'Authorization':'Bearer '+token})).read().decode()
assert 'class=\"badge-offline\"' in html, 'M02 离线徽标未生效'
print('TKT-M02 UAT 复验通过')
"
```

---

## TKT-M03 — 登录页 el-input automation-friendly

### M3.1 问题

UAT 智能体 M 通过内置浏览器自动化登录时，`ref` 在 `inspect` 后立即失效，无法完成 `fill` / `type` 等操作；坐标点击 + 单字符 press_key 也因 Vue 受控 `el-input` 未派发原生 `input` 事件而失败。**生产用户路径不受影响**（人类用户直接键入），仅影响 UAT 自动化工具链。

### M3.2 修改文件清单

| 文件 | 变更 |
|---|---|
| `frontend/index.html` | 登录页 `<el-input>` 加 `@keydown.enter="login"` 与 `autocomplete="username"` / `autocomplete="current-password"`；`<form>` 加 `@submit.prevent="login"`；根容器加 `id="login-form"` |
| `tests/` 下不新增 | 纯前端 UX 改进，单元/E2E 已有覆盖路径不受影响 |

### M3.3 照图施工级代码（`frontend/index.html` 登录页部分）

定位登录表单（约第 23—50 行），**示例 BEFORE**（以本机为准，Q 先 inspect）：

```html
<div v-if="!authState.token" class="login-page">
  ...
  <input data-backend-node-id="82" placeholder="用户名" type="text" />
  <input data-backend-node-id="107" placeholder="口令" type="password" />
  <button type="button" @click="login">登 录</button>
  ...
</div>
```

**AFTER**（Q 把 el-input 标签属性加齐；非 el-input 也可）：

```html
<form @submit.prevent="login" id="login-form" autocomplete="on">
  <el-input
    v-model="loginForm.username"
    placeholder="用户名"
    autocomplete="username"
    @keydown.enter="login"
    data-testid="login-username"
  />
  <el-input
    v-model="loginForm.password"
    type="password"
    placeholder="口令"
    autocomplete="current-password"
    show-password
    @keydown.enter="login"
    data-testid="login-password"
  />
  <el-button
    type="primary"
    native-type="submit"
    @click="login"
    data-testid="login-submit"
  >登 录</el-button>
</form>
```

> 设计细节：`@keydown.enter="login"` 已存在（app.js 第 482 行已绑定 `Enter` 提交），这里只是把它显式绑到 `el-input`；`@submit.prevent` 让 form 提交也走同一逻辑（自动化按 Enter 即可）。`data-testid` 给 UAT 工具提供稳定 selector。

### M3.4 回归

M3 不需要新增测试。`smoke_test.py` 已覆盖登录链路；`test_v2_uat.py` 已覆盖登录失败/锁定/锁定解除。

### M3.5 验证步骤（智能体 M 跑）

```text
1) Q 改完后重启 8003
2) 智能体 M 用浏览器 inspect 登录页：用户名输入框 ref 应稳定不失效
3) 用 fill 直接注入 admin / Uat@2026M!，点击登录
4) 断言：进入主界面，左侧菜单可见"Dashboard / 即时审核 / 文件审核 / ..."
5) 截屏留证
```

---

## 三张工单总体验收清单（智能体 M 提交 Mr.Linsang）

| 项 | Q 提交后 M 跑 | 通过判据 |
|---|---|---|
| 既有 1998 + 新增测试 | `pytest tests/ -q` | 0 failed |
| 变异锁 | 临时退化工单中标注的 1 行，断言对应测试变红 | 全部恢复后绿 |
| TKT-M01 | `_uat_req04_upload.py` 上传 → id=N+1 报告 HTML 仅 1 次"实例连接名称" | ✅ |
| TKT-M02 | `_uat_req01.py` 跑 H01 报告，HTML 含 `class="badge-offline"` | ✅ |
| TKT-M03 | 浏览器真实 fill 登录，截图主界面 | ✅ |
| 旧用例不退化 | 跑 1998 既有 + 1 M01 + 1 M02 = 2000 全绿 | ✅ |
| 业务规则总数 | 仍是 121 条 | ✅（M01/M02/M03 不动规则） |

---

出单人：智能体 M
提交给：智能体 Q
抄送：Mr.Linsang
