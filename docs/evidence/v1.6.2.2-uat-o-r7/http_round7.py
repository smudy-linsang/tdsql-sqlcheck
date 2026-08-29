"""Run the proven HTTP matrix against isolated round-seven names and port 8008."""
from pathlib import Path


HERE = Path(__file__).resolve().parent
source = (HERE.parent / "v1.6.2.2-uat-o-r6" / "http_round6.py").read_text(
    encoding="utf-8")
for old, new in (
    ("sixth-round", "seventh-round"),
    ("round-six", "round-seven"),
    ("8007", "8008"),
    ("r6", "r7"),
    ("R6", "R7"),
):
    source = source.replace(old, new)
exec(compile(source, str(HERE / "http_round7.py"), "exec"), {
    "__name__": "__main__", "__file__": str(HERE / "http_round7.py"),
})
