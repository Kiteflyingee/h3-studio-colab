"""仅使用 ComfyUI 本地 HTTP API；无需打开网页或配置反向代理。"""
from __future__ import annotations
import json
import mimetypes
import os
import shutil
import time
import uuid
import hashlib
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path

class ComfyError(RuntimeError):
    pass

class ComfyExecutionError(ComfyError):
    """A history-confirmed terminal inference failure, not a transport error."""
    pass

class ComfyClient:
    def __init__(self, base='http://127.0.0.1:8188'):
        self.base = base.rstrip('/')

    def request(self, path, body=None, timeout=60):
        data = None if body is None else json.dumps(body,ensure_ascii=False).encode('utf-8')
        req = urllib.request.Request(self.base + path, data=data,
                 headers={'Content-Type':'application/json'} if data is not None else {})
        try:
            with urllib.request.urlopen(req,timeout=timeout) as r:
                blob = r.read()
                return json.loads(blob) if blob else {}
        except urllib.error.HTTPError as e:
            raise ComfyError(f'ComfyUI {path} 返回 HTTP {e.code}: {e.read().decode("utf-8","replace")[:600]}') from e
        except urllib.error.URLError as e:
            raise ComfyError(f'无法连接 {self.base}: {e}') from e

    def info(self):
        return self.request('/object_info',timeout=60)

    def stats(self):
        return self.request('/system_stats',timeout=10)

    def models(self, group):
        return self.request('/models/'+urllib.parse.quote(group), timeout=20)

    def upload_image(self, path):
        p=Path(path).resolve()
        if not p.is_file():
            raise FileNotFoundError(p)
        boundary='h3studio-'+uuid.uuid4().hex
        mime=mimetypes.guess_type(p.name)[0] or 'application/octet-stream'
        from .model_registry import file_sha256
        filename='h3studio_'+file_sha256(p)+p.suffix.lower()
        prefix=(f'--{boundary}\r\nContent-Disposition: form-data; name="overwrite"\r\n\r\ntrue\r\n'
                f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="{filename}"\r\n'
                f'Content-Type: {mime}\r\n\r\n').encode('utf-8')
        data=prefix+p.read_bytes()+f'\r\n--{boundary}--\r\n'.encode()
        req=urllib.request.Request(self.base+'/upload/image',data=data,headers={'Content-Type':f'multipart/form-data; boundary={boundary}'})
        try:
            with urllib.request.urlopen(req,timeout=120) as r:
                obj=json.loads(r.read())
        except urllib.error.HTTPError as e:
            raise ComfyError(f'图片上传失败 HTTP {e.code}: {e.read().decode("utf-8","replace")[:500]}') from e
        return '/'.join(p for p in (obj.get('subfolder', '').strip('/'), obj['name']) if p)

    def stage_local_file(self, path, input_dir):
        src=Path(path).resolve()
        if not src.is_file():
            raise FileNotFoundError(src)
        if src.suffix.lower() not in {'.png','.jpg','.jpeg','.webp','.bmp','.mp4','.mov','.mkv','.webm','.avi','.wav','.mp3','.flac','.ogg','.m4a','.aac'}:
            raise ValueError('不支持该素材格式')
        destdir=Path(input_dir).resolve()
        if not destdir.is_dir():
            raise ValueError(f'ComfyUI input 目录不存在：{destdir}')
        from .model_registry import file_sha256
        sha=file_sha256(src)
        dest=destdir/f'h3studio_{sha}_{src.name}'
        if not dest.exists() or dest.stat().st_size != src.stat().st_size:
            shutil.copy2(src,dest)
        return dest.name

    def validate_graph(self, graph):
        if not isinstance(graph,dict) or not graph:
            raise ComfyError('必须使用 ComfyUI 导出的 API 格式 JSON（含节点 class_type 和 inputs）')
        available=self.info()
        errors=[]
        for node_id,node in graph.items():
            if not isinstance(node,dict) or 'class_type' not in node or not isinstance(node.get('inputs'),dict):
                errors.append(f'{node_id}: 不是 API 格式节点')
                continue
            if node['class_type'] not in available:
                errors.append(f'{node_id}: 缺少 {node["class_type"]}')
            for key,v in node['inputs'].items():
                if isinstance(v,list) and len(v)==2 and isinstance(v[0],str) and isinstance(v[1],int) and v[0] not in graph:
                    errors.append(f'{node_id}.{key}: 节点链接 {v[0]} 不存在')
        if errors:
            raise ComfyError('工作流无法执行：'+'；'.join(errors[:15]))

    def execute(self, graph, timeout=3600):
        self.validate_graph(graph)
        cid='h3studio-'+uuid.uuid4().hex
        response=self.request('/prompt',{'prompt':graph,'client_id':cid})
        if 'prompt_id' not in response:
            raise ComfyError('提交失败：'+repr(response))
        pid=response['prompt_id']
        return self.wait(pid, timeout)

    def history(self, prompt_id):
        return self.request('/history/'+urllib.parse.quote(prompt_id)).get(prompt_id)

    def wait(self, pid, timeout=3600):
        end=time.monotonic()+timeout
        while time.monotonic()<end:
            entry=self.history(pid)
            if entry:
                status=entry.get('status',{})
                if status.get('status_str')=='error':
                    errs=[m[1] for m in status.get('messages',[]) if m[0]=='execution_error']
                    raise ComfyExecutionError('推理错误：'+repr(errs[:2]))
                if status.get('completed') is True or status.get('status_str')=='success':
                    return pid,entry
            time.sleep(2)
        raise TimeoutError(f'工作流等待超时：prompt_id={pid}。该任务可能仍在 ComfyUI 运行，请检查 /queue。')

    def save_result(self, entry, output_dir, prefix='result'):
        out=Path(output_dir).resolve();out.mkdir(parents=True,exist_ok=True)
        results=[]
        self.last_downloads=[]
        seen=set()
        for output_node,items in entry.get('outputs',{}).items():
            for field in ('images','videos','video','gifs','audio'):
                values=items.get(field,[]) or []
                if isinstance(values,dict): values=[values]
                for item in values:
                    if not isinstance(item,dict) or item.get('type','output')!='output' or not item.get('filename'):
                        continue
                    identity=(item.get('subfolder',''),item['filename'])
                    if identity in seen: continue
                    seen.add(identity)
                    ext=Path(item['filename']).suffix.lower()
                    path=out/f'{prefix}_{len(results)+1:03}{ext}'
                    tmp=path.with_name(path.name+'.part')
                    params=urllib.parse.urlencode({'filename':item['filename'],'subfolder':item.get('subfolder',''),'type':'output'})
                    with urllib.request.urlopen(self.base+'/view?'+params,timeout=300) as r, tmp.open('wb') as fp:
                        shutil.copyfileobj(r,fp)
                    if not tmp.stat().st_size: raise ComfyError('下载结果为空：'+item['filename'])
                    os.replace(tmp,path)
                    results.append(path)
                    self.last_downloads.append({'path':str(path),'node':str(output_node),'field':field,'filename':item['filename']})
        if not results:
            raise ComfyError('ComfyUI 已执行，但历史记录未提供可下载的文件（检查 SaveImage / SaveVideo 节点）')
        return results
