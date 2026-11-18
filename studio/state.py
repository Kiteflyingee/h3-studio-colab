"""Atomic receipts and a single report writer, including per-frame GPU jobs."""
import contextlib
import hashlib
import json
from pathlib import Path


def atomic_json(path, data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.tmp')
    temp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(path)


@contextlib.contextmanager
def file_lock(path):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a+b') as handle:
        try:
            import os
            if os.name=='nt':
                import msvcrt
                if not path.stat().st_size:handle.write(b'0');handle.flush()
                handle.seek(0);msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except (OSError,BlockingIOError) as exc:
            raise RuntimeError('另一个进程正在运行此输出目录（作业锁），请等待或检查原进程') from exc
        try:yield
        finally:
            if os.name=='nt':
                handle.seek(0);msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(handle,fcntl.LOCK_UN)


def resumable_graph(graph, client, receipt, timeout):
    """Never re-POST an accepted/ambiguous graph, even after download failure."""
    receipt=Path(receipt)
    with file_lock(receipt.with_suffix('.lock')):
        digest=hashlib.sha256(json.dumps(graph,sort_keys=True).encode()).hexdigest()
        old=json.loads(receipt.read_text()) if receipt.exists() else {}
        if old.get('status')=='failed':
            import shutil
            suffix=hashlib.sha256(str(old.get('prompt_id')).encode()).hexdigest()[:12]
            shutil.copy2(receipt,receipt.with_name(receipt.name+'.failed-'+suffix))
            old={}
        if old and old.get('graph_sha256')!=digest:
            raise RuntimeError('逐帧作业图已改变，先核对原 prompt，不能覆盖待恢复记录')
        if old.get('entry'):return old['prompt_id'],old['entry']
        if old and not old.get('prompt_id'):
            raise RuntimeError('逐帧提交状态未知，先检查服务队列，不会重复提交')
        if not old:
            old={'graph_sha256':digest,'status':'submitting'};atomic_json(receipt,old)
            try:
                response=client.request('/prompt',{'prompt':graph,'client_id':'h3studio-frame-'+digest})
                if not response.get('prompt_id'):raise RuntimeError('没有返回 prompt_id')
                old.update(status='running',prompt_id=response['prompt_id']);atomic_json(receipt,old)
            except Exception:
                old['status']='uncertain';atomic_json(receipt,old);raise
        from .comfy_client import ComfyExecutionError
        try:_,entry=client.wait(old['prompt_id'],timeout)
        except ComfyExecutionError:
            old['status']='failed';atomic_json(receipt,old);raise
        old.update(status='collecting',entry=entry);atomic_json(receipt,old)
        return old['prompt_id'],entry
