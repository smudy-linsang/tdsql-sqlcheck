# 项目约定

## 称呼

用户统称为 **Mr.Linsang**。所有面向用户的回复、以及我署名产出的文档（评审报告、
测试报告、门禁材料等）中提及本人时，一律使用该称呼，不再使用"林桑"等其他写法。

> 例外：他人已归档的历史文档（如 O 的 UAT 报告、既往汇报材料）保持原貌不改写。

## 智能体身份

本会话在项目中的代号为 **智能体D**，署名产出的文档、汇报一律使用该代号。
历史文档中的 Q / A / O / M / G / Codex / Mavis 等为既往轮次的施工方或测试方，
其署名保持原貌，不改写、不冒用。

## Git 纪律

> 来源：Mr.Linsang 2026-09-10 指示。

1. **开工前先拉取**：每次接到工作，先 `git fetch` 并 `git pull` 拉取 GitHub 上的最新
   代码与文档，确认工作基线不是陈旧的，再动手。
2. **收工后先提交推送**：本轮产生的代码与文档，**先提交 git 并成功推送到 GitHub**，
   然后才向 Mr.Linsang 汇报。不得以"本地已改好"代替交付。
3. **提交范围**：只提交本轮自己产出的文件；他人在途的工作区改动与证据（如 O 正在
   进行的 UAT 证据目录）由产出方自行提交，不代为打包。
4. 远程 `origin = https://github.com/smudy-linsang/tdsql-sqlcheck.git`，主分支 `main`。
   HTTPS 匿名可读；本机以 LocalSystem 身份运行，走 **SSH 部署密钥**推送：
   私钥 `C:\Windows\system32\config\systemprofile\.ssh\id_ed25519_tdsql_sqlcheck`。
   推送前置
   `GIT_SSH_COMMAND="ssh -i <上述私钥> -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=<同目录>\known_hosts"`
   （`known_hosts` 取自 GitHub 官方公布的主机密钥），再执行
   `git -c safe.directory=<仓库路径> push git@github.com:smudy-linsang/tdsql-sqlcheck.git main:main`。
   **不改动仓库 remote 配置**，以免影响本人使用 HTTPS/GCM 推拉。
