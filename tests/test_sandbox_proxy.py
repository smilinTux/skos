from skos.autopilot.sandbox_proxy import AllowlistProxy, _target_host


def test_target_host_from_absolute_http_uri():
    assert _target_host("http://172.17.0.1:18780/v1/chat") == "172.17.0.1"


def test_target_host_from_absolute_http_uri_no_port():
    assert _target_host("http://gw.local/x") == "gw.local"


def test_target_host_from_relative_path_is_empty():
    assert _target_host("/relative") == ""


def test_target_host_feeds_allowlist_check():
    assert AllowlistProxy(["172.17.0.1"]).is_allowed(_target_host("http://172.17.0.1:18780/v1")) is True


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
