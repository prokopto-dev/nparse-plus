"""PetHandler — tracks the player's current pet.

Port of EQTool's Services/Handlers/PetHandler.cs over the ``PlayerPet``
state in ``nparseplus.core.pets``:

- Casting a known pet spell arms the rank table for the incoming pet.
- CREATION names the pet only when no name is known yet (screens out other
  players' pets); LEADER and PETATTACK always (re)name it, since only your
  own pet reports those.
- RECLAIMED/DEATH/GETLOST clear the pet only when the name matches ours;
  NONE, zoning, logging in, your own death, and a charm break always clear.
- Melee DamageEvents from the pet (excluding backstabs/kicks) feed the
  max-melee rank guess.
"""

from __future__ import annotations

from nparseplus.core.bus import EventBus
from nparseplus.core.enums import PetIncident
from nparseplus.core.events import (
    DamageEvent,
    LoadingPleaseWaitEvent,
    PetEvent,
    SlainEvent,
    SpellWornOffOtherEvent,
    WelcomeEvent,
    YouBeginCastingEvent,
    YouZonedEvent,
)
from nparseplus.core.handlers.base import BaseHandler
from nparseplus.core.pets import Pets, PlayerPet
from nparseplus.core.player import ActivePlayer

_CHARM_WORN_OFF_LINE = "Your charm spell has worn off."
_NON_RANK_DAMAGE_TYPES = frozenset({"backstabs", "kicks"})


class PetHandler(BaseHandler):
    def __init__(
        self,
        bus: EventBus,
        player: ActivePlayer,
        pets: Pets,
        player_pet: PlayerPet | None = None,
    ) -> None:
        super().__init__(bus, player)
        self.pets = pets
        self.player_pet = player_pet if player_pet is not None else PlayerPet()
        self._last_zone_name = ""
        bus.subscribe(YouBeginCastingEvent, self._on_begin_casting)
        bus.subscribe(LoadingPleaseWaitEvent, self._on_loading)
        bus.subscribe(WelcomeEvent, self._on_welcome)
        bus.subscribe(YouZonedEvent, self._on_zoned)
        bus.subscribe(SlainEvent, self._on_slain)
        bus.subscribe(SpellWornOffOtherEvent, self._on_spell_worn_off_other)
        bus.subscribe(PetEvent, self._on_pet)
        bus.subscribe(DamageEvent, self._on_damage)

    def _on_begin_casting(self, event: YouBeginCastingEvent) -> None:
        pet_spell = self.pets.spell_for(event.spell.name)
        if pet_spell is not None:
            self.player_pet.set_spell(pet_spell)

    def _on_zoned(self, event: YouZonedEvent) -> None:
        if event.short_name != self._last_zone_name:
            self._last_zone_name = event.short_name
            self.player_pet.reset()

    def _on_loading(self, event: LoadingPleaseWaitEvent) -> None:
        self.player_pet.reset()

    def _on_welcome(self, event: WelcomeEvent) -> None:
        self.player_pet.reset()

    def _on_slain(self, event: SlainEvent) -> None:
        if event.victim == "You":
            self.player_pet.reset()

    def _on_spell_worn_off_other(self, event: SpellWornOffOtherEvent) -> None:
        if event.line == _CHARM_WORN_OFF_LINE:
            self.player_pet.reset()

    def _on_pet(self, event: PetEvent) -> None:
        pet = self.player_pet
        if event.incident == PetIncident.NONE:
            pet.reset()
        elif event.incident == PetIncident.CREATION:
            # Only take a name when none is known yet; this screens out
            # almost all other players' pet-creation messages.
            if not pet.is_pet_name_known:
                pet.set_name(event.pet_name)
        elif event.incident == PetIncident.RECLAIMED:
            if event.pet_name == pet.pet_name:
                pet.reset()
        elif event.incident in (PetIncident.LEADER, PetIncident.PETATTACK):
            # Other commands (follow, guard, sit...) echo from every nearby
            # pet, so only these two are trusted to name ours.
            pet.set_name(event.pet_name)
        elif (
            event.incident in (PetIncident.DEATH, PetIncident.GETLOST)
            and event.pet_name == pet.pet_name
        ):
            pet.reset()

    def _on_damage(self, event: DamageEvent) -> None:
        pet = self.player_pet
        if not pet.is_pet_name_known or pet.pet_name != event.attacker_name:
            return
        if event.damage_type in _NON_RANK_DAMAGE_TYPES:
            return
        pet.check_max_melee(event.damage_done)
