# ACTIVE NICHE: DENTISTS

**The pipeline currently targets DENTISTS — and only dentists.**
Pivoted from med spas on 2026-07-19; all remaining med-spa artifacts retired 2026-07-22.

This file is the single source of truth for "what are we building for right now." Before
you build a sample, find leads, or tune prompts, assume **DENTAL** unless the line above
changes. **Do not build med-spa / cosmetic / aesthetic / "MedSpa" businesses.**

## What this means in the code
- `.env` → `CAMPAIGN_NICHE=dentist` (drives `find_leads.NICHE_QUERY_VARIANTS` + routing).
- `build_sample.py` → the `dental` brief in `NICHE_BRIEFS` fires: smile-hero logic, warm
  reception / genuine-smile imagery, and the clinical "surgery look" (masks, gloves,
  drills) scored as a POOR hero.
- `leads.db` → every non-dental lead has been set to `SKIP`. The build queue is dentists only.

## If the niche ever changes again, do these in order
1. Update the top line of THIS file first.
2. Set `CAMPAIGN_NICHE` in `.env`.
3. Add/confirm a matching brief in `build_sample.NICHE_BRIEFS` and query variants in
   `find_leads.NICHE_QUERY_VARIANTS`.
4. Re-`SKIP` the old-niche leads (or re-run `find_leads`) so the queue matches.
