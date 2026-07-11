"""Test bigtable collection API"""
import requests

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
    print(f'  {c["id"]}: {c["name"]} ({c["host"]}:{c["port"]})')

# Get first connection ID
if conns:
    cid = conns[0]['id']
    print(f'\nUsing connection: {cid}')
    
    # Collect big tables
    r = requests.get(f'{BASE}/api/v1/tdsql/check/large-tables?connection_id={cid}', headers=h)
    print(f'Large tables API status: {r.status_code}')
    if r.ok:
        data = r.json()
        print(f'Response keys: {list(data.keys())}')
        tables = data.get('tables', [])
        print(f'Tables count: {len(tables)}')
        if tables:
            print(f'First table: {tables[0]}')
        else:
            print('No big tables found (this is expected if no tables > 1GB)')
    else:
        print(f'Error: {r.text[:200]}')
    
    # Now check inventory
    print(f'\nChecking inventory...')
    r2 = requests.get(f'{BASE}/api/v1/bigtable/inventory/{cid}', headers=h)
    print(f'Inventory API status: {r2.status_code}')
    if r2.ok:
        data2 = r2.json()
        print(f'Inventory response keys: {list(data2.keys())}')
        items = data2.get('data', [])
        print(f'Inventory items count: {len(items)}')
        if items:
            print(f'First item: {items[0]}')
    else:
        print(f'Error: {r2.text[:200]}')
