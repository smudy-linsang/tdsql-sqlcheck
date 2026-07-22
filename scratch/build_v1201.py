"""
Build TDSQL-SQLCheck v1.2.0.1 release tarball
"""
import os
import shutil
import tarfile
import hashlib

ROOT = r"c:\TDSQL_SQLCHECK\TDSQL-SQLCheck"
VERSION = "1.2.0.1"
OUTPUT_TAR = os.path.join(ROOT, "dist", f"tdsql-sqlcheck-v{VERSION}-source.tar.gz")
OUTPUT_ROOT_TAR = os.path.join(ROOT, f"tdsql-sqlcheck-v{VERSION}-source.tar.gz")
STAGE_DIR = os.path.join(ROOT, "dist", f"stage-v{VERSION}")

print(f"[1/4] 清理并创建构建目录: {STAGE_DIR}")
if os.path.exists(STAGE_DIR):
    shutil.rmtree(STAGE_DIR)
os.makedirs(STAGE_DIR, exist_ok=True)
os.makedirs(os.path.join(ROOT, "dist"), exist_ok=True)

# 定义要打入发布包的主目录与文件
INCLUDE_ITEMS = [
    "backend",
    "frontend",
    "frontend_v2",
    "docs",
    "tests",
    "deploy",
    "data",
    "requirements.txt",
    "pyproject.toml",
    "Dockerfile",
    "docker-compose.yml",
    "README.md",
    "VERSION"
]

print("[2/4] 复制打包目录文件...")
for item in INCLUDE_ITEMS:
    src = os.path.join(ROOT, item)
    dst = os.path.join(STAGE_DIR, item)
    if os.path.isdir(src):
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".git", ".pytest_cache", "node_modules"))
    elif os.path.isfile(src):
        shutil.copy2(src, dst)

print("[3/4] 压缩为 tar.gz 离线发布包...")
with tarfile.open(OUTPUT_TAR, "w:gz") as tar:
    tar.add(STAGE_DIR, arcname=f"tdsql-sqlcheck-v{VERSION}")

# 复制一份到根目录供用户下载
shutil.copy2(OUTPUT_TAR, OUTPUT_ROOT_TAR)

print("[4/4] 计算 SHA256 校验码...")
hasher = hashlib.sha256()
with open(OUTPUT_TAR, "rb") as f:
    while chunk := f.read(65536):
        hasher.update(chunk)
sha256_val = hasher.hexdigest()

sha256_file = OUTPUT_TAR + ".sha256"
with open(sha256_file, "w", encoding="utf-8") as f:
    f.write(f"{sha256_val}  tdsql-sqlcheck-v{VERSION}-source.tar.gz\n")

print(f"\n[Build Success] release package built:\n- {OUTPUT_TAR}\n- {sha256_file}\nSHA256: {sha256_val}")
