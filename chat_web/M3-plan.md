# M3 implementation directive

- Source: <https://chatgpt.com/share/6a81f8a0-39e0-83ec-9a96-e752c1d84802>
- Captured: 2026-08-17
- Role: frozen local implementation input for M3.

## Required scope

| Task | Required behavior | Frozen v0 parameters | Confidence | Need review |
|---|---|---|---:|---|
| Separate liquidity and structure lifecycle | A wick taking liquidity must not remove a swing's structural value. | Liquidity: `ACTIVE -> TAKEN`; structure: `ACTIVE -> BROKEN/SUPERSEDED`. | 98% | No |
| Cross-timeframe interaction | A lower-timeframe bar may raid a higher-timeframe reference, but that close is not a higher-timeframe BOS/CHoCH. | Record detection and reference timeframes; only same-timeframe breaks are structurally eligible. | 95% | Yes |
| Swing hierarchy | Preserve STH/STL and promote to ITH/ITL then LTH/LTL append-only. | Do not hard-filter smaller swings. | 90% | Yes |
| Raid episode | Avoid duplicate setups from the same liquidity event/reference across nearby observations. | Group the evidence around one reference into one episode. | 90% | Yes |
| Sweep/reclaim | Same-bar reclaim is canonical; multi-bar reclaim remains visible. | Reclaim within at most 3 detection-timeframe bars; log later reclaim without promotion. | 80% | Yes, after replay |
| Shift candidate | After a raid, require a direction-matching close through same-timeframe confirmed structure. | Preserve every broken swing; do not hard-classify BOS/CHoCH. | 90% | Yes |
| Raid-to-shift window | Do not link temporally remote events. | M5: 12 bars; M15: 8 bars; H1: 4 bars. Late breaks remain logged. | 78% | Yes, after M4 |
| Displacement evidence | Preserve raw measurements and criterion flags. | Body/wick/ATR/median/close-location evidence remains permissive. | 95% | Not immediately |
| FVG linkage | Do not attach an arbitrary later FVG. | FVG must belong to the repricing/shift sequence; repricing is the break bar or next bar. | 90% | Yes |
| FVG reaction | Touch alone is insufficient for readiness. | Record touch, penetration fraction and favorable close outside; promote only on favorable reaction close. | 82% | Yes |
| Expiry | An unmitigated entry opportunity eventually expires. | Count from FVG availability: M5=24, M15=16, H1=6 bars. | 80% | Yes, after M4 |
| Invalidation | The thesis dies when setup-timeframe price truly breaks the raid extreme. | One setup-timeframe close beyond the extreme; zero buffer. | 82% | Yes, after M4 |
| Research logging | Preserve near misses and failed transitions. | Append-only raw evidence, thresholds and reason codes. | 98% | No |
| READY_FOR_LLM | Produce a traceable setup payload. | Include raid, broken swing(s), displacement, linked FVG, reaction and available targets/context. | 95% | Planner review after dev |

## Clock and data policy

```text
Exness timestamps and candles = UTC
D1/H4 = Exness candles
PDH/PDL v0 = previous Exness D1 high/low

New York clock = sessions, killzones and ICT True Day Open 00:00 NY
```

M3 must not invent another broker-day or NY-day abstraction.

## Definition of Done

```text
H1/M15 liquidity reference
-> M5/M15 raid/reclaim
-> structural shift candidate
-> displacement evidence
-> linked FVG
-> retrace/reaction
-> READY_FOR_LLM

or

-> INVALIDATED / EXPIRED
```

Every transition must trace back to raw candles, facts and candidates. Evidence
that is not promoted must remain inspectable.
