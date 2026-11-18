"""导出纯血 MiniMax H3 + WepeNerd Ripple 适配器的 API 预览图；占位文件需由执行器暂存后替换。"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from studio.executor import adapter_for
from studio.model_registry import select_profile


def main():
    image = {'name': 'reference.png', 'kind': 'image', 'width': 480, 'height': 832}
    video = {'name': 'source.mp4', 'kind': 'video', 'width': 480, 'height': 832, 'fps_float': 24, 'seconds': 4, 'frames': 96, 'has_audio': True}
    base = {'id': 'preview', 'type': 'video', 'mode': 'text', 'prompt': 'A person walks by a lake.', 'seconds': 4, 'seed': 42}
    cases = [
        ('h3-text', base, {}),
        ('h3-frames', dict(base, mode='frames', first='reference.png'), {'first': image}),
        ('h3-ref', dict(base, mode='ref', prompt='@图片1 walks by a lake.'), {'inputs': [image]}),
        ('picture', {'id': 'preview', 'type': 'image', 'prompt': 'A product photo.', 'seed': 42}, {}),
        ('tts', {'id': 'preview', 'type': 'tts', 'prompt': 'A woman says hello.', 'seconds': 4, 'seed': 42}, {}),
        ('h3-guides', dict(base, mode='guides', guides=[{'at_seconds': 0}, {'at_seconds': 2}]), {'guides': [{'at_seconds': 0, 'media': image}, {'at_seconds': 2, 'media': image}]}),
        ('h3-control', dict(base, mode='control'), {'control': {'kind': 'canny', 'video': video, 'preprocess': True}}),
        ('h3-ripple', {'id': 'preview', 'type': 'video_edit', 'seconds': 4, 'anchors': [{'frame': 0, 'image': 'reference.png'}], 'seed': 42},
         {'source_video': video, 'anchors': [{'frame': 0, 'image_media': image}]}),
        ('fasth3', dict(base, profile='fasth3-omni-int8'), {}),
        ('pdmd', dict(base, mode='ref', profile='prism-dmad-pdmd-int8'), {}),
    ]
    destination = ROOT / 'workflows/native'
    destination.mkdir(exist_ok=True)
    for name, job, staged in cases:
        graph = adapter_for(job).build_job(job, staged, select_profile(job))
        (destination / (name + '.json')).write_text(json.dumps(graph, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'导出 {len(cases)} 份 API 预览图；没有提交任务')


if __name__ == '__main__':
    main()
