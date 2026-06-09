"""Tests for skos.install.profiles — PERSONAL-FIRST capability recommendations."""
from __future__ import annotations

import pytest
from skos.install.profiles import InstallProfile, recommended, PROFILE_CAPS


class TestInstallProfile:
    def test_enum_values(self):
        assert InstallProfile.LOCAL.value == "local"
        assert InstallProfile.CLUSTER.value == "cluster"
        assert InstallProfile.CLOUD.value == "cloud"

    def test_from_str(self):
        assert InstallProfile("local") is InstallProfile.LOCAL
        assert InstallProfile("cluster") is InstallProfile.CLUSTER
        assert InstallProfile("cloud") is InstallProfile.CLOUD


class TestRecommended:
    def test_local_includes_personal_first_set(self):
        caps = recommended(InstallProfile.LOCAL)
        assert set(caps) >= {"capauth", "skmemory", "skchat", "skfence", "skmon"}

    def test_cluster_extends_local(self):
        local = set(recommended(InstallProfile.LOCAL))
        cluster = set(recommended(InstallProfile.CLUSTER))
        # cluster is a superset of local
        assert local <= cluster
        # cluster adds at least one more
        assert len(cluster) > len(local)

    def test_cloud_extends_cluster(self):
        cluster = set(recommended(InstallProfile.CLUSTER))
        cloud = set(recommended(InstallProfile.CLOUD))
        assert cluster <= cloud

    def test_returns_list_of_strings(self):
        for profile in InstallProfile:
            caps = recommended(profile)
            assert isinstance(caps, list)
            assert all(isinstance(c, str) for c in caps)

    def test_no_duplicates(self):
        for profile in InstallProfile:
            caps = recommended(profile)
            assert len(caps) == len(set(caps))

    def test_all_caps_exist_in_catalog(self):
        """Every recommended cap must be a real catalog entry."""
        from skos.capability import Catalog
        cat = Catalog.load()
        catalog_names = {c.name for c in cat.all()}
        for profile in InstallProfile:
            for cap in recommended(profile):
                assert cap in catalog_names, f"{cap!r} not in catalog (profile={profile.value})"

    def test_profile_caps_dict_covers_all_profiles(self):
        for profile in InstallProfile:
            assert profile in PROFILE_CAPS
