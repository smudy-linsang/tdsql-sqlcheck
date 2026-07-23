"""
清理系统数据库中旧的硬编码假 IP 记录 (10.0.8.21, 10.0.8.22, 10.0.8.23)
"""
from backend.services.database import _get_connection

def clean_fake_ips():
    conn = _get_connection()
    try:
        cur = conn.execute("DELETE FROM server_daily_inspection WHERE ip IN ('10.0.8.21', '10.0.8.22', '10.0.8.23')")
        conn.commit()
        print(f"已成功清理 {cur.rowcount if hasattr(cur, 'rowcount') else '历史'} 条旧的假 IP 数据条目！")
    finally:
        conn.close()

if __name__ == "__main__":
    clean_fake_ips()
