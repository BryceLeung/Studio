---
name: studio-script-review
description: Run or resume Studio substage 1.1, planning script ideas interactively before writing Word candidates for human approval.
---

# 1.1 — Script Review

Read the Studio root skill at `.agents/skills/studio/SKILL.md` (relative to the repository root) for project resolution, logging, completion manifests, and invalidation. Use this shared step skill when running a project.

## Inputs and work

Default intake for a new idea is an interactive discussion. Read the source material and any supplied script, then propose a small asset slate with the distinct angle, Reach/Trust/Traffic objective, and whether each piece needs filming or can be cut from footage. Discuss the slate with the user and refine it from their feedback. A proposed extract remains conditional until footage confirms that the exact wording and a usable opening and payoff exist. A project may be created during this discussion, before the slate is settled.

For a pickup child project, start from the specific parent asset that failed or needs revision. Discuss the smallest replacement slate, such as one new filmed take for an unusable extract. Record the parent project, source asset and version, failure reason, and replacement decision in the child's log. Keep the parent's approved set intact; the child has its own review and approval for any new filming script.

Do not draft full new scripts in chat, create or copy script files into the project, or advance to candidate review merely because an idea is promising or the user asked to create, run, or plan a project. Wait until the user explicitly asks to write scripts or names the scripts to draft; that request authorizes writing the selected scripts without another confirmation. When its OneDrive `1-Pre Production/1-Script Review/` folder is created, immediately create `Approved/` inside it, even if no candidate exists yet. After the writing request, use the agreed sources and decisions to write each filming candidate as a Word `.docx` directly in the step folder with a descriptive filename and no production-action prefix. Every `.docx` directly in the step folder is a script to be filmed. Record extract ideas in the discussion and `LOG.md`, but do not create extract documents during this step. Substage 1.3 creates extract documents from the approved scripts for its own approved-Markdown handoff. Record the source, discussion decisions, filming candidates, and handoff in that folder's `LOG.md`.

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

Each `paragraphs` entry becomes one spoken Word paragraph; embedded newlines remain line breaks. Omit `--input` to supply the same JSON through standard input when supported, avoiding an intermediate draft file. If using a JSON file, keep it in a scratch directory, not as another filming candidate. Do not create project-specific Python generators.

The helper writes only a new `.docx` directly in the existing project's Script Review folder, refuses paths in `--filename`, and never overwrites an existing file. It puts the title first, all nonspoken notes before one exact `Spoken script` heading, and only spoken paragraphs afterward for downstream cue-card and script readers. It verifies the paragraph sequence before publishing and reports spoken word count and estimated runtime. It does not approve scripts, append logs, invalidate stages, or record completion; follow the normal logging, regeneration, and human-review gates separately. Do not include the reserved standalone heading `Spoken script` in draft fields.

## Endings and CTA pickups

Decide the ending for each proposed piece during script review. If a new spoken end-of-film CTA or alternate ending must be recorded, account for it as filmed material before the shoot. Include it in the main filming script when it is inseparable from that piece; use a separate `CTA Pickup - <destination>.docx` in the step folder when it is an optional or reusable ending. State which piece and destination it serves, the exact approved words, and where the pickup attaches. It receives human approval and a cue card like any other filming script. Treat an ending as an extract only when its exact words are already present in the flagship footage. A link, caption, end card, or other nonspoken CTA can be recorded as a packaging note in the relevant script and needs no filming pickup.

Choose CTAs by the piece's Reach, Trust, or Traffic objective. An emotional Reach ending may simply end on its payoff; do not add a traffic CTA by default. Keep alternate CTA pickups separate from the core ending so the edit can choose the right version for each destination.

## Approval and completion

`Approved/` is the human approval mechanism for filming scripts: Kristy or Bryce edits a root-level candidate in Word and moves it there to approve it; deleting a candidate rejects it. Pause for review while any candidate `.docx` remains directly in the step folder. If both the step folder and `Approved/` contain no `.docx`, remain incomplete and continue the interactive plan until the user asks for scripts. Extract ideas neither require approval nor keep this step open; an extract that fails in editing can be dropped.

When no candidate `.docx` remains directly in the step folder and at least one `.docx` exists in `Approved/`, invoke the root skill's shared completion recorder with `--stage 1.1`. Do not write `DONE` by hand. The recorder validates the approved inventory, input/output fingerprints, and empty-candidate condition before recording completion. Extract ideas in the log are outside this approval gate; substage 1.3 has its own approved-Markdown gate.

## Rerun

Validate the existing `DONE` as described in the root skill. Preserve approved documents and human edits. Resume from the current candidate and approval state; do not recreate candidates that remain valid or overwrite edited documents. Append each attempt, handoff, or invalidation to `LOG.md`.

If the user explicitly authorizes rewriting an existing root-level candidate, archive that exact draft with the common regeneration helper before replacing it. Approved scripts are protected and are never swept into archives by generation. Marker invalidation uses the shared dependency graph and archives `DONE`, not the approved documents.
