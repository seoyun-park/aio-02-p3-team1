"""Acknowledgement polling and one-time escalation for Discord failure alerts."""
import copy
import json
import re
import time
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen


SNOWFLAKE = re.compile(r'[0-9]{17,20}')
WEBHOOK_PATH = re.compile(r'/api/(?:v[0-9]+/)?webhooks/[0-9]{17,20}/[A-Za-z0-9._-]+')
API_BASE = 'https://discord.com/api/v10'
ACK_EMOJI = '✅'


def _validate_webhook(webhook):
    parts = urlsplit(webhook or '')
    if (parts.scheme != 'https' or parts.hostname not in {'discord.com', 'discordapp.com'}
            or parts.username or parts.password or parts.fragment
            or WEBHOOK_PATH.fullmatch(parts.path) is None):
        raise ValueError('Invalid Discord webhook configuration.')
    return parts


def _request_webhook(webhook, payload, *, wait, sender=urlopen):
    parts = _validate_webhook(webhook)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query['wait'] = 'true' if wait else 'false'
    url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ''))
    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
        method='POST',
        headers={'Content-Type': 'application/json', 'User-Agent': 'SEOUL-WAVE-ACK/1.0'},
    )
    with sender(request, timeout=20) as response:
        if not 200 <= response.status < 300:
            raise RuntimeError('Discord webhook request failed.')
        body = response.read()
        message = json.loads(body.decode('utf-8')) if wait and body else {}
        return response.status, message.get('id')


def _get_reactors(bot_token, channel_id, message_id, *, sender=urlopen):
    emoji = quote(ACK_EMOJI, safe='')
    after = None
    reactor_ids = set()
    while True:
        params = {'limit': 100}
        if after:
            params['after'] = after
        url = f'{API_BASE}/channels/{channel_id}/messages/{message_id}/reactions/{emoji}?{urlencode(params)}'
        request = Request(
            url,
            method='GET',
            headers={'Authorization': f'Bot {bot_token}', 'User-Agent': 'SEOUL-WAVE-ACK/1.0'},
        )
        with sender(request, timeout=20) as response:
            if not 200 <= response.status < 300:
                raise RuntimeError('Discord reaction lookup failed.')
            users = json.loads(response.read().decode('utf-8'))
        if not isinstance(users, list):
            raise RuntimeError('Unexpected Discord reaction response.')
        reactor_ids.update(user['id'] for user in users if isinstance(user, dict) and SNOWFLAKE.fullmatch(str(user.get('id', ''))))
        if len(users) < 100:
            return reactor_ids
        after = str(users[-1]['id'])


def wait_for_ack(bot_token, channel_id, message_id, ack_user_ids, *, timeout_seconds=900,
                 poll_interval_seconds=30, sender=urlopen, sleeper=time.sleep, monotonic=time.monotonic):
    deadline = monotonic() + timeout_seconds
    expected = set(ack_user_ids)
    while True:
        if expected.intersection(_get_reactors(bot_token, channel_id, message_id, sender=sender)):
            return True
        remaining = deadline - monotonic()
        if remaining <= 0:
            return False
        sleeper(min(poll_interval_seconds, remaining))


def notify_failure(webhook, payload, *, ack_user_ids, escalation_user_id, bot_token, channel_id,
                   timeout_seconds=900, poll_interval_seconds=30, sender=urlopen,
                   sleeper=time.sleep, monotonic=time.monotonic, logger=print):
    """Send one failure notice; wait for an assigned user's ✅, otherwise ping the agent once."""
    valid_ack_ids = [str(user_id) for user_id in ack_user_ids if SNOWFLAKE.fullmatch(str(user_id))]
    config_ready = (
        bool(bot_token)
        and bool(SNOWFLAKE.fullmatch(str(channel_id or '')))
        and bool(SNOWFLAKE.fullmatch(str(escalation_user_id or '')))
        and bool(valid_ack_ids)
        and timeout_seconds > 0
        and poll_interval_seconds > 0
    )
    initial_payload = copy.deepcopy(payload)
    if not config_ready:
        status, _ = _request_webhook(webhook, initial_payload, wait=False, sender=sender)
        logger('::warning::Discord acknowledgement escalation is disabled; required bot settings or responder IDs are missing.')
        return status, 'disabled'

    initial_payload['content'] = (
        initial_payload.get('content', '')
        + '\n담당자는 이 메시지에 ✅ 반응을 남겨 확인해주세요. 15분 내 확인 반응이 없으면 박서윤에게 재통보됩니다.'
    )
    initial_payload.setdefault('allowed_mentions', {'parse': []})
    status, message_id = _request_webhook(webhook, initial_payload, wait=True, sender=sender)
    if not SNOWFLAKE.fullmatch(str(message_id or '')):
        logger('::warning::Discord returned no message ID; acknowledgement escalation was skipped.')
        fallback = {
            'content': f'<@{escalation_user_id}> the CI/CD acknowledgement check could not start. Please inspect the original failure alert.',
            'allowed_mentions': {'parse': [], 'users': [str(escalation_user_id)]},
        }
        _request_webhook(webhook, fallback, wait=False, sender=sender)
        return status, 'check_failed'

    try:
        acknowledged = wait_for_ack(
            bot_token, channel_id, str(message_id), valid_ack_ids,
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
            sender=sender, sleeper=sleeper, monotonic=monotonic,
        )
    except Exception as exc:
        logger(f'::warning::Discord acknowledgement check failed ({type(exc).__name__}); notifying the escalation owner immediately.')
        fallback = {
            'content': f'<@{escalation_user_id}> the 15-minute acknowledgement check failed. Please inspect the original CI/CD alert and verify the bot token and channel permissions.',
            'allowed_mentions': {'parse': [], 'users': [str(escalation_user_id)]},
        }
        _request_webhook(webhook, fallback, wait=False, sender=sender)
        return status, 'check_failed'
    if acknowledged:
        logger('Discord failure alert acknowledged by an assigned responder.')
        return status, 'acknowledged'

    escalation = {
        'content': f'<@{escalation_user_id}> CI/CD 실패 알림에 15분 동안 확인 반응이 없어 재통보합니다. 원본 알림을 확인해주세요.',
        'allowed_mentions': {'parse': [], 'users': [str(escalation_user_id)]},
    }
    _request_webhook(webhook, escalation, wait=False, sender=sender)
    logger('Discord escalation sent after the acknowledgement timeout.')
    return status, 'escalated'
