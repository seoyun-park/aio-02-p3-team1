"""Send a clearly marked manual test alert for the 15-minute Discord acknowledgement flow."""
import os
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.discord_ack import notify_failure


def main():
    responder_ids = [
        os.getenv(name, '').strip()
        for name in ('DISCORD_BACKEND_ID', 'DISCORD_FRONTEND_ID', 'DISCORD_DEPLOY_ID')
    ]
    responder_ids = [value for value in responder_ids if re.fullmatch(r'[0-9]{17,20}', value)]
    if not os.getenv('DISCORD_WEBHOOK_URL', '').strip():
        raise SystemExit('DISCORD_WEBHOOK_URL is required for the manual test.')
    result = notify_failure(
        os.environ['DISCORD_WEBHOOK_URL'],
        {
            'content': '[TEST] 15-minute acknowledgement test. No incident is active. React ✅ to this message within 15 minutes to cancel the test escalation.',
            'allowed_mentions': {'parse': []},
        },
        ack_user_ids=responder_ids,
        escalation_user_id=os.getenv('DISCORD_AGENT_ID', '').strip(),
        bot_token=os.getenv('DISCORD_BOT_TOKEN', '').strip(),
        channel_id=os.getenv('DISCORD_CHANNEL_ID', '').strip(),
    )
    print(f'Manual acknowledgement test result: {result[1]}')
    if result[1] not in {'acknowledged', 'escalated'}:
        raise SystemExit('The test could not verify the acknowledgement flow. Check repository configuration and workflow logs.')


if __name__ == '__main__':
    main()
