# grade-tracker

Keep track of marks, see letter grades and a GPA for each student, and export an HTML report. A command-line tool in plain Python with no dependencies. Marks are saved in `data/grades.csv`.

## Running it

You need Python 3.8 or newer.

```
python3 -m grade_tracker list
python3 -m grade_tracker summary CS101
python3 -m unittest discover tests
```

On Windows, use `python` instead of `python3`.

## Commands

| Command | Does |
|---|---|
| `list` | Every mark, with its letter grade |
| `summary <student_id>` | One student's marks and GPA |
| `average <subject>` | The class average for a subject |
| `add <student_id> <subject> <score> <max_score> <date> <credits> <semester>` | Adds a mark |
| `edit <student_id> <semester> <subject> <date> [--score N] [--max-score N] [--credits N] [--student-id ID] [--semester N] [--subject NAME] [--date YYYY-MM-DD]` | Edits one existing mark |
| `export [--output path]` | Writes an HTML report, `reports/index.html` by default |
| `validate` | Audits the CSV for malformed or invalid records, without changing the file |

`edit` corrects an existing record without touching the CSV by hand. The record is identified by its current assessment identity: `student_id`, `semester`, `subject`, and `date`. Only the options you pass are changed — `--score`, `--max-score`, `--credits`, `--student-id`, `--semester`, `--subject`, `--date` — and every omitted option keeps the record's current value (it is never replaced with a default). At least one change option is required.

The whole update runs as one transaction: the command takes the same cross-process lock `add` uses, re-reads the file, applies the change in memory, validates it with the same rules as `add` (score between 0 and `max_score`, `max_score` greater than 0, `date` in `YYYY-MM-DD`), checks that the resulting identity does not duplicate a *different* record, then writes a temporary file next to the CSV, flushes it, and atomically replaces the original. If the target is missing, several records match it (ambiguous), validation fails, or the write fails, the command exits 2 with an error on stderr and the file is left byte-for-byte unchanged. Retakes in a different semester or on a different date are still allowed, because they are different assessments.

`validate` is strictly read-only: it never rewrites, repairs, or deletes records, so file timestamps and bytes are untouched. Each problem is reported on stderr with its CSV row number (the header is row 1, the first data record is row 2), the affected field, and a reason. It exits 0 when the file is clean and 1 when any problem is found. Rows that `list`/`summary` would silently skip (a blank student ID or subject, a non-numeric score, and so on) are flagged here instead of being dropped. Legacy rows that simply omit the optional `credits` or `semester` columns are still accepted, matching the loader's documented defaults of 3.0 credits and semester 1.

## How it's supposed to work

- Grades go by percentage: 90 and above is A, 80–89 B, 70–79 C, 60–69 D, below 60 F.
- GPA is on a 4-point scale: A 4, B 3, C 2, D 1, F 0.
- A subject's average is the average percentage, so marks out of different totals compare fairly. 35/50 and 70/100 are both 70%.
- `add` refuses a score below 0 or above the maximum, a maximum of 0 or less, and a date that isn't `YYYY-MM-DD`.
- The report has a card for every student in the CSV, and only those.
- Everything in the report is shown as plain text. A subject name containing HTML is displayed, not run.

## Code

- `grade_tracker/calc.py`: letter grades, GPA, averages
- `grade_tracker/cli.py`: the commands, and reading and writing the CSV
- `grade_tracker/export.py`: the HTML report
- `tests/`: tests, run with `python3 -m unittest discover tests`

## Contributing

Fork the repo, make your changes on a new branch, and open a pull request. Run the tests first.

If you find a bug, open an issue with the steps to reproduce it, what you expected, and what happened instead.

Part of Source Start by CSI SPIT. MIT licensed.
