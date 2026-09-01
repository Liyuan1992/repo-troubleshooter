from repo_troubleshooter.normalize.content import split_body

DISCUSSION_BODY = """I upgraded and my plugin stopped loading.

```
Error: Service `llm` not found
    at Registry.get (C:\\Users\\abc\\project\\foo.ts:137:11)
```

My config:

```json
{"plugins": {"foo": {"version": "1.4"}}}
```

Any idea what changed?
"""


class TestSplitBody:
    def test_separates_prose_log_and_config(self):
        units = split_body(DISCUSSION_BODY)
        types = [u.unit_type for u in units]
        assert "log" in types
        assert "config" in types
        assert types.count("prose") >= 2

    def test_keeps_document_order(self):
        units = split_body(DISCUSSION_BODY)
        assert [u.seq for u in units] == sorted(u.seq for u in units)
        assert units[0].unit_type == "prose"

    def test_log_block_is_kept_whole(self):
        log = next(u for u in split_body(DISCUSSION_BODY) if u.unit_type == "log")
        assert "Service `llm` not found" in log.text
        assert "at Registry.get" in log.text

    def test_code_fence_language_is_recorded(self):
        config = next(u for u in split_body(DISCUSSION_BODY) if u.unit_type == "config")
        assert config.lang == "json"

    def test_empty_input(self):
        assert split_body(None) == []
        assert split_body("   \n  ") == []

    def test_unterminated_fence_still_captured(self):
        units = split_body("before\n\n```\nTraceback (most recent call last):\n")
        assert any(u.unit_type == "log" for u in units)

    def test_nested_longer_fence_does_not_close_early(self):
        body = "````\n```\ninner\n```\n````"
        units = split_body(body)
        assert len(units) == 1
        assert "inner" in units[0].text
