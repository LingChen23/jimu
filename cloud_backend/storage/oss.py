"""Official OSS SDK adapter. Authentication and bounded retries stay here."""

from pathlib import Path

from ..errors import ArchiveError


def _service_error(error, sdk):
    """Return the OSS response error hidden by the V2 SDK operation wrapper."""
    seen = set()
    current = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, sdk.exceptions.ServiceError):
            return current
        unwrap = getattr(current, 'unwrap', None)
        current = unwrap() if callable(unwrap) else None
    return None


class OssStorage:
    def __init__(self, settings):
        import alibabacloud_oss_v2 as oss
        from alibabacloud_credentials.client import Client
        from alibabacloud_credentials.models import Config

        credentials = Client(Config(type='ecs_ram_role', role_name=settings.role,
                                    disable_imds_v1=True))

        def obtain():
            value = credentials.get_credential()
            return oss.credentials.Credentials(value.access_key_id, value.access_key_secret,
                                               value.security_token)

        cfg = oss.config.load_default()
        cfg.credentials_provider = oss.credentials.CredentialsProviderFunc(obtain)
        cfg.region = settings.region
        cfg.endpoint = f'https://oss-{settings.region}-internal.aliyuncs.com'
        cfg.retry_max_attempts = 3
        cfg.connect_timeout = 10
        cfg.readwrite_timeout = 60
        self.sdk = oss
        self.client = oss.Client(cfg)
        self.bucket = settings.bucket

    def size(self, key):
        try:
            result = self.client.head_object(self.sdk.HeadObjectRequest(bucket=self.bucket, key=key))
            return result.content_length
        except Exception as error:
            service_error = _service_error(error, self.sdk)
            if (service_error is not None and service_error.status_code == 404
                    and service_error.code == 'NoSuchKey'):
                return None
            raise ArchiveError('oss_unavailable', 503, True) from None

    def put(self, key, stream, size, content_type, *, create_only=False):
        try:
            self.client.put_object(self.sdk.PutObjectRequest(
                bucket=self.bucket, key=key, body=stream, content_length=size,
                content_type=content_type, forbid_overwrite=create_only))
        except Exception as error:
            service_error = _service_error(error, self.sdk)
            if (create_only and service_error is not None
                    and service_error.status_code == 409
                    and service_error.code == 'FileAlreadyExists'):
                return False
            raise ArchiveError('oss_unavailable', 503, True) from None
        if self.size(key) != size:
            raise ArchiveError('oss_size_mismatch', 503, True)
        return True

    def download(self, key, target: Path, limit: int):
        try:
            response = self.client.get_object(self.sdk.GetObjectRequest(bucket=self.bucket, key=key))
            try:
                total = 0
                with target.open('wb') as output:
                    for chunk in response.body.iter_bytes(chunk_size=1024 * 1024):
                        total += len(chunk)
                        if total > limit:
                            raise ArchiveError('stored_archive_too_large', 422)
                        output.write(chunk)
            finally:
                response.body.close()
        except ArchiveError:
            raise
        except Exception:
            raise ArchiveError('oss_unavailable', 503, True) from None

    def stream(self, key, chunk_size=1024 * 1024):
        """Open an OSS object now and return a closing byte iterator."""
        try:
            response = self.client.get_object(
                self.sdk.GetObjectRequest(bucket=self.bucket, key=key)
            )
        except Exception as error:
            service_error = _service_error(error, self.sdk)
            if (service_error is not None and service_error.status_code == 404
                    and service_error.code == 'NoSuchKey'):
                raise ArchiveError('archive_file_missing', 503, True) from None
            raise ArchiveError('oss_unavailable', 503, True) from None

        def chunks():
            try:
                yield from response.body.iter_bytes(chunk_size=chunk_size)
            except Exception:
                raise ArchiveError('oss_unavailable', 503, True) from None
            finally:
                response.body.close()

        return chunks()
