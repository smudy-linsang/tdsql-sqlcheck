# ============================================================================
# TDSQL SQL审核工具 v1.2.0.5 发布包构建脚本 (Windows PowerShell版)
# 产出: dist/tdsql-sqlcheck-v1.2.0.5-linux-x86_64.tar.gz + .sha256
# ============================================================================
$ErrorActionPreference = "Stop"
$ARCH = "x86_64"
$PYTAG = "311"
$ROOT = Split-Path -Parent $PSScriptRoot
if (-not $ROOT) { $ROOT = (Get-Location).Path }
$VERSION_FILE = Join-Path $ROOT "VERSION"
if (-not (Test-Path -LiteralPath $VERSION_FILE)) { throw "错误: 读不到 $VERSION_FILE，无法确定版本号" }
$VERSION = (Get-Content -LiteralPath $VERSION_FILE -Raw).Trim()
if ([string]::IsNullOrWhiteSpace($VERSION) -or $VERSION -notmatch '^\d+\.\d+\.\d+\.\d+$') {
    throw "错误: VERSION 文件中的版本号无效: '$VERSION'"
}

# 离线环境提示
Write-Host "============================================================================" -ForegroundColor Cyan
Write-Host " 开始打包 TDSQL SQL审核工具 v${VERSION} (x86_64)" -ForegroundColor Cyan
Write-Host "============================================================================" -ForegroundColor Cyan

$DIST_DIR = Join-Path $ROOT "dist"
$STAGE_DIR = Join-Path $DIST_DIR "stage-${ARCH}"
$PKG_NAME = "tdsql-sqlcheck-v${VERSION}-linux-${ARCH}"
$PKG_DIR = Join-Path $STAGE_DIR $PKG_NAME
$TAR_NAME = "${PKG_NAME}.tar.gz"
$TAR_PATH = Join-Path $DIST_DIR $TAR_NAME

# 清理并创建阶段目录
if (Test-Path $PKG_DIR) { Remove-Item -Recurse -Force $PKG_DIR }
New-Item -ItemType Directory -Force -Path $PKG_DIR | Out-Null

# 复制 backend / frontend / requirements.txt
Write-Host "[1/4] 复制源码与配置文件..." -ForegroundColor Green
Copy-Item -Recurse (Join-Path $ROOT "backend") (Join-Path $PKG_DIR "backend")
Copy-Item -Recurse (Join-Path $ROOT "frontend") (Join-Path $PKG_DIR "frontend")
Copy-Item (Join-Path $ROOT "requirements.txt") (Join-Path $PKG_DIR "requirements.txt")

# 复制部署脚本
$DEPLOY_DIR = Join-Path $PKG_DIR "deploy"
New-Item -ItemType Directory -Force -Path $DEPLOY_DIR | Out-Null
Get-ChildItem -Path (Join-Path $ROOT "deploy") -Include "*.sh","*.service","env.template","nginx-sqlcheck.conf","README.md","*.env.example" -Recurse | ForEach-Object {
    Copy-Item $_.FullName (Join-Path $DEPLOY_DIR $_.Name) -Force
}

# 复制文档 (全量复制 docs 目录下所有部署与操作指南)
$DOCS_DIR = Join-Path $PKG_DIR "docs"
New-Item -ItemType Directory -Force -Path $DOCS_DIR | Out-Null
Get-ChildItem -Path (Join-Path $ROOT "docs") -Filter "*.md" | ForEach-Object {
    Copy-Item $_.FullName (Join-Path $DOCS_DIR $_.Name) -Force
}

# VERSION文件
Set-Content -Path (Join-Path $PKG_DIR "VERSION") -Value $VERSION -NoNewline

# 清理 __pycache__
Get-ChildItem -Path $PKG_DIR -Directory -Recurse -Filter "__pycache__" | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

Write-Host "[2/4] 准备目标平台 wheels (manylinux2014_$ARCH, cp$PYTAG)..."
$WHEELS_DIR = Join-Path $PKG_DIR "wheels"
New-Item -ItemType Directory -Force -Path $WHEELS_DIR | Out-Null

$WHEELS_TMP = Join-Path $DIST_DIR "wheels_tmp"
if (Test-Path $WHEELS_TMP) {
    Write-Host "  使用预下载的全量依赖 wheels (dist/wheels_tmp)..."
    Get-ChildItem -Path $WHEELS_TMP -Filter "*.whl" | ForEach-Object {
        Copy-Item $_.FullName (Join-Path $WHEELS_DIR $_.Name) -Force
    }
} else {
    python -m pip download -r (Join-Path $ROOT "requirements.txt") `
        -d $WHEELS_DIR `
        --platform "manylinux2014_$ARCH" --platform "manylinux_2_17_$ARCH" --platform "any" `
        --python-version $PYTAG --implementation cp --abi "cp$PYTAG" --abi "none" --abi "abi3" `
        --only-binary=:all:

    # pip/setuptools/wheel (venv升级用)
    python -m pip download pip setuptools wheel -d $WHEELS_DIR `
        --platform "any" --python-version $PYTAG --only-binary=:all: 2>$null
}

Write-Host "[3/4] 打包为 tar.gz..."
# 使用Python创建tar.gz (Windows无原生tar)
python -c "
import tarfile, os
stage = r'$STAGE_DIR'
pkg = '$PKG_NAME'
dist = r'$DIST_DIR'
out = os.path.join(dist, pkg + '.tar.gz')
with tarfile.open(out, 'w:gz') as tar:
    tar.add(os.path.join(stage, pkg), arcname=pkg)
print(f'  已创建: {out}')
"

Write-Host "[4/4] 生成 SHA256 校验和..."
python -c "
import hashlib, os
dist = r'$DIST_DIR'
pkg = '$PKG_NAME'
tarball = os.path.join(dist, pkg + '.tar.gz')
sha = hashlib.sha256()
with open(tarball, 'rb') as f:
    for chunk in iter(lambda: f.read(8192), b''):
        sha.update(chunk)
digest = sha.hexdigest()
with open(tarball + '.sha256', 'w') as f:
    f.write(f'{digest}  {pkg}.tar.gz\n')
print(f'  SHA256: {digest}')
"

# 清理staging
Remove-Item $STAGE_DIR -Recurse -Force

$tarball = Join-Path $DIST_DIR "$PKG_NAME.tar.gz"
$size_mb = [math]::Round((Get-Item $tarball).Length / 1MB, 2)
Write-Host ""
Write-Host "══════════════════════════════════════════"
Write-Host " 发布包: dist/$PKG_NAME.tar.gz ($size_mb MB)"
Write-Host " 校验和: dist/$PKG_NAME.tar.gz.sha256"
Write-Host ""
Write-Host " 交付部署: 将 dist/ 目录整个拷贝至内网目标机"
Write-Host "          cd dist && sha256sum -c $PKG_NAME.tar.gz.sha256"
Write-Host "          tar -xzf $PKG_NAME.tar.gz"
Write-Host "          cd $PKG_NAME && cp deploy/env.template deploy/.env"
Write-Host "          vi deploy/.env && sudo ./deploy/install.sh"
Write-Host "══════════════════════════════════════════"
