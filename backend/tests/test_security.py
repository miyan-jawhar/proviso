import unittest

from app.core.security import SQLValidationError, validate_sql_query


class SQLValidationTests(unittest.TestCase):
    def test_allows_selects_over_the_allow_list(self):
        allowed_queries = [
            "SELECT vendor_name FROM vendors",
            "SELECT * FROM vendors UNION SELECT * FROM vendors",
            (
                "WITH spend AS (SELECT vendor_id, SUM(amount_due) AS total "
                "FROM invoices GROUP BY vendor_id) "
                "SELECT vendors.vendor_name, spend.total "
                "FROM vendors JOIN spend ON vendors.vendor_id = spend.vendor_id"
            ),
        ]

        for query in allowed_queries:
            with self.subTest(query=query):
                self.assertTrue(validate_sql_query(query))

    def test_rejects_non_read_only_or_multi_statement_sql(self):
        rejected_queries = [
            "DROP TABLE vendors",
            "SELECT * FROM vendors; DELETE FROM invoices",
            "SELECT * INTO archive FROM vendors",
            "PRAGMA table_info(vendors)",
            "SELECT * FROM sqlite_master",
        ]

        for query in rejected_queries:
            with self.subTest(query=query):
                with self.assertRaises(SQLValidationError):
                    validate_sql_query(query)
