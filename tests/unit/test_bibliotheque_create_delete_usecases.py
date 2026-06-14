"""Unit tests for CreateProductUseCase and DeleteProductUseCase.

Uses lightweight in-memory fakes — no Flask, no SQLAlchemy, no DB.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional
from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from app.application.bibliotheque.create_product_usecase import CreateProductUseCase
from app.application.bibliotheque.delete_product_usecase import DeleteProductUseCase
from app.application.bibliotheque.exceptions import (
    CompanyAccessDeniedError,
    InsufficientPermissionError,
    InvalidProductInputError,
    ProductAlreadyExistsError,
    ProductNotFoundError,
    SupplierNotFoundError,
)
from app.domain.entities.library_product import LibraryProduct
from app.domain.entities.supplier import Supplier


# ---------------------------------------------------------------------------
# In-memory fakes
# ---------------------------------------------------------------------------


class FakeSupplierRepo:
    def __init__(self, suppliers: Optional[list[Supplier]] = None) -> None:
        self._suppliers: dict[UUID, Supplier] = {s.id: s for s in (suppliers or [])}
        self._by_slug: dict[tuple[UUID, str], Supplier] = {(s.company_id, s.slug): s for s in (suppliers or [])}

    def find_by_id(self, supplier_id: UUID) -> Optional[Supplier]:
        return self._suppliers.get(supplier_id)

    def get_or_create(self, supplier: Supplier) -> Supplier:
        key = (supplier.company_id, supplier.slug)
        if key not in self._by_slug:
            self._suppliers[supplier.id] = supplier
            self._by_slug[key] = supplier
        return self._by_slug[key]

    def list_by_company(self, company_id: UUID) -> list[Supplier]:
        return [s for s in self._suppliers.values() if s.company_id == company_id]

    def find_by_slug(self, company_id: UUID, slug: str) -> Optional[Supplier]:
        return self._by_slug.get((company_id, slug))


class FakeProductRepo:
    def __init__(self) -> None:
        self._products: dict[UUID, LibraryProduct] = {}
        self._purchases: dict[UUID, list] = {}  # product_id → list of purchase ids
        self._raise_integrity_on_next_add = False

    def find_by_id(self, product_id: UUID) -> Optional[LibraryProduct]:
        return self._products.get(product_id)

    def find_by_reference(self, company_id: UUID, supplier_id: UUID, reference: str) -> Optional[LibraryProduct]:
        for p in self._products.values():
            if p.company_id == company_id and p.supplier_id == supplier_id and p.supplier_reference == reference:
                return p
        return None

    def add(self, product: LibraryProduct) -> LibraryProduct:
        if self._raise_integrity_on_next_add:
            self._raise_integrity_on_next_add = False
            # Raise an IntegrityError (simplified: orig is not a real DBAPI error here)
            raise IntegrityError("duplicate key", params={}, orig=Exception("UNIQUE constraint failed"))
        self._products[product.id] = product
        return product

    def delete(self, product_id: UUID) -> bool:
        if product_id not in self._products:
            return False
        del self._products[product_id]
        self._purchases.pop(product_id, None)
        return True

    def upsert(self, product: LibraryProduct) -> LibraryProduct:
        self._products[product.id] = product
        return product

    # Remaining port methods (unused in these tests but needed for Protocol compatibility)
    def find_by_id_for_update(self, product_id: UUID) -> Optional[LibraryProduct]:
        return self.find_by_id(product_id)

    def list(self, company_id, *, supplier_id=None, category=None, q=None, limit=20, offset=0):
        items = [p for p in self._products.values() if p.company_id == company_id]
        return items[offset : offset + limit], len(items)

    def distinct_categories(self, company_id: UUID) -> list[str]:
        return []


class FakeImageStorage:
    def __init__(self) -> None:
        self.deleted_keys: list[str] = []
        self._raise_on_delete = False

    def put(self, key: str, fileobj: object, content_type: str) -> None:
        pass

    def get_stream(self, key: str):
        return None, 0, "image/jpeg"

    def delete(self, key: str) -> None:
        if self._raise_on_delete:
            raise RuntimeError("Storage unavailable")
        self.deleted_keys.append(key)


class FakeMembershipReader:
    def __init__(self, member_ids: set[UUID]) -> None:
        self._members = member_ids

    def is_member(self, user_id: UUID, company_id: UUID) -> bool:
        return user_id in self._members


class FakePermissionChecker:
    def __init__(self, permitted_ids: set[UUID]) -> None:
        self._permitted = permitted_ids

    def has_permission(self, user_id: UUID, permission_name: str) -> bool:
        return user_id in self._permitted


class _FakeNestedTx:
    """Context manager simulating a SAVEPOINT; re-raises on exit so the outer try/except sees it."""

    def __init__(self) -> None:
        self._exc: BaseException | None = None

    def __enter__(self) -> "_FakeNestedTx":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        # Do not suppress exceptions — let them propagate so the use-case can catch them.
        return False


class FakeSession:
    def __init__(self) -> None:
        self.committed = False

    def commit(self) -> None:
        self.committed = True

    def begin_nested(self) -> "_FakeNestedTx":
        return _FakeNestedTx()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def company_id():
    return uuid4()


@pytest.fixture
def requester_id():
    return uuid4()


@pytest.fixture
def supplier(company_id):
    return Supplier.create(company_id=company_id, name="Acme", slug="acme")


@pytest.fixture
def supplier_repo(supplier):
    return FakeSupplierRepo(suppliers=[supplier])


@pytest.fixture
def product_repo():
    return FakeProductRepo()


@pytest.fixture
def image_storage():
    return FakeImageStorage()


@pytest.fixture
def session():
    return FakeSession()


@pytest.fixture
def membership(requester_id, company_id):
    return FakeMembershipReader({requester_id})


@pytest.fixture
def perms(requester_id):
    return FakePermissionChecker({requester_id})


@pytest.fixture
def create_uc(supplier_repo, product_repo, membership, perms, session):
    return CreateProductUseCase(
        supplier_repo=supplier_repo,
        product_repo=product_repo,
        membership_reader=membership,
        permission_checker=perms,
        db_session=session,
    )


@pytest.fixture
def delete_uc(product_repo, image_storage, membership, perms, session):
    return DeleteProductUseCase(
        product_repo=product_repo,
        image_storage=image_storage,
        membership_reader=membership,
        permission_checker=perms,
        db_session=session,
    )


# ---------------------------------------------------------------------------
# CreateProductUseCase tests
# ---------------------------------------------------------------------------


class TestCreateProductUseCase:
    def test_auto_ref_when_blank(self, create_uc, requester_id, company_id):
        product = create_uc.execute(
            requester_id=requester_id,
            company_id=company_id,
            name="Widget",
            supplier_name="Acme",
        )
        assert product.supplier_reference.startswith("manual-")
        assert len(product.supplier_reference) == len("manual-") + 12

    def test_auto_ref_when_whitespace_only(self, create_uc, requester_id, company_id):
        product = create_uc.execute(
            requester_id=requester_id,
            company_id=company_id,
            name="Widget",
            supplier_name="Acme",
            supplier_reference="   ",
        )
        assert product.supplier_reference.startswith("manual-")

    def test_explicit_reference_preserved(self, create_uc, requester_id, company_id):
        product = create_uc.execute(
            requester_id=requester_id,
            company_id=company_id,
            name="Widget",
            supplier_name="Acme",
            supplier_reference="MY-REF-123",
        )
        assert product.supplier_reference == "MY-REF-123"

    def test_supplier_get_or_create_path(self, create_uc, requester_id, company_id, supplier_repo):
        """Using supplier_name creates or reuses supplier; slug derived server-side."""
        product = create_uc.execute(
            requester_id=requester_id,
            company_id=company_id,
            name="Widget",
            supplier_name="Brand New Co",
        )
        assert product.supplier_id is not None
        s = supplier_repo.find_by_slug(company_id, "brand-new-co")
        assert s is not None
        assert product.supplier_id == s.id

    def test_supplier_id_path(self, create_uc, requester_id, company_id, supplier):
        """Using an existing supplier_id resolves to that supplier."""
        product = create_uc.execute(
            requester_id=requester_id,
            company_id=company_id,
            name="Widget",
            supplier_id=supplier.id,
            supplier_reference="EXP-REF",
        )
        assert product.supplier_id == supplier.id

    def test_cross_company_supplier_id_raises(self, create_uc, requester_id, company_id):
        """supplier_id from a different company → SupplierNotFoundError."""
        foreign_supplier = Supplier.create(company_id=uuid4(), name="Foreign", slug="foreign")
        with pytest.raises(SupplierNotFoundError):
            create_uc.execute(
                requester_id=requester_id,
                company_id=company_id,
                name="Widget",
                supplier_id=foreign_supplier.id,
            )

    def test_unknown_supplier_id_raises(self, create_uc, requester_id, company_id):
        """supplier_id not in repo → SupplierNotFoundError."""
        with pytest.raises(SupplierNotFoundError):
            create_uc.execute(
                requester_id=requester_id,
                company_id=company_id,
                name="Widget",
                supplier_id=uuid4(),
            )

    def test_neither_supplier_raises(self, create_uc, requester_id, company_id):
        """No supplier_id or supplier_name → InvalidProductInputError."""
        with pytest.raises(InvalidProductInputError):
            create_uc.execute(
                requester_id=requester_id,
                company_id=company_id,
                name="Widget",
            )

    def test_integrity_error_mapped_to_already_exists(self, create_uc, product_repo, requester_id, company_id):
        """IntegrityError from repo.add → ProductAlreadyExistsError."""
        product_repo._raise_integrity_on_next_add = True
        with pytest.raises(ProductAlreadyExistsError):
            create_uc.execute(
                requester_id=requester_id,
                company_id=company_id,
                name="Widget",
                supplier_name="Acme",
                supplier_reference="DUP-001",
            )

    def test_membership_guard_fires_before_write(self, supplier_repo, product_repo, perms, session, company_id):
        """Non-member → CompanyAccessDeniedError before any repo write."""
        outsider = uuid4()
        uc = CreateProductUseCase(
            supplier_repo=supplier_repo,
            product_repo=product_repo,
            membership_reader=FakeMembershipReader(set()),  # nobody is member
            permission_checker=perms,
            db_session=session,
        )
        with pytest.raises(CompanyAccessDeniedError):
            uc.execute(
                requester_id=outsider,
                company_id=company_id,
                name="Widget",
                supplier_name="Acme",
            )
        assert len(product_repo._products) == 0

    def test_permission_guard_fires_before_write(
        self, supplier_repo, product_repo, membership, session, company_id, requester_id
    ):
        """No manage permission → InsufficientPermissionError before any repo write."""
        uc = CreateProductUseCase(
            supplier_repo=supplier_repo,
            product_repo=product_repo,
            membership_reader=membership,
            permission_checker=FakePermissionChecker(set()),  # no permissions
            db_session=session,
        )
        with pytest.raises(InsufficientPermissionError):
            uc.execute(
                requester_id=requester_id,
                company_id=company_id,
                name="Widget",
                supplier_name="Acme",
            )
        assert len(product_repo._products) == 0

    def test_commit_called_on_success(self, create_uc, session, requester_id, company_id):
        create_uc.execute(
            requester_id=requester_id,
            company_id=company_id,
            name="Widget",
            supplier_name="Acme",
        )
        assert session.committed

    def test_zero_aggregates_on_new_product(self, create_uc, requester_id, company_id):
        product = create_uc.execute(
            requester_id=requester_id,
            company_id=company_id,
            name="Widget",
            supplier_name="Acme",
        )
        assert product.purchase_count == 0
        assert product.total_quantity == Decimal("0")
        assert product.last_unit_price is None


# ---------------------------------------------------------------------------
# DeleteProductUseCase tests
# ---------------------------------------------------------------------------


def _make_product(company_id: UUID, image_key: Optional[str] = None) -> LibraryProduct:
    """Build a minimal LibraryProduct for testing."""
    supplier_id = uuid4()
    p = LibraryProduct.create(
        company_id=company_id,
        supplier_id=supplier_id,
        supplier_reference="TEST-REF",
        name="Test Product",
    )
    if image_key:
        # Replace the immutable dataclass field via dataclasses.replace.
        from dataclasses import replace

        p = replace(p, image_storage_key=image_key)
    return p


class TestDeleteProductUseCase:
    def test_deletes_product(self, delete_uc, product_repo, requester_id, company_id, session):
        p = _make_product(company_id)
        product_repo._products[p.id] = p

        delete_uc.execute(requester_id=requester_id, product_id=p.id)

        assert p.id not in product_repo._products
        assert session.committed

    def test_product_not_found_raises(self, delete_uc, requester_id):
        with pytest.raises(ProductNotFoundError):
            delete_uc.execute(requester_id=requester_id, product_id=uuid4())

    def test_membership_guard(self, product_repo, image_storage, perms, session, company_id):
        p = _make_product(company_id)
        product_repo._products[p.id] = p
        uc = DeleteProductUseCase(
            product_repo=product_repo,
            image_storage=image_storage,
            membership_reader=FakeMembershipReader(set()),
            permission_checker=perms,
            db_session=session,
        )
        with pytest.raises(CompanyAccessDeniedError):
            uc.execute(requester_id=uuid4(), product_id=p.id)
        # Product must NOT be deleted.
        assert p.id in product_repo._products

    def test_permission_guard(self, product_repo, image_storage, membership, session, company_id, requester_id):
        p = _make_product(company_id)
        product_repo._products[p.id] = p
        uc = DeleteProductUseCase(
            product_repo=product_repo,
            image_storage=image_storage,
            membership_reader=membership,
            permission_checker=FakePermissionChecker(set()),
            db_session=session,
        )
        with pytest.raises(InsufficientPermissionError):
            uc.execute(requester_id=requester_id, product_id=p.id)
        assert p.id in product_repo._products

    def test_image_deleted_after_commit(self, delete_uc, product_repo, image_storage, requester_id, company_id):
        p = _make_product(company_id, image_key="products/img-key.jpg")
        product_repo._products[p.id] = p

        delete_uc.execute(requester_id=requester_id, product_id=p.id)

        assert "products/img-key.jpg" in image_storage.deleted_keys

    def test_no_image_no_storage_call(self, delete_uc, product_repo, image_storage, requester_id, company_id):
        p = _make_product(company_id, image_key=None)
        product_repo._products[p.id] = p

        delete_uc.execute(requester_id=requester_id, product_id=p.id)

        assert image_storage.deleted_keys == []

    def test_storage_error_swallowed(self, delete_uc, product_repo, image_storage, requester_id, company_id, session):
        """A storage failure after commit must NOT raise — request already succeeded."""
        image_storage._raise_on_delete = True
        p = _make_product(company_id, image_key="products/fail-key.jpg")
        product_repo._products[p.id] = p

        # Must not raise — error is swallowed with a warning log.
        delete_uc.execute(requester_id=requester_id, product_id=p.id)

        assert session.committed
        assert p.id not in product_repo._products

    def test_commit_before_image_cleanup(
        self, product_repo, image_storage, session, company_id, requester_id, perms, membership
    ):
        """Verify commit is called before image.delete (ordering matters for correctness)."""
        commit_order = []

        class TrackingSession:
            def commit(self):
                commit_order.append("commit")

        class TrackingStorage(FakeImageStorage):
            def delete(self, key: str) -> None:
                commit_order.append("image_delete")

        p = _make_product(company_id, image_key="k")
        product_repo._products[p.id] = p

        uc = DeleteProductUseCase(
            product_repo=product_repo,
            image_storage=TrackingStorage(),
            membership_reader=membership,
            permission_checker=perms,
            db_session=TrackingSession(),
        )
        uc.execute(requester_id=requester_id, product_id=p.id)

        assert commit_order == ["commit", "image_delete"]
