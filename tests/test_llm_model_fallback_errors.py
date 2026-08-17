"""Unit coverage for HTTP/shape failures that must continue the fallback chain."""

import httpx
import pytest

from aletheia.model_provider.base import ProviderResponseError
from aletheia.model_provider.fallback import is_transient_model_error


def _http_status_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://example.test/v1/chat/completions")
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError("provider http error", request=request, response=response)


@pytest.mark.parametrize("status_code", [408, 500, 502, 503, 504])
def test_http_status_codes_are_transient(status_code):
    assert is_transient_model_error(_http_status_error(status_code)) is True


def test_provider_response_error_is_transient():
    assert is_transient_model_error(ProviderResponseError("openai_chat_missing_choices")) is True


def test_raw_choices_keyerror_is_not_transient():
    assert is_transient_model_error(KeyError("choices")) is False


def test_timeout_still_does_not_expand_the_chain():
    assert is_transient_model_error(httpx.ReadTimeout("provider stalled")) is False
