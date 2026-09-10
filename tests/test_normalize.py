import pytest

from tiny_reading_tracker.normalize import input_identifiers, normalize_zotero_item


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (
            "doi:10.1234/ABC.Def",
            [("doi", "10.1234/abc.def"), ("url", "https://doi.org/10.1234/abc.def")],
        ),
        (
            "https://dx.doi.org/10.1234/ABC.Def",
            [("doi", "10.1234/abc.def"), ("url", "https://doi.org/10.1234/abc.def")],
        ),
        (
            "https://doi.org/10.1234/ABC.Def?source=x#part",
            [("doi", "10.1234/abc.def"), ("url", "https://doi.org/10.1234/abc.def")],
        ),
        (
            "arXiv:2401.12345v3",
            [("arxiv", "2401.12345"), ("url", "https://arxiv.org/abs/2401.12345")],
        ),
        (
            "https://arxiv.org/pdf/2401.12345v2.pdf",
            [("arxiv", "2401.12345"), ("url", "https://arxiv.org/abs/2401.12345")],
        ),
        (
            "PMID: 12345678",
            [("pmid", "12345678"), ("url", "https://pubmed.ncbi.nlm.nih.gov/12345678/")],
        ),
        ("ISBN: 978-0-306-40615-7", [("isbn", "9780306406157")]),
        ("HTTPS://Example.COM:443/a?q=1#part", [("url", "https://example.com/a?q=1")]),
    ],
)
def test_input_identifiers(value, expected):
    assert input_identifiers(value) == expected


@pytest.mark.parametrize(
    "value", ["", "not an identifier", "12345678", "isbn:9780306406158", "ftp://example.com/x"]
)
def test_input_identifiers_rejects_unsupported_values(value):
    with pytest.raises(ValueError):
        input_identifiers(value)


def test_normalizes_item_and_preserves_raw_metadata():
    raw = {
        "itemType": "journalArticle",
        "title": "  A Paper  ",
        "creators": [
            {"creatorType": "author", "firstName": "Ada", "lastName": "Lovelace"},
            {"creatorType": "editor", "name": "Ignored Editor"},
        ],
        "publicationTitle": "Journal of Tests",
        "date": "March 2, 2024",
        "DOI": "10.5555/EXAMPLE",
        "url": "https://publisher.example/paper#abstract",
        "extra": "PMID: 123456\narXiv: 2401.12345v4",
    }
    item = normalize_zotero_item(raw, input_value="https://publisher.example/paper")

    assert item.title == "A Paper"
    assert item.kind == "paper"
    assert item.authors == ["Ada Lovelace"]
    assert item.venue == "Journal of Tests"
    assert item.published_at == "2024-03-02"
    assert item.metadata == raw
    assert ("doi", "10.5555/example") in item.identifiers
    assert ("pmid", "123456") in item.identifiers
    assert ("arxiv", "2401.12345") in item.identifiers


def test_arxiv_input_and_metadata_versions_collapse_to_one_alias():
    item = normalize_zotero_item(
        {
            "itemType": "preprint",
            "title": "Versioned",
            "url": "https://arxiv.org/pdf/2401.12345v2.pdf",
            "extra": "arXiv: 2401.12345v7",
        },
        input_value="https://arxiv.org/abs/2401.12345v1",
    )
    assert item.identifiers.count(("arxiv", "2401.12345")) == 1
    assert item.url == "https://arxiv.org/abs/2401.12345"


def test_arxiv_doi_adds_arxiv_alias():
    item = normalize_zotero_item(
        {
            "itemType": "preprint",
            "title": "An arXiv paper",
            "DOI": "10.48550/arXiv.1706.03762",
        }
    )
    assert item.kind == "paper"
    assert ("doi", "10.48550/arxiv.1706.03762") in item.identifiers
    assert ("arxiv", "1706.03762") in item.identifiers


def test_doi_parenthesis_is_not_discarded():
    assert input_identifiers("doi:10.1000/example(test)")[0] == (
        "doi",
        "10.1000/example(test)",
    )


def test_ipv6_url_keeps_brackets_and_whitespace_is_rejected():
    assert input_identifiers("http://[::1]:8080/path") == [("url", "http://[::1]:8080/path")]
    with pytest.raises(ValueError):
        input_identifiers("https://example.com/a b")


def test_export_arxiv_host_deduplicates():
    assert input_identifiers("https://export.arxiv.org/pdf/2401.12345v2.pdf") == input_identifiers(
        "arxiv:2401.12345"
    )


def test_port_zero_does_not_alias_default_port():
    assert input_identifiers("http://example.com:0/x") != input_identifiers("http://example.com/x")
