"""共享执行契约；不依赖 ComfyUI 或 GPU。"""
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Artifact:
    kind: str
    path: str
    sha256: str
    metadata: dict = field(default_factory=dict)


@dataclass
class JobResult:
    id: str
    status: str
    artifacts: list[Artifact] = field(default_factory=list)
    primary_index: int = 0
    metadata: dict = field(default_factory=dict)
    error: str | None = None

    def to_dict(self):
        value = asdict(self)
        if self.artifacts:
            value['file'] = self.artifacts[self.primary_index].path
        return value


@dataclass
class ExecutionContext:
    base_dir: Path
    output_dir: Path
    input_dir: Path | None
    backend: str
    client: Any
    allow_unverified: bool = False


@dataclass
class PreflightReport:
    ready: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    missing_components: list[dict] = field(default_factory=list)

