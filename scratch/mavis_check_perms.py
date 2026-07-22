import sys
sys.path.insert(0, '.')
from backend.services.database import _get_connection, ensure_db
from backend.services.auth_service import get_visible_menus
ensure_db()
conn = _get_connection()
print('roles 表:')
for r in conn.execute('SELECT role_id, role_name, is_builtin FROM roles').fetchall():
    d = dict(r) if hasattr(r,'keys') else r
    print(f'  {d}')
print()
print('dba 的 role_permissions:')
visible_dba = set()
for r in conn.execute("SELECT menu_key, visible FROM role_permissions WHERE role_id='dba' ORDER BY menu_key").fetchall():
    d = dict(r) if hasattr(r,'keys') else r
    if d.get('visible'):
        visible_dba.add(d['menu_key'])
        print(f'  {d["menu_key"]}: visible')
print()
print('dba 实际可见菜单数:', len(visible_dba))
print('sys-auditlog 在 dba 可见集合中:', 'sys-auditlog' in visible_dba)
print()
print('auditor 的 sys-auditlog 状态:')
for r in conn.execute("SELECT visible FROM role_permissions WHERE role_id='auditor' AND menu_key='sys-auditlog'").fetchall():
    d = dict(r) if hasattr(r,'keys') else r
    print(f'  {d}')
print()
print('developer 的 sys-auditlog 状态:')
for r in conn.execute("SELECT visible FROM role_permissions WHERE role_id='developer' AND menu_key='sys-auditlog'").fetchall():
    d = dict(r) if hasattr(r,'keys') else r
    print(f'  {d}')
conn.close()
