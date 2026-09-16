"""第四轮受控验证：施加 AAD 修复后，自检链路是否真能走通（tested_revision 被写入）。"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
R1 = HERE.parents[1] / "evidence/v1.6.4.0-uat-d"
ROOT = HERE.parents[2]
for p in (str(HERE), str(R1), str(ROOT)):
    sys.path.insert(0, p)
import _boot  # noqa: F401,E402

from uat_d40_api import Api, save, utc  # noqa: E402
from r4_core import selftest_e2e  # noqa: E402


def main():
    a = Api("uat_d_1640")
    a.relogin_after(2)
    out = {"utc": utc(), "selftest": selftest_e2e(a)}
    save("r4_selftest_after_fix", out)
    s = out["selftest"]
    for k in ("selftest_terminal", "selftest_error", "selftest_outbound_count",
              "provider_after_selftest", "tested_revision_written",
              "enable_succeeded"):
        print(f"{k}: {json.dumps(s.get(k), ensure_ascii=False, default=str)[:200]}")
    for st in s.get("steps", []):
        for k, v in st.items():
            print(f"  {k}: {json.dumps(v, ensure_ascii=False, default=str)[:200]}")
    a.close()


if __name__ == "__main__":
    main()
