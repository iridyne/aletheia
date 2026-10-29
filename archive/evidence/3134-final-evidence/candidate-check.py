"""One-time credential-free copy gate; run only inside the reviewed sandbox."""
import contextlib
import json
import os
from pathlib import Path
import sqlite3
import sys

assert not Path('/home/admin/.aletheia').exists()
assert not Path('/home/admin/aletheia').exists()
assert not Path('/run/user/1000').exists()
assert 'PYTEST_CURRENT_TEST' not in os.environ
assert os.environ['ALETHEIA_DATA_DIR'] == '/data'
import aletheia
import ops
assert Path(aletheia.__file__).resolve() == Path('/workspace/core/aletheia/__init__.py')
assert Path(ops.__file__).resolve() == Path('/workspace/ops/__init__.py')

try:
    with open(os.devnull, 'w') as quiet, contextlib.redirect_stdout(quiet), contextlib.redirect_stderr(quiet):
        from ops.update.packaged import validate_current_data
        from aletheia.storage.migrations import migration_status
        validate_current_data()
        status = migration_status()
        assert status['current'] and not status['pending']
        for name in ('app.db', 'observation-lab/lab.db'):
            connection = sqlite3.connect(Path('/data', name).as_uri() + '?mode=ro', uri=True)
            try:
                assert connection.execute('PRAGMA quick_check').fetchall() == [('ok',)]
            finally:
                connection.close()
    print(json.dumps({'copy_validation': True, 'schema': status['schema_version'],
                      'migration_count': len(status['applied']), 'databases_checked': 2,
                      'audit_history_proof': False}), flush=True)
    from ops.update.target_fresh import run_smoke_check
    # Smoke tests own a separate temporary test data root, never the migrated copy.
    smoke_env = dict(os.environ, ALETHEIA_DATA_DIR='/tmp/smoke-data', ALETHEIA_RUNTIME_MODE='test')
    smoke = run_smoke_check(project_root=Path('/workspace'), env=smoke_env, python_executable=sys.executable)
    print(json.dumps({'smoke': smoke}), flush=True)
    raise SystemExit(0 if smoke.get('ok') else 1)
except Exception as exc:
    print(json.dumps({'copy_validation': False, 'error': type(exc).__name__}), flush=True)
    raise SystemExit(1) from None
