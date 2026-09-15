"""智能体D / v1.6.4.0 UAT 环境准备（loopback + 本机内网网段；不碰生产与既有夹具）。

专用资源：
  元数据库 uat_d_1640_meta（127.0.0.1:13306 docker tdsql-mysql-test）
  账号 uat_d_1640(admin) / uat_d_1640b(admin) / uat_d_1640dev(developer) / uat_d_1640aud(auditor)
  连接 d40-dist / d40-cent
  Web 127.0.0.1:8025；受控模型网关 172.16.4.16:8443(主)/8444(备)/8445(公网档)
  运行期目录 data/reports/uat_d_1640（.gitignore 已排除）

命令：
  python prepare_uat_d40.py certs      # 生成 CA/服务端证书 + 注入 certifi 信任（可还原）
  python prepare_uat_d40.py untrust    # 还原 certifi 原始 bundle
  python prepare_uat_d40.py db         # 建库/账号/连接/知识包自检
  python prepare_uat_d40.py config     # 写 keyring/endpoints 并播种 Copilot 配置
  python prepare_uat_d40.py status     # 打印当前状态
"""
import base64
import json
import os
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
import _boot  # noqa: F401,E402  （补入用户级 site-packages）
RUNTIME = ROOT / "data/reports/uat_d_1640"
RUNTIME.mkdir(parents=True, exist_ok=True)

META_DB = "uat_d_1640_meta"
GW_HOST = os.getenv("UAT_D40_GW_HOST", "172.16.4.16")
ACCOUNTS = {
    "uat_d_1640": ("D40-UAT管理员甲", "admin"),
    "uat_d_1640b": ("D40-UAT管理员乙", "admin"),
    "uat_d_1640dev": ("D40-UAT开发员", "developer"),
    "uat_d_1640aud": ("D40-UAT审计员", "auditor"),
}
PW_FILE = RUNTIME / "accounts.json"
CERTIFI_BACKUP = RUNTIME / "certifi-cacert.pem.orig"

os.environ.update({
    "SQLCHECK_DB_HOST": "127.0.0.1", "SQLCHECK_DB_PORT": "13306",
    "SQLCHECK_DB_USER": "root", "SQLCHECK_DB_PASSWORD": "tdsql_test_2024",
    "SQLCHECK_DB_NAME": META_DB,
    "AUTH_ENABLED": "true", "SCHEDULER_ENABLED": "false",
    "DATA_MASKING_ENABLED": "false",
    "REPORT_OUTPUT_DIR": str(RUNTIME / "reports"),
    "COPILOT_KEYRING_FILE": str(RUNTIME / "copilot-keyring.json"),
    "COPILOT_ENDPOINTS_FILE": str(RUNTIME / "copilot-endpoints.json"),
    "PYTHONPATH": str(ROOT),
})
sys.path.insert(0, str(ROOT))


def passwords() -> dict:
    if PW_FILE.exists():
        return json.loads(PW_FILE.read_text(encoding="utf-8"))
    import secrets
    data = {u: "D40" + secrets.token_urlsafe(9) + "!aA1" for u in ACCOUNTS}
    PW_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                       encoding="utf-8")
    return data


# ══════════════════════════════════════════════════════════════════
# certs：自签 CA + 服务端证书；把 CA 注入 certifi bundle
# ══════════════════════════════════════════════════════════════════
def cmd_certs():
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "UAT-D40-Test-CA")])
    now = datetime.now(timezone.utc)
    ca_cert = (x509.CertificateBuilder()
               .subject_name(ca_name).issuer_name(ca_name)
               .public_key(ca_key.public_key())
               .serial_number(x509.random_serial_number())
               .not_valid_before(now - timedelta(days=1))
               .not_valid_after(now + timedelta(days=30))
               .add_extension(x509.BasicConstraints(ca=True, path_length=None),
                              critical=True)
               .add_extension(x509.KeyUsage(
                   digital_signature=True, content_commitment=False,
                   key_encipherment=False, data_encipherment=False,
                   key_agreement=False, key_cert_sign=True, crl_sign=True,
                   encipher_only=False, decipher_only=False), critical=True)
               .add_extension(x509.SubjectKeyIdentifier.from_public_key(
                   ca_key.public_key()), critical=False)
               .sign(ca_key, hashes.SHA256()))

    srv_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    import ipaddress
    san = x509.SubjectAlternativeName([
        x509.IPAddress(ipaddress.ip_address(GW_HOST)),
        x509.DNSName("localhost"),
    ])
    srv_cert = (x509.CertificateBuilder()
                .subject_name(x509.Name([
                    x509.NameAttribute(NameOID.COMMON_NAME, GW_HOST)]))
                .issuer_name(ca_name)
                .public_key(srv_key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(now - timedelta(days=1))
                .not_valid_after(now + timedelta(days=30))
                .add_extension(san, critical=False)
                .add_extension(x509.BasicConstraints(ca=False, path_length=None),
                               critical=True)
                .add_extension(x509.KeyUsage(
                    digital_signature=True, content_commitment=False,
                    key_encipherment=True, data_encipherment=False,
                    key_agreement=False, key_cert_sign=False, crl_sign=False,
                    encipher_only=False, decipher_only=False), critical=True)
                .add_extension(x509.ExtendedKeyUsage(
                    [x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
                .add_extension(x509.SubjectKeyIdentifier.from_public_key(
                    srv_key.public_key()), critical=False)
                .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(
                    ca_key.public_key()), critical=False)
                .sign(ca_key, hashes.SHA256()))

    (RUNTIME / "gw-ca.pem").write_bytes(
        ca_cert.public_bytes(serialization.Encoding.PEM))
    (RUNTIME / "gw-server.key").write_bytes(
        srv_key.private_bytes(serialization.Encoding.PEM,
                              serialization.PrivateFormat.PKCS8,
                              serialization.NoEncryption()))
    (RUNTIME / "gw-server.pem").write_bytes(
        srv_cert.public_bytes(serialization.Encoding.PEM)
        + ca_cert.public_bytes(serialization.Encoding.PEM))
    print(f"[certs] CA/服务端证书已生成 → {RUNTIME}")

    import certifi
    bundle = Path(certifi.where())
    if not CERTIFI_BACKUP.exists():
        shutil.copy2(bundle, CERTIFI_BACKUP)
        print(f"[certs] 已备份 certifi bundle → {CERTIFI_BACKUP}")
    marker = "# UAT-D40-TEST-CA"
    text = bundle.read_text(encoding="utf-8")
    if marker in text:
        # 先剔除上一版注入块（证书轮换时避免叠加旧 CA）
        text = text[:text.index(marker)].rstrip() + "\n"
    bundle.write_text(text + marker + "\n" +
                      (RUNTIME / "gw-ca.pem").read_text(encoding="utf-8"),
                      encoding="utf-8")
    print(f"[certs] 测试 CA 已注入 {bundle}（UAT 结束后执行 untrust 还原）")


def cmd_untrust():
    import certifi
    bundle = Path(certifi.where())
    if CERTIFI_BACKUP.exists():
        shutil.copy2(CERTIFI_BACKUP, bundle)
        print(f"[untrust] 已从备份还原 {bundle}")
    else:
        print("[untrust] 未找到备份，跳过")


# ══════════════════════════════════════════════════════════════════
# db：元数据库 + 账号 + 连接
# ══════════════════════════════════════════════════════════════════
def cmd_db():
    import pymysql
    from backend.services.database import ensure_db, _get_connection, MYSQL_CONFIG

    cfg = {k: v for k, v in MYSQL_CONFIG.items() if k != "database"}
    with pymysql.connect(**cfg) as conn:
        with conn.cursor() as c:
            c.execute(f"CREATE DATABASE IF NOT EXISTS {META_DB} "
                      "DEFAULT CHARSET utf8mb4")
            for db in ("uat_d_1640_dist", "uat_d_1640_cent"):
                c.execute(f"CREATE DATABASE IF NOT EXISTS {db} DEFAULT CHARSET utf8mb4")
        conn.commit()
    ensure_db()
    print(f"[db] {META_DB} 已就绪（ensure_db 完成，含 A 组迁移）")

    # B 组结构（正式维护入口）
    from backend.services.copilot import schema as schema_mod
    rc = schema_mod.apply_business_schema()
    print(f"[db] B组 apply_business_schema rc={rc}")
    if rc != 0:
        raise SystemExit(f"B组结构应用失败 rc={rc}")

    # 账号
    from backend.services.auth_service import auth_service
    pws = passwords()
    for username, (display, role) in ACCOUNTS.items():
        user, err = auth_service.create_user(username, pws[username], role,
                                             display_name=display,
                                             operator="D-UAT40-fixture")
        print(f"[db] 账号 {username}({role}): {'已创建' if user else err}")
    # 测试账号免改密（夹具专用；口令在 data/ 运行期目录，不入库外泄）
    cx = _get_connection()
    for username in ACCOUNTS:
        cx.execute("UPDATE users SET must_change_password = 0 WHERE username = ?",
                   (username,))
    cx.commit()
    cx.close()

    # 目标库对象（供在线元数据审核/证据适配器取真实记录）
    with pymysql.connect(**cfg) as conn:
        with conn.cursor() as c:
            for i in range(4):
                c.execute(f"CREATE TABLE IF NOT EXISTS uat_d_1640_dist.d40_tab_{i:02d} "
                          "(id BIGINT NOT NULL COMMENT 'ID', "
                          "name VARCHAR(64) NOT NULL DEFAULT '' COMMENT '名称', "
                          "PRIMARY KEY(id)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 "
                          "COMMENT='D40 real table'")
            c.execute("CREATE TABLE IF NOT EXISTS uat_d_1640_cent.d40_cent_a "
                      "(id BIGINT NOT NULL COMMENT 'ID', v VARCHAR(32) NOT NULL "
                      "DEFAULT '' COMMENT 'v', PRIMARY KEY(id)) ENGINE=InnoDB "
                      "DEFAULT CHARSET=utf8mb4 COMMENT='D40 cent table'")
        conn.commit()
    print("[db] 目标库对象已就绪")

    from backend.services.connection_registry import registry
    for cid, name, db, dist, is_def in (
        ("d40-dist", "D40-分布式测试库", "uat_d_1640_dist", True, True),
        ("d40-cent", "D40-集中式测试库", "uat_d_1640_cent", False, False),
    ):
        registry.save_connection(name=name, host="127.0.0.1", port=13306,
                                 username=MYSQL_CONFIG["user"],
                                 password=MYSQL_CONFIG["password"], database=db,
                                 is_distributed=dist, conn_id=cid,
                                 is_default=is_def, operator="D-UAT40")
    print("[db] 连接登记完成")


# ══════════════════════════════════════════════════════════════════
# config：keyring / endpoints / Copilot 配置播种
# ══════════════════════════════════════════════════════════════════
ENDPOINTS = {
    "schema_version": 1,
    "endpoints": [
        {"endpoint_id": "ep-uat-a", "scheme": "https", "canonical_host": GW_HOST,
         "port": 8443, "base_path": "/v1", "data_zone": "INTERNAL",
         "privacy_profile": "INTERNAL_REDACTED", "allows_schema_identifiers": True,
         "allowed_resolved_cidrs": ["172.16.0.0/12"], "tls_ca_ref": "internal"},
        {"endpoint_id": "ep-uat-b", "scheme": "https", "canonical_host": GW_HOST,
         "port": 8444, "base_path": "/v1", "data_zone": "INTERNAL",
         "privacy_profile": "INTERNAL_REDACTED", "allows_schema_identifiers": False,
         "allowed_resolved_cidrs": ["172.16.0.0/12"], "tls_ca_ref": "internal"},
        {"endpoint_id": "ep-uat-pub", "scheme": "https", "canonical_host": GW_HOST,
         "port": 8445, "base_path": "/v1", "data_zone": "PUBLIC",
         "privacy_profile": "PUBLIC_HELP", "allows_schema_identifiers": False,
         "allowed_resolved_cidrs": ["172.16.0.0/12"], "tls_ca_ref": "public"},
    ],
}


def cmd_config():
    kf = RUNTIME / "copilot-keyring.json"
    if not kf.exists():
        key = base64.b64encode(os.urandom(32)).decode()
        kf.write_text(json.dumps({"schema_version": 1, "active_kid": "key-uat40",
                                  "keys": {"key-uat40": key}}), encoding="utf-8")
        print(f"[config] keyring 已写入 {kf}")
    epf = RUNTIME / "copilot-endpoints.json"
    epf.write_text(json.dumps(ENDPOINTS, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(f"[config] endpoints 已写入 {epf}")

    os.environ["COPILOT_KEYRING_FILE"] = str(kf)
    os.environ["COPILOT_ENDPOINTS_FILE"] = str(epf)
    from backend.services.copilot.crypto import reset_keyring_cache
    from backend.services.copilot.policy import reset_policy_cache
    reset_keyring_cache()
    reset_policy_cache()

    from backend.services.database import _get_connection, ensure_db
    ensure_db()
    from backend.services.copilot.repository import RuntimeRepo
    conn = _get_connection()
    try:
        settings = RuntimeRepo.settings(conn)
        settings["enabled"] = True
        settings["allow_schema_identifiers"] = True
        rt = RuntimeRepo.get(conn) or {}
        RuntimeRepo.save_settings(conn, settings, int(rt.get("config_revision") or 1))
        conn.commit()
        print(f"[config] runtime settings = {settings}")
    finally:
        conn.close()


def cmd_status():
    from backend.services.database import _get_connection, ensure_db
    ensure_db()
    from backend.services.copilot.repository import ProviderRepo, RouteRepo, RuntimeRepo
    conn = _get_connection()
    try:
        rt = dict(RuntimeRepo.get(conn) or {})
        print(json.dumps({
            "runtime": {k: str(v) for k, v in rt.items()
                        if k in ("module_schema_state", "module_schema_epoch",
                                 "module_reconciled_epoch", "accepting",
                                 "config_revision", "settings_json")},
            "providers": [{k: p.get(k) for k in
                           ("id", "name", "endpoint_id", "model_id", "enabled",
                            "revision", "tested_revision", "cooldown_until")}
                          for p in ProviderRepo.list(conn)],
            "routes": [{k: r.get(k) for k in
                        ("scene_code", "primary_provider_id",
                         "fallback_provider_id", "enabled", "revision")}
                       for r in RouteRepo.list(conn)],
        }, ensure_ascii=False, indent=2))
    finally:
        conn.close()


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "help"
    {"certs": cmd_certs, "untrust": cmd_untrust, "db": cmd_db,
     "config": cmd_config, "status": cmd_status}.get(
        mode, lambda: print(__doc__))()
