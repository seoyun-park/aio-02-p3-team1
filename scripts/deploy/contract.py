"""Fail-closed validation for automatic EC2 deployment requests."""

from datetime import datetime, time, timedelta, timezone
from pathlib import Path
import sys


CI_SCRIPT_DIR = Path(__file__).resolve().parents[1] / 'ci'
if str(CI_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(CI_SCRIPT_DIR))

from prepare_deployment import validate_release  # noqa: E402


ECR_REGISTRY = '909345918351.dkr.ecr.ap-northeast-2.amazonaws.com'
ECR_REPOSITORIES = {'backend': 'seoulwave', 'frontend': 'seoul-wave-frontend'}
AWS_REGION = 'ap-northeast-2'
EC2_INSTANCE_ID = 'i-0bedbcc72c616cdb1'
KST = timezone(timedelta(hours=9), name='KST')


def validate_request(manifest, run, current_head, now):
    """Return a normalized, immutable deployment request or reject it."""
    if not isinstance(now, datetime) or now.utcoffset() is None:
        raise ValueError('An aware deployment time is required.')
    local_time = now.astimezone(KST).time().replace(tzinfo=None)
    if not time(9, 0) <= local_time < time(18, 0):
        raise ValueError('Deployment requests are accepted only from 09:00 through 17:59 KST.')

    run_id = str(run['id'])
    references = validate_release(manifest, run, run_id)
    if current_head != manifest['source_sha']:
        raise ValueError('The release commit is not the current dev HEAD.')
    for name, repository in ECR_REPOSITORIES.items():
        prefix = f'{ECR_REGISTRY}/{repository}@sha256:'
        if not references[f'{name.upper()}_IMAGE'].startswith(prefix):
            raise ValueError('Only the configured backend/frontend ECR repositories are accepted.')
    images = manifest['images']
    return {
        'instance_id': EC2_INSTANCE_ID,
        'region': AWS_REGION,
        'source_sha': manifest['source_sha'],
        'run_id': run_id,
        'backend_image': references['BACKEND_IMAGE'],
        'frontend_image': references['FRONTEND_IMAGE'],
        'backend_config_id': images['backend']['image_id'],
        'frontend_config_id': images['frontend']['image_id'],
    }
