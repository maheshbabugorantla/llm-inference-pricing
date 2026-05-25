# Open Questions

Questions that arose during implementation and need resolution before the next relevant milestone.

---

## Q1 — BenchmarkPoint TPS fields are FloatField, violating the Decimal-for-rates invariant

**Raised during:** M12 (migration 0016 — expand_current_cost_cells)

**Gap:** `catalog.BenchmarkPoint.prefill_tps_aggregate` and `decode_tps_aggregate` are
`FloatField` (float8 in Postgres). The materialized view casts them to `::numeric` before
using them as divisors in cost math, but the cast preserves whatever floating-point
imprecision already exists in the stored value. `CLAUDE.md` states: "Don't use float for
prices, hourly rates, or token counts in cost math. Decimal only."

**Options considered:**

1. **Change to `DecimalField(max_digits=12, decimal_places=2)`** — requires a catalog
   migration that alters the live column type, updates benchmark seeds/fixtures, and
   adjusts any scraper code that writes float literals. Clean solution; fully consistent
   with the invariant.

2. **Keep `FloatField`, document the exception** — TPS figures come from benchmark runners
   that produce float outputs; converting to Decimal on write introduces a rounding
   decision that may be arbitrary. The `::numeric` cast in the view already pins the
   division result to `numeric(12,4)`. Imprecision in the divisor is bounded by the
   precision of the benchmark itself (not amplified by the cost formula).

**Preferred if forced:** Option 1 — migrate to `DecimalField` in the next catalog-touching
milestone. The rounding strategy is `decimal_places=2` (matches `decode_tps_aggregate` and
`prefill_tps_aggregate` in the `CurrentCostCell` unmanaged model, which already uses 2dp).

**Status:** Deferred to the milestone that next touches `catalog.BenchmarkPoint` schema.
