# v1.6.4.0 AI Copilot 整改要求 · N-03 / N-04（并入新发现的 N-05）

| 项 | 内容 |
|---|---|
| 版本 | v1.6.4.0 / CP-1（不升版号，仍属本版整改） |
| 工作基线 | `b049179` |
| 上游 | `docs/SIT3-v1.6.4.0-AI-Copilot-第三轮SIT测试报告-ClaudeA.md` |
| 决策 | Mr.Linsang 2026-09-15：N-03、N-04 补上，然后准备进 UAT |
| 提出人 | 智能体A（独立评审/测试） |
| 施工方 | Q |

---

## 0. 本文性质与范围

**这份是可直接施工的要求，不是选项清单。** 三条的做法我都已经在 `b049179` 上实测验证过：
参考锁能在现行代码上跑绿（N-03、N-04）或如实报红（N-05），对应的变异也确实能被打红。
没有待定项，Q 照做即可；若有技术异议请直接反驳并给依据，不要因为"没定"停工。

**范围红线：**

- 第三轮已确认整个 v1.6.4.0 的运行期改动面只有 `builder.py` 一个构建期工具文件。
  本次整改**只允许再动 `knowledge.py` 的包目录解析这一处**（N-05），以及新增测试。
- `services` / `api` / `workers` / `schema` / `models` / `main.py` 的其余部分，
  以及 119 条审核规则、元数据审核、网关、门禁等既有核心功能，**一行都不要碰**。
- 不要为了这三条去重构 builder 的写入方式（见 §2.1，我特地考虑过并否掉了）。

**为什么把 N-05 并进来**：N-03 的整改动作本身就是"源文档改了就重建知识包"，
而重建会产生一个新的包目录；如果旧目录没删，运行期会**静默加载旧内容且状态仍是 READY**。
也就是说 N-05 现在是潜伏的，N-03 一落地它就被激活。两条必须同批做，不能拆。

---

## 1. N-03：随包知识包必须是当前 `sources/` 的构建结果

### 1.1 问题

改 `backend/copilot_knowledge/sources/*.md` 而不重建知识包，`tests/copilot/` 111 条全绿
（第三轮变异 X6 存活）。manifest 只记了 `source_id` / `title` / `authority`，**没有记源文件哈希**，
所以没有任何一层能发现随包产物与源文档已经脱节。Copilot 会继续拿旧内容回答。

用户指南、故障条目、TDSQL 语法摘要这几份源文档后续版本一定会改，改完忘记重建是很自然的事。

### 1.2 改法：补一条确定性锁，不改产品代码

构建是**确定性**的，这点我实测过两遍：

- 从 `sources/` 重建到临时目录 → `bundle_id` 与随包完全相同，
  `chunks.jsonl` / `index.json` 的 sha256 逐位相同，manifest 只有
  `approved_by` / `reviewed_at` 不同（这两个本就是构建入参）。
- 把 `sources/*.md` 整体转成 CRLF 再重建 → 结果**仍然完全相同**
  （`read_text()` 默认 universal newlines，读入即归一化）。所以这条锁在 Windows 上不会抖。

因此不需要改 builder、不需要往 manifest 里加源文件哈希，一条测试就够。

### 1.3 参考实现

落位 `tests/copilot/test_knowledge_output.py`，与 `test_shipped_bundle_ready` 放在一起：

```python
def test_shipped_bundle_matches_sources(self, tmp_path):
    """N-03：随包产物必须是当前 sources/ 的构建结果，不得静默漂移。"""
    import hashlib
    from pathlib import Path
    from backend.copilot_knowledge.builder import build
    from backend.services.copilot.knowledge import KnowledgeStore

    sources = Path(__file__).resolve().parents[2] / "backend/copilot_knowledge/sources"
    store = KnowledgeStore()
    assert store.load() == "READY", f"随包知识包未就绪: {store.status_info()}"
    shipped = store._bundle.dir

    rebuilt = build(sources, "contract-check", "2099-01-01T00:00:00Z", out_root=tmp_path)
    assert rebuilt.name == shipped.name, (
        f"sources/ 与随包知识包已漂移：按当前源文档重建得到 {rebuilt.name}，"
        f"随包为 {shipped.name}。请重新构建知识包并由审批人重新签署 "
        f"approved_by / reviewed_at，且删除旧包目录。")
    for name in ("chunks.jsonl", "index.json"):
        a = hashlib.sha256((rebuilt / name).read_bytes()).hexdigest()
        b = hashlib.sha256((shipped / name).read_bytes()).hexdigest()
        assert a == b, f"{name} 与按源文档重建的结果不一致（{a[:16]} vs {b[:16]}）"
```

三条硬性约束：

1. **必须传 `out_root=tmp_path`**，绝不能让测试写进生产知识包目录
   （我第二轮就是因为直接跑 `build()` 覆盖了仓内产物，靠 `git checkout` 才救回来）。
2. **比对对象必须是运行期加载器解析出来的那个包**（`KnowledgeStore().load()` 后的
   `store._bundle.dir`），不要写死 `kb-1.6.4.0-5b84…` 目录名。这样 N-05 的多包歧义
   也会在这条锁上暴露出来。
3. **不得自动重建随包产物来"修复"这条锁**。知识包带 `approved_by` / `reviewed_at`，
   内容是经过人工审批的；自动重建等于绕过审批。锁只许报红并给出操作指引。

`store._bundle.dir` 用的是私有属性，这是我有意为之——`KnowledgeStore` 目前没有公开的
包目录访问器，而为了一条测试去加公开属性属于扩大改动面。测试与产品同仓，可以接受；
如果 Q 更愿意加个 `bundle_dir` 只读属性也行，两种都过。

### 1.4 配套：把重建流程写进部署手册

`build()` 的 CLI 入口（`builder.py:210`）已经可用。请在部署/运维文档里补一段
**「源文档变更后的知识包重建流程」**，至少四步，缺一不可：

1. 改 `backend/copilot_knowledge/sources/*.md`
2. 跑 builder 重建，`--approved-by` 由审批人填写，`--expires-at` 重新设定
3. **`git rm -r` 掉旧的 `kb-<版本>-<旧hash>/` 目录**（重建会产生新目录名，
   旧目录不删就会触发 N-05）
4. 跑 `tests/copilot/`，`test_shipped_bundle_matches_sources` 与
   `test_shipped_bundle_ready` 必须同时为绿

---

## 2. N-04：让换行断言在 POSIX 上也能报红

### 2.1 先说我否掉的方案

第三轮我实测：撤掉 builder 三处的 `newline="\n"`（也就是把 B-01 的真正修复回退掉），
在 Linux 上 111 条全绿（变异 X1-nl 存活）。因为 `newline="\n"` 在 POSIX 上与默认行为等价，
`test_build_outputs_lf_bytes` 这条断言永远不会红。

我考虑过把三处产物落盘改成 `write_bytes(text.encode("utf-8"))`，从 API 层面根除换行翻译。
**否掉了**，理由是：这要动产品代码，而 §0 的范围红线要求把改动面压到最小；
而且加一条平台无关的源码断言就能覆盖同样的回退形态，代价更低。

如果 Q 将来想改 `write_bytes`，下面这条断言两种写法都放行，不会挡路。

### 2.2 改法：一条平台无关的源码断言

用 AST 检查 `builder.py` 里有没有"可能翻译换行"的落盘调用。我实测过：

| 被检代码 | 结果 |
|---|---|
| 现行 `write_text(..., encoding="utf-8", newline="\n")` | 放行（**无误报**） |
| 撤掉 `newline="\n"`（真实回退形态） | **三处全部检出** ✅ |
| 改成 `open(path, "w", encoding="utf-8")` 文本模式写 | **检出** ✅ |
| 改成 `write_bytes(...)` | 放行 |

### 2.3 参考实现

```python
def test_builder_has_no_translating_write(self):
    """N-04：builder 落盘不得走可能翻译换行的文本模式（平台无关断言）。"""
    import ast
    from pathlib import Path

    builder = Path(__file__).resolve().parents[2] / "backend/copilot_knowledge/builder.py"
    problems = []
    for node in ast.walk(ast.parse(builder.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
        kws = {k.arg: k.value for k in node.keywords if k.arg}
        if name == "write_text":
            nl = kws.get("newline")
            if not (isinstance(nl, ast.Constant) and nl.value == "\n"):
                problems.append(f"第{node.lineno}行 write_text 未显式 newline='\\n'")
        elif name == "open":
            mode = None
            if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
                mode = node.args[1].value
            elif isinstance(kws.get("mode"), ast.Constant):
                mode = kws["mode"].value
            mode = str(mode or "r")
            if "w" in mode and "b" not in mode and "newline" not in kws:
                problems.append(f"第{node.lineno}行 open(mode={mode!r}) 文本模式写且未固定换行")
    assert not problems, (
        "builder 存在可能翻译换行的落盘调用（Windows 上会写出 CRLF，导致 manifest "
        "摘要与存仓字节不符——B-01 根因）：" + "；".join(problems))
```

`test_build_outputs_lf_bytes` 原样保留，不要删 —— 它能抓"主动写 CRLF"那一类，
和这条是互补关系。

---

## 3. N-05（本轮新发现）：知识包目录不唯一时必须失败关闭

### 3.1 问题与实测

`backend/services/copilot/knowledge.py` 的 `_resolve_bundle_dir()`：

```python
# 根目录：取唯一/最新一个含 manifest 的子目录
candidates = [d for d in sorted(root.iterdir())
              if d.is_dir() and (d / "manifest.json").exists()]
return candidates[-1] if candidates else None
```

注释写的是"最新一个"，但 `sorted()` 是**按目录名的字典序**，而目录名是
`kb-<版本>-<内容hash>` —— 内容 hash 不含任何时间序。所以 `candidates[-1]`
等于**随机选一个**。

实测（两次，选中的都不是"新"的那个）：

```
旧包目录: kb-1.6.4.0-5b8426f429c6a89a
新包目录: kb-1.6.4.0-1c6dbcc0eb6e84c2
两包共存时运行期实际加载: kb-1.6.4.0-5b8426f429c6a89a   ← 旧包
```

后果是**静默的**：旧包自身完整性没问题，`load()` 返回 READY，
`test_shipped_bundle_ready` 照样绿，但 Copilot 回答用的是过期内容。

### 3.2 影响面（我查过部署脚本，如实说明）

- `deploy/upgrade_incremental.sh` 是 `cp -a backend` 到**全新的 release 目录**，
  旧包留在上一个 release 里，不会并存 —— **正常升级路径不受影响**。
- `deploy/apply_patch.sh` 是硬编码文件清单，压根不碰 `copilot_knowledge`。
- 真正会踩到的是：**开发者本地重建后忘记删旧目录**，以及**现场手工替换知识包**。

所以它今天是潜伏级别。但 N-03 落地之后，"改了源文档就重建"会成为常规动作，
这条就从潜伏变成常踩。**必须与 N-03 同批修。**

### 3.3 改法：失败关闭，不要猜

我定的是**失败关闭**，理由：与本项目 N-10、A 组迁移一贯的姿态一致；
按 `reviewed_at` 挑"最新"是猜（那是运维手填的字符串，可能填错或相同）；
加配置项钉 bundle_id 又多一个配置面。目录里本来就该只有一个包，多了就是运维错误，
应该让人来清，不该让程序替人选。

新增一个 `KNOWLEDGE_BUNDLE_AMBIGUOUS` 状态码，`load()` 返回 `INVALID`。

### 3.4 参考实现（我已在 `b049179` 上实测：111 条原有用例零回归，加上三条新锁共 114 全绿）

`backend/services/copilot/knowledge.py`，`class KnowledgeBundle` 之前加：

```python
class _AmbiguousBundle(Exception):
    """知识包根目录下存在多个包 —— 必须由运维删除旧包，不得由程序猜。"""

    def __init__(self, names: list[str]):
        super().__init__(", ".join(names))
        self.names = names
```

`_resolve_bundle_dir` 改为：

```python
    def _resolve_bundle_dir(root: Path) -> Optional[Path]:
        if (root / "manifest.json").exists():
            return root
        # 根目录下必须只有一个知识包。目录名里的是内容 hash，不含时间序，
        # 多包并存时按名字取“最后一个”等于随机选，会静默加载旧内容（N-05）。
        candidates = [d for d in sorted(root.iterdir())
                      if d.is_dir() and (d / "manifest.json").exists()]
        if len(candidates) > 1:
            raise _AmbiguousBundle([d.name for d in candidates])
        return candidates[0] if candidates else None
```

`load()` 里把调用点包起来（**注意必须是内层 try，否则会被函数末尾那个
`except Exception` 吃掉、退化成 `KNOWLEDGE_BUNDLE_MISSING`**）：

```python
            try:
                bdir = self._resolve_bundle_dir(root)
            except _AmbiguousBundle as amb:
                self._bundle = None
                self._status = STATUS_INVALID
                self._reason = "KNOWLEDGE_BUNDLE_AMBIGUOUS"
                self._error_detail = "知识包根目录存在多个包: " + ", ".join(amb.names)
                logger.error("知识包目录不唯一，拒绝加载: %s", self._error_detail)
                return self._status
            if bdir is None:
                ...
```

### 3.5 参考锁

```python
def test_bundle_dir_is_unambiguous(self, tmp_path, monkeypatch):
    """N-05：知识包根目录下同时存在多个包时必须失败关闭，不得按名字猜。"""
    from pathlib import Path
    from backend.copilot_knowledge.builder import build
    from backend.services.copilot.knowledge import KnowledgeStore

    sources = Path(__file__).resolve().parents[2] / "backend/copilot_knowledge/sources"
    root = tmp_path / "kbroot"
    root.mkdir()
    build(sources, "Mr.Linsang", "2099-01-01T00:00:00Z", out_root=root)
    drifted = tmp_path / "src2"
    drifted.mkdir()
    for f in sorted(sources.glob("*.md")):
        (drifted / f.name).write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
    first = drifted / sorted(p.name for p in sources.glob("*.md"))[0]
    first.write_text(first.read_text(encoding="utf-8") + "\n## 漂移\n\n内容。\n",
                     encoding="utf-8")
    build(drifted, "Mr.Linsang", "2099-01-01T00:00:00Z", out_root=root)
    assert len([d for d in root.iterdir() if (d / "manifest.json").exists()]) == 2

    monkeypatch.setenv("COPILOT_KNOWLEDGE_BUNDLE", str(root))
    store = KnowledgeStore()
    assert store.load() != "READY", (
        f"知识包根目录下有 2 个包却仍加载成功（选中 {store.bundle_id}）")
    assert store.status_info()["reason_code"] == "KNOWLEDGE_BUNDLE_AMBIGUOUS"
```

### 3.6 顺带把那句注释改掉

`# 根目录：取唯一/最新一个含 manifest 的子目录` —— "最新"是不成立的描述，
按上面的改法一并订正。注释写错会误导下一个人。

---

## 4. 提交前必须做的事

沿用第二轮我提、第三轮 Q 已经执行到位的那条规矩，不放松：

1. **在干净检出上**跑一次 `tests/copilot/`，把实际数字贴进开发记录 —— 说"全过"就得是真全过。
2. 三条锁各自做一次变异自证，把红/绿结果贴进开发记录（判据见 §5）。
3. 全量 `tests/` 做一次 base / head 受控对照，报**新增失败数**，不要只报总数。
4. 变异只在隔离副本上做，真实工作区不得留残留（第三轮你做到了，继续保持）。

---

## 5. 我第四轮会这么验（判据提前公开）

### 5.1 三条锁必须跑绿

| 锁 | 落位 | 现行代码上的预期 |
|---|---|---|
| `test_shipped_bundle_matches_sources` | `test_knowledge_output.py` | 绿（我已实测） |
| `test_builder_has_no_translating_write` | `test_knowledge_output.py` | 绿（我已实测，无误报） |
| `test_bundle_dir_is_unambiguous` | `test_knowledge_output.py` | 修 N-05 前红、修后绿（我已实测两态） |

### 5.2 变异必须被杀

| 变异 | 注入 | 判据 |
|---|---|---|
| **X6** | 在任一 `sources/*.md` 末尾追加一节，不重建 | `test_shipped_bundle_matches_sources` 报红，且错误信息里要能看出"重建得到 X、随包是 Y" |
| **X7** | 撤掉 builder 三处 `newline="\n"` | `test_builder_has_no_translating_write` 报红（**在 Linux 上也必须红**，这是本条的全部意义） |
| **X8** | 把 `_resolve_bundle_dir` 的失败关闭改回 `candidates[-1]` | `test_bundle_dir_is_unambiguous` 报红 |
| **X9** | 把 §3.4 的内层 `try` 去掉（让 `_AmbiguousBundle` 落到外层 `except Exception`） | `test_bundle_dir_is_unambiguous` 报红（reason 会退化成 MISSING，断言要能区分出来） |

X9 是专门防"改对了但被外层异常吞掉"的，请务必自测这一条。

### 5.3 防回退重跑（第一至三轮全部通过项）

方案乙逐表隔离 9/9、B-02、M-01 八种投影组合、M-02、M-03、N-10 双向、
RBAC 三角色矩阵、INV-03 零执行面、B 组全灭期间账号生命周期与核心端点、
X1/X2/X3A–D/X4/X5 变异复杀 —— 全部重跑，一条都不能退。

### 5.4 回归

全量 `tests/` base / head 受控对照，**新增失败必须为 0**。

### 5.5 影响面

`services` / `api` / `workers` / `schema` / `models` / `main.py` 的 diff，
本次**只允许出现 `knowledge.py` 的包目录解析这一处**。多出任何一个文件我都会追问。

---

## 6. 关于"准备进 UAT"

第三轮我已经给出"可以进 UAT"的结论，这三条是收尾，不改变那个结论。
第四轮定点复验通过后，UAT 准入材料建议齐这几样：

| 材料 | 出处 | 状态 |
|---|---|---|
| 三轮 SIT 报告 + 第四轮定点复验结论 | 智能体A | 前三轮已归档，第四轮待出 |
| 开发记录（含本轮实跑与变异证据） | Q | 待补 §8 |
| RevD 施工基线冻结记录 | O | 已有 |
| 设计 §10.4 health 只读例外的订正 | O | **待办**，不阻塞 SIT 但 UAT 前应闭合 |
| 知识包重建流程（§1.4） | Q | 待补 |
| Copilot 应急关闭演练结论 | 待定 | 见下 |

### 6.1 UAT 必须覆盖、SIT 测不到的

本版全部 SIT 都在单元/集成层面，以下四类**一次都没真跑过**，必须由 UAT 覆盖：

1. **真实模型接入后，`projection_mode` 与实际出站载荷的一致性抽检** ——
   M-01 三道闸的全部价值在这里兑现。SIT 只验证了判定逻辑，没验证真发出去的是什么。
2. **元数据库连接池与磁盘在 Copilot 并发下的占用** ——
   这是 Copilot 与主产品真正共享的资源。我在设计评审阶段把"共享资源"搞错过一次
   （拿宿主机内存当依据），被 O 纠正，所以这条要实测，不要再推理。
3. **知识包的实际检索质量** —— 本版只验证了产物完整性，没验证答得对不对。
   建议 UAT 备一个小黄金集（每个 authority 各若干题），至少有个基线数。
4. **`deploy/copilot_emergency_disable.sh` 的实机演练** ——
   出事时能不能一键把 Copilot 摘掉而不影响审核主流程，这条必须真跑一次，不能只看脚本语法。

---

**-ClaudeA**
