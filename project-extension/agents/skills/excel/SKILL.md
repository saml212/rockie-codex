---
name: excel
description: Create validated Excel `.xlsx` workbook artifacts with XlsxWriter, formula-injection protection, and Rockie artifact emission.
scope: community
contributor_name: Priya Nair
contributed_at: "2026-03-18"
---

# excel

Use this skill when the user asks for an Excel workbook or an `.xlsx`
artifact, including creating, modifying, formatting, analyzing,
validating, or exporting workbook content. For read-only analysis or
validation requests, inspect the workbook and report findings without
writing a new file unless the user asks for one.

In tenant runtimes, new workbook generation is productized through
`skills/excel/runtime/renderer.py`. Generate the workbook locally in the
tenant workspace, verify it, then publish it with the `emit_artifact`
tool using:

- `kind: "spreadsheet"`
- `content_encoding: "base64"`
- `filename: "<safe-name>.xlsx"`
- `mime_type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"`
- `destinations: ["ui", "chat"]`

## Core rules

- Produce a real `.xlsx` workbook. Do not rename CSV, TSV, HTML, JSON,
  or plain text files to `.xlsx`.
- Use `runtime/sanitize.py` at the cell-write boundary. Strings starting
  with `=`, `+`, `-`, or `@` are text unless the cell is explicitly typed
  as a formula.
- Preserve source data. Keep raw inputs available when appropriate,
  such as a `Source Data` or `Raw Data` sheet, and avoid silently
  replacing user-provided values with summaries.
- Use workbook features that fit the task: multiple sheets, formulas,
  number formats, styles, column widths, freeze panes, filters, charts,
  data validation, named ranges, and notes/comments when they materially
  improve usability.
- Keep formulas as formulas unless the user explicitly asks for static
  values.
- When writing a workbook, reopen and verify the finished file before
  reporting it.
- When writing a workbook, report the emitted artifact id or final path.
- Cap new workbook generation at 20 sheets. If the user asks for more,
  create the most useful 20 or fail clearly before writing, depending on
  the task.

## Verification

After writing the `.xlsx`, reopen it with an Excel-capable parser or by
inspecting the ZIP/XML package when no dependency is available. Verify:

- Expected workbook and worksheet names.
- Sheet dimensions and important ranges.
- Target cells changed as requested.
- Formulas are present as formula XML or formulas, not only cached
  values.
- Styles, number formats, widths, freeze panes, filters, charts, and
  validation rules that matter to the task.
- Sample values from source data and calculated or summarized outputs.

If verification fails, fix the workbook and verify again before
responding.

## Runtime renderer

The renderer accepts a JSON request and writes a workbook:

```bash
python "${OPENCLAW_SKILLS_DIR}/excel/runtime/renderer.py" \
  --input request.json \
  --output workbook.xlsx
```

Request shape:

```json
{
  "sheets": [
    {
      "name": "Sources",
      "headers": ["Title", "Type", "Score"],
      "rows": [["Paper A", "paper", 0.92]],
      "freeze_panes": "A2",
      "autofilter": true,
      "charts": [
        {
          "type": "column",
          "title": "Scores",
          "categories": "$A$2:$A$10",
          "values": "$C$2:$C$10",
          "position": "E2"
        }
      ]
    }
  ]
}
```

Cells can be objects when type matters:

```json
{"type": "formula", "value": "SUM(C2:C10)"}
```

String cells that look like formulas are sanitized automatically unless
they use the explicit formula object form.
