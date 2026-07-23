"""PluginMeta validation + the SDK/app compatibility handshake."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from nparseplus_sdk import PluginMeta, check_compat


def meta(**overrides: object) -> PluginMeta:
    base: dict[str, object] = {"id": "demo", "name": "Demo"}
    base.update(overrides)
    return PluginMeta.model_validate(base)


class TestPluginMeta:
    def test_minimal_meta_defaults(self) -> None:
        m = meta()
        assert m.version == "0.0.0"
        assert m.requires_sdk == ">=1.0,<2"
        assert m.min_app_version is None

    @pytest.mark.parametrize("bad", ["", "A", "UPPER", "1abc", "has space", "x" * 41, "-lead"])
    def test_invalid_ids_rejected(self, bad: str) -> None:
        with pytest.raises(ValidationError):
            meta(id=bad)

    @pytest.mark.parametrize("good", ["ab", "hello-timer", "dkp_tracker2", "a" + "b" * 39])
    def test_valid_ids_accepted(self, good: str) -> None:
        assert meta(id=good).id == good

    def test_meta_is_frozen(self) -> None:
        with pytest.raises(ValidationError):
            meta().name = "other"  # type: ignore[misc]


class TestCheckCompat:
    def test_compatible_by_default(self) -> None:
        assert check_compat(meta(), sdk_version="1.0.0") is None

    def test_sdk_below_range(self) -> None:
        reason = check_compat(meta(requires_sdk=">=2.0"), sdk_version="1.4.0")
        assert reason is not None and ">=2.0" in reason

    def test_sdk_above_range(self) -> None:
        reason = check_compat(meta(requires_sdk=">=1.0,<2"), sdk_version="2.0.0")
        assert reason is not None

    def test_invalid_specifier_is_reason_not_crash(self) -> None:
        reason = check_compat(meta(requires_sdk="not a spec"), sdk_version="1.0.0")
        assert reason is not None and "requires_sdk" in reason

    def test_unparseable_sdk_version_is_reason(self) -> None:
        reason = check_compat(meta(), sdk_version="garbage")
        assert reason is not None

    def test_min_app_version_satisfied(self) -> None:
        assert (
            check_compat(meta(min_app_version="1.10.0"), sdk_version="1.0.0", app_version="1.15.0")
            is None
        )

    def test_min_app_version_unsatisfied(self) -> None:
        reason = check_compat(
            meta(min_app_version="9.9.9"), sdk_version="1.0.0", app_version="1.15.0"
        )
        assert reason is not None and "9.9.9" in reason

    def test_min_app_version_ignored_without_app_version(self) -> None:
        assert check_compat(meta(min_app_version="9.9.9"), sdk_version="1.0.0") is None

    def test_invalid_min_app_version_is_reason(self) -> None:
        reason = check_compat(
            meta(min_app_version="not.a.version!"), sdk_version="1.0.0", app_version="1.0.0"
        )
        assert reason is not None and "min_app_version" in reason
