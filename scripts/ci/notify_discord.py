"""Keep the existing Discord channel and owner IDs for the verified pipeline."""
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.discord_ack import notify_failure


def main():
    webhook = os.getenv('DISCORD_WEBHOOK_URL', '').strip()
    if not webhook:
        print('Discord secret unavailable; no notification sent.')
        return
    url = urlsplit(webhook)
    if (url.scheme != 'https' or url.hostname not in {'discord.com', 'discordapp.com'}
            or not re.fullmatch(r'/api/(?:v[0-9]+/)?webhooks/[0-9]{17,20}/[A-Za-z0-9._-]+', url.path)):
        raise ValueError('Invalid Discord webhook configuration.')
    results = json.loads(os.environ['RESULTS'])
    failed = [name for name, result in results.items()
              if (name != 'publish' and result != 'success')
              or (name == 'publish' and result not in {'success', 'skipped'})]
    mentions = []
    if failed:
        keys = (['DISCORD_FRONTEND_ID'] if failed == ['frontend'] else
                ['DISCORD_BACKEND_ID'] if failed == ['backend'] else
                ['DISCORD_FRONTEND_ID', 'DISCORD_BACKEND_ID'])
        mentions = [os.getenv(key, '') for key in keys if re.fullmatch(r'[0-9]{17,20}', os.getenv(key, ''))]
    lines = ['SEOUL WAVE CI 실패' if failed else 'SEOUL WAVE CI 성공',
             *[f'{name}: {result}' for name, result in results.items()],
             f"Commit: {os.environ['COMMIT_SHA']}", os.environ['RUN_URL'],
             *[f'<@{user}>' for user in mentions]]
    payload = {'content': '\n'.join(lines), 'allowed_mentions': {'parse': [], 'users': mentions}}
    if failed:
        status, state = notify_failure(
            webhook, payload,
            ack_user_ids=mentions,
            escalation_user_id=os.getenv('DISCORD_AGENT_ID', '').strip(),
            bot_token=os.getenv('DISCORD_BOT_TOKEN', '').strip(),
            channel_id=os.getenv('DISCORD_CHANNEL_ID', '').strip(),
        )
        print(f'Discord response: {status}; acknowledgement: {state}')
        return
    with urlopen(Request(webhook, data=json.dumps(payload).encode(), method='POST',
                         headers={'Content-Type': 'application/json', 'User-Agent': 'SEOUL-WAVE-CI/1.0'}),
                 timeout=20) as response:
        if not 200 <= response.status < 300:
            raise RuntimeError('Discord notification failed.')
        print(f'Discord response: {response.status}')


if __name__ == '__main__':
    main()
