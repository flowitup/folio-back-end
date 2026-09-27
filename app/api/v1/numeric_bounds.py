"""Upper bounds for money and quantity request fields.

Each bound sits just under the column that stores the value, so an oversized
number is rejected with a validation error instead of failing in the database
(a 500), or — for amounts kept in JSON line items — overflowing the Decimal
and float maths that every later read of the row goes through.
"""

from decimal import Decimal

# Numeric(10, 2): worker daily rates, rate changes, labor amount overrides.
MAX_DAILY_AMOUNT = Decimal("99999999.99")
# Numeric(14, 2): project budget.
MAX_BUDGET = Decimal("9999999999.99")
# Postgres INTEGER: inventory counts.
MAX_INT_QUANTITY = 2_147_483_647
# Numeric(12, 3): chiffrage article quantity.
MAX_ARTICLE_QUANTITY = Decimal("999999999.999")
# Numeric(12, 4): chiffrage quote unit price.
MAX_QUOTE_UNIT_PRICE = Decimal("99999999.9999")
# Numeric(18, 4): bibliotheque purchase quantity and unit price.
MAX_LIBRARY_AMOUNT = Decimal("99999999999999.9999")
# Expense line items (JSON, no column limit): same caps as billing document lines.
MAX_LINE_QUANTITY = Decimal("9999999")
MAX_LINE_UNIT_PRICE = Decimal("999999999")
