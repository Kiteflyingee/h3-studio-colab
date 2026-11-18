"""Colab 传输与会话生命周期；生成仍由 VM 中同一个执行器处理。"""
import copy
import hashlib
import importlib.util
import json
import shutil
import sys
import tempfile
import time
import zipfile
import uuid
from pathlib import Path
from .manifest import file_slots,load_manifest
from .model_registry import file_sha256

ROOT = Path(__file__).resolve().parents[1]
REMOTE = '/content/h3studio_job'


def build_payload(manifest, base_dir):
    base_dir=Path(base_dir).resolve()
    payload=copy.deepcopy(manifest)
    uploads={}
    ids={job['id'] for job in payload['jobs']}
    for job in payload['jobs']:
        for container,key in file_slots(job):
            value=container[key]
            if value.startswith('@') or (key=='extend' and value in ids):continue
            source=(base_dir/value).resolve()
            if source not in uploads:
                uploads[source]=REMOTE+'/inputs/'+file_sha256(source)+source.suffix.lower()
            container[key]=uploads[source]
    return payload,[(str(source),target) for source,target in uploads.items()]


def legacy_transport():
    from . import colab_cli
    return colab_cli


def archive_application(root, destination):
    root=Path(root)
    folders={'studio':{'.py','.html'},'scripts':{'.py'},'remote':{'.py'},'models':{'.json'},'workflows':{'.json'}}
    with zipfile.ZipFile(destination,'w',zipfile.ZIP_DEFLATED) as archive:
        for folder,suffixes in folders.items():
            for path in sorted((root/folder).rglob('*')):
                if path.is_file() and path.suffix in suffixes and '__pycache__' not in path.parts:
                    archive.write(path,str(path.relative_to(root)))
        for name in ('dependencies.lock.json','project.json','requirements.txt'):
            path=root/name
            if path.is_file():archive.write(path,name)


def artifact_target(output,remote_path,remote_dir):
    raw_path=Path(remote_path);raw_base=Path(remote_dir)
    if not raw_path.is_absolute() or '..' in raw_path.parts:
        raise ValueError('远端结果路径不属于本批次：'+str(raw_path))
    path,base=raw_path,raw_base
    if not path.is_relative_to(base):
        path,base=raw_path.resolve(strict=False),raw_base.resolve(strict=False)
        if not path.is_relative_to(base):
            raise ValueError('远端结果路径不属于本批次：'+str(raw_path))
    relative=path.relative_to(base)
    if relative.parts and relative.parts[0]=='out':relative=Path(*relative.parts[1:])
    if not relative.parts:raise ValueError('结果必须是文件路径')
    return Path(output)/relative


def runner_command(args,remote_dir,run_id,resume=False):
    task=getattr(args,'remote_task','manifest')
    command=[sys.executable,remote_dir+'/app/remote/runner_v3.py','--run-id',run_id]
    if getattr(args,'gpu',None):command+=['--gpu',str(args.gpu).upper()]
    if task=='manifest':command+=['--manifest',remote_dir+'/manifest.json']
    else:command+=['--request',remote_dir+'/request.json','--action',task]
    for flag,enabled in (('--allow-unverified',args.allow_unverified),('--confirmed',getattr(args,'confirmed',False)),
                         ('--setup-runtime',args.setup_runtime and not resume),('--install-models',args.install_models and not resume),
                         ('--resume',args.resume or resume),('--download-intermediates',getattr(args,'download_intermediates',False))):
        if enabled:command.append(flag)
    return command


def launch_worker(transport, session, args, remote_dir, run_id, predecessor=None):
    command=runner_command(args,remote_dir,run_id,resume=predecessor is not None)
    attempt='start' if predecessor is None else hashlib.sha256(predecessor.encode()).hexdigest()[:16]
    command+=['--attempt-id',attempt]
    code = f"""import fcntl, subprocess, sys
from pathlib import Path
p = Path({remote_dir!r})
marker = p / {('launched-' + attempt)!r}
with (p / 'launch.lock').open('a') as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    if not marker.exists():
        command = {command!r}
        command[0] = sys.executable
        with (p / {('runner-' + attempt + '.log')!r}).open('w') as log:
            subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        marker.touch()
print('H3STARTED')
"""
    transport.remote_exec(session, code)


def idle_probe(run_root=REMOTE):
    return f"""import json, urllib.error, urllib.request
from pathlib import Path
others = any(json.loads(p.read_text()).get('running') for p in Path({(run_root + '/runs')!r}).glob('*/status.json'))
try:
    with urllib.request.urlopen('http://127.0.0.1:8188/queue', timeout=5) as response:
        queue = json.load(response)
except urllib.error.URLError as exc:
    if not isinstance(exc.reason, ConnectionRefusedError):
        raise
    queue = {{}}
print('H3STOP ' + str(not others and not queue.get('queue_running') and not queue.get('queue_pending')))
"""


def run_colab(args):
    from .manifest import selected_jobs
    from .model_registry import profiles,select_profile
    from .state import file_lock
    transport=legacy_transport()
    manifest_path=Path(args.manifest).resolve()
    manifest=load_manifest(manifest_path)
    manifest['jobs']=selected_jobs(manifest,set(args.only.split(',')) if args.only else None,args.with_dependents)
    unresolved=[x for j in manifest['jobs'] for x in profiles()[select_profile(j)].get('unresolved_components',[])]
    if unresolved:raise ValueError('配置包含未锁定组件：'+', '.join(unresolved))
    from .executor import gpu_jobs
    need_gpu=bool(gpu_jobs(manifest['jobs']))
    if need_gpu and not args.allow_unverified:raise ValueError('首次 GPU 试跑请使用 --allow-unverified')
    args.gpu=args.gpu or manifest.get('gpu') or ('L4' if need_gpu else 'CPU')
    if need_gpu and args.gpu.upper()=='CPU':raise ValueError('此作业需要 GPU')
    for job in manifest['jobs']:
        if job['type']=='video' and not job.get('resolution'):
            job['resolution']='720P' if args.gpu.upper() in ('G4','A100','H100') else '480P'
        if args.gpu.upper()=='A100' and not job.get('video_vae') and job['type'] in ('video','video_edit','tts'):
            pid=select_profile(job)
            if 'vae__minimax_h3_video_vae_int8_convrot' in profiles()[pid].get('components',[]):
                job['video_vae']='minimax_h3_video_vae_fp16.safetensors'
    out=Path(args.output_dir).resolve() if getattr(args,'output_dir',None) else manifest_path.parent/'out'
    out.mkdir(parents=True,exist_ok=True)
    with file_lock(out/'.remote-controller.lock'):
        return _run_colab(args,transport,manifest_path,manifest,out)


def _run_colab(args,transport,manifest_path,manifest,out):
    from .state import atomic_json
    connection_path=out/'remote-session.json'
    previous=json.loads(connection_path.read_text()) if connection_path.exists() else {}
    signature=file_sha256(manifest_path)
    task=getattr(args,'remote_task','manifest')
    if task=='manifest':remapped,uploads=build_payload(manifest,manifest_path.parent)
    else:
        from .remake_remote import build_remake_payload
        remapped,uploads=build_remake_payload(manifest,manifest_path.parent)
    inputs=hashlib.sha256(json.dumps(remapped,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    attach=bool(args.resume and previous and previous.get('manifest_sha256')==signature and
                previous.get('task','manifest')==task and
                previous.get('inputs_sha256',inputs)==inputs and
                previous.get('only')==args.only and previous.get('with_dependents')==args.with_dependents and
                (not args.session or previous.get('session')==args.session))
    if args.resume and previous and not attach:
        raise ValueError('已有远端批次与本次清单/选择不符；先恢复或核实原批次状态，不能覆盖连接记录')
    session=previous['session'] if attach else getattr(args,'session',None)
    if not session and not (args.setup_runtime and args.install_models):
        raise ValueError('新会话需明确 --setup-runtime --install-models；已有运行时请提供 --session')
    gpu=args.gpu.upper()
    if gpu not in ('CPU','T4','L4','G4','A100','H100'):raise ValueError('不支持的 GPU 类型')
    run_id=previous['run_id'] if attach else uuid.uuid4().hex
    session=session or 'h3skill-v3-'+run_id[:12]
    remote_dir=previous.get('remote_dir',REMOTE) if attach else REMOTE+'/runs/'+run_id
    results_path=out/'results.json'
    download_intermediates=bool(getattr(args,'download_intermediates',False))
    only_delivery=(not download_intermediates) and (
        task in ('remake-anchors','remake-render')
        or (task=='manifest' and bool(manifest.get('director')))
    )
    delivery_jid='director-delivery' if task=='manifest' else 'delivery'
    if attach and previous.get('complete') and results_path.is_file() and not (download_intermediates and not previous.get('download_intermediates',False)):
        saved=json.loads(results_path.read_text())
        checked_jobs=([saved['jobs'][delivery_jid]] if delivery_jid in saved.get('jobs',{}) else []) if only_delivery else list(saved.get('jobs',{}).values())
        paths=[a for j in checked_jobs for a in j.get('artifacts',[])]
        if saved.get('run_id')==run_id and paths and all(Path(a['path']).is_file() and file_sha256(a['path'])==a['sha256'] for a in paths):
            print(json.dumps(saved,ensure_ascii=False,indent=2))
            return saved
    if not args.session and not attach:
        transport.colab('sessions',timeout=120)
    created=bool(attach and previous.get('created'));launch_attempted=attach;terminal=False
    receipt=previous if attach else {}
    idle_minutes=getattr(args,'idle_minutes',15)
    managed=created or session.startswith('h3skill-v3-')
    try:
        if not attach:
            if not getattr(args,'session',None):
                print(f'🚀 启动 Colab {gpu}：{session}',file=sys.stderr,flush=True)
                accelerator=[] if gpu=='CPU' else ['--gpu',gpu]
                transport.colab('new','-s',session,*accelerator,timeout=900);created=True;managed=True
            else:
                print(f'♻️ 复用 Colab 会话：{session}',file=sys.stderr,flush=True)
            if managed:transport.set_keep(session,None)
            expected_uploads=[(source,target,file_sha256(source)) for source,target in uploads]
            probe_items=[(target,sha) for _,target,sha in expected_uploads]
            init_code=(
                "import hashlib, json\nfrom pathlib import Path\n"
                f"Path({remote_dir!r}).mkdir(parents=True,exist_ok=True)\n"
                f"Path({(REMOTE+'/inputs')!r}).mkdir(parents=True,exist_ok=True)\n"
                "cached=[]\n"
                f"for t,expected in {probe_items!r}:\n"
                "    p=Path(t)\n"
                "    if p.is_file():\n"
                "        h=hashlib.sha256()\n"
                "        with p.open('rb') as f:\n"
                "            for chunk in iter(lambda:f.read(1048576),b''):h.update(chunk)\n"
                "        if h.hexdigest()==expected:cached.append(t)\n"
                "print('H3CACHED '+json.dumps(cached))"
            )
            init_raw=transport.remote_exec(session,init_code) or ''
            cached_line=next((ln for ln in init_raw.splitlines() if ln.startswith('H3CACHED ')),None)
            remote_cached=set(json.loads(cached_line[len('H3CACHED '):])) if cached_line else set()
            pending_uploads=[(source,target) for source,target,_ in expected_uploads if target not in remote_cached]
            reused_count=len(expected_uploads)-len(pending_uploads)
            with tempfile.TemporaryDirectory(prefix='h3studio_transfer_') as d:
                archive=Path(d)/'source.zip';archive_application(ROOT,archive)
                transport.colab('upload','-s',session,archive,remote_dir+'/source.zip')
                transport.remote_exec(session,f"import zipfile\nzipfile.ZipFile({(remote_dir+'/source.zip')!r}).extractall({(remote_dir+'/app')!r})")
                filename='manifest.json' if task=='manifest' else 'request.json'
                local_manifest=Path(d)/filename;local_manifest.write_text(json.dumps(remapped,ensure_ascii=False))
                transport.colab('upload','-s',session,local_manifest,remote_dir+'/'+filename)
                if pending_uploads:
                    suffix=f'（复用远端已缓存 {reused_count} 个）…' if reused_count else '…'
                    print(f'⬆️ 上传 {len(pending_uploads)} 个输入素材{suffix}',file=sys.stderr,flush=True)
                    for source,target in pending_uploads:transport.colab('upload','-s',session,source,target,timeout=1200)
                elif reused_count:
                    print(f'♻️ 复用远端已缓存的 {reused_count} 个输入素材（0 字节重传）',file=sys.stderr,flush=True)
            receipt={'session':session,'run_id':run_id,'remote_dir':remote_dir,'manifest_sha256':signature,
                     'inputs_sha256':inputs,'only':args.only,'with_dependents':args.with_dependents,
                     'launched':True,'task':task,'created':created,'gpu':gpu,'launch_pending':True,
                     'download_intermediates':download_intermediates}
            atomic_json(connection_path,receipt)
            launch_attempted=True
        elif managed:
            transport.set_keep(session,None)
        if receipt.get('launch_pending'):
            launch_worker(transport,session,args,remote_dir,run_id)
            receipt['launch_pending']=False
            atomic_json(connection_path,receipt)
        started=time.time();downloaded=set();local_by_sha={};final=None;restarted=False;last_progress=None
        while time.time()-started<float(args.max_minutes)*60:
            elapsed=time.time()-started
            time.sleep(10 if elapsed<60 else 20)
            code=("import json\nfrom pathlib import Path\n"+f"p=Path({remote_dir!r})\n"+
                  "s=json.loads((p/'status.json').read_text()) if (p/'status.json').exists() else {}\n"
                  "r=json.loads((p/'out/results.json').read_text()) if (p/'out/results.json').exists() else {}\n"+
                  ("s['report']=r if s.get('done') else {'jobs':{k:{'status':v.get('status')} for k,v in r.get('jobs',{}).items()}}\n"
                   if only_delivery else "s['report']=r\n")+
                  "print('H3REMOTE '+json.dumps(s,ensure_ascii=False))")
            raw=transport.remote_exec(session,code)
            line=next((line for line in raw.splitlines() if line.startswith('H3REMOTE ')),None)
            if not line:continue
            state=json.loads(line[len('H3REMOTE '):])
            if state.get('run_id')!=run_id:continue
            report=state.get('report',{})
            summary=' '.join(f"{k}:{v.get('status')}" for k,v in report.get('jobs',{}).items())
            progress_key=(state.get('stage','running'),summary)
            if progress_key!=last_progress:
                print(f"[{(time.time()-started)/60:4.1f} 分] {progress_key[0]}"+(f" · {summary}" if summary else ""),file=sys.stderr,flush=True)
                last_progress=progress_key
            for jid,result in report.get('jobs',{}).items():
                if only_delivery and jid!=delivery_jid:continue
                if result.get('status') not in ('done','skipped') and jid!='delivery':continue
                for item in result.get('artifacts',[]):
                    sha=item['sha256'];key=(item['path'],sha);target=artifact_target(out,item['path'],remote_dir)
                    if key not in downloaded:
                        target.parent.mkdir(parents=True,exist_ok=True)
                        if not target.is_file() or file_sha256(target)!=sha:
                            cached_local=local_by_sha.get(sha)
                            if cached_local and cached_local.is_file() and cached_local!=target and file_sha256(cached_local)==sha:
                                shutil.copy2(cached_local,target)
                            else:
                                partial=target.with_name(target.name+'.part')
                                partial.unlink(missing_ok=True)
                                transport.colab('download','-s',session,item['path'],partial,timeout=1200)
                                if file_sha256(partial)!=sha:raise RuntimeError('下载哈希不匹配：'+jid)
                                partial.replace(target)
                                print(f'⬇️ {target}',file=sys.stderr,flush=True)
                        local_by_sha[sha]=target
                        downloaded.add(key)
                    item['path']=str(target)
                if result.get('artifacts'):result['file']=result['artifacts'][result.get('primary_index',0)]['path']
            report['session']=session;report['run_id']=run_id
            atomic_json(results_path,report)
            if state.get('done'):
                terminal=not any(j.get('status') in ('running','collecting','submitting','uncertain') for j in report.get('jobs',{}).values())
                if not terminal and args.resume and not restarted:
                    launch_worker(transport,session,args,remote_dir,run_id,predecessor=state.get('attempt_id','start'))
                    restarted=True
                    continue
                if state.get('error'):raise RuntimeError(state['error'])
                final=report;break
        if final is None:raise TimeoutError('等待超过上限；远端任务保留，请使用原清单 --resume 恢复')
        print(json.dumps(final,ensure_ascii=False,indent=2))
        if any(j['status'] not in ('done','skipped') for j in final.get('jobs',{}).values()):raise RuntimeError('远端有未完成作业，详见 results.json')
        receipt['complete']=True;receipt['download_intermediates']=download_intermediates
        atomic_json(connection_path,receipt)
        return final
    finally:
        # Ambiguous transport failures preserve the run and its receipt. Never stop at a busy guard.
        stop=created and not launch_attempted
        release=args.stop_after or (managed and (idle_minutes<=0 or gpu=='CPU'))
        if terminal and launch_attempted and release:
            try:
                stop='H3STOP True' in transport.remote_exec(session,idle_probe(REMOTE))
            except Exception:stop=False
        if stop:
            transport.colab('stop','-s',session,timeout=300,check=False)
            if managed:transport.drop_keep(session)
        elif terminal and managed and not release:
            transport.set_keep(session,time.time()+idle_minutes*60)
            transport.start_watchdog(session)
            print(f'会话 {session} 将在空闲 {idle_minutes:g} 分钟后释放')
        elif launch_attempted:print('远端会话保留：'+session+'；使用原清单 --resume 恢复，连接记录：'+str(connection_path))
