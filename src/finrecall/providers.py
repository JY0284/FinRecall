from __future__ import annotations

from finrecall.models import SearchError


class ProviderError(Exception):
    def __init__(
        self,
        message: str,
        *,
        error_class: str = "provider_error",
        retryable: bool = False,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_class = error_class
        self.retryable = retryable
        self.status_code = status_code

    def to_search_error(self) -> SearchError:
        return SearchError(
            message=self.message,
            error_class=self.error_class,
            retryable=self.retryable,
            status_code=self.status_code,
        )
