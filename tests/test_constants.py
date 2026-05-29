"""Unit tests for the JSON-RPC response predicates (no browser required).

These guard the core determinism property: a *search* response must not satisfy
the *read* predicate (and vice versa), regardless of whether model/method are in
the URL path or only in the POST body.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from odoo_extract.constants import (
    ACCOUNT_MOVE_MODEL,
    is_read_response,
    is_search_response,
    rpc_meta,
)


@dataclass
class _FakeRequest:
    post_data: str | None


@dataclass
class _FakeResponse:
    url: str
    request: _FakeRequest


def _resp(url: str, *, model=None, method=None) -> _FakeResponse:
    body = None
    if model is not None or method is not None:
        body = json.dumps({"params": {"model": model, "method": method}})
    return _FakeResponse(url=url, request=_FakeRequest(post_data=body))


CALL_KW = "https://x.odoo.com/web/dataset/call_kw"


def test_body_is_source_of_truth_for_bare_call_kw():
    search = _resp(CALL_KW, model=ACCOUNT_MOVE_MODEL, method="web_search_read")
    read = _resp(CALL_KW, model=ACCOUNT_MOVE_MODEL, method="web_read")

    # The historic bug: on the bare endpoint both collapsed to identical.
    assert is_search_response(search) and not is_read_response(search)
    assert is_read_response(read) and not is_search_response(read)


def test_search_response_never_matches_read_predicate():
    # "read" is a substring of "web_search_read" — must NOT trip is_read.
    search = _resp(CALL_KW, model=ACCOUNT_MOVE_MODEL, method="web_search_read")
    assert is_search_response(search) is True
    assert is_read_response(search) is False


def test_url_path_fallback_when_no_body():
    read = _resp(f"{CALL_KW}/account.move/web_read")  # no post body
    search = _resp(f"{CALL_KW}/account.move/web_search_read")
    assert is_read_response(read) is True
    assert is_search_response(read) is False
    assert is_search_response(search) is True
    assert is_read_response(search) is False


def test_read_predicate_scoped_to_account_move():
    other = _resp(CALL_KW, model="res.partner", method="web_read")
    assert is_read_response(other) is False  # right method, wrong model


def test_read_predicate_allows_unknown_model():
    # Model undeterminable but method is a read -> accepted (capture filters).
    unknown = _resp(f"{CALL_KW}/web_read")
    model, method = rpc_meta(unknown)
    assert method == "web_read" and model is None
    assert is_read_response(unknown) is True


def test_non_dataset_url_is_neither():
    other = _resp("https://x.odoo.com/web/image/123")
    assert is_search_response(other) is False
    assert is_read_response(other) is False
    assert rpc_meta(other) == (None, None)


@pytest.mark.parametrize("method", ["search_read", "web_search_read"])
def test_all_search_methods(method):
    assert is_search_response(_resp(CALL_KW, model=ACCOUNT_MOVE_MODEL, method=method))


def test_malformed_body_falls_back_gracefully():
    resp = _FakeResponse(
        url=f"{CALL_KW}/account.move/web_read",
        request=_FakeRequest(post_data="{not valid json"),
    )
    # Body unparseable -> URL path fallback still resolves it.
    assert is_read_response(resp) is True
