# v1.6.4.0 AI Copilot 第三轮质检验收报告（O）

> 质检日期：2026-09-17
> 被测基线：`621c05d42fc9e6f540fc251e4b305c3dca01c576`（Q 针对 QC2 阻断缺陷的修复提交）
> 前序基线：`338f7680f2ec70372dfcb170366a6a9be7be9a03`（QC2 阻断基线）
> 设计基线：Rev.D＋N-10，冻结提交 `4fb1f2e462dd17c036ed69f18b2128128614fdbd`
> 验收角色：设计与独立质检责任人 O；以客观硬证据独立判定

---

## 1. 质检裁决与总体结论

**第三轮质检结论：不通过（QC3 REJECT / NO-GO），当前基线依然不得作为 v1.6.4.0 生产发布。**

开发人员 Q 在提交 `621c05d` 中针对 QC2 提出的两项阻断缺陷进行了整改，并补充了 8 项单元测试（自动化单测增至 125 项且全部通过）。独立质检复验结果如下：
1. **【闭环通过】候选 SQL 校验崩溃缺陷（QC2-B02）彻底修复**：`_t09_validate` 已成功移入 `TurnExecutor` 类内，在包含非空候选 SQL 的真实调用中，执行器正常完成校验，返回 `INCOMPLETE / UNKNOWN` 结构化安全标识，未再抛出 `AttributeError`，轮次状态正常达到 `SUCCEEDED / DONE`。
2. **【继续阻断】多轮交互式对话出站历史依然恒空（`history: []`）（QC3-B01 / 继承 QC2-B01）**：
   - Q 确实修复了 `_build_history` 中解密历史预览快照时的 AAD 传参（传入了 `preview_id` 与 `owner`），使得第一道解密障碍得以打通；
   - **但是，在解析历史回答摘要时，代码写为 `s = ans.get("summary", "")`；而生产真实落库的 `response_envelope` 结构为 `{"answer": {"summary": ...}, ...}`，`summary` 位于二级字典 `answer` 内**！
   - 导致 `s` 提取结果恒为空字符串 `""`，紧接着触发 `if not q or not s: continue`，所有已成功的历史轮次再次被 100% 全部跳过！
   - 真实网关抓包证实：第二轮、第三轮提问出站网络请求体中，**`history` 依然恒为空数组 `[]`**！大模型在真实线路上依然无法获取历史问答，多轮连续追问与指代依然彻底失效。
   - **假绿根因**：Q 编写的回归单测 `test_build_history_returns_nonempty_after_decrypt_fix` 中，手工构造了伪造的顶层 `response = {"summary": "..."}`，绕过了真实执行器生成的二级 `answer.summary` 结构，导致单测变绿但线上全崩。

---

### 用户核心关切问题的明确解答

| 关切问题 | 质检结论 | 详细实测事实与证据 |
|---|---|---|
| **1. 在 AI 配置界面人类是不是能够在 WebUI 界面顺利进行大模型 API 的配置？** | **能（彻底闭环通过）** | **已完全具备人类可用性**。<br>管理员登录后，在 WebUI 纯界面即可完成全部大模型配置闭环，无任何控制台脚本依赖：<br>① 新增/编辑 Provider 抽屉（端点下拉、模型ID、密钥 KEEP/REPLACE、Token配额）；<br>② 10 个场景路由映射抽屉；<br>③ 双管理员授权申请、审批与即时撤销抽屉；<br>④ 全局 Copilot 运行设置抽屉。<br>📸 证据截图：`docs/evidence/v1.6.4.0-qc3-o/shots/qc3-01` ~ `qc3-06`。 |
| **2. 是不是能够通过 API 接口正确的调用大模型？** | **能（彻底闭环通过）** | **QC2-B02 崩溃已修复，全链路畅通**：<br>① 基础大模型调用协议（HTTPS/Bearer/OpenAI-compatible/JSON-Schema）正常交互，自检轮询正常；<br>② 当大模型返回带修复建议的候选 SQL（`SELECT * FROM qc_candidate_missing;`）时，后端受控 T09 校验器正常执行，安全输出：`validation: INCOMPLETE`, `executable: UNKNOWN`, `semantic_equivalence: NOT_PROVEN`，状态终态正常达到 `SUCCEEDED`。<br>已无任何 `INTERNAL_ERROR` 崩溃。 |
| **3. 能不能和 Copilot 助手你一言我一句的交互式对话咨询本项目的相关内容？** | **不能达到设计验收标准（严重阻断 BLOCKER）** | **交互气泡能往复，但模型出站无上下文记忆（BLOCKER）**：<br>① 用户在界面上可以发送多轮消息，但**出站网络抓包证据显示**：无论用户聊到第几轮，发送给大模型的实际出站请求体中，**`history` 字段恒为空数组 `[]`**！<br>② 原因：`_build_history` 提取历史回答摘要时，误将位于 `ans["answer"]["summary"]` 的摘要在顶层读取 `ans.get("summary")`，导致提取值恒为空字符串，历史记录全部被丢弃。<br>③ 实际影响：模型无法理解任何包含“接着”、“它”、“上一条”等指代语境，实质上每一次对话都是孤立的新单轮提问。<br>📋 抓包证据：`docs/evidence/v1.6.4.0-qc3-o/gateway-tail.jsonl` 第 1、2、3 行。 |

---

## 2. 复测环境与执行过程

- **运行环境**：
  - Web UI：`http://127.0.0.1:8026`
  - 本机受控 HTTPS 网关（Mock LLM Gateway）：`https://172.31.240.1:8453`（主模型）、`8454`（备模型）、`8455`（自检模型）
  - 执行器：`copilot-runner`、`metadata-runner` 常驻运行
  - 元数据库：`qc_o_1640_meta`（真实 MySQL 8.0 隔离库，端口 13306）
- **测试用例集**：
  - 聚焦单测：`pytest tests/copilot`（共 125 项全部 Passed）
  - 真实多轮对话端到端实测：[scratch/test_copilot_qc3.py](file:///c:/TDSQL_SQLCHECK/TDSQL-SQLCheck/scratch/test_copilot_qc3.py)
  - 真实网关请求抓包审计：[docs/evidence/v1.6.4.0-qc3-o/gateway-tail.jsonl](file:///c:/TDSQL_SQLCHECK/TDSQL-SQLCheck/docs/evidence/v1.6.4.0-qc3-o/gateway-tail.jsonl)
  - 真实浏览器 UI 录制与截图：[docs/evidence/v1.6.4.0-qc3-o/shots/](file:///c:/TDSQL_SQLCHECK/TDSQL-SQLCheck/docs/evidence/v1.6.4.0-qc3-o/shots/)

---

## 3. 缺陷深度剖析与根因定位

### 缺陷 QC3-B01（BLOCKER）：多轮会话历史出站恒为空（`history: []`）

#### 1. 现场实测网络抓包证据
执行连续三轮咨询对话：
- 第 1 轮：*“本项目的 R003 规则是什么？”*
- 第 2 轮：*“接着解释它与表注释的关系。”*
- 第 3 轮：*“请给出一个符合 R003 的建表 SQL 示例。”*

检查底层网关记录的真实 HTTP 请求包（`docs/evidence/v1.6.4.0-qc3-o/gateway-tail.jsonl`）：
```json
// Wire Request #2 (第2轮出站抓包)
{
  "utc": "2026-09-17T06:56:19.956643+00:00",
  "body": {
    "model": "qc1-synthetic",
    "messages": [
      { "role": "system", "content": "你是TDSQL SQL审核工具的建议助手..." },
      { "role": "user", "content": "{\"question\": \"接着解释它与表注释的关系。\", \"history\": [], \"evidence\": [...]}" }
    ]
  }
}
```
**第 2 轮与第 3 轮的 `history` 均为 `[]`！大模型在第二轮提问时根本拿不到第一轮的问答记录。**

#### 2. 代码级根因定位
定位至 [backend/services/copilot/preview_service.py](file:///c:/TDSQL_SQLCHECK/TDSQL-SQLCheck/backend/services/copilot/preview_service.py#L80-L105)：
```python
    for r in reversed(rows):  # 最旧在前
        try:
            from backend.services.copilot import crypto as crypto_mod
            kr = crypto_mod.load_keyring()
            # 解密 payload 获取 question（这部分 Q 已修复成功）
            payload_raw = crypto_mod.decrypt(
                r["payload_envelope"], "copilot_previews",
                r["preview_id"], "payload_envelope",
                owner=r["owner_subject_id"], keyring=kr)
            q = json.loads(payload_raw).get("question", "")
        except Exception:
            continue
        try:
            ans = json.loads(r["response_envelope"])
            s = ans.get("summary", "")  # ❌ 致命错误：生产 response_envelope 的结构是 ans["answer"]["summary"]！
        except Exception:
            continue
        if not q or not s:
            continue  # ❌ 致命错误：因为 s == ""，此处 100% 触发 continue，跳过了本轮！
        entry = {"question": q[:512], "answer_summary": s[:512]}
        items.append(entry)
```

在真实生产运行中，执行器 `TurnExecutor._publish_model` 与 `_publish_local` 写入的 `response_envelope` 为：
```json
{
  "answer": {
    "schema_version": 1,
    "summary": "已根据本轮证据与要求，给出受控网关返回的合成答案。",
    "findings": [],
    ...
  },
  "claims_rendered": [],
  "sources": [],
  "actions": [],
  "model": {...},
  "usage": {...}
}
```
顶层根本没有 `summary` 键，`summary` 位于 `answer` 字典内部。因此 `ans.get("summary", "")` 恒返回 `""`，进而触发 `if not q or not s: continue`，所有历史记录被逐一丢弃，最终导致返回的 `items` 恒为空数组！

#### 3. 为什么 Q 的回归单测没有测出来？
查阅 Q 编写的回归单测 `tests/copilot/test_qc2_regressions.py:196`：
```python
        # Q 在单测中手工伪造了错误的结构：
        response = {"summary": "R003 要求表必须有主键",
                     "findings": [], "steps": [], "sql_candidates": []}
        conn.execute("INSERT INTO copilot_turns (... response_envelope ...) VALUES (..., ?)",
                     (json.dumps(response), ...))
```
单测中的 mock 数据违背了实际代码运行时的结构，导致单测“假绿”，未能拦截生产缺陷。

---

## 4. 缺陷修复方案（施工级指南）

### 1. 修改 `backend/services/copilot/preview_service.py`（第 94 行）
将摘要提取逻辑改为兼容提取：
```python
        try:
            ans = json.loads(r["response_envelope"])
            # 兼容顶层 summary 与真实运行时的 answer.summary 结构
            s = ans.get("summary", "") or (ans.get("answer") or {}).get("summary", "")
        except Exception:
            continue
```

### 2. 同步修正单元测试 `tests/copilot/test_qc2_regressions.py`（第 196 行）
将 mock 结构与真实执行器统一：
```python
        response = {
            "answer": {
                "summary": "R003 要求表必须有主键",
                "findings": [], "steps": [], "sql_candidates": []
            }
        }
```

---

## 5. 验收总评与后续推进

| 检查项 | 状态 | 评价 |
|---|---|---|
| **WebUI 大模型配置能力** | **PASS** | 人类可在 WebUI 独立完成 Provider、密钥、路由、授权及设置全流程。 |
| **大模型调用与候选 SQL** | **PASS** | `_t09_validate` 归入类内，候选 SQL 校验正常，崩溃已消除。 |
| **多轮交互式对话（上下文记忆）** | **FAIL** | 由于 `response_envelope` 字段层级解析错误，出站 `history: []` 恒空，多轮上下文丧失。 |
| **自动化测试覆盖与真伪性** | **WARN** | 125 项单测全绿，但存在构造数据与运行时脱节导致的“假绿”现象。 |

**最终裁决：QC3 REJECT / NO-GO**。  
请开发人员 Q 按照上述第 4 节的 2 行代码改动完成修复，并于提交后通知开展第四轮（QC4）快速收尾复验。
