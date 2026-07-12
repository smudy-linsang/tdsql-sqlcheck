"""Direct query to information_schema.PARTITIONS for big_audit_trail"""
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

connection_id = "5ea70d74"

print("=== Direct query to information_schema.PARTITIONS ===")
print()

# Use the adhoc SQL query endpoint to directly query information_schema.PARTITIONS
# First, let's check if big_audit_trail exists in PARTITIONS
sql = """
SELECT TABLE_NAME, PARTITION_NAME, DATA_LENGTH, INDEX_LENGTH
FROM information_schema.PARTITIONS
WHERE TABLE_SCHEMA = 'tdsql_check'
  AND TABLE_NAME = 'big_audit_trail'
ORDER BY PARTITION_NAME
"""

# Use the explain endpoint or create a custom query
# Since we don't have a direct SQL endpoint, let's use the large-tables endpoint with threshold 0
print("Testing with threshold 0GB (should return all tables)...")
resp = requests.get(
    f"{BASE_URL}/api/v1/tdsql/check/large-tables",
    params={"connection_id": connection_id, "threshold_gb": 0.0},
    headers=headers
)
result = resp.json()
tables = result.get("tables", [])
print(f"Total tables found with threshold 0GB: {len(tables)}")

# Look for big_audit_trail
big_audit = [t for t in tables if "big_audit" in t.get("TABLE_NAME", "").lower()]
print(f"Tables with 'big_audit' in name: {len(big_audit)}")
for t in big_audit:
    print(f"  {t.get('TABLE_NAME')}: {t.get('size_gb')}GB")

# List all tables
print()
print("=== All tables (first 30) ===")
for i, table in enumerate(tables[:30], 1):
    print(f"{i}. {table.get('TABLE_NAME', 'N/A')}: {table.get('size_gb', 'N/A')}GB")

print()
print("Test completed.")
