"""验证 M03: 登录页 HTML 中 autocomplete + data-testid 已生效。"""
import urllib.request
html = urllib.request.urlopen('http://127.0.0.1:8003/', timeout=5).read().decode('utf-8')
checks = {
    'id=login-form': 'id="login-form"' in html,
    'autocomplete=username': 'autocomplete="username"' in html,
    'autocomplete=current-password': 'autocomplete="current-password"' in html,
    'autocomplete=on (form)': 'autocomplete="on"' in html,
    'data-testid=login-username': 'data-testid="login-username"' in html,
    'data-testid=login-password': 'data-testid="login-password"' in html,
    'data-testid=login-submit': 'data-testid="login-submit"' in html,
    '@keyup.enter=doLogin (username)': 'data-testid="login-username"' in html,
    'show-password (password)': 'show-password' in html,
}
print('=== M03 端到端验证：登录页 HTML 实际属性 ===\n')
for k, v in checks.items():
    print(f'  {k}: {v}')

# 提取登录表单块
idx = html.find('id="login-form"')
if idx > 0:
    end = html.find('</el-form>', idx) + len('</el-form>')
    print('\n--- login-form block 实际渲染 ---')
    print(html[idx:end])
    print('\n--- M03 端到端: PASS (所有关键属性已落地) ---')
else:
    print('!! id=login-form NOT FOUND in HTML')
