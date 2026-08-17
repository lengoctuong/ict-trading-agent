Đã check. Có một chuyện giống vụ cache trước: trang commit hiện chỉ index tới `8ae4c02`, nhưng raw `main` đã mới hơn — `M4ReplayEngine.version = 0.1.2`, đã có progress callback, store indexes/views và active raid/breach tracking. Vì vậy tôi review **`8ae4c02` + raw HEAD hiện tại**, thay vì tin commit-list đang stale. ([GitHub][1])

## Kết luận

**Vấn đề hiệu suất là thật và phân tích trong `performance-chat.md` đúng.**

Benchmark ghi nhận 398 records chạy ~8.9s, nhưng 1,991 records lên ~139.6s; một tuần sinh tới 24,690 raid observations, 15,865 research observations, 1,260 setups và 17,035 near-misses. Input tăng ~5× nhưng runtime tăng ~15.7×, nên đây rõ ràng không phải scaling tuyến tính. ([GitHub][2])

Điểm cốt lõi có thể hiểu rất đơn giản:

> **Engine hiện đang “mỗi cây nến đi kiểm tra lại quá nhiều thứ đã biết”, thay vì “chỉ xử lý những gì vừa thay đổi”.**

Không phải ICT quá phức tạp.

---

## Những tối ưu 2 commit vừa rồi đã làm được

Code hiện đã cải thiện đáng kể phần triệu chứng:

* `FactStore` / `CandidateStore` có index theo symbol/TF/type/time và có `*_view()` để hot path không deep-copy object liên tục.
* M3 đã giữ `_active_breaches` và `_active_episode_ids`, không còn hoàn toàn scan toàn bộ raid history ở mọi nơi.
* M4 có progress callback và lookup chính xác theo timestamp/candidate indexes.

Nhưng đây chưa chạm đủ sâu vào root.

---

# Root cause còn lại

### 1. Raid đang phát event dù **không có thông tin mới**

Đây là lỗi lớn nhất.

Trong `_observe_existing_episodes()`, nếu raid đang `BREACHED`, bar tiếp theo vẫn tạo `RAID_OBSERVATION` + `RaidEpisodeUpdate`, kể cả bar đó không tạo extreme mới và cũng chưa reclaim.

Ví dụ:

```text
SSL = 3300

Bar 1: low 3298 → BREACHED     ← có ý nghĩa
Bar 2: low 3299 → vẫn dưới level
Bar 3: low 3299.5
Bar 4: low 3299
Bar 5: low 3297 → NEW EXTREME  ← có ý nghĩa
Bar 6: close 3302 → RECLAIMED  ← có ý nghĩa
```

Hiện tại gần như:

```text
1 2 3 4 5 6
↓ ↓ ↓ ↓ ↓ ↓
event event event event event event
```

Tôi muốn:

```text
Bar 1 → BREACHED
Bar 5 → NEW_EXTREME
Bar 6 → RECLAIMED
```

Bar 2–4 **không mất dữ liệu market**; raw OHLC đã tồn tại rồi.

Đây là fix vừa tăng tốc vừa làm audit sạch hơn.

---

### 2. Evidence đang giả làm `SetupTransition`

Mỗi raid observation lại gọi `_merge_episode_evidence()`, và code tạo transition:

```text
DETECTED → DETECTED
FORMING  → FORMING
```

chỉ để thêm evidence.

Sau đó mỗi transition lại merge:

```text
old evidence list
+ new evidence
→ SetupCandidate mới
```

Evidence list càng dài thì càng copy nhiều.

Conceptually cũng không sạch:

```text
Transition
= state changed

EvidenceLink
= có thêm evidence
```

Hai thứ nên tách.

---

### 3. Swing hierarchy vẫn tính lại toàn bộ lịch sử

Current:

```python
history = list(facts)

for ...
    source = [fact for fact in history ...]
    sort(...)
    scan triples...
```

mỗi lần có bar mới.

Nhưng để promote swing ta thực chất chỉ cần:

```text
3 STH mới nhất
→ kiểm tra middle có thành ITH không

3 ITH mới nhất
→ kiểm tra middle có thành LTH không
```

Không cần đọc lại 5,000 swings cũ.

---

### 4. M2 vẫn thử **mọi reference level** với mỗi bar

Pipeline hiện lấy toàn bộ active:

```text
Swing
Session level
PDH/PDL
```

rồi:

```python
for reference_fact in reference_facts:
    detect(bar, reference)
```



Nhưng candle:

```text
low=3320
high=3330
```

không cần kiểm tra level:

```text
3100
3200
3500
3600
...
```

Chỉ query các active level nằm trong price range candle.

---

### 5. M3 vẫn duyệt **mọi setup lịch sử** mỗi bar

Trong `_process_bar()` hiện vẫn gọi `setup_store.visible_views(...)` rồi loop toàn bộ visible setups.

Terminal setup còn được đưa qua `_observe_terminal_setup()` cho post-terminal research. Cơ chế research đúng, nhưng scheduler phải biết setup nào hiện còn trong research horizon thay vì scan tất cả setup từng tồn tại.

---

### 6. RAM issue nằm ở audit representation

`_AuditCollector` giữ toàn bộ `M4AuditEvent` + full payload trong RAM. Sau replay M4 lại tạo thêm:

```text
raw_events
events
event_map
near_misses
steps
analysis_events
analysis_misses
```

và summary tiếp tục tạo các list `bars/facts/candidates/transitions/...`.

Đặc biệt `near_miss` còn mang lại payload của source event, nên dễ duplicate lượng data lớn.

Vì vậy working/private memory tăng rất nhanh là hợp lý với benchmark trong `performance-chat.md`. ([GitHub][2])

---

# Tôi đồng ý với hướng redesign trong `performance-chat.md`

Và tôi muốn biến nó thành **M4 Performance Hardening**, chưa sang empirical M4.2.

| Priority | Task                        | Answer đơn giản                                       | Cách dev                                                                                                                         | Confidence | Need review                                       |
| -------- | --------------------------- | ----------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------- | ---------: | ------------------------------------------------- |
| **P0**   | State-change-only raid      | Không thay đổi thì không sinh event                   | Emit chỉ khi `NOT_SEEN→BREACHED`, `new global/per-TF extreme`, `BREACHED→RECLAIMED`; raw bars vẫn giữ                            |    **99%** | **Có**, vì liên quan evidence semantics           |
| **P0**   | Evidence ≠ Transition       | Thêm evidence không phải đổi trạng thái setup         | Tạo append-only `SetupEvidenceLink`; `SetupTransition` chỉ dùng khi status thật sự đổi                                           |    **99%** | Không                                             |
| **P0**   | Incremental swing hierarchy | Không đọc lại toàn bộ swing cũ                        | Giữ rolling 3 swing theo `(symbol, TF, side, rank)`; promotion mới feed tiếp rank trên                                           |    **98%** | **Có**, phải chứng minh output giống algorithm cũ |
| **P0**   | Price-index references      | Candle chỉ kiểm tra level nó thực sự chạm             | Active BSL/SSL sorted index + range query theo `[low,high]`; taken lifecycle remove khỏi liquidity index, structural index riêng |    **97%** | **Có**, không được miss cross-TF level            |
| **P0**   | Active setup scheduler      | Không hỏi 1,000 setup cũ mỗi bar                      | Queue/index theo `state + relevant TF + bar-count deadline`; M15 bar chỉ wake setup cần M15, M5 tương tự                         |    **98%** | **Có**, vì expiry/MTF semantics                   |
| **P1**   | Compact research scheduler  | Terminal setup chỉ được xem trong horizon             | Schedule đến đúng `post_terminal_research_bars`, hết horizon remove                                                              |    **99%** | Không                                             |
| **P1**   | Stream audit                | Log hết nhưng không giữ hết RAM                       | append JSONL/event stream ngay lúc event sinh; RAM giữ active state + counters thôi                                              |    **98%** | Không                                             |
| **P1**   | Incremental summary         | Không đợi cuối run mới scan lại hàng trăm nghìn event | Counter/histogram update khi stream event; near-miss chỉ giữ source ID + threshold metrics                                       |    **99%** | Không                                             |
| **P1**   | Remove heavy replay `steps` | Không duplicate hàng nghìn event IDs                  | production research mode chỉ lưu timing/count per step hoặc stream riêng; detailed steps optional debug mode                     |    **97%** | Không                                             |
| **Gate** | Semantic equivalence tests  | Tăng tốc nhưng không được mất setup                   | Old vs new trên fixture: same raid episodes, extremes, shifts, FVGs, lifecycle, READY outputs; audit spam được phép giảm         |    **99%** | **Có, rất quan trọng**                            |
| **Gate** | Benchmark                   | Phải chứng minh scaling đã hết bệnh                   | 1 day → 1 week → full; profile acquisition/replay/export riêng                                                                   |    **99%** | Không                                             |

---

## Điểm cần đặc biệt cẩn thận

### Incremental swing không được đổi ICT semantics

Đây là optimization dễ viết sai nhất.

Muốn:

```text
STH1, STH2, STH3
→ STH2 promoted ITH

ITH1, ITH2, ITH3
→ ITH2 promoted LTH
```

Promotion mới chỉ được available khi swing bên phải đã confirm, y như algorithm hiện tại.

Nên tôi yêu cầu test kiểu:

```text
old full-history promoter
vs
new incremental promoter

→ exact same promotion IDs
→ exact same available_at
→ exact same rank
```

trên synthetic + random sequence.

Không chỉ unit-test vài case đẹp.

---

## Price index cũng không được làm giảm recall

Index chỉ là:

```text
"reference nào có khả năng được candle chạm?"
```

**không được** biến thành thêm ICT filtering.

Sau query, detector hiện tại vẫn quyết:

```text
breach?
reclaim?
structure close-through?
```

Tức:

```text
index = acceleration
không phải strategy rule
```

Đây là distinction rất quan trọng.

---

## Setup scheduler phải giữ MTF

Một setup M15/M5 có thể cần:

```text
M15
→ shift / invalidation

M5
→ FVG / reaction

post-terminal
→ M15 + M5 research
```

Nên scheduler không đơn giản:

```text
setup belongs to one TF
```

mà là:

```text
setup_id
├── wake_on_M15
├── wake_on_M5
└── deadline per purpose
```

Nếu optimize bằng cách chỉ đưa setup vào một queue thì rất dễ miss concept.

---

# Có nên stream audit trước không?

Tôi đồng ý với `performance-chat.md` rằng **không nên coi streaming là root fix**. File cũng nói rõ streaming giải quyết RAM nhưng không giải quyết lượng công việc/event thừa. ([GitHub][2])

Order đúng là:

```text
1. Giảm event thừa
2. Giảm scan thừa
3. Active scheduling/index
4. Stream audit
```

Streaming có thể code song song về engineering, nhưng benchmark chính chỉ có ý nghĩa sau 1–3.

---

# Có nên multithread / multiprocessing?

**Chưa.**

Core replay hiện intentionally chạy theo `close_time`, cùng timestamp xử lý TF theo thứ tự M1→M5→M15→H1→..., và các TF dùng chung raid/setup state.

Parallel lúc này sẽ:

```text
algorithm chậm
+ synchronization
+ race conditions
+ RAM cao hơn
```

chứ không sửa root.

Sau này parallel được ở:

```text
experiment A / B / C
symbol A / B
chart rendering
outcome analysis
```

sau khi event stream đã freeze.

---

# Một improvement tôi thêm ngoài `performance-chat.md`

**Đừng mặc định tạo object setup nặng cho cả H1 + M15 nếu bottleneck vẫn còn sau scheduler.**

Hiện mỗi raid tạo 2 setup hypotheses theo `setup_timeframes=(H1,M15)`.

Tuy nhiên **không được giải quyết bằng cách bỏ H1 hoặc M15**, vì sẽ giảm recall.

Nếu scheduler vẫn nặng, có thể lazy-materialize:

```text
RaidEpisode
├── lightweight waiting hypothesis M15
└── lightweight waiting hypothesis H1

M15 shift xuất hiện
→ materialize M15 SetupCandidate

H1 shift xuất hiện
→ materialize H1 SetupCandidate
```

Về ICT semantics vẫn giữ cả hai hypothesis; chỉ tránh tạo full evidence-bearing object trước khi có shift.

Tôi để cái này **P2**, chưa cần làm ngay.

---

## Performance gate trước khi tiếp tục M4.2

Target trong `performance-chat.md` là week sample **≤30 giây và RAM <400 MB**; tôi thấy đây là engineering gate hợp lý, không phải ICT parameter. ([GitHub][2])

Tôi còn thêm một gate quan trọng hơn:

> **Meaningful event count phải scale theo actual market events, không scale theo `raid × số bar × số setup`.**

Ví dụ cùng dataset 1 tuần, sau refactor tôi kỳ vọng `raid_observation` giảm rất mạnh từ 24,690 xuống gần số:

```text
first breach
+ new extreme
+ reclaim
```

thực sự xảy ra. Không đặt con số cứng trước khi benchmark.

### Trạng thái milestone

```text
M3       FROZEN ✅
M4.1     PASS ✅
M4.1.1   PASS ✅
M4-PERF  BLOCKING M4.2 🔴
M4.2     chưa chạy full
```

Tôi sẽ **không tune ICT parameter hoặc đọc PnL trước khi M4-PERF pass**. Nếu engine đang tự tạo 20–30k audit event thừa mỗi tuần thì parameter analysis sẽ vừa chậm vừa khó audit.

[1]: https://github.com/lengoctuong/ict-trading-agent/commit/8ae4c02 "feat: harden M4 replay prerequisites · lengoctuong/ict-trading-agent@8ae4c02 · GitHub"
[2]: https://github.com/lengoctuong/ict-trading-agent/blob/main/chat_web/performance-chat.md "ict-trading-agent/chat_web/performance-chat.md at main · lengoctuong/ict-trading-agent · GitHub"

---

# Codex implementation update — 2026-08-17

Đã implement M4-PERF theo đúng thứ tự trên, không đổi ICT parameter:

```text
P0.1 state-change-only raid observations       PASS
P0.2 SetupEvidenceLink != SetupTransition      PASS
P0.3 incremental rolling swing hierarchy       PASS
P0.4 active cross-TF price reference index     PASS
P0.5 MTF setup scheduler lanes                  PASS
P1   compact research/audit/near-miss/steps     PASS (pilot mode)
```

Semantic gates:

- `SetupTransition` bắt buộc đổi status; evidence-only update là append-only
  `SetupEvidenceLink`.
- Swing incremental được so exact ID, `available_at`, rank, source IDs với
  full-history promoter trên nested fixture và 20 random streams.
- Price index được so với full detector scan và giữ M5/M15/H1 cross-TF recall;
  liquidity `TAKEN` và structural inactivity vẫn độc lập.
- Full-audit và compact-pilot mode có cùng core raid/shift/FVG/transition/READY,
  setup summary và near-miss identity trên regression fixture.
- 109 tests pass.

Benchmark cùng cached Exness XAUUSDm:

```text
before: 1 week / 1,991 bars ~= 139.6 s, >1 GB private memory
after:  1 week / 1,992 bars = 18.38 s total
peak:   369.5 MB working set / 332.3 MB private bytes
raid_observation: 24,690 -> 7,704
research_observation: 14,751 (summary preserved, compact storage)
near-miss: 16,343 (summary preserved)
```

`18.38 s` gồm validate raw cache, replay, JSONL export và M4.2 analysis. Week
gate `<=30 s` và RAM `<400 MB` đã PASS.

Full cached warmup + analysis range đã được thử và chủ động dừng sau 3 phút để
không quay lại kiểu chạy mù/đè máy. Đây là long-run gate còn OPEN; week blocker
đã được gỡ nhưng chưa được phép ghi full-range artifact là PASS.

Trạng thái:

```text
M3       FROZEN ✅
M4.1     PASS ✅
M4.1.1   PASS ✅
M4-PERF  WEEK GATE PASS ✅
M4.2     machine report chạy được; full-range + chart review còn OPEN
CHART    PENDING_USER_REVIEW
```
