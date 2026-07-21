"""Pure helpers and shared command documentation for Interlink.

This module intentionally has no Discord, Flask, storage, or network imports so
its pagination and ordering rules can be tested without production services.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any, TypeVar


T = TypeVar("T")

SERVER_PAGE_SIZE = 20
AGENT_PAGE_SIZE = 25
SERVER_ORDER_KEY = "_server_order"
DISABLED_SERVER_IDS_KEY = "_disabled_server_ids"
OWNER_IDS_KEY = "_owner_ids"


def paginate_items(items: Sequence[T] | Iterable[T], page_size: int = SERVER_PAGE_SIZE) -> list[list[T]]:
    """Return concrete pages without inventing an empty page."""
    if page_size <= 0:
        raise ValueError("page_size must be greater than zero")
    values = list(items)
    return [values[index:index + page_size] for index in range(0, len(values), page_size)]


def normalize_snowflake(value: Any) -> str | None:
    """Normalize a Discord snowflake to a numeric string, or reject it."""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    normalized = str(value).strip()
    return normalized if normalized.isdigit() else None


def validate_server_order(order: Any, valid_ids: Iterable[Any]) -> list[str]:
    """Validate an admin-submitted server order and normalize IDs to strings."""
    if not isinstance(order, list):
        raise ValueError("'order' must be a list")

    allowed = {normalize_snowflake(value) for value in valid_ids}
    allowed.discard(None)
    normalized_order: list[str] = []
    seen: set[str] = set()

    for value in order:
        guild_id = normalize_snowflake(value)
        if guild_id is None:
            raise ValueError("order contains an invalid server ID")
        if guild_id in seen:
            raise ValueError("order contains duplicate server IDs")
        if guild_id not in allowed:
            raise ValueError(f"unknown server ID: {guild_id}")
        normalized_order.append(guild_id)
        seen.add(guild_id)

    return normalized_order


def reconcile_disabled_server_ids(disabled_ids: Any, valid_ids: Iterable[Any]) -> list[str]:
    """Normalize stored disabled IDs while ignoring invalid, duplicate, and stale entries."""
    allowed = {normalize_snowflake(value) for value in valid_ids}
    allowed.discard(None)
    candidates = disabled_ids if isinstance(disabled_ids, (list, tuple, set)) else ()
    normalized: list[str] = []
    seen: set[str] = set()

    for value in candidates:
        guild_id = normalize_snowflake(value)
        if guild_id is None or guild_id in seen or guild_id not in allowed:
            continue
        normalized.append(guild_id)
        seen.add(guild_id)

    return normalized


def partition_guilds(guilds: Iterable[T], disabled_ids: Any) -> tuple[list[T], list[T]]:
    """Split guilds into enabled and disabled lists without changing their order."""
    guild_list = list(guilds)
    guild_ids = (getattr(guild, "id", None) for guild in guild_list)
    disabled = set(reconcile_disabled_server_ids(disabled_ids, guild_ids))
    enabled_guilds: list[T] = []
    disabled_guilds: list[T] = []
    for guild in guild_list:
        guild_id = normalize_snowflake(getattr(guild, "id", None))
        (disabled_guilds if guild_id in disabled else enabled_guilds).append(guild)
    return enabled_guilds, disabled_guilds


def validate_owner_ids(owner_ids: Any) -> list[str]:
    """Validate a non-empty, duplicate-free list of Discord user snowflakes."""
    if not isinstance(owner_ids, list):
        raise ValueError("'owner_ids' must be a list")
    if not owner_ids:
        raise ValueError("at least one owner ID is required")

    normalized_owner_ids: list[str] = []
    seen: set[str] = set()
    for value in owner_ids:
        owner_id = normalize_snowflake(value)
        if owner_id is None:
            raise ValueError("owner_ids contains an invalid Discord user ID")

        numeric_id = int(owner_id)
        if numeric_id <= 0 or numeric_id >= 2**64:
            raise ValueError("owner_ids contains an invalid Discord user ID")
        owner_id = str(numeric_id)

        if owner_id in seen:
            raise ValueError("owner_ids contains duplicate Discord user IDs")
        normalized_owner_ids.append(owner_id)
        seen.add(owner_id)

    return normalized_owner_ids


def _guild_join_sort_key(guild: Any) -> tuple[int, float, int]:
    """Sort joined guilds oldest-first, with a deterministic ID fallback."""
    guild_id = int(getattr(guild, "id", 0))
    member = getattr(guild, "me", None)
    joined_at = getattr(member, "joined_at", None)
    if joined_at is None:
        return (1, 0.0, guild_id)

    try:
        joined_value = float(joined_at.timestamp())
    except (AttributeError, TypeError, ValueError, OverflowError):
        try:
            joined_value = float(joined_at)
        except (TypeError, ValueError, OverflowError):
            joined_value = 0.0
    return (0, joined_value, guild_id)


def reconcile_guild_order(guilds: Iterable[T], stored_order: Any) -> list[T]:
    """Apply a stored order, ignore stale entries, then append new guilds stably."""
    guild_list = list(guilds)
    guild_by_id: dict[str, T] = {}
    for guild in guild_list:
        guild_id = normalize_snowflake(getattr(guild, "id", None))
        if guild_id is not None and guild_id not in guild_by_id:
            guild_by_id[guild_id] = guild

    ordered: list[T] = []
    seen: set[str] = set()
    candidates = stored_order if isinstance(stored_order, (list, tuple)) else ()
    for value in candidates:
        guild_id = normalize_snowflake(value)
        if guild_id is None or guild_id in seen or guild_id not in guild_by_id:
            continue
        ordered.append(guild_by_id[guild_id])
        seen.add(guild_id)

    remaining = [
        guild for guild_id, guild in guild_by_id.items()
        if guild_id not in seen
    ]
    remaining.sort(key=_guild_join_sort_key)
    ordered.extend(remaining)
    return ordered


def replace_page_selection(
    selected_ids: Iterable[int],
    page_ids: Iterable[int],
    submitted_ids: Iterable[int],
) -> set[int]:
    """Replace only the current page's selections while preserving other pages."""
    updated = set(selected_ids)
    updated.difference_update(page_ids)
    updated.update(submitted_ids)
    return updated


HELP_CATEGORY_META = {
    "overview": ("Hướng dẫn sử dụng", "📘"),
    "public": ("Lệnh công khai", "🌐"),
    "manage": ("Lệnh Quản lý Server", "🛡️"),
    "owner": ("Lệnh Owner", "👑"),
}


HELP_COMMANDS = (
    {
        "name": "ping",
        "category": "public",
        "syntax": "!ping",
        "permission": "Mọi người",
        "summary": "Đo độ trễ WebSocket hiện tại giữa bot và Discord.",
        "details": "Không có tham số. Kết quả được trả về theo mili-giây.",
    },
    {
        "name": "auth",
        "category": "public",
        "syntax": "!auth",
        "permission": "Mọi người",
        "summary": "Tạo liên kết OAuth2 Discord với quyền `identify` và `guilds.join`.",
        "details": "Mở liên kết, kiểm tra đúng ứng dụng rồi chấp thuận. Đây là bước bắt buộc trước `!add_me` và các lệnh Owner thêm bạn vào server.",
    },
    {
        "name": "add_me",
        "category": "public",
        "syntax": "!add_me",
        "permission": "Mọi người đã chạy `!auth`",
        "summary": "Thử thêm chính người gọi vào tất cả server đang bật trong web admin.",
        "details": "Bot bỏ qua server đã tắt, báo tổng số lần thành công/thất bại và giữ khoảng chờ để hạn chế rate limit.",
    },
    {
        "name": "check_token",
        "category": "public",
        "syntax": "!check_token",
        "permission": "Mọi người",
        "summary": "Kiểm tra hệ thống lưu trữ có access token OAuth của bạn hay chưa.",
        "details": "Lệnh chỉ kiểm tra sự hiện diện và không hiển thị token. Nếu chưa có, dùng `!auth`.",
    },
    {
        "name": "status",
        "category": "public",
        "syntax": "!status",
        "permission": "Mọi người",
        "summary": "Hiển thị số server đang bật/tổng server, số user, trạng thái PostgreSQL, JSONBin và liên kết web.",
        "details": "Đây là chẩn đoán nhanh; trạng thái “configured” không đảm bảo một lần ghi dữ liệu sau đó sẽ thành công.",
    },
    {
        "name": "help",
        "category": "public",
        "syntax": "!help hoặc /help",
        "permission": "Mọi người",
        "summary": "Mở bảng hướng dẫn theo nhóm lệnh.",
        "details": "Dùng menu bên dưới để đổi nhóm. `/help` chỉ hiển thị riêng cho người gọi; nhóm Owner chỉ hiện với chủ bot.",
    },
    {
        "name": "invitebot",
        "category": "manage",
        "syntax": "!invitebot <bot_id_1> [bot_id_2 ...]",
        "permission": "Quyền Manage Server trong server gọi lệnh",
        "summary": "Tạo một liên kết mời Discord cho mỗi bot ID hợp lệ.",
        "details": "Các ID cách nhau bằng dấu cách; link dùng quyền mặc định 0 để bạn chọn sau. Ví dụ: `!invitebot 111111111 222222222`.",
    },
    {
        "name": "force_add",
        "category": "owner",
        "syntax": "!force_add <@user|user_id>",
        "permission": "Owner",
        "summary": "Thử thêm một người dùng vào tất cả server đang bật trong web admin.",
        "details": "Người đó phải hoàn tất `!auth`. Ví dụ: `!force_add @Agent`; server đã tắt bị bỏ qua và có khoảng chờ giữa các server.",
    },
    {
        "name": "invite",
        "category": "owner",
        "syntax": "!invite <@user|user_id>",
        "permission": "Owner",
        "summary": "Chọn nhiều server rồi thêm một người dùng vào các server đã chọn.",
        "details": "Menu có tối đa 20 server/trang và giữ lựa chọn khi chuyển trang. Người dùng đích phải hoàn tất `!auth`.",
    },
    {
        "name": "storage_info",
        "category": "owner",
        "syntax": "!storage_info",
        "permission": "Owner",
        "summary": "Kiểm tra các backend lưu trữ và số bản ghi có thể đọc được.",
        "details": "Có thể hiển thị JSONBin Bin ID; không đăng nội dung phản hồi này vào nơi công khai.",
    },
    {
        "name": "migrate_tokens",
        "category": "owner",
        "syntax": "!migrate_tokens <source> <target>",
        "permission": "Owner",
        "summary": "Sao chép token giữa `db`, `jsonbin` và `json`.",
        "details": "Ví dụ: `!migrate_tokens db jsonbin`. ⚠️ Có thao tác ghi hàng loạt; kiểm tra source/target và bản sao lưu trước khi chạy.",
    },
    {
        "name": "roster",
        "category": "owner",
        "syntax": "!roster",
        "permission": "Owner",
        "summary": "Hiển thị roster agent từ JSONBin theo trang, kèm avatar khi tải được.",
        "details": "Thứ tự ưu tiên lấy từ `_roster_order`; agent chưa có trong thứ tự được nối phía sau.",
    },
    {
        "name": "roster_move",
        "category": "owner",
        "syntax": "!roster_move <@user|user_id> <position>",
        "permission": "Owner",
        "summary": "Di chuyển agent tới vị trí 1-based trong roster.",
        "details": "Ví dụ: `!roster_move @Agent 1`. Lệnh cập nhật `_roster_order` trong JSONBin.",
    },
    {
        "name": "remove",
        "category": "owner",
        "syntax": "!remove <@user|user_id>",
        "permission": "Owner",
        "summary": "Xóa dữ liệu agent khỏi PostgreSQL, JSONBin và file JSON dự phòng.",
        "details": "⚠️ Đây là thao tác phá hủy dữ liệu và không có bước xác nhận. Kiểm tra đúng user trước khi chạy.",
    },
    {
        "name": "deploy",
        "category": "owner",
        "syntax": "!deploy",
        "permission": "Owner",
        "summary": "Chọn nhiều agent và nhiều server để thêm theo ma trận lựa chọn.",
        "details": "Agent cần có access token. Server có tối đa 20 mục/trang; agent giữ 25 mục/trang. Quá trình có delay chống rate limit.",
    },
    {
        "name": "getid",
        "category": "owner",
        "syntax": "!getid",
        "permission": "Owner",
        "summary": "Chọn server rồi tìm ID text channel theo tên chính xác.",
        "details": "Tên nhập không gồm `#`; so khớp không phân biệt hoa/thường. Menu server có tối đa 20 mục/trang.",
    },
    {
        "name": "kick",
        "category": "owner",
        "syntax": "!kick",
        "permission": "Owner",
        "summary": "Chọn nhiều agent và server, hoặc chọn toàn bộ server, rồi kick hàng loạt.",
        "details": "⚠️ Thao tác loại thành viên khỏi server. Bot cần quyền Kick Members và chỉ xử lý member tìm thấy trong cache.",
    },
    {
        "name": "setupadmin",
        "category": "owner",
        "syntax": "!setupadmin <@member|member_id>",
        "permission": "Owner",
        "summary": "Tạo/tìm role `Server Controller` có Administrator rồi cấp trên mọi server.",
        "details": "⚠️ Quyền rất cao. Lệnh chỉ chạy trên server đang bật, có bước xác nhận 30 giây; member phải có trong từng server và bot cần Manage Roles.",
    },
    {
        "name": "create",
        "category": "owner",
        "syntax": "!create",
        "permission": "Owner",
        "summary": "Chọn nhiều server, chọn 1–5 kênh và nhập tên để tạo text channel hàng loạt.",
        "details": "⚠️ Có thể tạo rất nhiều kênh. Bot cần Manage Channels; kiểm tra số server và tên kênh trước khi gửi modal.",
    },
)


def help_commands_for(category: str) -> tuple[dict[str, str], ...]:
    """Return the documented commands for one help category."""
    return tuple(command for command in HELP_COMMANDS if command["category"] == category)


def documented_command_names() -> set[str]:
    return {command["name"] for command in HELP_COMMANDS}
