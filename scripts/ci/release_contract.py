"""Fail-closed release checks using content-addressed Docker config bytes."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import tarfile


def archive_config_id(path: Path, expected_tag: str) -> str:
    # Docker Engine versions may expose config OR manifest digests as .Id.
    # The hash of the saved config bytes is always the registry config digest.
    with tarfile.open(path, 'r:*') as archive:
        manifest_file = archive.extractfile('manifest.json')
        if manifest_file is None:
            raise ValueError('Image archive has no Docker manifest.')
        entries = json.load(manifest_file)
        if len(entries) != 1 or expected_tag not in (entries[0].get('RepoTags') or []):
            raise ValueError('Expected exactly one image with the tested tag.')
        config_member = archive.getmember(entries[0]['Config'])
        if not config_member.isfile() or config_member.size > 10 * 1024 * 1024:
            raise ValueError('Invalid image configuration member.')
        config_file = archive.extractfile(config_member)
        if config_file is None:
            raise ValueError('Image archive has no configuration.')
        return 'sha256:' + hashlib.sha256(config_file.read()).hexdigest()


def require_success(results: dict[str, str]) -> None:
    if set(results) != {'backend', 'frontend', 'integration'} or any(value != 'success' for value in results.values()):
        raise ValueError(f'Release blocked by required checks: {results}')


def create_manifest(source_sha: str, images: dict[str, str]) -> dict:
    if not re.fullmatch(r'[0-9a-f]{40}', source_sha):
        raise ValueError('An exact source commit SHA is required.')
    if set(images) != {'backend', 'frontend'}:
        raise ValueError('Both tested application images are required.')
    if any(not re.fullmatch(r'sha256:[0-9a-f]{64}', value) for value in images.values()):
        raise ValueError('Invalid Docker image ID.')
    return {'schema_version': 1, 'source_sha': source_sha,
            'images': {name: {'image_id': value} for name, value in images.items()}}


def verify_image(manifest: dict, source_sha: str, name: str, actual_id: str) -> None:
    # Validate the complete manifest as well as the requested entry.
    if manifest.get('schema_version') != 1:
        raise ValueError('Unsupported release schema.')
    images = {key: value['image_id'] for key, value in manifest['images'].items()}
    create_manifest(manifest['source_sha'], images)
    if source_sha != manifest['source_sha'] or actual_id != images[name]:
        raise ValueError(f'Tested source/image mismatch: {name}')


def verify_run(run: dict, source_sha: str, run_id: str) -> None:
    if (str(run.get('id')) != run_id or run.get('head_sha') != source_sha
            or run.get('status') != 'completed' or run.get('conclusion') != 'success'
            or run.get('event') != 'push' or run.get('head_branch') != 'dev'
            or run.get('path') != '.github/workflows/pipeline.yml'
            or run.get('repository', {}).get('full_name') != 'munchkin112/SEOUL-WAVE'):
        raise ValueError('Deployment requires a successful trusted pipeline run.')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    gate = commands.add_parser('gate')
    gate.add_argument('--results', required=True, help='JSON object of all required job results')
    create = commands.add_parser('create')
    create.add_argument('--sha', required=True)
    create.add_argument('--backend-archive', required=True, type=Path)
    create.add_argument('--frontend-archive', required=True, type=Path)
    create.add_argument('--output', required=True, type=Path)
    verify = commands.add_parser('verify')
    verify.add_argument('--manifest', required=True, type=Path)
    verify.add_argument('--sha', required=True)
    verify.add_argument('--name', required=True, choices=['backend', 'frontend'])
    verify.add_argument('--archive', required=True, type=Path)
    args = parser.parse_args()
    if args.command == 'gate':
        require_success(json.loads(args.results))
    elif args.command == 'create':
        manifest = create_manifest(args.sha, {
            'backend': archive_config_id(args.backend_archive, f'ci-backend:{args.sha}'),
            'frontend': archive_config_id(args.frontend_archive, f'ci-frontend:{args.sha}'),
        })
        args.output.write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    else:
        verify_image(json.loads(args.manifest.read_text(encoding='utf-8')),
                     args.sha, args.name, archive_config_id(args.archive, f'ci-{args.name}:{args.sha}'))


if __name__ == '__main__':
    main()
