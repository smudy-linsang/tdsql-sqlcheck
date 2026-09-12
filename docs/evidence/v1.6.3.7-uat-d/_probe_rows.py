"""列出指定历史行的创建者与时间基（用于 UAT 取证判读）。"""
from backend.services.database import _get_connection

cx = _get_connection()
rows = cx.execute("SELECT id, source, created_at, created_by FROM audit_history "
                  "WHERE id IN (31,26,25,24,23,22,8,4) ORDER BY id").fetchall()
for x in rows:
    print(x["id"], x["created_at"], "|", x["created_by"], "|", x["source"])
cx.close()
