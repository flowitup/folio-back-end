"""worker_display_name: the Person's name, unless it is just a phone number."""

from app.domain.entities.worker import worker_display_name


def test_person_name_wins():
    assert worker_display_name("Antoine Dupont", "Old Copy") == "Antoine Dupont"


def test_phone_number_person_name_falls_back_to_the_worker_name():
    assert worker_display_name("+33621500003", "Nguyễn Văn An") == "Nguyễn Văn An"
    assert worker_display_name("06 21 50 00 03", "Nguyễn Văn An") == "Nguyễn Văn An"


def test_missing_person_name_uses_the_worker_name():
    assert worker_display_name(None, "Marc") == "Marc"
    assert worker_display_name("  ", "Marc") == "Marc"


def test_keeps_the_phone_when_the_worker_name_is_a_phone_too():
    assert worker_display_name("+33621500003", "+33621500003") == "+33621500003"
