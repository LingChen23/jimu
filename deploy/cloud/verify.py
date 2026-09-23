"""Stage-1 acceptance over a short-lived SSH tunnel. Never prints the token."""

import argparse
import http.client
import io
import json
from pathlib import Path
import subprocess
import tempfile
import time
import uuid
import zipfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('zip', type=Path)
    parser.add_argument('--host', default='root@39.102.212.53')
    parser.add_argument('--key', type=Path, default=Path.home()/'.ssh'/'jimu_ecs')
    parser.add_argument('--port', type=int, default=18767)
    args = parser.parse_args()
    ssh_options = ['-i', str(args.key), '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes']
    ssh = ['ssh', *ssh_options, args.host]

    def remote(script):
        return subprocess.run([*ssh, 'cd /opt/jimu-cloud/app && /opt/jimu-cloud/venv/bin/python -'],
                              input=script.encode(), capture_output=True, check=True).stdout

    token = remote("from pathlib import Path\n"
                   "values=dict(line.split('=',1) for line in Path('/etc/jimu-cloud/service.env').read_text().splitlines())\n"
                   "print(values['JIMU_UPLOAD_TOKEN'])\n").decode().strip()
    with zipfile.ZipFile(args.zip) as source:
        manifest = json.loads(source.read('task.json'))
    task_id = manifest['task']['task_id']
    if not all(c.isascii() and (c.isalnum() or c in '_-') for c in task_id):
        raise ValueError('invalid task id')
    tunnel = subprocess.Popen(['ssh', *ssh_options, '-o', 'ExitOnForwardFailure=yes',
                                '-N', '-L', f'127.0.0.1:{args.port}:127.0.0.1:8767', args.host],
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    def request(method, path, body=None, *, authorized=True, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', args.port, timeout=180)
        try:
            auth = {'Authorization': 'Bearer ' + token} if authorized else {}
            connection.request(method, path, body, {**auth, **(headers or {})})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def ready():
        for _ in range(40):
            if tunnel.poll() is not None:
                raise RuntimeError('SSH tunnel exited')
            try:
                if request('GET', '/health')[0] == 200:
                    return
            except (OSError, http.client.HTTPException):
                pass
            time.sleep(0.5)
        raise RuntimeError('service not ready')

    def upload(data, authorized=True):
        boundary = 'jimu' + uuid.uuid4().hex
        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="task.zip"\r\n'
                'Content-Type: application/zip\r\n\r\n').encode() + data + f'\r\n--{boundary}--\r\n'.encode()
        return request('POST', '/api/tasks/archive', body, authorized=authorized,
                       headers={'Content-Type': 'multipart/form-data; boundary=' + boundary})

    bootstrap = """from pathlib import Path
import json, os, sqlite3
from cloud_backend.config import Settings
from cloud_backend.storage.oss import OssStorage
for line in Path('/etc/jimu-cloud/service.env').read_text().splitlines():
    key,value=line.split('=',1)
    os.environ[key]=value
settings=Settings.from_env()
storage=OssStorage(settings)
db=sqlite3.connect(settings.data_dir/'tasks.sqlite3')
db.row_factory=sqlite3.Row
"""
    audit = bootstrap + f"task_id={task_id!r}\n" + """row=dict(db.execute('SELECT * FROM tasks WHERE task_id=?',(task_id,)).fetchone())
prefix=row['oss_prefix']
result=storage.client.list_objects_v2(storage.sdk.ListObjectsV2Request(bucket=settings.bucket,prefix=prefix))
objects={}
for item in result.contents:
    head=storage.client.head_object(storage.sdk.HeadObjectRequest(bucket=settings.bucket,key=item.key))
    objects[item.key]={'size':head.content_length,'etag':head.etag,'modified':str(head.last_modified)}
print(json.dumps({'objects':objects,'row_count':db.execute('SELECT count(*) FROM tasks').fetchone()[0],
                  'status':row['archive_status'],'database_bytes':(settings.data_dir/'tasks.sqlite3').stat().st_size,
                  'temporary_files':len(list(settings.temp_dir.glob('jimu-*.zip')))}))
"""
    downloaded = '/var/tmp/jimu-acceptance-' + uuid.uuid4().hex + '.zip'
    try:
        ready()
        original = args.zip.read_bytes()
        status, result = upload(original)
        assert status in (200, 201), (status, result)
        print('upload:', status, json.dumps(result), flush=True)
        before = json.loads(remote(audit))
        assert before['status'] == 'archived' and before['temporary_files'] == 0, before
        expected = {'task.zip', 'task.json'} | {name for m in manifest['movements']
                                               for name in m['included_files'] if name.endswith('/detail.json')}
        assert len(before['objects']) == len(expected), before
        with zipfile.ZipFile(args.zip) as source:
            for key, metadata in before['objects'].items():
                suffix = key.split('/', 4)[4]
                assert suffix in expected
                assert metadata['size'] == (len(original) if suffix == 'task.zip' else source.getinfo(suffix).file_size)
        status, result = upload(original)
        assert status == 200 and result['already_exists'], (status, result)
        # Also retry different valid bytes under the same task ID: first archive wins.
        changed = io.BytesIO()
        with zipfile.ZipFile(args.zip) as source, zipfile.ZipFile(changed, 'w', zipfile.ZIP_DEFLATED) as target:
            for name in source.namelist():
                data = source.read(name)
                if name == 'task.json':
                    value = json.loads(data)
                    value['task']['total_score'] = 99
                    data = json.dumps(value).encode()
                target.writestr(name, data)
        assert upload(changed.getvalue())[0] == 200
        assert json.loads(remote(audit)) == before, 'duplicate modified stored objects/index'
        assert upload(original, authorized=False)[0] == 401
        assert upload(b'broken zip')[0] == 422
        for unsafe in (False, True):
            broken = io.BytesIO()
            with zipfile.ZipFile(args.zip) as source, zipfile.ZipFile(broken, 'w', zipfile.ZIP_DEFLATED) as target:
                for name in source.namelist():
                    if not unsafe and name.endswith('/detail.json'):
                        continue
                    target.writestr(name, source.read(name))
                if unsafe:
                    target.writestr('../escape', b'x')
            assert upload(broken.getvalue())[0] == 422
        assert request('POST', '/api/tasks/archive', b'', headers={'Content-Length': str(101*1024*1024)})[0] == 413
        assert json.loads(remote(audit)) == before
        print('duplicate/no-overwrite, authentication, invalid ZIP/path/missing files and oversize: passed', flush=True)
        key = next(key for key in before['objects'] if key.endswith('/task.zip'))
        remote(bootstrap + f"storage.download({key!r},Path({downloaded!r}),settings.max_zip)\n")
        with tempfile.TemporaryDirectory() as directory:
            local = Path(directory)/'download.zip'
            subprocess.run(['scp', *ssh_options, args.host + ':' + downloaded, str(local)], check=True)
            assert local.read_bytes() == original, 'download differs from original bytes'
        print('OSS download byte-for-byte comparison: passed', flush=True)
        subprocess.run([*ssh, 'systemctl restart jimu-cloud.service'], check=True)
        ready()
        status, result = request('GET', f'/api/tasks/{task_id}/archive-status')
        assert status == 200 and result['status'] == 'archived', (status, result)
        print('systemd restart and archive-status query: passed', flush=True)
        print(json.dumps(json.loads(remote(audit)), indent=2), flush=True)
    finally:
        remote(f'from pathlib import Path\nPath({downloaded!r}).unlink(missing_ok=True)\n')
        tunnel.terminate()
        tunnel.wait(timeout=10)


if __name__ == '__main__':
    main()
