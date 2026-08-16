# M3.1 feedback and implementation record

> **Implementation update — 2026-08-17:** Các mục M3.1 bắt buộc trong review
> này đã được implement: global first-take + append-only `RaidEpisode`, tách
> H1/M15 setup khỏi M5 entry, effective swing rank as-of break, stateful FVG
> reaction/failure/expiry, và post-terminal research observer. Regression tests
> cover toàn bộ bảy case bắt buộc ở cuối file. Các mục được đánh dấu M5-related
> (dynamic MarketState payload và DOL target partition) vẫn để M5, không bị giả
> là đã giải quyết trong M3.1.

Review ban đầu: **Chưa nên sang M4 ngay.** Core M3 đã có đường chạy `RAID -> SHIFT -> linked FVG -> reaction -> READY_FOR_LLM`, cùng test cho multi-bar reclaim, invalidation, expiry, cross-TF structure và replay. Nhưng có **4 vấn đề quan trọng có thể làm miss ICT setup**: multi-timeframe raid/setup đang bị trộn, liquidity `TAKEN` đang theo từng TF, swing hierarchy chưa thực sự đi vào structure reasoning, và reaction/logging còn quá chặt.

Tôi chưa independently chạy `pytest` vì runtime này không clone được GitHub; review dưới đây là source-level review trực tiếp code/tests trên `main`.

## M3 review

| Status                          | Vấn đề / task                      | Hiểu đơn giản                                                                                     | Review + cách sửa                                                                                                                                                                                                                                                                                                                                                                                                                   |                             Confidence | Need review                    |
| ------------------------------- | ---------------------------------- | ------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------: | ------------------------------ |
| **IMPROVE — Critical**          | Liquidity lifecycle                | Một H1 liquidity đã bị M5 lấy thì về mặt price **nó đã bị lấy**, không thể còn “untaken trên M15” | Code hiện filter trạng thái `TAKEN` bằng `detection_timeframe`, nghĩa là cùng level có trạng thái riêng M5/M15.  **Sửa:** `first liquidity take` là global theo reference; còn M5/M15/H1 reclaim/acceptance là các observation của cùng raid episode.                                                                                                                                                                               |                                **98%** | Không, policy này mình đã chốt |
| **DONE**                        | Liquidity vs structural lifecycle  | Wick sweep không làm swing mất ý nghĩa structure                                                  | Đã tách `StructureLifecycleTracker`: chỉ same-TF close-break mới làm structural reference `BROKEN`; cross-TF close không consume structure.                                                                                                                                                                                                                                                                                         |                                **98%** | Không                          |
| **DONE**                        | Cross-TF structure                 | `M5 close > H1 swing` không được gọi H1 BOS                                                       | Code lưu `detection_timeframe=M5`, `reference_timeframe=H1`, `same_timeframe_structure_eligible=False`; test đúng policy. ([GitHub][1])                                                                                                                                                                                                                                                                                             |                                **98%** | Không                          |
| **IMPROVE — Critical**          | Raid episode vs setup TF           | TF nào thấy sweep trước không được “chiếm” cả setup                                               | `_start_or_merge_episode()` merge theo `reference_fact_id`; setup TF lại được cố định bằng TF của raid đầu tiên. Sau đó M3 chỉ update setup có TF bằng bar đang process.  **Ví dụ:** M5 sweep H1 level trước → tạo M5 setup; M15 confirmation sau bị merge vào setup đó nhưng không tạo được M15 path riêng. **Sửa:** `RaidEpisode` global riêng; từ một episode có thể tạo/update setup evidence trên M15/H1 và entry evidence M5. |                                **97%** | **Có — quan trọng**            |
| **IMPROVE — Critical**          | `setup_timeframe = raid timeframe` | M5 thấy sweep không có nghĩa thesis là M5 thesis                                                  | Hiện setup được tạo với `setup_timeframe=raid.timeframe`, và invalidation dùng chính TF đó.  Điều này xung đột profile của mình: **H1/M15 setup, M5 entry, M1 refine**. Tôi đề xuất tách `raid_detection_tf`, `setup_tf`, `entry_tf`; không tự động biến M5 raid thành M5 thesis.                                                                                                                                                   |                                **95%** | **Có — rất quan trọng**        |
| **PARTIAL**                     | Swing hierarchy                    | Đã biết STH → ITH → LTH nhưng structure chưa thật sự dùng rank mới                                | Promotion append-only đã implement đúng point-in-time.  Nhưng M2 chỉ dùng `SWING_POINT` làm structural/liquidity references, không dùng `SWING_PROMOTION`; original swing vẫn mang rank `SHORT_TERM`.  **Sửa:** structure break phải resolve `effective_rank_as_of_break = STH/ITH/LTH` của cùng underlying swing và đưa rank này vào evidence cho LLM. Không tạo duplicate swing level.                                            |                                **95%** | **Có**                         |
| **DONE / minor improve**        | Same-bar + multi-bar reclaim       | Same-bar canonical, reclaim trễ vẫn được bắt                                                      | `≤3 bars` được promote; reclaim sau window vẫn được ghi và không tạo setup. Tests cover cả hai. ([GitHub][1])                                                                                                                                                                                                                                                                                                                       |                                **90%** | M4 review parameter `3`        |
| **DONE**                        | Shift candidate                    | Sau raid, machine chỉ nói “có structural shift candidate”, chưa ép BOS/CHoCH                      | M3 lấy same-TF structure breaks cùng direction, gom nhiều broken references vào một SHIFT candidate và vẫn để `unclassified_bos_choch`.  Đây đúng boundary LLM.                                                                                                                                                                                                                                                                     |                                **95%** | Không                          |
| **DONE / research param**       | Raid → shift window                | Không nối raid với break quá xa                                                                   | M5=12, M15=8, H1=4 đã implement.  Đây chỉ là promotion window, cần M4 calibrate.                                                                                                                                                                                                                                                                                                                                                    |                                **80%** | **Có sau M4**                  |
| **DONE**                        | Displacement permissive            | Không loại candle vì 68% thay vì 70%                                                              | Repo register xác nhận directional candles vẫn visible và threshold chỉ là calibration.                                                                                                                                                                                                                                                                                                                                             |                                **95%** | M4 calibration                 |
| **DONE / research param**       | Linked FVG                         | Không lấy random FVG                                                                              | FVG phải cùng direction, FVG middle candle phải chính displacement candle; displacement được phép ở shift bar hoặc bar kế tiếp.                                                                                                                                                                                                                                                                                                     |                                **90%** | Review sensitivity M4          |
| **IMPROVE — High**              | FVG reaction                       | Hiện đang bắt **touch + favorable close trên cùng candle**                                        | Code chỉ xét `favorable_close` bên trong nhánh `touched`; bar phải vừa chạm FVG vừa close ra ngoài FVG mới READY.  Điều này miss case rất tự nhiên: `bar A touch/close inside -> bar B reject/close away`. **Sửa:** FVG có state `TOUCHED`; sau touch cho phép reaction confirmation ở các bar tiếp theo. Tôi đề xuất log 1/2/3-bar reaction và chưa hard-filter quá sớm.                                                           |      **92% architecture / 75% window** | **Có**                         |
| **NOT DONE — High**             | FVG failure                        | FVG bị xuyên hẳn phải được nhận biết riêng                                                        | Current path chỉ có touch/reaction hoặc chờ expiry; không có explicit full-fill/failure path.  **Sửa:** entry-zone lifecycle riêng `FRESH -> TOUCHED -> REACTED / FAILED / EXPIRED`. Một FVG fail không được xóa lịch sử raid/shift và không nhất thiết giết toàn thesis nếu còn/new FVG khác.                                                                                                                                      |                                **92%** | **Có**                         |
| **PARTIAL**                     | Invalidation                       | Rule đúng, nhưng TF dùng để invalidate có thể sai                                                 | Test đúng rule `1 setup-TF close beyond raid extreme, zero buffer`. ([GitHub][1]) Nhưng vì hiện `setup_tf=raid_tf`, M5 raid có thể khiến M5 close kill thesis quá sớm. Fix MTF model ở trên trước thì rule invalidation mới đúng nghĩa.                                                                                                                                                                                             | **90% rule / 70% current integration** | **Có**                         |
| **DONE**                        | FVG expiry clock                   | Đúng là đếm từ lúc FVG available                                                                  | Code tính số bars từ `zone.available_at`; defaults M5=24, M15=16, H1=6.                                                                                                                                                                                                                                                                                                                                                             | **90% implementation / 80% parameter** | M4 calibration                 |
| **IMPROVE — Critical research** | Near-miss logging sau expiry       | Đây là chỗ tôi chưa chấp nhận để sang M4                                                          | Khi setup thành terminal (`EXPIRED`, `INVALIDATED`...), loop bỏ qua nó.  Vì vậy raid expire ở bar 12 thì **shift ở bar 13/14 sẽ không còn được link để log “late shift”**; tương tự late FVG/retrace. Điều này làm M4 khó biết parameter có quá strict không. **Sửa:** trading lifecycle có thể terminal, nhưng `ResearchObserver` phải tiếp tục theo dõi candidate một calibration horizon.                                        |                                **99%** | Không                          |
| **PARTIAL**                     | READY_FOR_LLM payload              | Trace evidence khá tốt nhưng chưa thật sự MTF                                                     | Payload chứa setup evidence facts/candidates/targets và tests kiểm tra displacement có mặt.  Tuy nhiên context hiện là dict truyền vào constructor, targets cũng là snapshot tĩnh khi tạo pipeline. Trước M5 cần build context động từ `MarketState as_of`.                                                                                                                                                                         |                                **95%** | Review ở M5                    |
| **M5-related**                  | DOL targets                        | Chưa cần block M3                                                                                 | `ready_payload()` hiện đưa mọi target available & untaken; chưa tách same-direction selectable targets khỏi opposite-direction context.  M5 sửa thành `selectable_targets` + `context_targets`; LLM chọn DOL, safety chọn execution TP.                                                                                                                                                                                             |                                **95%** | Có                             |
| **DOC/PRESET stale**            | Exness/session policy              | Code docs chưa phản ánh hết decision mới                                                          | `OPEN_QUESTIONS.md` vẫn ghi trading-day/session windows là open.  Trong khi Exness MetaTrader dùng GMT+0, và XAUUSD có rollover/closure quanh 21:00 hoặc 22:00 UTC tùy mùa. ([Exness Help Center][2]) Nên update spec/preset: Exness raw candle clock UTC; ICT session clock NY/DST-aware; không tạo thêm “NY day” abstraction ở M3.                                                                                                |                                **95%** | Không                          |

## Hai bug semantics tôi coi là quan trọng nhất

### A. Current M3 chưa thật sự là multi-timeframe setup

Hiện nó giống:

```text
H1 reference
    ↓
M5 raid
    ↓
M5 shift
    ↓
M5 displacement
    ↓
M5 FVG
    ↓
M5 reaction
```

hoặc toàn bộ M15.

Trong khi profile của mình phải cho phép kiểu:

```text
H1 / M15 context & setup
        ↓
HTF liquidity reference
        ↓
M5 raid observation
        ↓
M15 structural evidence
        ↓
M5 displacement / MSS / FVG entry
```

Current implementation xử lý mỗi setup theo một clock duy nhất sau khi raid tạo ra nó. Đây là **vấn đề mới lớn nhất M3**.

Tôi đề xuất model:

```text
LiquidityReference
        ↓
Global RaidEpisode
        ├── M1 observations
        ├── M5 observations
        └── M15 observations

        ↓

SetupCandidate
setup_tf = H1/M15
entry_tf = M5
        ↓
same-TF structure evidence
+ lower-TF entry evidence
```

Không phải ép mọi evidence cùng TF.

### B. Research logging hiện chưa đủ cho triết lý recall-first

Ví dụ policy là shift ≤12 M5 bars.

Nếu true setup shift ở bar 13:

```text
bar 12
→ setup EXPIRED

bar 13
→ actual shift
→ current M3 không còn nhìn setup đó
```

Thì M4 sẽ không dễ trả lời:

> “12 bars có đang làm mình miss setup tốt không?”

Đó chính xác là dữ liệu mình cần để tune parameters và sau này làm ML features. Current code có `RESEARCH_OBSERVATION`, nhưng terminal state bị skip nên chưa đủ.

Tôi muốn:

```text
Trading path:
EXPIRED → stop considering trade

Research path:
continue observing +32/+64 bars
→ late_shift_after_13
→ late_fvg_after_15
→ late_retrace_after_20
→ future MFE/MAE
```

---

## M3.1 cần làm trước M4

Tôi đề xuất **chưa kêu Codex làm M4**, mà patch một lượt M3.1:

`global liquidity take + true RaidEpisode -> decouple setup_tf/entry_tf -> integrate effective swing rank -> stateful FVG reaction/failure -> post-terminal research logging -> MTF tests`

Sau patch đó tôi review lại.

### Tests mới bắt buộc

| Test                                                     | Vì sao                          |
| -------------------------------------------------------- | ------------------------------- |
| M5 sweep H1 reference → M15 vẫn có thể observe same raid | Không để M5 “ăn mất” HTF raid   |
| M5 raid + M15 setup shift + M5 entry FVG                 | Test đúng architecture multi-TF |
| Promoted ITH bị break → SHIFT evidence biết nó là ITH    | LLM phải thấy rank thật         |
| FVG touch bar A → reaction bar B                         | Không miss reaction multi-bar   |
| FVG fully failed before reaction                         | Entry zone lifecycle đúng       |
| Shift xảy ra 1–3 bars sau expiry                         | Research logger vẫn ghi         |
| FVG/retrace xảy ra sau configured window                 | Có data để M4 calibrate window  |

**Đánh giá M3 hiện tại:** core state machine khoảng **75%**, point-in-time/replay tốt, nhưng multi-TF semantics và research recall chưa đủ để tôi cho sang M4. Sau M3.1 nếu sửa đúng các điểm trên thì mới bắt đầu replay XAUUSD có ý nghĩa.

[1]: https://github.com/lengoctuong/ict-trading-agent/blob/main/tests/test_m3_pipeline.py "ict-trading-agent/tests/test_m3_pipeline.py at main · lengoctuong/ict-trading-agent · GitHub"
[2]: https://get.exness.help/hc/en-us/articles/360014390760-What-is-the-default-timezone-set-for-MetaTrader?utm_source=chatgpt.com "What is the default timezone set for MetaTrader?"
