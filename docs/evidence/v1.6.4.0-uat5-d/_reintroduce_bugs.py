"""临时把第四轮两个根因改回去，验证回归锁会变红（红→绿证据的另一半）。

用完立即 git checkout 还原，产品代码不保留任何改动。
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SELFTEST = ROOT / "backend/services/copilot/selftest.py"
WORKFLOW = ROOT / "backend/services/copilot/workflow.py"


def reintroduce_bugs():
    # ① R4-B01：AAD 行 ID 改回字面量 "selftest"
    t = SELFTEST.read_text(encoding="utf-8")
    t2 = t.replace('        preview_id, "payload_envelope", owner=identity.subject_id,',
                   '        "selftest", "payload_envelope", owner=identity.subject_id,')
    t2 = t2.replace('        preview_id, "model_projection_envelope", owner=identity.subject_id,',
                    '        "selftest", "model_projection_envelope", owner=identity.subject_id,')
    # ② R4-B02：去掉自检轮放行
    t3 = WORKFLOW.read_text(encoding="utf-8")
    t4 = t3.replace(
        """        if not int(p.get("enabled") or 0):
            # §12.6：自检的目的就是"启用前先验证"，此时 provider 必然 enabled=0。
            # 仅对 PROVIDER_SELFTEST 轮放行；普通业务轮仍要求 enabled=1。
            if self.turn.get("turn_kind") != TurnKind.PROVIDER_SELFTEST.value:
                return None
        return p""",
        """        if not int(p.get("enabled") or 0):
            return None
        return p""")
    changed = []
    if t2 != t:
        SELFTEST.write_text(t2, encoding="utf-8")
        changed.append("selftest.py AAD 行 ID → 字面量")
    if t4 != t3:
        WORKFLOW.write_text(t4, encoding="utf-8")
        changed.append("workflow.py 去掉自检轮放行")
    print("已重新引入缺陷：", "；".join(changed) or "（未匹配到，请检查代码是否已变）")


if __name__ == "__main__":
    reintroduce_bugs()
