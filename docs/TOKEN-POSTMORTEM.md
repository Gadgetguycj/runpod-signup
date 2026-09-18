# Where the tokens went

The 420k figure is the agent's **final context size**, not what it consumed. Actual
consumption was far larger, and the cause is not the late changes themselves.

| Measure | Value |
|---|---|
| Assistant turns | 411 |
| Cache creation | 3,486,114 |
| Cache reads | 97,449,576 |
| Output | 253,099 |
| Effective input-equivalents (create x1.25 + read x0.1) | ~14,100,000 |
| Final context size (the "420k") | 420,620 |

Cost here is **turns multiplied by context**. The agent carried a context that grew to
420k and then took 411 turns, each of which re-read roughly 237k tokens. Neither number
alone is the problem; their product is.

## Per working block

| Block | Turns | Cache create | Cache read | Output |
|---|---|---|---|---|
| 1, initial build | 132 | 368,059 | 14,710,918 | 68,626 |
| 2, abuse protection and address fix | 201 | 1,933,673 | 53,540,364 | 132,657 |
| 3, page one rework and username fix | 78 | 1,184,382 | 29,198,294 | 51,816 |

## The real driver

I instructed the agent to prove every test by breaking the code, watching the named test
fail, restoring it, and re-running. It reported 84 such breaks. Each costs roughly four
to five turns: edit, run, observe, restore, re-run. That is about 350 to 400 turns, which
is essentially the entire turn count of 411. Every one of those turns re-read the full
context.

Proof discipline is the cost, and it is charged per turn against a large context.

## What the earlier analysis got wrong

A first pass attributed the total to a cache rewrite after a four hour idle gap, claiming
354,738 tokens and over 300,000 in savings. Cache creation across all 411 turns was
3,486,114, so one 354k rewrite is about 3 percent of effective cost. Idle gaps are not
the story.

## What to do differently

1. **Scale proof to risk, per change.** Falsifying every test was correct for the credit
   handout, where a bug gives away money, and disproportionate for deleting a sentence and
   restyling a heading. Rule: full red-green for money, secrets, auth, and concurrency.
   For copy and layout, one test plus a screenshot. Blocks 2 and 3 above would have
   dropped by roughly two thirds.
2. **One agent per round, scoped to the diff.** A fresh agent handed only the five late
   asks starts near zero context instead of 350k, so its turns cost a fraction each.
3. **Cap the break budget explicitly.** Say "falsify the three tests that guard the money
   path" rather than "every test", which the agent reasonably read as all of them.
4. **Batch the corrections.** The five late asks arrived across two rounds and were
   verified twice. One round would have removed a whole re-verification pass.
