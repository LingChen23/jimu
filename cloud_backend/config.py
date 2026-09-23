"""Runtime configuration; secrets never belong in source control."""

from dataclasses import dataclass
import os
from pathlib import Path

MiB = 1024 * 1024


@dataclass(frozen=True)
class Settings:
    token: str
    data_dir: Path = Path('/var/lib/jimu-cloud')
    temp_dir: Path = Path('/var/tmp/jimu-cloud')
    log_dir: Path = Path('/var/log/jimu-cloud')
    bucket: str = 'jimu-motion-data-bj-2026'
    region: str = 'cn-beijing'
    role: str = 'JimuEcsOssRole'
    max_zip: int = 100 * MiB
    max_expanded: int = 1024 * MiB
    max_entries: int = 10_000
    temp_budget: int = 2048 * MiB
    min_free: int = 10 * 1024 * MiB
    recovery_seconds: int = 60

    @classmethod
    def from_env(cls):
        token = os.environ.get('JIMU_UPLOAD_TOKEN', '')
        if len(token) < 32:
            raise ValueError('JIMU_UPLOAD_TOKEN must contain at least 32 characters')
        return cls(
            token=token,
            data_dir=Path(os.environ.get('JIMU_DATA_DIR', '/var/lib/jimu-cloud')),
            temp_dir=Path(os.environ.get('JIMU_TEMP_DIR', '/var/tmp/jimu-cloud')),
            log_dir=Path(os.environ.get('JIMU_LOG_DIR', '/var/log/jimu-cloud')),
            bucket=os.environ.get('JIMU_OSS_BUCKET', cls.bucket),
            region=os.environ.get('JIMU_OSS_REGION', cls.region),
            role=os.environ.get('JIMU_OSS_ROLE', cls.role),
        )
