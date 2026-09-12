#!/usr/bin/env python3
"""
TDSQL-SQLCheck 增量更新补丁包跨平台构建脚本 (Python 原生版)
产出: dist/tdsql-sqlcheck-v{VERSION}-patch.tar.gz + .sha256
特点:
  1. 纯 Python 标准库，无 bash/wsl 外部依赖；
  2. 自动清理 __pycache__ / .pyc 等临时文件；
  3. 强制校验并修正 shell 脚本 LF 换行符；
  4. 自动赋予 tar 归档内 .sh 脚本 0o755 执行权限；
  5. 自动生成标准 sha256 校验和文件并验证解压完整性。
"""
import hashlib
import os
import shutil
import sys
import tarfile
from pathlib import Path

# 针对 Windows 控制台编码保护
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT_DIR = Path(__file__).resolve().parent.parent
DIST_DIR = ROOT_DIR / "dist"

def get_version() -> str:
    version_file = ROOT_DIR / "VERSION"
    if not version_file.exists():
        raise FileNotFoundError(f"VERSION file not found at {version_file}")
    ver = version_file.read_text(encoding="utf-8").strip()
    if not ver:
        raise ValueError("VERSION file is empty")
    return ver

def ensure_lf(file_path: Path):
    """确保文本/脚本文件使用 LF 换行符"""
    try:
        content = file_path.read_bytes()
        if b"\r\n" in content:
            new_content = content.replace(b"\r\n", b"\n")
            file_path.write_bytes(new_content)
    except Exception as e:
        print(f"  [WARN] LF 转换跳过 {file_path.name}: {e}")

def copy_tree_filtered(src: Path, dst: Path, ignore_patterns=None):
    if ignore_patterns is None:
        ignore_patterns = shutil.ignore_patterns(
            "__pycache__", "*.pyc", "*.pyo", ".pytest_cache", ".coverage",
            "*.swp", "*~", ".DS_Store", "node_modules", "raw_slowlog_exporter",
            "tdsql-dev-cluster"
        )
    shutil.copytree(src, dst, ignore=ignore_patterns, dirs_exist_ok=True)

def build_patch():
    version = get_version()
    patch_name = f"tdsql-sqlcheck-v{version}-patch"
    stage_dir = DIST_DIR / patch_name
    tar_path = DIST_DIR / f"{patch_name}.tar.gz"
    sha_path = DIST_DIR / f"{patch_name}.tar.gz.sha256"

    print(f"════════ 打包 TDSQL-SQLCheck 增量更新补丁 v{version} ════════")
    print(f"源码根目录: {ROOT_DIR}")
    print(f"产出目录:   {DIST_DIR}")

    # 清理历史 stage 与产物
    if stage_dir.exists():
        shutil.rmtree(stage_dir)
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    stage_dir.mkdir(parents=True, exist_ok=True)

    print("\n[1/4] 收集应用代码、前端资源、部署脚本与操作手册...")
    # 根目录基础文件
    shutil.copy2(ROOT_DIR / "VERSION", stage_dir / "VERSION")
    shutil.copy2(ROOT_DIR / "requirements.txt", stage_dir / "requirements.txt")
    ensure_lf(stage_dir / "VERSION")
    ensure_lf(stage_dir / "requirements.txt")

    # backend
    print("  - 复制 backend/ ...")
    copy_tree_filtered(ROOT_DIR / "backend", stage_dir / "backend")

    # frontend
    print("  - 复制 frontend/ ...")
    copy_tree_filtered(ROOT_DIR / "frontend", stage_dir / "frontend")

    # deploy
    print("  - 复制 deploy/ ...")
    copy_tree_filtered(ROOT_DIR / "deploy", stage_dir / "deploy")

    # docs (按需复制关键部署手册与验收报告)
    docs_dst = stage_dir / "docs"
    docs_dst.mkdir(parents=True, exist_ok=True)
    doc_candidates = [
        f"DEPLOY-v{version}-内网测试环境增量更新部署手册.md",
        f"DEPLOY-v{version}-内网生产环境增量更新部署手册.md",
        f"TEST-PLAN-v{version}-内网智能体大库测试方案与操作指引.md",
        f"TEST-REPORT-v{version}-内网测试环境大库验收报告.md",
        f"CONFIRM-v{version}-内网测试环境验证确认报告.md",
        f"REPORT-v{version}-第二轮独立质检验收报告.md",
        f"REPORT-v{version}-独立质检验收报告.md",
        f"DETAIL-v{version}-在线元数据提取SQL文件命名规则还原详细设计说明书.md",
        f"SIT-v{version}-在线元数据提取SQL文件命名规则还原-SIT测试报告-D.md",
        f"RETEST-v{version}-在线元数据提取SQL文件命名规则还原-SIT复测报告-D.md",
        f"UAT-v{version}-在线元数据提取SQL文件命名规则还原-第一轮测试报告-D.md",
        f"DETAIL-v{version}-内网大库测试问题针对性修复详细设计.md",
        f"DETAIL-v{version}-大库在线元数据审核稳定性修复.md",
        f"DETAIL-v{version}-报告实例标识与分区统计及审核网关修复.md",
        "REPORT-6000表元数据审核失败根因深度排查与解决方案报告.md",
        "PRODUCTION-DEPLOY-ISSUES-v1.6.3.0.md",
        f"GATE-DECISION-v{version}-生产发布门禁签署决议与整改任务书.md",
        f"GATE-v{version}-生产发布三项书面门禁发起.md",
    ]
    for doc_name in doc_candidates:
        src_doc = ROOT_DIR / "docs" / doc_name
        if src_doc.exists():
            shutil.copy2(src_doc, docs_dst / doc_name)
            ensure_lf(docs_dst / doc_name)
            print(f"  - 包含文档: docs/{doc_name}")

    # 规范化 deploy 目录下所有 shell 脚本的换行符
    for root, dirs, files in os.walk(stage_dir / "deploy"):
        for f in files:
            if f.endswith((".sh", ".env", ".service", ".conf", ".sql", ".template")):
                ensure_lf(Path(root) / f)

    print("\n[2/4] 生成 tar.gz 压缩归档并设置 POSIX 执行权限...")
    if tar_path.exists():
        tar_path.unlink()

    def tar_filter(tarinfo: tarfile.TarInfo):
        # 排除 cache
        if "__pycache__" in tarinfo.name or tarinfo.name.endswith((".pyc", ".pyo")):
            return None
        # 设置权限：如果是 shell 脚本或目录，赋予执行权限
        if tarinfo.name.endswith(".sh"):
            tarinfo.mode = 0o755
        elif tarinfo.isdir():
            tarinfo.mode = 0o755
        else:
            tarinfo.mode = 0o644
        # 属主置为 root (方便部署)
        tarinfo.uname = "root"
        tarinfo.gname = "root"
        tarinfo.uid = 0
        tarinfo.gid = 0
        return tarinfo

    with tarfile.open(tar_path, "w:gz", format=tarfile.PAX_FORMAT) as tar:
        tar.add(stage_dir, arcname=patch_name, filter=tar_filter)

    pkg_size = tar_path.stat().st_size
    print(f"  归档已生成: {tar_path} ({pkg_size / 1024 / 1024:.2f} MB, {pkg_size} 字节)")

    print("\n[3/4] 计算 SHA256 哈希校验和...")
    sha = hashlib.sha256()
    with open(tar_path, "rb") as f:
        while chunk := f.read(65536):
            sha.update(chunk)
    digest = sha.hexdigest()
    checksum_content = f"{digest}  {patch_name}.tar.gz\n"
    sha_path.write_text(checksum_content, encoding="utf-8")
    print(f"  SHA256: {digest}")
    print(f"  校验文件: {sha_path}")

    print("\n[4/4] 验证补丁包完整性与关键资产清单...")
    # 验证解开后的核心文件是否存在
    with tarfile.open(tar_path, "r:gz") as tar:
        names = set(tar.getnames())
        required_items = [
            f"{patch_name}/VERSION",
            f"{patch_name}/requirements.txt",
            f"{patch_name}/backend/main.py",
            f"{patch_name}/deploy/upgrade_incremental.sh",
            f"{patch_name}/deploy/verify_deploy.sh",
            f"{patch_name}/docs/DEPLOY-v{version}-内网测试环境增量更新部署手册.md",
        ]
        # 若存在生产部署手册，也纳入强校验
        prod_doc = f"DEPLOY-v{version}-内网生产环境增量更新部署手册.md"
        if (ROOT_DIR / "docs" / prod_doc).exists():
            required_items.append(f"{patch_name}/docs/{prod_doc}")

        missing = [item for item in required_items if item not in names]
        if missing:
            raise RuntimeError(f"补丁包缺少关键文件: {missing}")

        # 检查 shell 脚本权限
        upgrade_info = tar.getmember(f"{patch_name}/deploy/upgrade_incremental.sh")
        if not (upgrade_info.mode & 0o111):
            raise RuntimeError("deploy/upgrade_incremental.sh 缺少可执行权限")

    # 清理 stage 目录
    shutil.rmtree(stage_dir)

    print("\n══════════════════════════════════════════════════════════════════")
    print(f"  [OK] 增量更新补丁包构建成功！")
    print(f"  补丁介质: {tar_path.name} ({pkg_size / 1024 / 1024:.2f} MB)")
    print(f"  校验和:   {digest}")
    print(f"  包内已内置测试环境部署手册: docs/DEPLOY-v{version}-内网测试环境增量更新部署手册.md")
    print("══════════════════════════════════════════════════════════════════")

if __name__ == "__main__":
    try:
        build_patch()
    except Exception as err:
        print(f"\n[ERROR] 打包失败: {err}", file=sys.stderr)
        sys.exit(1)
