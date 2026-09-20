"""Exercise shell file handling with a Docker test double, not a real engine."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import shlex

import pytest

from app.brain import MushroomBody

ROOT=Path(__file__).resolve().parents[1]


@pytest.fixture
def install(tmp_path):
    source=tmp_path/'update'/'flytrade'
    shutil.copytree(ROOT,source,ignore=shutil.ignore_patterns('__pycache__','.pytest_cache','data','.venv'))
    target=tmp_path/'projects'/'flylab-alpha'
    (target/'app').mkdir(parents=True)
    (target/'app'/'brain.py').write_text('# original local source\n')
    (target/'compose.yaml').write_text('name: flylab-alpha\nservices:\n  lab:\n    build: .\n')
    (target/'.env').write_text('FLYLAB_PORT=8099\n')
    volume=tmp_path/'volume';volume.mkdir()
    raw={'brain':MushroomBody(9).to_dict(),'counter':0,'trials':[]}
    (volume/'state.json').write_text(json.dumps(raw))
    bindir=tmp_path/'bin';bindir.mkdir()
    # The installer and the Docker double use stdlib only. Avoid unrelated
    # site hooks in helper subprocesses; the application remains unmodified.
    py=bindir/'python3'
    py.write_text('#!/bin/sh\nexec '+shlex.quote(sys.executable)+' -S \"$@\"\n')
    py.chmod(0o755)
    docker=bindir/'docker'
    docker.write_text('''#!/usr/bin/env python3
import os,sys,shutil,json,io,tarfile
from pathlib import Path
args=sys.argv[1:]
with open(os.environ['MOCK_LOG'],'a') as log:log.write(json.dumps(args)+'\\n')
if args[0]=='info':sys.exit(0)
if args[0]=='ps':sys.exit(0)
if args[0]=='update':sys.exit(0)
if args[0]=='start':sys.exit(0)
if args[0]=='inspect':
    print(json.dumps([{'Id':'test-container','State':{'Running':os.environ.get('MOCK_RUNNING','yes')=='yes'},'Mounts':[{'Destination':'/app/data','Type':'volume','Name':'flylab-alpha_memory'}],
        'HostConfig':{'PortBindings':{'8000/tcp':[{'HostIp':'127.0.0.1','HostPort':'8099'}]}},
        'Config':{'Labels':{'com.docker.compose.project':'flylab-alpha'}}}]))
    sys.exit(0)
if args[0]=='cp':
    if os.environ.get('MOCK_CP_FAIL'):sys.exit(1)
    if args[-1]=='-':
        with tarfile.open(fileobj=sys.stdout.buffer,mode='w|') as tar:
            tar.add(os.environ['MOCK_VOLUME'],arcname='.',recursive=True)
    else:
        shutil.copytree(os.environ['MOCK_VOLUME'],args[-1],dirs_exist_ok=True)
    sys.exit(0)
if args[0]=='compose':
    # Skip --project-directory and -f, whose values may contain spaces.
    cmd=args[5:]
    if cmd[0]=='ps' and '-q' in cmd:print('test-container')
    if cmd[0]=='run':
        raw=sys.stdin.buffer.read()
        with tarfile.open(fileobj=io.BytesIO(raw),mode='r:') as tar:
            Path(os.environ['MOCK_VOLUME'],'state.json').write_bytes(tar.extractfile('state.json').read())
    sys.exit(0)
sys.exit(1)
''')
    docker.chmod(0o755)
    env={**os.environ,'PATH':str(bindir)+':'+os.environ['PATH'],
         'MOCK_LOG':str(tmp_path/'docker.jsonl'),'MOCK_VOLUME':str(volume)}
    return source,target,volume,env


def run(script,args,env,answer):
    return subprocess.run(['bash',str(script),*map(str,args)],input=answer,text=True,
                          capture_output=True,env=env,timeout=30)


def test_update_cancellation_changes_nothing(install):
    source,target,volume,env=install
    result=run(source/'mettre_a_jour.sh',[target],env,'non\n')
    assert result.returncode==0,result.stderr
    assert (target/'app/brain.py').read_text()=='# original local source\n'
    assert not list(target.parent.glob('_archives/Flytrade/backups/flytrade-sauvegarde-*'))


def test_update_saves_source_and_volume_and_preserves_env(install):
    source,target,volume,env=install
    before=(volume/'state.json').read_bytes()
    result=run(source/'mettre_a_jour.sh',[target],env,'oui\n')
    assert result.returncode==0,result.stderr+result.stdout
    backup=list(target.parent.glob('_archives/Flytrade/backups/flytrade-sauvegarde-*'))[0]
    assert (backup/'source/app/brain.py').read_text()=='# original local source\n'
    assert (backup/'data/state.json').read_bytes()==before
    assert 'FLYLAB_PORT=8099' in (target/'.env').read_text()
    assert 'FLYTRADE_PORT=8099' in (target/'.env').read_text()
    assert 'FLYTRADE_VOLUME=flylab-alpha_memory' in (target/'.env').read_text()
    assert 'FLYTRADE_VOLUME_EXTERNAL=true' in (target/'.env').read_text()
    assert (backup/'source/.env').read_text()=='FLYLAB_PORT=8099\n'
    assert (target/'app/compartment_brain.py').exists()
    assert (volume/'state.json').read_bytes()==before
    calls=[json.loads(line) for line in Path(env['MOCK_LOG']).read_text().splitlines()]
    assert any('--build' in call and '--wait' in call for call in calls)
    assert not any('-v' in call or 'down' in call for call in calls)


def test_cannot_update_in_place_without_separate_archive(install):
    source,target,volume,env=install
    result=run(source/'mettre_a_jour.sh',[source],env,'oui\n')
    assert result.returncode!=0
    assert 'AUTRE dossier' in result.stderr


def test_bad_target_stops_without_change(install):
    source,target,volume,env=install
    result=run(source/'mettre_a_jour.sh',[target/'missing'],env,'oui\n')
    assert result.returncode!=0
    assert not list(target.parent.glob('_archives/Flytrade/backups/flytrade-sauvegarde-*'))


def test_restore_recovers_both_code_and_checkpoint(install):
    source,target,volume,env=install
    old=(volume/'state.json').read_bytes()
    assert run(source/'mettre_a_jour.sh',[target],env,'oui\n').returncode==0
    backup=list(target.parent.glob('_archives/Flytrade/backups/flytrade-sauvegarde-*'))[0]
    (volume/'state.json').write_text('{"different":true}')
    result=run(source/'restaurer_sauvegarde.sh',[backup,target],env,'restaurer\n')
    assert result.returncode==0,result.stderr+result.stdout
    assert (target/'app/brain.py').read_text()=='# original local source\n'
    assert (volume/'state.json').read_bytes()==old
    assert list(target.parent.glob('_archives/Flytrade/backups/avant-restauration-*'))


def test_update_keeps_local_baseline_bytes_and_hashes(install):
    import hashlib
    source,target,volume,env=install
    base=target/'baselines/100-trades-v1';base.mkdir(parents=True)
    contents=b'user reference, do not replace\n'
    (base/'reference.json').write_bytes(contents)
    checks=(hashlib.sha256(contents).hexdigest()+'  reference.json\n').encode()
    (base/'SHA256SUMS').write_bytes(checks)
    result=run(source/'mettre_a_jour.sh',[target],env,'oui\n')
    assert result.returncode==0,result.stderr+result.stdout
    assert (base/'reference.json').read_bytes()==contents
    assert (base/'SHA256SUMS').read_bytes()==checks


def test_tampered_local_baseline_blocks_update(install):
    source,target,volume,env=install
    base=target/'baselines/100-trades-v1';base.mkdir(parents=True)
    (base/'reference.json').write_text('changed')
    (base/'SHA256SUMS').write_text('0'*64+'  reference.json\n')
    result=run(source/'mettre_a_jour.sh',[target],env,'oui\n')
    assert result.returncode!=0
    assert (target/'app/brain.py').read_text()=='# original local source\n'


def test_backup_includes_nested_kraken_profile_and_env_choice(install):
    import sqlite3,hashlib
    source,target,volume,env=install
    nested=volume/'sources/kraken';nested.mkdir(parents=True)
    db=sqlite3.connect(nested/'flytrade06.sqlite3');db.execute('CREATE TABLE marker(v)');db.execute('INSERT INTO marker VALUES (7)');db.commit();db.close()
    (nested/'info.json').write_text('{"profile":"kraken"}')
    with (target/'.env').open('a') as out:out.write('FLYTRADE_FEED=coinbase\n')
    result=run(source/'mettre_a_jour.sh',[target],env,'oui\n')
    assert result.returncode==0,result.stderr+result.stdout
    backup=list(target.parent.glob('_archives/Flytrade/backups/flytrade-sauvegarde-*'))[0]
    assert (backup/'data/sources/kraken/info.json').read_bytes()==(nested/'info.json').read_bytes()
    assert 'sources/kraken/flytrade06.sqlite3' in (backup/'data-SHA256SUMS').read_text()
    assert 'FLYTRADE_FEED=coinbase' in (target/'.env').read_text()


def test_corrupt_nested_database_stops_before_code_replacement(install):
    source,target,volume,env=install
    nested=volume/'sources/kraken';nested.mkdir(parents=True)
    (nested/'flytrade06.sqlite3').write_bytes(b'corrupt sqlite')
    result=run(source/'mettre_a_jour.sh',[target],env,'oui\n')
    assert result.returncode!=0
    assert (target/'app/brain.py').read_text()=='# original local source\n'


def test_backup_copy_failure_restarts_old_running_container(install):
    source,target,volume,env=install
    env={**env,'MOCK_CP_FAIL':'1','MOCK_RUNNING':'yes'}
    result=run(source/'mettre_a_jour.sh',[target],env,'oui\n')
    assert result.returncode!=0
    assert (target/'app/brain.py').read_text()=='# original local source\n'
    calls=[json.loads(line) for line in Path(env['MOCK_LOG']).read_text().splitlines()]
    assert ['start','test-container'] in calls
    assert not any('--build' in c for c in calls)


def test_backup_copy_failure_leaves_previously_stopped_container_stopped(install):
    source,target,volume,env=install
    env={**env,'MOCK_CP_FAIL':'1','MOCK_RUNNING':'no'}
    result=run(source/'mettre_a_jour.sh',[target],env,'oui\n')
    assert result.returncode!=0
    calls=[json.loads(line) for line in Path(env['MOCK_LOG']).read_text().splitlines()]
    assert ['start','test-container'] not in calls
    assert (target/'app/brain.py').read_text()=='# original local source\n'


def test_backup_keeps_raw_wal_and_shm_unchanged(install):
    import hashlib,sqlite3
    source,target,volume,env=install
    nested=volume/'sources/kraken';nested.mkdir(parents=True)
    db=nested/'pending.sqlite3'
    con=sqlite3.connect(db)
    try:
        con.execute('PRAGMA journal_mode=WAL')
        con.execute('PRAGMA wal_autocheckpoint=0')
        con.execute('CREATE TABLE evidence(v TEXT)')
        con.commit()
        con.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        con.execute("INSERT INTO evidence VALUES ('committed only in WAL')")
        con.commit()
        assert Path(str(db)+'-wal').stat().st_size > 0
        before={str(p.relative_to(volume)):p.read_bytes() for p in volume.rglob('*') if p.is_file()}
        result=run(source/'mettre_a_jour.sh',[target],env,'oui\n')
        assert result.returncode==0,result.stderr+result.stdout
        backup=next(target.parent.glob('_archives/Flytrade/backups/flytrade-sauvegarde-*'))
        for rel,raw in before.items():
            assert (backup/'data'/rel).read_bytes()==raw
            assert (volume/rel).read_bytes()==raw
        # Read a SEPARATE restored working copy, not the hashed raw backup.
        work=target.parent/'restored-check'
        shutil.copytree(backup/'data',work)
        check=sqlite3.connect(work/'sources/kraken/pending.sqlite3')
        try:
            assert check.execute('SELECT v FROM evidence').fetchall()==[('committed only in WAL',)]
        finally:check.close()
        assert not list(backup.glob('.sqlite-check-*'))
    finally:con.close()


@pytest.mark.skipif(os.name!='posix' or os.geteuid()!=0,reason='UID separation test requires root to drop privileges')
def test_actual_unix_permissions_reproduce_readonly_then_fix():
    """Real SQLite + distinct Unix UID; Docker transport alone is simulated."""
    import sqlite3,stat,sys,tempfile
    with tempfile.TemporaryDirectory(prefix='flytrade-install-uid-',dir='/tmp') as temp:
        base=Path(temp)
        source,target,volume,env=install.__wrapped__(base)
        nested=volume/'sources/kraken';nested.mkdir(parents=True)
        db=nested/'clean-wal.sqlite3'
        con=sqlite3.connect(db)
        assert con.execute('PRAGMA journal_mode=WAL').fetchone()==('wal',)
        con.execute('CREATE TABLE evidence(v)');con.execute('INSERT INTO evidence VALUES (42)')
        con.commit();con.close()
        original=base/'old-root-owned-backup/sources/kraken'
        original.mkdir(parents=True)
        shutil.copy2(db,original/db.name)
        uid=65534;gid=65534
        # Project is the unprivileged user's; volume + old bad copy are root-owned.
        os.chown(base,uid,gid);base.chmod(0o700)
        for p in base.rglob('*'):
            os.chown(p,uid,gid)
            p.chmod(0o755 if p.is_dir() or p.name in ('docker','python3') else 0o644)
        for top in [volume,base/'old-root-owned-backup']:
            for p in [top,*top.rglob('*')]:
                os.chown(p,0,0);p.chmod(0o755 if p.is_dir() else 0o644)
        user_args=dict(user=uid,group=gid,extra_groups=[])
        probe="import sqlite3,sys; c=sqlite3.connect(sys.argv[1]+'?mode=ro',uri=True); print(c.execute('PRAGMA quick_check').fetchall())"
        bad=subprocess.run([sys.executable,'-c',probe,(original/db.name).as_uri()],
                           capture_output=True,text=True,**user_args)
        assert bad.returncode!=0
        assert 'attempt to write a readonly database' in bad.stderr,bad.stderr
        result=subprocess.run(['bash',str(source/'mettre_a_jour.sh'),str(target)],
                              input='oui\n',capture_output=True,text=True,env=env,timeout=60,**user_args)
        assert result.returncode==0,result.stderr+result.stdout
        backup=next(target.parent.glob('_archives/Flytrade/backups/flytrade-sauvegarde-*'))
        raw=backup/'data/sources/kraken/clean-wal.sqlite3'
        assert raw.stat().st_uid==uid
        assert raw.read_bytes()==db.read_bytes()
        verify=subprocess.run(['sha256sum','-c','../data-SHA256SUMS'],cwd=backup/'data',
                              capture_output=True,text=True,**user_args)
        assert verify.returncode==0,verify.stderr
        assert 'SQLite OK : sources/kraken/clean-wal.sqlite3' in result.stdout
