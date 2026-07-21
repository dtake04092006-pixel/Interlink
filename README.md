# Interlink Discord Bot

Interlink kết hợp Discord bot, OAuth2 và Flask web admin để quản lý danh sách agent, thêm thành viên vào nhiều server và thực hiện một số tác vụ quản trị hàng loạt.

## Tính năng chính

- OAuth2 Discord với scope `identify` và `guilds.join`.
- Lưu access token qua PostgreSQL, JSONBin và file `tokens.json` dự phòng.
- Menu chọn server phân trang tối đa 20 server/trang và giữ lựa chọn khi chuyển trang.
- Web admin sắp xếp riêng account/agent và Discord server bằng kéo-thả.
- Thứ tự server lưu tại `_server_order` được dùng trong các menu `!invite`, `!deploy`, `!kick`, `!create` và `!getid`.
- Web admin quản lý nhiều Discord Owner ID; danh sách lưu tại `_owner_ids` và được áp dụng ngay cho các lệnh Owner.
- `!help` và `/help` dùng chung nội dung hướng dẫn theo nhóm quyền.

> Interlink không lưu mật khẩu Discord. Ứng dụng có lưu access token OAuth trong các backend được cấu hình; hãy bảo vệ biến môi trường, JSONBin và cơ sở dữ liệu như dữ liệu nhạy cảm.

## Lệnh

### Công khai

| Lệnh | Tác dụng |
| --- | --- |
| `!ping` | Kiểm tra độ trễ Discord của bot. |
| `!auth` | Tạo liên kết OAuth cần thiết trước khi thêm người dùng vào server. |
| `!add_me` | Thử thêm chính người gọi vào tất cả server của bot. |
| `!check_token` | Kiểm tra hệ thống có access token của người gọi hay chưa. |
| `!status` | Xem trạng thái bot và các backend lưu trữ. |
| `!help` hoặc `/help` | Mở hướng dẫn chi tiết theo danh mục. |

### Cần quyền Manage Server

| Lệnh | Tác dụng |
| --- | --- |
| `!invitebot <bot_id...>` | Tạo link mời cho một hoặc nhiều bot ID. |

### Chỉ Owner

| Lệnh | Tác dụng |
| --- | --- |
| `!force_add <@user\|user_id>` | Thêm một người đã OAuth vào tất cả server. |
| `!invite <@user\|user_id>` | Chọn nhiều server để thêm một người đã OAuth. |
| `!storage_info` | Xem trạng thái và số bản ghi của storage. |
| `!migrate_tokens <source> <target>` | Sao chép token giữa `db`, `jsonbin`, `json`. |
| `!roster` | Xem roster agent theo trang. |
| `!roster_move <@user\|user_id> <position>` | Đổi vị trí 1-based trong roster. |
| `!remove <@user\|user_id>` | Xóa dữ liệu agent khỏi các storage. |
| `!deploy` | Chọn nhiều agent và nhiều server để thêm hàng loạt. |
| `!getid` | Tìm ID text channel theo tên trong các server đã chọn. |
| `!kick` | Kick nhiều agent khỏi các server đã chọn. |
| `!setupadmin <@member\|member_id>` | Cấp role Administrator trên nhiều server sau bước xác nhận. |
| `!create` | Tạo 1–5 text channel trong nhiều server. |

Chạy `!help` hoặc `/help` để xem quyền, tham số, ví dụ, điều kiện tiên quyết và cảnh báo chi tiết của từng lệnh.

## Web admin

- Đăng nhập tại `/admin/login` bằng `ADMIN_PASSWORD`.
- `/admin/dashboard` có hai danh sách kéo-thả độc lập và một khu vực quản lý Owner ID:
  - Account/agent lưu qua API cũ `/admin/api/reorder` vào `_roster_order`.
  - Discord server lưu qua `/admin/api/reorder-servers` vào `_server_order`.
  - Có thể thêm nhiều Discord user ID làm Owner, xóa ID không còn dùng rồi lưu qua `/admin/api/owner-ids` vào `_owner_ids`. Hệ thống không cho lưu danh sách rỗng để tránh tự khóa quyền quản trị bot.
- Endpoint server chỉ chấp nhận ID server mà bot đang tham gia và từ chối lưu nếu bot chưa sẵn sàng hoặc JSONBin không khả dụng.

## Biến môi trường

| Biến | Bắt buộc | Mô tả |
| --- | --- | --- |
| `DISCORD_TOKEN` | Có | Bot token Discord. |
| `DISCORD_CLIENT_ID` | Có | Application client ID. |
| `DISCORD_CLIENT_SECRET` | Có | OAuth client secret. |
| `RENDER_EXTERNAL_URL` | Khi deploy | Base URL dùng để tạo OAuth callback. |
| `PORT` | Không | Cổng Flask, mặc định `5000`. |
| `FLASK_SECRET_KEY` | Khuyến nghị | Khóa ký session Flask. |
| `ADMIN_PASSWORD` | Khuyến nghị | Mật khẩu web admin. |
| `DATABASE_URL` | Không | PostgreSQL connection URL. |
| `JSONBIN_API_KEY` | Cho JSONBin | JSONBin master/access key. |
| `JSONBIN_BIN_ID` | Cho JSONBin | ID bin chứa record. |

Không commit `.env`, token, database URL, JSONBin key hoặc `tokens.json`.

## Chạy dự án

1. Cài Python 3.10+ và dependency: `python -m pip install -r requirements.txt`.
2. Tạo `.env` với các biến cần thiết.
3. Cấu hình OAuth redirect URI thành `<RENDER_EXTERNAL_URL>/callback` trong Discord Developer Portal.
4. Chạy `python Interlink.py`.

Flask phục vụ `/health` để kiểm tra trạng thái triển khai. Không dùng backend production hoặc secret thật trong unit test.

## Kiểm thử

```bash
python -m unittest discover -s tests -v
python -m compileall -q .
```
