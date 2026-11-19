"""Everything `lite_cache.__all__` names can be imported from the package (a name listed but not
imported only fails when someone imports it)."""

import lite_cache


def test_every_exported_name_exists() -> None:
    missing = [name for name in lite_cache.__all__ if not hasattr(lite_cache, name)]
    assert missing == []
    assert {"Verifier", "VerifierError", "ModelVerifier"} <= set(lite_cache.__all__)
