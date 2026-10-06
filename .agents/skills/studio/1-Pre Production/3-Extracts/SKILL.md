---
name: studio-extracts
description: Run or resume Studio substage 1.3, shortlisting short-form video opportunities from every approved filming script before recording.
---

# 1.3 — Extract Shortlist

Read the Studio root skill at `.agents/skills/studio/SKILL.md` for project resolution, logs, completion manifests, and invalidation. Read `.agents/skills/studio/1-Pre Production/3-Extracts/EXCERPT-PREFERENCES.md` before selecting candidates, and apply its guidance. Run after substages 1.1 and 1.2 in workflow order. Use every approved Word filming script in `1-Pre Production/1-Script Review/Approved/`, plus extract ideas recorded during Script Review. Do not use unapproved drafts.

## Work and output

Create one Markdown file, `Short-Form Excerpt Shortlist.md`, directly in the project's OneDrive `1-Pre Production/3-Extracts/` folder. Use a top-level title and a heading for each candidate so the candidates appear in the editor's outline. This is a preproduction opportunity list, not a finished edit plan or a request for new filming lines. Consider TikTok, Instagram Reels, YouTube Shorts, and X. A shared vertical presentation is the starting point; choose passages for their story, not a platform's maximum duration. Aim for roughly 30–60 seconds, allowing up to 90 seconds when the setup is needed for a coherent payoff. These are editorial targets, not platform requirements.

For each promising, distinct opportunity, identify its review status (`Pending Review`, `Approved`, or `Rejected`), the approved source script and its version, a descriptive working title, Reach/Trust/Traffic aim, approximate duration, the core idea, and brief opening and payoff anchors traceable to the approved wording. New candidates start as `Pending Review`. Include a provisional spoken script assembled only from that approved source, in source order. Mark every omitted span between selected passages as a proposed jump. Every sentence in the provisional spoken script must be copied verbatim from the approved master script, preserving words and punctuation. Do not add connective narration, a spoken CTA, or an alternate ending. Explain why the opportunity could stand alone and flag any dependence on context, delivery, visuals, or a possible jump. Rank the strongest opportunities. Include representation from every approved script that yields a viable opportunity; record why a script yields none if applicable. The script is a paper proposal, not a footage-tested edit: do not claim timestamps or final cut points before footage exists. Label all opportunities conditional on the recorded take.

Keep an append-only `LOG.md` in the step folder. Record all approved sources reviewed, shortlisted and rejected or merged ideas, the output document, and the later footage check. If there are no viable opportunities, record that finding in the log instead of creating an empty shortlist.

## Completion and rerun

Success requires reviewing every approved source, checking that each opening and payoff anchor appears in its stated source, verifying every spoken-script sentence is verbatim from the approved master script and passages remain in source order, confirming no candidate has `Pending Review` status, and distinguishing the shortlist from a footage-tested cut. `Approved` and `Rejected` candidates are both resolved; any `Pending Review` candidate prevents completion. A documented no-extract result also satisfies the step. Write `DONE` only after verifying the output. Its manifest must fingerprint the full approved source set, this step skill, the excerpt preferences file, and the shortlist Markdown file; for a no-extract result, record the empty-output condition. Do not make footage verification a 1.3 completion gate.

On rerun, validate upstream markers and this step's `DONE` in workflow order. If an approved source changes, invalidate 1.3 and re-review the shortlist. Preserve existing files for inspection, account for human edits, and do not overwrite valid or edited work blindly. Append each attempt and any invalidation to `LOG.md`.
