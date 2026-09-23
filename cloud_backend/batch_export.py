"""Shared batch ZIP format, independent of task collection and storage."""
from datetime import datetime
import json
import re
import shutil
import zipfile


def write_batch(target, identifiers, open_archive, *, max_expanded=1024**3):
    manifests = []
    expanded = 0
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as output:
        for index, identifier in enumerate(identifiers, 1):
            if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,95}', identifier):
                raise ValueError('invalid_task_id')
            directory = f'tasks/{index:03d}_{identifier}'
            included = []
            with open_archive(identifier) as source:
                seen = set()
                for member in source.infolist():
                    if member.is_dir():
                        continue
                    name = member.filename
                    if (name in seen or '\\' in name or ':' in name
                            or any(part in ('', '.', '..') for part in name.split('/'))):
                        raise ValueError('unsafe_zip_path')
                    seen.add(name)
                    expanded += member.file_size
                    if expanded > max_expanded:
                        raise ValueError('batch_too_large')
                    destination = f'{directory}/{name}'
                    with source.open(member) as reader, output.open(destination, 'w') as writer:
                        shutil.copyfileobj(reader, writer, 1024 * 1024)
                    included.append(destination)
            manifests.append(dict(task_id=identifier, directory=directory, included_files=included))
        output.writestr('batch.json', json.dumps(dict(export_schema_version=1,
            exported_at=datetime.now().astimezone().isoformat(timespec='seconds'),
            task_count=len(manifests), tasks=manifests), ensure_ascii=False, indent=2) + '\n')
