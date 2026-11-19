"""Build a Windows installer from program files, excluding local user data."""
import argparse
import hashlib
import re
import shutil
import subprocess
import sysconfig
import tempfile
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SECRET = re.compile(rb'sk-[A-Za-z0-9_-]{20,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----\r?\n[A-Za-z0-9+/]{32,}')
POLICY_FILES=('agent.md','library_agent.md','experiment_agent.md')


def stage_payload(bundle, stage, packages):
    bundle, stage, packages = Path(bundle).resolve(), Path(stage), Path(packages)
    policies=[bundle/name for name in POLICY_FILES]
    if any(not policy.is_file() or policy.read_bytes()!=(ROOT/policy.name).read_bytes() for policy in policies):
        raise ValueError('Missing or modified release policy; packaging stopped.')
    sources = [bundle/'MatKnow.exe', bundle/'THIRD_PARTY_NOTICES.txt', *policies]
    for source in (bundle/'_internal').rglob('*'):
        if not source.is_file():
            continue
        if source.is_symlink() or not source.resolve().is_relative_to(bundle):
            raise ValueError('Runtime contains an external file link; packaging stopped.')
        relative = source.relative_to(bundle/'_internal')
        data = source.read_bytes()
        if relative.parts[0] in ('docx', 'lxml', 'PySide6', 'shiboken6'):
            original = packages/relative
            if not original.is_file() or hashlib.sha256(data).digest() != hashlib.sha256(original.read_bytes()).digest():
                raise ValueError('Runtime resource differs from its installed dependency; packaging stopped.')
        elif relative.parts[0] == 'examples':
            if relative.name not in ('DEMO-A-教学样例.md', 'DEMO-B-教学样例.md') or len(relative.parts) != 2 or data != (ROOT/'examples'/relative.name).read_bytes():
                raise ValueError('Unexpected example resource; packaging stopped.')
        elif len(relative.parts) != 1 or (relative.suffix.lower() not in ('.dll', '.pyd') and relative.name != 'base_library.zip'):
            raise ValueError('Unexpected runtime data file; packaging stopped.')
        sources.append(source)
    # Audit everything before copying so a rejected payload is never compiled.
    for source in sources:
        if source.is_symlink() or not source.resolve().is_relative_to(bundle):
            raise ValueError('Program files contain an external file link; packaging stopped.')
        if SECRET.search(source.read_bytes()):
            raise ValueError('Potential secret found in program files; packaging stopped.')
    for source in sources:
        destination = stage/source.relative_to(bundle)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    return len(sources)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--compiler', type=Path, required=True)
    args = parser.parse_args()
    compiler = args.compiler.resolve()
    version = tomllib.loads((ROOT/'pyproject.toml').read_text(encoding='utf8'))['project']['version']
    build_root = (ROOT/'build').resolve()
    build_root.mkdir(exist_ok=True)
    output = ROOT/'dist/installer'
    output.mkdir(parents=True, exist_ok=True)
    language = compiler.parent/'Languages/ChineseSimplified.isl'
    if not language.is_file():
        language = compiler.parent/'Default.isl'
    with tempfile.TemporaryDirectory(prefix='installer-payload-', dir=build_root) as folder:
        stage = Path(folder).resolve()
        assert stage.is_relative_to(build_root)
        count = stage_payload(ROOT/'build/release-bundle/MatKnow', stage, Path(sysconfig.get_path('purelib')))
        subprocess.run([str(compiler), f'/DPayloadDir={stage}', f'/DOutputDir={output}',
                        f'/DAppVersion={version}', f'/DLanguageFile={language}',
                        str(ROOT/'packaging/windows.iss')], check=True, cwd=ROOT)
    print(f'Installer built from {count} audited program/runtime files; no user data directories included.')


if __name__ == '__main__':
    main()
