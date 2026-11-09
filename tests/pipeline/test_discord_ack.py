import json
import unittest
from scripts.discord_ack import notify_failure


class FakeResponse:
    def __init__(self, status, body=b''):
        self.status = status
        self.body = body
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self):
        return self.body


class DiscordAckTests(unittest.TestCase):
    def setUp(self):
        self.webhook = 'https://discord.com/api/webhooks/123456789012345678/token'
        self.requests = []
        self.clock = [0.0]
    def sender(self, request, timeout=20):
        self.requests.append(request)
        if request.get_method() == 'GET':
            return FakeResponse(200, b'[]')
        if 'wait=true' in request.full_url:
            return FakeResponse(200, json.dumps({'id': '987654321098765432'}).encode())
        return FakeResponse(204)
    def sleep(self, seconds):
        self.clock[0] += seconds
    def monotonic(self):
        return self.clock[0]
    def notify(self, **kwargs):
        return notify_failure(
            self.webhook, {'content': 'CI failed', 'allowed_mentions': {'parse': []}},
            timeout_seconds=900, poll_interval_seconds=30,
            sender=self.sender, sleeper=self.sleep, monotonic=self.monotonic,
            logger=lambda _: None, **kwargs,
        )
    def test_ack_by_assigned_responder_cancels_escalation(self):
        def sender(request, timeout=20):
            self.requests.append(request)
            if request.get_method() == 'GET':
                return FakeResponse(200, json.dumps([{'id': '111111111111111111'}]).encode())
            if 'wait=true' in request.full_url:
                return FakeResponse(200, b'{"id":"987654321098765432"}')
            return FakeResponse(204)
        result = notify_failure(
            self.webhook, {'content':'CI failed'}, ack_user_ids=['111111111111111111'],
            escalation_user_id='222222222222222222', bot_token='test-token', channel_id='333333333333333333',
            timeout_seconds=900, poll_interval_seconds=30, sender=sender,
            sleeper=self.sleep, monotonic=self.monotonic, logger=lambda _: None,
        )
        self.assertEqual(result[1], 'acknowledged')
        self.assertEqual(sum(1 for r in self.requests if r.get_method() == 'POST'), 1)
    def test_no_ack_escalates_once_after_timeout(self):
        result = self.notify(
            ack_user_ids=['111111111111111111'], escalation_user_id='222222222222222222',
            bot_token='test-token', channel_id='333333333333333333',
        )
        self.assertEqual(result[1], 'escalated')
        self.assertEqual(self.clock[0], 900)
        gets = [r for r in self.requests if r.get_method() == 'GET']
        self.assertEqual(len(gets), 31)
        self.assertEqual(gets[0].get_header('Authorization'), 'Bot test-token')
        posts = [r for r in self.requests if r.get_method() == 'POST']
        self.assertEqual(len(posts), 2)
        self.assertIn('<@222222222222222222>', posts[1].data.decode())
    def test_reaction_lookup_failure_notifies_agent_immediately(self):
        def sender(request, timeout=20):
            self.requests.append(request)
            if request.get_method() == 'GET':
                raise RuntimeError('permissions unavailable')
            if 'wait=true' in request.full_url:
                return FakeResponse(200, b'{"id":"987654321098765432"}')
            return FakeResponse(204)
        result = notify_failure(
            self.webhook, {'content':'CI failed'}, ack_user_ids=['111111111111111111'],
            escalation_user_id='222222222222222222', bot_token='test-token', channel_id='333333333333333333',
            sender=sender, logger=lambda _: None,
        )
        self.assertEqual(result[1], 'check_failed')
        posts = [request for request in self.requests if request.get_method() == 'POST']
        self.assertEqual(len(posts), 2)
        self.assertIn('<@222222222222222222>', posts[1].data.decode())
    def test_incomplete_bot_settings_keeps_failure_alert_but_skips_timer(self):
        result = self.notify(
            ack_user_ids=[], escalation_user_id='', bot_token='', channel_id='',
        )
        self.assertEqual(result[1], 'disabled')
        self.assertEqual(len(self.requests), 1)
        self.assertNotIn('wait=true', self.requests[0].full_url)


if __name__ == '__main__':
    unittest.main()
