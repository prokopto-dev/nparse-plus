"""P99 wiki (wiki.project1999.com) NPC lookup.

The wiki is a MediaWiki whose NPC/merchant pages are structured templates::

    {{Namedmobpage
    | imagefilename     = Mraaka.jpg
    | name              = Mraaka
    | level             = 66
    | zone              = [[Western Wastes]]
    | location          = 100% @ (-1699, -73)
    | respawn_time      = ?
    | AC                = 492
    | HP                = ~51k (?)
    | known_loot        = <ul><li> {{:Wurm Meat}} ...
    ...

``search`` uses the opensearch API; ``npc`` fetches the raw wikitext and
parses the template fields. Wiki ``location`` is in game (X, Y) order —
map-file/scene coordinates are ``(-y, -x)`` of that (calibrated against
NPCs that appear both on the wiki and as map labels, e.g. Lord Nagafen).

Field set and parsing are a port of EQTool's
``ViewModels/MobInfoComponents/MobInfoViewModel.cs`` +
``Services/Parsing/MobInfoParsing.cs`` (#113). Deliberate divergences from
the C#, each marked at the code that implements it:

* the page image is resolved through the MediaWiki API instead of
  string-building ``/images/<file>``;
* drop rarity (``<span class='drare'>``) is kept rather than stripped;
* a link's trailing text becomes ``WikiLink.note`` instead of being glued
  onto its name, and a section whose only content is "None" renders as
  empty rather than as a row saying None.

Being a good citizen to a volunteer-run wiki is part of the contract: one
page request per mob considered, answers cached per title behind
:data:`CACHE_TTL_S`, images cached on disk, and the 6 s timeout kept.

All failures degrade to ``None``/``[]``; never raises to callers.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from nparseplus.core.zones import ZoneDatabase

logger = logging.getLogger(__name__)

BASE_URL = "https://wiki.project1999.com"
TIMEOUT_S = 6.0
#: How long a parsed page stays good. NPC pages change on the order of
#: months; this only has to stop a session re-asking for a mob camped for an
#: hour, so it is measured in minutes, not seconds.
CACHE_TTL_S = 3600.0
#: Width asked of the MediaWiki thumbnailer. The window renders the picture
#: a few hundred pixels wide at most, and a full-size upload can be MBs.
IMAGE_WIDTH = 320
#: Ceiling on a cached image, in case the thumbnailer hands back the original.
MAX_IMAGE_BYTES = 4 * 1024 * 1024
_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp"})

# A one-line field. The separators are HORIZONTAL whitespace on purpose: an
# \s* after the "=" swallows the newline of an empty field ("| attacks_per_round =")
# and captures the NEXT field's line as its value.
_FIELD_RE = re.compile(r"^\|[ \t]*(?P<key>\w+)[ \t]*=[ \t]*(?P<value>.*?)[ \t]*$", re.MULTILINE)
_LINK_RE = re.compile(r"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]")
_LOC_RE = re.compile(r"\(\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)")
_HTML_RE = re.compile(r"<.*?>", re.DOTALL)
_SPACES_RE = re.compile(r"[ \t]{2,}")
_REDIRECT_RE = re.compile(r"^\s*#REDIRECT\s*\[\[([^\]|]+)", re.IGNORECASE)

# A template field's value runs until the next top-level "|field =" line, the
# template's closing "}}", or the end of the page — the multi-line fields
# (known_loot, factions, special, ...) are what needs this.
_BLOCK_END = r"(?=^\|\s*\w+\s*=|^\}\}|\Z)"
# "{{ :Item Name }}" transclusions and "[[Page|Display]]" / "[[Page]]" links.
_TRANSCLUSION_RE = re.compile(r"\{\{\s*:?\s*(?P<name>[^}|]+?)\s*\}\}")
_PIPED_LINK_RE = re.compile(r"\[\[\s*(?P<name>[^|\]]+?)\s*\|[^\]]*\]\]")
_PLAIN_LINK_RE = re.compile(r"\[\[\s*(?P<name>[^|\]]+?)\s*\]\](?P<trailing>[^\[\n]*)")
# The wiki's own drop-rarity markup, e.g. <span class='drare'>(Ultra Rare)</span>.
_RARITY_RE = re.compile(r"class=['\"]?drare['\"]?[^>]*>\s*(?P<text>[^<]*)", re.IGNORECASE)
# Drop-rate annotations the rarity does not cover: "[Overall: 35.0%]", "[1] 1x 25%".
_BRACKETED_RE = re.compile(r"\[[^\[\]]*\]")
_PARENTHETICAL_RE = re.compile(r"\([^()]*\)")
# Shape B: pages with no known_loot field carry the drops under a heading.
_LOOT_HEADING_RE = re.compile(
    r"^=+\s*(?:known\s+)?(?:loot|drops?|loot\s+table)\s*=+\s*$", re.IGNORECASE | re.MULTILINE
)
_HEADING_RE = re.compile(r"^=+[^=\n]+=+\s*$", re.MULTILINE)
_TABLE_RE = re.compile(r"^\{\|.*?^\|\}", re.DOTALL | re.MULTILINE)
_LOOTY_TABLE_RE = re.compile(r"!.*\b(item|loot|drop)", re.IGNORECASE)
# Links that are page furniture rather than content.
_NAMESPACES = ("file:", "image:", "category:", "media:", "template:")
_FACTION_SUFFIX = " (Faction)"


class WikiLink(BaseModel):
    """One named thing on the page, with its wiki page when it has one.

    ``note`` is the trailing annotation the wiki puts beside a link — a
    faction adjustment ``(-30)``, an AE's description. EQTool concatenates
    it onto the name; keeping it separate is what lets the window render the
    name as the link and the note as muted text beside it.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    url: str = ""
    note: str = ""


class WikiDrop(BaseModel):
    """One row of the mob's drop table."""

    model_config = ConfigDict(frozen=True)

    name: str
    url: str = ""
    #: "Common", "Rare", "Ultra Rare", "Always" — "" when the page is silent.
    #: EQTool strips this span; a drop table without it reads much worse.
    rarity: str = ""


class WikiNpc(BaseModel):
    model_config = ConfigDict(frozen=True)

    title: str
    name: str
    race: str = ""
    npc_class: str = ""
    level: str = ""
    zone_display: str = ""
    zone_short: str | None = None
    # Game-coordinate (x, y) from the wiki page, if stated.
    location: tuple[float, float] | None = None
    #: The whole ``location`` field as written. P99 routinely lists several
    #: spawn points ("50% @ (a), 50% @ (b)"); ``location`` keeps only the
    #: first pair, which is right for the map and wrong for a human asking
    #: where the thing spawns.
    spawn_location: str = ""
    respawn: str = ""
    description: str = ""
    url: str = ""

    # -- the stat block (MobComponent.xaml's left column) ----------------------
    hp: str = ""
    ac: str = ""
    hp_regen: str = ""
    mana_regen: str = ""
    aggro_radius: str = ""
    run_speed: str = ""
    attacks_per_round: str = ""
    attack_speed: str = ""
    damage_per_hit: str = ""

    # -- list fields ----------------------------------------------------------
    specials: tuple[WikiLink, ...] = ()
    factions: tuple[WikiLink, ...] = ()
    opposing_factions: tuple[WikiLink, ...] = ()
    related_quests: tuple[WikiLink, ...] = ()
    drops: tuple[WikiDrop, ...] = ()

    # -- the picture ----------------------------------------------------------
    #: The raw ``imagefilename`` field ("Mraaka.jpg"), before resolution.
    image_file: str = ""
    #: Resolved through the MediaWiki API (see ``P99WikiClient.image_url``).
    image_url: str | None = None
    #: Where the client cached the bytes. The UI turns this into a QPixmap —
    #: net/ never imports Qt.
    image_path: Path | None = None

    @property
    def map_location(self) -> tuple[float, float] | None:
        """Scene/map-file coordinates: (-y, -x) of the game location."""
        if self.location is None:
            return None
        x, y = self.location
        return (-y, -x)

    @property
    def has_details(self) -> bool:
        """Did the page carry anything worth a stat block?

        Non-NPC pages (disambiguations, quest articles) parse into a WikiNpc
        with nothing but a title, and the window must not draw an empty
        frame around that.
        """
        return bool(
            self.hp
            or self.ac
            or self.level
            or self.damage_per_hit
            or self.drops
            or self.factions
            or self.specials
        )


def strip_links(text: str) -> str:
    """[[A|B]] -> B, [[A]] -> A; drops surrounding wiki markup."""
    return _LINK_RE.sub(lambda m: m.group(1), text).strip()


def strip_html(text: str) -> str:
    """MobInfoParsing.StripHTML — the wiki fields carry <ul>/<span> markup."""
    return _HTML_RE.sub("", text)


def _tidy(text: str) -> str:
    """Collapse the runs of spaces the wiki uses to align its field values."""
    return _SPACES_RE.sub(" ", text.replace("\xa0", " ")).strip()


def page_url(title: str, base_url: str = BASE_URL) -> str:
    return f"{base_url}/{title.strip().replace(' ', '_')}"


def parse_template_fields(wikitext: str) -> dict[str, str]:
    return {m.group("key").lower(): m.group("value") for m in _FIELD_RE.finditer(wikitext)}


def field_block(wikitext: str, name: str) -> str:
    """The whole value of a template field, including its extra lines."""
    match = re.search(
        rf"^\|\s*{re.escape(name)}\s*=\s*(?P<value>.*?){_BLOCK_END}",
        wikitext,
        re.MULTILINE | re.DOTALL,
    )
    return match.group("value").strip() if match else ""


def _item_lines(block: str) -> Iterator[str]:
    """One entry per line: the wiki writes these as <li> runs or * bullets."""
    for line in re.split(r"</li>|<li>|<br\s*/?>|\n", block):
        stripped = line.strip()
        if stripped:
            yield stripped


def _link_from(text: str, base_url: str) -> tuple[str, str, str] | None:
    """(name, url, trailing) for the first wiki link in ``text``, else None.

    Port of MobInfoParsing's index-walking: a ``{{:X}}`` transclusion and a
    piped ``[[Page|Display]]`` both name the PAGE, never the display text —
    "[[Form of the Great Bear|Spell: Form of the Great Bear]]" is the spell,
    not "Spell: ...".
    """
    hit = _TRANSCLUSION_RE.search(text)
    if hit is not None:
        name = _tidy(hit.group("name"))
        return (name, page_url(name, base_url), _tidy(text[hit.end() :])) if name else None
    hit = _PIPED_LINK_RE.search(text)
    if hit is not None:
        name = _tidy(hit.group("name"))
        return (name, page_url(name, base_url), _tidy(text[hit.end() :])) if name else None
    hit = _PLAIN_LINK_RE.search(text)
    if hit is not None:
        name = _tidy(hit.group("name"))
        return (name, page_url(name, base_url), _tidy(hit.group("trailing"))) if name else None
    return None


def _is_content_link(name: str) -> bool:
    return not name.lower().startswith(_NAMESPACES)


def parse_links(block: str, base_url: str = BASE_URL) -> list[WikiLink]:
    """A list field (special, factions, related_quests) as links + notes."""
    out: list[WikiLink] = []
    seen: set[str] = set()
    for line in _item_lines(block):
        if "casts:" in line.lower():
            continue  # MobInfoParsing skips these; the spell links still land
        for chunk in strip_html(line).split(","):
            text = _tidy(chunk.lstrip("*# "))
            if not text:
                continue
            found = _link_from(text, base_url)
            if found is None:
                if _PARENTHETICAL_RE.fullmatch(text) and out:
                    # "<li>[[Wave of Heat]]</li>(PB AE 200 Fire Damage)" — the
                    # gloss lands on its own line once the markup is gone. It
                    # describes the entry above it, not a second special.
                    if not out[-1].note:
                        out[-1] = out[-1].model_copy(update={"note": text})
                    continue
                name, url, note = _tidy(_BRACKETED_RE.sub("", text)), "", ""
            else:
                name, url, note = found
            # "[[Trakanon (Faction)]]" names a faction page; the faction is
            # Trakanon. MobInfoParsing has the same special case.
            if name.endswith(_FACTION_SUFFIX):
                name = name[: -len(_FACTION_SUFFIX)].strip()
            if not name or name.casefold() == "none" or not _is_content_link(name):
                # EQTool renders a literal "None" row; an overlay is better
                # off hiding the whole section (deliberate divergence).
                continue
            if name.casefold() in seen:
                continue
            seen.add(name.casefold())
            out.append(WikiLink(name=name, url=url, note=note))
    return out


def _drop_from(line: str, base_url: str) -> WikiDrop | None:
    rarity_hit = _RARITY_RE.search(line)
    rarity = _tidy(rarity_hit.group("text")).strip("()") if rarity_hit else ""
    text = _tidy(strip_html(line).lstrip("*# "))
    if not text:
        return None
    found = _link_from(text, base_url)
    if found is not None:
        name, url = found[0], found[1]
    else:
        # A plain-text drop ("Level 50+ Velious Spells"): strip the drop-rate
        # annotations the markup put beside it, since they are not its name.
        cleaned = _BRACKETED_RE.sub("", text)
        if rarity:
            cleaned = cleaned.replace(f"({rarity})", "")
        name, url = _tidy(cleaned), ""
    if not name or name.casefold() == "none" or not _is_content_link(name):
        return None
    return WikiDrop(name=name, url=url, rarity=rarity)


def _drops_from_field(block: str, base_url: str) -> list[WikiDrop]:
    out: list[WikiDrop] = []
    seen: set[str] = set()
    for line in _item_lines(block):
        if "casts:" in line.lower():
            continue
        drop = _drop_from(line, base_url)
        if drop is None or drop.name.casefold() in seen:
            continue
        seen.add(drop.name.casefold())
        out.append(drop)
    return out


def _loot_sections(wikitext: str) -> Iterable[str]:
    """Shape B: the page has no known_loot field, so find the loot prose.

    Two hints, because P99 uses both: a "== Loot ==" heading (everything up
    to the next heading belongs to it) and a wikitable whose header row names
    items or drops.
    """
    for match in _LOOT_HEADING_RE.finditer(wikitext):
        rest = wikitext[match.end() :]
        next_heading = _HEADING_RE.search(rest)
        yield rest[: next_heading.start()] if next_heading else rest
    for table in _TABLE_RE.finditer(wikitext):
        body = table.group(0)
        if _LOOTY_TABLE_RE.search(body):
            yield body


def _drops_from_body(wikitext: str, base_url: str) -> list[WikiDrop]:
    out: list[WikiDrop] = []
    seen: set[str] = set()
    for section in _loot_sections(wikitext):
        for line in _item_lines(section):
            if line.startswith(("{|", "|}", "|-", "!")) or "casts:" in line.lower():
                continue
            for cell in re.split(r"\|\||\n", line):
                drop = _drop_from(cell.lstrip("|"), base_url)
                # Only linked cells count here. The known_loot field is a list
                # of items and a bare line in it is an item; a table cell is
                # as likely to be a rarity or a drop rate.
                if drop is None or not drop.url or drop.name.casefold() in seen:
                    continue
                seen.add(drop.name.casefold())
                out.append(drop)
    return out


#: The template fields that make a page an NPC's rather than a zone's or a
#: quest's. A P99 NPC page states at least one of them.
_NPC_FIELDS = ("hp", "ac", "level", "damage_per_hit", "attacks_per_round", "agro_radius")


def is_npc_page(wikitext: str) -> bool:
    fields = parse_template_fields(wikitext)
    return any(key in fields for key in _NPC_FIELDS)


def parse_drops(wikitext: str, base_url: str = BASE_URL) -> list[WikiDrop]:
    """The mob's drop table, from whichever shape the page uses."""
    block = field_block(wikitext, "known_loot")
    # A page that HAS the field and lists "None" drops nothing — falling back
    # to the body scan there would go looking for a table that is not its own.
    if block:
        return _drops_from_field(block, base_url)
    # The body scan is a guess, so it only runs on a page that is an NPC's:
    # "Cazic Thule" redirects to the ZONE article, whose tables would
    # otherwise be read as the god's drop table.
    if not is_npc_page(wikitext):
        return []
    return _drops_from_body(wikitext, base_url)


def parse_image_filename(wikitext: str) -> str:
    """The ``imagefilename`` field, trimmed at the extension.

    The field is sometimes followed by a comment or a second name; EQTool
    cuts at the first ``.png``/``.jpg`` and so do we.
    """
    raw = _tidy(strip_html(parse_template_fields(wikitext).get("imagefilename", "")))
    if not raw:
        return ""
    lowered = raw.lower()
    for suffix in (".png", ".jpg", ".jpeg", ".gif"):
        index = lowered.find(suffix)
        if index != -1:
            return raw[: index + len(suffix)]
    return raw


def parse_npc(title: str, wikitext: str, zones: ZoneDatabase | None = None) -> WikiNpc:
    fields = parse_template_fields(wikitext)
    base = BASE_URL

    def scalar(key: str) -> str:
        return _tidy(strip_html(strip_links(fields.get(key, ""))))

    zone_display = strip_links(fields.get("zone", ""))
    zone_short = zones.short_name(zone_display) if (zones and zone_display) else None
    spawn_location = scalar("location")
    loc_match = _LOC_RE.search(fields.get("location", ""))
    location = (float(loc_match.group(1)), float(loc_match.group(2))) if loc_match else None
    name = strip_links(fields.get("name", "")) or title
    # EQTool trims an allakhazam citation glued onto the name field.
    name = _tidy(name.split("[")[0]) or title
    return WikiNpc(
        title=title,
        name=name,
        race=scalar("race"),
        npc_class=scalar("class").strip("'"),
        level=scalar("level"),
        zone_display=zone_display,
        zone_short=zone_short,
        location=location,
        spawn_location=spawn_location,
        respawn=scalar("respawn_time"),
        description=strip_links(fields.get("description", "")),
        url=page_url(title, base),
        hp=scalar("hp"),
        ac=scalar("ac"),
        hp_regen=scalar("hp_regen"),
        mana_regen=scalar("mana_regen"),
        aggro_radius=scalar("agro_radius"),
        run_speed=scalar("run_speed"),
        attacks_per_round=scalar("attacks_per_round"),
        attack_speed=scalar("attack_speed"),
        damage_per_hit=scalar("damage_per_hit").split("\n")[0],
        specials=tuple(parse_links(field_block(wikitext, "special"), base)),
        factions=tuple(parse_links(field_block(wikitext, "factions"), base)),
        opposing_factions=tuple(parse_links(field_block(wikitext, "opposing_factions"), base)),
        related_quests=tuple(parse_links(field_block(wikitext, "related_quests"), base)),
        drops=tuple(parse_drops(wikitext, base)),
        image_file=parse_image_filename(wikitext),
    )


def parse_image_url(payload: str) -> str | None:
    """The thumbnail (or original) URL out of an ``imageinfo`` answer.

    Tolerates both API shapes: ``query.pages`` is a dict keyed by page id on
    older MediaWiki and a list under ``formatversion=2``.
    """
    try:
        data = json.loads(payload)
    except Exception:
        return None
    pages = (data.get("query") or {}).get("pages")
    if isinstance(pages, dict):
        pages = list(pages.values())
    for page in pages or []:
        if not isinstance(page, dict):
            continue
        for info in page.get("imageinfo") or []:
            url = info.get("thumburl") or info.get("url")
            if isinstance(url, str) and url:
                return url
    return None


def _cache_name(url: str) -> str:
    suffix = Path(urlparse(url).path).suffix.lower()
    if suffix not in _IMAGE_SUFFIXES:
        suffix = ".img"
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:24] + suffix


class P99WikiClient:
    def __init__(
        self,
        base_url: str = BASE_URL,
        zones: ZoneDatabase | None = None,
        client: httpx.Client | None = None,
        image_cache_dir: Path | None = None,
        ttl_s: float = CACHE_TTL_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._zones = zones
        self._client = client or httpx.Client(
            timeout=TIMEOUT_S, headers={"User-Agent": "nparseplus"}, follow_redirects=True
        )
        self._image_dir = image_cache_dir
        self._ttl = ttl_s
        self._clock = clock
        self._search_cache: dict[str, list[str]] = {}
        #: title -> (fetched-at, result). The stamp is what makes re-considering
        #: a mob free: the window polls twice a second and must never fetch.
        self._npc_cache: dict[str, tuple[float, WikiNpc | None]] = {}

    def search(self, query: str, limit: int = 8) -> list[str]:
        """Page titles matching the query (opensearch)."""
        key = f"{query.lower()}|{limit}"
        if key in self._search_cache:
            return self._search_cache[key]
        try:
            resp = self._client.get(
                f"{self._base}/api.php",
                params={
                    "action": "opensearch",
                    "search": query,
                    "limit": str(limit),
                    "format": "json",
                },
            )
            resp.raise_for_status()
            payload = json.loads(resp.text)
            titles = [t for t in payload[1] if isinstance(t, str)]
        except Exception:
            logger.warning("wiki search failed for %r", query, exc_info=True)
            return []
        self._search_cache[key] = titles
        return titles

    def npc(self, title: str, *, with_image: bool = False) -> WikiNpc | None:
        """Fetch and parse one NPC page (None on failure/non-NPC pages).

        ``with_image`` additionally resolves and caches the page picture. It
        is a second and third request, so the caller decides — in the app
        that is the ``mobinfo.show_image`` setting.
        """
        npc = self._page(title)
        if npc is None or not with_image or not npc.image_file:
            return npc
        if npc.image_url is not None or npc.image_path is not None:
            return npc
        url = self.image_url(npc.image_file)
        resolved = npc.model_copy(
            update={"image_url": url, "image_path": self.cache_image(url) if url else None}
        )
        stamp, _ = self._npc_cache.get(title, (self._clock(), None))
        self._npc_cache[title] = (stamp, resolved)
        return resolved

    def image_url(self, filename: str, width: int = IMAGE_WIDTH) -> str | None:
        """Resolve ``imagefilename`` to a real URL through the MediaWiki API.

        DELIBERATE DIVERGENCE from EQTool, which builds
        ``/images/<Capitalised filename>`` by hand. MediaWiki stores uploads
        under a hash fan-out (``/images/a/ab/Foo.png``), so the guess only
        works where a wiki happens to serve a flat directory — and it can
        never ask for a thumbnail, which is what keeps this cheap.
        """
        if not filename:
            return None
        try:
            resp = self._client.get(
                f"{self._base}/api.php",
                params={
                    "action": "query",
                    "titles": f"File:{filename}",
                    "prop": "imageinfo",
                    "iiprop": "url",
                    "iiurlwidth": str(width),
                    "format": "json",
                },
            )
            resp.raise_for_status()
            return parse_image_url(resp.text)
        except Exception:
            logger.warning("wiki image lookup failed for %r", filename, exc_info=True)
            return None

    def cache_image(self, url: str) -> Path | None:
        """Download ``url`` into the on-disk cache; return the file.

        No cache directory configured means no download at all — which is
        also what keeps an image fetch from escaping into the test suite.
        """
        if self._image_dir is None or not url:
            return None
        path = self._image_dir / _cache_name(url)
        try:
            if path.is_file() and path.stat().st_size > 0:
                return path
            resp = self._client.get(url)
            resp.raise_for_status()
            data = resp.content
            if not data or len(data) > MAX_IMAGE_BYTES:
                logger.warning("wiki image %r rejected (%d bytes)", url, len(data))
                return None
            self._image_dir.mkdir(parents=True, exist_ok=True)
            staging = path.with_suffix(path.suffix + ".part")
            staging.write_bytes(data)
            staging.replace(path)
            return path
        except Exception:
            logger.warning("wiki image download failed for %r", url, exc_info=True)
            return None

    def find_npcs(self, query: str, limit: int = 5) -> list[WikiNpc]:
        """Search + fetch in one call (worker-thread friendly)."""
        results = []
        for title in self.search(query, limit=limit):
            npc = self.npc(title)
            if npc is not None:
                results.append(npc)
        return results

    # -- internals ---------------------------------------------------------------

    def _page(self, title: str) -> WikiNpc | None:
        cached = self._npc_cache.get(title)
        if cached is not None and (self._clock() - cached[0]) < self._ttl:
            return cached[1]
        try:
            text = self._raw(title)
            redirect = _REDIRECT_RE.match(text) if text else None
            if redirect is not None:
                # action=raw hands back the redirect wikitext rather than
                # following it; one hop covers the name variants P99 uses.
                text = self._raw(redirect.group(1).strip())
            result = parse_npc(title, text, self._zones) if text else None
        except Exception:
            logger.warning("wiki page fetch failed for %r", title, exc_info=True)
            result = None
        self._npc_cache[title] = (self._clock(), result)
        return result

    def _raw(self, title: str) -> str:
        resp = self._client.get(f"{self._base}/index.php", params={"title": title, "action": "raw"})
        resp.raise_for_status()
        return resp.text
