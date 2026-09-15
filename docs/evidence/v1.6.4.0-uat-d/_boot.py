"""智能体D / v1.6.4.0 UAT：解释器路径引导。

本机 python 位于 C:\\Python314，第三方依赖（httpx/playwright/certifi/cryptography）
装在用户级 site-packages。当执行身份为 SYSTEM（APPDATA 指向 systemprofile）时，
用户级 site-packages 不在 sys.path 上，需显式补入（与 v1.6.3.6 UAT 的
PYTHONPATH 约定一致）。任何入口脚本先 import 本模块即可。
"""
import sys
from pathlib import Path

CANDIDATES = [
    Path(r"C:\Users\linsa\AppData\Roaming\Python\Python314\site-packages"),
    Path.home() / "AppData/Roaming/Python/Python314/site-packages",
]


def ensure():
    for p in CANDIDATES:
        if p.is_dir() and str(p) not in sys.path:
            sys.path.append(str(p))
    return [str(p) for p in CANDIDATES if p.is_dir()]


ensure()
