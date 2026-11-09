import importlib.util
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[2] / 'scripts/ci/release_contract.py'
spec = importlib.util.spec_from_file_location('release_contract', SCRIPT)
contract = importlib.util.module_from_spec(spec)
spec.loader.exec_module(contract)
sys.path.insert(0, str(SCRIPT.parent))
import prepare_deployment as deployment
import published_release as publishing


class ReleaseContractTests(unittest.TestCase):
    def test_archive_identity_uses_config_bytes_independent_of_docker_inspect_version(self):
        with tempfile.TemporaryDirectory() as directory:
            archive_path = Path(directory) / 'image.tar'
            with tarfile.open(archive_path, 'w') as archive:
                entries = {'config.json': b'{}', 'manifest.json': json.dumps([
                    {'Config': 'config.json', 'RepoTags': ['ci-backend:fixture'], 'Layers': []}
                ]).encode()}
                for name, content in entries.items():
                    member = tarfile.TarInfo(name)
                    member.size = len(content)
                    archive.addfile(member, io.BytesIO(content))
            expected = 'sha256:44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a'
            self.assertEqual(contract.archive_config_id(archive_path, 'ci-backend:fixture'), expected)
            with self.assertRaises(ValueError):
                contract.archive_config_id(archive_path, 'ci-frontend:fixture')

    def test_only_all_successful_checks_allow_release(self):
        contract.require_success({'backend': 'success', 'frontend': 'success', 'integration': 'success'})
        for value in ('failure', 'cancelled', 'skipped', '', None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                contract.require_success({'backend': 'success', 'frontend': value, 'integration': 'success'})
        with self.assertRaises(ValueError):
            contract.require_success({})
        with self.assertRaises(ValueError):
            contract.require_success({'backend': 'success'})

    def test_rebuilt_or_wrong_commit_images_are_rejected(self):
        manifest = {'schema_version': 1, 'source_sha': 'a' * 40, 'images': {
            'backend': {'image_id': 'sha256:' + 'b' * 64},
            'frontend': {'image_id': 'sha256:' + 'e' * 64},
        }}
        contract.verify_image(manifest, 'a' * 40, 'backend', 'sha256:' + 'b' * 64)
        for sha, image_id in [('c' * 40, 'sha256:' + 'b' * 64), ('a' * 40, 'sha256:' + 'd' * 64)]:
            with self.subTest(sha=sha), self.assertRaises(ValueError):
                contract.verify_image(manifest, sha, 'backend', image_id)

    def test_manifest_must_include_both_images_and_a_valid_sha(self):
        for images in ({}, {'backend': 'sha256:' + 'b' * 64}):
            with self.assertRaises(ValueError):
                contract.create_manifest('a' * 40, images)
        with self.assertRaises(ValueError):
            contract.create_manifest('dev', {'backend': 'bad', 'frontend': 'bad'})

    def test_deployment_rejects_untrusted_cancelled_or_failed_workflows(self):
        run = {'id': 123, 'head_sha': 'a' * 40, 'status': 'completed', 'conclusion': 'success',
               'event': 'push', 'head_branch': 'dev', 'path': '.github/workflows/pipeline.yml',
               'repository': {'full_name': 'munchkin112/SEOUL-WAVE'}}
        contract.verify_run(run, 'a' * 40, '123')
        for key, value in [('conclusion', 'failure'), ('conclusion', 'cancelled'),
                           ('status', 'in_progress'), ('event', 'pull_request'),
                           ('path', '.github/workflows/backend-ci.yml'), ('head_sha', 'b' * 40)]:
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                contract.verify_run({**run, key: value}, 'a' * 40, '123')

    def test_deployment_accepts_only_complete_digest_pinned_release(self):
        manifest = contract.create_manifest('a' * 40, {'backend': 'sha256:' + 'b' * 64,
                                                     'frontend': 'sha256:' + 'c' * 64})
        manifest.update(run_id='123', required_checks={'backend': 'success', 'frontend': 'success', 'integration': 'success'})
        for name in ('backend', 'frontend'):
            manifest['images'][name]['reference'] = f'123456789012.dkr.ecr.ap-northeast-2.amazonaws.com/{name}@sha256:' + 'd' * 64
        run = {'id': 123, 'head_sha': 'a' * 40, 'status': 'completed', 'conclusion': 'success',
               'event': 'push', 'head_branch': 'dev', 'path': '.github/workflows/pipeline.yml',
               'repository': {'full_name': 'munchkin112/SEOUL-WAVE'}}
        self.assertEqual(set(deployment.validate_release(manifest, run, '123')), {'BACKEND_IMAGE', 'FRONTEND_IMAGE'})
        with self.assertRaises(ValueError):
            deployment.validate_release({**manifest, 'schema_version': 2}, run, '123')
        with self.assertRaises(ValueError):
            deployment.validate_release({**manifest, 'run_id': '124'}, run, '123')
        manifest['images']['frontend']['reference'] = 'example.test/frontend:latest'
        with self.assertRaises(ValueError):
            deployment.validate_release(manifest, run, '123')

    def test_registry_reference_must_point_to_the_image_tested_by_ci(self):
        manifest = contract.create_manifest('a' * 40, {'backend': 'sha256:' + 'b' * 64,
                                                     'frontend': 'sha256:' + 'c' * 64})
        image = {'imageManifest': '{"config":{"digest":"sha256:' + 'b' * 64 + '"}}',
                 'imageId': {'imageDigest': 'sha256:' + 'd' * 64, 'imageTag': 'a' * 40}}
        expected = '123456789012.dkr.ecr.ap-northeast-2.amazonaws.com/backend@sha256:' + 'd' * 64
        self.assertEqual(publishing.verified_reference(manifest, 'a' * 40, 'backend',
            '123456789012.dkr.ecr.ap-northeast-2.amazonaws.com', 'backend', image), expected)
        altered = {**image, 'imageManifest': '{"config":{"digest":"sha256:' + 'e' * 64 + '"}}'}
        with self.assertRaises(ValueError):
            publishing.verified_reference(manifest, 'a' * 40, 'backend',
                '123456789012.dkr.ecr.ap-northeast-2.amazonaws.com', 'backend', altered)


if __name__ == '__main__':
    unittest.main()
