# publish — bundle, land, export

`/publish` takes an **accept-ready** draft and turns it into something a person can
download, cite, and share: it assembles a bundle, lands it as a lab Note plus a
downloadable artifact, optionally exports to GitHub or Hugging Face, and exposes the
Rock-Collection canonical-URL push as an explicit labeled stub. It never mutates
Sources and never fabricates a URL.

## Precondition (hard)

`/publish` refuses a draft that is not accept-ready — all seven criteria in
`references/method.md` § "Acceptance criteria" must hold. If they do not, do not
publish: report which criteria are unmet and route back to `/paper-draft`.

## Step 1 — assemble the bundle

The bundle is the complete, self-contained package for the paper:

- **PDF** — the compiled paper. Compile through Rockie compute, not the
  orchestrator host (see "LaTeX compile" below).
- **LaTeX source** — `main.tex`, `sections/*.tex`, `refs.bib`, the style file.
- **Figures** — every figure PDF plus the single versioned figure script
  (`templates/figure-gen.py.tmpl` filled in) that produced them.
- **Reproducibility spec** — a `README` describing what to run to reproduce each
  number: the scripts, the data, the commands, and the mapping from each headline
  number to the script/run that produced it.
- **Evidence pointers** — the claims-to-evidence map from the brief, so every
  number in the paper points at its source row.

The reproducibility spec and evidence pointers are what make the bundle
trustworthy: a reader can trace any claim to the code and data behind it.

### LaTeX compile (on Rockie compute, never the host)

Do not run `pdflatex` on the orchestrator host. Submit the compile as a job to the
lab's runtime, the same path `/experiment` uses:

```bash
# First render and approve a compile term sheet, then submit through /experiment.
python3 ${OPENCLAW_SKILLS_DIR}/budget-term-sheet/scripts/quote_term_sheet.py \
    --job-spec-json /tmp/latex-compile-job.json \
    > /tmp/latex-compile.term-sheet.json
python3 ${OPENCLAW_SKILLS_DIR}/budget-term-sheet/scripts/render_term_sheet.py \
    --term-sheet-json /tmp/latex-compile.term-sheet.json
# wait for explicit approve / modify / cancel in chat, save the approved artifact, then:
python3 ${OPENCLAW_SKILLS_DIR}/experiment/runtime/submit.py \
    --gpu-type A40_48GB \
    --gpu-count 1 \
    --region us \
    --tier spot \
    --script-file /tmp/latex-compile.sh \
    --timeout 3600 \
    --term-sheet-json /tmp/latex-compile.term-sheet.approved.json
# then fetch the resulting main.pdf from the job artifacts
curl -s "$ROCKIELAB_API_URL/api/jobs/${JOB_ID}/artifacts" \
    -H "User-Agent: rockie-runtime/1.0 (+https://api.rockielab.com)" \
    -H "X-Tenant-Token: $ROCKIELAB_TENANT_TOKEN" \
    -H "X-Tenant-Id: $ROCKIELAB_TENANT_ID"
```

The compile job runs `pdflatex main.tex && bibtex main && pdflatex main.tex &&
pdflatex main.tex` (the standard two-bibtex-pass sequence) and returns `main.pdf`
as an artifact. Confirm the PDF has no `??` markers (unresolved references) before
bundling it.

## Step 2 — land as a lab Note and a downloadable artifact

Persist the bundle through the platform-context Notes service and the artifact
emitter. The Note is the human-facing record in the lab; the artifact is the
downloadable file. Both operations below were read out of the live
platform-context API — they are the real routes/functions, not guessed endpoints.

### The Note (verified: `POST /api/notes`)

Create a **Note** in the lab describing the paper (title, abstract, the
reproducibility pointers, links to the gauntlet and detector-history Notes):

- **Route:** `POST /api/notes` — `api/routers/notes.py` → `create_note`, registered
  with the `/api` prefix in `api/main.py`, delegating to
  `notes_service.create_note(...)` (`api/notes_service.py`).
- **Body:** the `NoteCreate` schema (`api/models.py`): `content` (required —
  the Markdown description), `title`, `note_type: "ai"`, `notebook_id` (the lab),
  optional `metadata` for typed markers.
- **Auth:** the call goes through `PasswordAuthMiddleware` (`Authorization: Bearer
  $ROCKIELAB_PASSWORD`); the `tenant_id` is derived server-side from the request by
  `api/tenant_context.py` (SSO session cookie → `X-Tenant-Id` header → legacy
  `X-Tenant-Token` header), **not** read from the body. Send `X-Tenant-Id`
  (`$ROCKIELAB_TENANT_ID`) so the Note lands under the right tenant.

```bash
curl -s "$ROCKIELAB_API_URL/api/notes" \
    -H "User-Agent: rockie-runtime/1.0 (+https://api.rockielab.com)" \
    -H "Authorization: Bearer $ROCKIELAB_PASSWORD" \
    -H "X-Tenant-Id: $ROCKIELAB_TENANT_ID" \
    -H "Content-Type: application/json" \
    -d '{"content":"<paper description markdown>","title":"<paper title>","note_type":"ai","notebook_id":"<lab notebook id>"}'
```

### The downloadable artifact (verified: the artifact emitter, not a REST POST)

There is **no** public `POST /api/artifacts` create route — the artifacts router
(`api/routers/artifacts.py`) only lists/gets/streams/deletes. Downloadable artifacts
are created server-side through the **artifact emitter**,
`api/services/artifact_emitter.py` → `emit_artifact(...)`. Its `ui` destination
writes a `lab_artifact` row (which the `/artifacts` UI reads and which exposes the
downloadable, HMAC-signed file URL); its `chat` destination surfaces it in the
session. Emit the bundle with `destinations=["ui","chat"]`, `kind`/`filename`/
`mime_type` set for the zip, and `notebook_id` = the lab; the returned
`EmitDestinationsResult.artifact_id` is the persisted artifact. Surface that
artifact's signed download URL to the user.

If your runtime exposes the emitter only indirectly (e.g. through an agent
artifact-emit tool rather than a direct function call), use that tool — the
underlying operation is `emit_artifact`. Do not invent a `POST /api/artifacts`
route; it does not exist.

Sources stay inputs; the paper bundle is a Note + an emitted artifact (output).
Never write the bundle back over a Source.

## Step 3 — optional external export (GitHub / Hugging Face)

If the user asks to export, push to GitHub and/or Hugging Face using the approved
**tenant-secrets path**. This is the same path the artifact emitter already uses for
its `github` / `huggingface` destinations — do not invent a token-handling scheme.
The verified mechanism (`api/services/artifact_emitter.py`):

- **Where the tokens live.** The GitHub PAT and the HF token are stored
  **encrypted at rest** in the tenant's `tenant_compute_config.extras` blob, under
  the keys `github_pat` and `hf_token`. They are decrypted in-process only, via
  `_load_tenant_secrets(tenant_id)` (which calls the Fernet decrypt path keyed by
  `OPEN_NOTEBOOK_ENCRYPTION_KEY`). The tokens are **never** hard-coded, never asked
  for in plain chat, and never sent in a request body.
- **How the push runs.** Emit the bundle through `emit_artifact(...)` with
  `destinations=["github"]` and a `github_target` (or `["huggingface"]` with a
  `huggingface_target`). The emitter loads the tenant secrets once, performs the
  push, and returns a per-destination `DestinationResult`. You do not see or handle
  the raw token — the emitter does, inside the tenant boundary.
- **Redaction is enforced.** The emitter wraps every destination through `_redact`,
  which replaces the literal token value with `<redacted>` in every error message,
  log line, and caller-facing result before it reaches you. Honor the same rule in
  anything you write to chat: never echo a token, a PAT, or an HF write key into the
  transcript. If you ever see a raw token in a result, treat it as a bug and redact
  it before reporting.
- After the push, report the resulting repo/space URL (that URL is real — it is
  returned by the host), but never the token.
- For a double-blind venue submission, do NOT push the de-anonymized source to a
  public repo; use the anonymized code-drop path (e.g. an anonymous code host) and
  keep the named GitHub/HF export for the public write-up only.

(For AI-provider API keys — a separate concern from publishing — the platform uses
the per-tenant `Credential` records in `api/credentials_service.py`; publishing does
not touch those, but the same principle holds: secrets are stored encrypted and
resolved server-side, never echoed.)

## Step 4 — Rock-Collection canonical-URL push (explicit stub, gated on #57)

The Rock Collection is the canonical home for published Rockie papers, with a
stable canonical URL per paper. **That backend does not exist yet (gated on issue
#57).** Therefore `/publish` exposes the canonical-URL push as an **explicit,
clearly labeled stub** and does **not** fabricate a URL.

When a user asks for the canonical Rock-Collection URL, return exactly this shape
(labeled, not a real URL):

```
[ROCK-COLLECTION PUSH — NOT YET WIRED]
The Rock Collection canonical-URL backend is not yet available (gated on #57).
The paper is published as a lab Note + downloadable artifact (above). A canonical
Rock-Collection URL will be minted here once #57 lands. No URL is fabricated.
```

Do not invent a `rockcollection.*` URL, a slug, or a DOI. A fabricated canonical
URL is worse than an honest stub: it breaks the moment someone clicks it and it
poisons any citation that copies it. When #57 lands, replace this stub with the
real push.

## The acceptance gate (re-checked at publish time)

Before the bundle is final, re-run the core acceptance check — **every numerical
claim maps to an evidence row** (the format auditor's stage-05 check) — because the
published artifact is what the world cites and a number can lose its evidence row in
late edits. Fail the publish on any unmatched number and route back to
`/paper-draft`.

## Summary of what /publish returns

- The lab Note for the paper (its id / link).
- The downloadable bundle artifact (signed URL).
- For an export: the real repo/space URL (token redacted).
- For Rock Collection: the labeled not-yet-wired stub (no fabricated URL).
