# 本轮官方判据（智能体O实时核验）

核验时间：2026-08-28/29（Asia/Shanghai）。

- 腾讯云《TDSQL MySQL版 / 开发指南 / InnoDB 引擎 / 使用限制》，页面最近更新 2026-01-05。
- 原始链接：https://cloud.tencent.com/document/product/557/47511
- DML 小语法限制明确写明：“不支持 LOAD DATA/XML”。
- 因而本轮不要求把 LOAD XML 认作 TDSQL 合法可执行 SQL；恰恰要求 R042 按产品规则继续拒绝它。普通注释内出现单引号，不应改变它的语句类别。
- LOAD 用例全部仅进入审核引擎/审核接口，不连接目标库执行，也不读取 `/tmp/synthetic.xml` 文件。
- 公有云文档不能代替指定内网版本的完整兼容性认证；本轮判据同时依据项目已存在的 R042、前后版本差分和用户原定“不得损伤119条规则”的要求。

## 浏览器安全依据

- W3C CSP Level 3（2026-08-13 Working Draft）§7.8：https://www.w3.org/TR/CSP/#security-inherit-csp
- `srcdoc`、`blob:` 等本地文档会继承来源文档的 CSP；因此 `sandbox="allow-scripts"` 本身不等于其内联脚本获准执行。
- 本轮主页面响应头见 `browser_document_headers.json`，实际点击前后状态见 `gateway_interaction.json`。脚本未执行的机制由这两项与规范共同支持；浏览器日志工具未提供 iframe 的 CSP 报错，不伪造控制台异常。
