"""Live API smoke test. Defaults to localhost. Pass --base-url for a deployment."""
import argparse
import os
import httpx
from dotenv import load_dotenv

if __name__ == '__main__':
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://127.0.0.1:8000')
    args = parser.parse_args()
    token = os.getenv('APP_ACCESS_TOKEN', '')
    headers = {'Authorization': 'Bearer ' + token} if token else {}
    with httpx.Client(base_url=args.base_url, headers=headers, timeout=420) as c:
        for route in ['/api/health', '/api/ready', '/api/context', '/api/dashboard']:
            r = c.get(route); r.raise_for_status(); print('OK', route)
        response = c.post('/api/chat', json={'question': 'Дай обзор качества портфеля на последнем срезе, с источниками.', 'scenario': 'overview'})
        response.raise_for_status()
        result = response.json()
        assert result['trace'] and any(x['ok'] for x in result['trace'])
        print('OK /api/chat', 'mode:', result['mode'], 'tools:', len(result['trace']))
        print(result['answer'])
