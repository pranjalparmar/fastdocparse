# Cookbook: Bank Statement with a Balance Check

A bank statement is a good test of cross-field validation. The LLM reads an opening balance, a table of transactions and a stated closing balance, and these three have to agree: `opening + sum(transactions) == closing`. If one transaction is misread, the arithmetic breaks, and the `failed_check` flag tells you the extraction can't be trusted without a look.

This page builds that check end to end. It uses only the public API described in [Schema Guide](../schema-guide.md) and [Output & Validation](../output-format.md).

## Why a custom rule, not `numeric_sum_rule`

The built-in `numeric_sum_rule` flags a total that differs from the sum of a list's items. A closing balance isn't that sum: it is the opening balance *plus* the sum. So this recipe writes a small rule by hand. (If your document states a plain total of the transactions instead, `numeric_sum_rule(list_field="transactions", total_field=..., item_key="amount")` is enough.)

## 1. The schema

```python
from fastdocparse import Field, Schema

schema = Schema(
    name="BankStatement",
    fields=[
        Field(name="opening_balance", description="Balance at the start of the period", type="number", required=True),
        Field(
            name="transactions",
            description="Every transaction row. Deposits are positive, withdrawals negative.",
            type="list",
            sub_fields=[
                Field(name="date", description="Transaction date, YYYY-MM-DD", type="date"),
                Field(name="description", description="Transaction description"),
                Field(name="amount", description="Signed amount", type="number"),
            ],
        ),
        Field(name="closing_balance", description="Closing balance stated on the statement", type="number", required=True),
    ],
)
```

`transactions` is a `list` field, so it uses `sub_fields` for the columns. The description asks for signed amounts, because the rule below adds the amounts up as they are.

## 2. The cross-check rule

```python
from fastdocparse import Issue


def balance_rule(extracted: dict) -> "list[Issue] | None":
    opening = extracted.get("opening_balance")
    closing = extracted.get("closing_balance")
    transactions = extracted.get("transactions")
    if opening is None or closing is None or not transactions:
        return None
    expected = round(float(opening) + sum(float(t["amount"]) for t in transactions), 2)
    if abs(expected - float(closing)) > 0.01:
        message = f"opening {opening} + transactions = {expected}, but the statement says {closing}"
        return [
            Issue(field="closing_balance", message=message),
            Issue(field="transactions", message=message),
        ]
    return None
```

A rule takes the extracted values (a plain dict of field name to value) and returns a list of `Issue`s, or `None`. Each `Issue` names a field that gets the `failed_check` flag. Here both the closing balance and the transaction list are flagged, because the check can't tell which of them is wrong. The rule returns `None` when a value is missing, so a missing value shows up as `missing_required` and not as a confusing balance error.

You hand the rule to the parser with `rules=[balance_rule]`.

## 3. Running it

The example below needs no API key or network. The LLM client is replaced by a stub that returns a canned JSON reply, and the "document" is plain text registered under its own ingestion kind. **The LLM output in this example is canned**; it is not the result of a real model call. In a real setup you would pass an `LLMClient` and a PDF or image instead (see [Getting Started](../getting-started.md)).

The statement is invented sample data.

```python
import json

from fastdocparse import DocumentParser

STATEMENT = """\
EXAMPLE BANK - Statement of Account (sample data)
Account holder: Jane Example   Account: XXXX-0000
Period: 2025-03-01 to 2025-03-31

Opening balance 1,000.00

Date        Description               Amount
2025-03-03  Payroll deposit           2,500.00
2025-03-07  Grocery Store             -82.40
2025-03-15  Electric Company          -120.00
2025-03-28  Coffee Shop               -4.50

Closing balance 3,293.10
"""

# The same statement with the closing balance altered (3,293.10 -> 3,239.10)
ALTERED_STATEMENT = STATEMENT.replace("3,293.10", "3,239.10")

REPLY = """{
  "opening_balance": 1000.00,
  "transactions": [
    {"date": "2025-03-03", "description": "Payroll deposit", "amount": 2500.00},
    {"date": "2025-03-07", "description": "Grocery Store", "amount": -82.40},
    {"date": "2025-03-15", "description": "Electric Company", "amount": -120.00},
    {"date": "2025-03-28", "description": "Coffee Shop", "amount": -4.50}
  ],
  "closing_balance": 3293.10
}"""
ALTERED_REPLY = REPLY.replace("3293.10", "3239.10")


class CannedClient:
    """Stands in for LLMClient: DocumentParser only calls client.extract(prompt, chunk)."""

    def __init__(self, reply: str):
        self.reply = reply

    def extract(self, prompt: str, document_text: str) -> str:
        return self.reply


def run(text: str, reply: str) -> dict:
    parser = DocumentParser(CannedClient(reply))
    parser.register_ingestion_handler("text", lambda data, structured_mode, config: data.decode("utf-8"))
    return parser.extract(text.encode("utf-8"), schema, rules=[balance_rule], kind="text")


for title, text, reply in [("Reconciles", STATEMENT, REPLY), ("Altered closing balance", ALTERED_STATEMENT, ALTERED_REPLY)]:
    print(f"== {title}")
    result = run(text, reply)
    for name in ("opening_balance", "transactions", "closing_balance"):
        field = result[name]
        value = f"{len(field['value'])} rows" if name == "transactions" else field["value"]
        print(f"{name}: {value}  confidence={field['confidence']}  flags={field['flags']}")
```

Output of the script (the loop prints a summary of each field; the full result keeps every value, see [Output & Validation](../output-format.md#result-shape)):

```text
== Reconciles
opening_balance: 1000.0  confidence=high  flags=['grounded']
transactions: 4 rows  confidence=high  flags=['grounded']
closing_balance: 3293.1  confidence=high  flags=['grounded']
== Altered closing balance
opening_balance: 1000.0  confidence=high  flags=['grounded']
transactions: 4 rows  confidence=high  flags=['grounded', 'failed_check']
closing_balance: 3239.1  confidence=high  flags=['grounded', 'failed_check']
```

In the first run, `1000.00 + 2500.00 - 82.40 - 120.00 - 4.50 = 3293.10` matches the stated closing balance, so no field carries `failed_check`. In the second run the closing balance reads `3239.10`, the rule finds a 53.90 gap, and both `transactions` and `closing_balance` get `failed_check`. All fields are still `grounded`, since each value really appears in the source text. Grounding can't see an inconsistency between fields; that is what the rule is for.

## Notes

- **The CLI doesn't take rules.** `fastdocparse extract` has no option for cross-check rules today, so this check needs the Python API (`rules=[...]` on `DocumentParser.extract()`).
- **Rules run on the merged result.** For a long statement split into several chunks, the transactions from all chunks are concatenated before the rule runs.
- **Tolerance.** The rule allows a 0.01 difference to absorb rounding. Tighten or loosen it to suit your statements.
- **Signs matter.** If a statement prints withdrawals as positive numbers in a separate "Debit" column, say so in the field description (or handle the two columns in the rule), otherwise every statement will fail the check.
