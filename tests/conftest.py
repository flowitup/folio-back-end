"""Pytest configuration and fixtures for database tests."""

import os
import sys

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Add project root to Python path for wiring module
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.infrastructure.database.models import Base


@pytest.fixture(scope="session")
def test_db_url():
    """Get test database URL from environment or use default SQLite."""
    return os.getenv(
        "TEST_DATABASE_URL",
        "sqlite:///:memory:"  # Use in-memory SQLite for tests
    )


@pytest.fixture(scope="session")
def engine(test_db_url):
    """Create SQLAlchemy engine for tests."""
    return create_engine(test_db_url, echo=False)


@pytest.fixture(scope="session")
def tables(engine):
    """Create all tables for testing."""
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture(scope="function")
def session(engine, tables):
    """Create a new database session for a test."""
    connection = engine.connect()
    transaction = connection.begin()
    Session = sessionmaker(bind=connection)
    session = Session()

    yield session

    session.close()
    transaction.rollback()
    connection.close()
