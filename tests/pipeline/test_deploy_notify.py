import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
NOTIFY_PATH = REPOSITORY_ROOT / 'scripts/deploy/notify.py'
if NOTIFY_PATH.is_file():
    spec = importlib.util.spec_from_file_location('deploy_notify', NOTIFY_PATH)
    notify = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(notify)
else:
    notify = None


class FakeResponse:
    status = 204

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return b''


class DeploymentNotificationTests(unittest.TestCase):
    def test_missing_webhook_secret_fails_notification_step(self):
        self.assertIsNotNone(notify, 'scripts/deploy/notify.py has not been implemented')
        with tempfile.TemporaryDirectory() as directory:
            evidence_path = Path(directory) / 'deployment-evidence.json'
            evidence_path.write_text(
                json.dumps({'deployment_status': 'failure'}), encoding='utf-8',
            )
            with patch.dict(os.environ, {'DISCORD_WEBHOOK_URL': ''}):
                result = notify.main([
                    '--evidence', str(evidence_path), '--job-status', 'failure',
                ])

        self.assertNotEqual(result, 0)

    def test_success_notification_contains_only_allowlisted_deployment_evidence(self):
        self.assertIsNotNone(notify, 'scripts/deploy/notify.py has not been implemented')
        secret = 'discord-webhook-token-do-not-disclose'
        webhook = 'https://discord.com/api/webhooks/123456789012345678/' + secret
        evidence = {
            'deployment_status': 'success',
            'source_sha': 'a' * 40,
            'source_run_id': '123',
            'ssm_command_id': '9c4f29d0-5cc3-4c75-a880-35c6e4a99a21',
            'ssm_status': 'Success',
            'remote_outcome': 'applied',
            'standard_output': 'password=not-for-discord',
            'webhook': secret,
        }
        sent = []

        def sender(request, timeout):
            sent.append((request, timeout))
            return FakeResponse()

        notify.send_notification(webhook, evidence, sender=sender)

        self.assertEqual(len(sent), 1)
        request, timeout = sent[0]
        payload = json.loads(request.data)
        content = payload['content']
        self.assertIn('SEOUL WAVE 배포 성공', content)
        self.assertIn('a' * 40, content)
        self.assertIn('123', content)
        self.assertIn('Success', content)
        self.assertNotIn(secret, content)
        self.assertNotIn('not-for-discord', content)
        self.assertEqual(payload['allowed_mentions'], {'parse': []})
        self.assertEqual(timeout, 20)

    def test_failure_notification_reports_safe_status_without_remote_error_text(self):
        self.assertIsNotNone(notify, 'scripts/deploy/notify.py has not been implemented')
        webhook = 'https://discord.com/api/webhooks/123456789012345678/' + 'x' * 32
        evidence = {
            'deployment_status': 'failure',
            'source_sha': 'b' * 40,
            'source_run_id': '124',
            'ssm_command_id': '9c4f29d0-5cc3-4c75-a880-35c6e4a99a21',
            'ssm_status': 'Failed',
            'failure_code': 'ssm_command_failed',
            'standard_error': 'password=hunter2 DATABASE_URL=private',
            'exception': 'token=private-token',
        }
        requests = []

        notify.send_notification(
            webhook, evidence,
            sender=lambda request, timeout: (requests.append(request) or FakeResponse()),
        )

        content = json.loads(requests[0].data)['content']
        self.assertIn('SEOUL WAVE 배포 실패', content)
        self.assertIn('Failed', content)
        self.assertIn('ssm_command_failed', content)
        for secret in ('hunter2', 'private', 'private-token'):
            self.assertNotIn(secret, content)

    def test_plugin_materialization_timeout_code_is_safe_to_notify(self):
        self.assertIsNotNone(notify, 'scripts/deploy/notify.py has not been implemented')
        webhook = 'https://discord.com/api/webhooks/123456789012345678/' + 'x' * 32
        requests = []

        notify.send_notification(
            webhook,
            {
                'deployment_status': 'failure',
                'source_sha': 'c' * 40,
                'source_run_id': '126',
                'ssm_status': 'poll_error',
                'failure_code': 'ssm_plugin_not_materialized',
            },
            job_status='failure',
            sender=lambda request, timeout: (requests.append(request) or FakeResponse()),
        )

        content = json.loads(requests[0].data)['content']
        self.assertIn('ssm_plugin_not_materialized', content)
        self.assertIn('poll_error', content)

    def test_invalid_webhook_destination_is_rejected_before_sending(self):
        self.assertIsNotNone(notify, 'scripts/deploy/notify.py has not been implemented')
        sent = []

        with self.assertRaises(ValueError):
            notify.send_notification(
                'https://attacker.example/api/webhooks/123456789012345678/' + 'x' * 32,
                {'deployment_status': 'failure'},
                sender=lambda request, timeout: sent.append(request),
            )

        self.assertEqual(sent, [])

    def test_failed_workflow_step_cannot_be_notified_as_success(self):
        self.assertIsNotNone(notify, 'scripts/deploy/notify.py has not been implemented')
        webhook = 'https://discord.com/api/webhooks/123456789012345678/' + 'x' * 32
        requests = []

        notify.send_notification(
            webhook,
            {'deployment_status': 'pending', 'source_run_id': '125'},
            job_status='failure',
            sender=lambda request, timeout: (requests.append(request) or FakeResponse()),
        )

        content = json.loads(requests[0].data)['content']
        self.assertIn('SEOUL WAVE 배포 실패', content)
        self.assertIn('workflow_step_failed', content)
        self.assertNotIn('배포 성공', content)

    def test_public_evidence_keeps_release_artifact_identity_but_drops_raw_output(self):
        self.assertIsNotNone(notify, 'scripts/deploy/notify.py has not been implemented')
        digest = 'sha256:' + 'd' * 64
        backend_image = '909345918351.dkr.ecr.ap-northeast-2.amazonaws.com/seoulwave@sha256:' + 'b' * 64
        frontend_image = '909345918351.dkr.ecr.ap-northeast-2.amazonaws.com/seoul-wave-frontend@sha256:' + 'c' * 64
        backend_config_id = 'sha256:' + 'e' * 64
        frontend_config_id = 'sha256:' + 'f' * 64
        http_checks = {
            'proxy_health': 'passed',
            'protected_api': 'passed',
            'login': 'passed',
            'timetable': 'passed',
        }
        evidence = notify.normalize_evidence({
            'deployment_status': 'success',
            'source_sha': 'a' * 40,
            'source_run_id': '123',
            'artifact_id': 900,
            'artifact_digest': digest,
            'backend_image': backend_image,
            'frontend_image': frontend_image,
            'backend_config_id': backend_config_id,
            'frontend_config_id': frontend_config_id,
            'http_checks': http_checks,
            'migration_dry_run': 'none',
            'ssm_command_id': '9c4f29d0-5cc3-4c75-a880-35c6e4a99a21',
            'ssm_status': 'Success',
            'remote_outcome': 'applied',
            'StandardOutputContent': 'secret=never-publish',
        })

        self.assertEqual(evidence.get('artifact_id'), 900)
        self.assertEqual(evidence.get('artifact_digest'), digest)
        self.assertEqual(evidence.get('backend_image'), backend_image)
        self.assertEqual(evidence.get('frontend_image'), frontend_image)
        self.assertEqual(evidence.get('backend_config_id'), backend_config_id)
        self.assertEqual(evidence.get('frontend_config_id'), frontend_config_id)
        self.assertEqual(evidence.get('http_checks'), http_checks)
        self.assertEqual(evidence.get('migration_dry_run'), 'none')
        self.assertNotIn('StandardOutputContent', evidence)
        self.assertNotIn('secret=never-publish', json.dumps(evidence))


if __name__ == '__main__':
    unittest.main()
