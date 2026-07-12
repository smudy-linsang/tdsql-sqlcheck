"""Test big table detection with detailed logging"""
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
    print(f'Response status: {r.status_code}')
    print(f'Full response: {json.dumps(r.json(), indent=2, ensure_ascii=False)}')
    
    # Test with 0.01GB threshold
    print('\n=== Testing with 0.01GB threshold ===')
    r = requests.get(f'{BASE}/api/v1/tdsql/check/large-tables?connection_id={cid}&threshold_gb=0.01', headers=h)
    print(f'Response status: {r.status_code}')
    data = r.json()
    tables = data.get('tables', [])
    print(f'Tables found: {len(tables)}')
    for t in tables:
        print(f'  {t["TABLE_NAME"]}: {t["size_gb"]}GB, {t["TABLE_ROWS"]} rows')
else:
    print('No connection found')
