# channel_tracker.py
# Module (Cog) để theo dõi hoạt động của kênh, sử dụng JSONBin.io để lưu trữ.
# Phiên bản 5: Loại bỏ hoàn toàn sự phụ thuộc vào PostgreSQL.

import discord
from discord.ext import commands, tasks
import requests # Sử dụng requests để tương tác với JSONBin
import os
from datetime import datetime, timedelta, timezone
import json

# --- Các hàm tương tác với JSONBin.io (Synchronous) ---
JSONBIN_API_KEY = os.getenv('JSONBIN_API_KEY')
JSONBIN_BIN_ID = os.getenv('JSONBIN_BIN_ID')
JSONBIN_HEADERS = {
    "Content-Type": "application/json",
    "X-Master-Key": JSONBIN_API_KEY,
    "X-Access-Key": JSONBIN_API_KEY
}
JSONBIN_URL = f"https://api.jsonbin.io/v3/b/{JSONBIN_BIN_ID}"

def storage_read_data():
    """Đọc toàn bộ dữ liệu từ JSONBin."""
    if not all([JSONBIN_API_KEY, JSONBIN_BIN_ID]):
        print("[Tracker] Lỗi: Thiếu thông tin cấu hình JSONBin.")
        return {}
    try:
        response = requests.get(f"{JSONBIN_URL}/latest", headers=JSONBIN_HEADERS)
        if response.status_code == 200:
            return response.json().get('record', {})
        print(f"[Tracker] Lỗi khi đọc JSONBin: {response.status_code} - {response.text}")
        return {}
    except Exception as e:
        print(f"[Tracker] Lỗi ngoại lệ khi đọc JSONBin: {e}")
        return {}

def storage_write_data(data):
    """Ghi toàn bộ dữ liệu vào JSONBin."""
    if not all([JSONBIN_API_KEY, JSONBIN_BIN_ID]):
        return False
    try:
        response = requests.put(JSONBIN_URL, json=data, headers=JSONBIN_HEADERS)
        if response.status_code == 200:
            return True
        print(f"[Tracker] Lỗi khi ghi JSONBin: {response.status_code} - {response.text}")
        return False
    except Exception as e:
        print(f"[Tracker] Lỗi ngoại lệ khi ghi JSONBin: {e}")
        return False

# --- Các hàm quản lý dữ liệu theo dõi (trên nền JSONBin) ---
# Các hàm này sẽ thao tác với key 'tracked_channels' trong bin của bạn

def get_tracked_channels_data():
    """Lấy riêng phần dữ liệu của các kênh đang theo dõi."""
    full_data = storage_read_data()
    return full_data.get('tracked_channels', {})

def add_tracked_channel(channel_id, guild_id, user_id, notification_channel_id):
    """Thêm hoặc cập nhật một kênh vào danh sách theo dõi."""
    full_data = storage_read_data()
    if 'tracked_channels' not in full_data:
        full_data['tracked_channels'] = {}
    
    full_data['tracked_channels'][str(channel_id)] = {
        'guild_id': guild_id,
        'user_id': user_id,
        'notification_channel_id': notification_channel_id,
        'is_inactive': False # Luôn reset về False khi thêm mới hoặc cập nhật
    }
    storage_write_data(full_data)

def remove_tracked_channel(channel_id):
    """Xóa một kênh khỏi danh sách theo dõi."""
    full_data = storage_read_data()
    if 'tracked_channels' in full_data and str(channel_id) in full_data['tracked_channels']:
        del full_data['tracked_channels'][str(channel_id)]
        storage_write_data(full_data)

def get_all_tracked_for_check():
    """Lấy danh sách kênh để kiểm tra, định dạng giống phiên bản DB cũ."""
    tracked_data = get_tracked_channels_data()
    # Chuyển đổi dict thành list of tuples để tương thích với logic cũ
    # (channel_id, guild_id, user_id, notification_channel_id, is_inactive)
    return [
        (int(cid), data['guild_id'], data['user_id'], data['notification_channel_id'], data['is_inactive'])
        for cid, data in tracked_data.items()
    ]

def update_tracked_channel_status(channel_id, is_now_inactive: bool):
    """Cập nhật trạng thái cho một kênh."""
    full_data = storage_read_data()
    if 'tracked_channels' in full_data and str(channel_id) in full_data['tracked_channels']:
        full_data['tracked_channels'][str(channel_id)]['is_inactive'] = is_now_inactive
        storage_write_data(full_data)

# --- Các thành phần UI (Views, Modals) - Không thay đổi ---

class TrackByIDModal(discord.ui.Modal, title="Theo dõi bằng ID Kênh"):
    channel_id_input = discord.ui.TextInput(
        label="ID của kênh cần theo dõi",
        placeholder="Dán ID của kênh văn bản vào đây...",
        required=True, min_length=17, max_length=20
    )

    async def on_submit(self, interaction: discord.Interaction):
        bot = interaction.client
        try:
            channel_id = int(self.channel_id_input.value)
        except ValueError:
            return await interaction.response.send_message("ID kênh không hợp lệ. Vui lòng chỉ nhập số.", ephemeral=True)

        channel_to_track = bot.get_channel(channel_id)
        if not isinstance(channel_to_track, discord.TextChannel):
            return await interaction.response.send_message("Không tìm thấy kênh văn bản với ID này hoặc bot không có quyền truy cập.", ephemeral=True)
        
        # Thay thế lệnh gọi DB bằng lệnh gọi hàm mới
        await bot.loop.run_in_executor(
            None, add_tracked_channel, channel_to_track.id, channel_to_track.guild.id, interaction.user.id, interaction.channel_id
        )

        embed = discord.Embed(
            title="🛰️ Bắt đầu theo dõi",
            description=f"Thành công! Bot sẽ theo dõi kênh {channel_to_track.mention} trong server **{channel_to_track.guild.name}**.",
            color=discord.Color.green()
        )
        embed.set_footer(text="Cảnh báo sẽ được gửi về kênh này nếu kênh không hoạt động.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

class TrackByNameModal(discord.ui.Modal, title="Theo dõi kênh trên mọi Server"):
    channel_name_input = discord.ui.TextInput(
        label="Nhập chính xác tên kênh cần theo dõi",
        placeholder="Ví dụ: general, announcements, v.v.",
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        bot = interaction.client
        channel_name = self.channel_name_input.value.strip().lower().replace('-', ' ')

        found_channels = [
            target_channel
            for guild in bot.guilds
            if guild.get_member(interaction.user.id)
            and (target_channel := discord.utils.get(guild.text_channels, name=channel_name))
        ]

        if not found_channels:
            return await interaction.followup.send(f"Không tìm thấy kênh nào tên `{self.channel_name_input.value}` trong các server bạn có mặt.", ephemeral=True)

        for channel in found_channels:
            # Thay thế lệnh gọi DB bằng lệnh gọi hàm mới
            await bot.loop.run_in_executor(
                None, add_tracked_channel, channel.id, channel.guild.id, interaction.user.id, interaction.channel_id
            )

        server_list_str = "\n".join([f"• **{c.guild.name}**" for c in found_channels])
        embed = discord.Embed(
            title="🛰️ Bắt đầu theo dõi hàng loạt",
            description=f"Đã bắt đầu theo dõi **{len(found_channels)}** kênh tên `{self.channel_name_input.value}` tại:\n{server_list_str}",
            color=discord.Color.green()
        )
        embed.set_footer(text="Cảnh báo sẽ được gửi về kênh này nếu có kênh không hoạt động.")
        await interaction.followup.send(embed=embed, ephemeral=True)

class TrackInitialView(discord.ui.View):
    def __init__(self, author_id: int):
        super().__init__(timeout=180)
        self.author_id = author_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("Bạn không phải người dùng lệnh này!", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Theo dõi bằng ID Kênh", style=discord.ButtonStyle.primary, emoji="🆔")
    async def track_by_id(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(TrackByIDModal())

    @discord.ui.button(label="Theo dõi bằng Tên Kênh", style=discord.ButtonStyle.secondary, emoji="📝")
    async def track_by_name(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(TrackByNameModal())


# --- Cog chính ---
class ChannelTracker(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.inactivity_threshold_minutes = int(os.getenv('INACTIVITY_THRESHOLD_MINUTES', 7 * 24 * 60))
        if not all([JSONBIN_API_KEY, JSONBIN_BIN_ID]):
            print("[Tracker] VÔ HIỆU HÓA: Không tìm thấy JSONBIN_API_KEY hoặc JSONBIN_BIN_ID.")
        else:
            self.check_activity.start()

    def cog_unload(self):
        self.check_activity.cancel()

    @tasks.loop(minutes=30)
    async def check_activity(self):
        print(f"[{datetime.now()}] [Tracker] Bắt đầu kiểm tra trạng thái kênh bằng JSONBin...")
        
        # Thay thế lệnh gọi DB
        tracked_channels_data = await self.bot.loop.run_in_executor(None, get_all_tracked_for_check)
        
        for channel_id, guild_id, user_id, notification_channel_id, was_inactive in tracked_channels_data:
            notification_channel = self.bot.get_channel(notification_channel_id)
            if not notification_channel:
                print(f"[Tracker] LỖI: Không tìm thấy kênh thông báo {notification_channel_id}, xóa kênh {channel_id} khỏi theo dõi.")
                await self.bot.loop.run_in_executor(None, remove_tracked_channel, channel_id)
                continue

            channel_to_track = self.bot.get_channel(channel_id)
            if not channel_to_track:
                print(f"[Tracker] Kênh {channel_id} không tồn tại, đang xóa khỏi theo dõi.")
                await self.bot.loop.run_in_executor(None, remove_tracked_channel, channel_id)
                continue
            
            try:
                last_message = await channel_to_track.fetch_message(channel_to_track.last_message_id) if channel_to_track.last_message_id else None
                last_activity_time = last_message.created_at if last_message else channel_to_track.created_at
                time_since_activity = datetime.now(timezone.utc) - last_activity_time
                
                is_currently_inactive = time_since_activity > timedelta(minutes=self.inactivity_threshold_minutes)
                user_to_notify = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
                mention = user_to_notify.mention if user_to_notify else f"<@{user_id}>"

                # KỊCH BẢN 1: Kênh vừa mới trở nên không hoạt động
                if is_currently_inactive and not was_inactive:
                    print(f"[Tracker] Kênh {channel_id} đã không hoạt động. Gửi cảnh báo.")
                    await self.bot.loop.run_in_executor(None, update_tracked_channel_status, channel_id, True)
                    
                    embed = discord.Embed(
                        title="⚠️ Cảnh báo Kênh không hoạt động",
                        description=f"Kênh {channel_to_track.mention} tại **{channel_to_track.guild.name}** đã không có tin nhắn mới trong hơn **{self.inactivity_threshold_minutes // (24*60)}** ngày.",
                        color=discord.Color.orange()
                    )
                    embed.add_field(name="Lần hoạt động cuối", value=f"<t:{int(last_activity_time.timestamp())}:R>", inline=False)
                    embed.set_footer(text=f"Thiết lập bởi {user_to_notify.display_name if user_to_notify else f'User ID: {user_id}'}")
                    await notification_channel.send(content=f"Thông báo cho {mention}:", embed=embed)

                # KỊCH BẢN 2: Kênh đã hoạt động trở lại
                elif not is_currently_inactive and was_inactive:
                    print(f"[Tracker] Kênh {channel_id} đã hoạt động trở lại. Gửi thông báo.")
                    await self.bot.loop.run_in_executor(None, update_tracked_channel_status, channel_id, False)

                    embed = discord.Embed(
                        title="✅ Kênh đã hoạt động trở lại",
                        description=f"Kênh {channel_to_track.mention} tại **{channel_to_track.guild.name}** đã có hoạt động mới.",
                        color=discord.Color.green()
                    )
                    embed.add_field(name="Hoạt động gần nhất", value=f"<t:{int(last_activity_time.timestamp())}:R>", inline=False)
                    embed.set_footer(text="Bot sẽ tiếp tục theo dõi kênh này.")
                    await notification_channel.send(content=f"Cập nhật cho {mention}:", embed=embed)
            
            except discord.Forbidden:
                print(f"[Tracker] Lỗi quyền: Không thể đọc lịch sử kênh {channel_to_track.name} ({channel_id}). Bỏ qua.")
            except Exception as e:
                print(f"[Tracker] Lỗi không xác định khi kiểm tra kênh {channel_id}: {e}")

    @check_activity.before_loop
    async def before_check_activity(self):
        await self.bot.wait_until_ready()

    @commands.command(name='track', help='Theo dõi hoạt động của một kênh.')
    async def track(self, ctx: commands.Context):
        embed = discord.Embed(
            title="🛰️ Thiết lập Theo dõi Kênh",
            description="Chọn phương thức bạn muốn dùng để xác định kênh cần theo dõi. Dữ liệu sẽ được lưu trên JSONBin.",
            color=discord.Color.blue()
        )
        view = TrackInitialView(author_id=ctx.author.id)
        await ctx.send(embed=embed, view=view)

    @commands.command(name='untrack', help='Ngừng theo dõi hoạt động của một kênh.')
    async def untrack(self, ctx: commands.Context, channel: discord.TextChannel):
        tracked_channels_data = await self.bot.loop.run_in_executor(None, get_tracked_channels_data)
        
        if str(channel.id) not in tracked_channels_data:
            return await ctx.send(f"Kênh {channel.mention} hiện không được theo dõi.", ephemeral=True)
            
        await self.bot.loop.run_in_executor(None, remove_tracked_channel, channel.id)
        
        embed = discord.Embed(
            title="✅ Dừng theo dõi", description=f"Đã ngừng theo dõi kênh {channel.mention}.", color=discord.Color.red()
        )
        await ctx.send(embed=embed)

async def setup(bot: commands.Bot):
    await bot.add_cog(ChannelTracker(bot))
