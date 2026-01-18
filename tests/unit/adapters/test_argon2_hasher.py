"""Unit tests for Argon2PasswordHasher adapter."""

import pytest

from app.infrastructure.adapters.argon2_password_hasher import Argon2PasswordHasher


class TestArgon2PasswordHasherHash:
    """Tests for Argon2PasswordHasher.hash() method."""

    @pytest.fixture
    def hasher(self):
        """Create Argon2PasswordHasher with default params."""
        return Argon2PasswordHasher()

    def test_hash_returns_string(self, hasher):
        """Should return a string hash."""
        result = hasher.hash("password123")
        assert isinstance(result, str)

    def test_hash_contains_argon2id_identifier(self, hasher):
        """Should produce Argon2id hash format."""
        result = hasher.hash("password123")
        assert result.startswith("$argon2id$")

    def test_hash_is_different_each_time(self, hasher):
        """Should produce different hashes due to random salt."""
        hash1 = hasher.hash("password123")
        hash2 = hasher.hash("password123")
        assert hash1 != hash2

    def test_hash_empty_password(self, hasher):
        """Should hash empty password without error."""
        result = hasher.hash("")
        assert isinstance(result, str)
        assert result.startswith("$argon2id$")

    def test_hash_unicode_password(self, hasher):
        """Should handle unicode characters in password."""
        result = hasher.hash("pässwörd123")
        assert isinstance(result, str)
        assert result.startswith("$argon2id$")

    def test_hash_long_password(self, hasher):
        """Should handle long passwords."""
        long_password = "a" * 1000
        result = hasher.hash(long_password)
        assert isinstance(result, str)


class TestArgon2PasswordHasherVerify:
    """Tests for Argon2PasswordHasher.verify() method."""

    @pytest.fixture
    def hasher(self):
        """Create Argon2PasswordHasher."""
        return Argon2PasswordHasher()

    def test_verify_correct_password(self, hasher):
        """Should return True for correct password."""
        password = "password123"
        hashed = hasher.hash(password)

        result = hasher.verify(password, hashed)
        assert result is True

    def test_verify_incorrect_password(self, hasher):
        """Should return False for wrong password."""
        hashed = hasher.hash("password123")

        result = hasher.verify("wrongpassword", hashed)
        assert result is False

    def test_verify_invalid_hash(self, hasher):
        """Should return False for malformed hash."""
        result = hasher.verify("password", "not-a-valid-hash")
        assert result is False

    def test_verify_empty_hash(self, hasher):
        """Should return False for empty hash."""
        result = hasher.verify("password", "")
        assert result is False

    def test_verify_empty_password(self, hasher):
        """Should work correctly with empty password."""
        hashed = hasher.hash("")
        assert hasher.verify("", hashed) is True
        assert hasher.verify("notempty", hashed) is False

    def test_verify_unicode_password(self, hasher):
        """Should verify unicode passwords correctly."""
        password = "pässwörd123"
        hashed = hasher.hash(password)

        assert hasher.verify(password, hashed) is True
        assert hasher.verify("password123", hashed) is False


class TestArgon2PasswordHasherConfiguration:
    """Tests for Argon2PasswordHasher configuration options."""

    def test_default_configuration(self):
        """Should use secure defaults."""
        hasher = Argon2PasswordHasher()
        # Just verify it works with defaults
        hashed = hasher.hash("test")
        assert hasher.verify("test", hashed) is True

    def test_custom_time_cost(self):
        """Should accept custom time_cost parameter."""
        hasher = Argon2PasswordHasher(time_cost=3)
        hashed = hasher.hash("test")
        assert hasher.verify("test", hashed) is True

    def test_custom_memory_cost(self):
        """Should accept custom memory_cost parameter."""
        hasher = Argon2PasswordHasher(memory_cost=32768)
        hashed = hasher.hash("test")
        assert hasher.verify("test", hashed) is True

    def test_custom_parallelism(self):
        """Should accept custom parallelism parameter."""
        hasher = Argon2PasswordHasher(parallelism=2)
        hashed = hasher.hash("test")
        assert hasher.verify("test", hashed) is True

    def test_different_hashers_can_verify(self):
        """Hash from one hasher should be verifiable by another with any config."""
        hasher1 = Argon2PasswordHasher(time_cost=2, memory_cost=65536)
        hasher2 = Argon2PasswordHasher(time_cost=3, memory_cost=32768)

        # Hash with hasher1
        hashed = hasher1.hash("test")

        # Verify with hasher2 (Argon2 params are in the hash itself)
        assert hasher2.verify("test", hashed) is True
