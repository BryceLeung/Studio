# Initial Find Takes benchmark

Reference: the human Line review of What Is Freedom For, C01. The other two recordings have no human mapping reference yet.

- Exact script/transcript word ranges: 23 of 28 reference takes; 27 predicted takes.
- Take precision: 85.2%; recall: 82.1%; F1: 83.6%.
- Word assignments (script grouping or discard): 418 / 426 (98.1%).
- Quality labels agree on 23 / 23 exact-range matches.

## Missing or different reference takes

- Script words 0–19, transcript words 15–34: For most of my adult life, freedom meant that no one could tell me what to do with my time.
- Script words 167–177, transcript words 184–186: During his final...
- Script words 167–177, transcript words 187–197: During his final month of life, I was nine months pregnant.
- Script words 353–364, transcript words 397–408: Money gave me the freedom to choose how to spend my life.
- Script words 365–379, transcript words 409–423: My father-in-law helped me see why I might choose to give some of it away.

## Extra or different predictions

- Script words 0–19, transcript words 8–34: What is freedom for? Whenever you're ready. For most of my adult life, freedom meant that no one could tell me what to do with my time.
- Script words 167–177, transcript words 184–197: During his final... During his final month of life, I was nine months pregnant.
- Script words 353–364, transcript words 397–407: Money gave me the freedom to choose how to spend my
- Script words 365–379, transcript words 408–423: life. My father-in-law helped me see why I might choose to give some of it away.

The settings were fixed before this first comparison. This is a baseline, with no tuning to this reference. Scores are text similarity measures, not probabilities.
