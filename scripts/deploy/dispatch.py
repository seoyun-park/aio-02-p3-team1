#!/usr/bin/env python3
"""Validate a verified CI release, dispatch the fixed SSM document, and poll it."""

from datetime import datetime, timezone
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import zipfile


DEPLOY_DIR = Path(__file__).resolve().parent
CI_DIR = DEPLOY_DIR.parent / 'ci'
for _directory in (str(CI_DIR), str(DEPLOY_DIR)):
    if _directory not in sys.path:
        sys.path.insert(0, _directory)

from contract import (  # noqa: E402
    AWS_REGION,
    EC2_INSTANCE_ID,
    KST,
    validate_request as validate_deploy_request,
)
from prepare_deployment import REPOSITORY, validate_release  # noqa: E402
from release_contract import verify_run  # noqa: E402


VERIFIED_WORKFLOW_NAME = 'Verified application pipeline'
VERIFIED_WORKFLOW_PATH = '.github/workflows/pipeline.yml'
RELEASE_ARTIFACT_NAME = 'verified-release'
SSM_DOCUMENT_NAME = 'SeoulWave-Deploy-v1'
SSM_DOCUMENT_VERSION = '1'
SOURCE_REPOSITORY = 'munchkin112/SEOUL-WAVE'
TERMINAL_STATUSES = {
    'Success', 'Cancelled', 'TimedOut', 'Failed', 'Undeliverable', 'Terminated',
    'DeliveryTimedOut', 'ExecutionTimedOut', 'Incomplete', 'InvalidPlatform',
    'AccessDenied', 'RateExceeded', 'NoInstancesInTag', 'LimitExceeded',
}
PENDING_STATUSES = {'Pending', 'InProgress', 'Delayed', 'Cancelling'}
REMOTE_OUTCOMES = {'applied', 'not_applied', 'rolled_back', 'rollback_failed'}
COMMAND_ID_PATTERN = re.compile(
    r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}'
)
ARTIFACT_DIGEST_PATTERN = re.compile(r'sha256:[0-9a-f]{64}')
MAX_RELEASE_JSON_BYTES = 1024 * 1024
MAX_POLL_SECONDS = 3900
MAX_INVOCATION_NOT_READY_SECONDS = 120


class DeploymentRejected(RuntimeError):
    """A release or event did not satisfy the immutable deployment contract."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


class DeploymentFailed(RuntimeError):
    """A command failed or did not produce evidence of an applied release."""

    def __init__(self, code, evidence):
        super().__init__(code)
        self.code = code
        self.evidence = evidence
        self.evidence.setdefault('failure_code', code)


class InvocationNotReady(RuntimeError):
    """SSM has accepted a command but has not materialized its invocation yet."""

    def __init__(self, reason='invocation_missing'):
        super().__init__(reason)
        self.reason = reason


class GitHubApiClient:
    """Small, read-only GitHub API client for the original workflow run."""

    def __init__(self, token, *, opener=urlopen):
        if not isinstance(token, str) or not token.strip():
            raise ValueError('GitHub Actions read token is required.')
        self.token = token.strip()
        self.opener = opener

    def _request(self, path):
        request = Request(
            f'https://api.github.com/repos/{SOURCE_REPOSITORY}/{path}',
            headers={
                'Accept': 'application/vnd.github+json',
                'User-Agent': 'SEOUL-WAVE-deploy/1.0',
                'X-GitHub-Api-Version': '2022-11-28',
            },
        )
        request.add_unredirected_header('Authorization', f'Bearer {self.token}')
        try:
            with self.opener(request, timeout=30) as response:
                return response.read()
        except (HTTPError, URLError, TimeoutError, OSError):
            raise RuntimeError('GitHub API request failed.') from None

    def _json(self, path):
        try:
            return json.loads(self._request(path).decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise RuntimeError('GitHub API returned invalid JSON.') from None

    def get_workflow_run(self, run_id):
        if not re.fullmatch(r'[0-9]{1,20}', str(run_id)):
            raise ValueError('Invalid workflow run ID.')
        return self._json(f'actions/runs/{run_id}')

    def list_run_artifacts(self, run_id):
        if not re.fullmatch(r'[0-9]{1,20}', str(run_id)):
            raise ValueError('Invalid workflow run ID.')
        artifacts = []
        page = 1
        while page <= 100:
            result = self._json(
                f'actions/runs/{run_id}/artifacts?per_page=100&page={page}'
            )
            page_items = result.get('artifacts')
            if not isinstance(page_items, list):
                raise RuntimeError('GitHub artifact listing is invalid.')
            artifacts.extend(page_items)
            total = result.get('total_count')
            if isinstance(total, int):
                if len(artifacts) >= total:
                    return artifacts
            elif len(page_items) < 100:
                return artifacts
            if not page_items:
                raise RuntimeError('GitHub artifact listing is incomplete.')
            page += 1
        raise RuntimeError('GitHub artifact listing exceeded its page limit.')

    def download_artifact(self, artifact_id):
        if isinstance(artifact_id, bool) or not re.fullmatch(r'[0-9]{1,20}', str(artifact_id)):
            raise ValueError('Invalid artifact ID.')
        return self._request(f'actions/artifacts/{artifact_id}/zip')

    def current_branch_head(self, branch):
        if branch != 'dev':
            raise ValueError('Only the dev branch may be checked for deployment.')
        result = self._json('commits/dev')
        head = result.get('sha')
        if not isinstance(head, str):
            raise RuntimeError('GitHub did not return the dev branch head.')
        return head


class AwsSsmClient:
    """Use the runner's AWS CLI credentials; never accepts a shell command."""

    def __init__(self, *, runner=subprocess.run):
        self.runner = runner

    def _json(self, arguments, *, invocation_poll=False):
        try:
            result = self.runner(
                ['aws', 'ssm', *arguments, '--output', 'json'],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise RuntimeError('AWS SSM request could not be completed.') from None
        if result.returncode != 0:
            if invocation_poll and 'InvalidPluginName' in (result.stderr or ''):
                raise InvocationNotReady('plugin_not_materialized')
            if invocation_poll and 'InvocationDoesNotExist' in (result.stderr or ''):
                raise InvocationNotReady()
            raise RuntimeError('AWS SSM request failed.')
        try:
            value = json.loads(result.stdout)
        except (TypeError, json.JSONDecodeError):
            raise RuntimeError('AWS SSM returned invalid JSON.') from None
        if not isinstance(value, dict):
            raise RuntimeError('AWS SSM returned an invalid response.')
        return value

    def send_command(self, **request):
        expected_keys = {
            'region', 'document_name', 'document_version', 'instance_ids', 'parameters',
        }
        expected_parameters = {
            'SourceSha', 'RunId', 'BackendImage', 'FrontendImage',
            'BackendConfigId', 'FrontendConfigId',
        }
        if (set(request) != expected_keys
                or request['region'] != AWS_REGION
                or request['document_name'] != SSM_DOCUMENT_NAME
                or request['document_version'] != SSM_DOCUMENT_VERSION
                or request['instance_ids'] != [EC2_INSTANCE_ID]
                or not isinstance(request['parameters'], dict)
                or set(request['parameters']) != expected_parameters):
            raise ValueError('SSM deployment boundary mismatch.')
        return self._json([
            'send-command',
            '--region', request['region'],
            '--document-name', request['document_name'],
            '--document-version', request['document_version'],
            '--instance-ids', *request['instance_ids'],
            '--parameters', json.dumps(request['parameters'], separators=(',', ':')),
        ])

    def get_command_invocation(self, command_id, instance_id):
        try:
            return self._get_command_step(command_id, instance_id, 'runFixedDeployment')
        except InvocationNotReady as not_ready:
            for plugin_name in ('downloadExactCommitRunner', 'prepareFixedDirectory'):
                try:
                    invocation = self._get_command_step(command_id, instance_id, plugin_name)
                except InvocationNotReady:
                    continue
                if invocation.get('Status') != 'Success':
                    return invocation
            raise not_ready

    def _get_command_step(self, command_id, instance_id, plugin_name):
        return self._json([
            'get-command-invocation',
            '--region', AWS_REGION,
            '--command-id', command_id,
            '--instance-id', instance_id,
            '--plugin-name', plugin_name,
        ], invocation_poll=True)


class DeploymentDispatcher:
    def __init__(self, *, github, ssm, now=lambda: datetime.now(timezone.utc),
                 sleep=time.sleep, monotonic=time.monotonic,
                 poll_interval_seconds=10, max_poll_seconds=MAX_POLL_SECONDS):
        self.github = github
        self.ssm = ssm
        self.now = now
        self.sleep = sleep
        self.monotonic = monotonic
        self.poll_interval_seconds = poll_interval_seconds
        self.max_poll_seconds = max_poll_seconds

    def dispatch(self, event):
        return self.execute(self.prepare(event))

    def prepare(self, event):
        run_id = self._event_run_id(event)
        run = self.github.get_workflow_run(run_id)
        self._require_trusted_run(run, run_id)
        now = self.now()
        artifact = self._select_release_artifact(
            self.github.list_run_artifacts(run_id), run_id, now,
        )
        archive = self.github.download_artifact(artifact['id'])
        manifest = self._read_release_manifest(archive, artifact['digest'])
        try:
            validate_release(manifest, run, run_id)
            current_head = self.github.current_branch_head('dev')
            request = validate_deploy_request(manifest, run, current_head, now)
        except (KeyError, TypeError, ValueError):
            raise DeploymentRejected('release_contract_rejected') from None
        return {
            'schema_version': 1,
            'source_run_id': run_id,
            'source_sha': run['head_sha'],
            'artifact_id': artifact['id'],
            'artifact_digest': artifact['digest'],
            'manifest': manifest,
            'request': request,
            'source_run_url': f'https://github.com/{SOURCE_REPOSITORY}/actions/runs/{run_id}',
        }

    def execute(self, prepared):
        self._validate_prepared(prepared)
        run_id = prepared['source_run_id']
        now = self.now()
        run = self.github.get_workflow_run(run_id)
        self._require_trusted_run(run, run_id)
        if run.get('head_sha') != prepared['source_sha']:
            raise DeploymentRejected('source_run_changed')
        artifact = self._select_release_artifact(
            self.github.list_run_artifacts(run_id), run_id, now,
        )
        if artifact['id'] != prepared['artifact_id'] or artifact['digest'] != prepared['artifact_digest']:
            raise DeploymentRejected('release_artifact_changed')
        try:
            current_head = self.github.current_branch_head('dev')
            request = validate_deploy_request(
                prepared['manifest'], run, current_head, self.now(),
            )
        except (KeyError, TypeError, ValueError):
            raise DeploymentRejected('release_contract_rejected') from None
        if request != prepared['request']:
            raise DeploymentRejected('release_request_changed')

        if self.ssm is None:
            raise DeploymentFailed(
                'ssm_client_unavailable', self._evidence(prepared, deployment_status='failure'),
            )
        try:
            response = self.ssm.send_command(**self._send_request(request))
        except Exception:
            raise DeploymentFailed(
                'ssm_dispatch_failed', self._evidence(prepared, deployment_status='failure'),
            ) from None
        command = response.get('Command') if isinstance(response, dict) else None
        command_id = command.get('CommandId') if isinstance(command, dict) else None
        if not isinstance(command_id, str) or COMMAND_ID_PATTERN.fullmatch(command_id) is None:
            evidence = self._evidence(prepared, deployment_status='failure')
            evidence['failure_code'] = 'ssm_command_id_missing'
            raise DeploymentFailed('ssm_command_id_missing', evidence)

        invocation = self._poll(command_id, request['instance_id'], prepared)
        status = invocation['Status']
        remote = self._remote_summary(invocation, request)
        evidence = self._evidence(
            prepared,
            deployment_status='success' if status == 'Success' else 'failure',
            command_id=command_id,
            ssm_status=status,
            remote=remote,
        )
        if status != 'Success':
            evidence['failure_code'] = 'ssm_command_failed'
            raise DeploymentFailed('ssm_command_failed', evidence)
        if remote is None or remote['outcome'] != 'applied':
            evidence['deployment_status'] = 'failure'
            evidence['failure_code'] = 'remote_deployment_not_applied'
            raise DeploymentFailed('remote_deployment_not_applied', evidence)
        return evidence

    @staticmethod
    def _event_run_id(event):
        if not isinstance(event, dict):
            raise DeploymentRejected('invalid_workflow_event')
        event_repository = event.get('repository')
        if (isinstance(event_repository, dict)
                and event_repository.get('full_name') != SOURCE_REPOSITORY):
            raise DeploymentRejected('wrong_event_repository')
        workflow_run = event.get('workflow_run')
        raw_id = workflow_run.get('id') if isinstance(workflow_run, dict) else None
        if (isinstance(raw_id, bool) or not re.fullmatch(r'[0-9]{1,20}', str(raw_id))):
            raise DeploymentRejected('invalid_workflow_run_id')
        return str(raw_id)

    @staticmethod
    def _require_trusted_run(run, run_id):
        if not isinstance(run, dict):
            raise DeploymentRejected('untrusted_workflow_run')
        try:
            verify_run(run, run.get('head_sha'), run_id)
        except (AttributeError, TypeError, ValueError):
            raise DeploymentRejected('untrusted_workflow_run') from None
        if (run.get('name') != VERIFIED_WORKFLOW_NAME
                or run.get('path') != VERIFIED_WORKFLOW_PATH):
            raise DeploymentRejected('wrong_workflow')
        head_repository = run.get('head_repository')
        if (head_repository is not None
                and (not isinstance(head_repository, dict)
                     or head_repository.get('full_name') != SOURCE_REPOSITORY)):
            raise DeploymentRejected('wrong_source_repository')

    def _select_release_artifact(self, artifacts, run_id, now):
        if not isinstance(artifacts, list):
            raise DeploymentRejected('invalid_artifact_listing')
        matches = [item for item in artifacts
                   if isinstance(item, dict) and item.get('name') == RELEASE_ARTIFACT_NAME]
        if len(matches) != 1:
            raise DeploymentRejected('release_artifact_not_unique')
        artifact = matches[0]
        if artifact.get('expired') is not False:
            raise DeploymentRejected('release_artifact_expired')
        workflow_run = artifact.get('workflow_run')
        if not isinstance(workflow_run, dict) or str(workflow_run.get('id')) != run_id:
            raise DeploymentRejected('release_artifact_wrong_run')
        try:
            expires_at = datetime.fromisoformat(
                artifact['expires_at'].replace('Z', '+00:00')
            )
        except (KeyError, TypeError, ValueError):
            raise DeploymentRejected('release_artifact_expiry_invalid') from None
        if expires_at.utcoffset() is None or expires_at <= now:
            raise DeploymentRejected('release_artifact_expired')
        digest = artifact.get('digest')
        if not isinstance(digest, str) or ARTIFACT_DIGEST_PATTERN.fullmatch(digest) is None:
            raise DeploymentRejected('release_artifact_digest_missing')
        artifact_id = artifact.get('id')
        if (isinstance(artifact_id, bool)
                or not re.fullmatch(r'[0-9]{1,20}', str(artifact_id))):
            raise DeploymentRejected('release_artifact_id_invalid')
        return artifact

    @staticmethod
    def _read_release_manifest(archive_bytes, expected_digest):
        if not isinstance(archive_bytes, bytes):
            raise DeploymentRejected('release_artifact_download_invalid')
        actual_digest = 'sha256:' + hashlib.sha256(archive_bytes).hexdigest()
        if actual_digest != expected_digest:
            raise DeploymentRejected('release_artifact_digest_mismatch')
        try:
            with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
                members = [member for member in archive.infolist()
                           if member.filename == 'release.json']
                if len(members) != 1 or members[0].file_size > MAX_RELEASE_JSON_BYTES:
                    raise DeploymentRejected('release_manifest_missing_or_ambiguous')
                manifest = json.loads(archive.read(members[0]).decode('utf-8'))
        except DeploymentRejected:
            raise
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, zipfile.BadZipFile):
            raise DeploymentRejected('release_artifact_invalid') from None
        if not isinstance(manifest, dict):
            raise DeploymentRejected('release_manifest_invalid')
        return manifest

    @staticmethod
    def _send_request(request):
        return {
            'region': request['region'],
            'document_name': SSM_DOCUMENT_NAME,
            'document_version': SSM_DOCUMENT_VERSION,
            'instance_ids': [request['instance_id']],
            'parameters': {
                'SourceSha': [request['source_sha']],
                'RunId': [request['run_id']],
                'BackendImage': [request['backend_image']],
                'FrontendImage': [request['frontend_image']],
                'BackendConfigId': [request['backend_config_id']],
                'FrontendConfigId': [request['frontend_config_id']],
            },
        }

    def _poll(self, command_id, instance_id, prepared):
        deadline = self.monotonic() + self.max_poll_seconds
        not_ready_since = None
        while True:
            try:
                invocation = self.ssm.get_command_invocation(command_id, instance_id)
            except InvocationNotReady as not_ready:
                if not_ready_since is None:
                    not_ready_since = self.monotonic()
                if (self.monotonic() - not_ready_since
                        >= MAX_INVOCATION_NOT_READY_SECONDS):
                    is_plugin_missing = not_ready.reason == 'plugin_not_materialized'
                    failure_code = (
                        'ssm_plugin_not_materialized' if is_plugin_missing
                        else 'ssm_poll_timeout'
                    )
                    evidence = self._evidence(
                        prepared, deployment_status='failure', command_id=command_id,
                        ssm_status='poll_error' if is_plugin_missing else 'poll_timeout',
                    )
                    evidence['failure_code'] = failure_code
                    raise DeploymentFailed(failure_code, evidence) from None
                invocation = {'Status': 'Pending'}
            except Exception:
                evidence = self._evidence(
                    prepared, deployment_status='failure', command_id=command_id,
                    ssm_status='poll_error',
                )
                evidence['failure_code'] = 'ssm_poll_failed'
                raise DeploymentFailed('ssm_poll_failed', evidence) from None
            else:
                not_ready_since = None
            status = invocation.get('Status') if isinstance(invocation, dict) else None
            if status in TERMINAL_STATUSES:
                return invocation
            if status not in PENDING_STATUSES:
                evidence = self._evidence(
                    prepared, deployment_status='failure', command_id=command_id,
                    ssm_status='unknown',
                )
                evidence['failure_code'] = 'ssm_status_unknown'
                raise DeploymentFailed('ssm_status_unknown', evidence)
            remaining = deadline - self.monotonic()
            if remaining <= 0:
                evidence = self._evidence(
                    prepared, deployment_status='failure', command_id=command_id,
                    ssm_status='poll_timeout',
                )
                evidence['failure_code'] = 'ssm_poll_timeout'
                raise DeploymentFailed('ssm_poll_timeout', evidence)
            self.sleep(min(self.poll_interval_seconds, remaining))

    @staticmethod
    def _remote_summary(invocation, request):
        output = invocation.get('StandardOutputContent')
        if not isinstance(output, str) or len(output) > MAX_RELEASE_JSON_BYTES:
            return None
        try:
            result = json.loads(output)
        except json.JSONDecodeError:
            return None
        if (not isinstance(result, dict)
                or result.get('source_sha') != request['source_sha']
                or str(result.get('run_id')) != request['run_id']
                or result.get('outcome') not in REMOTE_OUTCOMES):
            return None
        reason = result.get('reason')
        safe_reason = reason if isinstance(reason, str) and re.fullmatch(r'[a-z0-9_]{1,64}', reason) else None
        summary = {'outcome': result['outcome'], 'reason': safe_reason}
        if result['outcome'] == 'applied':
            checks = result.get('checks')
            expected_http_checks = {
                'proxy_health': 'passed',
                'protected_api': 'passed',
                'login': 'passed',
                'timetable': 'passed',
            }
            if (result.get('schema_version') != 1
                    or result.get('backend_config_id') != request['backend_config_id']
                    or result.get('frontend_config_id') != request['frontend_config_id']
                    or not isinstance(checks, dict)
                    or checks.get('config_ids') != 'passed'
                    or checks.get('migration_dry_run') != 'none'
                    or checks.get('http') != expected_http_checks):
                return None
            summary.update({
                'backend_config_id': result['backend_config_id'],
                'frontend_config_id': result['frontend_config_id'],
                'http_checks': expected_http_checks,
                'migration_dry_run': 'none',
            })
        return summary

    @staticmethod
    def _evidence(prepared, *, deployment_status, command_id=None,
                  ssm_status='not_sent', remote=None):
        evidence = {
            'schema_version': 1,
            'deployment_status': deployment_status,
            'source_sha': prepared['source_sha'],
            'source_run_id': prepared['source_run_id'],
            'source_run_url': prepared['source_run_url'],
            'artifact_id': prepared['artifact_id'],
            'artifact_digest': prepared['artifact_digest'],
            'ssm_command_id': command_id,
            'ssm_status': ssm_status,
        }
        request = prepared.get('request')
        if isinstance(request, dict):
            evidence['backend_image'] = request.get('backend_image')
            evidence['frontend_image'] = request.get('frontend_image')
        if remote is not None:
            evidence['remote_outcome'] = remote['outcome']
            if remote['reason'] is not None:
                evidence['remote_reason'] = remote['reason']
            for key in (
                'backend_config_id', 'frontend_config_id', 'http_checks',
                'migration_dry_run',
            ):
                if key in remote:
                    evidence[key] = remote[key]
        return evidence

    @staticmethod
    def _validate_prepared(prepared):
        expected = {
            'schema_version', 'source_run_id', 'source_sha', 'artifact_id',
            'artifact_digest', 'manifest', 'request', 'source_run_url',
        }
        if not isinstance(prepared, dict) or set(prepared) != expected:
            raise DeploymentRejected('prepared_release_invalid')
        if (prepared['schema_version'] != 1
                or not re.fullmatch(r'[0-9]{1,20}', str(prepared['source_run_id']))
                or not re.fullmatch(r'[0-9a-f]{40}', str(prepared['source_sha']))
                or not ARTIFACT_DIGEST_PATTERN.fullmatch(str(prepared['artifact_digest']))):
            raise DeploymentRejected('prepared_release_invalid')


def _write_json(path, value):
    Path(path).write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n',
        encoding='utf-8',
    )


def build_rejection_evidence(event, code, *, prepared=None):
    workflow_run = event.get('workflow_run') if isinstance(event, dict) else None
    workflow_run = workflow_run if isinstance(workflow_run, dict) else {}
    run_id = str(workflow_run.get('id', ''))
    source_sha = workflow_run.get('head_sha')
    if not re.fullmatch(r'[0-9]{1,20}', run_id):
        run_id = None
    if not isinstance(source_sha, str) or not re.fullmatch(r'[0-9a-f]{40}', source_sha):
        source_sha = None
    evidence = {
        'schema_version': 1,
        'deployment_status': 'failure',
        'source_sha': source_sha,
        'source_run_id': run_id,
        'source_run_url': (
            f'https://github.com/{SOURCE_REPOSITORY}/actions/runs/{run_id}'
            if run_id else None
        ),
        'artifact_id': None,
        'artifact_digest': None,
        'ssm_command_id': None,
        'ssm_status': 'not_sent',
        'failure_code': code,
    }
    if isinstance(prepared, dict):
        prepared_run_id = str(prepared.get('source_run_id', ''))
        prepared_sha = prepared.get('source_sha')
        artifact_id = prepared.get('artifact_id')
        artifact_digest = prepared.get('artifact_digest')
        if re.fullmatch(r'[0-9]{1,20}', prepared_run_id):
            evidence['source_run_id'] = prepared_run_id
            evidence['source_run_url'] = (
                f'https://github.com/{SOURCE_REPOSITORY}/actions/runs/{prepared_run_id}'
            )
        if isinstance(prepared_sha, str) and re.fullmatch(r'[0-9a-f]{40}', prepared_sha):
            evidence['source_sha'] = prepared_sha
        if (not isinstance(artifact_id, bool)
                and re.fullmatch(r'[0-9]{1,20}', str(artifact_id))):
            evidence['artifact_id'] = int(artifact_id)
        if isinstance(artifact_digest, str) and ARTIFACT_DIGEST_PATTERN.fullmatch(artifact_digest):
            evidence['artifact_digest'] = artifact_digest
    return evidence


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    prepare = commands.add_parser('prepare', help='validate a workflow_run and release artifact')
    prepare.add_argument('--event', type=Path, default=Path(os.environ.get('GITHUB_EVENT_PATH', 'event.json')))
    prepare.add_argument('--request', type=Path, default=Path('deployment-request.json'))
    prepare.add_argument('--evidence', type=Path, default=Path('deployment-evidence.json'))
    send = commands.add_parser('send', help='recheck the dev head, dispatch SSM, and poll it')
    send.add_argument('--request', type=Path, default=Path('deployment-request.json'))
    send.add_argument('--evidence', type=Path, default=Path('deployment-evidence.json'))
    args = parser.parse_args(argv)

    event = {}
    prepared = None
    evidence_path = args.evidence
    try:
        token = os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN')
        github = GitHubApiClient(token)
        if args.command == 'prepare':
            event = json.loads(args.event.read_text(encoding='utf-8'))
            dispatcher = DeploymentDispatcher(github=github, ssm=None)
            prepared = dispatcher.prepare(event)
            _write_json(args.request, prepared)
            evidence = DeploymentDispatcher._evidence(prepared, deployment_status='pending')
            _write_json(evidence_path, evidence)
            print('Verified release preflight passed; no AWS credentials were requested.')
            return 0

        prepared = json.loads(args.request.read_text(encoding='utf-8'))
        if isinstance(prepared, dict):
            event = {'workflow_run': {
                'id': prepared.get('source_run_id'),
                'head_sha': prepared.get('source_sha'),
            }}
        dispatcher = DeploymentDispatcher(github=github, ssm=AwsSsmClient())
        try:
            evidence = dispatcher.execute(prepared)
        except DeploymentFailed as error:
            _write_json(evidence_path, error.evidence)
            print(f'Deployment failed: {error.code}')
            return 1
        _write_json(evidence_path, evidence)
        print(f"Deployment completed: SSM status {evidence['ssm_status']}.")
        return 0
    except DeploymentRejected as error:
        _write_json(evidence_path, build_rejection_evidence(event, error.code, prepared=prepared))
        print(f'Deployment preflight rejected: {error.code}')
        return 1
    except Exception:
        _write_json(
            evidence_path,
            build_rejection_evidence(event, 'dispatch_unexpected_failure', prepared=prepared),
        )
        print('Deployment dispatcher failed; details were not written to logs.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
