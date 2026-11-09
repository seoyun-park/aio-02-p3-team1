"""Fail-closed, single-host Compose deployment runner for the fixed EC2 target."""

from contextlib import contextmanager
from datetime import datetime, timezone
import argparse
try:
    import fcntl
except ImportError:  # pragma: no cover - Windows test runner uses msvcrt.
    fcntl = None
import http.cookiejar
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, quote, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPCookieProcessor, Request, build_opener


REGISTRY = '909345918351.dkr.ecr.ap-northeast-2.amazonaws.com'
BACKEND_REPOSITORY = f'{REGISTRY}/seoulwave'
FRONTEND_REPOSITORY = f'{REGISTRY}/seoul-wave-frontend'
AWS_REGION = 'ap-northeast-2'
COMPOSE_PROJECT = 'seoul-wave'
APP_SERVICES = ('backend', 'frontend', 'itinerary-worker', 'database-health-monitor')
CONTAINER_NAMES = {
    'db': 'seoul-wave-db-1',
    'backend': 'seoul-wave-backend-1',
    'frontend': 'seoul-wave-frontend-1',
    'itinerary-worker': 'seoul-wave-itinerary-worker-1',
    'database-health-monitor': 'seoul-wave-database-health-monitor-1',
}
DEFAULT_ROOT = Path('/home/ubuntu/seoul-wave/SEOUL-WAVE')
DEFAULT_STATE_DIR = Path('/var/lib/seoul-wave-deploy')
DEFAULT_LOCK_PATH = Path('/var/lock/seoul-wave-deploy.lock')
MIN_AVAILABLE_MEMORY_MIB = 300
MIN_FREE_DISK_MIB = 1024
COMMAND_TIMEOUT_SECONDS = 600
HEALTH_TIMEOUT_SECONDS = 180


class DeploymentFailure(Exception):
    """A failure with a safe, non-sensitive result code."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


class LockConflict(DeploymentFailure):
    def __init__(self):
        super().__init__('lock_conflict')


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


def validate_request(request):
    """Reject any input except the exact, immutable release tuple."""
    if not isinstance(request, dict):
        raise DeploymentFailure('invalid_request')
    patterns = {
        'source_sha': r'[0-9a-f]{40}',
        'run_id': r'[0-9]{1,20}',
        'backend_image': re.escape(BACKEND_REPOSITORY) + r'@sha256:[0-9a-f]{64}',
        'frontend_image': re.escape(FRONTEND_REPOSITORY) + r'@sha256:[0-9a-f]{64}',
        'backend_config_id': r'sha256:[0-9a-f]{64}',
        'frontend_config_id': r'sha256:[0-9a-f]{64}',
    }
    if set(request) != set(patterns):
        raise DeploymentFailure('invalid_request')
    for name, pattern in patterns.items():
        value = request.get(name)
        if not isinstance(value, str) or re.fullmatch(pattern, value) is None:
            raise DeploymentFailure('invalid_request')
    return dict(request)


@contextmanager
def deployment_lock(path):
    """Acquire a non-blocking OS lock; the lock file remains harmlessly reusable."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open('a+b')
    try:
        if os.name == 'nt':
            import msvcrt

            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b'0')
                handle.flush()
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as error:
                raise LockConflict() from error
        else:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                raise LockConflict() from error
        try:
            yield
        finally:
            if os.name == 'nt':
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def run_command(argv, *, env=None, timeout=None, input_text=None):
    environment = os.environ.copy()
    if env:
        environment.update(env)
    return subprocess.run(
        [str(value) for value in argv],
        cwd=DEFAULT_ROOT,
        env=environment,
        input=input_text,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
        check=False,
    )


def available_memory_mib():
    values = {}
    with Path('/proc/meminfo').open(encoding='ascii') as meminfo:
        for line in meminfo:
            key, value = line.split(':', 1)
            if key in {'MemAvailable'}:
                values[key] = int(value.split()[0])
    if 'MemAvailable' not in values:
        raise DeploymentFailure('preflight_memory_unavailable')
    return values['MemAvailable'] // 1024


def verify_http(base_url, site_access_passwords, *, opener_factory=build_opener):
    """Check proxy liveness, auth enforcement, login, stops and timetable API."""
    base_url = base_url.rstrip('/')
    parsed_base = urlsplit(base_url)
    if parsed_base.scheme != 'http' or parsed_base.hostname not in {'127.0.0.1', 'localhost', '::1'}:
        raise DeploymentFailure('http_verification_must_use_loopback')
    passwords = [value.strip() for value in site_access_passwords.split(',') if value.strip()]
    if not passwords:
        raise DeploymentFailure('http_login_not_configured')
    # Production sets Secure cookies. Permit them only on this fixed loopback
    # verification connection; never weaken cookie handling for a remote host.
    cookie_policy = http.cookiejar.DefaultCookiePolicy(secure_protocols=('http', 'https'))
    opener = opener_factory(
        HTTPCookieProcessor(http.cookiejar.CookieJar(policy=cookie_policy)),
        _NoRedirects(),
    )

    def request(path, *, method='GET', payload=None, expect_auth_failure=False,
                allow_not_found=False):
        data = None if payload is None else json.dumps(payload).encode('utf-8')
        headers = {} if data is None else {'Content-Type': 'application/json'}
        req = Request(base_url + path, data=data, headers=headers, method=method)
        try:
            response = opener.open(req, timeout=10)
            status = response.status
            raw = response.read()
        except HTTPError as error:
            status = error.code
            raw = error.read()
        except (URLError, TimeoutError, OSError) as error:
            raise DeploymentFailure('http_unreachable') from error
        if expect_auth_failure:
            if status != 401:
                raise DeploymentFailure('http_protected_route_not_401')
            return None
        if status == 404 and allow_not_found:
            return None
        if status != 200:
            raise DeploymentFailure('http_check_failed')
        try:
            return json.loads(raw.decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise DeploymentFailure('http_invalid_json') from error

    health = request('/api/health')
    if not isinstance(health, dict) or health.get('status') != 'ok':
        raise DeploymentFailure('http_proxy_health_failed')
    request('/api/stops', expect_auth_failure=True)
    login = request('/api/auth/login', method='POST', payload={'password': passwords[0]})
    if not isinstance(login, dict) or login.get('authenticated') is not True:
        raise DeploymentFailure('http_login_failed')
    stops = request('/api/stops')
    stop_items = stops.get('stops') if isinstance(stops, dict) else None
    if not isinstance(stop_items, list) or len(stop_items) < 2:
        raise DeploymentFailure('http_stops_invalid')
    stop_ids = [stop.get('stop_id') for stop in stop_items
                if isinstance(stop, dict) and isinstance(stop.get('stop_id'), str)
                and stop['stop_id']]
    if len(stop_ids) < 2:
        raise DeploymentFailure('http_stops_invalid')
    route_pairs = [(origin, destination) for origin in stop_ids for destination in stop_ids
                   if origin != destination][:100]
    timetable_ok = False
    for origin, destination in route_pairs:
        query = urlencode(
            {'from_stop_id': origin, 'to_stop_id': destination}, quote_via=quote,
        )
        trips = request('/api/trips?' + query, allow_not_found=True)
        if isinstance(trips, dict) and isinstance(trips.get('departures'), list):
            timetable_ok = True
            break
    if not timetable_ok:
        raise DeploymentFailure('http_timetable_invalid')
    return {
        'proxy_health': 'passed',
        'protected_api': 'passed',
        'login': 'passed',
        'timetable': 'passed',
    }


def verify_image_identities(actual, request):
    expected = {
        'backend': {request['backend_config_id'], request['backend_image'].rsplit('@', 1)[1]},
        'frontend': {request['frontend_config_id'], request['frontend_image'].rsplit('@', 1)[1]},
        'itinerary-worker': {
            request['backend_config_id'], request['backend_image'].rsplit('@', 1)[1],
        },
        'database-health-monitor': {
            request['backend_config_id'], request['backend_image'].rsplit('@', 1)[1],
        },
    }
    for service, identities in expected.items():
        if actual.get(service) not in identities:
            raise DeploymentFailure(f"image_identity_mismatch_{service.replace('-', '_')}")
    return True


class HostDeployer:
    def __init__(self, *, root=DEFAULT_ROOT, state_dir=DEFAULT_STATE_DIR,
                 lock_path=DEFAULT_LOCK_PATH, runner=run_command,
                 http_opener_factory=build_opener,
                 memory_available=available_memory_mib, sleep=time.sleep,
                 now=lambda: datetime.now(timezone.utc)):
        self.root = Path(root)
        self.state_dir = Path(state_dir)
        self.lock_path = Path(lock_path)
        self.runner = runner
        self.http_opener_factory = http_opener_factory
        self.memory_available = memory_available
        self.sleep = sleep
        self.now = now
        self.result_path = self.state_dir / 'deployment-result.json'

    def deploy(self, request):
        started = self.now().isoformat()
        try:
            release = validate_request(request)
        except DeploymentFailure as error:
            return self._save_result(self._result('not_applied', error.reason, None, started))

        try:
            with deployment_lock(self.lock_path):
                return self._deploy_locked(release, started)
        except LockConflict:
            return self._save_result(self._result('not_applied', 'lock_conflict', release, started))
        except Exception:
            return self._save_result(self._result('not_applied', 'unexpected_failure', release, started))

    def _deploy_locked(self, release, started):
        mutation_started = False
        snapshot = None
        checks = {}
        try:
            self._check_files()
            if self.memory_available() < MIN_AVAILABLE_MEMORY_MIB:
                raise DeploymentFailure('preflight_memory_low')
            if shutil.disk_usage(self.root).free < MIN_FREE_DISK_MIB * 1024 * 1024:
                raise DeploymentFailure('preflight_disk_low')

            self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.state_dir.chmod(0o700)
            with self._temporary_docker_config('seoul-wave-docker-') as docker_config:
                docker_env = {'DOCKER_CONFIG': docker_config}
                target_env = {
                    **docker_env,
                    'BACKEND_IMAGE': release['backend_image'],
                    'FRONTEND_IMAGE': release['frontend_image'],
                }
                self._compose(['config', '--quiet'], target_env, 'preflight_compose_invalid')
                self._require_db_healthy(docker_env)
                snapshot = self._capture_previous_release(docker_env)
                self._require_no_pending_migrations(docker_env, 'preflight_migrations_pending')

                self._ecr_login(docker_env, required=True)
                self._checked(['docker', 'pull', release['backend_image']], env=docker_env,
                              timeout=COMMAND_TIMEOUT_SECONDS, reason='image_pull_failed')
                self._checked(['docker', 'pull', release['frontend_image']], env=docker_env,
                              timeout=COMMAND_TIMEOUT_SECONDS, reason='image_pull_failed')
                self._verify_pulled_image_identities(release, docker_env)
                self._compose(['config', '--quiet'], target_env, 'preflight_compose_invalid')

                mutation_started = True
                self._compose(['up', '--detach', '--no-deps', *APP_SERVICES],
                              target_env, 'compose_apply_failed', timeout=COMMAND_TIMEOUT_SECONDS)
                self._wait_for_health(docker_env)
                actual = self._running_config_ids(docker_env)
                verify_image_identities(actual, release)
                self._require_no_pending_migrations(docker_env, 'postdeploy_migrations_pending')
                passwords = self._site_access_passwords(docker_env)
                checks['http'] = verify_http(
                    'http://127.0.0.1:8080', passwords,
                    opener_factory=self.http_opener_factory,
                )
                checks['config_ids'] = 'passed'
                checks['migration_dry_run'] = 'none'
                self._write_release_state(release, snapshot)
                return self._save_result(self._result('applied', 'verified', release, started, checks))
        except DeploymentFailure as error:
            if mutation_started and snapshot is not None:
                return self._rollback(release, snapshot, error.reason, started, checks)
            return self._save_result(self._result('not_applied', error.reason, release, started, checks))
        except Exception:
            if mutation_started and snapshot is not None:
                return self._rollback(release, snapshot, 'unexpected_failure', started, checks)
            return self._save_result(self._result('not_applied', 'unexpected_failure', release, started, checks))

    def _check_files(self):
        if not self.root.is_dir() or not (self.root / '.env.prod').is_file():
            raise DeploymentFailure('preflight_server_files_missing')
        if not (self.root / 'compose.prod.yaml').is_file():
            raise DeploymentFailure('preflight_compose_missing')

    def _compose(self, args, env, reason, timeout=60):
        command = [
            'docker', 'compose', '--project-name', COMPOSE_PROJECT,
            '--env-file', str(self.root / '.env.prod'),
            '--file', str(self.root / 'compose.prod.yaml'), *args,
        ]
        self._checked(command, env=env, timeout=timeout, reason=reason)

    def _checked(self, argv, *, env=None, timeout=60, reason, input_text=None):
        result = self.runner(argv, env=env, timeout=timeout, input_text=input_text)
        if result.returncode != 0:
            raise DeploymentFailure(reason)
        return result

    def _inspect(self, container, fmt, env, reason):
        result = self._checked(
            ['docker', 'inspect', '--format', fmt, container],
            env=env, timeout=20, reason=reason,
        )
        return result.stdout.strip()

    def _require_db_healthy(self, env):
        state = self._inspect(
            CONTAINER_NAMES['db'], '{{.State.Health.Status}}', env,
            'preflight_database_not_healthy',
        )
        if state != 'healthy':
            raise DeploymentFailure('preflight_database_not_healthy')

    def _capture_previous_release(self, env):
        backend_id = self._inspect(
            CONTAINER_NAMES['backend'], '{{.Image}}', env,
            'preflight_backend_missing',
        )
        frontend_id = self._inspect(
            CONTAINER_NAMES['frontend'], '{{.Image}}', env,
            'preflight_frontend_missing',
        )
        for service in ('backend', 'frontend', 'itinerary-worker', 'database-health-monitor'):
            state = self._inspect(
                CONTAINER_NAMES[service], '{{.State.Status}}', env,
                'preflight_service_not_running',
            )
            if state != 'running':
                raise DeploymentFailure('preflight_service_not_running')
        for service in ('itinerary-worker', 'database-health-monitor'):
            image_id = self._inspect(
                CONTAINER_NAMES[service], '{{.Image}}', env,
                'preflight_service_image_mismatch',
            )
            if image_id != backend_id:
                raise DeploymentFailure('preflight_service_image_mismatch')
        backend_ref = self._repository_digest(backend_id, BACKEND_REPOSITORY, env)
        frontend_ref = self._repository_digest(frontend_id, FRONTEND_REPOSITORY, env)
        return {
            'source_sha': None,
            'run_id': None,
            'backend_image': backend_ref,
            'frontend_image': frontend_ref,
            'backend_config_id': backend_id,
            'frontend_config_id': frontend_id,
        }

    def _repository_digest(self, image_id, repository, env):
        result = self._checked(
            ['docker', 'image', 'inspect', '--format', '{{json .RepoDigests}}', image_id],
            env=env, timeout=20, reason='preflight_previous_digest_missing',
        )
        try:
            digests = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise DeploymentFailure('preflight_previous_digest_missing') from error
        matches = [value for value in digests if isinstance(value, str)
                   and re.fullmatch(re.escape(repository) + r'@sha256:[0-9a-f]{64}', value)]
        if len(matches) != 1:
            raise DeploymentFailure('preflight_previous_digest_missing')
        return matches[0]

    def _verify_pulled_image_identities(self, release, env):
        for name in ('backend', 'frontend'):
            image = release[f'{name}_image']
            expected = {release[f'{name}_config_id'], image.rsplit('@', 1)[1]}
            actual = self._checked(
                ['docker', 'image', 'inspect', '--format', '{{.Id}}', image],
                env=env, timeout=20, reason=f'preflight_{name}_image_inspect_failed',
            ).stdout.strip()
            if actual not in expected:
                raise DeploymentFailure(f'pulled_image_identity_mismatch_{name}')

    def _require_no_pending_migrations(self, env, reason):
        result = self._checked(
            ['docker', 'exec', CONTAINER_NAMES['backend'],
             'python', 'scripts/apply_migrations.py'],
            env=env, timeout=60, reason='migration_dry_run_failed',
        )
        if 'Pending migrations: none' not in result.stdout:
            raise DeploymentFailure(reason)

    def _ecr_login(self, env, *, required):
        try:
            token = self._checked(
                ['aws', 'ecr', 'get-login-password', '--region', AWS_REGION],
                env=env, timeout=30, reason='ecr_auth_failed',
            ).stdout.strip()
            if not token:
                raise DeploymentFailure('ecr_auth_failed')
            self._checked(
                ['docker', 'login', '--username', 'AWS', '--password-stdin', REGISTRY],
                env=env, input_text=token, timeout=30, reason='ecr_auth_failed',
            )
            return True
        except Exception:
            if required:
                raise DeploymentFailure('ecr_auth_failed')
            return False

    @staticmethod
    def _temporary_docker_config(prefix):
        runtime_dir = Path('/run')
        if runtime_dir.is_dir():
            try:
                return tempfile.TemporaryDirectory(prefix=prefix, dir=runtime_dir)
            except PermissionError:
                pass
        return tempfile.TemporaryDirectory(prefix=prefix)

    def _wait_for_health(self, env):
        deadline = time.monotonic() + HEALTH_TIMEOUT_SECONDS
        while True:
            healthy = True
            for service in ('backend', 'frontend', 'itinerary-worker'):
                state = self._inspect(
                    CONTAINER_NAMES[service], '{{.State.Health.Status}}', env,
                    'postdeploy_healthcheck_failed',
                )
                healthy = healthy and state == 'healthy'
            monitor_state = self._inspect(
                CONTAINER_NAMES['database-health-monitor'], '{{.State.Status}}', env,
                'postdeploy_healthcheck_failed',
            )
            if healthy and monitor_state == 'running':
                return
            if time.monotonic() >= deadline:
                raise DeploymentFailure('postdeploy_healthcheck_timeout')
            self.sleep(5)

    def _running_config_ids(self, env):
        return {
            service: self._inspect(
                CONTAINER_NAMES[service], '{{.Image}}', env,
                'postdeploy_config_inspect_failed',
            )
            for service in ('backend', 'frontend', 'itinerary-worker', 'database-health-monitor')
        }

    def _site_access_passwords(self, env):
        raw = self._inspect(
            CONTAINER_NAMES['backend'], '{{range .Config.Env}}{{println .}}{{end}}', env,
            'http_login_not_configured',
        )
        for line in raw.splitlines():
            if line.startswith('SITE_ACCESS_PASSWORDS='):
                return line.partition('=')[2]
        raise DeploymentFailure('http_login_not_configured')

    def _rollback(self, release, snapshot, trigger, started, checks):
        try:
            with self._temporary_docker_config('seoul-wave-rollback-') as docker_config:
                env = {
                    'DOCKER_CONFIG': docker_config,
                    'BACKEND_IMAGE': snapshot['backend_image'],
                    'FRONTEND_IMAGE': snapshot['frontend_image'],
                }
                self._ecr_login(env, required=False)
                self._compose(['up', '--detach', '--no-deps', *APP_SERVICES], env,
                              'rollback_compose_failed', timeout=COMMAND_TIMEOUT_SECONDS)
                self._wait_for_health(env)
                actual = self._running_config_ids(env)
                previous = {
                    'backend_image': snapshot['backend_image'],
                    'frontend_image': snapshot['frontend_image'],
                    'backend_config_id': snapshot['backend_config_id'],
                    'frontend_config_id': snapshot['frontend_config_id'],
                }
                verify_image_identities(actual, previous)
                self._require_db_healthy(env)
                self._require_no_pending_migrations(env, 'rollback_migrations_pending')
                passwords = self._site_access_passwords(env)
                checks['rollback_http'] = verify_http(
                    'http://127.0.0.1:8080', passwords,
                    opener_factory=self.http_opener_factory,
                )
                checks['rollback'] = 'verified'
                return self._save_result(
                    self._result('rolled_back', trigger, release, started, checks)
                )
        except Exception:
            checks['rollback'] = 'failed'
            return self._save_result(
                self._result('rollback_failed', 'rollback_failed', release, started, checks)
            )

    def _write_release_state(self, release, snapshot):
        path = self.state_dir / 'release-state.json'
        previous = snapshot
        if path.is_file():
            try:
                existing = json.loads(path.read_text(encoding='utf-8'))
                if isinstance(existing.get('current'), dict):
                    previous = existing['current']
            except (OSError, json.JSONDecodeError):
                pass
        state = {
            'schema_version': 1,
            'current': {**release, 'verified_at': self.now().isoformat()},
            'previous': previous,
        }
        self._atomic_json(path, state)

    def _result(self, outcome, reason, release, started, checks=None):
        return {
            'schema_version': 1,
            'outcome': outcome,
            'reason': reason,
            'source_sha': release.get('source_sha') if isinstance(release, dict) else None,
            'run_id': release.get('run_id') if isinstance(release, dict) else None,
            'backend_config_id': release.get('backend_config_id') if isinstance(release, dict) else None,
            'frontend_config_id': release.get('frontend_config_id') if isinstance(release, dict) else None,
            'checks': checks or {},
            'started_at': started,
            'completed_at': self.now().isoformat(),
        }

    def _save_result(self, result):
        try:
            self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            self._atomic_json(self.result_path, result)
        except OSError:
            pass
        return result

    @staticmethod
    def _atomic_json(path, value):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor, temporary_name = tempfile.mkstemp(prefix=f'.{path.name}.', dir=path.parent)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, 'w', encoding='utf-8') as target:
                json.dump(value, target, ensure_ascii=False, sort_keys=True)
                target.write('\n')
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary_name, path)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-sha', required=True)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--backend-image', required=True)
    parser.add_argument('--frontend-image', required=True)
    parser.add_argument('--backend-config-id', required=True)
    parser.add_argument('--frontend-config-id', required=True)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    request = {
        'source_sha': args.source_sha,
        'run_id': args.run_id,
        'backend_image': args.backend_image,
        'frontend_image': args.frontend_image,
        'backend_config_id': args.backend_config_id,
        'frontend_config_id': args.frontend_config_id,
    }
    result = HostDeployer().deploy(request)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result['outcome'] == 'applied' else 1


if __name__ == '__main__':
    raise SystemExit(main())
