import json
from pathlib import Path
from .. import geometry as G
from .h3 import node, component_name
from .common import collect_job


def build_job(job, staged, profile_id):
    root = Path(__file__).resolve().parents[2]
    graph = json.loads((root / 'workflows/h3_tts32_api.json').read_text(encoding='utf-8'))
    voices = staged.get('voices', [])
    if len(voices) > 3: raise ValueError('TTS 最多接受 3 条参考声音')
    graph['7']['inputs']['prompt'] = job['prompt']
    graph['54']['inputs']['seed'] = int(job.get('seed', 0))
    graph['56'] = node('PrimitiveFloat', value=float(job.get('seconds', 10)))
    graph['62']['inputs']['filename_prefix'] = 'h3studio/' + job['id']
    if profile_id != 'h3-tts32-legacy':
        graph['1']['inputs']['unet_name'] = component_name(profile_id, 'diffusion_models')
        graph['2']['inputs']['clip_name'] = component_name(profile_id, 'text_encoders')
        graph['3']['inputs']['vae_name'] = component_name(profile_id, 'vae', 'video_vae', job=job)
        graph['4']['inputs']['vae_name'] = component_name(profile_id, 'vae', 'audio_vae')
        graph['63']['inputs']['lora_name'] = component_name(profile_id, 'loras', 'pika')
    G.bypass(graph, '59')  # Sparse attention is optional, never an unconditional TTS dependency.
    for i, item in enumerate(voices):
        graph[f'ra{i}'] = node('LoadAudio', audio=item['name'])
        graph['7']['inputs'][f'ref_audios.ref_audio_{i}'] = [f'ra{i}', 0]
    return graph
