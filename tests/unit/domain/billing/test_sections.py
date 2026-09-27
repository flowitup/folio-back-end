"""Section headings: lines with no section after a section get their own heading."""

from app.domain.billing.sections import UNSECTIONED_HEADING, section_headings


def test_a_heading_where_the_section_changes():
    assert section_headings(["Gros oeuvre", "Gros oeuvre", "Peinture"]) == ["Gros oeuvre", None, "Peinture"]


def test_unsectioned_lines_after_a_section_get_a_neutral_heading():
    assert section_headings(["Gros oeuvre", None, ""]) == ["Gros oeuvre", UNSECTIONED_HEADING, None]


def test_unsectioned_lines_at_the_top_keep_no_heading():
    assert section_headings([None, None, "Peinture", None]) == [None, None, "Peinture", UNSECTIONED_HEADING]
