#!/usr/bin/env bash
# ============================================================================
# 发布包构建脚本（在有外网/内网 pip 源的打包机上执行，非目标服务器）
# 产出: dist/tdsql-sqlcheck-v1.0.3-linux-<arch>.tar.gz + .sha256
#
# 用法:
#   ./deploy/make_release.sh --arch x86_64            # 为 x86_64 麒麟打包
#   ./deploy/make_release.sh --arch aarch64           # 为 鲲鹏/飞腾 aarch64 打包
#   ./deploy/make_release.sh --arch aarch64 --py 39   # 目标机使用 python3.9
#   加 --with-python 会额外内置便携 CPython（目标机无 python3.9+ 时使用）
# ============================================================================
set -euo pipefail
VERSION="1.0.3"
ARCH="x86_64"; PYTAG="311"; WITH_PYTHON="no"
while [[ $# -gt 0 ]]; do case "$1" in
  --arch) ARCH="$2"; shift 2;;
  --py) PYTAG="${2/./}"; shift 2;;
  --with-python) WITH_PYTHON="yes"; shift;;
  *) echo "未知参数 $1"; exit 1;; esac; done
[[ "$ARCH" == "x86_64" || "$ARCH" == "aarch64" ]] || { echo "--arch 仅支持 x86_64/aarch64"; exit 1; }

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAGE="${ROOT}/dist/stage-${ARCH}"
PKG="tdsql-sqlcheck-v${VERSION}-linux-${ARCH}"
rm -rf "${STAGE}"; mkdir -p "${STAGE}/${PKG}" "${ROOT}/dist"

echo "[1/5] 复制代码与部署脚本"
cp -a "${ROOT}/backend" "${ROOT}/frontend" "${ROOT}/requirements.txt" "${STAGE}/${PKG}/"
mkdir -p "${STAGE}/${PKG}/deploy"
cp -a "${ROOT}/deploy/"*.sh "${ROOT}/deploy/"*.service "${ROOT}/deploy/env.template" \
      "${ROOT}/deploy/nginx-sqlcheck.conf" "${ROOT}/deploy/README.md" "${STAGE}/${PKG}/deploy/" 2>/dev/null || true
# 文档随包（部署/运维/上线清单）
mkdir -p "${STAGE}/${PKG}/docs"
cp -a "${ROOT}/docs/部署手册-v1.0.2.md" "${ROOT}/docs/运维手册-v1.0.2.md" \
      "${ROOT}/docs/上线检查清单-v1.0.2.md" "${ROOT}/docs/发布说明-v1.0.2.md" "${STAGE}/${PKG}/docs/" 2>/dev/null || true
echo "${VERSION}" > "${STAGE}/${PKG}/VERSION"
find "${STAGE}/${PKG}" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true

echo "[2/5] 下载目标平台 wheels (manylinux2014_${ARCH}, cp${PYTAG})"
mkdir -p "${STAGE}/${PKG}/wheels"
python3 -m pip download -r "${ROOT}/requirements.txt" \
  -d "${STAGE}/${PKG}/wheels" \
  --platform "manylinux2014_${ARCH}" --platform "manylinux_2_17_${ARCH}" --platform "any" \
  --python-version "${PYTAG}" --implementation cp --abi "cp${PYTAG}" --abi none --abi abi3 \
  --only-binary=:all:
# pip 自身与构建工具（venv 内升级用）
python3 -m pip download pip setuptools wheel -d "${STAGE}/${PKG}/wheels" \
  --platform any --python-version "${PYTAG}" --only-binary=:all: 2>/dev/null || true

echo "[3/5] 便携 Python: ${WITH_PYTHON}"
if [[ "${WITH_PYTHON}" == "yes" ]]; then
  # python-build-standalone 便携版（indygreg），目标: cpython-3.11 + ${ARCH}
  PBS_TAG="20250115"; PBS_VER="3.11.11"
  case "$ARCH" in
    x86_64)  TRIPLE="x86_64-unknown-linux-gnu";;
    aarch64) TRIPLE="aarch64-unknown-linux-gnu";;
  esac
  URL="https://github.com/indygreg/python-build-standalone/releases/download/${PBS_TAG}/cpython-${PBS_VER}+${PBS_TAG}-${TRIPLE}-install_only.tar.gz"
  echo "  下载 ${URL}"
  curl -fL "${URL}" -o "${STAGE}/python.tar.gz"
  tar -xzf "${STAGE}/python.tar.gz" -C "${STAGE}/${PKG}/"   # 解出 python/ 目录
  rm -f "${STAGE}/python.tar.gz"
fi

echo "[4/5] 打包"
chmod +x "${STAGE}/${PKG}/deploy/"*.sh
tar -czf "${ROOT}/dist/${PKG}.tar.gz" -C "${STAGE}" "${PKG}"

echo "[5/5] 生成校验和"
( cd "${ROOT}/dist" && sha256sum "${PKG}.tar.gz" > "${PKG}.tar.gz.sha256" )
rm -rf "${STAGE}"
echo "══════════════════════════════════════════"
echo " 发布包: dist/${PKG}.tar.gz"
echo " 校验和: dist/${PKG}.tar.gz.sha256"
echo " 交付部署: 拷贝至目标机 → sha256sum -c 校验 → tar -xzf 解压 →"
echo "          编辑 deploy/.env → sudo ./deploy/install.sh"
echo "══════════════════════════════════════════"
