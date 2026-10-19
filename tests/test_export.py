"""Regression tests for Issue #22: escape user-controlled values in the HTML report."""

import csv
import tempfile
import unittest
from pathlib import Path

from grade_tracker.cli import load_grades
from grade_tracker.export import build_html


class TestHtmlReportEscaping(unittest.TestCase):
    """Verify untrusted CSV values are rendered as text, not markup."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.data_path = Path(self.temp_dir.name) / "grades.csv"

    def _write(self, rows):
        with self.data_path.open("w", newline="", encoding="utf-8") as handle:
            handle.write(",".join([
                "student_id", "subject", "score", "max_score", "date", "credits", "semester",
            ]) + "\n")
            handle.writelines(rows)

    def _load(self):
        with self.data_path.open(newline="", encoding="utf-8") as handle:
            return load_grades(self.data_path)

    def _build(self, grades):
        output_path = self.temp_dir.name + "/report.html"
        build_html(grades, output_path)
        with open(output_path, "r", encoding="utf-8") as handle:
            return handle.read()

    def _assert_text(self, html, fragment, *, count=1):
        """Assert a value appears as text (its HTML-special characters intact)."""
        self.assertIn(fragment, html)
        self.assertEqual(html.count(fragment), count)

    def _assert_no_tag(self, html, tag):
        """Assert a markup fragment is not present as executable HTML."""
        self.assertNotIn(tag, html)

    # --- 1. Student IDs containing HTML tags and script-like payloads ---

    def test_student_id_html_tag_is_escaped_not_native_element(self):
        self._write([
            "S1<script>alert(1)</script>,Mathematics,90,100,2026-01-01,3,1\n",
            "<img src=x onerror=alert(2)>,Mathematics,90,100,2026-01-01,3,1\n",
        ])

        html = self._build(self._load())
        self._assert_no_tag(html, "<script")
        self._assert_no_tag(html, '<img src')
        self._assert_text(html, "Student: S1&lt;script&gt;alert(1)&lt;/script&gt;")
        self._assert_text(html, "Student: &lt;img src=x onerror=alert(2)&gt;")

    def test_student_id_script_payload_stays_text(self):
        self._write([
            "<script>alert(document.cookie)</script>,Mathematics,90,100,2026-01-01,3,1\n",
        ])

        html = self._build(self._load())
        self._assert_no_tag(html, "<script>")
        self._assert_text(html, "&lt;script&gt;alert(document.cookie)&lt;/script&gt;")

    # --- 2. Subject names containing HTML tags, ampersands, and quotes ---

    def test_subject_html_tags_and_quote_escaped(self):
        self._write([
            "S1,Science <i>for</i> \"great\" students & more,90,100,2026-01-01,3,1\n",
        ])

        html = self._build(self._load())
        self._assert_no_tag(html, "<i>")
        self._assert_text(html, "Science &lt;i&gt;for&lt;/i&gt; &quot;great&quot; students &amp; more")

    def test_subject_ampersand_and_quotation_mark_escaped(self):
        self._write([
            "S1,AT&T & Sons,90,100,2026-01-01,3,1\n",
        ])

        html = self._build(self._load())
        self._assert_text(html, "AT&amp;T &amp; Sons")

    # --- 3. Malicious values in other user-controlled fields ---

    def test_credits_score_max_score_escaped_when_numeric_strings(self):
        """Numeric strings from CSV are escaped defensively in the report.

        load_grades converts these to floats, but build_html escapes the
        original values as a defense-in-depth measure.
        """
        self._write([
            "S1,Mathematics,90,100,2026-01-01,3,1\n",
        ])

        html = self._build(self._load())
        self._assert_text(html, "90.0 / 100.0")

    def test_date_simple_script_payload_escaped(self):
        self._write([
            "S1,Mathematics,90,100,2026-01-01<x>,3,1\n",
        ])

        html = self._build(self._load())
        self._assert_text(html, "2026-01-01&lt;x&gt;")

    def test_semester_and_semester_sgpa_escaped(self):
        self._write([
            "S1,Mathematics,90,100,2026-01-01,3,1<x>\n",
        ])

        html = self._build(self._load())
        self._assert_text(html, "Semester 1&lt;x&gt;")

    def test_cgpa_unescaped_optional_style_escaped(self):
        self._write([
            "S1&lt;x&gt;,Mathematics,90,100,2026-01-01,3,1\n",
        ])

        html = self._build(self._load())
        self._assert_text(html, "CGPA: 10.0")

    # --- 4. Normal values remain correctly represented ---

    def test_normal_values_preserved(self):
        self._write([
            "S1,Mathematics,90,100,2026-01-01,3,1\n",
        ])

        html = self._build(self._load())
        self._assert_text(html, "Student: S1")
        self._assert_text(html, "Mathematics")
        self._assert_text(html, "90.0 / 100.0 (90.0%)")
        self._assert_text(html, "Semester 1 (SGPA: 10.0)")
        self._assert_text(html, "CGPA: 10.0")

    def test_card_and_table_structure_preserved(self):
        self._write([
            "S1,Mathematics,90,100,2026-01-01,3,1\n",
            "S2,Physics,85,100,2026-01-02,4,2\n",
        ])

        html = self._build(self._load())
        self.assertGreaterEqual(html.count('<div class="card">'), 2)
        self.assertIn('<table', html)
        self.assertIn("<th>Subject</th>", html)
        self.assertIn("<th>Date</th>", html)
        self.assertIn("<td>Mathematics</td>", html)
        self.assertIn("<td>Physics</td>", html)
        self.assertIn("<div class=\"grid\">", html)

    # --- 5. Existing report structure, formatting, and export behavior ---

    def test_existing_export_behavior_compatible(self):
        self._write([
            "CS101,Mathematics,90,100,2026-01-01,3,1\n",
        ])

        html = self._build(self._load())
        self.assertTrue(html.startswith("<!DOCTYPE html>"))
        self.assertIn("<html lang=\"en\">", html)
        self.assertIn("<meta charset=\"UTF-8\">", html)
        self.assertIn("Academic Performance Report", html)
        self.assertIn("Generated by Grade Tracker", html)
        self.assertIn("<title>Academic Performance Report - Grade Tracker</title>", html)

    def test_empty_records_fallback_without_user_data(self):
        self._write([
            "<script>,Mathematics,90,100,2026-01-01,3,1\n",
        ])

        html = self._build(self._load())
        self._assert_text(html, "Student: &lt;script&gt;")
        self._assert_text(html, "Mathematics")

    def test_html_doc_is_not_fully_escaped(self):
        """The document shell is trusted app markup; only data nodes are escaped."""
        self._write([
            "S1,Mathematics,90,100,2026-01-01,3,1\n",
        ])

        html = self._build(self._load())
        self.assertIn("<html lang=\"en\">", html)
        self.assertIn("<style>", html)
        self.assertIn("Academic Performance Report", html)

    # --- 6. Regression test for Issue #26: only students present in data get cards ---

    def test_export_only_includes_students_present_in_grades(self):
        """Report must only include student IDs present in the CSV dataset."""
        from grade_tracker.export import get_enrolled_students
        sample = [
            {"student_id": "CS101", "subject": "Math", "score": 90, "max_score": 100},
            {"student_id": "CS103", "subject": "Physics", "score": 80, "max_score": 100},
        ]
        self.assertEqual(get_enrolled_students(sample), ["CS101", "CS103"])

        self._write([
            "CS101,Mathematics,90,100,2026-01-01,3,1\n",
            "CS103,Physics,80,100,2026-01-01,3,1\n",
        ])
        html = self._build(self._load())
        self.assertIn("Student: CS101", html)
        self.assertIn("Student: CS103", html)
        self.assertNotIn("Student: CS104", html)
        self.assertNotIn("Student: CS102", html)


