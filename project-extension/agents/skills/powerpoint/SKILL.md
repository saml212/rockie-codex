---
name: powerpoint
description: Create validated PowerPoint `.pptx` deck artifacts through the lean Rockie PPTAgent wrapper and emit them to the lab.
scope: community
contributor_name: Elias Ward
contributed_at: "2026-03-22"
---

# powerpoint

Use this skill when the user asks for a PowerPoint deck, slide deck, or
`.pptx` artifact based on lab findings, sources, notes, or experiment
plans.

Tenant runtimes call the lean PPTAgent package, not DeepPresenter:

```bash
rockie-pptagent-render --input request.json --output deck.pptx
```

The equivalent import contract is:

```python
from rockie_pptagent.wrapper import render_presentation
```

## Rules

- Produce a real `.pptx` package. Do not rename PDF, HTML, images, or
  markdown as `.pptx`.
- Use the Pebble ML template bundled with `rockie-pptagent` unless the
  user supplies a specific template.
- Cap decks at 30 slides. Requests above 30 must fail clearly or be
  rewritten with an explicit warning before rendering.
- Do not use DeepPresenter, Tavily, MinerU, model downloads, image
  generation, local GPU frameworks, or network-only enrichment paths.
- Verify the output as an Office package before emitting it.

## Request Shape

```json
{
  "prompt": "Create a 5-slide deck summarizing this lab.",
  "slide_count": 5,
  "title": "Quantifying Impact",
  "findings": [],
  "attachments": [],
  "template_path": null,
  "theme": {
    "background": "#FAF5E7",
    "accent": "#8B2E1F",
    "heading_font": "Space Grotesk",
    "mono_font": "JetBrains Mono"
  }
}
```

## Artifact Emission

After rendering, base64-encode the `.pptx` bytes and call
`emit_artifact` with:

- `kind: "slides"`
- `content_encoding: "base64"`
- `filename: "<safe-name>.pptx"`
- `mime_type: "application/vnd.openxmlformats-officedocument.presentationml.presentation"`
- `destinations: ["ui", "chat"]`

Report the emitted artifact id or final path. If rendering fails, report
the wrapper exit code and stderr summary without leaking tenant secrets.
