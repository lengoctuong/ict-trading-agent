Review lại current HEAD. **Performance refactor đã làm đúng phần lớn root-cause fix**, nhưng tôi chưa cho M4-PERF = PASS vì còn **1 issue semantic/performance mới khá quan trọng** và **2 phần optimization chưa hoàn toàn đóng**.

Lưu ý: trang commit history public vẫn chỉ hiện tới `8ae4c02`, trong khi raw source hiện đã có `M3 0.1.4`, `M4 0.2.0`, `SetupEvidenceLink`, scheduler và price indexes mới. Vì vậy review này dựa trên **current raw source**, không dựa vào commit-list đang stale. ([GitHub][1])

## Review M4-PERF

| Status            | Task                                   | Hiểu đơn giản                                                              | Review                                                                                                                                                                                                                         | Confidence | Need review                     |
| ----------------- | -------------------------------------- | -------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ---------: | ------------------------------- |
| ✅ **DONE**        | State-change-only raid                 | Raid đứng yên thì không spam event                                         | Đã skip khi state vẫn `BREACHED`, chưa reclaim và không có extreme mới. Test cũng cover bar “không đổi gì” → zero update.                                                                                                      |        99% | Không                           |
| ✅ **DONE**        | Evidence ≠ transition                  | Thêm evidence không giả làm đổi state                                      | Có `SetupEvidenceLink`; lifecycle transition giờ chỉ dành cho status change.                                                                                                                                                   |        99% | Không                           |
| ✅ **DONE**        | Incremental swing hierarchy            | Không scan toàn bộ swing mỗi bar                                           | Rolling deque 3 swing/rank đã có; full-history implementation vẫn giữ làm reference.                                                                                                                                           |        96% | **Có một test thiếu**, xem dưới |
| ✅ **DONE**        | Price indexes                          | Không quét mọi liquidity level                                             | FactStore có sorted price index và M2 đã gọi trực tiếp active liquidity/structure range queries.                                                                                                                               |        97% | Không                           |
| ✅ **DONE**        | Setup scheduler                        | Không scan mọi historical setup mỗi bar                                    | `scheduled_views()` wake setup theo TF; terminal lane được retire sau research horizon.                                                                                                                                        |        98% | Không                           |
| ✅ **DONE**        | Compact research mode                  | Research logs không nhất thiết giữ hết Pydantic facts                      | Có option không retain research facts/events; test xác nhận core semantics + summary + near-miss giống full mode.                                                                                                              |        98% | Không                           |
| 🟡 **PARTIAL**    | Streaming audit                        | RAM giảm nhưng chưa stream thật                                            | `export_jsonl()` vẫn export **sau replay** từ `result.events`; collector vẫn giữ phần lớn core events trong RAM.                                                                                                               |        98% | Không                           |
| 🟡 **PARTIAL**    | Evidence storage complexity            | Link riêng rồi, nhưng current setup vẫn rebuild cumulative evidence arrays | Mỗi evidence link vẫn tạo SetupCandidate mới với old evidence + new evidence. Event frequency đã giảm nhiều nên có thể đủ nhanh, nhưng chưa O(1).                                                                              |        96% | Không ngay                      |
| 🔴 **NEW / HIGH** | **Repeated cross-TF structure breaks** | M5 nằm trên H1 swing 10 bars có thể sinh 10 “break” facts                  | Detector hiện chỉ check `close >/< level`, không check **crossing transition**. Same-TF được consume sau break, nhưng cross-TF reference không bị structural consume, nên các bar LTF sau vẫn có thể tạo lại STRUCTURE_BREAK.  |    **98%** | **Có — ICT semantics**          |
| 🟡 **TEST GAP**   | Incremental swing equivalence          | Optimization không được đổi swing rank                                     | Code có `detect_full_history()` để compare, nhưng tôi chưa thấy test actually so incremental vs full-history.                                                                                                                  |        97% | Không                           |

---

# Vấn đề mới: cross-TF break đang có thể spam

Đây là cái tôi muốn sửa trước benchmark tiếp.

Hiện rule:

```text
M5 close > H1 swing high
→ STRUCTURE_BREAK candidate
```

đúng với abstraction trước đó là:

> “M5 close-through H1 reference”, **không phải H1 BOS**.

Nhưng detector không hỏi:

```text
M5 trước đó ở dưới level?
```

Nó chỉ hỏi:

```text
current M5 close > level?
```



Ví dụ:

```text
H1 swing = 3400

M5:
09:00 close 3402  → interaction
09:05 close 3404  → interaction nữa
09:10 close 3405  → interaction nữa
09:15 close 3403  → interaction nữa
...
```

Với **same-TF H1 break** thì không sao, vì H1 structural lifecycle sau break sẽ deactivate reference. Nhưng M5 không được phép deactivate H1 structure — đúng policy mình đã chốt — nên H1 reference vẫn active cho các M5 bars tiếp theo. FactStore price query vẫn trả nó nếu M5 close còn nằm phía bên kia level. 

### Tôi đề xuất semantic đúng hơn

Cross-TF cần state:

```text
(reference, detection_tf)

BELOW
→ CROSS_ABOVE      # emit interaction

ABOVE
→ ABOVE            # không emit break mới

ABOVE
→ CROSS_BELOW      # acceptance/reclaim state change
```

Mirror bearish.

Tức:

```text
M5 first close across H1 level
→ CROSS_TF_CLOSE_THROUGH

M5 stays above for 4 bars
→ acceptance_duration = 4
→ không tạo 4 STRUCTURE_BREAK candidates
```

Nếu sau này xuống dưới rồi lại cross lên:

```text
→ có thể mở interaction episode mới
```

### Vì sao tôi thích cách này?

Nó **không giảm recall**.

Ta vẫn giữ:

* first lower-TF cross;
* detection TF;
* reference TF;
* distance;
* acceptance duration;
* max excursion;
* reclaimed/not reclaimed.

Nhưng bỏ duplicate semantic event kiểu “giá vẫn đang ở cùng một phía”.

**Need review: Có.** Tôi đánh confidence **90% về semantic policy, 98% rằng current behavior có thể spam**.

---

# Incremental swing: tôi muốn thêm equivalence gate

Algorithm mới nhìn đúng:

```text
rolling 3 STH
→ ITH

rolling 3 ITH
→ LTH
```

và vẫn dùng right swing `available_at` để confirm promotion. 

Nhưng vì swing rank ảnh hưởng LLM sau này, tôi không muốn chỉ tin source review.

Repo đã giữ:

```python
SwingHierarchyPromoter.detect_full_history(...)
```

chính xác để làm reference implementation. 

Tôi muốn test:

```text
random swing stream
        ↓
incremental promoter

vs

full-history promoter
        ↓
exact same:
- promoted swing ID
- rank
- available_at
- source IDs
```

100–1000 random sequences là đủ.

Không liên quan ICT parameter; chỉ chứng minh optimization không làm đổi concept.

---

# RAM: đã tốt hơn nhưng chưa giải quyết hoàn toàn

Compact mode là improvement thật.

Test hiện chứng minh:

```text
full research mode
vs
compact mode

→ same core events
→ same summary
→ same near misses
```



Nhưng `_AuditCollector` vẫn giữ:

```text
events dict
_sequence
_setup_origin_times
near misses
```

và cuối run vẫn build `events`, `event_map`, `misses`, `analysis_events`... 

Nên nếu week benchmark giờ CPU ổn nhưng RAM vẫn >400–500 MB:

> **lúc đó mới làm true streaming collector.**

Không cần làm trước benchmark mới.

---

# EvidenceLink vẫn copy cumulative lists

Ta đã sửa cái sai lớn:

```text
DETECTED → DETECTED
```

không còn dùng transition nữa.

Nhưng khi append EvidenceLink, store vẫn:

```text
new SetupCandidate =
old evidence ids
+ new evidence ids
```



Nghĩa là một setup có 100 evidence links vẫn có một chút kiểu:

```text
1 + 2 + 3 + ... + 100
```

copy cost.

Tuy nhiên sau state-change-only raid, **số evidence link thực tế đáng lẽ giảm rất mạnh**, nên tôi chưa muốn redesign thêm abstraction trước khi benchmark.

Nếu profiling vẫn chỉ vào `_apply_evidence_link`, mới chuyển current setup thành:

```text
core state
+
evidence-link IDs/reference
```

và materialize full payload chỉ khi READY/analysis.

---

# Tôi muốn benchmark lại trước khi code thêm lớn

`performance-chat.md` benchmark cũ là:

```text
1 day   398 records → 8.9s
1 week 1991 records → 139.6s
```

với 24,690 raid observations. 

Nhưng những con số đó **được đo trước các root fixes hiện có**: state-change-only raid, incremental swings, price index, scheduler, evidence links. Current source rõ ràng đã thay đổi sau benchmark đó. 

Vậy **đừng optimize mù tiếp**.

## Bước kế tiếp

| Priority        | Task                                | Answer đơn giản                                | Dev                                                                         | Confidence | Need review |
| --------------- | ----------------------------------- | ---------------------------------------------- | --------------------------------------------------------------------------- | ---------: | ----------- |
| **P0**          | Cross-TF close state                | Không spam cùng một H1 break mỗi M5 bar        | per `(reference_id, detection_tf)` side-state / interaction episode         |        90% | **Có**      |
| **P0**          | Swing equivalence test              | Chứng minh optimization không đổi ICT swing    | randomized incremental vs full-history exact comparison                     |        99% | Không       |
| **P0**          | Week benchmark lại                  | Xem root fixes đã đủ chưa                      | same exact 1,991-record dataset; compact mode; profile CPU+RAM+event counts |        99% | Không       |
| **Gate**        | Event count                         | Raid observation phải giảm mạnh                | compare old 24,690 với new meaningful state changes                         |        99% | Không       |
| **Conditional** | True audit streaming                | Chỉ làm nếu RAM còn cao                        | JSONL sink + incremental aggregates; return lightweight result              |        95% | Không       |
| **Conditional** | Lazy setup evidence materialization | Chỉ làm nếu CPU profile vẫn dính evidence-copy | current state giữ link refs thay vì cumulative arrays                       |        92% | Không       |

### Gate tôi giữ

```text
1-week sample
≤ ~30 sec
RAM < ~400 MB

AND

core semantic output identical
```

Nếu đạt:

> **M4-PERF = PASS → sang M4.2 real empirical analysis.**

Nếu runtime vẫn ~60–100s thì nhìn profiler trước; không tự động chuyển sang multiprocessing.

**Tóm lại:** lần refactor này tốt, đã đánh đúng root hơn hẳn. Tôi chỉ muốn xử lý **cross-TF repeated close-through**, thêm swing-equivalence test, rồi benchmark lại. Không cần redesign tiếp trước khi có số mới.

---

## Codex implementation update — 2026-08-17 (cross-TF close state)

Đã implement P0 repeated cross-TF break theo đúng policy ở trên, nhưng không cần một state-map riêng: trạng thái được suy ra causally từ **previous closed bar của detection TF**.

```text
H1 swing high = 3400

previous M5 close <= 3400
+
current M5 close > 3400
→ emit CROSS_TF_CLOSE_THROUGH

previous M5 close > 3400
+
current M5 close > 3400
→ no duplicate event

previous M5 close <= 3400, sau khi đã return lại phía dưới
+
current M5 close > 3400
→ emit a new interaction episode
```

Mirror cho sell-side/H1 swing low. Same-TF vẫn giữ lifecycle structural consume như cũ; cross-TF không deactivate H1 reference. Có regression test cho `cross → stay beyond → return → cross again`.

Swing equivalence randomized gate đã có trong current source: 20 seeded streams × 80 swings, exact identity/rank/available-at/source IDs so với `detect_full_history()`.

Pending: chạy lại week benchmark compact mode và quyết định true audit streaming chỉ nếu RAM gate chưa đạt.

### Profile follow-up

Week replay sau cross-TF semantic fix đã giảm `price_break`/`structure_break` từ
`2,286` xuống `1,844` mà không đổi raid/shift/FVG/setup output. Nhưng wall time
đo được là `45.18s`, nên M4-PERF **chưa PASS lại**. Memory number từ harness
đầu tiên không hợp lệ vì Windows venv launcher tách child process; không dùng
con số đó cho gate.

`cProfile` chỉ ra thời gian nằm chủ yếu ở query/model overhead của replay
sequential (không phải bản thân ICT multi-TF): `visible_views` sorting/deepcopy,
`ClosedBarFeed.bars()` re-scan history, và terminal research fan-out. Đã bắt đầu
harden hai read-only hot paths an toàn: lookup bars bằng close-time bisect và
M3 kiểm M2 candle theo exact `available_at` thay vì scan history. Sau đó cần
benchmark lại và đo RAM theo đúng process tree trước khi quyết định streaming
collector hay evidence materialization.

### Re-benchmark result

Fresh compact replay on the same 1,992-bar cached week completed successfully.
The profile-safe output keeps raid/shift/FVG/setup and near-miss summaries
unchanged, while `price_break` and `structure_break` are `1,844` versus `2,286`
before the cross-TF transition rule. Two wall-clock measurements are about
`45.18s` then `~40s` after the safe feed/M2 lookup hardening, still above the
`<=30s` gate. Therefore **M4-PERF runtime remains OPEN**. Do not attribute the
time to cross-TF ICT logic: profile shows replay query/Pydantic/audit and
terminal-research overhead. The post-change RAM number is intentionally OPEN
until a monitor follows the actual Python child/process tree.

[1]: https://github.com/lengoctuong/ict-trading-agent/commits/main "Commits · lengoctuong/ict-trading-agent · GitHub"
