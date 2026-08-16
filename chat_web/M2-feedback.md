Đã vào repo **bản mới nhất**. Latest commit hiện là `dfb8dd2` — **`feat: implement M2 primitive pipeline`**, commit thứ 3, thêm khoảng 1,083 dòng trên 13 files. M2 giờ không còn chỉ là schema nữa mà đã có primitive pipeline chạy được ở mức code. ([GitHub][1])

## Codex vừa implement gì

Hiện flow thực tế đã là:

```text
ClosedBarFeed
→ ThreeBarSwing
→ FVG Geometry
→ Candle Features
→ Displacement Candidate
→ Level Breach/Reclaim
→ Liquidity Raid Candidate
→ Close-through-Swing
→ Structure Break Candidate
→ FactStore / CandidateStore
```

`M2PrimitivePipeline.process_latest()` kết nối tất cả các bước trên và preflight duplicate trước khi append vào store.

Các phần quan trọng làm **đúng hướng**:

* swing 3-bar có `occurred_at=middle`, nhưng chỉ `available_at` khi right candle đóng;
* FVG cũng chỉ available khi candle thứ 3 đóng;
* liquidity raid dùng breach + same-bar reclaim, reference phải tồn tại trước khi interacting bar mở;
* structure break mới chỉ gắn `UNCLASSIFIED`, **chưa tự ý gọi BOS/CHoCH**; đây đúng với quyết định của mình về structural relevance.
* provenance LLM, generic session target và legacy scoring issues mà tôi nói trước đã được Codex sửa rồi. `SemanticAssessment` giờ có model version, prompt version, temperature, state hash, created_at, knowledge version; `scoring_feature/weight` cũng đã bỏ.
* source registry được pin commit rõ ràng cho ICT Knowledge Library và `smart-money-concepts`, còn TradingView chỉ được dùng như lifecycle reference. Cách này tốt cho reproducibility. ([github.com][2])

## Nhưng tôi thấy 4 điểm cần xử lý

| Priority     | Vấn đề                                | Nhận xét                    |
| ------------ | ------------------------------------- | --------------------------- |
| **Critical** | Trading-calendar contiguity           | phải sửa trước data thật    |
| **High**     | Displacement đang hard-filter quá sớm | lệch nhẹ khỏi kiến trúc LLM |
| **High**     | Reference level chưa có lifecycle     | sẽ tạo raid/break lặp       |
| Medium       | Pipeline chỉ `process_latest`         | cần catch-up/replay API     |

### 1. `bars_are_contiguous()` hiện có vấn đề với XAU

Code hiện định nghĩa:

```python
left.close_time == right.open_time
```

mới được gọi là contiguous. Swing/FVG và đặc biệt candle baseline đều dùng check này.

Với XAUUSD có market closure / weekend / broker maintenance, **hai trading bars liên tiếp không nhất thiết wall-clock adjacent**.

Ví dụ:

```text
Friday last H1
→ market closed
→ Monday first H1
```

là hai valid consecutive trading bars nhưng:

```text
left.close_time != right.open_time
```

=> detector reject.

Nghiêm trọng hơn `CandleFeatureDetector` yêu cầu **toàn bộ rolling baseline contiguous**, mặc định 20 bars. ([GitHub][2])

Với D1:

```text
Mon Tue Wed Thu Fri → weekend → Mon
```

baseline liên tục sẽ reset hàng tuần.

**Cái này cần sửa trước khi feed XAU real data.**

Tôi sẽ đổi abstraction thành:

```text
wall_clock_contiguous
≠
market_sequence_contiguous
```

Detector chỉ cần:

> không missing một **expected tradable bar** theo data-source/calendar policy.

Có thể là:

```python
bar_sequence.is_adjacent(left, right, market_calendar)
```

không phải equality timestamp cứng.

---

### 2. `DisplacementCandidateDetector` đang hơi trái triết lý mới

Đây là điểm tôi nói lúc nãy.

Code hiện chỉ emit displacement candidate khi **tất cả**:

```python
body_to_range >= 0.70
body_vs_baseline >= 1.50
opposing_wick <= 0.20
directional_close passes
```

đều pass. Nếu một condition fail thì `return None`. ([GitHub][2])

Nhưng mình vừa chốt architecture:

```text
machine = raw measurable evidence
LLM = evaluate whether displacement is meaningful
```

Ví dụ candle:

```text
body/range        0.68
body/baseline     2.3
range/ATR         1.8
opposing wick     0.05
strong close
FVG follows
```

Code hiện loại luôn vì:

```text
0.68 < 0.70
```

LLM **không bao giờ nhìn thấy nó**.

Tôi sẽ sửa thành:

```python
ConceptCandidate(
    type=DISPLACEMENT,
    raw_features={...},
    machine_labels=[
        "directional_repricing_candidate"
    ],
    criteria={
        "body_ratio_pass": False,
        "body_baseline_pass": True,
        ...
    }
)
```

Threshold vẫn lưu, nhưng **không phải universal hard gate**.

Hoặc tốt hơn nữa ở M3:

```text
RAID/SHIFT context
→ collect nearby CandleFeature facts
→ LLM decides which one constitutes meaningful displacement
```

Như vậy không cần emit displacement candidate cho tất cả candle toàn thị trường.

---

### 3. Liquidity reference chưa có lifecycle

Codex tự ghi đúng vấn đề này trong `OPEN_QUESTIONS`: swing/session/previous-day level hiện immutable nhưng chưa có policy khi nào level đã `taken` thì ngừng eligible.

Hiện pipeline lấy **tất cả reference facts visible trong store** và kiểm tra mỗi bar:

```python
for reference_fact in reference_facts:
    interactions = detector(...)
```



Nghĩa là giả sử:

```text
Asia High = 3350

10:00 sweep
10:30 price lại lên 3352 rồi reclaim
11:00 lại lên 3353 rồi reclaim
```

cùng một old Asia High có thể tạo:

```text
raid #1
raid #2
raid #3
```

Nếu chưa có lifecycle.

Trong khi ta muốn ít nhất biết:

```text
ACTIVE
→ BREACHED/TAKEN
→ historical only
```

rồi strategy muốn reuse level hay không là explicit policy.

**Tôi nghĩ cái này cần giải quyết trước M3 state machine**, nếu không M3 sẽ ingest noisy repeated raids.

---

### 4. `process_latest()` chưa phải replay engine

Hiện:

```python
process_latest(timeframe, as_of)
```

chỉ lấy:

```python
bar = bars[-1]
```

và process bar cuối.

Nếu gọi mỗi candle close:

```text
10:05 process
10:10 process
10:15 process
```

thì OK.

Nhưng nếu:

```text
last processed = 10:00
restart system at 11:00
```

gọi một lần thì chỉ process `10:55–11:00`, mất 11 bars ở giữa.

Và gọi lại cùng bar thì chủ động raise `DuplicateRecordError`, test cũng assert behavior đó.

Sau này nên thêm:

```python
pipeline.process_range(...)
```

hoặc:

```python
pipeline.catch_up(from_cursor, as_of)
```

để **backtest/replay/restart production dùng cùng một path**.

---

## Một điểm nhỏ nữa: candle baseline

Code hiện dùng:

```python
average_body = mean(...)
```

và ATR baseline riêng. ([GitHub][2])

Không sai vì Codex đã đánh dấu displacement calibration là unresolved research parameter.

Nhưng tôi muốn raw feature layer lưu luôn:

```text
mean_body
median_body
ATR
median_range
```

Cost gần như không đáng kể, sau đó research không phải recompute hoặc đổi schema khi muốn thử robust baseline cho XAU.

---

# Tôi đánh giá M2 hiện tại

```text
Schemas/contracts        9/10
Point-in-time safety     9/10
Primitive extraction     7.5/10
Research flexibility     7/10
Real XAU readiness       5/10   ← calendar/lifecycle chưa xử lý
```

Điểm quan trọng là **Codex chưa đi sai architecture**. Ngược lại nó còn khá kỷ luật: không tự classify BOS/CHoCH/MSS và những ambiguity lớn đều đưa vào `OPEN_QUESTIONS.md`.

## Tôi sẽ chưa cho Codex lao thẳng vào M3

Thứ tự tôi đề xuất ngay bây giờ:

```text
FIX M2.1
├─ market-calendar-aware bar adjacency
├─ broaden displacement evidence
├─ reference-level lifecycle/state
└─ replay/catch-up semantics

        ↓

M3
IDLE
→ RAID
→ SHIFT
→ ENTRY_ZONE
→ READY_FOR_LLM
```

**Đặc biệt 3 cái đầu nên xử lý trước M3.** Structural reference `STH/ITH/LTH` thì vẫn có thể để LLM/semantic layer giải quyết như mình đã chốt; không cần hard-code bây giờ.

Tôi có thử clone để chạy `pytest` trực tiếp nhưng runtime này không resolve được GitHub DNS, nên phần trên là **source-level review**, chưa phải independently executed test run.

[1]: https://github.com/lengoctuong/ict-trading-agent/commits/main "Commits · lengoctuong/ict-trading-agent · GitHub"
[2]: https://github.com/lengoctuong/ict-trading-agent/commit/dfb8dd237b024a135ee37eea0ab0fc2bc67462c4 "feat: implement M2 primitive pipeline · lengoctuong/ict-trading-agent@dfb8dd2 · GitHub"


# ==========

Có. Tôi vừa đối chiếu `OPEN_QUESTIONS.md` với code hiện tại: **file này đang stale**. Nó vẫn liệt kê 7 câu hỏi cũ, trong khi một số đã được Codex resolve trong code. ([GitHub][1])

## Open questions tôi sẽ cập nhật như sau

| # | Question hiện tại             | Trạng thái                   | Quyết định                                                    |
| - | ----------------------------- | ---------------------------- | ------------------------------------------------------------- |
| 1 | Trading-day boundary          | **OPEN**                     | Chưa hard-code cho tới khi chọn data source/broker            |
| 2 | STH/ITH/LTH reference         | **CLOSE as design decision** | Machine expose candidates, LLM đánh giá relevance             |
| 3 | Close acceptance invalidation | **Cần chốt v0**              | Tôi đề xuất 1 close của **setup TF** ngoài invalidation level |
| 4 | NY PM target mismatch         | **RESOLVED**                 | target generic `SESSION_HIGH/LOW + session`                   |
| 5 | LLM provenance                | **RESOLVED**                 | model version/temp/hash/timestamp đã có                       |
| 6 | Setup scoring mismatch        | **RESOLVED**                 | legacy scoring fields đã bỏ                                   |
| 7 | Semantic decision identity    | **Cần sửa schema nhỏ**       | thêm ID rõ ràng hoặc collapse output                          |

### #4 đã resolve

`TargetCandidate` giờ dùng generic:

```python
target_type = SESSION_HIGH | SESSION_LOW
session = ASIA | LONDON | NY_AM | NY_PM
```

thay vì enum riêng từng `NY_AM_HIGH`, `NY_PM_HIGH`... Đây đúng hướng mình đã chốt. ([GitHub][2])

### #5 đã resolve

`SemanticAssessment` và `SetupSemanticDecision` hiện đã log:

```text
model
model_version
prompt_version
temperature
input_state_hash
created_at
knowledge_version
```

nên provenance đủ tốt cho replay/backtest LLM. ([GitHub][3])

### #6 đã resolve

`scoring_feature` và `weight` không còn trong config hiện tại, phù hợp boundary mới: machine giữ facts/invariants, semantic scoring để LLM. ([GitHub][4])

---

# 1. Trading day — giữ OPEN

Cái này chưa nên đoán.

Ta cần:

```python
TradingDayPolicy
```

với interface kiểu:

```python
trading_day_id(timestamp)
previous_trading_day(timestamp)
day_start(timestamp)
day_end(timestamp)
```

Sau này preset có thể là:

```text
BROKER_DAY
NY_ROLLOVER
CUSTOM
```

Vì `PDH/PDL`, session aggregation và `no overnight` đều phụ thuộc vào nó. `OPEN_QUESTIONS.md` hiện cũng đánh dấu đây là blocker của production preset. ([GitHub][1])

**Không blocker M2/M3 architecture**, chỉ blocker trước khi backtest XAU nghiêm túc.

---

# 2. Structural reference policy — tôi cho CLOSED

Không cần tiếp tục coi đây là unresolved.

Mình đã quyết:

```text
machine:
detect all valid confirmed swings
+ rank / timeframe / distance / age

LLM:
which one is structurally relevant?
```

Structure detector hiện cũng đang giữ break ở mức `UNCLASSIFIED`, tức chưa tự hard-code BOS/CHoCH significance. Đây đúng với architecture này. `OPEN_QUESTIONS.md` vẫn ghi câu này là open nhưng về mặt design nó đã được giải quyết. ([GitHub][1])

Chỉ cần deterministic **bounding** để khỏi dump 500 swing cho LLM:

```text
same timeframe
recent N structural candidates
not already stale/taken
within reasonable age
```

Nhưng không rule:

```text
only ITH can be CHoCH
```

---

# 3. Close acceptance — tôi nghĩ nên chốt v0 ngay

Hiện câu hỏi là:

> bao nhiêu close, TF nào, khoảng cách bao nhiêu? ([GitHub][1])

Tôi đề xuất:

```yaml
hard_invalidation:
  type: close_beyond_level
  timeframe: SETUP_TIMEFRAME
  consecutive_closes: 1
  distance_buffer: 0.0
```

Ví dụ setup M15 LONG:

```text
sweep extreme = 3330

M5 close 3328
→ chưa hard invalidate M15 thesis

M15 close 3328
→ INVALIDATED
```

Vì entry TF noise không nên dễ dàng kill setup TF thesis.

Sau này research:

```text
1 close vs 2 closes
0 buffer vs ATR buffer
M5 vs M15
```

nhưng phải có **default deterministic v0** để lifecycle chạy.

---

# 4. Semantic decision identity — nên sửa ngay

Hiện:

```python
TradeDecision.semantic_assessment_id
```

nhưng `SetupSemanticDecision` không có ID riêng. ([GitHub][5])

Tôi sẽ sửa thành:

```python
class SetupSemanticDecision:
    decision_id: str
    assessment_id: str
    setup_candidate_id: str
    ...
```

Và final:

```python
TradeDecision:
    semantic_decision_id: str | None
```

Trace trở thành:

```text
SemanticAssessment
→ SetupSemanticDecision
→ SafetyAssessment
→ TradeDecision
```

Không ambiguous.

---

# Ngoài 7 câu cũ, M2 tạo ra 4 open question mới

Đây mới là mấy cái cần Codex quan tâm trước M3.

### A. Trading-calendar bar adjacency — **Critical**

Pipeline đang chạy closed-bar tốt, nhưng cần tách:

```text
wall-clock adjacent
vs
consecutive tradable bars
```

để weekend/maintenance gaps của XAU không phá swing/FVG/baseline.

### B. Liquidity reference lifecycle — **Critical**

Pipeline hiện lấy mọi swing/session/PD reference fact visible rồi test interaction trên mỗi bar. ([GitHub][6])

Cần state:

```text
ACTIVE
→ TAKEN
→ historical
```

nếu không cùng Asia Low/old swing có thể sinh repeated raids mãi.

### C. Displacement candidate strictness — **High**

Hiện pipeline gọi `DisplacementCandidateDetector`, nếu detector trả `None` thì evidence đó biến mất khỏi candidate layer. ([GitHub][6])

Theo architecture mới, nên permissive hơn:

```text
raw candle repricing features
→ candidate
→ LLM judges strong/weak/relevant
```

không nên một threshold `0.70` loại evidence trước khi LLM thấy.

### D. Replay/catch-up semantics — **High**

Hiện public path là:

```python
process_latest(...)
```

và nó chỉ process `bars[-1]`. ([GitHub][6])

Cần thêm:

```python
process_range(...)
# hoặc
catch_up(last_cursor, as_of)
```

để backtest, restart live system và realtime dùng cùng logic.

---

## Vậy `OPEN_QUESTIONS.md` mới nên còn khoảng này

```text
OPEN / NEED IMPLEMENTATION
1. TradingDayPolicy concrete preset
2. CloseAcceptancePolicy calibration
3. MarketCalendar / bar adjacency
4. Liquidity reference lifecycle
5. Displacement candidate permissiveness
6. Replay / catch-up semantics

RESOLVED
- Structural reference relevance → semantic evaluator
- NY PM targets → generic session targets
- LLM provenance
- legacy rule scoring
- semantic decision identity → patch schema
```

Tôi sẽ bảo Codex **update `OPEN_QUESTIONS.md` trước**, rồi làm M2.1 theo priority:

`calendar adjacency -> liquidity lifecycle -> displacement broadening -> replay -> semantic decision ID`

sau đó mới sang M3 state machine.

[1]: https://github.com/lengoctuong/ict-trading-agent/blob/main/OPEN_QUESTIONS.md "ict-trading-agent/OPEN_QUESTIONS.md at main · lengoctuong/ict-trading-agent · GitHub"
[2]: https://github.com/lengoctuong/ict-trading-agent/blob/main/src/ict_trading_agent/candidates.py "ict-trading-agent/src/ict_trading_agent/candidates.py at main · lengoctuong/ict-trading-agent · GitHub"
[3]: https://github.com/lengoctuong/ict-trading-agent/blob/main/src/ict_trading_agent/semantics.py "ict-trading-agent/src/ict_trading_agent/semantics.py at main · lengoctuong/ict-trading-agent · GitHub"
[4]: https://github.com/lengoctuong/ict-trading-agent/blob/main/src/ict_trading_agent/config.py "ict-trading-agent/src/ict_trading_agent/config.py at main · lengoctuong/ict-trading-agent · GitHub"
[5]: https://github.com/lengoctuong/ict-trading-agent/blob/main/src/ict_trading_agent/decisions.py "ict-trading-agent/src/ict_trading_agent/decisions.py at main · lengoctuong/ict-trading-agent · GitHub"
[6]: https://github.com/lengoctuong/ict-trading-agent/blob/main/src/ict_trading_agent/pipeline.py "ict-trading-agent/src/ict_trading_agent/pipeline.py at main · lengoctuong/ict-trading-agent · GitHub"
