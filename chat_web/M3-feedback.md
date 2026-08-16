# M3.3 feedback and implementation record

> **Implementation update — 2026-08-17:** Bốn mục trong review này đã được
> implement. Shift chấp nhận khoảng cách `0..window` và gắn label
> `SAME_BAR_RAID_SHIFT`; inside-shift FVG neo vào physical
> `first_take_fact_id`; raid extreme dynamic trước SHIFT rồi freeze thành hard
> invalidation tại SHIFT; hai path feature đã đổi thành
> `max_zone_penetration_points` và `max_zone_penetration_fraction`. Regression
> suite có fixture M5 first take/FVG trước M15 same-bar reclaim+shift. M3 được
> freeze để chuyển sang M4.

Check lại bản **M3.2 mới nhất** rồi. Lần này 4 lỗi tôi bắt trước đó đều đã được Codex sửa đúng hướng và có test. `implementation_plan.md` cũng đã chuyển M3 sang trạng thái **M3.2 implemented**.

## Status M3.2

| Status         | Task                                   | Hiểu đơn giản                                                                    | Review                                                                                              | Confidence | Need review          |
| -------------- | -------------------------------------- | -------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------- | ---------: | -------------------- |
| ✅ **DONE**     | Global RaidEpisode                     | Liquidity bị lấy một lần globally, nhiều TF cùng quan sát                        | Đúng                                                                                                |        98% | Không                |
| ✅ **DONE**     | Cross-TF multi-bar reclaim             | M15 breach bar A, bar B reclaim mà không cần breach lại                          | Đã có state `NOT_SEEN → BREACHED → RECLAIMED`, test đúng case này.                                  |        98% | Không                |
| ✅ **DONE**     | Global extreme                         | Giá raid sâu thêm thì extreme được update                                        | Test `99 → 98` và invalidation level update theo episode.                                           |        97% | Không                |
| ✅ **DONE**     | M5 FVG trong M15 shift candle          | Không bỏ cú displacement tạo chính M15 MSS                                       | Đã link `inside_shift_bar`; FVG bị consume trước M15 close thì chỉ research-log, không dùng entry.  |        98% | Không                |
| ✅ **DONE**     | FVG reaction nhiều bar                 | Touch → touch → reaction vẫn được                                                | Test track 2 touches rồi reaction.                                                                  |        96% | Window `3` review M4 |
| ✅ **DONE**     | FVG path logging                       | Giữ penetration, CE, fill, time-in-zone                                          | Đã có touch count, first/max penetration, CE, full fill...                                          |        96% | Không                |
| ✅ **DONE**     | Containing-candle invalidation         | M15 candle mở trước raid nhưng đóng sau raid vẫn được xét                        | Đã sửa causality theo close/availability và có test.                                                |        92% | Xem lưu ý dưới       |
| ✅ **DONE**     | Research after terminal                | Parameter quá strict vẫn có dữ liệu late setup                                   | Giữ nguyên tốt                                                                                      |        98% | Không                |
| 🟠 **NEW**     | **Raid + shift cùng một setup candle** | Một candle có thể vừa sweep liquidity vừa break structure                        | Hiện code bắt `bars_after_raid >= 1`, nên case `0 bar` bị mất.                                      |    **95%** | **Có**               |
| 🟡 **IMPROVE** | Tên ML feature MAE                     | Feature đang gọi `max_adverse_excursion` nhưng thực chất gần với depth xuyên FVG | Rename/define chính xác trước dataset M4                                                            |        95% | Không                |

---

# Vấn đề mới đáng sửa: sweep + shift cùng candle

Code hiện:

```text
if 1 <= bars_after_raid <= window:
    accept shift
```

nên bắt buộc:

```text
raid candle
→ ít nhất 1 candle sau
→ shift
```



Nhưng market hoàn toàn có thể:

```text
M15 candle
↓ wick lấy SSL
↓ displacement mạnh
↓ close xuyên relevant swing high

= liquidity raid + structural shift
  trên cùng một candle
```

Với triết lý **recall-first**, tôi không muốn loại case này.

### Đề xuất

Cho:

```text
0 <= bars_after_raid <= window
```

và label riêng:

```text
SAME_BAR_RAID_SHIFT
```

Không khẳng định đây chắc chắn là CHoCH/MSS tốt.

LLM sau này quyết:

```text
meaningful MSS?
hay oversized/noisy candle?
```

### Còn MTF case quan trọng hơn

Ví dụ chính candle M15 đó:

```text
10:00 ───────────── 10:15 M15

10:03 sweep SSL
10:05 M5 displacement
10:10 M5 FVG
10:15 M15 close xuyên swing
```

Nếu M15 raid chỉ được confirm lúc `10:15`, ta **vẫn phải cho phép M5 FVG 10:10 thuộc cùng raid/shift episode**, vì physical first-take đã xảy ra trước đó.

M3.2 đã có `first_take_fact_id` và episode bắt đầu từ breach, nên data để làm đúng việc này đã tồn tại.

**Tôi đề xuất patch nhỏ M3.3 cho case này trước M4. Confidence 95%.**

---

## Một điểm tôi muốn bạn review: invalidation của candle chứa raid

Current test nói:

```text
M5 raid low = 99.0

cùng M15 candle:
low   = 98.5
close = 98.8

→ INVALIDATED vì close < 99.0
```



Có một ambiguity:

### Cách A — current code

Dùng **extreme đã biết trước khi M15 candle đóng**:

```text
old extreme = 99
M15 close 98.8 < 99
→ invalidate
```

### Cách B — dynamic raid extreme

Khi M15 đóng ta biết:

```text
raid episode extreme = 98.5
close = 98.8 > 98.5
```

→ đây có thể được hiểu là raid **đã extend xuống 98.5 rồi reclaim khỏi extreme**, chưa nhất thiết invalidate.

Tôi hơi nghiêng **B cho detector/research**, vì phù hợp với global RaidEpisode đang continuously update extreme hơn và giảm nguy cơ kill setup quá sớm.

Nhưng đây chính xác là loại **ICT semantic cần bạn review**, không nên để software tự quyết.

Proposal recall-first:

```text
trước SHIFT confirmation:
    raid extreme có thể tiếp tục deepen
    → update dynamic extreme
    → không hard invalidate bởi containing candle

sau SHIFT confirmed:
    freeze hard_invalidation_price
    → setup-TF close beyond frozen extreme kills thesis
```

**Confidence: 82%, cần review.**

Cách này khá tự nhiên:

```text
RAID đang hình thành
→ extreme dynamic

SHIFT xác nhận
→ raid hoàn tất
→ freeze extreme
→ từ đây dùng làm invalidation
```

---

# Logging feature: một chỉnh sửa nhỏ

Code đang lưu:

```text
max_adverse_excursion_before_reaction
```

nhưng calculation thực tế hiện gần với:

```text
distance từ near edge của FVG đến deepest penetration
```

chứ chưa phải MAE của một trade thực sự.

Nên trước M4 tôi đổi tên:

```text
max_zone_penetration_points
max_zone_penetration_fraction
```

Sau này khi có hypothetical entry price mới tính:

```text
MAE
MFE
```

Để dataset ML sau này không bị feature mang tên sai nghĩa.

---

## Kết luận

M3.1 tôi chấm ~90%.

**M3.2 hiện ~96%.**

Tôi chỉ muốn **M3.3 rất nhỏ**:

1. hỗ trợ `same-bar raid + shift`;
2. link entry-TF repricing dựa trên physical first-take trong same setup candle;
3. chốt **dynamic vs frozen raid extreme**;
4. rename penetration feature cho đúng nghĩa.

Sau đó tôi **đồng ý freeze M3 và chuyển M4**. M4 lúc đó tập trung vào real Exness XAUUSD replay, detection recall và calibration `3 / 12-8-4 / 24-16-6` thay vì tiếp tục sửa architecture.
