import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import importlib.util
import io
import json
from pathlib import Path
import threading
import unittest
from types import SimpleNamespace
from urllib.request import Request, urlopen
import zipfile


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DISPATCH_PATH = REPOSITORY_ROOT / 'scripts/deploy/dispatch.py'
if DISPATCH_PATH.is_file():
    spec = importlib.util.spec_from_file_location('deploy_dispatch', DISPATCH_PATH)
    dispatch = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dispatch)
else:
    dispatch = None


RUN_ID = '123'
SOURCE_SHA = 'a' * 40
REGISTRY = '909345918351.dkr.ecr.ap-northeast-2.amazonaws.com'


def make_run(**changes):
    run = {
        'id': int(RUN_ID),
        'name': 'Verified application pipeline',
        'path': '.github/workflows/pipeline.yml',
        'head_sha': SOURCE_SHA,
        'status': 'completed',
        'conclusion': 'success',
        'event': 'push',
        'head_branch': 'dev',
        'repository': {'full_name': 'munchkin112/SEOUL-WAVE'},
        'head_repository': {'full_name': 'munchkin112/SEOUL-WAVE'},
    }
    run.update(changes)
    return run


def make_manifest():
    return {
        'schema_version': 1,
        'source_sha': SOURCE_SHA,
        'run_id': RUN_ID,
        'required_checks': {
            'backend': 'success', 'frontend': 'success', 'integration': 'success',
        },
        'images': {
            'backend': {
                'image_id': 'sha256:' + 'b' * 64,
                'reference': f'{REGISTRY}/seoulwave@sha256:' + 'd' * 64,
            },
            'frontend': {
                'image_id': 'sha256:' + 'c' * 64,
                'reference': f'{REGISTRY}/seoul-wave-frontend@sha256:' + 'e' * 64,
            },
        },
    }


def make_archive(manifest=None):
    archive_bytes = io.BytesIO()
    with zipfile.ZipFile(archive_bytes, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('release.json', json.dumps(manifest or make_manifest()).encode())
    return archive_bytes.getvalue()


class FakeGitHub:
    def __init__(self, *, run=None, artifacts=None, archive=None, current_head=SOURCE_SHA):
        self.run = run or make_run()
        self.archive = archive if archive is not None else make_archive()
        self.artifacts = artifacts
        if self.artifacts is None:
            self.artifacts = [{
                'id': 900,
                'name': 'verified-release',
                'expired': False,
                'expires_at': '2026-12-07T00:00:00Z',
                'digest': 'sha256:' + hashlib.sha256(self.archive).hexdigest(),
                'workflow_run': {'id': int(RUN_ID)},
            }]
        self.current_head = current_head
        self.run_requests = []
        self.artifact_requests = []
        self.download_requests = []
        self.head_requests = []

    def get_workflow_run(self, run_id):
        self.run_requests.append(str(run_id))
        return self.run

    def list_run_artifacts(self, run_id):
        self.artifact_requests.append(str(run_id))
        return self.artifacts

    def download_artifact(self, artifact_id):
        self.download_requests.append(artifact_id)
        return self.archive

    def current_branch_head(self, branch):
        self.head_requests.append(branch)
        return self.current_head


class FakeSsm:
    def __init__(self, invocations=None):
        self.send_calls = []
        self.poll_calls = []
        self.invocations = list(invocations or [{
            'Status': 'Success',
            'StandardOutputContent': json.dumps({
                'schema_version': 1,
                'outcome': 'applied',
                'reason': 'verified',
                'source_sha': SOURCE_SHA,
                'run_id': RUN_ID,
                'backend_config_id': 'sha256:' + 'b' * 64,
                'frontend_config_id': 'sha256:' + 'c' * 64,
                'checks': {
                    'config_ids': 'passed',
                    'migration_dry_run': 'none',
                    'http': {
                        'proxy_health': 'passed',
                        'protected_api': 'passed',
                        'login': 'passed',
                        'timetable': 'passed',
                    },
                },
            }),
        }])

    def send_command(self, **kwargs):
        self.send_calls.append(kwargs)
        return {'Command': {'CommandId': '9c4f29d0-5cc3-4c75-a880-35c6e4a99a21'}}

    def get_command_invocation(self, command_id, instance_id):
        self.poll_calls.append((command_id, instance_id))
        if len(self.invocations) > 1:
            return self.invocations.pop(0)
        return self.invocations[0]


def make_dispatcher(github, ssm):
    return dispatch.DeploymentDispatcher(
        github=github,
        ssm=ssm,
        now=lambda: dispatch.datetime(2026, 11, 7, 9, 0, tzinfo=dispatch.KST),
        sleep=lambda _seconds: None,
        poll_interval_seconds=0,
    )


class DeploymentDispatchTests(unittest.TestCase):
    def test_window_is_rechecked_after_github_calls_before_ssm_send(self):
        self.assertIsNotNone(dispatch, 'scripts/deploy/dispatch.py has not been implemented')
        times = iter((
            dispatch.datetime(2026, 11, 7, 17, 0, tzinfo=dispatch.KST),
            dispatch.datetime(2026, 11, 7, 17, 59, tzinfo=dispatch.KST),
            dispatch.datetime(2026, 11, 7, 18, 0, tzinfo=dispatch.KST),
        ))
        github = FakeGitHub()
        ssm = FakeSsm()
        dispatcher = dispatch.DeploymentDispatcher(
            github=github,
            ssm=ssm,
            now=lambda: next(times),
            sleep=lambda _seconds: None,
            poll_interval_seconds=0,
        )

        with self.assertRaises(dispatch.DeploymentRejected):
            dispatcher.dispatch({'workflow_run': {'id': int(RUN_ID)}})

        self.assertEqual(github.head_requests, ['dev', 'dev'])
        self.assertEqual(ssm.send_calls, [])

    def test_github_token_is_not_forwarded_to_cross_host_artifact_redirect(self):
        self.assertIsNotNone(dispatch, 'scripts/deploy/dispatch.py has not been implemented')
        received = {}

        class RedirectHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == '/api':
                    received['api_authorization'] = self.headers.get('Authorization')
                    self.send_response(302)
                    self.send_header(
                        'Location', f'http://localhost:{self.server.server_port}/artifact',
                    )
                    self.end_headers()
                    return
                received['artifact_authorization'] = self.headers.get('Authorization')
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'archive')

            def log_message(self, *_args):
                return

        server = HTTPServer(('127.0.0.1', 0), RedirectHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def loopback_opener(request, *, timeout):
            local_request = Request(
                f'http://127.0.0.1:{server.server_port}/api',
            )
            local_request.headers = dict(request.headers)
            local_request.unredirected_hdrs = dict(request.unredirected_hdrs)
            return urlopen(local_request, timeout=timeout)

        try:
            client = dispatch.GitHubApiClient('test-token', opener=loopback_opener)
            self.assertEqual(client._request('actions/artifacts/123/zip'), b'archive')
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        self.assertEqual(received['api_authorization'], 'Bearer test-token')
        self.assertIsNone(received['artifact_authorization'])

    def test_failed_original_workflow_is_rejected_before_artifact_or_ssm_access(self):
        self.assertIsNotNone(dispatch, 'scripts/deploy/dispatch.py has not been implemented')
        github = FakeGitHub(run=make_run(conclusion='failure'))
        ssm = FakeSsm()

        with self.assertRaises(dispatch.DeploymentRejected):
            make_dispatcher(github, ssm).dispatch({'workflow_run': {'id': int(RUN_ID)}})

        self.assertEqual(github.artifact_requests, [])
        self.assertEqual(ssm.send_calls, [])

    def test_cancelled_pr_wrong_repository_and_wrong_workflow_are_rejected(self):
        self.assertIsNotNone(dispatch, 'scripts/deploy/dispatch.py has not been implemented')
        untrusted_runs = (
            make_run(conclusion='cancelled'),
            make_run(event='pull_request'),
            make_run(repository={'full_name': 'attacker/SEOUL-WAVE'}),
            make_run(path='.github/workflows/untrusted.yml'),
            make_run(name='Lookalike pipeline'),
        )

        for run in untrusted_runs:
            with self.subTest(run=run):
                github = FakeGitHub(run=run)
                ssm = FakeSsm()
                with self.assertRaises(dispatch.DeploymentRejected):
                    make_dispatcher(github, ssm).dispatch(
                        {'workflow_run': {'id': int(RUN_ID)}}
                    )
                self.assertEqual(github.artifact_requests, [])
                self.assertEqual(ssm.send_calls, [])

    def test_stale_dev_head_is_rejected_before_ssm_send(self):
        self.assertIsNotNone(dispatch, 'scripts/deploy/dispatch.py has not been implemented')
        github = FakeGitHub(current_head='f' * 40)
        ssm = FakeSsm()

        with self.assertRaises(dispatch.DeploymentRejected):
            make_dispatcher(github, ssm).dispatch({'workflow_run': {'id': int(RUN_ID)}})

        self.assertEqual(github.head_requests, ['dev'])
        self.assertEqual(ssm.send_calls, [])

    def test_missing_verified_release_is_rejected(self):
        self.assertIsNotNone(dispatch, 'scripts/deploy/dispatch.py has not been implemented')
        github = FakeGitHub(artifacts=[])
        ssm = FakeSsm()

        with self.assertRaises(dispatch.DeploymentRejected):
            make_dispatcher(github, ssm).dispatch({'workflow_run': {'id': int(RUN_ID)}})

        self.assertEqual(github.artifact_requests, [RUN_ID])
        self.assertEqual(ssm.send_calls, [])

    def test_duplicate_verified_release_is_rejected_even_when_only_one_is_unexpired(self):
        self.assertIsNotNone(dispatch, 'scripts/deploy/dispatch.py has not been implemented')
        github = FakeGitHub()
        github.artifacts.append({**github.artifacts[0], 'id': 901, 'expired': True})
        ssm = FakeSsm()

        with self.assertRaises(dispatch.DeploymentRejected):
            make_dispatcher(github, ssm).dispatch({'workflow_run': {'id': int(RUN_ID)}})

        self.assertEqual(ssm.send_calls, [])

    def test_expired_verified_release_is_rejected(self):
        self.assertIsNotNone(dispatch, 'scripts/deploy/dispatch.py has not been implemented')
        github = FakeGitHub()
        github.artifacts[0]['expired'] = True
        ssm = FakeSsm()

        with self.assertRaises(dispatch.DeploymentRejected):
            make_dispatcher(github, ssm).dispatch({'workflow_run': {'id': int(RUN_ID)}})

        self.assertEqual(github.download_requests, [])
        self.assertEqual(ssm.send_calls, [])

    def test_expiry_timestamp_past_now_is_rejected_even_if_api_flag_is_false(self):
        self.assertIsNotNone(dispatch, 'scripts/deploy/dispatch.py has not been implemented')
        github = FakeGitHub()
        github.artifacts[0]['expires_at'] = '2026-11-06T23:59:59Z'
        ssm = FakeSsm()

        with self.assertRaises(dispatch.DeploymentRejected):
            make_dispatcher(github, ssm).dispatch({'workflow_run': {'id': int(RUN_ID)}})

        self.assertEqual(github.download_requests, [])
        self.assertEqual(ssm.send_calls, [])

    def test_artifact_digest_mismatch_is_rejected_before_ssm_send(self):
        self.assertIsNotNone(dispatch, 'scripts/deploy/dispatch.py has not been implemented')
        github = FakeGitHub()
        github.artifacts[0]['digest'] = 'sha256:' + '0' * 64
        ssm = FakeSsm()

        with self.assertRaises(dispatch.DeploymentRejected):
            make_dispatcher(github, ssm).dispatch({'workflow_run': {'id': int(RUN_ID)}})

        self.assertEqual(github.download_requests, [900])
        self.assertEqual(ssm.send_calls, [])

    def test_artifact_from_another_run_is_rejected(self):
        self.assertIsNotNone(dispatch, 'scripts/deploy/dispatch.py has not been implemented')
        github = FakeGitHub()
        github.artifacts[0]['workflow_run'] = {'id': 456}
        ssm = FakeSsm()

        with self.assertRaises(dispatch.DeploymentRejected):
            make_dispatcher(github, ssm).dispatch({'workflow_run': {'id': int(RUN_ID)}})

        self.assertEqual(github.download_requests, [])
        self.assertEqual(ssm.send_calls, [])

    def test_send_command_uses_the_fixed_document_version_parameters_and_instance(self):
        self.assertIsNotNone(dispatch, 'scripts/deploy/dispatch.py has not been implemented')
        github = FakeGitHub()
        ssm = FakeSsm()

        evidence = make_dispatcher(github, ssm).dispatch(
            {'workflow_run': {'id': int(RUN_ID)}}
        )

        self.assertEqual(ssm.send_calls, [{
            'region': 'ap-northeast-2',
            'document_name': 'SeoulWave-Deploy-v1',
            'document_version': '1',
            'instance_ids': ['i-0bedbcc72c616cdb1'],
            'parameters': {
                'SourceSha': [SOURCE_SHA],
                'RunId': [RUN_ID],
                'BackendImage': [f'{REGISTRY}/seoulwave@sha256:' + 'd' * 64],
                'FrontendImage': [f'{REGISTRY}/seoul-wave-frontend@sha256:' + 'e' * 64],
                'BackendConfigId': ['sha256:' + 'b' * 64],
                'FrontendConfigId': ['sha256:' + 'c' * 64],
            },
        }])
        self.assertEqual(evidence['ssm_status'], 'Success')
        self.assertEqual(evidence['deployment_status'], 'success')
        self.assertEqual(evidence['backend_config_id'], 'sha256:' + 'b' * 64)
        self.assertEqual(evidence['frontend_config_id'], 'sha256:' + 'c' * 64)
        self.assertEqual(evidence['http_checks'], {
            'proxy_health': 'passed',
            'protected_api': 'passed',
            'login': 'passed',
            'timetable': 'passed',
        })

    def test_ssm_poll_names_the_fixed_host_deployment_step(self):
        self.assertIsNotNone(dispatch, 'scripts/deploy/dispatch.py has not been implemented')
        calls = []

        def runner(arguments, **_kwargs):
            calls.append(arguments)
            return SimpleNamespace(returncode=0, stdout='{"Status":"Failed"}', stderr='')

        response = dispatch.AwsSsmClient(runner=runner).get_command_invocation(
            '9c4f29d0-5cc3-4c75-a880-35c6e4a99a21', 'i-0bedbcc72c616cdb1',
        )

        self.assertEqual(response['Status'], 'Failed')
        self.assertEqual(calls[0][calls[0].index('--plugin-name') + 1], 'runFixedDeployment')

    def test_ssm_poll_retries_when_fixed_plugin_is_not_materialized_yet(self):
        self.assertIsNotNone(dispatch, 'scripts/deploy/dispatch.py has not been implemented')
        plugin_calls = []
        fixed_step_attempts = 0
        command_id = '9c4f29d0-5cc3-4c75-a880-35c6e4a99a21'
        applied_invocation = FakeSsm().invocations[0]

        def runner(arguments, **_kwargs):
            nonlocal fixed_step_attempts
            if 'send-command' in arguments:
                return SimpleNamespace(
                    returncode=0,
                    stdout=json.dumps({'Command': {'CommandId': command_id}}),
                    stderr='',
                )

            plugin_name = arguments[arguments.index('--plugin-name') + 1]
            plugin_calls.append(plugin_name)
            if plugin_name == 'runFixedDeployment':
                fixed_step_attempts += 1
                if fixed_step_attempts == 1:
                    return SimpleNamespace(
                        returncode=255,
                        stdout='',
                        stderr=('An error occurred (InvalidPluginName) when calling '
                                'the GetCommandInvocation operation: Plugin '
                                'runFixedDeployment not found.'),
                    )
                return SimpleNamespace(
                    returncode=0, stdout=json.dumps(applied_invocation), stderr='',
                )

            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps({'PluginName': plugin_name, 'Status': 'InProgress'}),
                stderr='',
            )

        evidence = make_dispatcher(
            FakeGitHub(), dispatch.AwsSsmClient(runner=runner),
        ).dispatch({'workflow_run': {'id': int(RUN_ID)}})

        self.assertEqual(evidence['deployment_status'], 'success')
        self.assertEqual(evidence['remote_outcome'], 'applied')
        self.assertEqual(plugin_calls, [
            'runFixedDeployment', 'downloadExactCommitRunner', 'runFixedDeployment',
        ])

    def test_persistent_missing_ssm_plugin_fails_before_the_general_poll_timeout(self):
        self.assertIsNotNone(dispatch, 'scripts/deploy/dispatch.py has not been implemented')
        elapsed = [0.0]
        command_id = '9c4f29d0-5cc3-4c75-a880-35c6e4a99a21'

        def runner(arguments, **_kwargs):
            if 'send-command' in arguments:
                return SimpleNamespace(
                    returncode=0,
                    stdout=json.dumps({'Command': {'CommandId': command_id}}),
                    stderr='',
                )
            return SimpleNamespace(
                returncode=255,
                stdout='',
                stderr=('An error occurred (InvalidPluginName) when calling '
                        'the GetCommandInvocation operation: Plugin not found.'),
            )

        def sleep(seconds):
            elapsed[0] += seconds

        dispatcher = dispatch.DeploymentDispatcher(
            github=FakeGitHub(),
            ssm=dispatch.AwsSsmClient(runner=runner),
            now=lambda: dispatch.datetime(2026, 11, 7, 9, 0, tzinfo=dispatch.KST),
            sleep=sleep,
            monotonic=lambda: elapsed[0],
            poll_interval_seconds=10,
            max_poll_seconds=3900,
        )

        with self.assertRaises(dispatch.DeploymentFailed) as raised:
            dispatcher.dispatch({'workflow_run': {'id': int(RUN_ID)}})

        self.assertEqual(raised.exception.code, 'ssm_plugin_not_materialized')
        self.assertEqual(raised.exception.evidence['ssm_status'], 'poll_error')
        self.assertEqual(elapsed[0], 120)

    def test_all_absent_ssm_steps_fail_before_the_general_poll_timeout(self):
        self.assertIsNotNone(dispatch, 'scripts/deploy/dispatch.py has not been implemented')
        elapsed = [0.0]
        command_id = '9c4f29d0-5cc3-4c75-a880-35c6e4a99a21'

        def runner(arguments, **_kwargs):
            if 'send-command' in arguments:
                return SimpleNamespace(
                    returncode=0,
                    stdout=json.dumps({'Command': {'CommandId': command_id}}),
                    stderr='',
                )
            return SimpleNamespace(
                returncode=255,
                stdout='',
                stderr=('An error occurred (InvocationDoesNotExist) when calling '
                        'the GetCommandInvocation operation.'),
            )

        def sleep(seconds):
            elapsed[0] += seconds

        dispatcher = dispatch.DeploymentDispatcher(
            github=FakeGitHub(),
            ssm=dispatch.AwsSsmClient(runner=runner),
            now=lambda: dispatch.datetime(2026, 11, 7, 9, 0, tzinfo=dispatch.KST),
            sleep=sleep,
            monotonic=lambda: elapsed[0],
            poll_interval_seconds=10,
            max_poll_seconds=3900,
        )

        with self.assertRaises(dispatch.DeploymentFailed) as raised:
            dispatcher.dispatch({'workflow_run': {'id': int(RUN_ID)}})

        self.assertEqual(raised.exception.code, 'ssm_poll_timeout')
        self.assertEqual(raised.exception.evidence['ssm_status'], 'poll_timeout')
        self.assertEqual(elapsed[0], 120)

    def test_alternating_ssm_not_ready_errors_share_one_short_timeout(self):
        self.assertIsNotNone(dispatch, 'scripts/deploy/dispatch.py has not been implemented')
        elapsed = [0.0]

        class AlternatingNotReadySsm:
            def __init__(self):
                self.poll_count = 0

            def send_command(self, **_kwargs):
                return {'Command': {'CommandId': '9c4f29d0-5cc3-4c75-a880-35c6e4a99a21'}}

            def get_command_invocation(self, _command_id, _instance_id):
                reason = (
                    'invocation_missing' if self.poll_count % 2 == 0
                    else 'plugin_not_materialized'
                )
                self.poll_count += 1
                raise dispatch.InvocationNotReady(reason)

        def sleep(seconds):
            elapsed[0] += seconds

        dispatcher = dispatch.DeploymentDispatcher(
            github=FakeGitHub(),
            ssm=AlternatingNotReadySsm(),
            now=lambda: dispatch.datetime(2026, 11, 7, 9, 0, tzinfo=dispatch.KST),
            sleep=sleep,
            monotonic=lambda: elapsed[0],
            poll_interval_seconds=10,
            max_poll_seconds=3900,
        )

        with self.assertRaises(dispatch.DeploymentFailed) as raised:
            dispatcher.dispatch({'workflow_run': {'id': int(RUN_ID)}})

        self.assertEqual(raised.exception.code, 'ssm_poll_timeout')
        self.assertEqual(raised.exception.evidence['ssm_status'], 'poll_timeout')
        self.assertEqual(elapsed[0], 120)

    def test_ssm_poll_surfaces_a_failed_prerequisite_before_host_step_starts(self):
        self.assertIsNotNone(dispatch, 'scripts/deploy/dispatch.py has not been implemented')
        calls = []

        def runner(arguments, **_kwargs):
            step = arguments[arguments.index('--plugin-name') + 1]
            calls.append(step)
            if step != 'prepareFixedDirectory':
                return SimpleNamespace(
                    returncode=255, stdout='', stderr='InvocationDoesNotExist',
                )
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps({'PluginName': step, 'Status': 'Failed', 'ResponseCode': 1}),
                stderr='',
            )

        response = dispatch.AwsSsmClient(runner=runner).get_command_invocation(
            '9c4f29d0-5cc3-4c75-a880-35c6e4a99a21', 'i-0bedbcc72c616cdb1',
        )

        self.assertEqual(response['Status'], 'Failed')
        self.assertEqual(calls, [
            'runFixedDeployment', 'downloadExactCommitRunner', 'prepareFixedDirectory',
        ])

    def test_polling_waits_for_nonterminal_status_before_success(self):
        self.assertIsNotNone(dispatch, 'scripts/deploy/dispatch.py has not been implemented')
        github = FakeGitHub()
        ssm = FakeSsm(invocations=[
            {'Status': 'InProgress'},
            FakeSsm().invocations[0],
        ])

        evidence = make_dispatcher(github, ssm).dispatch(
            {'workflow_run': {'id': int(RUN_ID)}}
        )

        self.assertEqual(len(ssm.poll_calls), 2)
        self.assertEqual(evidence['ssm_status'], 'Success')
        self.assertEqual(evidence['remote_outcome'], 'applied')

    def test_polling_terminal_failure_is_not_reported_as_success(self):
        self.assertIsNotNone(dispatch, 'scripts/deploy/dispatch.py has not been implemented')
        for terminal_status in ('Failed', 'Cancelled', 'TimedOut', 'Undeliverable',
                                'Terminated', 'DeliveryTimedOut', 'ExecutionTimedOut'):
            with self.subTest(status=terminal_status):
                github = FakeGitHub()
                ssm = FakeSsm(invocations=[{'Status': terminal_status}])
                with self.assertRaises(dispatch.DeploymentFailed) as raised:
                    make_dispatcher(github, ssm).dispatch(
                        {'workflow_run': {'id': int(RUN_ID)}}
                    )
                self.assertEqual(raised.exception.evidence['ssm_status'], terminal_status)
                self.assertEqual(raised.exception.evidence['deployment_status'], 'failure')

    def test_remote_success_status_requires_an_applied_remote_result(self):
        self.assertIsNotNone(dispatch, 'scripts/deploy/dispatch.py has not been implemented')
        github = FakeGitHub()
        ssm = FakeSsm(invocations=[{
            'Status': 'Success',
            'StandardOutputContent': json.dumps({
                'outcome': 'rolled_back', 'reason': 'compose_apply_failed',
                'source_sha': SOURCE_SHA, 'run_id': RUN_ID,
            }),
        }])

        with self.assertRaises(dispatch.DeploymentFailed) as raised:
            make_dispatcher(github, ssm).dispatch({'workflow_run': {'id': int(RUN_ID)}})

        self.assertEqual(raised.exception.evidence['ssm_status'], 'Success')
        self.assertEqual(raised.exception.evidence['deployment_status'], 'failure')

    def test_remote_success_requires_matching_config_ids_and_http_checks(self):
        self.assertIsNotNone(dispatch, 'scripts/deploy/dispatch.py has not been implemented')
        valid_result = json.loads(FakeSsm().invocations[0]['StandardOutputContent'])
        invalid_results = (
            {**valid_result, 'backend_config_id': 'sha256:' + 'f' * 64},
            {**valid_result, 'checks': {
                'config_ids': 'passed',
                'migration_dry_run': 'none',
            }},
            {**valid_result, 'checks': {
                'config_ids': 'passed',
                'migration_dry_run': 'none',
                'http': {
                    'proxy_health': 'passed',
                    'protected_api': 'passed',
                    'login': 'passed',
                    'timetable': 'failed',
                },
            }},
        )

        for remote_result in invalid_results:
            with self.subTest(remote_result=remote_result):
                github = FakeGitHub()
                ssm = FakeSsm(invocations=[{
                    'Status': 'Success',
                    'StandardOutputContent': json.dumps(remote_result),
                }])
                with self.assertRaises(dispatch.DeploymentFailed):
                    make_dispatcher(github, ssm).dispatch(
                        {'workflow_run': {'id': int(RUN_ID)}}
                    )

    def test_revalidation_failure_evidence_retains_verified_artifact_identity(self):
        self.assertIsNotNone(dispatch, 'scripts/deploy/dispatch.py has not been implemented')
        builder = getattr(dispatch, 'build_rejection_evidence', None)
        self.assertIsNotNone(builder, 'rejection evidence builder is missing')
        digest = 'sha256:' + 'd' * 64
        prepared = {
            'source_run_id': RUN_ID,
            'source_sha': SOURCE_SHA,
            'artifact_id': 900,
            'artifact_digest': digest,
        }

        evidence = builder(
            {'workflow_run': {'id': int(RUN_ID), 'head_sha': SOURCE_SHA}},
            'source_run_changed',
            prepared=prepared,
        )

        self.assertEqual(evidence['source_run_id'], RUN_ID)
        self.assertEqual(evidence['artifact_id'], 900)
        self.assertEqual(evidence['artifact_digest'], digest)
        self.assertEqual(evidence['failure_code'], 'source_run_changed')


if __name__ == '__main__':
    unittest.main()
