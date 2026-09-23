class ArchiveError(Exception):
    """Public errors contain stable codes, never dependency exception text."""

    def __init__(self, code: str, status: int = 422, retryable: bool = False):
        super().__init__(code)
        self.code = code
        self.status = status
        self.retryable = retryable
