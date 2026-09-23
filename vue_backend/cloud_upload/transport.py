"""Public HTTP adapter for the ECS archive endpoint."""

from __future__ import annotations

import http.client
import json
from pathlib import Path
import secrets
from urllib.parse import urlsplit


class TransportError(Exception):
    def __init__(self, code: str, retryable: bool) -> None:
        self.code = code
        self.retryable = retryable
        super().__init__(code)


class HttpTransport:
    """Deep public transport behind archive, JSON and byte-download methods."""

    def __init__(self, config: dict) -> None:
        base_url = str(
            config.get("base_url")
            or config.get("cloud_url")
            or (f"http://{config['host']}" if config.get("host") else "")
        ).strip().rstrip("/")
        token = str(config.get("token", "")).strip()
        parsed = urlsplit(base_url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("云端地址无效")
        if len(token) < 32:
            raise ValueError("云端上传令牌无效")
        self.base_url = base_url
        self.token = token
        self._connected = False

    @classmethod
    def from_file(cls, path: Path) -> "HttpTransport | None":
        if not path.exists():
            return None
        try:
            config = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError("无法读取云端上传配置") from error
        if not isinstance(config, dict) or not config.get("enabled", True):
            return None
        return cls(config)

    def state(self) -> str:
        return "connected" if self._connected else "disconnected"

    def close(self) -> None:
        self._connected = False

    def archive(self, task_id: str, zip_path: Path) -> None:
        status, payload = self._request("GET", f"/api/tasks/{task_id}/archive-status", timeout=20)
        if status == 200 and payload.get("status") == "archived":
            return
        if status != 404:
            self._raise_response(status, payload)
        status, payload = self._upload(zip_path)
        if status not in (200, 201) or payload.get("status") != "archived":
            self._raise_response(status, payload)
        if payload.get("task_id") != task_id:
            raise TransportError("archive_task_mismatch", False)

    def json(self, method: str, path: str, body: dict | None = None) -> dict:
        status, payload = self._request(method, path, body=body, timeout=20)
        if status >= 400:
            self._raise_response(status, payload)
        return payload

    def bytes(self, method: str, path: str, body: dict | None = None) -> tuple[bytes, str]:
        connection = self._connection(timeout=1800)
        encoded = self._encode_body(body)
        try:
            connection.request(method, self._target(path), body=encoded, headers=self._headers(body))
            response = connection.getresponse()
            data = response.read()
            self._connected = True
            if response.status >= 400:
                self._raise_response(response.status, self._payload(data))
            return data, response.getheader("Content-Type", "application/octet-stream")
        except (OSError, http.client.HTTPException):
            self._connected = False
            raise TransportError("cloud_connection_failed", True) from None
        finally:
            connection.close()

    def _request(self, method: str, path: str, *, body: dict | None = None, timeout: int) -> tuple[int, dict]:
        connection = self._connection(timeout=timeout)
        encoded = self._encode_body(body)
        try:
            connection.request(method, self._target(path), body=encoded, headers=self._headers(body))
            response = connection.getresponse()
            payload = self._payload(response.read())
            self._connected = True
            return response.status, payload
        except (OSError, http.client.HTTPException):
            self._connected = False
            raise TransportError("cloud_connection_failed", True) from None
        finally:
            connection.close()

    def _upload(self, path: Path) -> tuple[int, dict]:
        boundary = "jimu" + secrets.token_hex(16)
        prefix = (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
            f"filename=\"task.zip\"\r\nContent-Type: application/zip\r\n\r\n"
        ).encode()
        suffix = f"\r\n--{boundary}--\r\n".encode()
        connection = self._connection(timeout=600)
        try:
            connection.putrequest("POST", self._target("/api/tasks/archive"))
            connection.putheader("Authorization", "Bearer " + self.token)
            connection.putheader("Content-Type", "multipart/form-data; boundary=" + boundary)
            connection.putheader("Content-Length", str(len(prefix) + path.stat().st_size + len(suffix)))
            connection.endheaders()
            connection.send(prefix)
            with path.open("rb") as source:
                while chunk := source.read(1024 * 1024):
                    connection.send(chunk)
            connection.send(suffix)
            response = connection.getresponse()
            payload = self._payload(response.read())
            self._connected = True
            return response.status, payload
        except (OSError, http.client.HTTPException):
            self._connected = False
            raise TransportError("cloud_connection_failed", True) from None
        finally:
            connection.close()

    def _connection(self, *, timeout: int):
        parsed = urlsplit(self.base_url)
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        connection_type = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
        return connection_type(parsed.hostname, port, timeout=timeout)

    def _target(self, path: str) -> str:
        parsed = urlsplit(self.base_url)
        base_path = parsed.path.rstrip("/")
        return base_path + (path if path.startswith("/") else "/" + path)

    def _headers(self, body: dict | None) -> dict[str, str]:
        headers = {"Authorization": "Bearer " + self.token}
        if body is not None:
            headers["Content-Type"] = "application/json"
        return headers

    @staticmethod
    def _encode_body(body: dict | None) -> bytes | None:
        return json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None

    @staticmethod
    def _payload(data: bytes) -> dict:
        try:
            value = json.loads(data)
            return value if isinstance(value, dict) else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {}

    @staticmethod
    def _raise_response(status: int, payload: dict) -> None:
        error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
        code = str(error.get("code") or f"cloud_http_{status}")
        retryable = bool(error.get("retryable", status >= 500))
        raise TransportError(code, retryable)
