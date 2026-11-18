"""记录可重跑的离线 CPU/契约验收（输出不含本机绝对路径或隐私目录，适合随仓库发布），不产生 GPU 验收结论。"""
import json
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMP_PATH_RE = re.compile(r'(?:/private)?/var/folders/\S+|/tmp/tmp[\w-]+')


def sanitize_log(text: str) -> str:
    text = text.replace(str(ROOT), '<REPO_ROOT>')
    return TEMP_PATH_RE.sub('<TEMP_DIR>', text)


def main():
    destination = ROOT / 'acceptance'
    destination.mkdir(exist_ok=True)
    checks = {}
    commands = {
        'unit-tests': (['python', '-m', 'unittest', 'discover', '-s', 'tests', '-v'],
                       [sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v']),
        'export-graphs': (['python', 'scripts/export_examples.py'],
                          [sys.executable, 'scripts/export_examples.py']),
        'compile': (['python', '-m', 'compileall', '-q', 'studio', 'scripts', 'remote'],
                    [sys.executable, '-m', 'compileall', '-q', 'studio', 'scripts', 'remote']),
        'offline-plan': (['python', 'scripts/h3studio.py', 'plan', 'examples/advanced-v3.json'],
                         [sys.executable, 'scripts/h3studio.py', 'plan', 'examples/advanced-v3.json']),
        'offline-doctor': (['python', 'scripts/h3studio.py', 'doctor', '--manifest', 'examples/basic-v3.json', '--offline'],
                           [sys.executable, 'scripts/h3studio.py', 'doctor', '--manifest', 'examples/basic-v3.json', '--offline']),
    }
    for name, (display_cmd, exec_cmd) in commands.items():
        result = subprocess.run(exec_cmd, cwd=ROOT, capture_output=True, text=True)
        text = sanitize_log(result.stdout + result.stderr)
        (destination / (name + '.txt')).write_text(text, encoding='utf-8')
        checks[name] = {'command': display_cmd, 'exit_code': result.returncode, 'log': name + '.txt'}
        if name == 'unit-tests':
            match = re.search(r'Ran (\d+) tests', text)
            checks[name]['test_count'] = int(match[1]) if match else None
        print(name, result.returncode, text[-400:] if result.returncode else '')
    report = {
        'python': platform.python_version(),
        'ffmpeg_available': bool(shutil.which('ffmpeg')),
        'gpu_test_executed': False,
        'checks': checks,
    }
    (destination / 'verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    if any(c['exit_code'] for c in checks.values()):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
