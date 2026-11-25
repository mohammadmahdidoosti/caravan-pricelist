"""«.xls» files: HTML tables from accounting programs and xlsx files with the wrong name."""
import tempfile
import unittest
from pathlib import Path

import openpyxl

from pl.read import table_rows


class Xls(unittest.TestCase):
    def test_html_table_named_xls(self):
        p = Path(tempfile.mkdtemp(), "a.xls")
        p.write_text("<table><tr><th>ردیف</th><th>نام کالا</th><th>قیمت</th></tr>"
                     "<tr><td>1</td><td>فیوز 10 آمپر</td><td>120,000</td></tr></table>", encoding="utf-8")
        rows = table_rows(p)
        self.assertEqual([(r["name"], r["price"]) for r in rows], [("فیوز 10 آمپر", "120,000")])

    def test_xlsx_named_xls(self):
        wb = openpyxl.Workbook()
        wb.active.append(["نام", "قیمت"])
        wb.active.append(["رله", 99000])
        p = Path(tempfile.mkdtemp(), "b.xls")
        wb.save(p)
        self.assertEqual([(r["name"], r["price"]) for r in table_rows(p)], [("رله", "99000")])


if __name__ == "__main__":
    unittest.main()
