"""Test big table detection via API"""
import requests
import json

BASE_URL = "http://127.0.0.1:8000"

# Login
login_resp = requests.post(f"{BASE_URL}/api/v1/auth/login", json={
    "username": "admin",
    "password": "Abcd1234"
})
token = login_resp.json().get("token")
headers = {"Authorization": f"Bearer {token}"}

# Get connections
conn_resp = requests.get(f"{BASE_URL}/api/v1/tdsql/connections", headers=headers)
conn_data = conn_resp.json()
print(f"Connection response: {json.dumps(conn_data, indent=2, ensure_ascii=False)}")
connections = conn_data.get("connections", conn_data) if isinstance(conn_data, dict) else conn_data
print("=== Available Connections ===")
if isinstance(connections, list):
    for conn in connections:
        if isinstance(conn, dict):
            print(f"  {conn.get('id', 'N/A')}: {conn.get('name', 'N/A')} ({conn.get('host', 'N/A')}:{conn.get('port', 'N/A')})")
        else:
            print(f"  {conn}")
else:
    print(f"  {connections}")

# Use the first connection (SIT-分布式实例A)
connection_id = "5ea70d74"

print()
print(f"=== Testing with connection_id={connection_id} ===")
print()

# Test with 1GB threshold
print("--- Threshold: 1.0GB ---")
resp = requests.get(
    f"{BASE_URL}/api/v1/tdsql/check/large-tables",
    params={"connection_id": connection_id, "threshold_gb": 1.0},
    headers=headers
)
result = resp.json()
print(f"Tables found: {len(result.get('tables', []))}")
for table in result.get("tables", []):
    print(f"  {table['table_name']}: {table.get('size_gb', 'N/A')}GB, {table.get('table_rows', 'N/A')} rows, level={table.get('level', 'N/A')}")

# Test with 0.01GB threshold
print()
print("--- Threshold: 0.01GB ---")
resp = requests.get(
    f"{BASE_URL}/api/v1/tdsql/check/large-tables",
    params={"connection_id": connection_id, "threshold_gb": 0.01},
    headers=headers
)
result = resp.json()
print(f"Tables found: {len(result.get('tables', []))}")
if result.get('tables'):
    print(f"First table keys: {list(result['tables'][0].keys())}")
    print(f"First table data: {json.dumps(result['tables'][0], indent=2, ensure_ascii=False)}")
for table in result.get("tables", [])[:10]:
    # Try different key names
    table_name = table.get('table_name') or table.get('TABLE_NAME') or 'N/A'
    size_gb = table.get('size_gb') or table.get('SIZE_GB') or 'N/A'
    table_rows = table.get('table_rows') or table.get('TABLE_ROWS') or 'N/A'
    level = table.get('level') or table.get('LEVEL') or 'N/A'
    print(f"  {table_name}: {size_gb}GB, {table_rows} rows, level={level}")

# Check specifically for big_audit_trail and big_order_log
print()
print("--- Checking for big_audit_trail and big_order_log ---")
all_tables = result.get("tables", [])
big_tables = [t for t in all_tables if "big_" in t.get("table_name", "")]
print(f"Tables with 'big_' in name: {len(big_tables)}")
for table in big_tables:
    print(f"  {table['table_name']}: {table.get('size_gb', 'N/A')}GB, {table.get('table_rows', 'N/A')} rows")

print()
print("Test completed.")
