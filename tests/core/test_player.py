"""ActivePlayer helpers — tracking distance formula (PlayerInfo.cs:332)."""

from nparseplus.core.enums import PlayerClass, Server
from nparseplus.core.player import ActivePlayer, tracking_distance


def test_tracking_distance_per_class() -> None:
    assert tracking_distance(PlayerClass.RANGER, 100) == 2400.0  # skill*24
    assert tracking_distance(PlayerClass.DRUID, 100) == 2000.0  # skill*20
    assert tracking_distance(PlayerClass.BARD, 100) == 1400.0  # skill*14


def test_tracking_distance_unset_skill_defaults_to_10() -> None:
    # C#: TrackingSkill ?? 10; our stored default 0 also means unset.
    assert tracking_distance(PlayerClass.DRUID, None) == 200.0
    assert tracking_distance(PlayerClass.DRUID, 0) == 200.0


def test_tracking_distance_untrackable_classes() -> None:
    assert tracking_distance(PlayerClass.WARRIOR, 100) is None
    assert tracking_distance(PlayerClass.CLERIC, 50) is None
    assert tracking_distance(None, 100) is None


def test_server_key_and_reset() -> None:
    player = ActivePlayer()
    player.reset_for("Xantik", Server.GREEN)
    assert player.server_key == "green"
    player.tracking_skill = 50
    player.reset_for("Alt", Server.BLUE)
    assert player.server_key == "blue"
    assert player.tracking_skill is None
    player.reset_for("Nowhere", None)
    assert player.server_key is None
