"""Enums for the billing bounded context.

String values are persisted to DB — keep them lowercase ASCII (matches InvoiceType).
"""

from enum import Enum


class BillingDocumentKind(str, Enum):
    DEVIS = "devis"
    FACTURE = "facture"


class BillingDocumentStatus(str, Enum):
    DRAFT = "draft"
    SENT = "sent"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    EXPIRED = "expired"
    PAID = "paid"
    OVERDUE = "overdue"
    CANCELLED = "cancelled"
