#!/usr/bin/env bash
set -euo pipefail

public_key_file="${1:?public key file is required}"
test "$(id -u)" = 0
public_key="$(tr -d '\r\n' < "$public_key_file")"
case "$public_key" in
    ssh-ed25519\ *) ;;
    *) echo "an ed25519 public key is required" >&2; exit 1 ;;
esac

if ! id jimu-upload >/dev/null 2>&1; then
    useradd --system --create-home --home-dir /var/lib/jimu-upload \
        --shell /usr/sbin/nologin jimu-upload
fi
# Public-key authentication must not be rejected as a locked system account.
# OpenSSH's default PermitEmptyPasswords=no keeps the empty password unusable.
passwd -d jimu-upload >/dev/null
install -d -m 700 -o jimu-upload -g jimu-upload /var/lib/jimu-upload/.ssh
temporary="$(mktemp /var/lib/jimu-upload/.ssh/authorized_keys.XXXXXX)"
printf '%s %s\n' \
    'no-agent-forwarding,no-X11-forwarding,no-pty,permitopen="127.0.0.1:8767"' \
    "$public_key" > "$temporary"
chown jimu-upload:jimu-upload "$temporary"
chmod 600 "$temporary"
mv "$temporary" /var/lib/jimu-upload/.ssh/authorized_keys

# Earlier development versions installed a per-user sshd Match block.  The
# single-PC prototype only needs the dedicated account and authorized key, so
# remove that optional server-wide configuration if it exists.
if test -f /etc/ssh/sshd_config.d/90-jimu-upload.conf; then
    rm /etc/ssh/sshd_config.d/90-jimu-upload.conf
    /usr/sbin/sshd -t
    systemctl reload ssh.service
fi
