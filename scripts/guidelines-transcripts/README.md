# Drop transcripts here (local only)

This directory is the default input for
[`scripts/condense_expert_principles.py`](../condense_expert_principles.py).

**Do not commit copyrighted transcript text.** Prefer `tmp/guidelines-transcripts/`
(under gitignored `tmp/`). If you use this folder, keep only notes you own.

## File format

```markdown
---
source: Jeff Nippard
title: Progressive Overload Explained
url: https://www.youtube.com/watch?v=…
date: 2024-06-01
---

Paste the official captions / transcript you own below…
```

Frontmatter is optional. Filenames sort lexicographically for stable condensation order.

See [`guidelines-corpus.md`](../guidelines-corpus.md).
