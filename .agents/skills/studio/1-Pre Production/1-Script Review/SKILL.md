---
name: studio-script-review
description: Run or resume Studio substage 1.1, planning script ideas interactively before writing Word candidates for human approval.
---

# 1.1 — Script Review

Read the Studio root skill at `.agents/skills/studio/SKILL.md` (relative to the repository root) for project resolution, logging, completion manifests, and invalidation. Use this shared step skill when running a project.

## Inputs and work

Default intake for a new idea is an interactive discussion. Read the source material and any supplied script, then propose a small asset slate with the distinct angle, Reach/Trust/Traffic objective, and whether each piece needs filming or can be cut from footage. Discuss the slate with the user and refine it from their feedback. A proposed extract remains conditional until footage confirms that the exact wording and a usable opening and payoff exist. A project may be created during this discussion, before the slate is settled.

For a pickup child project, start from the specific parent asset that failed or needs revision. Discuss the smallest replacement slate, such as one new filmed take for an unusable extract. Record the parent project, source asset and version, failure reason, and replacement decision in the child's log. Keep the parent's approved set intact; the child has its own review and approval for any new filming script.

Do not draft full new scripts in chat, create or copy script files into the project, or advance to candidate review merely because an idea is promising or the user asked to create, run, or plan a project. Wait until the user explicitly asks to write scripts or names the scripts to draft; that request authorizes writing the selected scripts without another confirmation. When its OneDrive `1-Pre Production/1-Script Review/` folder is created, immediately create `Approved/` inside it, even if no candidate exists yet. After the writing request, use the agreed sources and decisions to write each filming candidate as a Word `.docx` directly in the step folder with a descriptive filename and no production-action prefix. Every root-level `.docx` is pending review and blocks completion. Previous revisions and unfinished drafts may be stored in the optional `DRAFTS/` subfolder; they are not approved filming inputs and do not block completion. Only documents moved by the human into `Approved/` are filming inputs for later stages. Record extract ideas in the discussion and `LOG.md`, but do not create extract documents during this step. Substage 1.3 creates extract documents from the approved scripts for its own approved-Markdown handoff. Record the source, discussion decisions, filming candidates, and handoff in that folder's `LOG.md`.

## Writing Word candidates

Use the maintained `scripts/write-script.py` helper in this skill directory instead of creating a one-off Python generator. It requires Python 3.9+ and `python-docx` from `scripts/requirements.txt`; dependency installation remains subject to normal permission approval.

From the workflow repository root, run:

```text
python "./.agents/skills/studio/1-Pre Production/1-Script Review/scripts/write-script.py" --project-root "<real-project-root>" --filename "Descriptive Title.docx" --input "<draft.json>"
```

Use `scripts/draft-template.json` as the reusable review-notes and script template. Fill its fields in the JSON supplied to the helper; do not edit the shared blank template for each project. The UTF-8 JSON draft has this shape:

```json
{
  "title": "Descriptive Title",
  "review": {
    "objective": "Trust",
    "audience": "U.S. parents saving for their children",
    "ending_cta": "Decision-framework payoff; no spoken sales CTA",
    "factual_caveats": "State rules vary; recheck current tax rules before publication"
  },
  "sources": ["IRS guidance: https://www.irs.gov/"],
  "notes": ["Optional visual ideas, packaging notes, or conditional extract ideas. Not spoken."],
  "paragraphs": ["First spoken paragraph.", "Second spoken paragraph."]
}
```

Fill the review fields with the agreed objective (`Reach`, `Trust`, or `Traffic`), intended audience, ending/CTA decision, and factual caveats. Say explicitly when there is no CTA or no known caveat rather than assuming one. Add source references under `sources`; reserve `notes` for other nonspoken context. The helper always renders a standard review block and a separate sources section before the spoken script, calculates estimated runtime from spoken text at 140-160 words per minute, and marks missing review fields as `Not specified` and absent sources as `Not supplied` without inventing decisions or references. `review`, `sources`, and `notes` are optional inputs, but fill applicable fields during normal script review.

Each `paragraphs` entry becomes one Word paragraph; embedded newlines remain line breaks in the initial draft. Standalone square-bracketed paragraphs or lines such as `[Make a cute animation.]` are nonspoken stage directions and do not count toward runtime. Inline brackets inside dialogue remain spoken. Omit `--input` to supply the same JSON through standard input when supported, avoiding an intermediate draft file. If using a JSON file, keep it in a scratch directory, not as another filming candidate. Do not create project-specific Python generators.

The helper writes only a new `.docx` directly in the existing project's Script Review folder, refuses paths in `--filename`, and never overwrites an existing file. It puts the title first, all review notes before one exact `Spoken script` heading, and the dialogue/direction draft afterward. It verifies the paragraph sequence before publishing and reports spoken word count and estimated runtime. It does not approve scripts, append logs, invalidate stages, or record completion; follow the normal logging, regeneration, and human-review gates separately. Do not include the reserved standalone heading `Spoken script` in draft fields.

## Script layouts and revisions

For a conversion-only chat request such as `convert script to 3-col format "<script.docx>" [project]`, use the stage-specific [Convert Script to 3-Col Format](convert-script-to-3-col-format/SKILL.md) skill. It resolves the selected script and optional output, invokes the existing writer, and returns a draft for human review without running the full stage or recording completion.

Before human approval, convert the candidate to a three-column Word table using the same writer's conversion function through its thin CLI wrapper:

```text
python "./.agents/skills/studio/1-Pre Production/1-Script Review/scripts/convert-script.py" "<script.docx>" --to three-column
python "./.agents/skills/studio/1-Pre Production/1-Script Review/scripts/convert-script.py" "<script.v1.docx>" --to one-column --output "<review-copy.docx>"
```

The table headers are exactly `ID`, `Spoken Dialog`, and `Stage Directions`, occupying 7%, 65%, and 28% of usable page width. Each spoken paragraph gets a row. Both Enter and Shift+Enter split logical paragraphs; visual wrapping and page breaks do not. Whole bracketed paragraphs attach to the immediately preceding spoken paragraph; leading directions attach to the first following paragraph. Consecutive directions keep their order, blanks do not interrupt attachment, and inline bracketed phrases remain dialogue. Remove the outer square-bracket delimiters in the direction column, retaining inner brackets and text formatting; restore the outer brackets on one-column conversion. Earlier three-column drafts with bracketed directions remain readable and are normalized on conversion. A directions-only document can be converted without losing content but cannot be approved as a filming script.

IDs are sequential `L01`, `L02`, etc., expanding naturally to `L100`. Regenerate them in reading order on each three-column conversion; they are not persistent across revisions or the Line tool's sentence IDs. Manually added spoken paragraphs within a cell become separate rows on conversion, with that cell's directions attached to the last paragraph. One-column conversion omits IDs and emits dialogue followed by its bracketed directions. Leading directions may therefore move after the first dialogue paragraph; content and readable ordering, not exact original layout, are the round-trip contract.

Only the body after the `Spoken script` heading (matched case-insensitively, ignoring surrounding whitespace) is converted. Preserve front matter, styles, inline formatting, hyperlinks, and document resources by editing the loaded document rather than rebuilding it as plain text. Three-column normalization retains the source table style; one-column output materializes its basic inherited text formatting while respecting paragraph, character, and direct run styles. Conditional table text formatting cannot be flattened safely and fails explicitly; use paragraph/run styles for that formatting. Unknown tables, content-controlled rows/cells, merged script cells, mixed table/paragraph bodies, and section breaks within the script body also fail rather than dropping content. Word comment selections are compared before and after rearrangement, excluding generated IDs and headers. A selection that would change or become invalid fails; reanchor that comment to the spoken paragraph before conversion. Use supported Word paragraphs and the script table for the body.

Without `--output`, save beside the source as `<script>.v<N>.docx`, choosing a number above the source version and all existing sibling versions. An explicit bare filename is also relative to the source folder; an explicit path chooses another existing directory. Never overwrite the source, an existing output, or a symlink. Keep candidates awaiting review in the stage root and retained drafts in `DRAFTS/`, never generate into `Approved/`; only the human approves a chosen three-column revision. Conversion does not update completion or logs. Before completing the stage, move retained historical versions out of the root into `DRAFTS/` rather than deleting them; do not move existing project files without authorization.

Downstream helpers share `studio/script_docx.py` to read dialogue separately from IDs and directions. Cue cards, spoken word counts, script matching, and extract dialogue must exclude IDs, table headers, and stage directions. Directions remain available as production context through structured script rows.

## Endings and CTA pickups

Decide the ending for each proposed piece during script review. If a new spoken end-of-film CTA or alternate ending must be recorded, account for it as filmed material before the shoot. Include it in the main filming script when it is inseparable from that piece; use a separate `CTA Pickup - <destination>.docx` in the step folder when it is an optional or reusable ending. State which piece and destination it serves, the exact approved words, and where the pickup attaches. It receives human approval and a cue card like any other filming script. Treat an ending as an extract only when its exact words are already present in the flagship footage. A link, caption, end card, or other nonspoken CTA can be recorded as a packaging note in the relevant script and needs no filming pickup.

Choose CTAs by the piece's Reach, Trust, or Traffic objective. An emotional Reach ending may simply end on its payoff; do not add a traffic CTA by default. Keep alternate CTA pickups separate from the core ending so the edit can choose the right version for each destination.

## Approval and completion

`Approved/` is the human approval mechanism for filming scripts: Kristy or Bryce edits a root-level candidate in Word and moves the chosen three-column revision there to approve it. Keep the existing `Approved/` folder spelling; no folder rename is required. All active scripts must be approved and no `.docx` may remain directly in the stage root before completion. Retain historical revisions or unfinished drafts in `DRAFTS/`; its contents are excluded from approval inventory and completion fingerprints and do not need to be approved. Root `LOG.md`, other non-DOCX notes, and subfolders do not block completion. If `Approved/` has no `.docx`, remain incomplete and continue planning or hand off the requested candidates for review. Extract ideas neither require approval nor keep this step open; an extract that fails in editing can be dropped.

When no `.docx` remains directly in the stage root, at least one `.docx` exists in `Approved/`, and every approved document has a valid three-column script body, sequential IDs, and spoken dialogue, invoke the root skill's shared completion recorder with `--stage 1.1`. Do not write `DONE` by hand. The recorder enforces the empty-root condition and fingerprints the complete approved inventory, not `DRAFTS/`. A root candidate, one-column approved document, or malformed approved document blocks completion with a diagnostic; never silently move, convert, or edit a file to make the gate pass. Extract ideas in the log are outside this approval gate; substage 1.3 has its own approved-Markdown gate.

## Rerun

Validate the existing `DONE` as described in the root skill. Preserve approved documents and human edits. Resume from the current candidate and approval state; do not recreate candidates that remain valid or overwrite edited documents. Append each attempt, handoff, or invalidation to `LOG.md`.

Prefer a new versioned draft and retain earlier revisions in `DRAFTS/` with authorization. If the user explicitly authorizes replacing an existing root-level candidate, archive that exact draft with the common regeneration helper before replacing it. Approved scripts are protected and are never swept into archives by generation. Marker invalidation uses the shared dependency graph and archives `DONE`, not the approved documents. Adding a root `.docx` makes the empty-root condition fail on validation; changing `DRAFTS/` alone does not invalidate completed evidence. Never delete or silently relocate pending candidates merely to report completion.
