"""Run the proven round-six fixture builder against isolated round-seven names."""
from pathlib import Path


HERE = Path(__file__).resolve().parent
source = (HERE.parent / "v1.6.2.2-uat-o-r6" / "prepare_browser.py").read_text(
    encoding="utf-8")
for old, new in (
    ("sixth-round", "seventh-round"),
    ("round-six", "round-seven"),
    ("r6", "r7"),
    ("R6", "R7"),
):
    source = source.replace(old, new)
exec(compile(source, str(HERE / "prepare_browser.py"), "exec"), {
    "__name__": "__main__", "__file__": str(HERE / "prepare_browser.py"),
})
