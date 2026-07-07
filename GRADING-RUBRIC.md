# Sample Grading Rubric

Sample quality is the pipeline's single biggest reply-rate lever — a great cold
email pointing at a mediocre sample gets ignored. So the first ~30 samples are
hand-graded before the generator is trusted to run unattended.

**The bar for every factor:** *"Would I, as this business owner, be impressed
seeing my business rendered this way?"* If not, the hook dies.

Grade each factor **1-5**, plus an **overall gut /10** kept separate on purpose —
sometimes the parts score well but the whole doesn't land, and that gap is itself
a signal worth catching.

## The seven factors (A-G)

| # | Factor | 1 = kill it | 3 = fine, forgettable | 5 = proud to send | Tuning lever |
|---|--------|-------------|-----------------------|-------------------|--------------|
| A | **Hero / first 3 sec** | Generic or off-putting | Fine but forgettable | Stops me, makes me scroll | prompt HERO block |
| B | **Design craft** (premium vs basic) | 2010 template | Clean but plain | "A designer made this" | prompt CRAFT block + `RUBRIC` |
| C | Layout & variety | Monotonous centered stack | Some variety | Varied, rhythmic sections | archetypes + `RUBRIC` |
| D | Imagery use | Broken / ugly / misplaced | Photos just sit there | Photos elevate the page | image ladder (`select_images`) |
| E | Copy / voice | Robotic filler | Generic but fine | Sounds written for *this* business | prompt design direction |
| F | Accuracy / trust *(owner's eye)* | I'd be embarrassed / it overclaims | Mostly fine | All true and flattering | honesty guardrails |
| G | Beats their current site | No better than what they have | Somewhat better | Night and day | prompt use of `qualify_reason` |

Two free-text notes are worth more than the numbers — always fill them:

- **CHANGE FIRST** — the one thing you'd fix before this goes out.
- **KEEP** — parts worth keeping/stealing even if the overall grade is low.

## How the grade feeds back

The seven factors mirror the machine `critique()` rubric in `build_sample.py`.
When a factor scores low across several samples, that rubric line gets hardened
so the AI self-critique starts catching it before a human ever sees it. The goal
is for `grade_averages` to climb across the first 30 and for the machine critique
to converge with the human grades.

## Logging a grade

One command per graded sample (records the grade against the exact build that
was graded — the slug changes on every rebuild):

```bash
# run.py grade <lead_id> <ABCDEFG> <overall> [note]
#   ABCDEFG = 7 digits, each 1-5, in factor order
sudo -u weblo .venv/bin/python run.py grade 18 5443454 7 \
  "CHANGE: kill hero rating badge | KEEP: real photos, headline"
```

It prints the recorded grade plus the running per-factor average across all
graded samples, so calibration progress is visible after each one.

## Also grade the email copy

The cold email is the other reply-rate lever. `run.py draft [N]` writes and
prints each email without sending. Copy bar: opens with one specific TRUE detail
about the business, reads like a real person, one link, under ~110 words, no spam
tells.
