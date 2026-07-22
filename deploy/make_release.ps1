# ============================================================================
# TDSQL SQL审核工具 v1.2.0.0 发布包构建脚本 (Windows PowerShell版)
# 产出: dist/tdsql-sqlcheck-v1.2.0.0-linux-x86_64.tar.gz + .sha256
# ============================================================================
$ErrorActionPreference = "Stop"
$VERSION = "1.2.0.0"
$ARCH = "x86_64"
$PYTAG = "311"
$ROOT = $PSScriptRoot
if (-not $ROOT) { $ROOT = Split-Path -Parent $MyInvocation.MyCommand.Path }
if (-not $ROOT) { $ROOT = (Get-Location).Path }
if ($ROOT -like '*\deploy') { $ROOT = Split-Path -Parent $ROOT }
if ($ROOT -like '*\deploy\') { $ROOT = Split-Path -Parent $ROOT }

$STAGE = Join-Path $ROOT "dist\stage-$ARCH"
$PKG = "tdsql-sqlcheck-v$VERSION-linux-$ARCH"
$PKG_DIR = Join-Path $STAGE $PKG
$DIST = Join-Path $ROOT "dist"

# 清理旧目录
if (Test-Path $STAGE) { Remove-Item $STAGE -Recurse -Force }
New-Item -ItemType Directory -Force -Path $PKG_DIR | Out-Null
New-Item -ItemType Directory -Force -Path $DIST | Out-Null

Write-Host "[1/4] 复制代码与部署脚本..."
$BACKEND_DIR = Join-Path $PKG_DIR "backend"
$FRONTEND_DIR = Join-Path $PKG_DIR "frontend"
$DEPLOY_DIR = Join-Path $PKG_DIR "deploy"

# 复制核心代码
Copy-Item (Join-Path $ROOT "backend") $PKG_DIR -Recurse
Copy-Item (Join-Path $ROOT "frontend") $PKG_DIR -Recurse
Copy-Item (Join-Path $ROOT "requirements.txt") $PKG_DIR

# 复制部署脚本
New-Item -ItemType Directory -Force -Path $DEPLOY_DIR | Out-Null
$deploy_files = @("install.sh","preflight_check.sh","make_release.sh",
                  "verify_deploy.sh","tdsql-sqlcheck.service","env.template",
                  "nginx-sqlcheck.conf","README.md")
foreach ($f in $deploy_files) {
    $src = Join-Path $ROOT "deploy\$f"
    if (Test-Path $src) { Copy-Item $src (Join-Path $DEPLOY_DIR $f) }
}

# 复制文档
$DOCS_DIR = Join-Path $PKG_DIR "docs"
New-Item -ItemType Directory -Force -Path $DOCS_DIR | Out-Null
$doc_files = @("部署手册-v1.0.2.md","运维手册-v1.0.2.md","上线检查清单-v1.0.2.md",
               "发布说明-v1.0.2.md","V1.0.3变更清单与测试要点.md",
               "V1.0.3.1增量更新部署说明.md", "v1.2.0.0_upgrade_manual.md")
foreach ($f in $doc_files) {
    $src = Join-Path $ROOT "docs\$f"
    if (Test-Path $src) { Copy-Item $src (Join-Path $DOCS_DIR $f) }
}

# VERSION文件
Set-Content -Path (Join-Path $PKG_DIR "VERSION") -Value $VERSION -NoNewline

# 清理 __pycache__
Get-ChildItem -Path $PKG_DIR -Directory -Recurse -Filter "__pycache__" | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

Write-Host "[2/4] 下载目标平台 wheels (manylinux2014_$ARCH, cp$PYTAG)..."
$WHEELS_DIR = Join-Path $PKG_DIR "wheels"
New-Item -ItemType Directory -Force -Path $WHEELS_DIR | Out-Null

python -m pip download -r (Join-Path $ROOT "requirements.txt") `
    -d $WHEELS_DIR `
    --platform "manylinux2014_$ARCH" --platform "manylinux_2_17_$ARCH" --platform "any" `
    --python-version $PYTAG --implementation cp --abi "cp$PYTAG" --abi "none" --abi "abi3" `
    --only-binary=:all:

# pip/setuptools/wheel (venv升级用)
python -m pip download pip setuptools wheel -d $WHEELS_DIR `
    --platform "any" --python-version $PYTAG --only-binary=:all: 2>$null

Write-Host "[3/4] 打包为 tar.gz..."
# 使用Python创建tar.gz (Windows无原生tar)
python -c "
import tarfile, os
stage = r'$STAGE'
pkg = '$PKG'
dist = r'$DIST'
out = os.path.join(dist, pkg + '.tar.gz')
with tarfile.open(out, 'w:gz') as tar:
    tar.add(os.path.join(stage, pkg), arcname=pkg)
print(f'  已创建: {out}')
"

Write-Host "[4/4] 生成 SHA256 校验和..."
python -c "
import hashlib, os
dist = r'$DIST'
pkg = '$PKG'
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
Remove-Item $STAGE -Recurse -Force

$tarball = Join-Path $DIST "$PKG.tar.gz"
$size_mb = [math]::Round((Get-Item $tarball).Length / 1MB, 2)
Write-Host ""
Write-Host "══════════════════════════════════════════"
Write-Host " 发布包: dist/$PKG.tar.gz ($size_mb MB)"
Write-Host " 校验和: dist/$PKG.tar.gz.sha256"
Write-Host ""
Write-Host " 交付部署: 将 dist/ 目录整个拷贝至内网目标机"
Write-Host "          cd dist && sha256sum -c $PKG.tar.gz.sha256"
Write-Host "          tar -xzf $PKG.tar.gz"
Write-Host "          cd $PKG && cp deploy/env.template deploy/.env"
Write-Host "          vi deploy/.env && sudo ./deploy/install.sh"
Write-Host "══════════════════════════════════════════"
