"""The Mob Info window rendering the wiki page (#113).

Two things here are load-bearing beyond "does it show up": wiki text is
third-party and reaches RichText labels, so it is escaped and its URLs are
screened; and the window is an overlay over EverQuest, so a picture and a
long drop table must not stop it shrinking to its minimum.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtGui import QColor, QPixmap

from nparseplus.config.settings import Settings
from nparseplus.core.handlers.consider import LootPrice, MobInfoState
from nparseplus.net.p99wiki import WikiDrop, WikiLink, WikiNpc
from nparseplus.ui.mobinfo import MobInfoWindow, link_html, safe_url, stat_rows

NAGAFEN = WikiNpc(
    title="Lord Nagafen",
    name="Lord Nagafen",
    race="Dragon",
    npc_class="Warrior",
    level="60",
    hp="52000",
    ac="450",
    damage_per_hit="200 - 400",
    attacks_per_round="2",
    attack_speed="100%",
    run_speed="1.6",
    aggro_radius="200",
    spawn_location="100% @ (-1000, 200)",
    respawn="7 days",
    specials=(WikiLink(name="Summon", url=""),),
    factions=(WikiLink(name="Lord Nagafen", url="https://wiki.project1999.com/x", note="(-30)"),),
    related_quests=(WikiLink(name="Shawl Quest", url="https://wiki.project1999.com/q"),),
    drops=(WikiDrop(name="Cloak of Flames", url="https://wiki.project1999.com/c", rarity="Rare"),),
    url="https://wiki.project1999.com/Lord_Nagafen",
)


@pytest.fixture
def state() -> MobInfoState:
    return MobInfoState(name="Lord Nagafen", zone="soldungb", wiki=NAGAFEN)


def _window(qtbot, settings: Settings, state: MobInfoState) -> MobInfoWindow:
    window = MobInfoWindow(settings, state)
    qtbot.addWidget(window)
    window.refresh()
    return window


def test_the_stat_block_renders(qtbot, state: MobInfoState) -> None:
    window = _window(qtbot, Settings(), state)
    stats = window.current_stats()
    for value in ("52000", "450", "200 - 400", "100%", "1.6", "200"):
        assert value in stats
    assert "Level 60" in window.current_subtitle()
    assert "Warrior" in window.current_subtitle()


def test_only_the_fields_the_page_has_get_a_row() -> None:
    sparse = WikiNpc(title="a rat", name="a rat", hp="30")
    assert stat_rows(sparse) == [("HP", "30")]
    assert stat_rows(None) == []


def test_spawn_location_and_respawn(qtbot, state: MobInfoState) -> None:
    window = _window(qtbot, Settings(), state)
    detail = window.current_detail()
    assert "Spawn: 100% @ (-1000, 200)" in detail
    assert "Wiki respawn: 7 days" in detail


def test_both_respawn_figures_show_because_they_disagree(qtbot, state: MobInfoState) -> None:
    """ZoneDatabase.spawn_time always answers — it falls back to a zone
    default and then to a global 6:40 — so it cannot say "no idea". A wiki
    that says "7 days" beside a 22:00 default is the useful half."""
    state.spawn_seconds = 1320
    window = _window(qtbot, Settings(), state)
    detail = window.current_detail()
    assert "Respawn: 22:00" in detail
    assert "Wiki respawn: 7 days" in detail


def test_a_wiki_that_shrugs_adds_no_line(qtbot) -> None:
    shrug = NAGAFEN.model_copy(update={"respawn": "?"})
    state = MobInfoState(name="x", spawn_seconds=400, wiki=shrug)
    window = _window(qtbot, Settings(), state)
    assert "Wiki respawn" not in window.current_detail()


def test_factions_specials_and_quests_render_with_their_notes(qtbot, state: MobInfoState) -> None:
    window = _window(qtbot, Settings(), state)
    extra = window.current_extra()
    assert "Summon" in extra and "(-30)" in extra and "Shawl Quest" in extra
    assert 'href="https://wiki.project1999.com/q"' in extra


def test_loot_shows_price_and_rarity(qtbot, state: MobInfoState) -> None:
    state.loot = [
        LootPrice(
            name="Cloak of Flames",
            url="https://wiki.project1999.com/c",
            price="42000",
            price_url="https://pigparse.azurewebsites.net/ItemDetails/1",
            rarity="Rare",
        )
    ]
    window = _window(qtbot, Settings(), state)
    loot = window.current_loot()
    assert "Cloak of Flames" in loot and "42000p" in loot and "(Rare)" in loot


def test_a_pet_is_never_dressed_as_a_wiki_page(qtbot, state: MobInfoState) -> None:
    state.is_pet = True
    window = _window(qtbot, Settings(), state)
    assert window.current_stats() == "" and window.current_extra() == ""
    assert "(pet)" in window.current_name()


def test_a_mob_with_no_page_renders_what_it_always_did(qtbot) -> None:
    state = MobInfoState(name="a lava basilisk", zone="soldungb", spawn_seconds=1320)
    window = _window(qtbot, Settings(), state)
    assert "a lava basilisk" in window.current_name()
    assert "Respawn: 22:00" in window.current_detail()
    assert window.current_stats() == "" and window.current_subtitle() == ""


def test_an_unreachable_wiki_explains_why_no_details_appeared(qtbot) -> None:
    state = MobInfoState(name="Lord Nagafen", wiki_unreachable=True)
    window = _window(qtbot, Settings(), state)
    assert "Wiki: unavailable" in window.current_detail()


# --- escaping (the #102 class of bug) -------------------------------------------


def test_wiki_text_is_escaped_not_interpreted(qtbot) -> None:
    hostile = WikiNpc(
        title="x",
        name="x",
        hp="<b>bold</b>",
        drops=(WikiDrop(name='Sword <img src=x onerror="alert(1)">', url="", rarity="<i>R</i>"),),
        factions=(WikiLink(name="<script>", url="", note='"quoted"'),),
    )
    state = MobInfoState(name="x", wiki=hostile)
    state.loot = [LootPrice(name=hostile.drops[0].name, url="", rarity="<i>R</i>")]
    window = _window(qtbot, Settings(), state)
    assert "&lt;b&gt;bold&lt;/b&gt;" in window.current_stats()
    assert "<b>bold</b>" not in window.current_stats()
    assert "onerror" not in window.current_loot() or "&lt;img" in window.current_loot()
    assert "<script>" not in window.current_extra()


def test_a_url_that_is_not_http_is_dropped() -> None:
    assert safe_url("javascript:alert(1)") == ""
    assert safe_url("") == ""
    assert safe_url("https://wiki.project1999.com/x") == "https://wiki.project1999.com/x"
    # ... and the name still renders, just not as a link.
    assert link_html("Cloak", "javascript:alert(1)") == "Cloak"
    assert 'href="https://wiki.project1999.com/x"' in link_html(
        "Cloak", "https://wiki.project1999.com/x"
    )


def test_a_quote_in_a_url_cannot_break_out_of_the_attribute() -> None:
    html = link_html("x", 'https://wiki.project1999.com/a"onclick="evil()')
    assert '"onclick="' not in html
    assert "&quot;" in html


# --- the picture ----------------------------------------------------------------


def _png(tmp_path: Path) -> Path:
    pixmap = QPixmap(64, 48)
    pixmap.fill(QColor("#804020"))
    path = tmp_path / "mob.png"
    assert pixmap.save(str(path))
    return path


def test_the_picture_renders_and_follows_its_setting(qtbot, tmp_path: Path) -> None:
    npc = NAGAFEN.model_copy(update={"image_path": _png(tmp_path)})
    state = MobInfoState(name="Lord Nagafen", wiki=npc)
    settings = Settings()
    window = _window(qtbot, settings, state)
    window.show()
    assert window.has_image()

    settings.mobinfo.show_image = False
    window.apply_mobinfo_settings()  # what Settings > Apply calls
    assert not window.has_image()

    settings.mobinfo.show_image = True
    window.apply_mobinfo_settings()
    assert window.has_image()


def test_a_picture_that_will_not_load_is_simply_absent(qtbot, tmp_path: Path) -> None:
    broken = tmp_path / "broken.png"
    broken.write_bytes(b"not a png")
    state = MobInfoState(name="x", wiki=NAGAFEN.model_copy(update={"image_path": broken}))
    window = _window(qtbot, Settings(), state)
    assert not window.has_image()


def test_the_window_minimum_does_not_grow_with_its_content(qtbot, tmp_path: Path) -> None:
    """A picture plus a full drop table must not push the overlay bigger.

    That is what the scrolling body buys: the minimum is the chrome around
    it (title bar, headline, the wiki button), never what the wiki page
    happened to carry.
    """
    plain = MobInfoState(name="Lord Nagafen", wiki=NAGAFEN)
    plain.loot = [LootPrice(name="Cloak of Flames", url="", price="42000")]
    lean = _window(qtbot, Settings(), plain)
    lean.show()
    floor = lean.layout().minimumSize()

    heavy = MobInfoState(
        name="Lord Nagafen", wiki=NAGAFEN.model_copy(update={"image_path": _png(tmp_path)})
    )
    heavy.loot = [LootPrice(name=f"Item {i}", url="", price=str(i)) for i in range(30)]
    loaded = _window(qtbot, Settings(), heavy)
    loaded.show()
    assert loaded.layout().minimumSize() == floor


def test_the_wiki_button_opens_the_pages_own_url(qtbot, state: MobInfoState, monkeypatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr("webbrowser.open", opened.append)
    window = _window(qtbot, Settings(), state)
    window._open_wiki()
    assert opened == ["https://wiki.project1999.com/Lord_Nagafen"]


def test_without_a_page_the_button_falls_back_to_the_name(qtbot, monkeypatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr("webbrowser.open", opened.append)
    window = _window(qtbot, Settings(), MobInfoState(name="a lava basilisk"))
    window._open_wiki()
    assert opened == ["https://wiki.project1999.com/a_lava_basilisk"]
