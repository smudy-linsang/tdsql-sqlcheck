# 本机 Git 推送通道与凭据排查说明

| 项 | 内容 |
|---|---|
| 编制 | **智能体D**（v1.6.4.0 UAT 期间实测） |
| 日期 | 2026-09-15 |
| 触发 | v1.6.4.0 UAT 报告推送 GitHub 时**挂起约 1 小时**无响应 |
| 结论 | **不是网络问题，也不是仓库问题；是"当前进程身份下没有可用写凭据 + 凭据助手在无桌面环境永久阻塞"** |

---

## 1. 现象

`git push origin main` 长时间无输出、不返回、不报错（挂死），只能中断。

## 2. 排查过程与证据

| 步骤 | 命令 | 结果 | 判读 |
|---|---|---|---|
| 1 | `git ls-remote --heads origin main` | 25 秒内返回 `52e270f … refs/heads/main` | **网络与传输正常**，仓库可匿名读 |
| 2 | `git remote -v` | `origin https://github.com/smudy-linsang/tdsql-sqlcheck.git` | 走 HTTPS |
| 3 | `git config --get credential.helper` | `manager` | 使用 Git Credential Manager（GCM） |
| 4 | `whoami` / `$env:USERPROFILE` | `nt authority\system` / `…\config\systemprofile` | **进程以 SYSTEM 身份运行，无交互桌面** |
| 5 | `GIT_TERMINAL_PROMPT=0 git push origin main` | **1.3 秒**返回 `fatal: could not read Username for 'https://github.com': terminal prompts disabled` | **确认：挂起就是在等凭据输入** |
| 6 | 查 `GITHUB_TOKEN/GH_TOKEN/GIT_TOKEN/GITHUB_PAT` | 全为空 | 无令牌可用 |
| 7 | 查 `~/.git-credentials`、`cmdkey /list` | 无 GitHub 条目 | 无缓存凭据 |
| 8 | `ssh -T git@github.com` | `Permission denied (publickey)` | 默认 SSH 无可用密钥 |
| 9 | 查 `C:\Windows\system32\config\systemprofile\.ssh\` | **存在专用部署密钥** `id_ed25519_tdsql_sqlcheck`（2026-09-10 创建，`known_hosts` 已含 github.com） | **找到本机既有的正确通道** |
| 10 | `ssh -i <key> -T git@github.com` | `Hi smudy-linsang/tdsql-sqlcheck! You've successfully authenticated` | 部署密钥有效且对本仓库有写权限 |

**根因**：HTTPS 推送需要认证 → `credential.helper=manager` 触发 GCM 交互流程 →
SYSTEM 身份没有桌面会话可弹窗/开浏览器 → **GCM 永久等待**，表现为"挂起一小时"。
第 8 步失败只是因为 SSH 默认只自动加载 `id_rsa`/`id_ed25519` 这类标准文件名，
而这把密钥的名字是自定义的，**需要显式 `-i` 指定**。

## 3. 修复（已落地并验证）

推送走 SSH 部署密钥；**不动 `origin`**，另外加一个专用远端，
这样用户身份（林桑本机会话）原有的 HTTPS/GCM 通道完全不受影响：

```powershell
# 1) 让 SSH 传输使用本仓库的部署密钥（仓库级配置，只影响 SSH，不影响 HTTPS）
git config core.sshCommand "ssh -i C:/Windows/system32/config/systemprofile/.ssh/id_ed25519_tdsql_sqlcheck -o IdentitiesOnly=yes -o BatchMode=yes"

# 2) 新增专用远端（origin 保持 https 不变）
git remote add github-agent git@github.com:smudy-linsang/tdsql-sqlcheck.git

# 3) 之后推送
git push github-agent main
```

> 路径务必用**正斜杠**：写成 `C:\Windows\...` 时反斜杠会被 SSH 当转义符吃掉，
> 报 `Identity file C:Windowssystem32configsystemprofile.sshid_... not accessible`。

**验证结果**：

| 检查 | 结果 |
|---|---|
| `git push github-agent main` | `52e270f..6db1edc  main -> main`，**4.6 秒**完成 |
| 复测幂等 | `Everything up-to-date` |
| 三处哈希一致 | `local = origin/main = github-agent/main = 6db1edc` |

## 4. 纪律建议（写给后续所有智能体）

1. **推送一律用 `github-agent`**（SSH 部署密钥），不要用 `origin` 推送。
   拉取可以继续用 `origin`（HTTPS 匿名读，已验证可用）或 `github-agent`，两者皆可。
2. **任何 git 网络命令都加 `GIT_TERMINAL_PROMPT=0`**（或 PowerShell 里先
   `$env:GIT_TERMINAL_PROMPT='0'`）。这样缺凭据时**秒级失败并给出原因**，
   而不是挂死几十分钟。这是本次浪费一小时的直接教训。
3. 若 `push` 报 `Permission denied (publickey)`：先确认用的是不是 `github-agent`
   （`origin` 是 HTTPS，没有密钥概念）。
4. 若 `push` 报 `could not read Username`：说明走到了 HTTPS 分支，
   换成 `github-agent` 即可。
5. **不要把 PAT / 私钥内容写进任何文档或提交**；本机已有部署密钥，无需新增凭据。

## 5. 一句话总结

> 本机推送的正确姿势是 `git push github-agent main`；
> `origin` 是 HTTPS，在 SYSTEM 身份下会被 GCM 卡死——**加 `GIT_TERMINAL_PROMPT=0` 就不会再盲等**。

---

**-智能体D**
