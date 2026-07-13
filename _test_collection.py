"""Test big table collection via API"""
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

# Use SIT-分布式实例A
connection_id = "5ea70d74"

print("=== Testing big table collection ===")
print(f"Connection ID: {connection_id}")
print()

# Test with threshold 1GB (default)
print("--- Threshold: 1.0GB ---")
resp = requests.get(
    f"{BASE_URL}/api/v1/tdsql/check/large-tables",
    params={"connection_id": connection_id, "threshold_gb": 1.0},
    headers=headers
)
result = resp.json()
print(f"Status: {resp.status_code}")
print(f"Database: {result.get('database')}")
print(f"Threshold: {result.get('threshold_gb')}GB")
print(f"Total tables found: {result.get('total')}")
print()

tables = result.get("tables", [])
if tables:
    print("Tables found:")
    for i, table in enumerate(tables, 1):
        print(f"  {i}. {table.get('TABLE_NAME')}: {table.get('size_gb')}GB, level={table.get('level')}")
else:
    print("No tables found with threshold 1.0GB")

print()

# Test with threshold 0GB to see all tables
print("--- Threshold: 0.0GB (all tables) ---")
resp = requests.get(
    f"{BASE_URL}/api/v1/tdsql/check/large-tables",
    params={"connection_id": connection_id, "threshold_gb": 0.0},
    headers=headers
)
result = resp.json()
print(f"Total tables found: {result.get('total')}")

tables = result.get("tables", [])
print()
print("All tables (first 20):")
for i, table in enumerate(tables[:20], 1):
    print(f"  {i}. {table.get('TABLE_NAME')}: {table.get('size_gb')}GB")

# Check specifically for big_audit_trail
print()
print("--- Checking for big_audit_trail ---")
big_audit = [t for t in tables if "big_audit" in t.get("TABLE_NAME", "").lower()]
if big_audit:
    print(f"Found {len(big_audit)} table(s) with 'big_audit' in name:")
    for t in big_audit:
        print(f"  {t.get('TABLE_NAME')}: {t.get('size_gb')}GB, level={t.get('level')}")
else:
    print("big_audit_trail NOT found in results")

print()
print("Test completed.")
