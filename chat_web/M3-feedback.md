# M3.2 feedback and implementation record

> **Implementation update — 2026-08-17:** Bốn mục M3.2 trong review này đã
> được implement. `RaidEpisode` bắt đầu từ first breach và giữ state
> `BREACHED -> RECLAIMED` riêng từng TF; inside-shift M5 FVG chỉ được promote ở
> setup-TF close nếu chưa fully consumed/failed; containing candle dùng
> close-time causality để invalidation; mọi touch bar cập nhật path aggregates
> phục vụ research. Regression suite có fixture riêng cho toàn bộ case bắt buộc.

Review ban đầu: **M3.1 sửa đúng phần lớn các issue lần trước**: global `RaidEpisode`, tách H1/M15 setup với M5 entry, effective swing rank, FVG touch→reaction nhiều bar, FVG failure và post-terminal research logging đều đã được implement/test. Docs hiện cũng đánh M3.1 là implemented.

Nhưng tôi phát hiện **2 vấn đề ICT critical mới + 2 improvement quan trọng**. Tôi chưa cho sang M4 trước khi patch chúng.

## M3.1 review

| Status              | Vấn đề / task                                | Hiểu đơn giản                                                                         | Cách dev đề xuất                                                                                                                                                | Confidence | Need review          |
| ------------------- | -------------------------------------------- | ------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------: | -------------------- |
| **DONE**            | Global liquidity take                        | M5 lấy H1 liquidity thì level đã TAKEN toàn cục                                       | Giữ hiện tại: 1 global `RaidEpisode`, nhiều TF observation                                                                                                      |        98% | Không                |
| **DONE**            | Setup TF ≠ Entry TF                          | M15/H1 là setup, M5 là entry                                                          | Hiện tạo H1 + M15 setup path riêng, `entry_timeframe=M5`                                                                                                        |        98% | Không                |
| **DONE**            | Structure cross-TF                           | M5 close H1 swing không phải H1 BOS                                                   | Same-TF mới eligible shift; cross-TF giữ raw evidence                                                                                                           |        98% | Không                |
| **DONE**            | Swing hierarchy                              | LLM biết break đó là STH hay ITH/LTH                                                  | Effective rank hiện được resolve tại thời điểm break và đưa vào SHIFT evidence                                                                                  |        95% | Không                |
| **DONE**            | FVG multi-bar reaction                       | Touch bar A, reaction bar B vẫn được                                                  | Có `TOUCHED -> REACTED/FAILED/EXPIRED`, reaction window=3                                                                                                       |        93% | Review `3 bars` ở M4 |
| **DONE**            | Post-expiry logging                          | Setup hết hạn vẫn quan sát xem event đến muộn không                                   | Có `LATE_SHIFT`, `LATE_FVG`, `LATE_RETRACE` và research horizon                                                                                                 |        97% | Không                |
| **CRITICAL NEW**    | **M5 FVG nằm bên trong M15/H1 shift candle** | FVG tạo ra chính cú MSS có thể xuất hiện **trước lúc candle M15 đóng**                | Cho phép entry-TF displacement/FVG nằm trong setup-TF shift candle, không chỉ sau `shift.available_at`; chỉ dùng nếu FVG vẫn valid/fresh khi shift được confirm |    **98%** | **Có**               |
| **CRITICAL NEW**    | **Cross-TF multi-bar raid/reclaim**          | M5 take trước, M15 close dưới level rồi candle M15 sau reclaim lên — hiện có thể miss | `RaidEpisode` phải track breach/extreme/reclaim theo từng TF qua nhiều bars, độc lập với global `TAKEN`                                                         |    **98%** | **Có**               |
| **HIGH**            | Invalidation candle chứa raid                | M15 candle đang mở lúc M5 raid có thể đóng phá setup, nhưng code có thể skip nó       | Dùng availability/close-time causality thay vì loại toàn bộ candle có `open_time <= raid.created_at`                                                            |        94% | Có                   |
| **HIGH / research** | FVG touch path logging                       | FVG bị touch 3 lần trước khi reaction nhưng hiện raw path chưa đủ chi tiết            | Log mỗi zone observation hoặc aggregate `touch_count`, max penetration, CE reached, time in zone, etc.                                                          |        97% | Không                |

---

# Critical #1 — tôi nghĩ rất quan trọng

Code hiện yêu cầu:

```text
M5 displacement.occurred_at >= M15 shift.available_at
```

Nếu displacement xảy ra trước lúc M15 candle đóng thì `_repricing_lag()` trả `None`.

Nhưng market có thể rất tự nhiên như:

```text
10:00 ───────────── 10:15
       M15 shift candle

10:05 liquidity raid
10:08 M5 displacement
10:10 M5 FVG confirmed
10:15 M15 closes → structural shift confirmed
```

Cú:

```text
10:08 displacement
→ 10:10 FVG
```

có thể chính là repricing **tạo nên M15 shift**.

Current M3 lại chỉ bắt kiểu:

```text
10:15 M15 shift confirmed
↓
10:20+ M5 displacement/FVG
```

=> có nguy cơ bỏ rất nhiều ICT sequence tốt.

### Tôi đề xuất

Định nghĩa relation:

```text
M5 FVG relation to M15 shift:

1. INSIDE_SHIFT_BAR
2. AFTER_SHIFT_CONFIRMATION
```

Cả hai đều được candidate.

Nhưng nếu là `INSIDE_SHIFT_BAR`, tại thời điểm M15 shift được confirmed phải kiểm tra:

```text
FVG đã available
AND
FVG chưa failed/fully consumed
```

Ví dụ:

```text
10:10 FVG xuất hiện
10:12 FVG bị fill hoàn toàn
10:15 M15 shift confirm

→ giữ làm historical evidence
→ KHÔNG coi là current entry zone
```

Đây vẫn point-in-time safe: **10:15 mới đưa setup đi tiếp**, ta chỉ nhìn lại những facts đã xảy ra trước 10:15 chứ không giả vờ biết M15 shift từ 10:05.

---

# Critical #2 — multi-TF reclaim vẫn chưa hoàn chỉnh

Test hiện tại cover:

```text
M5 sweep H1 level
+
M15 candle cũng breach + reclaim cùng candle
→ M15 observes same RaidEpisode
```

và test này pass theo code fixture.

Nhưng case quan trọng khác:

```text
H1 SSL = 3300

M5:
3298 → first take

M15 candle A:
low = 3295
close = 3298
→ chưa reclaim

M15 candle B:
low = 3297
close = 3304
→ reclaim
```

hoặc thậm chí:

```text
M15 B:
low = 3301
close = 3304
```

Tức B **không re-breach**, nó chỉ reclaim level mà candle trước đã breach.

Current `_observe_existing_episodes()` yêu cầu mỗi observation bar:

```text
breached == True
AND
reclaimed == True
```

trên **cùng bar**.

=> case multi-bar M15 phía trên có thể mất.

Nghiêm trọng hơn: trong lúc chờ reclaim:

```text
M5 first extreme = 3298
sau đó market xuống 3293
sau đó mới reclaim 3300
```

`RaidEpisode.extreme` phải trở thành:

```text
3293
```

chứ không giữ `3298`.

### Model nên là

```text
Global RaidEpisode
reference = 3300
first_take = 3298
extreme = continuously updated

M15 observation state:
NOT_SEEN
→ BREACHED
→ RECLAIMED

M5 observation state:
BREACHED
→ RECLAIMED

H1 observation state:
...
```

Quan trọng:

> **Global TAKEN chỉ ngăn tạo một liquidity event mới. Nó không được ngăn việc tiếp tục quan sát episode đang diễn ra.**

Đây đúng triết lý mình đã chốt trước đó.

---

# #3 Invalidation có một edge case

Hiện code không invalidate nếu:

```python
bar.open_time <= setup.created_at
```



Ví dụ:

```text
10:00 M15 candle opens

10:05 M5 raid
10:10 raid confirmed

10:15 M15 closes mạnh dưới raid extreme
```

M15 candle mở trước raid:

```text
10:00 < 10:05
```

nên hiện có khả năng bị skip.

Nhưng tại **10:15**, setup đã tồn tại và M15 close là thông tin mới hợp lệ.

Tôi muốn condition dựa vào:

```text
bar.close_time > setup.available_at
```

chứ không dựa vào lúc bar mở.

---

# #4 Logging FVG cần thêm cho ML/research

M3.1 đã tiến bộ nhiều:

```text
TOUCHED
→ REACTED
/ FAILED
/ EXPIRED
```



Nhưng nếu:

```text
touch #1 = 20%
touch #2 = 60%
touch #3 = CE
touch #4 = 85%
→ reaction
```

ta rất muốn giữ path đó.

Tôi muốn cuối M3 có features:

```text
touch_count
first_touch_at
last_touch_at

first_penetration
max_penetration

ce_reached
full_fill

bars_since_first_touch
bars_inside_zone

max_adverse_excursion_before_reaction
```

Không cần dùng chúng để filter.

**Chỉ log.**

Sau M4/M5 chúng cực kỳ hữu ích để tìm xem:

```text
20% penetration tốt hơn 80%?
CE touch có edge?
multiple-touch FVG yếu đi?
reaction sau 1 bar vs 3 bars?
```

và sau này đưa trực tiếp thành ML features.

---

# M3.2 tôi đề xuất Codex làm

| Priority | Task                                      | Answer đơn giản                                                          | Dev                                                                                       | Confidence | Need review |
| -------- | ----------------------------------------- | ------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------- | ---------: | ----------- |
| **P0**   | Cross-TF repricing/FVG                    | Cho phép M5 FVG nằm **trong M15/H1 shift candle**                        | Link bằng temporal containment + future FVG; tại shift confirmation check zone vẫn usable |        98% | **Có**      |
| **P0**   | Raid observation state                    | Multi-bar reclaim phải hoạt động cả cross-TF                             | Track per-TF `BREACHED → RECLAIMED`, update global extreme mọi bar trong episode          |        98% | **Có**      |
| **P1**   | Setup invalidation timing                 | Candle chứa raid được phép invalidate khi nó đóng sau setup availability | sửa causality check                                                                       |        94% | Có          |
| **P1**   | FVG observation logging                   | Không mất penetration/touch path                                         | append raw observation per touch/bar + derived aggregates                                 |        97% | Không       |
| **Test** | M5 FVG inside M15 shift candle            | Bắt bug #1                                                               | fixture M5 displacement/FVG trước M15 close                                               |        99% | Không       |
| **Test** | M5 take → M15 breach → next M15 reclaim   | Bắt bug #2                                                               | không yêu cầu candle reclaim re-breach                                                    |        99% | Không       |
| **Test** | Raid extreme deepens before reclaim       | Invalidation level phải update                                           | 3298→3293→reclaim                                                                         |        99% | Không       |
| **Test** | M15 containing raid closes beyond extreme | Bắt invalidation edge                                                    | ensure setup INVALIDATED                                                                  |        98% | Không       |

## Kết luận

M3 trước: **~75%**

M3.1 hiện tại: **~90%**

Tôi **chưa sang M4** vì hai P0 phía trên liên quan trực tiếp đến *“có detect đủ đúng ICT sequence hay không”*. Nếu replay trước khi sửa, M4 có thể báo thống kê rất đẹp nhưng dataset setup đã bị missing một nhóm MTF setup ngay từ detector.

Sau **M3.2 này**, tôi nghĩ đủ để freeze detection semantics và chuyển sang **M4 real XAU replay + parameter/recall analysis**.
