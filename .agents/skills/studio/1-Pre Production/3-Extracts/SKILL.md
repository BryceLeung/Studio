---
name: studio-extracts
description: Run or resume Studio substage 1.3, shortlisting short-form video opportunities from every approved filming script before recording.
---

# 1.3 — Extract Shortlist

Read the Studio root skill at `.agents/skills/studio/SKILL.md` for project resolution, logs, completion manifests, and invalidation. Read `.agents/skills/studio/1-Pre Production/3-Extracts/EXCERPT-PREFERENCES.md` before selecting candidates, and apply its guidance. Run after substages 1.1 and 1.2 in workflow order. Use every approved Word filming script in `1-Pre Production/1-Script Review/Approved/`, plus extract ideas recorded during Script Review. Do not use unapproved drafts.

## Work and output

After the shared prerequisite check passes and this stage is selected for execution, ensure the project's `1-Pre Production/3-Extracts/` folder exists and create its `APPROVED/` subfolder if missing, even before a shortlist is ready. On rerun, create a missing `APPROVED/` folder without changing an existing folder or its contents. Folder creation is setup only, not approval: never move the shortlist into it automatically, and do not create folders during read-only status or a rejected prerequisite check.

Read the approved three-column script through `studio/script_docx.py`: use `document_xml(path)`, `spoken_paragraphs(root)`, and `spoken_text(paragraph)` for canonical spoken text, or `script_rows(root)` to inspect line IDs and directions separately. Copy only the `Spoken Dialog` wording into extracts. IDs, `Stage Directions`, table headers, and review front matter are not spoken passages. Directions can inform editorial feasibility but do not authorize new dialogue. Do not flatten all table cells into script text. Line IDs may have been reassigned during stage 1.1; retain the approved source filename/version as provenance.

Use the maintained read-only CLI rather than a one-off DOCX extraction script:

```text
python "./studio/script_docx.py" "<approved-script.docx>" --format text
python "./studio/script_docx.py" "<approved-script.docx>" --format json
```

Text output is dialogue only; JSON includes the title and ordered rows with IDs, dialogue, and directions in separate fields. Neither command writes files.

Create one Markdown file, `Short-Form Excerpt Shortlist.md`, directly in the project's OneDrive `1-Pre Production/3-Extracts/` folder. Use a top-level title and a heading for each candidate so the candidates appear in the editor's outline. This is a preproduction opportunity list, not a finished edit plan or a request for new filming lines. Consider TikTok, Instagram Reels, YouTube Shorts, and X. A shared vertical presentation is the starting point; choose passages for their story, not a platform's maximum duration. Aim for roughly 30–60 seconds, allowing up to 90 seconds when the setup is needed for a coherent payoff. These are editorial targets, not platform requirements.

For each promising, distinct opportunity, identify its review status (`Pending Review`, `Approved`, or `Rejected`), the approved source script and its version, a descriptive working title, Reach/Trust/Traffic aim, approximate duration, the core idea, and brief opening and payoff anchors traceable to the approved wording. New candidates start as `Pending Review`. Include a provisional spoken script assembled only from that approved source, in source order. Mark every omitted span between selected passages as a proposed jump. Every sentence in the provisional spoken script must be copied verbatim from the approved master script, preserving words and punctuation. Do not add connective narration, a spoken CTA, or an alternate ending. Explain why the opportunity could stand alone and flag any dependence on context, delivery, visuals, or a possible jump. Rank the strongest opportunities. Include representation from every approved script that yields a viable opportunity; record why a script yields none if applicable. The script is a paper proposal, not a footage-tested edit: do not claim timestamps or final cut points before footage exists. Label all opportunities conditional on the recorded take.

Keep an append-only `LOG.md` in the step folder. Record all approved sources reviewed, shortlisted and rejected or merged ideas, the output document, and the later footage check. If there are no viable opportunities, document that finding in the shortlist Markdown as well as the log. It still requires approval; a missing document is not a successful no-extract result.

## Completion and rerun

Use the root skill's shared prerequisite check before work. After the human moves the Markdown into `APPROVED/`, invoke the shared completion recorder with `--stage 1.3`; it records the current source set and excerpt-preference revision. Do not write `DONE` by hand or record pending drafts.

Before handoff, review every approved source, check anchors and verbatim wording in source order, and distinguish the shortlist from a footage-tested cut. Human approval is accomplished by moving `Short-Form Excerpt Shortlist.md` into this step's `APPROVED/` folder. The shared validator checks that the approved Markdown exists and that its fingerprint and all recorded input fingerprints match `DONE`; it does not infer approval from candidate status labels. Keep candidate statuses for downstream extract selection. Write `DONE` only after the file is in `APPROVED/`, fingerprinting the full approved source set, this step skill, the excerpt preferences file, and the approved Markdown. A no-extract decision uses the same approved-document gate. Footage verification is not a completion gate. Downstream stages read only `APPROVED/Short-Form Excerpt Shortlist.md`.

On rerun, validate upstream markers and this step's `DONE` in workflow order. When 1.3 is selected for execution after a source change, use common graph invalidation and re-review the shortlist. Before deliberately replacing an existing root-level draft, select it with the common `prepare-regeneration` command so it is preserved in the pending archive slot. Do not move or overwrite the approved Markdown automatically; human approval remains a separate handoff. Account for human edits and append each attempt and invalidation to `LOG.md`.
