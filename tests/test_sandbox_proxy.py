from skos.autopilot.sandbox_proxy import AllowlistProxy


def test_allows_only_listed_hosts():
    p = AllowlistProxy(["github.com", "gw.local"])
    assert p.is_allowed("github.com") is True
    assert p.is_allowed("GITHUB.COM") is True          # case-insensitive
    assert p.is_allowed("github.com:443") is True       # port stripped
    assert p.is_allowed("evil.example.com") is False
    assert p.is_allowed("") is False
    assert p.is_allowed("githubXcom") is False          # no substring match


def test_empty_allowlist_denies_all():
    assert AllowlistProxy([]).is_allowed("github.com") is False
