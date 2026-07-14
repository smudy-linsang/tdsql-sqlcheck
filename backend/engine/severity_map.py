"""严重度归一映射（全平台共用）

我方体系只有 ERROR / WARNING / INFO 三级。原厂巡检/比对类工具使用
FATAL/CRITICAL/HIGH/MEDIUM/INFO 等级别，落地时统一经此函数映射，
杜绝把体系外等级透传到报告/看板。
"""

_ERROR = {"FATAL", "CRITICAL", "HIGH"}
_WARNING = {"MEDIUM", "WARNING", "WARN"}


def map_severity(vendor_level: str) -> str:
    """把原厂/外部严重度映射为我方三级 ERROR/WARNING/INFO。"""
    v = (vendor_level or "").upper()
    if v in _ERROR:
        return "ERROR"
    if v in _WARNING:
        return "WARNING"
    return "INFO"
