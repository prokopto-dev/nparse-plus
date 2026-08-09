"""composition — what an inventory upload destination needs built for it.

Uploading and location sharing are separate decisions the user makes in
separate controls, so picking a destination has to bring its own plumbing
rather than depending on sharing being on.
"""

from __future__ import annotations

import pytest

from nparseplus.composition import build_backend
from nparseplus.config.settings import Settings


class StubSpeaker:
    def speak(self, *_a, **_k) -> None: ...
    def interrupt(self) -> None: ...
    def close(self) -> None: ...


def backend_for(mode: str, target: str):
    settings = Settings()
    settings.sharing.mode = mode
    settings.dumps.upload_target = target
    return build_backend(settings, speaker=StubSpeaker())


def test_no_destination_builds_no_network_plumbing() -> None:
    backend = backend_for("off", "off")
    assert backend.net_worker is None
    assert backend.planner_api is None
    assert backend.pigparse_api is None
    # The handler still exists; it just answers "off" to everything.
    assert backend.inventory_upload is not None


@pytest.mark.parametrize("target", ["pigparse", "p99planner"])
def test_a_destination_brings_its_own_worker_with_sharing_off(target: str) -> None:
    backend = backend_for("off", target)
    assert backend.net_worker is not None, "an upload needs a thread to run on"


def test_p99planner_builds_only_its_own_client() -> None:
    backend = backend_for("off", "p99planner")
    assert backend.planner_api is not None
    assert backend.pigparse_api is None  # nothing to do with pigparse


def test_pigparse_upload_works_without_location_sharing() -> None:
    """The picker offers pigparse independently of sharing.mode, so the REST
    client cannot be sharing's to own."""
    backend = backend_for("off", "pigparse")
    assert backend.pigparse_api is not None
    assert backend.sharing_client is None  # still not sharing a location


def test_sharing_and_uploading_coexist() -> None:
    backend = backend_for("pigparse", "p99planner")
    assert backend.pigparse_api is not None  # sharing's
    assert backend.planner_api is not None  # the upload destination's
    assert backend.net_worker is not None
