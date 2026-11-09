#!/usr/bin/env python3
"""Send a sanitized deployment outcome to the existing Discord webhook."""

import argparse
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.discord_ack import notify_failure


WEBHOOK_PATH = re.compile(r'/api/(?:v[0-9]+/)?webhooks/[0-9]{17,20}/[A-Za-z0-9._-]+')
SHA_PATTERN = re.compile(r'[0-9a-f]{40}')
RUN_ID_PATTERN = re.compile(r'[0-9]{1,20}')
ARTIFACT_ID_PATTERN = re.compile(r'[0-9]{1,20}')
ARTIFACT_DIGEST_PATTERN = re.compile(r'sha256:[0-9a-f]{64}')
BACKEND_IMAGE_PATTERN = re.compile(
    r'909345918351\.dkr\.ecr\.ap-northeast-2\.amazonaws\.com/seoulwave@sha256:[0-9a-f]{64}'
)
FRONTEND_IMAGE_PATTERN = re.compile(
    r'909345918351\.dkr\.ecr\.ap-northeast-2\.amazonaws\.com/seoul-wave-frontend@sha256:[0-9a-f]{64}'
)
CONFIG_ID_PATTERN = re.compile(r'sha256:[0-9a-f]{64}')
COMMAND_ID_PATTERN = re.compile(
    r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}'
)
SAFE_FAILURE_CODES = {
    'workflow_step_failed', 'dispatch_unexpected_failure', 'invalid_workflow_event',
    'wrong_event_repository', 'invalid_workflow_run_id', 'untrusted_workflow_run',
    'wrong_workflow', 'wrong_source_repository', 'release_artifact_not_unique',
    'release_artifact_expired', 'release_artifact_wrong_run',
    'release_artifact_expiry_invalid', 'release_artifact_digest_missing',
    'release_artifact_id_invalid', 'release_artifact_download_invalid',
    'release_artifact_digest_mismatch', 'release_manifest_missing_or_ambiguous',
    'release_artifact_invalid', 'release_manifest_invalid', 'release_contract_rejected',
    'source_run_changed', 'release_artifact_changed', 'release_request_changed',
    'prepared_release_invalid', 'ssm_client_unavailable', 'ssm_dispatch_failed',
    'ssm_command_id_missing', 'ssm_poll_failed', 'ssm_poll_timeout',
    'ssm_plugin_not_materialized',
    'ssm_status_unknown', 'ssm_command_failed', 'remote_deployment_not_applied',
}
SAFE_SSM_STATUSES = {
    'not_sent', 'poll_error', 'poll_timeout', 'unknown', 'Pending', 'InProgress',
    'Delayed', 'Cancelling', 'Success', 'Cancelled', 'TimedOut', 'Failed',
    'Undeliverable', 'Terminated', 'DeliveryTimedOut', 'ExecutionTimedOut',
    'Incomplete', 'InvalidPlatform', 'AccessDenied', 'RateExceeded',
    'NoInstancesInTag', 'LimitExceeded',
}


def normalize_evidence(evidence, *, job_status='success'):
    """Reduce evidence to public fields and never let a failed job claim success."""
    if not isinstance(evidence, dict):
        evidence = {}
    status = evidence.get('deployment_status')
    failure_code = evidence.get('failure_code')
    if job_status != 'success' and status != 'failure':
        status = 'failure'
        failure_code = 'workflow_step_failed'
    if not isinstance(status, str) or status not in {'success', 'failure'}:
        status = 'failure'
        failure_code = failure_code or 'workflow_step_failed'
    run_id = str(evidence.get('source_run_id', ''))
    source_sha = evidence.get('source_sha')
    command_id = evidence.get('ssm_command_id')
    remote_outcome = evidence.get('remote_outcome')
    artifact_id = evidence.get('artifact_id')
    if isinstance(artifact_id, bool) or not ARTIFACT_ID_PATTERN.fullmatch(str(artifact_id)):
        artifact_id = None
    artifact_digest = evidence.get('artifact_digest')
    if (not isinstance(artifact_digest, str)
            or ARTIFACT_DIGEST_PATTERN.fullmatch(artifact_digest) is None):
        artifact_digest = None
    ssm_status = evidence.get('ssm_status')
    if not isinstance(ssm_status, str) or ssm_status not in SAFE_SSM_STATUSES:
        ssm_status = 'unknown'
    if not isinstance(remote_outcome, str) or remote_outcome not in {
        'applied', 'not_applied', 'rolled_back', 'rollback_failed',
    }:
        remote_outcome = None
    backend_image = evidence.get('backend_image')
    if (not isinstance(backend_image, str)
            or BACKEND_IMAGE_PATTERN.fullmatch(backend_image) is None):
        backend_image = None
    frontend_image = evidence.get('frontend_image')
    if (not isinstance(frontend_image, str)
            or FRONTEND_IMAGE_PATTERN.fullmatch(frontend_image) is None):
        frontend_image = None
    backend_config_id = evidence.get('backend_config_id')
    if (not isinstance(backend_config_id, str)
            or CONFIG_ID_PATTERN.fullmatch(backend_config_id) is None):
        backend_config_id = None
    frontend_config_id = evidence.get('frontend_config_id')
    if (not isinstance(frontend_config_id, str)
            or CONFIG_ID_PATTERN.fullmatch(frontend_config_id) is None):
        frontend_config_id = None
    expected_http_check_names = {
        'proxy_health', 'protected_api', 'login', 'timetable',
    }
    raw_http_checks = evidence.get('http_checks')
    http_checks = None
    if (isinstance(raw_http_checks, dict)
            and set(raw_http_checks) == expected_http_check_names
            and all(value == 'passed' for value in raw_http_checks.values())):
        http_checks = {name: 'passed' for name in sorted(expected_http_check_names)}
    migration_dry_run = evidence.get('migration_dry_run')
    if migration_dry_run != 'none':
        migration_dry_run = None
    if not isinstance(failure_code, str) or failure_code not in SAFE_FAILURE_CODES:
        failure_code = 'deployment_failed' if status == 'failure' else None
    return {
        'schema_version': 1,
        'deployment_status': status,
        'source_sha': source_sha if isinstance(source_sha, str) and SHA_PATTERN.fullmatch(source_sha) else None,
        'source_run_id': run_id if RUN_ID_PATTERN.fullmatch(run_id) else None,
        'artifact_id': int(artifact_id) if artifact_id is not None else None,
        'artifact_digest': artifact_digest,
        'ssm_command_id': (
            command_id if isinstance(command_id, str)
            and COMMAND_ID_PATTERN.fullmatch(command_id) else None
        ),
        'ssm_status': ssm_status,
        'remote_outcome': remote_outcome,
        'failure_code': failure_code,
        'backend_image': backend_image,
        'frontend_image': frontend_image,
        'backend_config_id': backend_config_id,
        'frontend_config_id': frontend_config_id,
        'http_checks': http_checks,
        'migration_dry_run': migration_dry_run,
    }


def build_payload(evidence, *, job_status='success'):
    safe = normalize_evidence(evidence, job_status=job_status)
    run_url = (
        f"https://github.com/munchkin112/SEOUL-WAVE/actions/runs/{safe['source_run_id']}"
        if safe['source_run_id'] else 'Source run: unavailable'
    )
    source_sha = safe['source_sha'] or 'unavailable'
    command_id = safe['ssm_command_id'] or 'not dispatched'
    if safe['deployment_status'] == 'success':
        lines = [
            'SEOUL WAVE 배포 성공',
            f'Commit: {source_sha}',
            f'Source run: {run_url}',
            f'SSM command: {command_id}',
            f"SSM status: {safe['ssm_status']}",
            f"Outcome: {safe['remote_outcome'] or 'applied'}",
        ]
    else:
        lines = [
            'SEOUL WAVE 배포 실패',
            f'Commit: {source_sha}',
            f'Source run: {run_url}',
            f'SSM command: {command_id}',
            f"SSM status: {safe['ssm_status']}",
            f"Failure: {safe['failure_code']}",
        ]
    return {'content': '\n'.join(lines), 'allowed_mentions': {'parse': []}}


def send_notification(webhook, evidence, *, job_status='success', sender=urlopen):
    safe_evidence = normalize_evidence(evidence, job_status=job_status)
    payload = build_payload(safe_evidence, job_status=job_status)
    if safe_evidence['deployment_status'] == 'failure':
        deploy_id = os.getenv('DISCORD_DEPLOY_ID', '').strip()
        agent_id = os.getenv('DISCORD_AGENT_ID', '').strip()
        if re.fullmatch(r'[0-9]{17,20}', deploy_id):
            payload['content'] += f'\n<@{deploy_id}>'
            payload['allowed_mentions'] = {'parse': [], 'users': [deploy_id]}
        status, ack_state = notify_failure(
            webhook, payload,
            ack_user_ids=[deploy_id] if deploy_id else [],
            escalation_user_id=agent_id,
            bot_token=os.getenv('DISCORD_BOT_TOKEN', '').strip(),
            channel_id=os.getenv('DISCORD_CHANNEL_ID', '').strip(),
            sender=sender,
        )
        print(f'Discord response: {status}; acknowledgement: {ack_state}')
        return status

    url = urlsplit(webhook or '')
    if (url.scheme != 'https' or url.hostname not in {'discord.com', 'discordapp.com'}
            or url.username or url.password or url.query or url.fragment
            or WEBHOOK_PATH.fullmatch(url.path) is None):
        raise ValueError('Invalid Discord webhook configuration.')
    request = Request(
        webhook,
        data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
        method='POST',
        headers={
            'Content-Type': 'application/json',
            'User-Agent': 'SEOUL-WAVE-DEPLOY/1.0',
        },
    )
    try:
        with sender(request, timeout=20) as response:
            if not 200 <= response.status < 300:
                raise RuntimeError('Discord notification failed.')
            return response.status
    except Exception:
        raise RuntimeError('Discord notification failed.') from None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', type=Path, default=Path('deployment-evidence.json'))
    parser.add_argument('--job-status', default=os.environ.get('JOB_STATUS', 'success'))
    args = parser.parse_args(argv)
    try:
        evidence = json.loads(args.evidence.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        evidence = {'deployment_status': 'failure', 'failure_code': 'workflow_step_failed'}
    safe_evidence = normalize_evidence(evidence, job_status=args.job_status)
    try:
        args.evidence.write_text(
            json.dumps(safe_evidence, ensure_ascii=False, sort_keys=True) + '\n',
            encoding='utf-8',
        )
    except OSError:
        print('Deployment evidence could not be normalized.')
        return 1

    webhook = os.getenv('DISCORD_WEBHOOK_URL', '').strip()
    if not webhook:
        print('Discord secret unavailable; deployment notification was not sent.')
        return 1
    try:
        status = send_notification(webhook, safe_evidence, job_status=args.job_status)
    except (RuntimeError, ValueError):
        print('Deployment notification failed; request details were not written to logs.')
        return 1
    print(f'Discord deployment notification sent: {status}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
