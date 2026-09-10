import httpx
import pytest

from tiny_reading_tracker.ingest import ResolutionError, resolve


def _response(status=200, json=None, text=None):
    request = httpx.Request("POST", "http://translator.test/web")
    if json is not None:
        return httpx.Response(status, json=json, request=request)
    return httpx.Response(status, text=text or "", request=request)


def test_url_posts_plain_text_to_web(monkeypatch):
    seen = {}

    def post(url, **kwargs):
        seen.update(url=url, **kwargs)
        return _response(
            200, [{"itemType": "webpage", "title": "Example", "url": "https://example.com/x"}]
        )

    monkeypatch.setattr(httpx, "post", post)
    item = resolve("https://example.com/x#part", "http://translator.test/", timeout=3.5)

    assert seen == {
        "url": "http://translator.test/web",
        "content": "https://example.com/x#part",
        "headers": {"Content-Type": "text/plain"},
        "timeout": 3.5,
    }
    assert item.title == "Example"
    assert item.metadata["itemType"] == "webpage"


def test_identifier_posts_to_search(monkeypatch):
    seen = {}

    def post(url, **kwargs):
        seen.update(url=url, **kwargs)
        return _response(
            200, [{"itemType": "journalArticle", "title": "Paper", "DOI": "10.1234/X"}]
        )

    monkeypatch.setattr(httpx, "post", post)
    item = resolve("doi:10.1234/X", "http://translator.test")
    assert seen["url"] == "http://translator.test/search"
    assert ("doi", "10.1234/x") in item.identifiers


def test_identifier_url_posts_to_web(monkeypatch):
    seen = {}

    def post(url, **kwargs):
        seen["url"] = url
        return _response(
            200, [{"itemType": "journalArticle", "title": "Paper", "DOI": "10.1234/X"}]
        )

    monkeypatch.setattr(httpx, "post", post)
    resolve("https://doi.org/10.1234/X", "http://translator.test")
    assert seen["url"] == "http://translator.test/web"


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (_response(300, json=[{"title": "one"}, {"title": "two"}]), "multiple choices"),
        (_response(200, json=[]), "no matching bibliographic item"),
        (
            _response(200, json=[{"title": "one"}, {"title": "two"}]),
            "returned 2 bibliographic items",
        ),
        (_response(200, json={"title": "not an array"}), "expected an array"),
        (_response(200, json=["not an item"]), "malformed item"),
        (_response(200, text="not json"), "invalid JSON"),
    ],
)
def test_malformed_or_ambiguous_responses_are_errors(monkeypatch, response, message):
    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: response)
    with pytest.raises(ResolutionError, match=message):
        resolve("https://example.com", "http://translator.test")


def test_network_errors_are_user_errors(monkeypatch):
    request = httpx.Request("POST", "http://translator.test/web")

    def fail(*args, **kwargs):
        raise httpx.ConnectError("connection refused", request=request)

    monkeypatch.setattr(httpx, "post", fail)
    with pytest.raises(ValueError, match="unavailable"):
        resolve("https://example.com", "http://translator.test")


def test_child_note_does_not_make_result_ambiguous(monkeypatch):
    payload = [
        {
            "key": "PARENT",
            "itemType": "preprint",
            "title": "Attention Is All You Need",
            "url": "http://arxiv.org/abs/1706.03762",
            "archiveID": "arXiv:1706.03762",
        },
        {"itemType": "note", "parentItem": "PARENT", "note": "15 pages"},
    ]
    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: _response(200, payload))

    item = resolve("https://arxiv.org/abs/1706.03762", "http://translator.test")

    assert item.title == "Attention Is All You Need"
    assert ("arxiv", "1706.03762") in item.identifiers
    assert item.metadata == payload[0]
