"""Download only an approved pipeline run's release artifact for digest-pinned deployment.

Requires GitHub CLI authenticated with Actions read access. This prepares deployment
references; it does not contact EC2 or change a database.
"""
import argparse
import io
import json
from pathlib import Path
import re
import subprocess
import zipfile

from release_contract import create_manifest, require_success, verify_run


REPOSITORY = 'munchkin112/SEOUL-WAVE'


def github_json(endpoint: str):
    return json.loads(subprocess.check_output(['gh', 'api', endpoint], text=True))


def validate_release(manifest: dict, run: dict, run_id: str) -> dict[str, str]:
    if manifest.get('schema_version') != 1:
        raise ValueError('Unsupported release schema.')
    verify_run(run, manifest['source_sha'], run_id)
    if str(manifest.get('run_id')) != run_id:
        raise ValueError('Artifact run ID differs from the trusted workflow.')
    require_success(manifest['required_checks'])
    create_manifest(manifest['source_sha'], {name: image['image_id'] for name, image in manifest['images'].items()})
    references = {}
    for name in ('backend', 'frontend'):
        reference = manifest['images'][name]['reference']
        if not re.fullmatch(r'[0-9]{12}\.dkr\.ecr\.[a-z0-9-]+\.amazonaws\.com/[a-z0-9_./-]+@sha256:[0-9a-f]{64}', reference):
            raise ValueError('Only immutable private ECR references are accepted.')
        references[f'{name.upper()}_IMAGE'] = reference
    return references


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', required=True, type=int)
    parser.add_argument('--output', default='.env.verified', type=Path)
    args = parser.parse_args()
    run_id = str(args.run_id)
    run = github_json(f'repos/{REPOSITORY}/actions/runs/{run_id}')
    # Reject failed/cancelled/untrusted runs before fetching any artifact.
    verify_run(run, run['head_sha'], run_id)
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    if revision != run['head_sha']:
        raise ValueError('Check out the verified source SHA before preparing deployment.')
    artifacts = github_json(f'repos/{REPOSITORY}/actions/runs/{run_id}/artifacts?per_page=100')
    matches = [item for item in artifacts['artifacts'] if item['name'] == 'verified-release' and not item['expired']]
    if len(matches) != 1:
        raise ValueError('Exactly one unexpired verified-release artifact is required.')
    content = subprocess.check_output(['gh', 'api', f"repos/{REPOSITORY}/actions/artifacts/{matches[0]['id']}/zip"])
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        manifest = json.loads(archive.read('release.json'))
    references = validate_release(manifest, run, run_id)
    if args.output.exists():
        raise ValueError('Output already exists; preserve or explicitly remove the old release file first.')
    args.output.write_text(''.join(f'{key}={value}\n' for key, value in references.items()), encoding='utf-8')
    print(f'Prepared digest-pinned references for verified run {run_id}. No deployment performed.')


if __name__ == '__main__':
    main()
