"""Record registry digests only when they describe the images tested by CI."""
import argparse
import json
from pathlib import Path
import re
import subprocess

from release_contract import verify_image


def registry_image(repository: str, sha: str) -> dict:
    output = subprocess.check_output([
        'aws', 'ecr', 'batch-get-image', '--repository-name', repository,
        '--image-ids', f'imageTag={sha}', '--accepted-media-types',
        'application/vnd.docker.distribution.manifest.v2+json',
        'application/vnd.oci.image.manifest.v1+json', '--output', 'json',
    ], text=True)
    result = json.loads(output)
    if result.get('failures') or len(result.get('images', [])) != 1:
        raise ValueError('Expected exactly one published image.')
    return result['images'][0]


def verified_reference(manifest: dict, sha: str, name: str, registry: str,
                       repository: str, image: dict) -> str:
    config_id = json.loads(image['imageManifest']).get('config', {}).get('digest', '')
    verify_image(manifest, sha, name, config_id)
    digest = image['imageId']['imageDigest']
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', digest):
        raise ValueError('Invalid published image digest.')
    return f'{registry}/{repository}@{digest}'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--registry', required=True)
    parser.add_argument('--frontend-repository', required=True)
    parser.add_argument('--sha', required=True)
    parser.add_argument('--run-id', required=True)
    args = parser.parse_args()
    manifest = json.loads(Path('tested-images.json').read_text())
    manifest['run_id'] = args.run_id
    manifest['required_checks'] = {'backend': 'success', 'frontend': 'success', 'integration': 'success'}
    for name, repository in [('backend', 'seoulwave'), ('frontend', args.frontend_repository)]:
        # Backend metadata was collected before switching to the frontend IAM role.
        image = (json.loads(Path('backend-registry.json').read_text())['images'][0]
                 if name == 'backend' else registry_image(repository, args.sha))
        manifest['images'][name]['reference'] = verified_reference(
            manifest, args.sha, name, args.registry, repository, image)
    Path('release.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
