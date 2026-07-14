from nparseplus.core.enums import PetIncident
from nparseplus.core.events import PetEvent
from nparseplus.core.parsers.pet import PetParser


def test_pet_creation(ctx, make_line, spy):
    events = spy(PetEvent)
    parser = PetParser()
    assert parser.handle(make_line("Gobaner says 'At your service Master.'"), ctx)
    assert events[0].incident == PetIncident.CREATION
    assert events[0].pet_name == "Gobaner"


def test_pet_leader(ctx, make_line, spy):
    events = spy(PetEvent)
    parser = PetParser()
    assert parser.handle(make_line("Jobober says 'My leader is Whitewitch.'"), ctx)
    assert events[0].incident == PetIncident.LEADER
    assert events[0].pet_name == "Jobober"


def test_pet_death(ctx, make_line, spy):
    events = spy(PetEvent)
    parser = PetParser()
    assert parser.handle(make_line("Gobaner says 'Sorry to have failed you, oh Great One.'"), ctx)
    assert events[0].incident == PetIncident.DEATH


def test_pet_attack(ctx, make_line, spy):
    events = spy(PetEvent)
    parser = PetParser()
    assert parser.handle(make_line("Gobaner tells you, 'Attacking a spectre Master.'"), ctx)
    assert events[0].incident == PetIncident.PETATTACK
    assert events[0].pet_name == "Gobaner"


def test_no_pet(ctx, make_line, spy):
    events = spy(PetEvent)
    parser = PetParser()
    assert parser.handle(make_line("You don't have a pet to command!"), ctx)
    assert events[0].incident == PetIncident.NONE
    assert events[0].pet_name == "None"
