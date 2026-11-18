"""MiniMax H3 画幅尺寸换算、多模态 @引用 解析与首尾帧对齐行生成（纯函数，无网络或外部依赖）。"""
import random
import re

FPS = 24
IMAGE_SIZES = {"1K": 1024, "1.3K": 1344, "1.6K": 1664, "2K": 2048, "2.7K": 2720}
ASPECTS = {"16:9": (16, 9), "9:16": (9, 16), "1:1": (1, 1), "4:3": (4, 3), "3:4": (3, 4), "21:9": (21, 9)}
LABEL = {"image": "Picture", "video": "Video", "audio": "Audio"}
LITERAL_RE = re.compile(r"<(Picture|Video|Audio) (\d+)>")
MENTION_RE = re.compile(r"@(圖片|图片|Image|影片|视频|Video|音訊|音讯|音频|Audio)\s?(\d+)", re.IGNORECASE)
MENTION_KIND = {
    "圖片": "image", "图片": "image", "image": "image",
    "影片": "video", "视频": "video", "video": "video",
    "音訊": "audio", "音讯": "audio", "音频": "audio", "audio": "audio",
}
VIDEO_MODES = {"ref": "ref", "frames": "frames", "first_last": "frames", "text": "text"}


def round32(x):
    return max(32, int(round(x / 32)) * 32)


def dims(aspect, resolution, auto_size=None):
    short = int(str(resolution).rstrip("pP"))
    if aspect == "auto" and auto_size and auto_size[0] and auto_size[1]:
        a, b = auto_size
    else:
        a, b = ASPECTS.get(aspect, (16, 9))
    w, h = (short * a / b, short) if a >= b else (short, short * b / a)
    return round32(w), round32(h)


def image_dims(aspect, size_key, refs):
    long_side = IMAGE_SIZES.get(size_key, 1344)
    if aspect == "auto":
        a, b = (refs[0]["width"], refs[0]["height"]) if refs and refs[0].get("width") else (1, 1)
    else:
        a, b = ASPECTS.get(aspect, (1, 1))
    w, h = (long_side, long_side * b / a) if a >= b else (long_side * a / b, long_side)
    return round32(w), round32(h)


def pick_seed(seed, rng=None):
    if seed not in (None, "", -1):
        return int(seed)
    return (rng or random).randint(0, 2**48)


def resolve_mentions(text, items):
    if not MENTION_RE.search(text):
        text = LITERAL_RE.sub(lambda m: "@" + {"Picture": "Image"}.get(m.group(1), m.group(1)) + m.group(2), text)
    by_kind = {"image": [], "video": [], "audio": []}
    for item in items:
        by_kind[item["kind"]].append(item)
    chosen = {"image": [], "video": [], "audio": []}
    assigned = {}

    def repl(m):
        kind = MENTION_KIND[m.group(1).lower() if m.group(1).isascii() else m.group(1)]
        n = int(m.group(2))
        pool = by_kind[kind]
        if not 1 <= n <= len(pool):
            raise ValueError(f"找不到参考素材 {m.group(0)}（inputs 里只有 {len(pool)} 个{kind}）")
        key = (kind, n)
        if key not in assigned:
            chosen[kind].append(pool[n - 1])
            assigned[key] = len(chosen[kind])
        return f"<{LABEL[kind]} {assigned[key]}>"

    return MENTION_RE.sub(repl, text), chosen


def alignment_line(task, duration, last_shot=1):
    end = f"{duration:.2f}"
    if task == "I2VA":
        return "For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced."
    if task == "FL2VA":
        return (
            "How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the "
            f"0.00-second mark of the target video; Picture 2 (from Shot {last_shot}) aligns with the "
            f"{end}-second mark of the target video."
        )
    if task == "L2VA":
        return (
            f"How the reference pictures align with the target video — <Picture 1> (from "
            f"[Shot {last_shot}]) aligns with the {end}-second mark of the target video."
        )
    return ""


def raw_prompt(text, task, duration, chosen, extending):
    if task == "Ref2VA":
        if extending:
            k = len(chosen["image"])
            text += f"\n<Picture {k}> is the last frame of the previous clip; the video continues directly from it."
        return text
    head = alignment_line(task, duration)
    if not head:
        return text
    first, _, rest = text.lstrip().partition("\n")
    if first.startswith(head[:30]):
        text = rest.lstrip("\n")
    return head + "\n\n" + text


def bypass(wf, node_id, port="model"):
    upstream = wf[node_id]["inputs"][port]
    for other in wf.values():
        for k, v in other["inputs"].items():
            if v == [node_id, 0]:
                other["inputs"][k] = upstream
    del wf[node_id]


def plan_video(job, inputs=(), first=None, last=None, extend=None, rng=None):
    mode = VIDEO_MODES.get(job.get("mode", "ref"))
    if mode is None:
        raise ValueError(f"未知的 mode：{job.get('mode')}")
    duration = float(job.get("seconds", 10))
    text = job["prompt"]
    chosen = {"image": [], "video": [], "audio": []}
    if mode == "ref":
        tagged, chosen = resolve_mentions(text, list(inputs))
        if extend:
            chosen["image"].append(extend["last_frame"])
        task = "Ref2VA"
    elif mode == "frames":
        if not first and not last:
            raise ValueError("首尾帧模式至少要放一张首帧或尾帧")
        for item in (first, last):
            if item:
                if item["kind"] != "image":
                    raise ValueError("首帧与尾帧必须是图片")
                chosen["image"].append(item)
        tagged, task = text, "FL2VA" if first and last else "I2VA" if first else "L2VA"
    else:
        tagged, task = text, "T2VA"
    if extend and mode != "ref":
        raise ValueError("延伸只能用 ref 模式")

    auto_size = None
    if extend:
        auto_size = (extend.get("width"), extend.get("height"))
    elif task in ("I2VA", "FL2VA", "L2VA"):
        f0 = chosen["image"][0]
        auto_size = (f0.get("width"), f0.get("height")) if f0.get("width") else None
    width, height = dims(job.get("aspect", "auto"), job.get("resolution", "480P"), auto_size)
    unused = [i for i in inputs if i not in chosen[i["kind"]]] if mode == "ref" else []
    return {
        "task": task,
        "mode": mode,
        "final_prompt": raw_prompt(tagged, task, duration, chosen, bool(extend)),
        "chosen": chosen,
        "seconds": duration,
        "width": width,
        "height": height,
        "seed": pick_seed(job.get("seed"), rng),
        "extend": bool(extend),
        "unused_inputs": [i["name"] for i in unused],
    }
