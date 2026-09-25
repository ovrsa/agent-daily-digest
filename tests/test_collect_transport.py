from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from contextlib import contextmanager

import pytest

from agent_daily_digest.collect import (
    DETAIL_MAX_CHARS,
    HttpResponse,
    HttpSettings,
    ResponseTooLargeError,
    UnsafeXmlError,
    UrllibFetcher,
    classify_failure,
    make_fetcher,
    parse_json,
    parse_xml,
)
from agent_daily_digest.collect import transport
from agent_daily_digest.contracts import ErrorKind

import collect_support as s


class TestParseXml:
    @pytest.mark.parametrize(
        "name",
        [
            "simonwillison_atom.xml",
            "boristane_rss.xml",
            "nyosegawa_rss.xml",
            "addyosmani_rss.xml",
            "latent_space_rss.xml",
            "interconnects_rss.xml",
            "ai_news_smol_rss.xml",
            "reddit_localllama_atom.xml",
            "claude_sitemap.xml",
            "anthropic_sitemap.xml",
        ],
    )
    def test_every_recorded_document_parses(self, name: str) -> None:
        assert parse_xml(s.read(name)).tag

    def test_namespaces_survive_as_elementtree_qualified_names(self) -> None:
        root = parse_xml(s.read("simonwillison_atom.xml"))
        assert root.tag == "{http://www.w3.org/2005/Atom}feed"
        assert root.findall("{http://www.w3.org/2005/Atom}entry")

    def test_an_unprefixed_rss_keeps_unqualified_names(self) -> None:
        root = parse_xml(s.read("boristane_rss.xml"))
        assert root.tag == "rss"
        assert root.findall(".//item")

    def test_entity_declarations_are_refused(self) -> None:
        # "Billion laughs": ElementTree would expand this.
        document = (
            b'<!DOCTYPE feed [<!ENTITY a "xx"><!ENTITY b "&a;&a;&a;">]>'
            b"<feed><title>&b;</title></feed>"
        )
        with pytest.raises(UnsafeXmlError):
            parse_xml(document)

    def test_an_external_entity_is_refused_before_it_is_fetched(self) -> None:
        document = (
            b'<!DOCTYPE feed [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
            b"<feed><title>&x;</title></feed>"
        )
        with pytest.raises(UnsafeXmlError):
            parse_xml(document)

    @pytest.mark.parametrize(
        "document", [b"", b"<feed><unclosed>", b'{"not": "xml"}', b"<feed>&undeclared;</feed>"]
    )
    def test_broken_documents_raise_parse_error(self, document: bytes) -> None:
        with pytest.raises(ET.ParseError):
            parse_xml(document)


    def test_the_parser_refuses_external_entity_references(self) -> None:
        """Pinned on the parser, because no document can reach the handler.

        `EntityDeclHandler` refuses the declaration, so expat never gets as
        far as resolving a reference, and removing this second handler changes
        no parse result. It is the layer that would hold if the first one were
        ever relaxed, so it is pinned where it is set.
        """
        built = []
        real = transport.expat.ParserCreate

        def recording(*args, **kwargs):
            parser = real(*args, **kwargs)
            built.append(parser)
            return parser

        transport.expat.ParserCreate = recording
        try:
            parse_xml(b"<feed/>")
        finally:
            transport.expat.ParserCreate = real

        handler = built[0].ExternalEntityRefHandler
        assert handler is not None
        with pytest.raises(UnsafeXmlError):
            handler("ctx", "base", "sysid", "pubid")


class TestParseJson:
    def test_reads_a_recorded_payload(self) -> None:
        assert isinstance(parse_json(s.read("hf_papers.json")), list)

    def test_broken_json_raises(self) -> None:
        with pytest.raises(json.JSONDecodeError):
            parse_json(b"{")


class TestClassifyFailure:
    @pytest.mark.parametrize(("name", "kind"), sorted(s.FAILURE_KINDS.items(), key=str))
    def test_kind(self, name: str, kind: ErrorKind) -> None:
        assert classify_failure(s.failure(name)).kind is kind

    def test_an_http_error_detail_is_the_status_only(self) -> None:
        # The exception's own message repeats the URL; the status is enough.
        assert classify_failure(s.http_error(503)).detail == "HTTP 503"

    def test_detail_names_the_exception_type(self) -> None:
        assert classify_failure(ValueError("boom")).detail == "ValueError: boom"

    def test_detail_is_capped_well_under_the_contract_limit(self) -> None:
        detail = classify_failure(ValueError("x" * 5000)).detail
        assert detail is not None
        assert len(detail) == DETAIL_MAX_CHARS

    def test_detail_is_collapsed_to_a_single_line(self) -> None:
        assert classify_failure(ValueError("a\n\tb  c")).detail == "ValueError: a b c"

    def test_a_blank_message_still_leaves_a_usable_detail(self) -> None:
        assert classify_failure(ValueError("   ")).detail == "ValueError"

    def test_every_classified_failure_fits_the_error_record_contract(self) -> None:
        for exc in (s.http_error(500), TimeoutError(), ET.ParseError("bad"), ValueError("x" * 900)):
            record = classify_failure(exc)
            assert record.detail is None or len(record.detail) <= DETAIL_MAX_CHARS


class _FakeResponse:
    def __init__(self, body: bytes, url: str = "https://example.com/x") -> None:
        self._body = body
        self._url = url
        self.status = 200

    def read(self, amount: int | None = None) -> bytes:
        return self._body if amount is None else self._body[:amount]

    def geturl(self) -> str:
        return self._url

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


@contextmanager
def _urlopen_returning(body: bytes):
    original = transport.urllib.request.urlopen
    transport.urllib.request.urlopen = lambda *_a, **_kw: _FakeResponse(body)
    try:
        yield
    finally:
        transport.urllib.request.urlopen = original


class TestUrllibFetcher:
    def test_reads_a_response_that_fits(self) -> None:
        fetcher = UrllibFetcher(timeout_seconds=1, user_agent="test", max_response_bytes=16)
        with _urlopen_returning(b"0123456789"):
            assert fetcher.get("https://example.com/x").body == b"0123456789"

    def test_a_response_exactly_at_the_cap_is_accepted(self) -> None:
        fetcher = UrllibFetcher(timeout_seconds=1, user_agent="test", max_response_bytes=10)
        with _urlopen_returning(b"0123456789"):
            assert len(fetcher.get("https://example.com/x").body) == 10

    def test_a_response_over_the_cap_is_refused(self) -> None:
        fetcher = UrllibFetcher(timeout_seconds=1, user_agent="test", max_response_bytes=10)
        with _urlopen_returning(b"0123456789!"), pytest.raises(ResponseTooLargeError):
            fetcher.get("https://example.com/x")


class TestHttpResponse:
    def test_text_replaces_undecodable_bytes_instead_of_raising(self) -> None:
        assert HttpResponse(url="u", status=200, body=b"a\xffb").text() == "a�b"

    def test_text_can_be_limited(self) -> None:
        assert HttpResponse(url="u", status=200, body=b"abcdef").text(limit=3) == "abc"


@contextmanager
def _urlopen_recording(body: bytes):
    """Like `_urlopen_returning`, but keeps what the fetcher asked for."""
    calls: list[tuple[object, dict]] = []
    original = transport.urllib.request.urlopen

    def fake(request, **kwargs):
        calls.append((request, kwargs))
        return _FakeResponse(body)

    transport.urllib.request.urlopen = fake
    try:
        yield calls
    finally:
        transport.urllib.request.urlopen = original


class TestMakeFetcher:
    """Every value in `collection.http` has to reach the fetcher.

    Nothing read `HttpSettings` before: the block was declared in
    `config.json` and no code applied it, so a caller could build a
    `UrllibFetcher` with other values and the config would say nothing.
    """

    SETTINGS = HttpSettings(
        timeout_seconds=7.5,
        user_agent="agent-daily-digest/test (+https://example.com)",
        max_response_bytes=2048,
    )

    def test_the_timeout_reaches_the_request(self) -> None:
        with _urlopen_recording(b"ok") as calls:
            make_fetcher(self.SETTINGS).get("https://example.com/x")
        assert calls[0][1]["timeout"] == 7.5

    def test_the_user_agent_reaches_the_request(self) -> None:
        with _urlopen_recording(b"ok") as calls:
            make_fetcher(self.SETTINGS).get("https://example.com/x")
        assert calls[0][0].get_header("User-agent") == self.SETTINGS.user_agent

    def test_the_byte_cap_reaches_the_reader(self) -> None:
        with _urlopen_recording(b"x" * 2049), pytest.raises(ResponseTooLargeError):
            make_fetcher(self.SETTINGS).get("https://example.com/x")

    def test_a_response_inside_the_cap_is_returned_whole(self) -> None:
        with _urlopen_recording(b"x" * 2048):
            assert len(make_fetcher(self.SETTINGS).get("https://example.com/x").body) == 2048
