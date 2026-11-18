"""在提交前对照服务真实 schema 检查节点、字段与权重。"""
from pathlib import PurePosixPath
from .contracts import PreflightReport

MODEL_FIELDS = {
    'UNETLoader': ('unet_name', 'diffusion_models'),
    'CLIPLoader': ('clip_name', 'text_encoders'),
    'DualCLIPLoader': ('clip_name1', 'text_encoders'),
    'CLIPVisionLoader': ('clip_name', 'clip_vision'),
    'VAELoader': ('vae_name', 'vae'),
    'LoraLoaderModelOnly': ('lora_name', 'loras'),
    'LoraLoaderBypassModelOnly': ('lora_name', 'loras'),
    'SelfLiftH3Sampler': ('upscaler_model', 'latent_upscale_models'),
    'MiniMaxH3SemanticBridgeConfigT8': ('model_name', 'semantic_bridge'),
    'ModelPatchLoader': ('name', 'model_patches'),
}


def normalized(value):
    return str(PurePosixPath(str(value).replace('\\', '/')))


def check_workflow(graph, profile_id, client):
    report = PreflightReport(True)
    known = client.info()
    model_lists = {}
    for nid, node in graph.items():
        if not isinstance(node, dict) or not isinstance(node.get('inputs'), dict):
            report.errors.append(f'{nid}：必须为 API 节点'); continue
        cls = node.get('class_type')
        if cls not in known:
            report.errors.append(f'{nid}：缺少节点 {cls}，请安装对应版本'); continue
        schema = known[cls].get('input', {})
        required = schema.get('required', {})
        declared = dict(required, **schema.get('optional', {}))
        inputs = node['inputs']
        for key in required:
            if key not in inputs and not any(k.startswith(key + '.') for k in inputs):
                report.errors.append(f'{nid}.{key}：缺少必需参数')
        for key, value in inputs.items():
            if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str) and isinstance(value[1], int):
                src = graph.get(value[0])
                if not src:
                    report.errors.append(f'{nid}.{key}：引用不存在的节点 {value[0]}')
                elif value[1] < 0:
                    report.errors.append(f'{nid}.{key}：输出序号不能为负数')
                else:
                    outputs = known.get(src.get('class_type'), {}).get('output')
                    if outputs and value[1] >= len(outputs): report.errors.append(f'{nid}.{key}：输出序号越界')
                continue
            spec = declared.get(key)
            file_input = ((cls in ('LoadImage', 'LoadAudio', 'LoadVideo') and key in ('image', 'audio', 'file'))
                          or (cls in ('WN_RippleClip', 'WN_LoadVideo') and key == 'video'))
            if spec and isinstance(spec[0], list) and value not in spec[0] and not file_input:
                report.errors.append(f'{nid}.{key}：服务不支持枚举值 {value!r}')
            elif spec and spec[0] in ('INT', 'FLOAT'):
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    report.errors.append(f'{nid}.{key}：必须是数字')
                else:
                    opts = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
                    if value < opts.get('min', float('-inf')) or value > opts.get('max', float('inf')):
                        report.errors.append(f'{nid}.{key}：数字超出节点允许范围')
            if key not in declared and declared and '.' not in key:
                report.errors.append(f'{nid}.{key}：当前节点版本没有此参数')
        field = MODEL_FIELDS.get(cls)
        if field:
            key, folder = field
            value = inputs.get(key)
            if isinstance(value, str):
                if folder not in model_lists:
                    try: model_lists[folder] = {normalized(x) for x in client.models(folder)}
                    except Exception: model_lists[folder] = None
                names = model_lists[folder]
                # Loader enumerations remain authoritative when a plugin does not expose /models.
                enum = declared.get(key, [None])[0]
                exists = normalized(value) in names if names is not None else isinstance(enum, list) and value in enum
                if not exists:
                    report.errors.append(f'{nid}.{key}：缺少模型 {folder}/{value}')
                    report.missing_components.append({'node': nid, 'field': key, 'folder': folder, 'file': value})
    report.ready = not report.errors
    return report

