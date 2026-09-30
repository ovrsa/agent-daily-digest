"""話題別配置と概要・本文の対応。入力は合成記事のみ。"""
import pytest
import render_factories as f
from pydantic import ValidationError
from agent_daily_digest.contracts.editorial import SelectorOutput


def test_topics_group_before_priority_and_share_overview_numbers():
    payload = f.selector_payload()
    for item in payload['must_read'] + payload['worth_knowing']:
        item['section'] = 'coding_agent'
    payload['must_read'][0]['section'] = 'hermes_use_cases'
    rendered = f.render(SelectorOutput.model_validate(payload))
    assert rendered.index('## Coding Agent\n') < rendered.index('## Hermes系Agentの活用事例\n')
    overview, body = rendered.split('## Coding Agent\n', 1)
    assert '1. **Coding Agent / Must Read**' in overview
    assert '5. **Hermes系Agentの活用事例 / Must Read**' in overview
    assert '#### 5. [Splitting plan and act' in body
    assert body.count('Splitting plan and act') == 1


@pytest.mark.parametrize('section,empty', [('coding_agent', 'Hermes系Agentの活用事例'), ('hermes_use_cases', 'Coding Agent')])
def test_empty_topic_visible_without_filling_it(section, empty):
    payload = f.selector_payload()
    for item in payload['must_read'] + payload['worth_knowing']:
        item['section'] = section
    rendered = f.render(SelectorOutput.model_validate(payload))
    assert f'## {empty}\n\n本日の採用記事はありません。' in rendered


def test_topic_is_required_and_rejects_unknown_values():
    payload = f.selector_payload()
    payload['must_read'][0].pop('section', None)
    with pytest.raises(ValidationError):
        SelectorOutput.model_validate(payload)
    payload['must_read'][0]['section'] = 'both'
    with pytest.raises(ValidationError):
        SelectorOutput.model_validate(payload)


def test_cross_topic_duplicate_cannot_be_published_twice():
    payload = f.selector_payload()
    item = dict(payload['must_read'][0], section='hermes_use_cases')
    payload['worth_knowing'].append(item)
    with pytest.raises(ValidationError, match='exactly one'):
        SelectorOutput.model_validate(payload)


def test_caps_remain_global_across_topics():
    payload = f.selector_payload()
    payload['must_read'] = [f.included_payload(f'a{i:03}') | {'section': 'coding_agent' if i % 2 else 'hermes_use_cases'} for i in range(6)]
    with pytest.raises(ValidationError):
        SelectorOutput.model_validate(payload)


@pytest.mark.parametrize('fail', [False, True])
def test_two_topics_publish_one_file_and_restore_all_files_on_failure(tmp_path, fail):
    from agent_daily_digest.contracts.articles import ProcessedState
    from agent_daily_digest.publish import publish_selection

    directory = tmp_path / 'digests'
    directory.mkdir()
    index = directory / 'README.md'
    index.write_text(f.README_TEMPLATE)
    state_path = tmp_path / 'processed.json'
    state_path.write_text('{"records": []}\n')
    before = {index: index.read_bytes(), state_path: state_path.read_bytes()}
    payload = f.selector_payload()
    payload['must_read'][0]['section'] = 'hermes_use_cases'
    output = SelectorOutput.model_validate(payload)

    class Publisher:
        def publish(self, files, message):
            assert [path.name for path in files] == ['2026-09-18.md', 'README.md', 'processed.json']
            digest = files[0].read_text()
            assert '## Coding Agent\n' in digest and '## Hermes系Agentの活用事例\n' in digest
            assert index.read_text().count('(./2026-09-18.md)') == 1
            if fail:
                raise RuntimeError('synthetic publication failure')
            return 'test-commit'

    def publish():
        return publish_selection(output, f.articles(), ProcessedState(), f.DIGEST_DATE,
                                 digests_dir=directory, state_path=state_path, publisher=Publisher(), adopted=True)

    if fail:
        with pytest.raises(RuntimeError):
            publish()
        assert not (directory / '2026-09-18.md').exists()
        assert all(path.read_bytes() == data for path, data in before.items())
    else:
        assert publish().digest_path == directory / '2026-09-18.md'
