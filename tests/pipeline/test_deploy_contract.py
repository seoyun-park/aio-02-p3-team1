import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest


CONTRACT_PATH = Path(__file__).resolve().parents[2] / 'scripts/deploy/contract.py'
if CONTRACT_PATH.is_file():
    spec = importlib.util.spec_from_file_location('deploy_contract', CONTRACT_PATH)
    deployment = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(deployment)
else:
    deployment = None


REGISTRY = '909345918351.dkr.ecr.ap-northeast-2.amazonaws.com'
REPOSITORIES = {'backend': 'seoulwave', 'frontend': 'seoul-wave-frontend'}
KST = timezone(timedelta(hours=9), name='KST')


def make_run(**changes):
    run = {
        'id': 123,
        'head_sha': 'a' * 40,
        'status': 'completed',
        'conclusion': 'success',
        'event': 'push',
        'head_branch': 'dev',
        'path': '.github/workflows/pipeline.yml',
        'repository': {'full_name': 'munchkin112/SEOUL-WAVE'},
    }
    run.update(changes)
    return run


def make_manifest():
    images = {}
    for name, config_character, digest_character in (
        ('backend', 'b', 'd'),
        ('frontend', 'c', 'e'),
    ):
        images[name] = {
            'image_id': 'sha256:' + config_character * 64,
            'reference': f'{REGISTRY}/{REPOSITORIES[name]}@sha256:' + digest_character * 64,
        }
    return {
        'schema_version': 1,
        'source_sha': 'a' * 40,
        'run_id': '123',
        'required_checks': {'backend': 'success', 'frontend': 'success', 'integration': 'success'},
        'images': images,
    }


class DeployRequestContractTests(unittest.TestCase):
    def test_public_validate_request_interface_exists(self):
        self.assertTrue(
            callable(getattr(deployment, 'validate_request', None)),
            'deploy contract must expose validate_request(manifest, run, current_head, now)',
        )

    def test_failed_original_workflow_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'successful trusted pipeline run'):
            deployment.validate_request(
                make_manifest(),
                make_run(conclusion='failure'),
                'a' * 40,
                datetime(2026, 11, 7, 9, 0, tzinfo=KST),
            )

    def test_wrong_source_repository_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'successful trusted pipeline run'):
            deployment.validate_request(
                make_manifest(),
                make_run(repository={'full_name': 'attacker/SEOUL-WAVE'}),
                'a' * 40,
                datetime(2026, 11, 7, 9, 0, tzinfo=KST),
            )

    def test_stale_release_commit_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'current dev HEAD'):
            deployment.validate_request(
                make_manifest(),
                make_run(),
                'f' * 40,
                datetime(2026, 11, 7, 9, 0, tzinfo=KST),
            )

    def test_unconfigured_ecr_repository_is_rejected(self):
        manifest = make_manifest()
        manifest['images']['backend']['reference'] = (
            f'{REGISTRY}/lookalike@sha256:' + 'd' * 64
        )
        with self.assertRaisesRegex(ValueError, 'configured backend/frontend ECR repositories'):
            deployment.validate_request(
                manifest,
                make_run(),
                'a' * 40,
                datetime(2026, 11, 7, 9, 0, tzinfo=KST),
            )

    def test_each_image_must_use_its_exact_account_registry_and_repository(self):
        invalid_references = (
            ('backend', f'{REGISTRY}/lookalike@sha256:' + 'd' * 64),
            ('frontend', f'{REGISTRY}/seoulwave@sha256:' + 'e' * 64),
            ('backend', '000000000000.dkr.ecr.ap-northeast-2.amazonaws.com/seoulwave@sha256:' + 'd' * 64),
        )
        for image_name, reference in invalid_references:
            with self.subTest(image_name=image_name, reference=reference[:80]):
                manifest = make_manifest()
                manifest['images'][image_name]['reference'] = reference
                with self.assertRaisesRegex(ValueError, 'configured backend/frontend ECR repositories'):
                    deployment.validate_request(
                        manifest,
                        make_run(),
                        'a' * 40,
                        datetime(2026, 11, 7, 9, 0, tzinfo=KST),
                    )

    def test_request_before_nine_kst_is_rejected(self):
        with self.assertRaisesRegex(ValueError, '09:00 through 17:59 KST'):
            deployment.validate_request(
                make_manifest(),
                make_run(),
                'a' * 40,
                datetime(2026, 11, 7, 8, 59, tzinfo=KST),
            )

    def test_naive_request_time_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'aware deployment time'):
            deployment.validate_request(
                make_manifest(),
                make_run(),
                'a' * 40,
                datetime(2026, 11, 7, 12, 0),
            )

    def test_request_at_sixteen_hundred_is_rejected(self):
        with self.assertRaisesRegex(ValueError, '09:00 through 17:59 KST'):
            deployment.validate_request(
                make_manifest(),
                make_run(),
                'a' * 40,
                datetime(2026, 11, 7, 18, 0, tzinfo=KST),
            )

    def test_complete_current_release_is_accepted_at_nine_kst(self):
        actual = deployment.validate_request(
            make_manifest(),
            make_run(),
            'a' * 40,
            datetime(2026, 11, 7, 9, 0, tzinfo=KST),
        )
        self.assertEqual(actual, {
            'instance_id': 'i-0bedbcc72c616cdb1',
            'region': 'ap-northeast-2',
            'source_sha': 'a' * 40,
            'run_id': '123',
            'backend_image': '909345918351.dkr.ecr.ap-northeast-2.amazonaws.com/seoulwave@sha256:' + 'd' * 64,
            'frontend_image': '909345918351.dkr.ecr.ap-northeast-2.amazonaws.com/seoul-wave-frontend@sha256:' + 'e' * 64,
            'backend_config_id': 'sha256:' + 'b' * 64,
            'frontend_config_id': 'sha256:' + 'c' * 64,
        })

    def test_complete_current_release_is_accepted_at_seventeen_fifty_nine_kst(self):
        actual = deployment.validate_request(
            make_manifest(),
            make_run(),
            'a' * 40,
            datetime(2026, 11, 7, 17, 59, tzinfo=KST),
        )
        self.assertEqual(actual['source_sha'], 'a' * 40)

    def test_utc_time_is_converted_to_kst_for_admission(self):
        actual = deployment.validate_request(
            make_manifest(),
            make_run(),
            'a' * 40,
            datetime(2026, 11, 7, 0, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(actual['run_id'], '123')


if __name__ == '__main__':
    unittest.main()
