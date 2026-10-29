"""One-time owner-authorized exact source-only repair; no rollback or data rewrite."""
import json
import os
from pathlib import Path
import subprocess
import sys

from ops.update import applier, continuity, target_fresh
from ops.update.auto import _deployment_lock
from aletheia.update.process_env import safe_env
from extensions.observation_lab.runner import observation_settlement_barrier
from extensions.observation_lab.checkpoint import verify_joint_checkpoint

ROOT = Path('/home/admin/aletheia')
DATA = Path('/home/admin/.aletheia')
OLD = '03fa69c93ae75cdd24272a80f9b0db586f1e0477'
TARGET = '94435c5e289004811ae8357f96be673fb6a3db49'
RECEIPT = Path('/var/tmp/aletheia-3134-maintenance-ei62pc1e/funding-fix-continuity.json')

def git(*args):
    return subprocess.check_output(['git', '-C', str(ROOT), *args], text=True).strip()

def emit(stage, **values):
    print(json.dumps({'stage':stage, **values}),flush=True)

def main():
    os.umask(0o077)
    import aletheia, ops
    from aletheia.config import loader
    assert Path(aletheia.__file__).resolve() == ROOT / 'core/aletheia/__init__.py'
    assert Path(ops.__file__).resolve() == ROOT / 'ops/__init__.py'
    assert loader._PROJECT_ROOT.resolve() == ROOT and loader.get_data_dir().resolve() == DATA
    assert git('rev-parse','HEAD') == OLD and not git('status','--porcelain')
    assert not RECEIPT.exists()
    assert set(git('diff','--name-only',OLD,TARGET).splitlines()) == {
        'extensions/exchanges/bitget/profile.py', 'tests/uta/test_ccxt_adapter.py',
        'tests/uta/test_composition.py'}
    assert git('rev-parse',TARGET+'^{tree}') == 'f2077ba151506425251c9c3ed5425fc57cc90625'
    with _deployment_lock() as locked:
        assert locked
        before = continuity.capture_update_continuity()
        preflight = continuity.evaluate_preflight(before)
        assert preflight['ok'] and not preflight['proof_gaps']
        assert before['gateway']['boot_commit'] == OLD
        assert before['gateway']['current_commit'] == OLD
        RECEIPT.write_text(json.dumps({'before':before,'preflight':preflight},sort_keys=True))
        subprocess.run(['systemctl','--user','stop','aletheia-observer.timer'],check=True)
        assert applier._stop_gateway()['ok']
        emit('old_gateway_stopped')
        with observation_settlement_barrier() as settlement:
            assert settlement['ok']
            check = verify_joint_checkpoint(create_lab=False,allow_main_outbox_progress=False)
            assert check.ok and check.reason == 'fingerprint_match'
            fingerprint = check.current_fingerprint
            assert subprocess.check_output(['systemctl','--user','show','aletheia-gateway.service','-p','MainPID','--value'],text=True).strip() == '0'
            subprocess.run(['git','-C',str(ROOT),'merge','--ff-only','--no-stat',TARGET],check=True)
            result = target_fresh.persist_current_commit(TARGET,project_root=ROOT,data_dir=DATA,env=safe_env(),python_executable=sys.executable)
            assert result.get('ok') is True
            check = verify_joint_checkpoint(create_lab=False,allow_main_outbox_progress=False)
            assert check.ok and check.current_fingerprint == fingerprint
            emit('exact_target_activated',target=TARGET,checkpoint_unchanged=True)
            assert applier._restart_gateway()['ok']
        subprocess.run(['systemctl','--user','start','aletheia-observer.timer'],check=True)
        after = continuity.capture_update_continuity_until_settled(TARGET,timeout_sec=40,poll_sec=2,gateway_port=8787)
        comparison = continuity.compare_update_continuity(before,after,expected_target=TARGET,restart=True)
        RECEIPT.write_text(json.dumps({'before':before,'preflight':preflight,'after':after,'comparison':comparison},sort_keys=True))
        emit('postflight',**comparison)
        assert comparison['ok'] and comparison['continuity_verified']
    subprocess.run(['systemctl','--user','start','aletheia-auto-update.timer'],check=True)
    emit('owned_timers_restored',runtime_health_is_separate=True)

if __name__ == '__main__':
    try: main()
    except Exception as exc:
        emit('STOP',error_type=type(exc).__name__)
        raise SystemExit(1) from None
