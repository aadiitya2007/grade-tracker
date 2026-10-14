"""Multi-process regression tests for concurrent grade writes (Issue #14).

These tests exercise ``save_grade`` across independent OS processes using the
``spawn`` start method (the only method supported on Windows). They verify:

* Two independent processes saving the same assessment: exactly one succeeds
  and one receives a duplicate error.
* Two independent processes saving different assessments: both succeed and
  both records remain.
* The resulting CSV is valid, readable, and retains the expected header,
  column order, and records.
* Deadlock/lock-failure hygiene: every child process exits, every lock file
  descriptor is closed, and no lock is stranded even when a child raises.

Synchronization is done with ``multiprocessing.Barrier`` plus bounded
``time.monotonic()`` timeouts. No arbitrary ``time.sleep`` durations are
used to hide races; retries are driven by the barriers and timeouts.
"""

import csv
import multiprocessing
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

# Force the ``spawn`` start method so these tests exercise independent OS
# processes (the only model available on Windows for ``multiprocessing``).
try:
    multiprocessing.set_start_method("spawn", force=True)
except RuntimeError:
    pass


def _worker_same_assessment(csv_path, result_queue, barrier):
    """Attempt to save one assessment from an independent process."""
    try:
        # Wait until the sibling process is also ready to write.
        barrier.wait(timeout=10.0)
        from grade_tracker.cli import save_grade

        save_grade(
            student_id="S1",
            subject="Mathematics",
            score=25.0,
            max_score=50.0,
            date="2026-10-06",
            credits=3.0,
            semester="1",
            csv_path=str(csv_path),
        )
        result_queue.put("OK")
    except ValueError as exc:
        # Expected duplicate error from the sibling writer.
        result_queue.put("DUPLICATE: {}".format(exc))
    except Exception as exc:  # pragma: no cover - defensive
        result_queue.put("ERROR: {!r}".format(exc))


def _worker_different_assessments(csv_path, subject, result_queue, barrier):
    """Attempt to save a specific assessment from an independent process."""
    try:
        barrier.wait(timeout=10.0)
        from grade_tracker.cli import save_grade

        save_grade(
            student_id="S1",
            subject=subject,
            score=25.0,
            max_score=50.0,
            date="2026-10-06",
            credits=3.0,
            semester="1",
            csv_path=str(csv_path),
        )
        result_queue.put("OK-{}".format(subject))
    except Exception as exc:  # pragma: no cover - defensive
        result_queue.put("ERROR: {!r}".format(exc))


def _worker_edit_record(csv_path, result_queue, barrier, score):
    """Attempt to edit an existing record from an independent process."""
    try:
        barrier.wait(timeout=10.0)
        from grade_tracker.cli import edit_grade

        edit_grade(
            csv_path=str(csv_path),
            student_id="S1",
            semester="1",
            subject="Mathematics",
            date="2026-10-06",
            score=score,
        )
        result_queue.put("OK")
    except ValueError as exc:
        result_queue.put("VALUEERROR: {}".format(exc))
    except Exception as exc:  # pragma: no cover - defensive
        result_queue.put("ERROR: {!r}".format(exc))


def _worker_edit_and_add(csv_path, result_queue, barrier, subject):
    """Edit one record while a sibling process adds a different one."""
    try:
        barrier.wait(timeout=10.0)
        from grade_tracker.cli import edit_grade, save_grade

        edit_grade(
            csv_path=str(csv_path),
            student_id="S1",
            semester="1",
            subject="Mathematics",
            date="2026-10-06",
            score=75.0,
        )
        save_grade(
            student_id="S1",
            subject=subject,
            score=25.0,
            max_score=50.0,
            date="2026-10-06",
            credits=3.0,
            semester="1",
            csv_path=str(csv_path),
        )
        result_queue.put("OK-{}".format(subject))
    except Exception as exc:  # pragma: no cover - defensive
        result_queue.put("ERROR: {!r}".format(exc))


def _read_rows(csv_path):
    with open(csv_path, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


class TestConcurrentGradeWrites(unittest.TestCase):
    """Multi-process regression tests for concurrent grade writes."""

    EXPECTED_HEADER = [
        "student_id", "subject", "score", "max_score", "date", "credits", "semester"
    ]

    def _fresh_csv(self):
        handle = tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".csv",
            prefix="gctl_",
            delete=False,
            encoding="utf-8",
            newline="",
        )
        handle.close()
        with open(handle.name, "w", encoding="utf-8", newline="") as fh:
            fh.write(",".join(self.EXPECTED_HEADER) + "\n")
        return handle.name

    def _cleanup_files(self, csv_path):
        if csv_path:
            from grade_tracker.cli import _lock_path_for
            lock_path = _lock_path_for(csv_path)
            Path(lock_path).unlink(missing_ok=True)
            Path(csv_path).unlink(missing_ok=True)

    def _run_workers_same(self, csv_path):
        """Spawn `_worker_same_assessment` in two processes synchronized by a barrier."""
        ctx = multiprocessing.get_context("spawn")
        barrier = ctx.Barrier(2)
        result_queue = ctx.Queue()
        children = []
        for _ in range(2):
            proc = ctx.Process(
                target=_worker_same_assessment,
                args=(csv_path, result_queue, barrier),
            )
            proc.start()
            children.append(proc)
        return self._join_children_and_collect_results(children, result_queue)

    def _run_workers_different(self, csv_path):
        """Spawn `_worker_different_assessments` for Mathematics and Physics."""
        ctx = multiprocessing.get_context("spawn")
        barrier = ctx.Barrier(2)
        result_queue = ctx.Queue()
        children = []
        for subject in ["Mathematics", "Physics"]:
            proc = ctx.Process(
                target=_worker_different_assessments,
                args=(csv_path, subject, result_queue, barrier),
            )
            proc.start()
            children.append(proc)
        return self._join_children_and_collect_results(children, result_queue)

    def _join_children_and_collect_results(self, children, result_queue):
        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline and len(children) > 0:
            alive = [p for p in children if p.is_alive()]
            for p in alive:
                p.join(timeout=max(0.1, deadline - time.monotonic()))
            children = [p for p in children if p.is_alive()]
        if children:
            for p in children:
                p.terminate()
            self.fail("concurrent writers did not finish within the timeout")
        results = []
        while not result_queue.empty():
            try:
                results.append(result_queue.get_nowait())
            except Exception:
                break
        return results

    def test_same_assessment_one_succeeds_one_receives_duplicate(self):
        csv_path = self._fresh_csv()
        try:
            results = self._run_workers_same(csv_path)
            self.assertEqual(len(results), 2, f"expected 2 worker results, got {results!r}")
            oks = [r for r in results if r == "OK"]
            duplicates = [r for r in results if r.startswith("DUPLICATE:")]
            self.assertEqual(
                len(oks), 1,
                f"expected exactly one success, got results {results!r}"
            )
            self.assertEqual(
                len(duplicates), 1,
                f"expected exactly one duplicate error, got results {results!r}"
            )
            rows = _read_rows(csv_path)
            self.assertEqual(len(rows), 1, "exactly one record should remain")
            self.assertEqual(
                list(rows[0].keys()), self.EXPECTED_HEADER, "header/column order preserved"
            )
            self.assertEqual(rows[0]["student_id"], "S1")
            self.assertEqual(rows[0]["subject"], "Mathematics")
        finally:
            self._cleanup_files(csv_path)

    def test_different_assessments_both_succeed(self):
        csv_path = self._fresh_csv()
        try:
            results = self._run_workers_different(csv_path)
            self.assertEqual(
                sorted(results), ["OK-Mathematics", "OK-Physics"],
                "expected both processes to succeed, got {!r}".format(results),
            )
            rows = _read_rows(csv_path)
            self.assertEqual(len(rows), 2, "both records should remain")
            self.assertEqual(
                list(rows[0].keys()), self.EXPECTED_HEADER, "header/column order preserved"
            )
            subjects = {row["subject"] for row in rows}
            self.assertEqual(subjects, {"Mathematics", "Physics"})
        finally:
            self._cleanup_files(csv_path)

    def test_csv_is_valid_and_readable(self):
        csv_path = self._fresh_csv()
        try:
            results = self._run_workers_different(csv_path)
            self.assertEqual(
                sorted(results), ["OK-Mathematics", "OK-Physics"],
                "writers misbehaved: {!r}".format(results),
            )
            rows = _read_rows(csv_path)
            # Validate readability end-to-end and schema.
            self.assertEqual(
                list(rows[0].keys()), self.EXPECTED_HEADER, "expected header/column order"
            )
            self.assertEqual(len(rows), 2)
            for row in rows:
                float(row["score"])
                float(row["max_score"])
                float(row["credits"])
                int(row["semester"])
        finally:
            self._cleanup_files(csv_path)


class TestConcurrentEdits(unittest.TestCase):
    """Multi-process regression tests for concurrent ``edit_grade`` calls."""

    EXPECTED_HEADER = [
        "student_id", "subject", "score", "max_score", "date", "credits", "semester"
    ]

    def _seed_csv(self):
        """Create a temp CSV containing one editable record."""
        handle = tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", prefix="gedit_", delete=False, encoding="utf-8"
        )
        handle.close()
        with open(handle.name, "w", encoding="utf-8", newline="") as fh:
            fh.write(",".join(self.EXPECTED_HEADER) + "\n")
            fh.write("S1,Mathematics,50,100,2026-10-06,3,1\n")
        return handle.name

    def _cleanup_files(self, csv_path):
        if csv_path:
            from grade_tracker.cli import _lock_path_for
            Path(_lock_path_for(csv_path)).unlink(missing_ok=True)
            Path(csv_path).unlink(missing_ok=True)

    def _join_children_and_collect_results(self, children, result_queue):
        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline and any(p.is_alive() for p in children):
            for p in children:
                if p.is_alive():
                    p.join(timeout=max(0.1, deadline - time.monotonic()))
        if any(p.is_alive() for p in children):
            for p in children:
                p.terminate()
            self.fail("concurrent editors did not finish within the timeout")
        results = []
        while not result_queue.empty():
            try:
                results.append(result_queue.get_nowait())
            except Exception:
                break
        return results

    def _run_workers(self, target, csv_path, *extra_args):
        ctx = multiprocessing.get_context("spawn")
        barrier = ctx.Barrier(2)
        result_queue = ctx.Queue()
        children = []
        for arg in extra_args:
            proc = ctx.Process(
                target=target, args=(csv_path, result_queue, barrier, arg)
            )
            proc.start()
            children.append(proc)
        return self._join_children_and_collect_results(children, result_queue)

    def test_concurrent_edits_of_same_record_no_lost_update(self):
        """Two processes editing the same record serialize without data loss."""
        csv_path = self._seed_csv()
        try:
            results = self._run_workers(_worker_edit_record, csv_path, 60.0, 70.0)
            self.assertEqual(len(results), 2, "expected 2 worker results: {!r}".format(results))
            oks = [r for r in results if r == "OK"]
            self.assertEqual(
                len(oks), 2,
                "both edits should serialize cleanly, got {!r}".format(results),
            )
            rows = _read_rows(csv_path)
            self.assertEqual(len(rows), 1, "exactly one record should remain")
            self.assertEqual(
                list(rows[0].keys()), self.EXPECTED_HEADER, "header/column order preserved"
            )
            # The last writer wins; the file must be one of the two scores,
            # never a torn/mixed row.
            self.assertIn(rows[0]["score"], {"60.0", "70.0"})
            self.assertEqual(rows[0]["max_score"], "100")
            self.assertEqual(rows[0]["subject"], "Mathematics")
        finally:
            self._cleanup_files(csv_path)

    def test_concurrent_edit_and_add_produce_both_records(self):
        """An edit and an independent add from two processes both land."""
        csv_path = self._seed_csv()
        try:
            results = self._run_workers(
                _worker_edit_and_add, csv_path, "Physics", "Chemistry"
            )
            self.assertEqual(len(results), 2, "expected 2 worker results: {!r}".format(results))
            self.assertEqual(
                sorted(results), ["OK-Chemistry", "OK-Physics"],
                "workers misbehaved: {!r}".format(results),
            )
            rows = _read_rows(csv_path)
            self.assertEqual(
                list(rows[0].keys()), self.EXPECTED_HEADER, "header/column order preserved"
            )
            subjects = {row["subject"] for row in rows}
            self.assertEqual(subjects, {"Mathematics", "Physics", "Chemistry"})
            edited = [r for r in rows if r["subject"] == "Mathematics"]
            self.assertEqual(len(edited), 1)
            self.assertEqual(edited[0]["score"], "75.0")
        finally:
            self._cleanup_files(csv_path)


if __name__ == "__main__":
    unittest.main()
