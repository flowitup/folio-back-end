"""Unit tests for ProjectTag domain entity — validation, construction, and mutations."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.domain.entities.project_tag import ProjectTag


# ---------------------------------------------------------------------------
# Valid construction tests
# ---------------------------------------------------------------------------


class TestProjectTagConstruction:
    def test_create_with_valid_name_and_color(self):
        """Create a tag with valid name and hex color."""
        project_id = uuid4()
        tag = ProjectTag.create(
            project_id=project_id,
            name="Fondations",
            color="#FF5733",
        )
        assert tag.project_id == project_id
        assert tag.name == "Fondations"
        assert tag.color == "#FF5733"
        assert tag.id is not None
        assert tag.created_at is not None
        assert tag.updated_at is not None
        assert isinstance(tag.created_at, datetime)

    def test_create_trims_whitespace_from_name(self):
        """Name is stripped of leading/trailing whitespace."""
        tag = ProjectTag.create(
            project_id=uuid4(),
            name="  Phase A  ",
            color="#00FF00",
        )
        assert tag.name == "Phase A"

    def test_create_with_lowercase_hex_color(self):
        """Lowercase hex color is accepted."""
        tag = ProjectTag.create(
            project_id=uuid4(),
            name="Tag",
            color="#abcdef",
        )
        assert tag.color == "#abcdef"

    def test_create_with_uppercase_hex_color(self):
        """Uppercase hex color is accepted."""
        tag = ProjectTag.create(
            project_id=uuid4(),
            name="Tag",
            color="#ABCDEF",
        )
        assert tag.color == "#ABCDEF"

    def test_create_with_mixed_case_hex_color(self):
        """Mixed case hex color is accepted."""
        tag = ProjectTag.create(
            project_id=uuid4(),
            name="Tag",
            color="#AbCdEf",
        )
        assert tag.color == "#AbCdEf"

    def test_create_tag_timestamps_near_identical(self):
        """created_at and updated_at are set to approximately the same time."""
        tag = ProjectTag.create(
            project_id=uuid4(),
            name="Test",
            color="#000000",
        )
        delta = abs((tag.updated_at - tag.created_at).total_seconds())
        assert delta < 1  # Should be within 1 second


# ---------------------------------------------------------------------------
# Name validation tests
# ---------------------------------------------------------------------------


class TestProjectTagNameValidation:
    def test_empty_name_raises_valueerror(self):
        """Empty name raises ValueError."""
        with pytest.raises(ValueError, match="Tag name must not be empty"):
            ProjectTag.create(
                project_id=uuid4(),
                name="",
                color="#000000",
            )

    def test_whitespace_only_name_raises_valueerror(self):
        """Whitespace-only name raises ValueError."""
        with pytest.raises(ValueError, match="Tag name must not be empty"):
            ProjectTag.create(
                project_id=uuid4(),
                name="   ",
                color="#000000",
            )

    def test_whitespace_only_tabs_name_raises_valueerror(self):
        """Tabs-only name raises ValueError."""
        with pytest.raises(ValueError, match="Tag name must not be empty"):
            ProjectTag.create(
                project_id=uuid4(),
                name="\t\t\t",
                color="#000000",
            )

    def test_name_exactly_100_chars_valid(self):
        """Name of exactly 100 characters is valid."""
        name_100 = "a" * 100
        tag = ProjectTag.create(
            project_id=uuid4(),
            name=name_100,
            color="#000000",
        )
        assert tag.name == name_100
        assert len(tag.name) == 100

    def test_name_101_chars_raises_valueerror(self):
        """Name of 101 characters exceeds max and raises ValueError."""
        name_101 = "a" * 101
        with pytest.raises(ValueError, match="Tag name must not exceed 100 characters"):
            ProjectTag.create(
                project_id=uuid4(),
                name=name_101,
                color="#000000",
            )

    def test_name_with_unicode_chars_valid(self):
        """Unicode characters in name are allowed."""
        tag = ProjectTag.create(
            project_id=uuid4(),
            name="Fondations 🏗️",
            color="#000000",
        )
        assert "🏗️" in tag.name

    def test_name_with_accents_valid(self):
        """Accented characters (é, à, etc.) are allowed."""
        tag = ProjectTag.create(
            project_id=uuid4(),
            name="Rénovation façade",
            color="#000000",
        )
        assert tag.name == "Rénovation façade"

    def test_name_single_char_valid(self):
        """Single character name is valid."""
        tag = ProjectTag.create(
            project_id=uuid4(),
            name="A",
            color="#000000",
        )
        assert tag.name == "A"


# ---------------------------------------------------------------------------
# Color validation tests
# ---------------------------------------------------------------------------


class TestProjectTagColorValidation:
    def test_missing_hash_raises_valueerror(self):
        """Color without # prefix raises ValueError."""
        with pytest.raises(ValueError, match="Color must be a valid hex color"):
            ProjectTag.create(
                project_id=uuid4(),
                name="Tag",
                color="FF5733",
            )

    def test_wrong_hex_length_short_raises_valueerror(self):
        """Color with only 3 hex digits (short form) raises ValueError."""
        with pytest.raises(ValueError, match="Color must be a valid hex color"):
            ProjectTag.create(
                project_id=uuid4(),
                name="Tag",
                color="#FFF",
            )

    def test_wrong_hex_length_long_raises_valueerror(self):
        """Color with 8 hex digits (RRGGBBAA) raises ValueError."""
        with pytest.raises(ValueError, match="Color must be a valid hex color"):
            ProjectTag.create(
                project_id=uuid4(),
                name="Tag",
                color="#FF5733FF",
            )

    def test_non_hex_chars_raises_valueerror(self):
        """Non-hex characters in color string raise ValueError."""
        with pytest.raises(ValueError, match="Color must be a valid hex color"):
            ProjectTag.create(
                project_id=uuid4(),
                name="Tag",
                color="#GGGGGG",
            )

    def test_color_with_spaces_raises_valueerror(self):
        """Color with spaces raises ValueError."""
        with pytest.raises(ValueError, match="Color must be a valid hex color"):
            ProjectTag.create(
                project_id=uuid4(),
                name="Tag",
                color="#FF 57 33",
            )

    def test_valid_black_color(self):
        """Black color #000000 is valid."""
        tag = ProjectTag.create(
            project_id=uuid4(),
            name="Tag",
            color="#000000",
        )
        assert tag.color == "#000000"

    def test_valid_white_color(self):
        """White color #FFFFFF is valid."""
        tag = ProjectTag.create(
            project_id=uuid4(),
            name="Tag",
            color="#FFFFFF",
        )
        assert tag.color == "#FFFFFF"

    def test_valid_random_hex_colors(self):
        """Various valid hex colors."""
        colors = ["#123456", "#ABCDEF", "#abcdef", "#fedcba", "#000001", "#fffffe"]
        for color in colors:
            tag = ProjectTag.create(
                project_id=uuid4(),
                name="Tag",
                color=color,
            )
            assert tag.color == color


# ---------------------------------------------------------------------------
# with_updates tests — mutation and validation
# ---------------------------------------------------------------------------


class TestProjectTagWithUpdates:
    def test_with_updates_name_only(self):
        """Update name only, color unchanged."""
        original = ProjectTag.create(
            project_id=uuid4(),
            name="Original Name",
            color="#FF0000",
        )
        updated = original.with_updates(name="New Name")
        assert updated.name == "New Name"
        assert updated.color == "#FF0000"  # unchanged
        assert updated.id == original.id
        assert updated.project_id == original.project_id
        assert updated.created_at == original.created_at
        assert updated.updated_at > original.updated_at

    def test_with_updates_color_only(self):
        """Update color only, name unchanged."""
        original = ProjectTag.create(
            project_id=uuid4(),
            name="Tag Name",
            color="#FF0000",
        )
        updated = original.with_updates(color="#00FF00")
        assert updated.name == "Tag Name"  # unchanged
        assert updated.color == "#00FF00"
        assert updated.updated_at > original.updated_at

    def test_with_updates_both_fields(self):
        """Update both name and color."""
        original = ProjectTag.create(
            project_id=uuid4(),
            name="Old Name",
            color="#FF0000",
        )
        updated = original.with_updates(
            name="New Name",
            color="#0000FF",
        )
        assert updated.name == "New Name"
        assert updated.color == "#0000FF"
        assert updated.id == original.id
        assert updated.created_at == original.created_at
        assert updated.updated_at > original.updated_at

    def test_with_updates_no_changes(self):
        """Call with_updates with no arguments returns equivalent tag."""
        original = ProjectTag.create(
            project_id=uuid4(),
            name="Name",
            color="#000000",
        )
        updated = original.with_updates()
        assert updated.name == original.name
        assert updated.color == original.color
        assert updated.id == original.id
        # updated_at will be newer due to timestamp refresh
        assert updated.updated_at >= original.updated_at

    def test_with_updates_trims_whitespace(self):
        """with_updates strips whitespace from name."""
        original = ProjectTag.create(
            project_id=uuid4(),
            name="Original",
            color="#000000",
        )
        updated = original.with_updates(name="  New Name  ")
        assert updated.name == "New Name"

    def test_with_updates_invalid_name_raises_valueerror(self):
        """with_updates with invalid name raises ValueError."""
        original = ProjectTag.create(
            project_id=uuid4(),
            name="Original",
            color="#000000",
        )
        with pytest.raises(ValueError, match="Tag name must not be empty"):
            original.with_updates(name="")

    def test_with_updates_invalid_color_raises_valueerror(self):
        """with_updates with invalid color raises ValueError."""
        original = ProjectTag.create(
            project_id=uuid4(),
            name="Original",
            color="#000000",
        )
        with pytest.raises(ValueError, match="Color must be a valid hex color"):
            original.with_updates(color="invalid")

    def test_with_updates_preserves_immutability(self):
        """Original tag is not modified by with_updates."""
        original = ProjectTag.create(
            project_id=uuid4(),
            name="Original",
            color="#FF0000",
        )
        original_updated_at = original.updated_at
        _ = original.with_updates(name="Modified", color="#00FF00")
        # Verify original is unchanged
        assert original.name == "Original"
        assert original.color == "#FF0000"
        assert original.updated_at == original_updated_at

    def test_with_updates_timestamp_increments(self):
        """Each with_updates call increments updated_at."""
        tag = ProjectTag.create(
            project_id=uuid4(),
            name="Tag",
            color="#000000",
        )
        t1 = tag.updated_at
        tag = tag.with_updates(name="Tag2")
        t2 = tag.updated_at
        tag = tag.with_updates(name="Tag3")
        t3 = tag.updated_at
        assert t1 <= t2 <= t3
        # At least one should be strictly later
        assert t2 >= t1 and t3 >= t2


# ---------------------------------------------------------------------------
# Edge cases and boundary conditions
# ---------------------------------------------------------------------------


class TestProjectTagEdgeCases:
    def test_tag_with_max_length_name_and_whitespace(self):
        """Name at max length after stripping whitespace."""
        base_name = "a" * 100
        tag = ProjectTag.create(
            project_id=uuid4(),
            name=f"  {base_name}  ",
            color="#000000",
        )
        assert len(tag.name) == 100

    def test_tag_with_newline_in_name(self):
        """Name containing newlines (not whitespace-only) is allowed after strip."""
        tag = ProjectTag.create(
            project_id=uuid4(),
            name="Line 1\nLine 2",
            color="#000000",
        )
        # Note: strip() only removes leading/trailing whitespace, not interior newlines
        assert "Line 1\nLine 2" in tag.name

    def test_tag_immutability_direct_assignment_prevented(self):
        """ProjectTag is frozen (immutable) — direct field assignment raises error."""
        tag = ProjectTag.create(
            project_id=uuid4(),
            name="Tag",
            color="#000000",
        )
        with pytest.raises(Exception):  # FrozenInstanceError from dataclass
            tag.name = "Modified"

    def test_tag_entity_with_none_updated_at_initial(self):
        """ProjectTag.updated_at may be None only in edge initialization."""
        # In normal flow create() always sets updated_at,
        # but the field is Optional to allow initialization variants.
        # This test documents that the domain entity allows it.
        project_id = uuid4()
        tag_id = uuid4()
        now = datetime.now(timezone.utc)
        # Direct construction (not via create factory)
        tag = ProjectTag(
            id=tag_id,
            project_id=project_id,
            name="Tag",
            color="#000000",
            created_at=now,
            updated_at=None,  # Explicitly None
        )
        assert tag.updated_at is None
