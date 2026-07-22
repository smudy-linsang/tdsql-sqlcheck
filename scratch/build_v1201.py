"""
Build TDSQL-SQLCheck v1.2.0.1 release tarball
"""
import os
import shutil
import tarfile
import hashlib

ROOT = r"c:\TDSQL_SQLCHECK\TDSQL-SQLCheck"
VERSION = "1.2.0.4"
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

print("[2/4] 复制打包目录文件与离线 wheels 依赖包...")
for item in INCLUDE_ITEMS:
    src = os.path.join(ROOT, item)
    dst = os.path.join(STAGE_DIR, item)
    if os.path.isdir(src):
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".git", ".pytest_cache", "node_modules"))
    elif os.path.isfile(src):
        shutil.copy2(src, dst)

# 下载离线 wheels 依赖包，供纯内网无网环境直接 pip install
wheels_dst = os.path.join(STAGE_DIR, "wheels")
wheels_src = os.path.join(ROOT, "wheels")
os.makedirs(wheels_dst, exist_ok=True)
if os.path.exists(wheels_src) and os.listdir(wheels_src):
    for f in os.listdir(wheels_src):
        shutil.copy2(os.path.join(wheels_src, f), os.path.join(wheels_dst, f))
else:
    print("下载依赖包到 wheels/...")
    import subprocess
    subprocess.run([
        "python", "-m", "pip", "download", "-r", os.path.join(ROOT, "requirements.txt"),
        "-d", wheels_dst, "--only-binary=:all:"
    ], check=False)

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
