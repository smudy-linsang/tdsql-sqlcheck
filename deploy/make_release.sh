#!/usr/bin/env bash
# ============================================================================
# 发布包构建脚本（在有外网/内网 pip 源的打包机上执行，非目标服务器）
# 产出: dist/tdsql-sqlcheck-v1.0.3.4-linux-<arch>.tar.gz + .sha256
#
# 用法:
#   ./deploy/make_release.sh --arch x86_64            # 为 x86_64 麒麟打包
#   ./deploy/make_release.sh --arch aarch64           # 为 鲲鹏/飞腾 aarch64 打包
#   ./deploy/make_release.sh --arch aarch64 --py 39   # 目标机使用 python3.9
#   加 --with-python 会额外内置便携 CPython（目标机无 python3.9+ 时使用）
# ============================================================================
# 版本号从仓库根 VERSION 文件读取，不得再硬编码。
# 曾硬编码为 1.4.0.1 而产品已到 1.5.2.4，包内 VERSION 被覆盖成旧值，
# verify_deploy.sh 读该文件与 /health 实报版本比对不上，部署最后一步 exit 1。
set -euo pipefail

_REL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="$(tr -d ' \r\n' < "${_REL_ROOT}/VERSION" 2>/dev/null)"
[[ -n "${VERSION}" ]] || { echo "错误: 读不到 ${_REL_ROOT}/VERSION，无法确定版本号"; exit 1; }
ARCH="x86_64"; PYTAG="311"; WITH_PYTHON="no"; PY_EXPLICIT="no"
# 内置便携 Python 的版本（python-build-standalone），wheels 的 ABI 必须与它一致
BUNDLED_PY_VER="3.11.11"; BUNDLED_PYTAG="311"
while [[ $# -gt 0 ]]; do case "$1" in
  --arch) ARCH="$2"; shift 2;;
  --py) PYTAG="$2"; PY_EXPLICIT="yes"; shift 2;;
  --version) VERSION="$2"; shift 2;;
  --with-python) WITH_PYTHON="yes"; shift;;
  *) shift;;
esac; done
[[ "$PYTAG" =~ ^3[0-9]{1,2}$ ]] || { echo "--py 仅支持 39/310/311 等形如 3NN 的值，收到: ${PYTAG}"; exit 1; }
# --with-python 时目标机跑的就是内置的这个解释器，wheels 必须按它的 ABI 下载。
# 否则会出现「下 cp39 的轮子、却内置 3.11 运行时」，目标机离线装依赖必因 ABI
# 不匹配失败，而打包阶段毫无提示。二者不一致时直接拒绝，不做静默纠正。
if [[ "$WITH_PYTHON" == "yes" && "$PY_EXPLICIT" == "yes" && "$PYTAG" != "$BUNDLED_PYTAG" ]]; then
  echo "错误: --with-python 会内置 CPython ${BUNDLED_PY_VER}（cp${BUNDLED_PYTAG}），"
  echo "      与 --py ${PYTAG} 冲突——目标机将用内置解释器运行，cp${PYTAG} 的 wheels 装不上。"
  echo "      请二选一: 去掉 --py（按 cp${BUNDLED_PYTAG} 打包），或去掉 --with-python（目标机自备 python3.${PYTAG#3}）。"
  exit 1
fi
[[ "$ARCH" == "x86_64" || "$ARCH" == "aarch64" ]] || { echo "--arch 仅支持 x86_64/aarch64"; exit 1; }
case "$ARCH" in
  x86_64) EXPORTER_GOARCH="amd64";;
  aarch64) EXPORTER_GOARCH="arm64";;
esac
[[ "$WITH_PYTHON" == "yes" ]] && { echo "警告: 内置 CPython 将大幅增加发布包体积"; }

echo "════ 打包 TDSQL SQL审核工具 v${VERSION} (${ARCH}) ════"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAGE="${ROOT}/dist/stage-${ARCH}"
PKG="tdsql-sqlcheck-v${VERSION}-linux-${ARCH}"

rm -rf "${STAGE}/${PKG}"
mkdir -p "${STAGE}/${PKG}/deploy" "${STAGE}/${PKG}/docs"

echo "[1/5] 复制代码与部署脚本..."
cp -a "${ROOT}/backend" "${STAGE}/${PKG}/"
cp -a "${ROOT}/frontend" "${STAGE}/${PKG}/"
cp -a "${ROOT}/requirements.txt" "${STAGE}/${PKG}/"
# 复制部署脚本与所有交付文档
cp -a "${ROOT}/deploy/"*.sh "${STAGE}/${PKG}/deploy/" 2>/dev/null || true
cp -a "${ROOT}/deploy/"*.service "${ROOT}/deploy/env.template" \
      "${ROOT}/deploy/nginx-sqlcheck.conf" "${ROOT}/deploy/README.md" "${STAGE}/${PKG}/deploy/" 2>/dev/null || true

# 文档随包（部署/运维/上线清单/全量更新说明）
mkdir -p "${STAGE}/${PKG}/docs"
cp -a "${ROOT}/docs/"*.md "${STAGE}/${PKG}/docs/" 2>/dev/null || true
echo "${VERSION}" > "${STAGE}/${PKG}/VERSION"
find "${STAGE}/${PKG}" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true

echo "[2/5] 下载目标平台 wheels (manylinux2014_${ARCH}, cp${PYTAG})"
mkdir -p "${STAGE}/${PKG}/wheels"
if command -v pip3 >/dev/null 2>&1 || python3 -m pip --version >/dev/null 2>&1; then
  python3 -m pip download -r "${ROOT}/requirements.txt" \
    -d "${STAGE}/${PKG}/wheels" \
    --platform "manylinux2014_${ARCH}" --platform "manylinux_2_17_${ARCH}" --platform "any" \
    --python-version "${PYTAG}" --implementation cp --abi "cp${PYTAG}" --abi none --abi abi3 \
    --only-binary=:all: || echo "警告: pip download 尝试失败，继续生成全量包"
  python3 -m pip download pip setuptools wheel -d "${STAGE}/${PKG}/wheels" \
    --platform any --python-version "${PYTAG}" --only-binary=:all: 2>/dev/null || true
else
  echo "提示: 打包环境未配置 python3-pip，继续生成源码全量发布包"
fi

echo "[3/5] 便携 Python: ${WITH_PYTHON}"
if [[ "${WITH_PYTHON}" == "yes" ]]; then
  # python-build-standalone 便携版（indygreg），版本须与 BUNDLED_PY_VER 一致
  PBS_TAG="20250115"; PBS_VER="${BUNDLED_PY_VER}"
  case "$ARCH" in
    x86_64)  TRIPLE="x86_64-unknown-linux-gnu";;
    aarch64) TRIPLE="aarch64-unknown-linux-gnu";;
  esac
  URL="https://github.com/indygreg/python-build-standalone/releases/download/${PBS_TAG}/cpython-${PBS_VER}+${PBS_TAG}-${TRIPLE}-install_only.tar.gz"
  echo "  下载 ${URL}"
  # 本脚本没有 set -e：下载/解包失败若不显式拦，会打出一个「声称内置 Python
  # 但实际没有」的包，目标机装到一半才发现。故此处失败即中止。
  curl -fL "${URL}" -o "${STAGE}/python.tar.gz" \
    || { echo "错误: 便携 Python 下载失败 (${URL})，已中止打包"; exit 1; }
  tar -xzf "${STAGE}/python.tar.gz" -C "${STAGE}/${PKG}/" \
    || { echo "错误: 便携 Python 解包失败，已中止打包"; exit 1; }
  [[ -x "${STAGE}/${PKG}/python/bin/python3" ]] \
    || { echo "错误: 内置 Python 解包后不可执行，已中止打包"; exit 1; }
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
