"""A database refusal on a worker save never echoes table or constraint names."""

from sqlalchemy.exc import IntegrityError

from app.infrastructure.adapters.sqlalchemy_worker import _integrity_error_to_domain


def _error(detail: str) -> IntegrityError:
    return IntegrityError("INSERT INTO workers ...", {}, Exception(detail))


def test_unknown_constraint_gives_a_generic_message():
    err = _integrity_error_to_domain(
        _error('insert or update on table "workers" violates foreign key constraint "workers_role_id_fkey"')
    )
    assert "workers_role_id_fkey" not in str(err)
    assert "table" not in str(err)


def test_known_constraints_keep_their_messages():
    assert "already linked" in str(_integrity_error_to_domain(_error("uq_workers_project_user")))
    assert "existing user" in str(_integrity_error_to_domain(_error("fk_workers_user_id")))
