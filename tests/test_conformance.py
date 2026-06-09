import subprocess
import pytest
from skos.descriptor import AppDescriptor
from skos.packaging.base import PackagingAdapter, InstallResult
from skos.packaging.oci import OciAdapter

ADAPTERS = [OciAdapter]  # every new adapter is added here and must pass


def _app():
    return AppDescriptor.model_validate({
        "name": "probe", "capability": "test",
        "packaging": {"oci": {"image": "example/probe:1", "ports": []}},
    })


@pytest.fixture(autouse=True)
def _mock_runtime(monkeypatch):
    monkeypatch.setattr("skos.packaging.oci.runtime.run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 0, "id123", ""))


@pytest.mark.parametrize("AdapterCls", ADAPTERS)
def test_adapter_implements_port(AdapterCls):
    assert issubclass(AdapterCls, PackagingAdapter)
    a = AdapterCls()
    assert isinstance(a.name, str) and a.name


@pytest.mark.parametrize("AdapterCls", ADAPTERS)
def test_materialize_returns_install_result(AdapterCls):
    res = AdapterCls().materialize(_app())
    assert isinstance(res, InstallResult)
    assert res.adapter == AdapterCls().name


@pytest.mark.parametrize("AdapterCls", ADAPTERS)
def test_locate_and_remove_callable(AdapterCls):
    a = AdapterCls()
    a.locate(_app())          # must not raise
    a.remove(_app())          # must be idempotent / not raise
