# local-trader

Bot phá đỉnh/đáy khung 15 phút, chạy trên máy bạn. Dữ liệu nến và funding lưu trong SQLite. Một vị thế mỗi lần, khối lượng bằng một phần vốn, cắt lỗ cứng, và dừng mở lệnh mới khi lỗ trong ngày chạm ngưỡng. Lãi ngày đạt 50 USD thì đóng vị thế, tính ngày mới từ lúc đó và được vào lệnh tiếp. Lỗ ngày đạt 20 USD thì nghỉ mở lệnh mới 4 giờ. Lệnh `run` in log này ra terminal một lần mỗi 15 phút.

Lệnh demo chỉ đi tới `https://demo-fapi.binance.com`. Tool từ chối host của Binance thật.

Cách cài và kiểm thử nằm trong [docs/kiem-thu.md](docs/kiem-thu.md).
