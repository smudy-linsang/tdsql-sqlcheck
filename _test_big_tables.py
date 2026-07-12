"""Test big table detection for big_audit_trail and big_order_log"""
import requests
import json

BASE = 'http://127.0.0.1:8000'

# Login
r = requests.post(f'{BASE}/api/v1/auth/login', json={'username': 'admin', 'password': 'Abcd1234'})
token = r.json().get('token', '')
h = {'Authorization': f'Bearer {token}'}

# Get connections
r = requests.get(f'{BASE}/api/v1/tdsql/connections', headers=h)
conns = r.json().get('connections', [])
print(f'Connections: {len(conns)}')
for c in conns:
    print(f'  {c["id"]}: {c["name"]} ({c["host"]}:{c["port"]}/{c.get("database", "")})')

# Find the tdsql_check connection
tdsql_check_conn = None
for c in conns:
    if c.get('database') == 'tdsql_check' or 'tdsql_check' in c.get('name', ''):
        tdsql_check_conn = c
        break

if not tdsql_check_conn:
    print('tdsql_check connection not found, using first connection')
    tdsql_check_conn = conns[0] if conns else None

if tdsql_check_conn:
    cid = tdsql_check_conn['id']
    print(f'\nUsing connection: {cid} ({tdsql_check_conn["name"]})')
    
    # Test with 1GB threshold
    print('\n=== Testing with 1GB threshold ===')
    r = requests.get(f'{BASE}/api/v1/tdsql/check/large-tables?connection_id={cid}&threshold_gb=1.0', headers=h)
    if r.ok:
        data = r.json()
        tables = data.get('tables', [])
        print(f'Tables found: {len(tables)}')
        for t in tables:
            print(f'  {t["TABLE_NAME"]}: {t["size_gb"]}GB, {t["TABLE_ROWS"]} rows, level={t.get("level", "")}')
    else:
        print(f'Error: {r.status_code} - {r.text[:200]}')
    
    # Test with 0.1GB threshold to see all tables
    print('\n=== Testing with 0.1GB threshold (to see more tables) ===')
    r = requests.get(f'{BASE}/api/v1/tdsql/check/large-tables?connection_id={cid}&threshold_gb=0.1', headers=h)
    if r.ok:
        data = r.json()
        tables = data.get('tables', [])
        print(f'Tables found: {len(tables)}')
        for t in tables[:10]:  # Show first 10
            print(f'  {t["TABLE_NAME"]}: {t["size_gb"]}GB, {t["TABLE_ROWS"]} rows, level={t.get("level", "")}')
        if len(tables) > 10:
            print(f'  ... and {len(tables) - 10} more tables')
    else:
        print(f'Error: {r.status_code} - {r.text[:200]}')
    
    # Check if big_audit_trail and big_order_log are in the results
    print('\n=== Checking for big_audit_trail and big_order_log ===')
    r = requests.get(f'{BASE}/api/v1/tdsql/check/large-tables?connection_id={cid}&threshold_gb=0.01', headers=h)
    if r.ok:
        data = r.json()
        tables = data.get('tables', [])
        big_tables = [t for t in tables if 'big_' in t['TABLE_NAME']]
        print(f'Tables with "big_" in name: {len(big_tables)}')
        for t in big_tables:
            print(f'  {t["TABLE_NAME"]}: {t["size_gb"]}GB, {t["TABLE_ROWS"]} rows')
    else:
        print(f'Error: {r.status_code} - {r.text[:200]}')
else:
    print('No connection found')
