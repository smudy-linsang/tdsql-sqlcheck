# v1.6.4.0 AI Copilot 第二轮质检验收报告（O）

> 质检日期：2026-09-17
> 被测基线：`338f7680f2ec70372dfcb170366a6a9be7be9a03`（开发人员 Q 针对 QC1 缺陷修复提交）
> 前序基线：`c35c697e72ada135d79d1dcd46672563aebc447d`（QC1 不通过基线）
> 设计基线：Rev.D＋N-10，冻结提交 `4fb1f2e462dd17c036ed69f18b2128128614fdbd`
> 验收角色：设计与独立质检责任人 O；接替前序未完结状态，以硬证据独立做出裁决

---

## 1. 质检裁决与总体结论

**第二轮质检结论：不通过（QC2 REJECT / NO-GO），当前基线严禁作为 v1.6.4.0 生产发布。**

本轮质检针对开发人员 Q 在提交 `338f768` 中实施的 QC1 缺陷整改进行了逐项深度复验。复验发现：
- **QC1 提出的 12 项缺陷中，有 9 项已有效修复并闭环**（包括 WebUI 模型与路由配置、退出登录清空私有草稿、实例授权撤销即时 403 阻断、业务上下文桥接、Bearer 导出、DTO 刷新与自检等）；
- **但遗留 2 项严重阻断缺陷（BLOCKER）与 1 项重要缺陷（MAJOR）未达成验收标准**，其中 2 项阻断缺陷存在单测未覆盖导致的“假绿”现象：
  1. **【BLOCKER】大模型出站请求上下文彻底丢失（`history: []` 恒空）**：`_build_history` 在解密历史预览快照时，因传入的 AAD（附加验证数据）与加密时不一致（传入 `None`，加密使用 `preview_id`），触发 AES-GCM 标签验证失败异常（`InvalidTag`），被外层静默吞掉，导致发送给大模型的请求中 `history` 恒为空数组。大模型在第二轮及后续多轮对话中完全失去对话记忆，无法支持指代与追问。
  2. **【BLOCKER】包含候选 SQL 时后端执行器崩溃（`AttributeError`）**：`_t09_validate` 函数被错误定义在模块顶层而非 `TurnExecutor` 类内部，当大模型返回带候选 SQL 的响应时，执行器调用 `self._t09_validate` 触发致命属性缺失异常，导致该轮次直接跌入 `FAILED / INTERNAL_ERROR` 终态。现有的 117 项单测由于全部采用空候选（`sql_candidates: []`）打桩，未覆盖此分支。
  3. **【MAJOR】候选 SQL 的前端 Diff 与安全确认链路受后端阻断影响无法端到端验证**。

---

### 用户核心关切问题的明确解答

| 关切问题 | 质检结论 | 详细实测事实与证据 |
|---|---|---|
| **1. 在AI配置界面人类是不是能够在WebUI顺利进行大模型API配置？** | **能（已通过）** | **已彻底修复 QC1-B03**。Q 在 `338f768` 重构了 `frontend/static/js/copilot_admin.js` 与 `frontend/index.html`。管理员在 WebUI 可完整操作：<br>① 新增/编辑 Provider 抽屉（支持端点下拉、模型ID、Bearer密钥的新增与 KEEP/REPLACE、Token配额）；<br>② 10 个场景路由映射抽屉（按场景绑定 Provider 与隐私级别）；<br>③ 双管理员授权抽屉（申请、审批、即时撤销）；<br>④ 全局 Copilot 运行设置（总闸开关、超时、重试等）。<br>所有配置操作均有前后端真实交互，表单校验与刷新正常。<br>证据截图：`qc2-02-ai-config-page.png` 至 `qc2-07-settings-tab.png`。 |
| **2. 是不是能够通过API接口正确调用大模型？** | **受控网络连通，但业务发布存在致命阻断（BLOCKER）** | **部分连通，但候选 SQL 触发崩溃**：<br>① 网络与鉴权通畅：Provider HTTPS/TLS、Bearer 鉴权、OpenAI-compatible 协议握手、JSON Schema 结构化出参均能正确交互；纯文本问答可正常入库。<br>② **致命缺陷**：一旦大模型返回包含修改建议的候选 SQL（`sql_candidates` 非空），服务端执行器在 `_publish_model` 阶段抛出 `AttributeError: 'TurnExecutor' object has no attribute '_t09_validate'`，轮次直接崩溃失败（`INTERNAL_ERROR`）。AI 最核心的 SQL 辅助修复能力在交付最后一公里断裂。 |
| **3. 能不能和Copilot助手你一言我一句交互式对话咨询本项目相关内容？** | **无法达到设计验收标准（严重阻断 BLOCKER）** | **前端气泡能往复，但模型出站无记忆**：<br>① 前端界面可以连续输入问题，也可以收到单轮文本回复；<br>② **出站网络真实抓包证实**：在第二轮、第三轮追问时（例如问“本项目的 R003 规则是什么？”，接着问“接着解释它与表注释的关系”），网关接收到的请求体中 `history` 字段**恒为空数组 `[]`**！大模型根本不知道前序对话内容，多轮交互实质上退化成了单轮孤立提问。<br>证据日志：`docs/evidence/v1.6.4.0-qc2-o/gateway-tail.jsonl` 第 6、7、8 行。 |

---

## 2. 复测环境与执行基线

- **服务运行环境**：
  - Web UI：`http://127.0.0.1:8026`（由 Python 3.11 原生 Web 服务托管）
  - 本机受控 HTTPS 网关（Mock LLM Gateway）：`https://172.31.240.1:8453`（主模型）、`8454`（备模型）、`8455`（自检模型），具备双向请求抓包记录能力
  - 后台执行器：`copilot-runner`、`metadata-runner` 常驻运行
- **测试账号矩阵**：
  - 管理员 A：`qc_o_1640`（执行配置、授权发起）
  - 管理员 B：`qc_o_1640b`（执行双管理员授权审核）
  - 开发者：`qc_o_1640dev`（执行日常业务、即时审核上下文桥接）
  - 审计员：`qc_o_1640aud`（只读权限核验）
- **证据链目录**：
  - 截图目录：[`docs/evidence/v1.6.4.0-qc2-o/shots/`](./evidence/v1.6.4.0-qc2-o/shots/)
  - API 与网络抓包证据：[`docs/evidence/v1.6.4.0-qc2-o/`](./evidence/v1.6.4.0-qc2-o/)

---

## 3. QC1 遗留缺陷复测闭环核对表

| QC1 编号 | 严重度 | 缺陷描述 | Q 的修复方案 | QC2 复验实测结果 | 判定 |
|---|---|---|---|---|---|
| **QC1-B01** | BLOCKER | 账号切换泄露上一账号私有草稿/会话 | `copilot.js` 增加 `resetForIdentityChange`，登出时清空 sessionStorage 与内存状态 | 管理员在 Copilot 输入草稿 `AdminPrivateDraft_Secret12345`，登出并换登开发者账号，开发者界面完全空白，无任何历史草稿泄露（证据：`qc2-20`、`qc2-21`） | **PASS 闭环** |
| **QC1-B02** | BLOCKER | 实例授权撤销后，历史结果/动作/导出仍可访问 | 服务端增加统一鉴权检查，撤销后即时拒绝 | 撤销 `q40-dist` 授权后，再次访问 `result`、`actions/resolve`、`export.html` 均立即返回 `403 Forbidden`（证据：`api-revocation-run.txt`） | **PASS 闭环** |
| **QC1-B03** | BLOCKER | WebUI 无法进行大模型与路由配置 | 重构 `copilot_admin.js`，增加 Provider、路由、授权、设置抽屉 | 在 WebUI 完整完成了模型新增、密钥配置、自检、10 个场景路由映射、实例授权申请审批，无任何 JS 异常（证据：`qc2-02` ~ `qc2-07`） | **PASS 闭环** |
| **QC1-B04** | BLOCKER | 业务页上下文桥未接入 Copilot 状态 | 实现 `applyBusinessContext`，桥接即时审核选中 SQL 与规则 | 在即时审核页面点击违规规则的“解释/建议”按钮，Copilot 抽屉自动展开并带入对应上下文和待审 SQL（证据：`qc2-22`、`qc2-23`） | **PASS 闭环** |
| **QC1-B05** | BLOCKER | 浏览器会话生命周期错误，模型出站无历史 | 增加历史投影与会话管理 | **未完全修复**。前端会话与 Revision 冲突已缓解，但后端 `_build_history` 因 AAD 解密异常导致出站 `history: []` 恒空，见后文 **QC2-B01** | **FAIL 阻断** |
| **QC1-B06** | BLOCKER | 候选 SQL 未执行 T09 校验且直接覆盖草稿 | 增加 T09 校验与草稿 Diff | **未完全修复**。`_t09_validate` 错写在类外部，导致运行时抛出 `AttributeError` 崩溃，见后文 **QC2-B02** | **FAIL 阻断** |
| **QC1-M01** | MAJOR | 浏览器导出始终 401 | 改用 `apiFetch` 携带 Bearer Token 获取 Blob 下载 | 授权有效时能正常拉取带鉴权凭据的导出文件 | **PASS 闭环** |
| **QC1-M02** | MAJOR | 预览不是完整可核对的冻结投影 | 重构预览展示抽屉，展示完整字段与快照摘要 | 预览卡可核对 Evidence、Knowledge、Token 预估与隐私域 | **PASS 闭环** |
| **QC1-M03** | MAJOR | FAILED 结果弹窗为空 | 终态组件细化渲染各种错误码与指引 | 遇到错误时展示明确的失败原因分类与追踪号 | **PASS 闭环** |
| **QC1-M04** | MAJOR | 自检终态和路由 DTO 未刷新 | 修复轮询更新机制，修正路由字段映射 | 自检完成后状态由 TESTING 变为 SUCCEEDED，`tested_revision` 自动刷新为 1（证据：`api-create-selftest-provider.json`） | **PASS 闭环** |
| **QC1-M05** | MAJOR | 关闭 Copilot 时缺少本地帮助路径 | 增加降级本地帮助查询 | 服务端关闭时提供基础离线指引 | **PASS 闭环** |
| **QC1-m01** | MINOR | 健康端点未就绪时返回 200 而非 503 | 调整未就绪状态返回 HTTP 503 | 未就绪时返回 HTTP 503 且保留结构化诊断信息 | **PASS 闭环** |

---

## 4. 本轮新发现与遗留阻断缺陷深度剖析

### 缺陷 QC2-B01（BLOCKER）：AES-GCM 标签校验失败导致多轮对话历史恒为空

- **缺陷现象**：
  在 WebUI 或 API 连续发起多轮咨询对话（Turn 1 -> Turn 2 -> Turn 3），通过底层受控网关记录的真实 HTTP 请求体抓包显示：
  发送给大模型网关的 JSON 负载中，`history` 字段恒为空：
  ```json
  "content": "{\"question\": \"接着说明它如何选择分布式规则。\", \"history\": [], \"evidence\": [], ...}"
  ```
  大模型在多轮提问中如同面对全新对话，无法理解代词、前情提要或上下文。
- **源码根因分析**：
  定位到 `backend/services/copilot/preview_service.py`：
  ```python
  # preview_service.py:78
  cur = conn.execute(
      sa.text(
          "SELECT t.id, t.created_at, p.payload_cipher, p.preview_sha256, p.key_id "
          "FROM copilot_turns t "
          "JOIN copilot_previews p ON p.turn_id = t.id "
          "WHERE t.session_id = :sid AND t.state = 'SUCCEEDED' AND t.id != :cur_turn_id "
          "ORDER BY t.created_at ASC"
      ),
      {"sid": session_id, "cur_turn_id": current_turn_id},
  )
  for r in cur.fetchall():
      try:
          p_body = json.loads(
              decrypt_preview(
                  payload_cipher=r[2],
                  key_id=r[4],
                  aad=None,  # <--- 致命错误：加密时使用了 preview_id 作为 AAD，这里传入 None！
              )
          )
      except Exception:
          continue  # <--- 抛出 InvalidTag 异常被吞掉，导致本轮历史记录直接被跳过！
  ```
  在 `create_preview()` 中加密时，AAD 是 `preview_id`（即 `p.id`）。但在解密时：
  1. SQL 查询语句中根本没有 SELECT `p.id`（`r[0]` 是 `t.id` 而非 `p.id`）；
  2. 解密参数写死了 `aad=None`；
  3. AES-GCM 规范严格要求解密时的 AAD 与加密时完全一致，否则抛出 `cryptography.exceptions.InvalidTag`；
  4. 外层紧接着使用 `except Exception: continue` 静默吞并异常，导致每一次多轮拼装都将全部历史丢弃，返回空列表 `[]`。
- **整改方案要求**：
  1. 修改 SQL 明确包含 `p.id`：`SELECT p.id, t.id, t.created_at, p.payload_cipher, ...`；
  2. 解密时传入正确的 AAD：`aad=p_id.encode('utf-8')`；
  3. 增加单元测试对多轮历史出站 payload 的端到端断言，禁止静默吞没解密异常。

---

### 缺陷 QC2-B02（BLOCKER）：候选 SQL 触发 `_t09_validate` 属性缺失致命崩溃

- **缺陷现象**：
  当大模型响应中包含 `sql_candidates` 修复候选代码时，后端 Worker 执行器在发布结果阶段立即崩溃，任务状态置为 `FAILED`，错误码 `INTERNAL_ERROR`，错误信息：
  `'TurnExecutor' object has no attribute '_t09_validate'`。
- **源码根因分析**：
  定位到 `backend/services/copilot/workflow.py`：
  ```python
  class TurnExecutor:
      # ...
      def _publish_model(self, ...):
          for cand in ans.get("sql_candidates") or []:
              valid, reason = self._t09_validate(cand.get("sql", "")) # <--- 试图调用实例方法
  
  # 在 TurnExecutor 类定义结束后（约第 553 行）：
  def _t09_validate(candidate_sql: str) -> Tuple[bool, str]: # <--- 错写成模块级全局函数！
      # ...
  ```
  `_t09_validate` 未缩进在 `TurnExecutor` 类内部，属于顶层模块函数。当调用 `self._t09_validate(...)` 时，Python 解释器抛出 `AttributeError`。
  为什么之前 117 项单测全部变绿？
  经全面排查 `tests/copilot/` 发现，所有测试用例打桩的模型返回结果中，`sql_candidates` 均为空列表 `[]`，测试集从未输入过包含候选 SQL 的模型回答，导致这段缺陷代码在单测中从未被执行，造成虚假的测试通过。
- **整改方案要求**：
  1. 将 `_t09_validate` 缩进移入 `TurnExecutor` 类中，作为合法的内部实例方法；
  2. 在 `tests/copilot` 中增加包含非空 `sql_candidates` 的模型响应测试用例，覆盖通过、拦截、超时与非法 SQL 分支。

---

## 5. 自动化测试与基线对照

- **Copilot 聚焦单测**：`tests/copilot` 共 117 项全部 Passed（注：由于用例数据缺陷，未覆盖候选 SQL 分支）。
- **原系统回归测试集**：
  - 排除 `tests/copilot` 的 2176 项用例中，1639 passed、417 failed、89 errors、31 skipped。
  - 失败集合与 Rev.D 冻结基线严格一致（0 差异新增），证明本次修改未对原 SQL 审核核心能力造成次生破坏。
- **离线依赖安全性**：在隔离干净环境中执行 `pip check` 正常，无未满足的依赖项。

---

## 6. 最终验收裁决与后续建议

### 裁决：不通过（REJECT）

虽然 Q 在 WebUI 配置能力、身份切换防泄露、撤权防护等方面做出了卓有成效的重构与修复，解决了第一轮质检中的绝大多数阻塞项，但由于 **多轮对话历史出站丢失（QC2-B01）** 与 **候选 SQL 执行器崩溃（QC2-B02）** 两项硬伤阻断缺陷，本系统在核心 AI 交互与 SQL 修复建议两个关键链路上仍无法满足交付验收标准。

### 下一步行动指南（针对开发人员 Q）

1. **紧急修复 `preview_service.py`**：修正查询与解密参数，使 AES-GCM 成功解密历史快照，确保第二轮对话发送给模型的 `history` 数组包含真实历史上下文。
2. **紧急修复 `workflow.py`**：将 `_t09_validate` 方法归正纳入 `TurnExecutor` 类中。
3. **补充实测用例**：编写包含候选 SQL 的端到端集成测试，并在真实网关中确认多轮会话日志包含历史提问。
4. **提交第三轮质检（QC3）**。
