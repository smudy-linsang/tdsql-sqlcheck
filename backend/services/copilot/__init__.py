# -*- coding: utf-8 -*-
"""v1.6.4.0 AI Copilot 专家助手服务包（CP-1 受控只读专家助手）。

模块职责（DETAIL §14 工作包划分）：
- errors      CP-W01 错误码闭集
- crypto      CP-W03 AES-GCM 密钥环加密封套
- policy      CP-W03 出站端点策略与部署参数
- authz       CP-W03 四道权限闸、subject 代际身份、授权复核
- knowledge   CP-W04 离线知识包与检索
- redaction   CP-W04 敏感检测与脱敏投影
- evidence    CP-W04 既有记录只读适配器
- tools       CP-W04 内部工具闭集 T01—T09
- providers   CP-W05 OPENAI_COMPAT_CHAT 协议适配
- routing     CP-W05 场景路由与主备
- output      CP-W05 输出校验/引用/动作卡/本地模板
- workflow    CP-W06 场景固定编排
- schema      CP-W02 B组结构验收/迁移维护入口/故障代次
- recovery    CP-W02 B组故障恢复对账
- repository  CP-W02 持久层
- preview_service CP-W07 预览构建
- report      CP-W09 独立 HTML 建议报告
"""
from __future__ import annotations

import logging
import time

logger = logging.getLogger("tdsql.copilot")

#: 本 Web 进程本地的 B组就绪状态（进程默认未就绪，不照抄其他进程）
_LOCAL_READY = False
_LOCAL_REASON = "COPILOT_SCHEMA_UNAVAILABLE"


def copilot_bootstrap_check() -> None:
    """bootstrap 之后的 Copilot 局部启动步骤（DETAIL §10.4 启动顺序第 2 条）。

    · 读验 B组：总预算 10 秒；禁止 DDL、禁止持核心初始化锁/runtime 行锁；
    · 超时/任一表或台账/合同不符只把本进程置为未就绪（UNAVAILABLE），
      释放本模块连接并继续 Web；不影响原功能；
    · 同时加载知识包（失败仅知识能力降级）。
    """
    global _LOCAL_READY, _LOCAL_REASON
    started = time.monotonic()
    _LOCAL_READY = False
    # M-02（SIT 第一轮）：资源参数超界启动即拒绝启用（不静默夹值到运行期）。
    _config_invalid = False
    try:
        from backend.services.copilot.policy import Limits
        _limit_problems = Limits.validate()
        if _limit_problems:
            logger.error("Copilot 部署参数越界，助手不启用: %s", "; ".join(_limit_problems))
            _config_invalid = True
    except Exception:
        pass
    # 关键：bootstrap 验收用独立直连（不入连接池），避免 SET SESSION 等会话级设置
    # 污染池化连接（MAX_EXECUTION_TIME 残留会中断后续业务慢查询——v1.6.4.0 实测踩坑）。
    raw = None
    try:
        from backend.services.copilot import schema as schema_mod
        from backend.services.database import _create_raw_connection, ensure_db
        ensure_db()
        raw = _create_raw_connection()
        import pymysql
        cur = raw.cursor(pymysql.cursors.DictCursor)
        try:
            # 单 DB 操作有界：statement 级 3 秒超时（仅本独立连接生效，不回池）
            try:
                cur.execute("SET SESSION MAX_EXECUTION_TIME = 3000")
            except Exception:
                pass
            # 包装一个最小 execute 适配器（schema_mod 只用到 execute/fetchone/fetchall）
            class _CurConn:
                def execute(self, sql, params=None):
                    cur.execute(sql, params)
                    return cur
                def cursor(self):
                    return cur
            conn = _CurConn()
            ready, reason, _st = schema_mod.evaluate_ready(conn)
            if ready:
                problems = schema_mod.verify_business_schema(conn)
                ready = not problems
                if problems:
                    logger.error("Copilot B组本进程结构验收失败: %s", problems[:5])
            _LOCAL_READY = ready
            _LOCAL_REASON = reason or ("" if ready else "COPILOT_SCHEMA_UNAVAILABLE")
        finally:
            try:
                raw.close()
            except Exception:
                pass
    except Exception as e:
        _LOCAL_READY = False
        _LOCAL_REASON = "COPILOT_SCHEMA_UNAVAILABLE"
        logger.error("Copilot B组启动验收失败（助手降级）: %s", e)
    finally:
        elapsed = time.monotonic() - started
        if elapsed > 10:
            logger.error("Copilot B组启动验收超过 10 秒预算（%.1fs），置未就绪",
                         elapsed)
            _LOCAL_READY = False
    # 知识包加载（失败仅知识降级，不影响上面结构状态）
    try:
        from backend.services.copilot.knowledge import store
        st = store.load()
        if st != "READY":
            logger.warning("Copilot 知识包状态: %s (%s)", st,
                           store.status_info().get("reason_code"))
    except Exception as e:
        logger.error("Copilot 知识包加载异常: %s", e)
    # M-02：配置越界 → 最终置为未就绪（本地帮助仍可用，但不接受模型/任务受理）
    if _config_invalid:
        _LOCAL_READY = False
        _LOCAL_REASON = "COPILOT_DISABLED"
    logger.info("Copilot 启动验收完成: local_ready=%s reason=%s",
                _LOCAL_READY, _LOCAL_REASON)


def local_ready() -> tuple[bool, str]:
    """本 Web 进程的 B组就绪状态（供 capabilities 展示；不做修复）。"""
    return _LOCAL_READY, _LOCAL_REASON
