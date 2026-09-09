# O / v1.6.3.5 / UAT2 证据索引

被测 `701957b7da39c8b5f978d8748389f51155012de7`；执行日期2026-09-09～10。
主报告：`docs/UAT2-v1.6.3.5-在线元数据审核第二轮用户验收报告-O.md`。

## 来源边界

- 02、03、04、05、06 PNG均为真实浏览器截图，不是绘图或接口替身。
- 01是首次runner未启动场景的页面截图，截图时临时toast已经消失；503及中文提示的依据是浏览器观察记录与`verification-summary.json`里的服务访问记录，**不声称01图片显示了toast**。
- `contract-probes.json`：9月9日第一份取证；`contract-probes-final.json`：9月10日收尾取证。job/API部分来自本机MySQL/HTTP；heartbeat、worker分支、deploy变异部分明确是内存mock，不是假装内网真实运行。
- `rescan-before-click.json` / `rescan-after-click.json`：新增第64张合成表后，再次点击前后DB快照。两者相同恰好证明没有新任务/新报告；配合06截图及07:15:02/07:15:44的202/200访问记录。
- `verification-summary.json`：三次全量回归结果、失败/跳过用例名、有限服务访问及runner事件。首两次环境不齐也保留，不只选报最终绿灯。原始JUnit/带完整日志的运行目录留在`data/reports/uat_o_1635_r2/`，不将认证信息写入Git。

## 本机重复执行

从仓库根目录运行；**只允许本机13306与脚本中明确命名的O专用测试库**。不要用于内网/生产，不要改为现有业务库。原8003服务不能为此重启或重置账号。

```powershell
$env:PYTHONIOENCODING='utf-8'
$env:UAT_FIXTURE_PASSWORD='<首次创建本轮专用测试账号时指定的口令>'
python docs/evidence/v1.6.3.5-uat2-o/local_fixture.py setup
# 两个独立终端，使用本地前台命令；自动后台启动时应隐藏窗口并记录PID：
python docs/evidence/v1.6.3.5-uat2-o/local_fixture.py web
python docs/evidence/v1.6.3.5-uat2-o/local_fixture.py runner
```

站点仅绑定`http://127.0.0.1:8005/`，AUTH_ENABLED=true，账号`uat_o_1635_r2`；setup不重置已有账号。数据库密码使用本机既有测试配置或`SQLCHECK_DB_USER/SQLCHECK_DB_PASSWORD`环境变量，禁止提交真实密码。

`setup`不DROP旧数据；本轮当前目标已是64表，重新验证“63→64”时需另行明确隔离的新夹具或据现有数目验证递增，不能抹掉证据或把当前64表仍标为63表容量运行。表名`O-UAT2-63表-本机模拟目标`是首轮夹具名称，不能代替真实count。

```powershell
python docs/evidence/v1.6.3.5-uat2-o/local_fixture.py inspect
python docs/evidence/v1.6.3.5-uat2-o/probe_contracts.py local-new-probes.json
```

probe会调用已退役POST验证410零业务副作用，其余HTTP是读操作；变异替换发生于内存，不改生产脚本。证据脚本输出0不代表所记录的缺陷已经修好，必须看actual及验收断言。

定向测试：

```powershell
$env:SQLCHECK_DB_NAME='uat_o_1635_r2_tests'
python -m pytest tests/test_v1635_r035_streaming.py tests/test_v1635_metadata_jobs.py tests/test_v1635_metadata_artifacts.py tests/test_v1635_delivery_gate.py tests/test_v1635_sit_r1.py tests/test_v1635_deploy_contract.py tests/test_v1635_uat_r1.py tests/test_verify_deploy_contract.py -q
```

全量测试必须先补前置数据，破坏性测试白名单**只能指向O专用回归库**：

```powershell
$env:SQLCHECK_DB_NAME='uat_o_1635_r2_regression'
$env:G14_ALLOW_DESTRUCTIVE_TESTS='1'
$env:G14_TEST_DB_NAME='uat_o_1635_r2_regression'
$env:ADMIN_INITIAL_PASSWORD='<新建隔离回归库的初始化口令>'
python docs/evidence/v1.6.3.5-uat2-o/prepare_regression.py
python -m pytest tests -q --tb=short --junitxml=data/reports/uat_o_1635_r2/full-regression-final.xml
python docs/evidence/v1.6.3.5-uat2-o/collect_results.py
```

不要对已有部署库设置该破坏性测试白名单。无需修改当前应用的登录密码、权限、业务连接。收尾只停止自己启动且命令行已核对的PID，先检查slot空闲及child回收，测试数据保留。
