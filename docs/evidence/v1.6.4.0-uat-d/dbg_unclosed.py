"""根因取证 v5：用栈式 HTML 解析复现浏览器嵌套，定位未闭合祖先（含行号）。

浏览器对 in-DOM 模板先做 HTML 解析：一个未闭合的 <el-dialog> 会把其后所有兄弟
节点吞进去。本脚本按标签栈还原嵌套，找出 .copilot-drawer / [data-testid=copilot-page]
的真正祖先链与起始行号。
"""
import json
import sys
from html.parser import HTMLParser
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
SRC = ROOT / "frontend/index.html"

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
        "meta", "param", "source", "track", "wbr"}


class StackParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.targets = {}
        self.mismatches = []
        self._seen_drawer = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        line = self.getpos()[0]
        if tag in VOID:
            return
        # 记录目标节点被解析时的祖先链
        cls = a.get("class", "")
        tid = a.get("data-testid", "")
        if "copilot-drawer" in cls and "copilot-drawer" not in self.targets:
            self.targets["copilot-drawer"] = list(self.stack)
        if tid == "copilot-page" and "copilot-page" not in self.targets:
            self.targets["copilot-page"] = list(self.stack)
        if tid == "copilot-open" and "copilot-open" not in self.targets:
            self.targets["copilot-open"] = list(self.stack)
        if "copilot-advice" in cls and "copilot-dialog" not in self.targets:
            self.targets["copilot-dialog"] = list(self.stack)
        self.stack.append({"tag": tag, "line": line, "cls": cls[:60],
                           "vmodel": a.get("v-model", ""),
                           "title": a.get("title", "")[:40],
                           "tid": tid})

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        # 向上找匹配
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i]["tag"] == tag:
                if i != len(self.stack) - 1:
                    self.mismatches.append({
                        "at_line": self.getpos()[0], "end_tag": tag,
                        "auto_closed": [
                            s["tag"] + ":" + str(s["line"])
                            for s in self.stack[i + 1:]]})
                del self.stack[i:]
                return
        self.mismatches.append({"at_line": self.getpos()[0], "end_tag": tag,
                                "orphan": True})


def main():
    text = SRC.read_text(encoding="utf-8")
    p = StackParser()
    p.feed(text)
    out = {"targets": {}, "mismatch_count": len(p.mismatches),
           "mismatches_sample": p.mismatches[:25],
           "unclosed_at_eof": [f'{s["tag"]}@{s["line"]}' for s in p.stack]}
    for k, chain in p.targets.items():
        out["targets"][k] = [
            f'{s["tag"]}@{s["line"]}' +
            (f'[{s["tid"]}]' if s["tid"] else '') +
            (f'({s["cls"]})' if s["cls"] else '') +
            (f' v-model={s["vmodel"]}' if s["vmodel"] else '')
            for s in chain]
    (HERE / "dbg_unclosed.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2)[:7000])


if __name__ == "__main__":
    main()
