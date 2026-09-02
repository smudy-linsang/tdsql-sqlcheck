# -*- coding: utf-8 -*-
"""G14 · 表类型统计 API（DESIGN-v1.6.3.0 Rev.P §5）

Rev.G（O 评审整改）：
  · P1-02  /run 由 service 层进入 registry.scan_slot(connection_id)，
           本层只负责把 ScanBusyError 映射为 429（与 tdsql_manage.py:432 同口径）。
  · P1-08  SchemaNotReadyError 单独映射，把可执行的处置提示原样带给用户，
           不被兜底 except 吞成一句无信息的 500。
  · P2-02  接收 Request 并把 request.state.username 传给 run_stats(operator=)，
           否则 created_by 在真实调用中永远为空，REQ-6 的"可回看"缺了操作人。
  · P2-03  connection_id 必须显式非空：空串/缺字段由模型 `min_length=1` 在进入路由前
           拦截（422）；**全空白由本层在连接解析之前拦截（400，Rev.N / DEF-SIT-03）**；
           service 层保留同名守卫作为服务被直接调用时的兜底。
  · Rev.K  TimeoutError（采集预算耗尽）单独映射为 503——它是"稍后重试可能成功"
           的暂时性状况，与 500 的"结构不对，重试也没用"语义不同。
  · Rev.P  UAT-O-G14-04：`_pool()` 连接解析纳入完整异常边界——离线/超时/认证
           失败经 translate_db_error 严格白名单映射为可读 422；未知程序异常
           原样抛出走 500，绝不伪装成连接失败（口径同 daily_inspect.py O-19/O-24）。
"""
import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from backend.services import table_type_stats_service as svc
from backend.services.connection_errors import (
    AuthenticationFailedError, ConnectionRefusedError_, DatabaseNotFoundError,
    InstanceConnectionError, translate_db_error,
)
from backend.services.connection_registry import (
    registry, ConnectionNotFoundError, ScanBusyError)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/table-type-stats", tags=["表类型统计"])


class StatsRequest(BaseModel):
    connection_id: str = Field(..., min_length=1, description="目标连接ID（必填）")
    database: str = Field("", description="仅统计指定库；空则全部业务库")


def _operator(request: Request) -> str:
    return getattr(request.state, "username", "anonymous")


def _pool(cid):
    try:
        return registry.get(cid)
    except ConnectionNotFoundError:
        raise HTTPException(status_code=400, detail="未连接TDSQL实例或连接不存在")


@router.post("/run", summary="发起表类型统计")
def run(body: StatsRequest, http_request: Request):
    # DEF-SIT-03：入参口径校验必须先于连接解析。否则 registry.get("   ") 会先抛
    # ConnectionNotFoundError，用户输入空白却被告知"未连接TDSQL实例"，排查方向跑偏；
    # 服务层同名守卫也因此在 HTTP 路径上永远不可达（单测直调服务层，测不出来）。
    if not body.connection_id.strip():
        raise HTTPException(
            status_code=400,
            detail="必须指定 connection_id（本模块不接受默认连接："
                   "连接解析与实例类型解析在空 ID 下可能指向不同实例）")
    try:
        try:
            pool = _pool(body.connection_id)
        except HTTPException:
            raise
        except Exception as conn_exc:
            # UAT-O-G14-04：registry.get 对已保存但当前离线的实例会抛底层连接
            # 异常（如 pymysql OperationalError 2003），此前绕过本函数 except
            # 穿透为裸 500。严格白名单映射；未知程序异常原样抛出走 500，
            # 绝不伪装成"实例连接失败"掩盖代码缺陷。
            mapped = translate_db_error(conn_exc)
            if mapped is None:
                raise
            raise mapped
        return svc.run_stats(pool, connection_id=body.connection_id,
                             database=body.database,
                             operator=_operator(http_request))
    except HTTPException:
        raise
    except ScanBusyError as e:
        # 并发超限：与既有慢查询扫描共用同一份配额，口径与 tdsql_manage.py:432 一致
        raise HTTPException(status_code=429, detail=str(e))
    except TimeoutError as e:
        # 采集总时长预算耗尽：暂时性，稍后重试或缩小 database 范围即可
        raise HTTPException(status_code=503, detail=str(e))
    except svc.SchemaNotReadyError as e:
        # 留档表结构不符：消息里已带可执行处置步骤，原样透出
        raise HTTPException(status_code=500, detail=str(e))
    except ConnectionRefusedError_ as e:
        raise HTTPException(status_code=422, detail="实例连接失败：请检查地址、端口、网络和账号；本次未产生统计结果")
    except AuthenticationFailedError as e:
        raise HTTPException(status_code=422, detail="实例认证失败（请核对连接的用户名/口令）；本次未产生统计结果")
    except DatabaseNotFoundError as e:
        raise HTTPException(status_code=422, detail="目标数据库不存在；本次未产生统计结果")
    except InstanceConnectionError as e:
        raise HTTPException(status_code=422, detail="实例连接失败：请检查地址、端口、网络和账号；本次未产生统计结果")
    except ValueError as e:
        # 入参口径错误（系统库 / 空 connection_id / 指定库不存在）——回 400 而非 500
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        # 未知程序错误仍是 500 并记录完整堆栈，响应不泄漏主机/口令/SQL 细节，
        # 以 X-Request-ID 关联日志（中间件下发）。
        logger.exception("表类型统计发生未预期异常")
        raise HTTPException(status_code=500,
                            detail="表类型统计内部错误，请携带 X-Request-ID 联系管理员排查")


@router.get("/history", summary="表类型统计历史")
def history(connection_id: str = "", limit: int = 20):
    return {"items": svc.list_history(connection_id, limit)}


@router.get("/detail/{stat_id}", summary="表类型统计明细")
def detail(stat_id: int):
    return svc.get_detail(stat_id)
