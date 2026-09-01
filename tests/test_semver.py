from repo_troubleshooter.versions import semver


class TestNormalize:
    def test_strips_real_world_tag_decoration(self):
        assert semver.normalize_version("dsh-v0.1.2-alpha.3") == "0.1.2a3"
        assert semver.normalize_version("v0.2.3") == "0.2.3"
        assert semver.normalize_version("release-1.4") == "1.4"
        assert semver.normalize_version("foo@1.4") == "1.4"
        assert semver.normalize_version("0.1.1-rc.2") == "0.1.1rc2"

    def test_unparseable_returns_none_instead_of_guessing(self):
        for raw in (None, "", "latest", "main", "nightly", "master", "v"):
            assert semver.normalize_version(raw) is None


class TestCompare:
    def test_orders_prereleases_below_final(self):
        assert semver.compare("0.1.2-alpha.3", "0.1.2") == -1
        assert semver.compare("0.1.1-rc.2", "0.1.2-alpha.1") == -1
        assert semver.compare("0.2.0", "0.2.0") == 0

    def test_unknown_side_yields_none_not_false(self):
        assert semver.compare("0.1.0", "latest") is None
        assert semver.compare("main", "0.1.0") is None

    def test_prerelease_flag(self):
        assert semver.is_prerelease("dsh-v0.1.2-alpha.3") is True
        assert semver.is_prerelease("v1.0.0") is False
        assert semver.is_prerelease("main") is None


class TestSortKey:
    def test_unparseable_sorts_last_deterministically(self):
        tags = ["main", "dsh-v0.1.2-alpha.3", "dsh-v0.1.0-rc.7", "dsh-v0.1.1-rc.2"]
        assert sorted(tags, key=semver.sort_key) == [
            "dsh-v0.1.0-rc.7",
            "dsh-v0.1.1-rc.2",
            "dsh-v0.1.2-alpha.3",
            "main",
        ]


class TestVersionRange:
    def test_bounds(self):
        rng = semver.VersionRange(min_inclusive="0.1.0", max_exclusive="0.2.0")
        assert rng.contains("0.1.5") is True
        assert rng.contains("0.0.9") is False
        assert rng.contains("0.2.0") is False

    def test_unknown_version_is_unknown_not_excluded(self):
        rng = semver.VersionRange(min_inclusive="0.1.0")
        assert rng.contains("nightly") is None
