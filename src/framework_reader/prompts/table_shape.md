You are looking at a table read from an Excel or CSV file, judging its
structure.

A workbook may contain **several sheets**, separated by lines like
`=== Sheet "Name" ===`. The common layout is an instructions sheet first and the
actual list after it - **do not stop at the first sheet**. Once you have chosen,
put `"sheet":"the name of that sheet"` in your answer.

Each line you get looks like `R3 C0:Control ID │ C1:Control name │ C2:Parent` -
`R` is the row number (from 1), `C` is the column number (from 0). Cell contents
are truncated; just enough to recognize structure.

**You point at positions only; you never copy contents.** The program extracts
every value cell by cell using your row and column numbers - you never get to
touch them.

First decide which of the two this is:

**One: it is a control list** - one header row, then one control per row. The
header cells say things like "ID / Control ID / Ref" and "Title / Name /
Control". Sheets from real organizations often have a big title or form-info row
sitting **above** the header, so the header is not always row 1.

Answer:

```
{"kind":"table","sheet":"Checklist","header_row":3,
 "id_col":0,"label_col":1,"parent_col":2,"body_col":3}
```

- `sheet`: which sheet the data is in. If there is only one, use "".
- `header_row`: the header row **within that sheet** (from 1).
- `id_col`: the column holding the control number (from 0). **When several
  columns look like numbers, pick the finest-grained one** - `Process ID`
  (T1.1, T1.2) is finer than `Outcome ID` (T1, T1), because the latter repeats
  across rows and numbers must be unique per row.
- `parent_col`: the coarser column is usually the parent. In the example, put
  `Outcome ID` here.
- `label_col`: the column holding the control title.
- `body_col`: the column holding the control body / requirement. null if none.

**Two: it is not a control list at all** - for example a policy document pasted
into Excel, one paragraph per row, with no "ID" or "Title" columns; or a ledger
or stats table where a row is not a control.

Answer:

```
{"kind":"document","sheet":"","why":"one sentence on why you decided this"}
```

**When unsure, answer document.** A forced column mapping produces an entire
fake control list, which is far worse than taking another route.

Output a single JSON object, no explanations, no leading or trailing text, no
markdown code fences.
