from repo_troubleshooter.relations.extract import extract_references

REPO = "deepseek-ai/deepseek-harness"


def kinds(refs):
    return {(r.kind, r.value) for r in refs}


class TestExtractReferences:
    def test_github_urls(self):
        text = (
            "See https://github.com/deepseek-ai/deepseek-harness/discussions/182 and "
            "https://github.com/deepseek-ai/deepseek-harness/commit/"
            "dd6322d604e00eec1ba5e0c8541159906a21094a"
        )
        found = kinds(extract_references(text, self_repo=REPO))
        assert ("discussion", "182") in found
        assert ("commit", "dd6322d604e00eec1ba5e0c8541159906a21094a") in found

    def test_release_tag_url(self):
        text = "fixed in https://github.com/deepseek-ai/deepseek-harness/releases/tag/dsh-v0.1.2"
        assert ("release", "dsh-v0.1.2") in kinds(extract_references(text, self_repo=REPO))

    def test_hash_number_reference(self):
        found = kinds(extract_references("duplicate of #391", self_repo=REPO))
        assert ("number", "391") in found

    def test_bare_sha_is_medium_confidence_when_short(self):
        refs = extract_references("try 99f6f02 or 0a53fb55bea101816fa226bb964ae2bed71c343b")
        by_value = {r.value: r for r in refs if r.kind == "commit"}
        assert by_value["99f6f02"].confidence == "medium"
        assert by_value["0a53fb55bea101816fa226bb964ae2bed71c343b"].confidence == "high"

    def test_version_mentions_are_separate_from_edges(self):
        found = kinds(extract_references("fixed in v0.1.2 and upgrade to 0.2.0"))
        assert ("version", "v0.1.2") in found
        assert ("version", "0.2.0") in found

    def test_does_not_double_count_urls_as_bare_numbers(self):
        refs = extract_references(
            "https://github.com/deepseek-ai/deepseek-harness/discussions/182", self_repo=REPO
        )
        assert not [r for r in refs if r.kind == "number"]

    def test_hex_colour_is_not_a_commit(self):
        assert not [r for r in extract_references("color: #a1b2c3d") if r.kind == "commit"]

    def test_empty(self):
        assert extract_references(None) == []
        assert extract_references("") == []
