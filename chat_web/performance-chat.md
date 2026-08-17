• Đã dừng toàn bộ benchmark. Kết quả hiện tại:

   Test                                Input                     Thời gian                                RAM
  ━━━━━━━━━━━━━━━━━━━━━━━━━  ━━━━━━━━━━━━━━━━  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   Trước tối ưu               37.667 records       hơn 30 phút, không xong               ~200 MB lúc quan sát
  ─────────────────────────  ────────────────  ────────────────────────────  ─────────────────────────────────
   One-day sau các fix đầu       398 records                      8,9 giây                                 ổn
  ─────────────────────────  ────────────────  ────────────────────────────  ─────────────────────────────────
   Week hoàn thành             1.991 records                    139,6 giây                     output rất lớn
  ─────────────────────────  ────────────────  ────────────────────────────  ─────────────────────────────────
   Week sau fix mới nhất       1.991 records    dừng ở 100 giây, chưa xong    585 MB working / 1,1 GB private

  Week hoàn thành đã sinh:

  - 24.690 raid_observation
  - 15.865 research_observation
  - 1.260 setups
  - 17.035 near-misses

  Kết luận: các lỗi quét O(N²), deep-copy và lookup đã giảm nhiều, nhưng M4.2 vẫn chưa đạt hiệu suất để chạy full. Vấn đề còn lại là event explosion và giữ toàn bộ audit/Pydantic objects trong RAM. Chạy full
  lúc này có thể vẫn mất hàng chục phút và vượt RAM.

  Hướng sửa tiếp theo:

  - Stream audit events trực tiếp xuống JSONL thay vì giữ hết trong RAM.
  - Tính summary/distribution tăng dần, không đợi tạo M4ReplayResult khổng lồ.
  - Giữ state đang active trong RAM; historical facts chỉ lưu ID/index.
  - Thêm progress mỗi 250 records và đo riêng acquisition/replay/export.
  - Chỉ chạy full khi week benchmark đạt khoảng ≤30 giây, RAM <400 MB.

  Hiện không còn process benchmark nào chạy.

# ==========

• Ừ, m nói đúng: tao mới xử lý triệu chứng trước, chưa đánh thẳng vào root.

  Root không phải “ICT đa khung vốn quá nặng”. Với 37.667 bars, một ICT scanner thiết kế đúng phải chạy được trên máy này. Root là cách biểu diễn và duyệt state hiện tại đang tạo công việc thừa gần bậc hai.

  Cụ thể hiện tại:

  - Replay chạy single-thread, tuần tự theo close_time để bảo đảm causal.
  - Mỗi bar lại duyệt nhiều reference, raid episode và setup đã tích lũy.
  - Một raid đang BREACHED sinh RAID_OBSERVATION ở mọi bar tiếp theo, kể cả khi:
      - state không đổi;
      - extreme không đổi;
      - chưa reclaim.

  - Mỗi observation lại tạo evidence transition cho các setup liên quan.
  - Mỗi raid tạo hai setup M15/H1.
  - Swing hierarchy hiện còn tính lại từ toàn bộ swing history thay vì cập nhật từ swing mới.
  - Reference detector quét danh sách level thay vì query các level nằm trong range giá của bar.

  Số liệu chứng minh:

  - 1 ngày: 398 input records → hơn 2.000 raid observations.
  - 1 tuần: 1.991 input records → 24.690 raid observations, 15.865 research observations, 1.260 setups.
  - Input tăng khoảng 5 lần nhưng runtime tăng từ 8,9 lên 139,6 giây, tức khoảng 15,7 lần. Ước lượng thực nghiệm gần N^1.7, không tuyến tính.
  - Nếu giữ thiết kế này, full data có thể tạo hàng trăm nghìn đến cả triệu objects. Tối ưu copy chỉ làm mỗi vòng lặp rẻ hơn, không giải quyết số vòng lặp.

  Đây không phải bài toán search space ICT thực sự lớn. Không có hàng triệu tổ hợp BOS/FVG cần brute-force. Chính implementation đang tự tạo search space và event thừa.

  Hướng sửa đúng

  1. Raid episode chỉ phát update khi có thông tin mới:
      - NOT_SEEN → BREACHED
      - xuất hiện extreme mới;
      - BREACHED → RECLAIMED

     Bar không đổi state/extreme thì không tạo fact, update hay evidence transition. Việc này vẫn giữ nguyên toàn bộ thông tin có ý nghĩa.

  2. Không dùng SETUP → cùng SETUP status chỉ để gắn thêm evidence.
      - Transition chỉ dành cho đổi state.
      - Evidence bổ sung lưu thành edge/link append-only riêng.
      - Tránh evidence list ngày càng dài rồi bị copy lại.

  3. Swing hierarchy chạy incremental:
      - Giữ ba swing gần nhất theo (TF, side, rank).
      - Mỗi swing mới xử lý O(1).
      - Không scan lại toàn bộ swing history mỗi bar.

  4. Active reference dùng price index:
      - Buy-side và sell-side levels được giữ trong cấu trúc sắp xếp theo giá.
      - Với [low, high] của bar, chỉ query level thực sự bị chạm.
      - Từ O(bars × tất cả levels) thành gần O(bars × log(levels) + số hit thật).

  5. Setup dùng scheduler theo state/timeframe/deadline:
      - Bar M5 chỉ xử lý setup cần M5.
      - Setup hết shift/FVG/terminal window bị loại khỏi active queue.
      - Không duyệt toàn bộ setup lịch sử.

  6. Cuối cùng mới stream audit xuống disk. Streaming giải quyết RAM, nhưng chỉ có ý nghĩa sau khi giảm số event thừa.

  Độ phức tạp mục tiêu là gần:

  O(N log R + K)

  Trong đó:

  - N: số bars;
  - R: số active reference levels;
  - K: số event có ý nghĩa thật.

  Không nên parallel core replay lúc này. Các timeframe dùng chung raid/setup state và phải giữ thứ tự causal; chạy song song sẽ thêm race condition và RAM mà không chữa thuật toán. Chỉ nên parallel phần
  outcome/chart analysis sau khi replay đã tạo event stream, hoặc chạy nhiều symbol/experiment độc lập.

  Bước tiếp theo đúng là redesign ba phần đầu: state-change-only raid updates, evidence links riêng, incremental swing/reference indexes. Không chạy thêm full benchmark cho tới khi week sample xuống dưới
  khoảng 30 giây và số events không còn tăng vô lý.