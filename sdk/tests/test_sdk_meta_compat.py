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


class TestPrereleaseAppVersions:
    """``min_app_version`` against a PRERELEASE host.

    This matters when a host-backed capability debuts in a beta: the audience
    the feature ships to is running the prerelease, so the pin has to admit it.
    The comparison is ``packaging.version.Version`` (PEP 440), so
    ``2.31.0-beta.1`` normalises to ``2.31.0b1`` and orders BELOW ``2.31.0``.

    The consequence is the whole point, and it is counter-intuitive enough to
    pin: naming the STABLE release refuses every beta host, and naming the
    PRERELEASE admits the betas AND every later release. A feature debuting in
    a prerelease therefore pins the prerelease.
    """

    BETA = "2.31.0-beta.1"

    def _refused(self, pin: str, app: str) -> bool:
        return (
            check_compat(meta(min_app_version=pin), sdk_version="1.0.0", app_version=app)
            is not None
        )

    def test_a_prerelease_string_is_accepted_as_a_pin(self) -> None:
        # No validator narrows min_app_version, so the spelling survives verbatim.
        assert meta(min_app_version=self.BETA).min_app_version == self.BETA

    @pytest.mark.parametrize("host", ["2.31.0-beta.1", "2.31.0-beta.2", "2.31.0rc1"])
    def test_pinning_the_stable_release_refuses_every_prerelease_of_it(self, host: str) -> None:
        """The trap. A 2.31.0 pin locks out exactly the users a beta ships to."""
        assert self._refused("2.31.0", host)

    @pytest.mark.parametrize(
        "host", ["2.31.0-beta.1", "2.31.0-beta.2", "2.31.0rc1", "2.31.0", "2.32.0"]
    )
    def test_pinning_the_prerelease_admits_it_and_everything_after(self, host: str) -> None:
        assert not self._refused(self.BETA, host)

    def test_pinning_the_prerelease_still_refuses_the_release_before_it(self) -> None:
        # It must not become a pin that admits everything.
        assert self._refused(self.BETA, "2.30.0")

    def test_semver_and_pep440_spellings_of_the_same_prerelease_agree(self) -> None:
        # '-beta.1' and 'b1' normalise to one version, so the pin cannot be
        # accidentally stricter or looser depending on how it was written.
        for pin in ("2.31.0-beta.1", "2.31.0b1"):
            assert not self._refused(pin, "2.31.0-beta.1")
            assert self._refused(pin, "2.30.0")
