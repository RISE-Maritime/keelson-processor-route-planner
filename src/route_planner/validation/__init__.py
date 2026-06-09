"""Validation engine — RouteChallenge / RouteIssue + provenance (P7 — stub).

Runs after every generation and every manual edit. Hard checks emit ``RouteIssue`` (CRITICAL);
soft checks emit typed ``RouteChallenge``. Attaches provenance (chart source/edition, limitations,
assumptions, vessel profile, generation time) and an edited-after-validation flag. See
docs/DESIGN.md §9, §12 and docs/ROADMAP.md (P7). Not implemented in the P1 skeleton.
"""
