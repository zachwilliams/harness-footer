"""Print the caller's sessions on an omnigent server as JSON.

Usage: python -P omnigent_sessions.py SERVER_URL

This runs under omnigent's own interpreter, so it must not import
harness_footer. It borrows omnigent's request headers so every credential
source `omnigent login` supports works here too. Sessions that are neither
terminal-native nor stopped need their runner state, which only the
per-session endpoint reports, so those are fetched one by one.
"""

import json
import sys

import httpx
from omnigent.chat import _remote_headers

WRAPPER_LABEL = 'omnigent.wrapper'
LIMIT = 50


def main(server):
    server = server.rstrip('/')
    headers = _remote_headers(server_url=server, host_id=None)
    with httpx.Client(base_url=server, headers=headers, timeout=15) as http:
        me = http.get('/v1/me')
        owner = me.json().get('user_id') if me.status_code == 200 else None
        response = http.get(
            '/v1/sessions', params={'limit': LIMIT, 'order': 'desc'}
        )
        response.raise_for_status()
        sessions = []
        for row in response.json().get('data', []):
            # Resume is owner-only; shared sessions would fail to open.
            if owner and row.get('owner') not in (None, owner):
                continue
            if row.get('parent_session_id'):
                continue
            wrapper = (row.get('labels') or {}).get(WRAPPER_LABEL)
            runner_online = False
            if not wrapper:
                detail = http.get(f'/v1/sessions/{row["id"]}')
                if detail.status_code == 200:
                    runner_online = bool(detail.json().get('runner_online'))
            sessions.append(
                {
                    'id': row['id'],
                    'title': row.get('title') or '',
                    'agent': row.get('agent_name') or '',
                    'status': row.get('status') or '',
                    'updated_at': row.get('updated_at') or 0,
                    'wrapper': wrapper or '',
                    'runner_online': runner_online,
                }
            )
    json.dump(sessions, sys.stdout)


if __name__ == '__main__':
    main(sys.argv[1])
