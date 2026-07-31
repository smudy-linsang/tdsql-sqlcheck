"""monitordb 时间窗过滤回归测试（v1.5.2.3）

缘起：Q 在 v1.5.2.2 压测中报告"monitordb 扫描的时间窗过滤在 DIST 实例上
会清空结果"，并归因为"时间戳单位/类型差异"。

复核结论（见 docs/v1.5.2.3_monitordb时间窗问题复核与修复_A.md）：
  · 归因不成立 —— 四种列类型（timestamp/datetime/bigint秒/bigint毫秒）
    的 SQL 生成经验证全部正确；
  · 但代码中确有一条【会静默清空结果】的路径：时间文本解析失败时，
    旧实现把原始字符串塞给数值列比较，MySQL 隐式转成前导整数（2026），
    于是 `timestramp < 2026` 匹配零行。
  · 且时间窗过滤的是【采集时刻】而非执行时刻，空结果与"这段时间没有慢SQL"
    外观完全相同，此前无任何解释。

本文件锁定修复后的行为。
"""
import pathlib
import re

import pytest

from backend.services.tdsql_connector import TDSQLConnectionPool


class _SpyPool(TDSQLConnectionPool):
    """拦截 SQL 生成，不连真库"""

    def __init__(self, ts_type="timestamp", sample=None, main_rows=None, diag_rows=None):
        self._ts_type = ts_type
        self._sample = sample
        self._main_rows = main_rows if main_rows is not None else []
        self._diag_rows = diag_rows
        self.calls = []

        class _Cfg:
            monitor_db = "tdsqlpcloud_monitor"
        self.config = _Cfg()

    def monitor_probe(self):
        return {
            "ok": True,
            "columns": {"db", "checksum", "fingerprint", "query_count",
                        "query_time_avg", "query_time_sum", "timestramp", "user"},
            "col_types": {"timestramp": self._ts_type},
            "error": "",
        }

    def _monitor_execute(self, sql, params=None):
        self.calls.append((sql, params))
        if "SELECT timestramp FROM" in sql:
            return [{"timestramp": self._sample}]
        if "COUNT(*) AS total" in sql:
            return self._diag_rows or []
        return self._main_rows

    def main_sql(self):
        return [c for c in self.calls if "GROUP BY" in c[0]][0]


WINDOW = ("2026-07-29 10:00:00", "2026-07-29 11:00:00")


# ── 时间窗参数正确性 ────────────────────────────────────────────

@pytest.mark.parametrize("ts_type,sample,expect_numeric", [
    ("timestamp", None, False),      # 仓库文档记录的真实类型
    ("datetime", None, False),
    ("bigint", 1785312103, True),    # 秒
    ("bigint", 1785312103495, True),  # 毫秒
])
def test_window_params_match_column_type(ts_type, sample, expect_numeric):
    """四种列类型都要下发类型正确的参数 —— Q 的'单位/类型差异'归因在此被证伪"""
    p = _SpyPool(ts_type, sample)
    p.get_cluster_slow_queries(limit=10, min_time=0.0,
                               time_start=WINDOW[0], time_end=WINDOW[1])
    _, params = p.main_sql()
    ts_params = [x for x in params
                 if isinstance(x, int) and x > 1_000_000_000] or \
                [x for x in params if isinstance(x, str) and x in WINDOW]
    assert ts_params, "时间窗参数未下发"
    if expect_numeric:
        assert all(isinstance(x, int) for x in ts_params)
    else:
        assert all(isinstance(x, str) for x in ts_params)


def test_millisecond_column_multiplies_by_1000():
    p = _SpyPool("bigint", 1785312103495)
    p.get_cluster_slow_queries(limit=10, min_time=0.0,
                               time_start=WINDOW[0], time_end=WINDOW[1])
    nums = [x for x in p.main_sql()[1] if isinstance(x, int) and x > 1_000_000_000]
    assert all(x > 1_000_000_000_000 for x in nums), "毫秒列未乘 1000"


# ── 核心：解析失败不得静默清空结果 ──────────────────────────────

@pytest.mark.parametrize("bad", [
    "2026-07-29 10:00:00.123",   # 带毫秒
    "29/07/2026 10:00:00",       # 非 ISO 排列
    "昨天",
    "",
])
def test_unparseable_time_skips_filter_instead_of_emitting_broken_one(bad):
    """【红线】解析失败必须跳过该侧过滤，绝不能把字符串塞给数值列。

    旧实现走 except 分支 params.append(原始字符串)，MySQL 会把
    '2026-07-29 10:00:00' 隐式转成前导整数 2026，于是
    `timestramp < 2026` 匹配零行 —— 结果被静默清空且不报错。
    宁可不过滤（多取一些，可见），也不能悄悄返回空集（不可见）。
    """
    p = _SpyPool("bigint", 1785312103, main_rows=[{"DIGEST_TEXT": "select 1"}])
    p.get_cluster_slow_queries(limit=10, min_time=0.0,
                               time_start=bad, time_end=WINDOW[1])
    sql, params = p.main_sql()
    assert sql.count("timestramp >=") == 0, "无法解析的起点仍下发了过滤条件"
    # 且绝不能出现字符串型时间参数与数值列比较
    assert not [x for x in params if isinstance(x, str) and "-" in x and ":" in x]


def test_iso_t_format_now_parsed_to_epoch():
    """ISO 带 T 的格式此前会落入 except 分支，现已能正确解析"""
    p = _SpyPool("bigint", 1785312103)
    p.get_cluster_slow_queries(limit=10, min_time=0.0,
                               time_start="2026-07-29T10:00:00",
                               time_end="2026-07-29T11:00:00")
    nums = [x for x in p.main_sql()[1] if isinstance(x, int) and x > 1_000_000_000]
    assert len(nums) == 2 and all(isinstance(x, int) for x in nums)


def test_zero_sample_does_not_break_unit_probe():
    """采样值为 0 时旧写法被判 falsy，本应可用的探测被跳过"""
    p = _SpyPool("bigint", 0)
    p.get_cluster_slow_queries(limit=10, min_time=0.0,
                               time_start=WINDOW[0], time_end=WINDOW[1])
    assert p.main_sql()[1], "样本为 0 时时间窗参数丢失"


# ── 空结果诊断 ──────────────────────────────────────────────────

def test_empty_window_produces_diagnosis():
    """空结果 + 用了时间窗 → 必须给出可执行的解释，说明实际采集时刻范围"""
    p = _SpyPool("timestamp", main_rows=[], diag_rows=[
        {"total": 1240, "min_ts": "2026-07-28 09:00:00",
         "max_ts": "2026-07-29 09:30:00"}])
    rows = p.get_cluster_slow_queries(limit=10, min_time=0.0,
                                      time_start=WINDOW[0], time_end=WINDOW[1])
    assert rows == []
    msg = p._last_window_diagnosis
    assert "2026-07-29 09:30:00" in msg, "未给出实际采集时刻范围"
    assert "采集时刻" in msg, "未说明过滤的是采集时刻而非执行时刻"


def test_empty_table_diagnosis_distinguishes_from_window_miss():
    """库里压根没数据，要说清「与时间窗无关」，不要让人白调窗口"""
    p = _SpyPool("timestamp", main_rows=[], diag_rows=[
        {"total": 0, "min_ts": None, "max_ts": None}])
    p.get_cluster_slow_queries(limit=10, min_time=0.0, database="tdsql_check",
                               time_start=WINDOW[0], time_end=WINDOW[1])
    assert "与所选时间窗无关" in p._last_window_diagnosis


def test_no_diagnosis_when_rows_returned():
    """有结果时不做多余探测"""
    p = _SpyPool("timestamp", main_rows=[{"DIGEST_TEXT": "select 1"}])
    p.get_cluster_slow_queries(limit=10, min_time=0.0,
                               time_start=WINDOW[0], time_end=WINDOW[1])
    assert p._last_window_diagnosis == ""


def test_no_diagnosis_when_no_window_applied():
    """没传时间窗时的空结果是正常的，不该误报成时间窗问题"""
    p = _SpyPool("timestamp", main_rows=[], diag_rows=[
        {"total": 0, "min_ts": None, "max_ts": None}])
    p.get_cluster_slow_queries(limit=10, min_time=0.0)
    assert p._last_window_diagnosis == ""


def test_diagnosis_failure_does_not_break_scan():
    """诊断本身失败不得反过来影响扫描主流程"""
    class _Broken(_SpyPool):
        def _monitor_execute(self, sql, params=None):
            if "COUNT(*) AS total" in sql:
                raise RuntimeError("boom")
            return super()._monitor_execute(sql, params)

    p = _Broken("timestamp", main_rows=[])
    rows = p.get_cluster_slow_queries(limit=10, min_time=0.0,
                                      time_start=WINDOW[0], time_end=WINDOW[1])
    assert rows == []
    assert p._last_window_diagnosis == ""


# ── UI 事前提示（L-04）─────────────────────────────────────────
#
# 空结果诊断是【事后】解释，只在查空时才出现；三种数据源的时间窗语义都不是
# "这段时间执行的慢SQL"，不写在界面上使用者根本无从知道。这里锁定事前提示
# 存在，防止后续重构把它删掉又退回到"看到 0 条就以为没有慢SQL"。

_INDEX_HTML = pathlib.Path(__file__).resolve().parents[1] / "frontend" / "index.html"


def _scan_drawer_html() -> str:
    html = _INDEX_HTML.read_text(encoding="utf-8")
    start = html.index('title="新建扫描任务"')
    end = html.index("</el-drawer>", start)
    return html[start:end]


def test_time_window_has_per_source_note():
    """三种数据源各有一条常驻说明，且互斥渲染（v-if/v-else-if/v-else）"""
    drawer = _scan_drawer_html()
    assert "form-note" in drawer, "时间窗口缺少常驻说明"
    note = drawer[drawer.index("form-note"):drawer.index("</el-form-item>", drawer.index("form-note"))]
    assert "采集时刻" in note, "monitordb 未说明过滤的是采集时刻"
    assert re.search(r"v-if=\"scanTaskForm\.source==='monitordb'\"", note)
    assert re.search(r"v-else-if=\"scanTaskForm\.source==='digest'\"", note)
    assert "v-else>" in note, "processlist 分支缺失（三条说明会同时显示）"


def test_time_window_tooltip_covers_all_three_sources():
    """tooltip 详解必须三种源都讲到，且点明各自真正过滤的东西"""
    drawer = _scan_drawer_html()
    assert "tip-block" in drawer, "时间窗口缺少 tooltip 详解"
    tip = drawer[drawer.index("tip-block"):drawer.index("</el-tooltip>", drawer.index("tip-block"))]
    for kw in ("monitordb", "digest", "processlist",
               "采集入库的时刻",      # monitordb：采集时刻而非执行时刻
               "不参与查询过滤",      # digest：窗口只是任务元数据
               "轮询时长"):           # processlist：范围由轮询参数决定
        assert kw in tip, f"tooltip 缺少关键说明: {kw}"
