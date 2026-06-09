import pytest
from skos import descriptor


VALID = """
name: capauth
capability: identity
description: Sovereign PGP identity
packaging:
  oci:
    image: ghcr.io/smilintux/capauth:latest
    ports: [8088]
data: [keys]
"""


def test_load_valid(tmp_path):
    p = tmp_path / "app.yaml"
    p.write_text(VALID)
    d = descriptor.load_descriptor(p)
    assert d.name == "capauth"
    assert d.capability == "identity"
    assert d.packaging.oci.image.endswith("capauth:latest")
    assert d.packaging.oci.ports == [8088]


def test_missing_name_rejected(tmp_path):
    p = tmp_path / "app.yaml"
    p.write_text("capability: identity\npackaging: {oci: {image: x}}\n")
    with pytest.raises(descriptor.DescriptorError):
        descriptor.load_descriptor(p)


def test_no_packaging_adapter_rejected(tmp_path):
    p = tmp_path / "app.yaml"
    p.write_text("name: x\ncapability: y\npackaging: {}\n")
    with pytest.raises(descriptor.DescriptorError):
        descriptor.load_descriptor(p)
