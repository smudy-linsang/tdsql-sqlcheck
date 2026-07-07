# deploy/ 部署套件说明（v1.0.2）

| 文件 | 用途 | 在哪执行 |
|---|---|---|
| `make_release.sh` | 构建离线发布包（代码+目标架构wheels+可选便携Python） | **打包机**（可访问 pip 源） |
| `install.sh` | **一键部署**（预检→用户/目录→venv离线装依赖→.env→systemd→启动→验证） | 目标服务器（麒麟V10SP3） |
| `preflight_check.sh` | 部署前预检（OS/Python/端口/wheels架构/TDSQL连通/生产开关） | 目标服务器 |
| `verify_deploy.sh` | 部署后一键冒烟（health/版本/登录/119规则/审核命中/元数据库/metrics） | 目标服务器 |
| `rollback.sh` | 一键回滚到上一发布目录 | 目标服务器 |
| `env.template` | 生产配置模板 → 复制为 `deploy/.env` 填写后再执行 install | — |
| `tdsql-sqlcheck.service` | systemd 服务模板（install.sh 自动渲染安装） | — |
| `nginx-sqlcheck.conf` | 可选 Nginx TLS 反代配置 | 目标服务器（可选） |

**标准流程**（详见《docs/部署手册-v1.0.2.md》）：

```bash
# 打包机
./deploy/make_release.sh --arch x86_64        # 或 aarch64；目标机无python3.9+则加 --with-python
# 目标机
sha256sum -c tdsql-sqlcheck-v1.0.2-linux-x86_64.tar.gz.sha256
tar -xzf tdsql-sqlcheck-v1.0.2-linux-x86_64.tar.gz && cd tdsql-sqlcheck-v1.0.2-linux-x86_64
cp deploy/env.template deploy/.env && vi deploy/.env       # 填 TDSQL 元数据库连接、ADMIN 初始口令
sudo ./deploy/install.sh                                    # 一键部署（内含预检与部署后验证）
```
