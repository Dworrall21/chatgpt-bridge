import json, urllib.request, sys

req = urllib.request.Request(
    'http://127.0.0.1:11557/v1/chat/completions',
    data=json.dumps({
        'model': 'auto',
        'messages': [{'role': 'user', 'content': 'Reply with exactly: bridge-e2e-ok'}]
    }).encode(),
    headers={'Content-Type': 'application/json'},
    method='POST'
)
try:
    resp = urllib.request.urlopen(req, timeout=120)
    body = resp.read().decode()
    print('HTTP', resp.status)
    data = json.loads(body)
    content = data.get('choices', [{}])[0].get('message', {}).get('content', '(no content)')
    print('Response:', content)
    if 'bridge-e2e-ok' in content:
        print('E2E: PASS')
    else:
        print('E2E: FAIL (unexpected content)')
        sys.exit(1)
except Exception as e:
    print('ERROR:', e)
    sys.exit(1)
