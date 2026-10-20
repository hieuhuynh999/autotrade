# Kiểm thử local-trader

Tool này kiểm tra luật phá đỉnh/đáy trên nến 15 phút sau phí. Nó không dự báo lãi. Phần lớn lệnh dưới đây chạy trên máy bạn. Chỉ mục demo mới gửi lệnh, và chỉ gửi lên OKX Demo Trading.

Funding là phí của hợp đồng vĩnh cửu, nên sàn để test là OKX Demo Trading, hợp đồng BTC-USDT-SWAP, không phải Spot.

## 1. Cài

Mở PowerShell:

```powershell
cd $env:USERPROFILE\local-trader
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

Nếu `py` không chạy, thay bằng `python`. File `.env` mặc định là `MODE=paper`. Giữ nguyên cho đến khi các mục paper đều ổn.

## 2. Test tự động, không cần mạng và không cần API key

```powershell
pytest -q
```

Kỳ vọng: dòng cuối là `25 passed`. Bộ này kiểm tra:

- nến đang chạy bị loại, tín hiệu chỉ tính trên nến đã đóng
- phá đỉnh hoặc phá đáy 24 nến, chặn khi funding ngược chiều
- chỉ một vị thế, không nhồi thêm lệnh
- cắt lỗ cứng, và không đảo chiều ngay trên cùng nến đó
- lỗ trong ngày vượt ngưỡng thì dừng mở lệnh mới
- lãi ngày đạt 50 USD thì chốt vị thế, tính ngày mới từ lúc đó và được vào lệnh ở nến sau
- lỗ ngày đạt 20 USD thì nghỉ mở lệnh mới 4 giờ, rồi được vào lại
- sang ngày UTC mới thì được phép chạy lại
- vòng khứ hồi có trừ phí
- dữ liệu ghi SQLite không bị nhân đôi
- mọi URL ký đều là `openapi.okx.com` và có header demo, lệnh cắt lỗ đi vào `/api/v5/trade/order-algo`
- `LEVERAGE` khác 1 và `MODE` lạ bị từ chối

Nếu một test fail, dừng lại. Chưa chạy lệnh demo.

## 3. Kéo dữ liệu demo về máy

Vẫn để `MODE=paper`. Không cần API key. Máy cần ra được internet.

```powershell
python -m local_trader sync
python -m local_trader status
```

Kỳ vọng của `sync`: một dòng kiểu `đã lưu ... nến 15m và ... điểm funding cho BTCUSDT`. Khoảng 8.640 nến là 90 ngày. Số funding phải lớn hơn 0. Nến 15 phút hiện tại vẫn được lưu, nhưng backtest và tín hiệu bỏ nến đó cho đến khi nó đóng.

`status` in ngưỡng:

- vào lệnh khi đóng cửa phá đỉnh hoặc phá đáy 24 nến 15 phút, và funding không ngược chiều
- thoát long khi đóng cửa thủng đáy 24 nến, thoát short khi đóng cửa vượt đỉnh 24 nến
- khối lượng 10% vốn
- cắt lỗ 2%
- dừng bot khi lỗ trong ngày đạt 3%
- chốt lãi khi lãi ngày đạt 50 USD, rồi tính lại từ lúc chốt
- lỗ ngày 20 USD thì nghỉ mở lệnh 4 giờ

File dữ liệu nằm ở `data\trader.db`.

## 4. Xem tín hiệu, chưa đặt lệnh

```powershell
python -m local_trader signal
```

Kỳ vọng: dòng phá đỉnh/đáy, dòng funding, rồi một trong các câu:

- `tín hiệu vào lệnh: long`
- `tín hiệu vào lệnh: short`
- `tín hiệu vào lệnh: không — ...`

Câu cuối phải nói lệnh mới khi đóng cửa phá đỉnh hoặc phá đáy 24 nến 15 phút, và chỉ dùng nến đã đóng.

## 5. Backtest trên dữ liệu vừa kéo

```powershell
python -m local_trader backtest
```

Kỳ vọng: vốn đầu 10000, vốn cuối, số lệnh, số thắng, tổng phí, drawdown. Dòng nhắc `đây là kiểm tra cơ chế và phí, không phải dự báo lãi.`

Đọc kết quả theo cách này:

- tổng phí lớn so với lãi thì luật đang vào ra quá dày
- drawdown là mức vốn tụt sâu nhất so với đỉnh trong mẫu
- lý do lệnh thường là `cắt lỗ cứng`, `thoát vì phá biên ngược của 24 nến`, hoặc `đóng cuối kỳ backtest`

Backtest không đụng tài khoản paper và không gửi lệnh.

## 6. Chạy một vòng paper

```powershell
python -m local_trader once
python -m local_trader status
python -m local_trader once
```

Lần `once` đầu xử lý nến 15 phút đã đóng gần nhất.

- Nếu đủ điều kiện: dòng `vào long khi phá đỉnh/đáy` hoặc `vào short khi phá đỉnh/đáy`, có khối lượng, giá, phí. `status` cho thấy đúng một vị thế và giá cắt lỗ.
- Nếu chưa đủ: một lý do, ví dụ chưa phá đỉnh/đáy, hoặc funding ngược chiều. `status` vẫn là `không có vị thế`.

Lần `once` thứ hai, cùng một nến 15 phút, phải có `đã xử lý nến này, chờ nến 15m đóng tiếp`. Bot không được vào thêm vị thế.

Muốn xem vòng sau, đợi nến 15 phút đóng rồi chạy `once` lại. Hoặc để bot chờ hộ:

```powershell
python -m local_trader run
```

Dừng bằng `Ctrl+C`. Mỗi vòng paper chỉ hành động khi có nến 15 phút mới. Cứ 15 phút, terminal in một log: số lệnh lãi/lỗ trong phiên, lãi lỗ lệnh đang mở, còn bao nhiêu USDT thì đủ target 50 hoặc chạm lỗ 20, và tổng tiền còn lại.

Reset tài khoản giả lập, không xóa nến đã lưu:

```powershell
python -m local_trader reset-paper
```

Kỳ vọng: `đã reset paper. Vốn 10000.00 USDT. Không có vị thế.`

## 7. Đặt lệnh trên OKX Demo Trading

Chỉ làm bước này sau khi mục 2 đến mục 6 chạy đúng.

1. Mở https://www.okx.com và đăng nhập.
2. Vào Trade, rồi Demo Trading. Chuyển position mode về **net** (không dùng hedge).
3. Tạo API key trong mục Demo Trading API. Bật quyền trade. Không tạo key ở mục API của tài khoản thật.
4. Sửa `.env`:

```text
MODE=demo
OKX_API_KEY=key_demo
OKX_API_SECRET=secret_demo
OKX_API_PASSPHRASE=passphrase_demo
```

6. Chạy:

```powershell
python -m local_trader once
```

Kỳ vọng:

- bot kéo nến mới, đặt đòn bẩy 1
- nếu có tín hiệu: một lệnh market, rồi một lệnh conditional cắt lỗ trên demo
- dòng trạng thái ghi `demo`, một vị thế, giá cắt lỗ
- nếu không có tín hiệu: lý do bỏ qua, không có lệnh mới

7. Mở OKX Demo Trading, mục hợp đồng vĩnh cửu, kiểm tra:

- chỉ một vị thế BTCUSDT
- đòn bẩy 1
- có lệnh điều kiện cắt lỗ
- không có vị thế thứ hai

8. Chạy lại `python -m local_trader once` trong cùng giờ. Kỳ vọng có câu đã xử lý nến này, và số vị thế trên web không tăng.

9. Để cắt lỗ được kiểm tra giữa giờ (theo mark price), chạy `python -m local_trader run`. Vòng mặc định 60 giây. Lệnh cắt lỗ trên OKX demo vẫn còn tác dụng nếu bạn tắt máy.

Đưa `.env` về `MODE=paper` khi test xong nếu bạn chưa muốn bot tự đặt lệnh.

## 8. Checklist

Đạt khi tất cả dòng sau đúng:

- `pytest -q` toàn passed
- `sync` lưu được cả nến và funding
- `signal` nói rõ là chỉ dùng nến đã đóng
- `backtest` in phí và số lệnh
- paper `once` lần hai không mở thêm vị thế
- file `.env` không bị commit (`git status` không thấy `.env`)
- lúc `MODE=demo`, vị thế trên web demo khớp với `status`

## 9. Lỗi hay gặp

| Hiện tượng | Việc làm |
|---|---|
| `chưa đủ nến đóng` | Chạy `python -m local_trader sync` |
| `MODE=demo cần OKX_API_KEY` | Điền key tạo trong OKX Demo Trading vào `.env` |
| `50111` hoặc API key không hợp lệ | Key không phải key demo, hoặc thiếu passphrase |
| `hedge mode` | Trên OKX demo, đổi sang net mode |
| `khối lượng nhỏ hơn mức tối thiểu` | Tăng `PAPER_CASH` hoặc `POSITION_PCT` trong `.env`, rồi `reset-paper` |
| `LEVERAGE chỉ được bằng 1` | Giữ `LEVERAGE=1` |
| Lỗi mạng lúc `run` | Bot in lỗi và thử lại vòng sau. `once` thì dừng để bạn thấy lỗi |

Sửa ngưỡng trong `.env` rồi chạy lại. `LOOKBACK`, `ENTRY_THRESHOLD`, `STOP_PCT`, `DAILY_LOSS_PCT` đổi ngay từ vòng sau. `PAPER_CASH` chỉ áp dụng cho tài khoản paper mới, nên sau khi đổi thì chạy `reset-paper`.
