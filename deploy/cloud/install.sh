#!/usr/bin/env bash
set -euo pipefail
# Run as root from a release directory containing cloud_backend/ and deploy/cloud/.
cd "$(dirname "$0")/../.."
test "$(id -u)" = 0
test "$(python3 -c 'import platform; print(platform.python_version())')" = '3.14.4'
test -f vue_frontend/dist/index.html

# Compatibility check happens before user/config/database/service creation.
install -d -m 755 /opt/jimu-cloud
if python3 -c 'import ensurepip' 2>/dev/null; then
    python3 -m venv /opt/jimu-cloud/venv
else
    # Ubuntu minimal images omit ensurepip. Install only the pure pip wheel;
    # python3.14-venv can require an unwanted system Python package upgrade.
    apt-get install -y --no-upgrade python3-pip-whl
    python3 -m venv --without-pip /opt/jimu-cloud/venv
    python3 - <<'PY'
from pathlib import Path
import subprocess
wheel = next(Path('/usr/share/python-wheels').glob('pip-*.whl'))
subprocess.run(['/opt/jimu-cloud/venv/bin/python', str(wheel) + '/pip',
                'install', '--no-index', str(wheel)], check=True)
PY
fi
/opt/jimu-cloud/venv/bin/python -m pip install -r cloud_backend/requirements-test.txt
/opt/jimu-cloud/venv/bin/python -m pip check
/opt/jimu-cloud/venv/bin/python -m unittest discover -s cloud_backend/tests -v

if systemctl is-active --quiet jimu-cloud.service; then
    systemctl stop jimu-cloud.service
fi
id jimu-cloud >/dev/null 2>&1 || useradd --system --home-dir /var/lib/jimu-cloud --shell /usr/sbin/nologin jimu-cloud
install -d -m 750 -o jimu-cloud -g jimu-cloud /var/lib/jimu-cloud /var/tmp/jimu-cloud /var/log/jimu-cloud
install -d -m 700 /etc/jimu-cloud
install -d -m 755 /opt/jimu-cloud/app/cloud_backend/storage
for file in cloud_backend/*.py cloud_backend/requirements*.txt; do
    install -m 644 "$file" /opt/jimu-cloud/app/cloud_backend/
done
for file in cloud_backend/storage/*.py; do
    install -m 644 "$file" /opt/jimu-cloud/app/cloud_backend/storage/
done
install -d -m 755 /opt/jimu-cloud/web
find /opt/jimu-cloud/web -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
cp -R vue_frontend/dist/. /opt/jimu-cloud/web/
find /opt/jimu-cloud/web -type d -exec chmod 755 {} +
find /opt/jimu-cloud/web -type f -exec chmod 644 {} +
if [ ! -f /etc/jimu-cloud/service.env ]; then
    umask 077
    /opt/jimu-cloud/venv/bin/python - <<'PY'
from pathlib import Path
import secrets
Path('/etc/jimu-cloud/service.env').write_text(
    'JIMU_UPLOAD_TOKEN=' + secrets.token_urlsafe(48) + '\n'
    'JIMU_OSS_BUCKET=jimu-motion-data-bj-2026\n'
    'JIMU_OSS_REGION=cn-beijing\n'
    'JIMU_OSS_ROLE=JimuEcsOssRole\n', encoding='utf-8')
PY
fi
chmod 600 /etc/jimu-cloud/service.env
install -m 644 deploy/cloud/jimu-cloud.service /etc/systemd/system/jimu-cloud.service
systemctl daemon-reload
systemctl enable jimu-cloud.service
systemctl restart jimu-cloud.service
for attempt in $(seq 1 30); do
    if curl --fail --silent http://127.0.0.1:8767/health; then
        break
    fi
    if [ "$attempt" = 30 ]; then
        systemctl status jimu-cloud.service --no-pager
        exit 1
    fi
    sleep 1
done

if ! command -v nginx >/dev/null 2>&1; then
    apt-get install -y --no-upgrade nginx
fi
install -m 644 deploy/cloud/jimu-cloud.nginx /etc/nginx/sites-available/jimu-cloud
rm -f /etc/nginx/sites-enabled/default
ln -sfn /etc/nginx/sites-available/jimu-cloud /etc/nginx/sites-enabled/jimu-cloud
nginx -t
systemctl enable nginx.service
systemctl restart nginx.service
curl --fail --silent http://127.0.0.1/cloud-history >/dev/null
printf '\nJimu cloud archive and history page are ready.\n'
