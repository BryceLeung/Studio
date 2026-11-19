---
name: draw-keyframes
description: When the user says "draw keyframes", turn the recent discussion into a paste-ready ChatGPT app image prompt for 2x3 keyframe sheets, with each pose labelled below using the current project's filenames. Prompt only; does not generate images.
---

# Draw Keyframes

Use this skill when "draw keyframes" is a request to act, not when the phrase is quoted while discussing the skill. Use the active project and shot or asset from the recent conversation; an explicit selection or correction in the current message takes precedence. This is a prompt-writing workflow, not a Studio run, sketch, render, or import. Read relevant project references as needed, but do not call image generation, run Remotion, or modify project files.

## Identify the drawings

1. Pull the intended key poses or visual states from the latest discussion. Preserve their order and agreed subject, action, camera, framing, direction, eyeline, expression, and continuity. Use a short, concrete visual description per pose: what moves, where it goes, and what stays fixed. Do not revive alternatives the user ruled out.
2. Separate drawable states from editorial instructions. A hold on an existing pose does not need another drawing. A cut, empty frame, or separately composited layer does not get a character panel unless the user wants artwork for it. Include alternate mouths, smears, or other variations only if they are part of the requested set. Ask a focused question only if recent context and project references do not resolve a consequential ambiguity.
3. Check nearby reference or pose files in the active project for its naming pattern. Give each drawing a unique, descriptive filename using the local convention for case, separators, prefixes, numbering, and extension. Use an existing exact name only when the user intends to reuse that named drawing; do not accidentally collide with an existing file. If no pattern exists, use `<subject>_<action>_<pose>.png` in lowercase snake case. For example, the Trump Accounts pose `trump_girl_laugh_head_back.png` follows subject, action, and pose.

## Build the ChatGPT prompt

- Return a single copy-ready text block addressed to ChatGPT's image generator. Instruct it to draw **one separate sheet image per batch**, each with **2 columns and 3 rows** of equal-size panels, read left to right and top to bottom. If there are more than six drawings, enumerate additional sheets. For unused slots in the final sheet, leave the panel blank with no filename; never invent filler drawings or repeat poses.
- List each sheet's slots in reading order with the exact filename and a visual description. Direct ChatGPT to print the filename verbatim, including its extension, in a readable label strip **directly underneath** that drawing. Keep labels outside the artwork, leave gutters for splitting panels, and forbid extra text, title banners, or watermarks.
- Include this instruction verbatim in every image prompt: "Export every sheet as a PNG with a real alpha channel. In every panel, pixels outside the intended artwork must be fully transparent (alpha 0), including the corners, gutters, and unused panels. Do not fill the background with a color, gradient, texture, or drawn checkerboard. Filename labels may sit on their own strip below the artwork, outside the crop."
- State the shared visual rules once, before listing the poses. When drawing isolated character sprites, preserve the same design, outfit, viewpoint, scale, and registration anchor across cells unless a pose intentionally changes them. Use the agreed crop and leave room for hair, arms, and motion within each cell. If the character is to be composited separately, omit other characters, scenery, and separate props from the drawing.
- If references are needed, briefly identify the exact images to attach outside the copy-ready block. Inside it, refer to them as attached references by name or role. Do not assume the ChatGPT app can see the workspace, or claim anything has been uploaded, drawn, exported, or saved. The filenames are labels for the future split assets, not a claim that ChatGPT exports individual files.
