import importlib.util
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import re
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError


MODULE_PATH = Path(__file__).resolve().parents[2] / 'scripts/deploy/host_deploy.py'
if MODULE_PATH.is_file():
    spec = importlib.util.spec_from_file_location('host_deploy', MODULE_PATH)
    host_deploy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(host_deploy)
else:
    host_deploy = None


REGISTRY = '909345918351.dkr.ecr.ap-northeast-2.amazonaws.com'
OLD_BACKEND_IMAGE = f'{REGISTRY}/seoulwave@sha256:' + 'e' * 64
OLD_FRONTEND_IMAGE = f'{REGISTRY}/seoul-wave-frontend@sha256:' + 'f' * 64
NEW_BACKEND_IMAGE = f'{REGISTRY}/seoulwave@sha256:' + '1' * 64
NEW_FRONTEND_IMAGE = f'{REGISTRY}/seoul-wave-frontend@sha256:' + '2' * 64
OLD_BACKEND_ID = 'sha256:' + 'a' * 64
OLD_FRONTEND_ID = 'sha256:' + 'b' * 64
NEW_BACKEND_ID = 'sha256:' + 'c' * 64
NEW_FRONTEND_ID = 'sha256:' + 'd' * 64


def make_request():
    return {
        'source_sha': '3' * 40,
        'run_id': '1234567890',
        'backend_image': NEW_BACKEND_IMAGE,
        'frontend_image': NEW_FRONTEND_IMAGE,
        'backend_config_id': NEW_BACKEND_ID,
        'frontend_config_id': NEW_FRONTEND_ID,
    }


class FakeRunner:
    def __init__(self, *, fail_apply=False, fail_rollback=False,
                 wrong_backend_config=False, wrong_pulled_backend_config=False,
                 report_manifest_image_ids=False,
                 db_healthy=True):
        self.fail_apply = fail_apply
        self.fail_rollback = fail_rollback
        self.wrong_backend_config = wrong_backend_config
        self.wrong_pulled_backend_config = wrong_pulled_backend_config
        self.report_manifest_image_ids = report_manifest_image_ids
        self.db_healthy = db_healthy
        self.applied = False
        self.up_envs = []
        self.calls = []

    def __call__(self, argv, *, env=None, timeout=None, input_text=None):
        argv = [str(part) for part in argv]
        env = env or {}
        self.calls.append((argv, env.copy(), input_text))
        joined = ' '.join(argv)

        if argv[:3] == ['aws', 'ecr', 'get-login-password']:
            return SimpleNamespace(returncode=0, stdout='temporary-token', stderr='')
        if argv[:2] == ['docker', 'inspect']:
            container = argv[-1]
            fmt = argv[argv.index('--format') + 1] if '--format' in argv else ''
            if '.State.Health.Status' in fmt:
                state = 'healthy' if self.db_healthy or container != 'seoul-wave-db-1' else 'unhealthy'
                return SimpleNamespace(returncode=0, stdout=state, stderr='')
            if '.State.Status' in fmt:
                return SimpleNamespace(returncode=0, stdout='running', stderr='')
            if '.Image' in fmt:
                if container == 'seoul-wave-backend-1':
                    value = (NEW_BACKEND_IMAGE.rsplit('@', 1)[1]
                             if self.applied and self.report_manifest_image_ids
                             and not self.wrong_backend_config else
                             NEW_BACKEND_ID if self.applied and not self.wrong_backend_config
                             else OLD_BACKEND_ID)
                elif container == 'seoul-wave-frontend-1':
                    value = (NEW_FRONTEND_IMAGE.rsplit('@', 1)[1]
                             if self.applied and self.report_manifest_image_ids else
                             NEW_FRONTEND_ID if self.applied else OLD_FRONTEND_ID)
                else:
                    value = (NEW_BACKEND_IMAGE.rsplit('@', 1)[1]
                             if self.applied and self.report_manifest_image_ids else
                             NEW_BACKEND_ID if self.applied else OLD_BACKEND_ID)
                return SimpleNamespace(returncode=0, stdout=value, stderr='')
            if '.RepoDigests' in fmt:
                image_id = container
                if image_id == OLD_BACKEND_ID:
                    value = [OLD_BACKEND_IMAGE]
                elif image_id == OLD_FRONTEND_ID:
                    value = [OLD_FRONTEND_IMAGE]
                else:
                    value = []
                return SimpleNamespace(returncode=0, stdout=json.dumps(value), stderr='')
            if '.Config.Env' in fmt:
                return SimpleNamespace(
                    returncode=0,
                    stdout='SITE_ACCESS_PASSWORDS=internal-test-secret\n',
                    stderr='',
                )
        if argv[:3] == ['docker', 'image', 'inspect']:
            fmt = argv[argv.index('--format') + 1] if '--format' in argv else ''
            image_id = argv[-1]
            if fmt == '{{.Id}}':
                if image_id == NEW_BACKEND_IMAGE:
                    value = (OLD_BACKEND_ID if self.wrong_pulled_backend_config else
                             NEW_BACKEND_IMAGE.rsplit('@', 1)[1]
                             if self.report_manifest_image_ids else NEW_BACKEND_ID)
                elif image_id == NEW_FRONTEND_IMAGE:
                    value = (NEW_FRONTEND_IMAGE.rsplit('@', 1)[1]
                             if self.report_manifest_image_ids else NEW_FRONTEND_ID)
                else:
                    value = 'sha256:' + '0' * 64
                return SimpleNamespace(returncode=0, stdout=value, stderr='')
            if image_id == OLD_BACKEND_ID:
                value = [OLD_BACKEND_IMAGE]
            elif image_id == OLD_FRONTEND_ID:
                value = [OLD_FRONTEND_IMAGE]
            else:
                value = []
            return SimpleNamespace(returncode=0, stdout=json.dumps(value), stderr='')
        if argv[:2] == ['docker', 'exec']:
            return SimpleNamespace(returncode=0, stdout='Pending migrations: none\n', stderr='')
        if argv[:2] == ['docker', 'compose'] and 'config' in argv:
            return SimpleNamespace(returncode=0, stdout='', stderr='')
        if argv[:2] == ['docker', 'compose'] and 'up' in argv:
            self.up_envs.append(env.copy())
            is_new_release = env.get('BACKEND_IMAGE') == NEW_BACKEND_IMAGE
            if is_new_release:
                self.applied = True
                if self.fail_apply:
                    return SimpleNamespace(returncode=1, stdout='', stderr='sensitive docker output')
            elif self.fail_rollback:
                return SimpleNamespace(returncode=1, stdout='', stderr='sensitive rollback output')
            else:
                self.applied = False
            return SimpleNamespace(returncode=0, stdout='', stderr='')
        return SimpleNamespace(returncode=0, stdout='', stderr='')


class FakeHttpOpener:
    def __init__(self, *, fail_timetable=False, missing_first_route=False):
        self.requests = []
        self.authenticated = False
        self.fail_timetable = fail_timetable
        self.missing_first_route = missing_first_route
        self.saw_first_route = False

    def open(self, request, timeout=None):
        url = request.full_url
        self.requests.append(request)
        if url.endswith('/api/health'):
            return _response(200, {'status': 'ok'})
        if url.endswith('/api/stops'):
            if not self.authenticated:
                raise HTTPError(url, 401, 'Unauthorized', {}, None)
            return _response(200, {
                'stops': [{'stop_id': 'A'}, {'stop_id': 'B'}],
            })
        if url.endswith('/api/auth/login'):
            self.authenticated = True
            return _response(200, {'authenticated': True})
        if '/api/trips?' in url:
            if self.fail_timetable:
                raise HTTPError(url, 503, 'Unavailable', {}, None)
            if self.missing_first_route and not self.saw_first_route:
                self.saw_first_route = True
                raise HTTPError(url, 404, 'Not Found', {}, None)
            return _response(200, {'departures': []})
        raise AssertionError(f'unexpected HTTP path: {url}')


def _response(status, payload):
    class Response:
        def __init__(self):
            self.status = status

        def read(self):
            return json.dumps(payload).encode('utf-8')

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    return Response()


class DockerConfigDirectoryTests(unittest.TestCase):
    def test_runtime_directory_permission_error_falls_back_to_default_temp_dir(self):
        runtime_dir = Path('/run')
        original_is_dir = Path.is_dir
        real_temporary_directory = host_deploy.tempfile.TemporaryDirectory
        calls = []

        def is_dir(path):
            return True if path == runtime_dir else original_is_dir(path)

        def temporary_directory(*, prefix, dir=None):
            calls.append(dir)
            if dir == runtime_dir:
                raise PermissionError('injected unwritable runtime directory')
            return real_temporary_directory(prefix=prefix, dir=dir)

        with patch.object(host_deploy.Path, 'is_dir', new=is_dir):
            with patch.object(host_deploy.tempfile, 'TemporaryDirectory',
                              new=temporary_directory):
                with host_deploy.HostDeployer._temporary_docker_config('seoul-wave-test-') as path:
                    self.assertTrue(Path(path).is_dir())
                    self.assertEqual(calls, [runtime_dir, None])


class HostDeployTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(host_deploy, 'scripts/deploy/host_deploy.py must exist')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'SEOUL-WAVE'
        self.root.mkdir()
        (self.root / '.env.prod').write_text('private values stay unread\n', encoding='utf-8')
        (self.root / 'compose.prod.yaml').write_text('services: {}\n', encoding='utf-8')
        self.state_dir = Path(self.temp.name) / 'state'
        self.lock_path = Path(self.temp.name) / 'deploy.lock'

    def make_deployer(self, runner, opener=None):
        return host_deploy.HostDeployer(
            root=self.root,
            state_dir=self.state_dir,
            lock_path=self.lock_path,
            runner=runner,
            http_opener_factory=lambda *_args, **_kwargs: opener or FakeHttpOpener(),
            memory_available=lambda: 1024,
            sleep=lambda _seconds: None,
        )

    def test_lock_conflict_is_not_applied_and_runs_no_commands(self):
        runner = FakeRunner()
        deployer = self.make_deployer(runner)
        with host_deploy.deployment_lock(self.lock_path):
            result = deployer.deploy(make_request())
        self.assertEqual(result['outcome'], 'not_applied')
        self.assertEqual(result['reason'], 'lock_conflict')
        self.assertEqual(runner.calls, [])

    def test_preflight_failure_is_not_applied_and_never_runs_compose_up(self):
        runner = FakeRunner(db_healthy=False)
        result = self.make_deployer(runner).deploy(make_request())
        self.assertEqual(result['outcome'], 'not_applied')
        self.assertIn('preflight', result['reason'])
        self.assertFalse(any('up' in call[0] for call in runner.calls))

    def test_pulled_image_identity_mismatch_is_rejected_before_compose_mutation(self):
        runner = FakeRunner(wrong_pulled_backend_config=True)

        result = self.make_deployer(runner).deploy(make_request())

        self.assertEqual(result['outcome'], 'not_applied')
        self.assertEqual(result['reason'], 'pulled_image_identity_mismatch_backend')
        self.assertFalse(any(call[0][:2] == ['docker', 'compose'] and 'up' in call[0]
                             for call in runner.calls))

    def test_manifest_digest_image_ids_are_accepted_when_pinned_to_the_verified_release(self):
        runner = FakeRunner(report_manifest_image_ids=True)

        result = self.make_deployer(runner).deploy(make_request())

        self.assertEqual(result['outcome'], 'applied')
        self.assertEqual(result['reason'], 'verified')

    def test_apply_failure_restores_previous_digests_and_reports_rolled_back(self):
        runner = FakeRunner(fail_apply=True)
        result = self.make_deployer(runner).deploy(make_request())
        self.assertEqual(result['outcome'], 'rolled_back')
        self.assertEqual(len(runner.up_envs), 2)
        self.assertEqual(runner.up_envs[-1]['BACKEND_IMAGE'], OLD_BACKEND_IMAGE)
        self.assertEqual(runner.up_envs[-1]['FRONTEND_IMAGE'], OLD_FRONTEND_IMAGE)
        self.assertNotIn('sensitive', json.dumps(result))

    def test_success_updates_only_app_services_and_keeps_environment_file_unchanged(self):
        runner = FakeRunner()
        before = (self.root / '.env.prod').read_bytes()
        result = self.make_deployer(runner).deploy(make_request())
        self.assertEqual(result['outcome'], 'applied')
        self.assertEqual((self.root / '.env.prod').read_bytes(), before)
        apply_call = next(call for call in runner.calls
                          if call[0][:2] == ['docker', 'compose'] and 'up' in call[0])
        argv = apply_call[0]
        services = argv[argv.index('--no-deps') + 1:]
        self.assertEqual(services, [
            'backend', 'frontend', 'itinerary-worker', 'database-health-monitor',
        ])
        self.assertNotIn('db', services)
        self.assertFalse(any('admin' in service for service in services))
        saved = json.loads((self.state_dir / 'deployment-result.json').read_text(encoding='utf-8'))
        state = json.loads((self.state_dir / 'release-state.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['outcome'], 'applied')
        self.assertEqual(state['current']['source_sha'], make_request()['source_sha'])
        self.assertNotIn('internal-test-secret', json.dumps(saved))

    def test_failed_rollback_has_a_distinct_failure_outcome(self):
        runner = FakeRunner(fail_apply=True, fail_rollback=True)
        result = self.make_deployer(runner).deploy(make_request())
        self.assertEqual(result['outcome'], 'rollback_failed')
        self.assertNotIn('sensitive', json.dumps(result))

    def test_running_backend_rejects_an_unrelated_image_identity(self):
        runner = FakeRunner(wrong_backend_config=True)
        result = self.make_deployer(runner).deploy(make_request())
        self.assertEqual(result['outcome'], 'rolled_back')
        self.assertEqual(result['reason'], 'image_identity_mismatch_backend')

    def test_http_verification_checks_proxy_auth_and_timetable_without_leaking_password(self):
        opener = FakeHttpOpener()
        checks = host_deploy.verify_http(
            'http://127.0.0.1:8080',
            'internal-test-secret,another-secret',
            opener_factory=lambda *_args, **_kwargs: opener,
        )
        paths = [request.full_url for request in opener.requests]
        self.assertEqual(checks, {
            'proxy_health': 'passed',
            'protected_api': 'passed',
            'login': 'passed',
            'timetable': 'passed',
        })
        self.assertTrue(any('/api/trips?from_stop_id=A&to_stop_id=B' in path for path in paths))
        self.assertNotIn('internal-test-secret', json.dumps(checks))

    def test_http_verification_fails_when_timetable_is_unavailable(self):
        with self.assertRaises(host_deploy.DeploymentFailure):
            host_deploy.verify_http(
                'http://127.0.0.1:8080',
                'internal-test-secret',
                opener_factory=lambda *_args, **_kwargs: FakeHttpOpener(fail_timetable=True),
            )

    def test_http_verification_skips_invalid_stop_pair_and_checks_a_valid_route(self):
        opener = FakeHttpOpener(missing_first_route=True)
        checks = host_deploy.verify_http(
            'http://127.0.0.1:8080',
            'internal-test-secret',
            opener_factory=lambda *_args, **_kwargs: opener,
        )
        self.assertEqual(checks['timetable'], 'passed')
        self.assertEqual(sum('/api/trips?' in request.full_url for request in opener.requests), 2)

    def test_http_verification_refuses_non_loopback_hosts(self):
        with self.assertRaises(host_deploy.DeploymentFailure):
            host_deploy.verify_http('http://example.com', 'internal-test-secret')

    def test_untrusted_request_parameters_are_rejected_before_commands(self):
        runner = FakeRunner()
        request = make_request()
        request['source_sha'] = "'; touch /tmp/pwned; '"
        result = self.make_deployer(runner).deploy(request)
        self.assertEqual(result['outcome'], 'not_applied')
        self.assertEqual(runner.calls, [])
        self.assertNotIn(request['source_sha'], json.dumps(result))

    def test_cli_prints_only_sanitized_result_and_uses_the_fixed_release_tuple(self):
        expected = {
            'schema_version': 1,
            'outcome': 'applied',
            'reason': 'verified',
            'source_sha': '3' * 40,
            'run_id': '1234567890',
        }

        class Deployer:
            def deploy(self, request):
                self.request = request
                return expected

        deployed = Deployer()
        output = io.StringIO()
        args = [
            '--source-sha', '3' * 40,
            '--run-id', '1234567890',
            '--backend-image', NEW_BACKEND_IMAGE,
            '--frontend-image', NEW_FRONTEND_IMAGE,
            '--backend-config-id', NEW_BACKEND_ID,
            '--frontend-config-id', NEW_FRONTEND_ID,
        ]
        with patch.object(host_deploy, 'HostDeployer', return_value=deployed):
            with redirect_stdout(output):
                exit_code = host_deploy.main(args)
        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(output.getvalue()), expected)
        self.assertEqual(set(deployed.request), set(make_request()))


class DeploymentInfrastructureContractTests(unittest.TestCase):
    def test_ssm_document_downloads_exact_source_commit_and_accepts_only_release_fields(self):
        document_path = MODULE_PATH.parents[2] / 'infrastructure/ssm/deploy.json'
        document = json.loads(document_path.read_text(encoding='utf-8'))
        self.assertEqual(document['schemaVersion'], '2.2')
        parameters = document['parameters']
        self.assertEqual(set(parameters), {
            'SourceSha', 'RunId', 'BackendImage', 'FrontendImage',
            'BackendConfigId', 'FrontendConfigId',
        })
        for name, item in parameters.items():
            self.assertEqual(item['type'], 'String', name)
            self.assertEqual(item['interpolationType'], 'ENV_VAR', name)
            self.assertIn('allowedPattern', item, name)
        serialized = json.dumps(document)
        self.assertIn('munchkin112', serialized)
        self.assertIn('SEOUL-WAVE', serialized)
        self.assertIn('commitID:{{ SourceSha }}', serialized)
        self.assertNotIn('AWS-RunShellScript', serialized)
        self.assertNotIn('"commands"', serialized)
        request = make_request()
        for field, parameter in (
            ('source_sha', 'SourceSha'), ('run_id', 'RunId'),
            ('backend_image', 'BackendImage'), ('frontend_image', 'FrontendImage'),
            ('backend_config_id', 'BackendConfigId'),
            ('frontend_config_id', 'FrontendConfigId'),
        ):
            self.assertRegex(request[field], re.compile(parameters[parameter]['allowedPattern']))

    def test_deploy_role_is_pinned_to_one_document_one_instance_and_dev_oidc_subject(self):
        iam_dir = MODULE_PATH.parents[2] / 'infrastructure/iam'
        permissions = json.loads((iam_dir / 'deploy-role.json').read_text(encoding='utf-8'))
        trust = json.loads((iam_dir / 'deploy-role-trust.json').read_text(encoding='utf-8'))
        allowed_resources = {
            'arn:aws:ssm:ap-northeast-2:909345918351:document/SeoulWave-Deploy-v1',
            'arn:aws:ec2:ap-northeast-2:909345918351:instance/i-0bedbcc72c616cdb1',
        }
        send = next(item for item in permissions['Statement']
                    if 'ssm:SendCommand' in item['Action'])
        self.assertEqual(set(send['Resource']), allowed_resources)
        self.assertEqual(permissions['Statement'][0]['Effect'], 'Allow')
        self.assertFalse(any('AWS-RunShellScript' in json.dumps(item)
                             for item in permissions['Statement']))
        statement = trust['Statement'][0]
        self.assertEqual(statement['Principal']['Federated'],
                         'arn:aws:iam::909345918351:oidc-provider/token.actions.githubusercontent.com')
        self.assertEqual(statement['Condition']['StringEquals'], {
            'token.actions.githubusercontent.com:aud': 'sts.amazonaws.com',
            'token.actions.githubusercontent.com:sub':
                'repo:munchkin112@287117305/SEOUL-WAVE@1442459999:ref:refs/heads/dev',
        })


if __name__ == '__main__':
    unittest.main()
