"""`collect_all`: per-source results, failure isolation, and the probe report."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_daily_digest.collect import CollectionReport, SourceProbeStats, collect_all, collect_source
from agent_daily_digest.contracts import ErrorKind, SourceFetchStatus, SourceKind, SourceMetrics

import collect_support as s

SIMONW_URL = "https://simonwillison.net/atom/everything/"
BORISTANE_URL = "https://boristane.com/rss.xml"
CLAUDE_POST = "https://claude.com/blog/build-artifacts"


BORISTANE_IN_90_DAYS = 1
"""boristane's recorded feed has three posts; only the August one is within 90 days of the fixed clock."""


def two_feeds() -> tuple[dict, dict]:
    return (
        s.feed_source("simonw", url=SIMONW_URL),
        s.feed_source("boristane", url=BORISTANE_URL),
    )


def both_recorded() -> dict:
    return {
        SIMONW_URL: s.read("simonwillison_atom.xml"),
        BORISTANE_URL: s.read("boristane_rss.xml"),
    }


class TestOneSource:
    def test_a_successful_source_reports_its_items(self) -> None:
        report = collect_all(
            s.config(s.feed_source(url=SIMONW_URL)),
            fetcher=s.FixtureFetcher(both_recorded()),
            now=s.NOW,
        )
        result = report.result("simonw")
        assert result.status is SourceFetchStatus.SUCCEEDED
        assert result.item_count == 3
        assert result.failure is None
        assert result.source_kind is SourceKind.FIXED_WATCH

    def test_an_empty_source_succeeds_with_zero_items(self) -> None:
        empty = b'<?xml version="1.0"?><rss version="2.0"><channel><title>x</title></channel></rss>'
        report = collect_all(
            s.config(s.feed_source(url=SIMONW_URL)),
            fetcher=s.FixtureFetcher({SIMONW_URL: empty}),
            now=s.NOW,
        )
        result = report.result("simonw")
        assert result.status is SourceFetchStatus.SUCCEEDED
        assert result.item_count == 0
        assert result.failure is None

    @pytest.mark.parametrize(
        ("answer", "kind"),
        [
            ("truncated feed", "parse"),
            ("empty body", "parse"),
            ("url timeout", "timeout"),
            ("http 500", "http_status"),
            ("http 429", "rate_limit"),
            ("http 403", "authentication"),
        ],
    )
    def test_a_failing_source_records_a_classified_reason(self, answer: str, kind: str) -> None:
        bodies = {"truncated feed": b"<rss><channel", "empty body": b""}
        served = bodies[answer] if answer in bodies else s.failure(answer)
        report = collect_all(
            s.config(s.feed_source(url=SIMONW_URL)),
            fetcher=s.FixtureFetcher({SIMONW_URL: served}),
            now=s.NOW,
        )
        result = report.result("simonw")
        assert result.status is SourceFetchStatus.FAILED
        assert result.failure is not None
        assert result.failure.kind.value == kind
        assert result.item_count == 0

    def test_a_source_duration_is_recorded(self) -> None:
        report = collect_all(
            s.config(s.feed_source(url=SIMONW_URL)),
            fetcher=s.FixtureFetcher(both_recorded()),
            now=s.NOW,
        )
        assert report.result("simonw").duration_ms >= 0

    def test_a_disabled_source_is_not_collected(self) -> None:
        fetcher = s.FixtureFetcher(both_recorded())
        report = collect_all(
            s.config(s.feed_source(url=SIMONW_URL, enabled=False), *two_feeds()[1:]),
            fetcher=fetcher,
            now=s.NOW,
        )
        assert [result.source_id for result in report.results] == ["boristane"]
        assert SIMONW_URL not in fetcher.requested

    def test_repeated_links_within_one_source_are_kept_once(self) -> None:
        feed = (
            b'<?xml version="1.0"?><rss version="2.0"><channel>'
            b"<item><title>A</title><link>https://example.com/a</link></item>"
            b"<item><title>A again</title><link>https://example.com/a</link></item>"
            b"</channel></rss>"
        )
        report = collect_all(
            s.config(s.feed_source(url=SIMONW_URL)),
            fetcher=s.FixtureFetcher({SIMONW_URL: feed}),
            now=s.NOW,
        )
        # Without this the whole source would fail the contract's uniqueness rule.
        assert report.result("simonw").item_count == 1
        assert report.result("simonw").status is SourceFetchStatus.SUCCEEDED


class TestSitemapIndex:
    """A sitemap that grew into an index is a failure, not an empty source."""

    def report(self):
        return collect_all(
            s.config(s.sitemap_source()),
            fetcher=s.FixtureFetcher({s.CLAUDE_SITEMAP: s.read("sitemap_index.xml")}),
            now=s.NOW,
        )

    def test_the_source_is_recorded_as_failed(self) -> None:
        result = self.report().result("claude_blog")
        assert result.status is SourceFetchStatus.FAILED
        assert result.item_count == 0

    def test_the_failure_carries_a_machine_readable_reason(self) -> None:
        failure = self.report().result("claude_blog").failure
        assert failure.kind is ErrorKind.PARSE
        assert failure.detail.startswith("SitemapIndexError:")


class TestFailureIsolation:
    def test_one_failing_source_does_not_stop_the_others(self) -> None:
        routes = both_recorded() | {SIMONW_URL: s.timeout()}
        report = collect_all(s.config(*two_feeds(), window_days=90), fetcher=s.FixtureFetcher(routes), now=s.NOW)
        assert report.failed_source_ids == ("simonw",)
        assert report.result("boristane").status is SourceFetchStatus.SUCCEEDED
        assert report.result("boristane").item_count == BORISTANE_IN_90_DAYS

    def test_a_source_that_fails_first_does_not_stop_a_later_one(self) -> None:
        routes = both_recorded() | {BORISTANE_URL: s.http_error(500)}
        report = collect_all(s.config(*two_feeds()), fetcher=s.FixtureFetcher(routes), now=s.NOW)
        assert report.failed_source_ids == ("boristane",)
        assert report.result("simonw").item_count == 3

    def test_every_source_failing_still_produces_a_result_each(self) -> None:
        report = collect_all(
            s.config(*two_feeds()),
            fetcher=s.FixtureFetcher({}, default=s.timeout()),
            now=s.NOW,
        )
        assert len(report.results) == 2
        assert set(report.failed_source_ids) == {"simonw", "boristane"}
        assert report.items == ()

    def test_results_keep_the_registry_order(self) -> None:
        report = collect_all(s.config(*two_feeds()), fetcher=s.FixtureFetcher(both_recorded()), now=s.NOW)
        assert [result.source_id for result in report.results] == ["simonw", "boristane"]

    def test_an_unexpected_error_inside_a_connector_is_contained(self) -> None:
        class Exploding:
            def get(self, url, *, accept="*/*"):
                raise RuntimeError("connector bug")

        report = collect_all(s.config(*two_feeds()), fetcher=Exploding(), now=s.NOW)
        assert set(report.failed_source_ids) == {"simonw", "boristane"}
        assert report.result("simonw").failure.kind.value == "unexpected"


class TestProbeReport:
    def routes(self):
        return {
            s.CLAUDE_SITEMAP: s.read("claude_sitemap.xml"),
            CLAUDE_POST: s.read("claude_post_head.html"),
        }

    def test_a_feed_source_reports_no_probes(self) -> None:
        report = collect_all(
            s.config(s.feed_source(url=SIMONW_URL)),
            fetcher=s.FixtureFetcher(both_recorded()),
            now=s.NOW,
        )
        stats = report.probe("simonw")
        assert (stats.attempted, stats.succeeded, stats.failed, stats.duration_ms) == (0, 0, 0, 0)

    def test_a_sitemap_source_reports_what_it_probed(self) -> None:
        report = collect_all(
            s.config(s.sitemap_source(max_metadata_probes=2)),
            fetcher=s.FixtureFetcher(self.routes(), default=b"<html></html>"),
            now=s.NOW,
        )
        stats = report.probe("claude_blog")
        assert stats.attempted == 2
        assert stats.succeeded == 2
        assert stats.failed == 0
        assert stats.skipped_over_cap == report.result("claude_blog").item_count - 2

    def test_failed_probes_are_counted(self) -> None:
        report = collect_all(
            s.config(s.sitemap_source(max_metadata_probes=2)),
            fetcher=s.FixtureFetcher(
                {s.CLAUDE_SITEMAP: s.read("claude_sitemap.xml")}, default=s.http_error(404)
            ),
            now=s.NOW,
        )
        stats = report.probe("claude_blog")
        assert (stats.attempted, stats.succeeded, stats.failed) == (2, 0, 2)
        assert report.result("claude_blog").status is SourceFetchStatus.SUCCEEDED

    def test_probe_time_is_not_counted_twice(self) -> None:
        class SlowProbe:
            def __init__(self) -> None:
                self.routes = {s.CLAUDE_SITEMAP: s.read("claude_sitemap.xml")}

            def get(self, url, *, accept="*/*"):
                from agent_daily_digest.collect import HttpResponse

                if url in self.routes:
                    return HttpResponse(url=url, status=200, body=self.routes[url])
                deadline = __import__("time").monotonic_ns() + 20_000_000  # 20 ms
                while __import__("time").monotonic_ns() < deadline:
                    pass
                return HttpResponse(url=url, status=200, body=b"<html><head></head></html>")

        report = collect_all(
            s.config(s.sitemap_source(max_metadata_probes=2)),
            fetcher=SlowProbe(),
            now=s.NOW,
        )
        stats = report.probe("claude_blog")
        assert stats.duration_ms >= 35
        # The source's own duration excludes what the probes spent.
        assert report.result("claude_blog").duration_ms < stats.duration_ms

    def test_a_source_that_fails_after_probing_still_reports_its_probes(self) -> None:
        class FailsAfterProbe:
            def __init__(self) -> None:
                self.seen = 0

            def get(self, url, *, accept="*/*"):
                from agent_daily_digest.collect import HttpResponse

                if url == s.CLAUDE_SITEMAP:
                    return HttpResponse(url=url, status=200, body=s.read("claude_sitemap.xml"))
                self.seen += 1
                if self.seen > 1:
                    raise RuntimeError("blew up after the first probe")
                return HttpResponse(url=url, status=200, body=s.read("claude_post_head.html"))

        report = collect_all(
            s.config(s.sitemap_source(max_metadata_probes=3)),
            fetcher=FailsAfterProbe(),
            now=s.NOW,
        )
        # A probe failure is caught, so the source still succeeds and the
        # counts survive either way.
        assert report.probe("claude_blog").attempted == 3
        assert report.probe("claude_blog").succeeded == 1
        assert report.probe("claude_blog").failed == 2

    def test_there_is_one_probe_row_per_collected_source(self) -> None:
        report = collect_all(s.config(*two_feeds()), fetcher=s.FixtureFetcher(both_recorded()), now=s.NOW)
        assert [stats.source_id for stats in report.probes] == [
            result.source_id for result in report.results
        ]


class TestReportShape:
    def test_metrics_are_the_contract_type(self) -> None:
        report = collect_all(
            s.config(s.feed_source(url=SIMONW_URL)),
            fetcher=s.FixtureFetcher(both_recorded()),
            now=s.NOW,
        )
        metrics = report.metrics
        assert all(isinstance(entry, SourceMetrics) for entry in metrics)
        assert metrics[0].item_count == report.result("simonw").item_count
        assert metrics[0].duration_ms == report.result("simonw").duration_ms

    def test_metrics_of_a_failed_source_carry_the_reason(self) -> None:
        report = collect_all(
            s.config(s.feed_source(url=SIMONW_URL)),
            fetcher=s.FixtureFetcher({SIMONW_URL: s.timeout()}),
            now=s.NOW,
        )
        assert report.metrics[0].failure == report.result("simonw").failure

    def test_items_flattens_every_source(self) -> None:
        report = collect_all(s.config(*two_feeds(), window_days=90), fetcher=s.FixtureFetcher(both_recorded()), now=s.NOW)
        assert len(report.items) == 3 + BORISTANE_IN_90_DAYS

    def test_the_report_is_frozen(self) -> None:
        report = CollectionReport()
        with pytest.raises(ValidationError):
            report.results = ()

    def test_probe_stats_reject_negative_counts(self) -> None:
        with pytest.raises(ValidationError):
            SourceProbeStats(
                source_id="simonw",
                attempted=-1,
                succeeded=0,
                failed=0,
                skipped_over_cap=0,
                duration_ms=0,
            )

    def test_an_unknown_source_id_raises(self) -> None:
        report = CollectionReport()
        with pytest.raises(KeyError):
            report.result("nope")
        with pytest.raises(KeyError):
            report.probe("nope")


class TestKnownUrls:
    def test_already_processed_urls_are_passed_through_to_the_connectors(self) -> None:
        first = collect_all(
            s.config(s.feed_source(url=SIMONW_URL)),
            fetcher=s.FixtureFetcher(both_recorded()),
            now=s.NOW,
        )
        known = frozenset(item.url for item in first.items)
        second = collect_all(
            s.config(s.feed_source(url=SIMONW_URL)),
            fetcher=s.FixtureFetcher(both_recorded()),
            now=s.NOW,
            known_urls=known,
        )
        assert second.result("simonw").item_count == 0
        assert second.result("simonw").status is SourceFetchStatus.SUCCEEDED

    def test_the_default_is_empty_so_nothing_is_dropped(self) -> None:
        report = collect_all(
            s.config(s.feed_source(url=SIMONW_URL)),
            fetcher=s.FixtureFetcher(both_recorded()),
            now=s.NOW,
        )
        assert report.result("simonw").item_count == 3


class TestPerSourceWindow:
    """A feed's own `window_days` replaces the shared window for that feed alone."""

    RECENT = "https://example.com/two-days-ago"
    OLDER = "https://example.com/twenty-days-ago"

    def run(self) -> CollectionReport:
        # NOW is Fri, 18 Sep 2026 06:00 UTC.
        entries = "".join(
            f"<item><title>{url}</title><link>{url}</link><pubDate>{date}</pubDate></item>"
            for url, date in ((self.RECENT, "Wed, 16 Sep 2026 00:00:00 GMT"), (self.OLDER, "Sat, 29 Aug 2026 00:00:00 GMT"))
        )
        feed = f'<?xml version="1.0"?><rss version="2.0"><channel>{entries}</channel></rss>'.encode()
        return collect_all(
            s.config(
                s.feed_source("arxiv_surveys", url="https://a.example/feed", window_days=30),
                s.feed_source("simonw", url="https://b.example/feed"),
                window_days=7,
            ),
            fetcher=s.FixtureFetcher({"https://a.example/feed": feed, "https://b.example/feed": feed}),
            now=s.NOW,
        )

    def test_a_feed_with_its_own_window_keeps_what_the_shared_window_leaves_out(self) -> None:
        assert [item.url for item in self.run().result("arxiv_surveys").items] == [self.RECENT, self.OLDER]

    def test_a_feed_without_one_keeps_the_shared_window(self) -> None:
        assert [item.url for item in self.run().result("simonw").items] == [self.RECENT]


class TestCollectSource:
    def test_a_single_source_can_be_collected_on_its_own(self) -> None:
        import datetime as dt

        from agent_daily_digest.collect.config import SOURCE_SPEC_ADAPTER

        spec = SOURCE_SPEC_ADAPTER.validate_python(s.feed_source(url=SIMONW_URL))
        result, stats = collect_source(
            spec,
            fetcher=s.FixtureFetcher(both_recorded()),
            now=s.NOW,
            window=dt.timedelta(days=7),
            max_items=12,
        )
        assert result.item_count == 3
        assert stats.source_id == "simonw"
