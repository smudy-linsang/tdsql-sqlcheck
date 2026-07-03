"""
TDSQL SQL审核工具 - 指标服务 (V2.0)

进程内轻量指标收集，Prometheus 文本格式暴露（无第三方依赖）。

指标:
- tdsql_http_requests_total{method,path,status}     HTTP请求计数
- tdsql_http_request_duration_seconds_{sum,count}   请求耗时
- tdsql_audit_sql_total                             SQL审核次数
- tdsql_violations_total{severity}                  违规计数
- tdsql_scan_tasks_total{status}                    扫描任务计数
- tdsql_login_total{result}                         登录计数
- tdsql_active_connections                          活跃TDSQL连接数
- tdsql_app_info{version}                           版本信息
"""
import threading
import time
from collections import defaultdict

from backend import config

_lock = threading.Lock()
_counters: dict[tuple[str, tuple], float] = defaultdict(float)
_start_time = time.time()

# 路径归组：避免高基数标签（/api/v1/slow-queries/123 → /api/v1/slow-queries/{id}）
_PATH_GROUPS = [
    "/api/v1/audit", "/api/v1/slow-queries", "/api/v1/dashboard",
    "/api/v1/gitlab", "/api/v1/tdsql", "/api/v1/rules", "/api/v1/rulesets",
    "/api/v1/projects", "/api/v1/bigtable", "/api/v1/gate", "/api/v1/monitor",
    "/api/v1/inspection", "/api/v1/auth", "/api/v1/admin",
    "/health", "/metrics", "/static", "/docs",
]


def group_path(path: str) -> str:
    """路径归组，控制标签基数"""
    for prefix in _PATH_GROUPS:
        if path == prefix or path.startswith(prefix + "/") or path.startswith(prefix + "?"):
            return prefix
    return "other"


def inc(name: str, labels: dict = None, value: float = 1.0):
    """计数器自增"""
    key = (name, tuple(sorted((labels or {}).items())))
    with _lock:
        _counters[key] += value


def observe_request(method: str, path: str, status: int, duration_seconds: float):
    """记录一次HTTP请求"""
    g = group_path(path)
    inc("tdsql_http_requests_total",
        {"method": method, "path": g, "status": str(status)})
    inc("tdsql_http_request_duration_seconds_sum", {"path": g}, duration_seconds)
    inc("tdsql_http_request_duration_seconds_count", {"path": g})


def render_prometheus() -> str:
    """渲染 Prometheus 文本格式"""
    lines = [
        "# HELP tdsql_app_info 应用版本信息",
        "# TYPE tdsql_app_info gauge",
        f'tdsql_app_info{{version="{config.APP_VERSION}"}} 1',
        "# HELP tdsql_uptime_seconds 进程运行时长",
        "# TYPE tdsql_uptime_seconds gauge",
        f"tdsql_uptime_seconds {time.time() - _start_time:.1f}",
    ]
    # 活跃连接数（延迟导入避免循环依赖）
    try:
        from backend.services.connection_registry import registry
        lines.append("# HELP tdsql_active_connections 活跃TDSQL连接实例数")
        lines.append("# TYPE tdsql_active_connections gauge")
        lines.append(f"tdsql_active_connections {registry.active_count()}")
    except Exception:
        pass

    with _lock:
        snapshot = dict(_counters)
    seen_names = set()
    for (name, labels), value in sorted(snapshot.items()):
        if name not in seen_names:
            lines.append(f"# TYPE {name} counter")
            seen_names.add(name)
        if labels:
            label_str = ",".join(f'{k}="{v}"' for k, v in labels)
            lines.append(f"{name}{{{label_str}}} {value:g}")
        else:
            lines.append(f"{name} {value:g}")
    return "\n".join(lines) + "\n"


def reset():
    """清空指标（测试用）"""
    with _lock:
        _counters.clear()
