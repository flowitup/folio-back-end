"""Persons application module.

Person is a global identity entity decoupled from Project and Company.
A single Person can have Worker rows in projects belonging to different
companies (multi-company support).
"""

from app.application.persons.ports import IPersonRepository
from app.application.persons.create_person import (
    CreatePersonUseCase,
    CreatePersonRequest,
    CreatePersonResponse,
)
from app.application.persons.search_persons import (
    SearchPersonsUseCase,
    SearchPersonsRequest,
    SearchPersonsResponse,
    PersonSummary,
)

__all__ = [
    "IPersonRepository",
    "CreatePersonUseCase",
    "CreatePersonRequest",
    "CreatePersonResponse",
    "SearchPersonsUseCase",
    "SearchPersonsRequest",
    "SearchPersonsResponse",
    "PersonSummary",
]
