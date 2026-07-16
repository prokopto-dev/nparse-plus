"""Spell gem icon loader (bundled sprite sheets) + row icon slot."""

from datetime import timedelta
from pathlib import Path

import pytest
from tests.ui.test_spellwindow import NOW, make_backend

from nparseplus.core.spells.models import Spell
from nparseplus.core.timers import YOU_GROUP, SpellRow
from nparseplus.ui.spellicons import spell_icon_pixmap
from nparseplus.ui.spellwindow import SpellTimerWindow

pytestmark = pytest.mark.qt

SHEETS = Path("data/spells")


def test_known_index_yields_scaled_pixmap(qtbot) -> None:  # qtbot: QPixmap needs a QApplication
    pixmap = spell_icon_pixmap(40, sheet_dir=SHEETS)  # sheet 2, mid-cell
    assert pixmap is not None
    assert pixmap.width() == 15 and pixmap.height() == 15
    # Cached: same object back.
    assert spell_icon_pixmap(40, sheet_dir=SHEETS) is pixmap


def test_out_of_range_or_missing_sheet_is_none(qtbot) -> None:
    assert spell_icon_pixmap(0, sheet_dir=SHEETS) is None
    assert spell_icon_pixmap(36 * 7 + 1, sheet_dir=SHEETS) is None  # sheet 8 not shipped
    assert spell_icon_pixmap(40, sheet_dir=Path("nonexistent")) is None


def test_spell_rows_show_icons_timer_rows_do_not(qtbot) -> None:
    backend = make_backend()
    backend.timers.add_spell(
        SpellRow(
            name="Iconized",
            group=YOU_GROUP,
            updated_at=NOW,
            spell=Spell(id=9, name="Iconized", spell_icon=40),
            ends_at=NOW + timedelta(minutes=5),
            total_duration_s=300.0,
        )
    )
    window = SpellTimerWindow(backend)
    qtbot.addWidget(window)
    window.refresh()
    by_name = {w.row_name: w for w in window._row_widgets.values()}
    assert by_name["Iconized"]._icon.isVisible()
    assert by_name["Iconized"]._icon.pixmap().width() == 15
    assert not by_name["Custom Timer"]._icon.isVisible()  # TimerRow: no icon
