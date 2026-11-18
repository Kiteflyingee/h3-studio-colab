"""兼容旧导演入口；所有 current/local 作业交给共享执行器。"""
from pathlib import Path
from .comfy_client import ComfyClient, ComfyError
from .contracts import ExecutionContext
from .preflight import check_workflow


def check_graph_models(client, graph):
    report = check_workflow(graph, None, client)
    if not report.ready: raise ComfyError('\n'.join(report.errors))


def execute_storyboard(script_path, output_dir=None, url='http://127.0.0.1:8188', input_dir=None,
                       allow_unverified=False, resume=False, only=None, backend='current'):
    from .director import write_plan
    from .executor import execute_manifest
    base = Path(script_path).resolve().parent
    manifest, _ = write_plan(script_path, base / 'director_preview')
    context = ExecutionContext(base, Path(output_dir).resolve() if output_dir else base / 'out',
                               Path(input_dir).resolve() if input_dir else None, backend,
                               ComfyClient(url), allow_unverified)
    result = execute_manifest(manifest, context, resume, set(only.split(',')) if only else None, True)
    bad = [k for k, v in result['jobs'].items() if v['status'] not in ('done', 'skipped')]
    if bad: raise ComfyError('镜头未完成：' + ', '.join(bad) + '；详见 out/results.json')
    return [v['file'] for v in result['jobs'].values()]
