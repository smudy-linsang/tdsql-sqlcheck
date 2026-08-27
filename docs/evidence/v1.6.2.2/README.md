# v1.6.2.2 解析恢复链——Rev.P 可执行证据

本目录是
`docs/DESIGN-v1.6.2.2-索引类型误判与唯一索引注释解析崩溃修复详细设计说明书.md`
的唯一可执行证据面。Rev.P 把验证对象明确分成两类：设计态从不可变产品基线机械重建目标；
实施态直接验证当前工作树产品。二者不能混用，也不能用临时重建物冒充已开发产品。

本次 Rev.P 只修改设计文档与本目录证据，尚未修改产品代码。因此 design 模式应通过；
implementation 模式当前应明确返回 `STATUS NOT_IMPLEMENTED` 和退出码 3。产品开发完成后，
implementation 模式才允许变为通过。

## 固定身份

```text
baseline_commit = 03216b788412caa476bba49b9d8524de80919bf4
release_sqlglot = 30.14.0
design_bundle_normalized_sha256 = 3cd8756a327f7c18401fd174ebc19148bc01aea3110faafa12ba312db3914c38
parser_normalized_utf8_sha256 = 185f43fcf835508f3ca0b52094cdf324cea4bb5b050df7fdade2aaed3219af9c
distributed_normalized_utf8_sha256 = 5b1884bf0a08f44f2287375cec9a2e504b80ae80cb0fe4f04aedcf81701ad0f0
requirements_normalized_utf8_sha256 = 36916e67bba0c05eaea18a64c80f63e82412b5233a3b9569a0293838d4c6a073
pyproject_normalized_utf8_sha256 = 60785ef0b35ed49fd29d174530b8a6b380777473a948f0f9306f5be5ac3ec98b
```

bundle 包含以下四个文件，按相对路径字典序计算：

- `backend/engine/parser/parser_legacy.py`
- `backend/engine/rules/distributed.py`
- `requirements.txt`
- `pyproject.toml`

规范化哈希先把 CRLF/CR 统一为 LF，再按 UTF-8 编码。bundle 对每个文件依次输入
`path + NUL + normalized_bytes + NUL` 后计算 SHA256。

## 两条准出命令

```powershell
python docs/evidence/v1.6.2.2/run_all.py --mode design --matrix
python docs/evidence/v1.6.2.2/run_all.py --mode implementation --matrix
```

`design` 模式从固定 commit 读取四个 baseline blob，只消费设计正文列明的 stable-id，写入临时
目录后运行三版 sqlglot manifest；在发布 pin 上还运行冻结专项与 `pytest tests/` 全量，并核对
manifest/codestat 生成区段、依赖 pin 和 bundle 哈希。

`implementation` 模式禁止套用设计补丁。它先比较当前四个产品文件与设计目标 bundle；不一致时
返回码 3。施工后一致时，才对当前产品副本运行与 design 相同的矩阵。

runner 为每个 sqlglot 版本建立隔离 venv 并安装精确版本。需要当前 Python 能创建 venv、环境可安装
指定依赖，并已可用 pytest。输出经过 ASCII 转义，默认 Windows/PowerShell 代码页无需额外设置
`PYTHONUTF8`。

## 退出码与诊断参数

| 状态 | 含义 |
|---|---|
| `0` | 当前模式的全部门禁通过 |
| `1` | 重建、语义、生成区段、哈希、pin、专项或全量任一失败 |
| `3` | implementation 的当前产品 bundle 尚未达到设计目标，状态为 `NOT_IMPLEMENTED` |

- `--keep`：保留临时目录，用于定位失败。
- `--skip-full-tests`：只供快速诊断；使用该参数的结果不得作为准出证据。
- 不加 `--matrix` 只跑发布 pin，供本地定位；正式评审/准出必须带 `--matrix`。

## 文件职责

| 文件 | 职责 |
|---|---|
| `parser_recovery_manifest.py` | 唯一 case manifest；含稳定 cid、SQL、分类、来源与结构化 oracle |
| `test_parser_recovery_manifest.py` | 通用 oracle 执行器；无 cid 特判；异常不得静默跳过 |
| `manifest_doc.py` | 生成 §7.1 唯一表格区段和 cases/suites/assertions/collect 计数；`--update-design` 可机械更新正文 |
| `codestat.py` | 从固定 baseline 与设计目标生成 §3.4 规模/唯一性区段；`--update-design` 可机械更新正文 |
| `rebuild_from_design.py` | 从固定四文件 blob 和 stable-id 动作机械重建设计目标，只写调用者指定目录 |
| `run_all.py` | design/implementation 双模式、三版矩阵、哈希、生成区段、专项与全量统一编排 |
| `README.md` | 固定身份、命令、失败码与阶段状态 |

## 单项复现

```powershell
# 生成 manifest 正文区段
python docs/evidence/v1.6.2.2/manifest_doc.py

# 将 manifest 生成区段机械更新进设计文档
python docs/evidence/v1.6.2.2/manifest_doc.py --update-design docs/DESIGN-v1.6.2.2-索引类型误判与唯一索引注释解析崩溃修复详细设计说明书.md

# codestat 更新需要显式给出固定 baseline parser、设计目标 parser 与设计文档
python docs/evidence/v1.6.2.2/codestat.py <baseline-parser> <target-parser> --update-design docs/DESIGN-v1.6.2.2-索引类型误判与唯一索引注释解析崩溃修复详细设计说明书.md

# 从固定基线重建到显式临时目录（不会修改工作区产品代码）
New-Item -ItemType Directory -Force .tmp-revp-target | Out-Null
python docs/evidence/v1.6.2.2/rebuild_from_design.py .tmp-revp-target
```

## 计数口径

| 口径 | 含义 |
|---|---|
| manifest 用例数 | `len(CASES)`，逐条 SQL 用例 |
| 变异断言数 | 每套变异中正确候选与所有定向变异候选的断言总数 |
| pytest collect 数 | `len(CASES) + len(MUTATION_SUITES) + 1`；模糊测试整体为 1 个 item |

准确数字只认 `manifest_doc.py` 与 `pytest --collect-only -q` 的实际输出，其他章节不得维护副本。
