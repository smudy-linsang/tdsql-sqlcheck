"""
自动修复和补齐 role_permissions 菜单列表脚本
"""
from backend.services.database import ensure_db, _get_connection

def fix_perms():
    ensure_db()
    conn = _get_connection()
    try:
        roles = ['admin', 'dba', 'developer', 'auditor']
        for r in roles:
            conn.execute("""
                REPLACE INTO role_permissions(role_id, menu_key, visible)
                VALUES(%s, 'schema-extractor-audit', 1)
            """, (r,))
        conn.commit()
        print("Successfully backfilled 'schema-extractor-audit' permission to all roles.")
    finally:
        conn.close()

if __name__ == "__main__":
    fix_perms()
