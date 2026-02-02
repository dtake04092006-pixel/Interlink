# main.py - Discord Bot with PostgreSQL + JSONBin.io for persistent token storage
import os
import json
import asyncio
import threading
import discord
import aiohttp
import requests
from discord.ext import commands
from flask import Flask, request
from dotenv import load_dotenv
from urllib.parse import urlparse
import time
from PIL import Image, ImageDraw
import io
import aiohttp
from flask import session, redirect, url_for, render_template_string, jsonify
import functools

# Try to import psycopg2, fallback to JSONBin if not available
try:
    import psycopg2
    HAS_PSYCOPG2 = True
    print("✅ psycopg2 imported successfully")
except ImportError:
    HAS_PSYCOPG2 = False
    print("⚠️ WARNING: psycopg2 not available, using JSONBin.io storage only")

# --- LOAD ENVIRONMENT VARIABLES ---
load_dotenv()
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
CLIENT_ID = os.getenv('DISCORD_CLIENT_ID')
CLIENT_SECRET = os.getenv('DISCORD_CLIENT_SECRET')
DATABASE_URL = os.getenv('DATABASE_URL')
ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', 'admin123') 

# JSONBin.io configuration
JSONBIN_API_KEY = os.getenv('JSONBIN_API_KEY')  # Thêm vào .env file
JSONBIN_BIN_ID = os.getenv('JSONBIN_BIN_ID')    # Thêm vào .env file

if not DISCORD_TOKEN:
    exit("LỖI: Không tìm thấy DISCORD_TOKEN")
if not CLIENT_ID:
    exit("LỖI: Không tìm thấy DISCORD_CLIENT_ID")
if not CLIENT_SECRET:
    exit("LỖI: Không tìm thấy DISCORD_CLIENT_SECRET")

# Kiểm tra JSONBin config
if not JSONBIN_API_KEY or not JSONBIN_BIN_ID:
    print("⚠️ WARNING: JSONBin.io config not found, will create new bin if needed")

# --- RENDER CONFIGURATION ---
PORT = int(os.getenv('PORT', 5000))
RENDER_URL = os.getenv('RENDER_EXTERNAL_URL', f'http://127.0.0.1:{PORT}')
REDIRECT_URI = f'{RENDER_URL}/callback'

# --- JSONBIN.IO FUNCTIONS ---
class JSONBinStorage:
    def __init__(self):
        self.api_key = JSONBIN_API_KEY
        self.bin_id = JSONBIN_BIN_ID
        self.base_url = "https://api.jsonbin.io/v3"
        
    def _get_headers(self):
        """Tạo headers cho requests"""
        return {
            "Content-Type": "application/json",
            "X-Master-Key": self.api_key,
            "X-Access-Key": self.api_key
        }
    
    def create_bin(self, data=None):
        """Tạo bin mới nếu chưa có"""
        if data is None:
            data = {}
        
        try:
            response = requests.post(
                f"{self.base_url}/b",
                json=data,
                headers=self._get_headers()
            )
            
            if response.status_code == 200:
                result = response.json()
                self.bin_id = result['metadata']['id']
                print(f"✅ Created new JSONBin: {self.bin_id}")
                print(f"🔑 Add this to your .env: JSONBIN_BIN_ID={self.bin_id}")
                return self.bin_id
            else:
                print(f"❌ Failed to create bin: {response.text}")
                return None
        except Exception as e:
            print(f"❌ JSONBin create error: {e}")
            return None
    
    def read_data(self):
        """Đọc dữ liệu từ JSONBin"""
        if not self.bin_id:
            print("⚠️ No bin ID, creating new bin...")
            self.create_bin()
            return {}
            
        try:
            response = requests.get(
                f"{self.base_url}/b/{self.bin_id}/latest",
                headers=self._get_headers()
            )
            
            if response.status_code == 200:
                data = response.json()
                return data.get('record', {})
            elif response.status_code == 404:
                print("⚠️ Bin not found, creating new one...")
                self.create_bin()
                return {}
            else:
                print(f"❌ Failed to read from JSONBin: {response.status_code}")
                return {}
        except Exception as e:
            print(f"❌ JSONBin read error: {e}")
            return {}
    
    def write_data(self, data):
        """Ghi dữ liệu vào JSONBin"""
        if not self.bin_id:
            print("⚠️ No bin ID, creating new bin...")
            if not self.create_bin(data):
                return False
        
        try:
            response = requests.put(
                f"{self.base_url}/b/{self.bin_id}",
                json=data,
                headers=self._get_headers()
            )
            
            if response.status_code == 200:
                print("✅ Data saved to JSONBin successfully")
                return True
            else:
                print(f"❌ Failed to save to JSONBin: {response.status_code} - {response.text}")
                return False
        except Exception as e:
            print(f"❌ JSONBin write error: {e}")
            return False
    
    def get_user_token(self, user_id):
        """Lấy token của user từ JSONBin"""
        data = self.read_data()
        user_data = data.get(str(user_id))
        
        if isinstance(user_data, dict):
            return user_data.get('access_token')
        return user_data
    
    def save_user_token(self, user_id, access_token, username=None, avatar_hash=None):
        """Lưu token của user vào JSONBin"""
        data = self.read_data()
        
        data[str(user_id)] = {
            'access_token': access_token,
            'username': username,
            'avatar_hash': avatar_hash,
            'updated_at': str(time.time())
        }
        
        return self.write_data(data)

    def delete_user(self, user_id):
        """Xóa một user khỏi JSONBin, bao gồm cả trong danh sách thứ tự."""
        full_data = self.read_data()
        user_id_str = str(user_id)
        
        made_change = False

        # 1. Xóa hồ sơ chính của user
        if user_id_str in full_data:
            del full_data[user_id_str]
            made_change = True
            print(f"[Storage] Đã xóa hồ sơ chính của user {user_id_str}.")

        # 2. Xóa user khỏi danh sách thứ tự '_roster_order' nếu có
        if '_roster_order' in full_data:
            try:
                full_data['_roster_order'].remove(user_id_str)
                made_change = True
                print(f"[Storage] Đã xóa user {user_id_str} khỏi _roster_order.")
            except ValueError:
                # User không có trong danh sách thứ tự, không sao cả
                pass
        
        # 3. Chỉ ghi lại vào bin nếu có sự thay đổi
        if made_change:
            return self.write_data(full_data)
        
        return True # Trả về True nếu không có gì để xóa

# Khởi tạo JSONBin storage
jsonbin_storage = JSONBinStorage()

def login_required(f):
    @functools.wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session:
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated_function
    
# --- DATABASE SETUP ---
def init_database():
    """Khởi tạo database và tạo bảng nếu chưa có"""
    if not DATABASE_URL or not HAS_PSYCOPG2:
        print("⚠️ WARNING: Không có DATABASE_URL hoặc psycopg2, sử dụng JSONBin.io")
        return False
    
    try:
        conn = psycopg2.connect(DATABASE_URL, sslmode='require')
        cursor = conn.cursor()
        
        # Tạo bảng user_tokens nếu chưa có
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_tokens (
                user_id VARCHAR(50) PRIMARY KEY,
                access_token TEXT NOT NULL,
                username VARCHAR(100),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        conn.commit()
        cursor.close()
        conn.close()
        print("✅ Database initialized successfully")
        return True
        
    except Exception as e:
        print(f"❌ Database connection failed: {e}")
        print("🔄 Falling back to JSONBin.io storage")
        return False

# --- DATABASE FUNCTIONS ---
def get_db_connection():
    """Tạo connection tới database"""
    if DATABASE_URL and HAS_PSYCOPG2:
        try:
            return psycopg2.connect(DATABASE_URL, sslmode='require')
        except Exception as e:
            print(f"Database connection error: {e}")
            return None
    return None

def get_user_access_token_db(user_id: str):
    """Lấy access token từ database"""
    conn = get_db_connection()
    if conn:
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT access_token FROM user_tokens WHERE user_id = %s", (user_id,))
            result = cursor.fetchone()
            cursor.close()
            conn.close()
            return result[0] if result else None
        except Exception as e:
            print(f"Database error: {e}")
            if conn:
                conn.close()
    return None

def save_user_token_db(user_id: str, access_token: str, username: str = None, avatar_hash: str = None):
    """Lưu access token vào database"""
    conn = get_db_connection()
    if conn:
        try:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO user_tokens (user_id, access_token, username, avatar_hash) 
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (user_id) 
                DO UPDATE SET 
                    access_token = EXCLUDED.access_token,
                    username = EXCLUDED.username,
                    avatar_hash = EXCLUDED.avatar_hash,
                    updated_at = CURRENT_TIMESTAMP
            ''', (user_id, access_token, username, avatar_hash))
            conn.commit()
            cursor.close()
            conn.close()
            print(f"✅ Saved token for user {user_id} to database")
            return True
        except Exception as e:
            print(f"Database error: {e}")
            if conn:
                conn.close()
    return False

# --- FALLBACK JSON FUNCTIONS (kept for compatibility) ---
def get_user_access_token_json(user_id: str):
    """Backup: Lấy token từ file JSON"""
    try:
        with open('tokens.json', 'r') as f:
            tokens = json.load(f)
            data = tokens.get(str(user_id))
            if isinstance(data, dict):
                return data.get('access_token')
            return data
    except (FileNotFoundError, json.JSONDecodeError):
        return None

def save_user_token_json(user_id: str, access_token: str, username: str = None, avatar_hash: str = None):
    """Backup: Lưu token vào file JSON"""
    try:
        try:
            with open('tokens.json', 'r') as f:
                tokens = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            tokens = {}
        
        tokens[user_id] = {
            'access_token': access_token,
            'username': username,
            'avatar_hash': avatar_hash,
            'updated_at': str(time.time())
        }
        
        with open('tokens.json', 'w') as f:
            json.dump(tokens, f, indent=4)
        print(f"✅ Saved token for user {user_id} to JSON file")
        return True
    except Exception as e:
        print(f"JSON file error: {e}")
        return False

# --- UNIFIED TOKEN FUNCTIONS ---
def get_user_access_token(user_id: int):
    """Lấy access token (Ưu tiên: Database > JSONBin.io > JSON file)"""
    user_id_str = str(user_id)
    
    # Try database first
    token = get_user_access_token_db(user_id_str)
    if token:
        return token
    
    # Try JSONBin.io
    if JSONBIN_API_KEY:
        token = jsonbin_storage.get_user_token(user_id_str)
        if token:
            return token
    
    # Fallback to JSON file (for local development)
    return get_user_access_token_json(user_id_str)

def save_user_token(user_id: str, access_token: str, username: str = None, avatar_hash: str = None):
    """Lưu access token (Database + JSONBin.io + JSON backup)"""
    success_db = save_user_token_db(user_id, access_token, username, avatar_hash)
    success_jsonbin = False
    success_json = False
    
    # Try JSONBin.io
    if JSONBIN_API_KEY:
        success_jsonbin = jsonbin_storage.save_user_token(user_id, access_token, username, avatar_hash)
    
    # Local JSON backup (for development)
    success_json = save_user_token_json(user_id, access_token, username, avatar_hash)
    
    return success_db or success_jsonbin or success_json

def delete_user_from_db(user_id: str):
    """Xóa user khỏi database"""
    conn = get_db_connection()
    if conn:
        try:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM user_tokens WHERE user_id = %s", (user_id,))
            conn.commit()
            cursor.close()
            conn.close()
            print(f"✅ Deleted user {user_id} from database")
            return True
        except Exception as e:
            print(f"Database delete error: {e}")
            if conn:
                conn.close()
    return False

def delete_user_from_json(user_id: str):
    """Xóa user khỏi file JSON"""
    try:
        with open('tokens.json', 'r') as f:
            tokens = json.load(f)

        if user_id in tokens:
            del tokens[user_id]
            with open('tokens.json', 'w') as f:
                json.dump(tokens, f, indent=4)
            print(f"✅ Deleted user {user_id} from JSON file")
        return True
    except (FileNotFoundError, json.JSONDecodeError):
        return True # File không tồn tại coi như đã xóa
    except Exception as e:
        print(f"JSON file delete error: {e}")
        return False
        
# --- DISCORD BOT SETUP ---
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents, owner_id=1386710352426959011, help_command=None)

# --- FLASK WEB SERVER SETUP ---
app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY', 'super_secret_key_change_me') # <--- DÁN VÀO ĐÂY
# --- UTILITY FUNCTIONS ---
async def add_member_to_guild(guild_id: int, user_id: int, access_token: str):
    """Thêm member vào guild sử dụng Discord API trực tiếp"""
    url = f"https://discord.com/api/v10/guilds/{guild_id}/members/{user_id}"
    headers = {
        "Authorization": f"Bot {DISCORD_TOKEN}",
        "Content-Type": "application/json"
    }
    data = {
        "access_token": access_token
    }
    
    async with aiohttp.ClientSession() as session:
        async with session.put(url, headers=headers, json=data) as response:
            if response.status == 201:
                return True, "Thêm thành công"
            elif response.status == 204:
                return True, "User đã có trong server"
            else:
                error_text = await response.text()
                return False, f"HTTP {response.status}: {error_text}"
                
# --- INTERACTIVE UI COMPONENTS ---

# Lớp này định nghĩa giao diện lựa chọn server
class ServerSelectView(discord.ui.View):
    def __init__(self, author: discord.User, target_user: discord.User, guilds: list[discord.Guild]):
        super().__init__(timeout=300)
        self.author = author
        self.target_user = target_user
        self.guilds = guilds
        self.selected_guild_ids = set()

        # Chia danh sách server thành các phần nhỏ, mỗi phần tối đa 25
        guild_chunks = [self.guilds[i:i + 25] for i in range(0, len(self.guilds), 25)]
        
        # Tạo một menu cho mỗi phần
        for index, chunk in enumerate(guild_chunks):
            self.add_item(self.create_server_select(chunk, index, len(guild_chunks)))

    def create_server_select(self, guild_chunk: list[discord.Guild], page_index: int, total_pages: int):
        options = [discord.SelectOption(label=g.name, value=str(g.id)) for g in guild_chunk]
        placeholder = f"Chọn server (Trang {page_index + 1}/{total_pages})"
        
        select = discord.ui.Select(
            placeholder=placeholder,
            options=options,
            min_values=1,
            max_values=len(options)
        )
        
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.author.id:
                return await interaction.response.send_message("Bạn không có quyền tương tác.", ephemeral=True)
            
            # Cập nhật tập hợp các ID đã chọn
            ids_in_this_menu = {int(opt.value) for opt in select.options}
            self.selected_guild_ids.difference_update(ids_in_this_menu)
            for gid in interaction.data["values"]:
                self.selected_guild_ids.add(int(gid))

            await interaction.response.send_message(f"✅ Đã cập nhật! Hiện đã chọn **{len(self.selected_guild_ids)}** server.", ephemeral=True)

        select.callback = callback
        return select

    @discord.ui.button(label="Summon", style=discord.ButtonStyle.green, emoji="✨")
    async def summon_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author.id:
            return await interaction.response.send_message("Bạn không có quyền sử dụng nút này.", ephemeral=True)
        
        if not self.selected_guild_ids:
            return await interaction.response.send_message("Bạn chưa chọn server nào cả!", ephemeral=True)

        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)
        
        await interaction.followup.send(f"✅ Đã nhận lệnh! Bắt đầu mời **{self.target_user.name}** vào **{len(self.selected_guild_ids)}** server đã chọn...")

        access_token = get_user_access_token(self.target_user.id)
        if not access_token:
            await interaction.followup.send(f"❌ Người dùng **{self.target_user.name}** chưa ủy quyền cho bot.")
            return

        success_count, fail_count = 0, 0
        for guild_id in self.selected_guild_ids:
            await asyncio.sleep(2)
            success, message = await add_member_to_guild(guild_id, self.target_user.id, access_token)
            if success:
                success_count += 1
            else:
                fail_count += 1
        
        embed = discord.Embed(title=f"📊 Kết quả mời {self.target_user.name}", color=0x00ff00)
        embed.add_field(name="✅ Thành công", value=f"{success_count} server", inline=True)
        embed.add_field(name="❌ Thất bại", value=f"{fail_count} server", inline=True)
        await interaction.followup.send(embed=embed)

# Roster
class RosterPages(discord.ui.View):
    def __init__(self, agents, ctx):
        super().__init__(timeout=180)  # Menu sẽ tự động tắt sau 180 giây
        self.agents = agents
        self.ctx = ctx
        self.current_page = 0
        self.items_per_page = 6  # Hiển thị 6 điệp viên mỗi trang
        self.total_pages = (len(self.agents) + self.items_per_page - 1) // self.items_per_page
        self.message = None

    async def create_page_embed(self, page_num):
        """Tạo Embed và ảnh ghép cho một trang cụ thể."""
        start_index = page_num * self.items_per_page
        end_index = start_index + self.items_per_page
        page_agents = self.agents[start_index:end_index]

        if not page_agents:
            return discord.Embed(title="Lỗi", description="Không có dữ liệu cho trang này."), None

        # --- Logic tạo ảnh ghép cho trang hiện tại (ĐÃ SỬA) ---
        avatar_size = 128
        padding = 10
        
        canvas = Image.new('RGBA', ((avatar_size + padding) * len(page_agents) + padding, avatar_size + padding * 2), (44, 47, 51, 255))
        current_x = padding
        
        # Sử dụng aiohttp để tải ảnh bất đồng bộ
        async with aiohttp.ClientSession() as session:
            for agent in page_agents:
                if agent.get('avatar_hash'):
                    avatar_url = f"https://cdn.discordapp.com/avatars/{agent['id']}/{agent['avatar_hash']}.png?size=128"
                    try:
                        async with session.get(avatar_url) as response:
                            if response.status == 200:
                                avatar_data = await response.read()
                                avatar_img = Image.open(io.BytesIO(avatar_data)).convert("RGBA")
                                canvas.paste(avatar_img, (current_x, padding))
                            else:
                                print(f"Failed to load avatar for {agent['id']}: HTTP {response.status}")
                    except Exception as e:
                        print(f"Could not load avatar for {agent['id']}: {e}")
                current_x += avatar_size + padding
        
        buffer = io.BytesIO()
        canvas.save(buffer, 'PNG')
        buffer.seek(0)
        discord_file = discord.File(buffer, filename=f"roster_page_{page_num}.png")
        # --- Kết thúc logic tạo ảnh ---

        description_list = [f"👤 **{agent['username']}** `(ID: {agent['id']})`" for agent in page_agents]
        description_text = "\n".join(description_list)

        embed = discord.Embed(
            title=f"AGENT ROSTER ({len(self.agents)} Active)",
            description=description_text,
            color=discord.Color.dark_grey()
        )
        embed.set_image(url=f"attachment://roster_page_{page_num}.png")
        embed.set_footer(text=f"Trang {self.current_page + 1}/{self.total_pages}")
        
        return embed, discord_file
        
        buffer = io.BytesIO()
        canvas.save(buffer, 'PNG')
        buffer.seek(0)
        discord_file = discord.File(buffer, filename=f"roster_page_{page_num}.png")
        # --- Kết thúc logic tạo ảnh ---

        description_list = [f"👤 **{agent['username']}** `(ID: {agent['id']})`" for agent in page_agents]
        description_text = "\n".join(description_list)

        embed = discord.Embed(
            title=f"AGENT ROSTER ({len(self.agents)} Active)",
            description=description_text,
            color=discord.Color.dark_grey()
        )
        embed.set_image(url=f"attachment://roster_page_{page_num}.png")
        embed.set_footer(text=f"Trang {self.current_page + 1}/{self.total_pages}")
        
        return embed, discord_file

    async def update_buttons(self):
        """Cập nhật trạng thái (bật/tắt) của các nút."""
        # Fast backward button (<<)
        self.children[0].disabled = self.current_page == 0
        # Slow backward button (<)
        self.children[1].disabled = self.current_page == 0
        # Slow forward button (>)
        self.children[2].disabled = self.current_page >= self.total_pages - 1
        # Fast forward button (>>)
        self.children[3].disabled = self.current_page >= self.total_pages - 1

    async def send_initial_message(self):
        """Gửi tin nhắn đầu tiên."""
        embed, file = await self.create_page_embed(self.current_page) # Thêm await
        await self.update_buttons()
        self.message = await self.ctx.send(embed=embed, file=file, view=self)
    
    @discord.ui.button(style=discord.ButtonStyle.secondary, emoji="⏪")
    async def fast_backward(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Lùi nhanh 5 trang hoặc về trang đầu."""
        self.current_page = max(0, self.current_page - 5)
        embed, file = await self.create_page_embed(self.current_page) # Thêm await
        await self.update_buttons()
        await interaction.response.edit_message(embed=embed, attachments=[file], view=self)

    @discord.ui.button(style=discord.ButtonStyle.secondary, emoji="◀️")
    async def slow_backward(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Lùi chậm 1 trang."""
        if self.current_page > 0:
            self.current_page -= 1
            embed, file = await self.create_page_embed(self.current_page) # Thêm await
            await self.update_buttons()
            await interaction.response.edit_message(embed=embed, attachments=[file], view=self)
        else:
            await interaction.response.defer()

    @discord.ui.button(style=discord.ButtonStyle.secondary, emoji="▶️")
    async def slow_forward(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Tiến chậm 1 trang."""
        if self.current_page < self.total_pages - 1:
            self.current_page += 1
            embed, file = await self.create_page_embed(self.current_page) # Thêm await
            await self.update_buttons()
            await interaction.response.edit_message(embed=embed, attachments=[file], view=self)
        else:
            await interaction.response.defer()

    @discord.ui.button(style=discord.ButtonStyle.secondary, emoji="⏩")
    async def fast_forward(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Tiến nhanh 5 trang hoặc đến trang cuối."""
        self.current_page = min(self.total_pages - 1, self.current_page + 5)
        embed, file = await self.create_page_embed(self.current_page) # Thêm await
        await self.update_buttons()
        await interaction.response.edit_message(embed=embed, attachments=[file], view=self)

class DeployView(discord.ui.View):
    def __init__(self, author: discord.User, guilds: list[discord.Guild], agents: list[dict]):
        super().__init__(timeout=600) # Tăng thời gian chờ
        self.author = author
        
        # Chia dữ liệu thành các trang
        self.guild_pages = [guilds[i:i + 25] for i in range(0, len(guilds), 25)]
        self.agent_pages = [agents[i:i + 25] for i in range(0, len(agents), 25)]
        
        # Theo dõi trang hiện tại
        self.current_guild_page = 0
        self.current_agent_page = 0
        
        # *** THAY ĐỔI 1: Lưu trữ nhiều ID server thay vì một đối tượng guild duy nhất ***
        self.selected_guild_ids = set()
        self.selected_user_ids = set()

        # Gọi hàm để xây dựng giao diện ban đầu
        self.update_view()

    def update_view(self):
        """Xóa các thành phần cũ và dựng lại giao diện dựa trên trang hiện tại."""
        self.clear_items() # Xóa tất cả các nút và menu cũ

        # --- Tạo menu chọn Server ---
        guild_options = [
            discord.SelectOption(
                label=g.name, 
                value=str(g.id), 
                # *** THAY ĐỔI 2: Đánh dấu các server đã được chọn trong set ***
                default=(g.id in self.selected_guild_ids)
            ) 
            for g in self.guild_pages[self.current_guild_page]
        ]
        guild_placeholder = f"Bước 1: Chọn Server (Trang {self.current_guild_page + 1}/{len(self.guild_pages)})"
        # *** THAY ĐỔI 3: Cho phép chọn nhiều server (min_values=0, max_values=...) ***
        guild_select = discord.ui.Select(
            placeholder=guild_placeholder, 
            min_values=0, 
            max_values=len(guild_options), 
            options=guild_options, 
            row=0
        )
        
        # *** THAY ĐỔI 4: Cập nhật callback để xử lý nhiều lựa chọn server ***
        async def guild_callback(interaction: discord.Interaction):
            if interaction.user.id != self.author.id: return
            
            # Xóa các lựa chọn cũ từ trang này để không bị trùng lặp
            ids_on_this_page = {int(opt.value) for opt in guild_options}
            self.selected_guild_ids.difference_update(ids_on_this_page)
            
            # Thêm các lựa chọn mới
            for gid in interaction.data["values"]:
                self.selected_guild_ids.add(int(gid))
                
            # Cập nhật lại view để hiển thị đúng các lựa chọn
            self.update_view()
            await interaction.message.edit(view=self)
            await interaction.response.send_message(f"✅ Cập nhật! Đã chọn **{len(self.selected_guild_ids)}** server.", ephemeral=True)

        guild_select.callback = guild_callback
        self.add_item(guild_select)

        # --- Tạo các nút điều hướng cho Server ---
        if len(self.guild_pages) > 1:
            prev_guild_button = discord.ui.Button(label="◀️ Server Trước", style=discord.ButtonStyle.secondary, row=1, disabled=(self.current_guild_page == 0))
            next_guild_button = discord.ui.Button(label="Server Tiếp ▶️", style=discord.ButtonStyle.secondary, row=1, disabled=(self.current_guild_page >= len(self.guild_pages) - 1))

            async def prev_guild_callback(interaction: discord.Interaction):
                if interaction.user.id != self.author.id: return
                self.current_guild_page -= 1
                self.update_view()
                await interaction.response.edit_message(view=self)
            
            async def next_guild_callback(interaction: discord.Interaction):
                if interaction.user.id != self.author.id: return
                self.current_guild_page += 1
                self.update_view()
                await interaction.response.edit_message(view=self)
            
            prev_guild_button.callback = prev_guild_callback
            next_guild_button.callback = next_guild_callback
            self.add_item(prev_guild_button)
            self.add_item(next_guild_button)

        # --- Tạo menu chọn Điệp viên ---
        agent_options = [
            discord.SelectOption(
                label=str(agent.get('username', agent.get('id'))), 
                value=str(agent.get('id')),
                default=(int(agent.get('id')) in self.selected_user_ids)
            ) for agent in self.agent_pages[self.current_agent_page]
        ]
        agent_placeholder = f"Bước 2: Chọn Điệp viên (Trang {self.current_agent_page + 1}/{len(self.agent_pages)})"
        agent_select = discord.ui.Select(placeholder=agent_placeholder, min_values=0, max_values=len(agent_options), options=agent_options, row=2)

        async def agent_callback(interaction: discord.Interaction):
            if interaction.user.id != self.author.id: return
            
            ids_on_this_page = {int(opt.value) for opt in agent_options}
            self.selected_user_ids.difference_update(ids_on_this_page)
            
            for uid in interaction.data["values"]:
                self.selected_user_ids.add(int(uid))
                
            self.update_view()
            await interaction.message.edit(view=self)
            await interaction.followup.send(f"✅ Cập nhật! Đã chọn **{len(self.selected_user_ids)}** điệp viên.", ephemeral=True)
            
        agent_select.callback = agent_callback
        self.add_item(agent_select)
        
        # --- Tạo các nút điều hướng cho Điệp viên ---
        if len(self.agent_pages) > 1:
            prev_agent_button = discord.ui.Button(label="◀️ Điệp viên Trước", style=discord.ButtonStyle.secondary, row=3, disabled=(self.current_agent_page == 0))
            next_agent_button = discord.ui.Button(label="Điệp viên Tiếp ▶️", style=discord.ButtonStyle.secondary, row=3, disabled=(self.current_agent_page >= len(self.agent_pages) - 1))

            async def prev_agent_callback(interaction: discord.Interaction):
                if interaction.user.id != self.author.id: return
                self.current_agent_page -= 1
                self.update_view()
                await interaction.response.edit_message(view=self)

            async def next_agent_callback(interaction: discord.Interaction):
                if interaction.user.id != self.author.id: return
                self.current_agent_page += 1
                self.update_view()
                await interaction.response.edit_message(view=self)

            prev_agent_button.callback = prev_agent_callback
            next_agent_button.callback = next_agent_callback
            self.add_item(prev_agent_button)
            self.add_item(next_agent_button)

        # --- Nút hành động cuối cùng ---
        # *** THAY ĐỔI 5: Cập nhật label và điều kiện disabled của nút ***
        button_label = f"Triển Khai ({len(self.selected_user_ids)} agents -> {len(self.selected_guild_ids)} servers)"
        deploy_button = discord.ui.Button(
            label=button_label, 
            style=discord.ButtonStyle.danger, 
            emoji="🚀", 
            row=4, 
            disabled=(not self.selected_guild_ids or not self.selected_user_ids)
        )
        
        # *** THAY ĐỔI 6: Cập nhật logic xử lý triển khai cho nhiều server ***
        async def deploy_callback(interaction: discord.Interaction):
            if interaction.user.id != self.author.id: return
            
            # Vô hiệu hóa view
            for item in self.children: item.disabled = True
            await interaction.response.edit_message(view=self)
            
            await interaction.followup.send(
                f"🚀 **Bắt đầu triển khai {len(self.selected_user_ids)} điệp viên tới {len(self.selected_guild_ids)} server...**"
            )
            
            success_count, fail_count, failed_adds = 0, 0, []
            
            for guild_id in self.selected_guild_ids:
                await asyncio.sleep(2)
                guild = bot.get_guild(guild_id)
                if not guild:
                    fail_count += len(self.selected_user_ids)
                    failed_adds.append(f"Tất cả agents -> Server ID `{guild_id}` (Không tìm thấy hoặc bot không ở trong server)")
                    continue
                    
                for user_id in self.selected_user_ids:
                    await asyncio.sleep(1)
                    await asyncio.sleep(1)
                    access_token = get_user_access_token(user_id)
                    if not access_token:
                        fail_count += 1
                        failed_adds.append(f"<@{user_id}> -> `{guild.name}` (Không có token)")
                        continue
                    
                    try:
                        success, message = await add_member_to_guild(guild.id, user_id, access_token)
                        if success:
                            success_count += 1
                        else:
                            fail_count += 1
                            failed_adds.append(f"<@{user_id}> -> `{guild.name}` ({message[:50]})")
                    except Exception as e:
                        fail_count += 1
                        failed_adds.append(f"<@{user_id}> -> `{guild.name}` (Lỗi: {e})")
            
            embed = discord.Embed(title=f"Báo Cáo Triển Khai Hàng Loạt", color=0x00ff00)
            embed.add_field(name="✅ Lượt Thêm Thành Công", value=f"{success_count}", inline=True)
            embed.add_field(name="❌ Lượt Thêm Thất Bại", value=f"{fail_count}", inline=True)
            
            if failed_adds:
                # Giới hạn chi tiết lỗi để không vượt quá giới hạn của Discord Embed
                error_details = "\n".join(failed_adds)
                if len(error_details) > 1024:
                    error_details = error_details[:1020] + "\n..."
                embed.add_field(name="Chi tiết thất bại", value=error_details, inline=False)
                
            await interaction.followup.send(embed=embed)

        deploy_button.callback = deploy_callback
        self.add_item(deploy_button)

class KickView(discord.ui.View):
    def __init__(self, author: discord.User, guilds: list[discord.Guild], agents: list[dict]):
        super().__init__(timeout=600)
        self.author = author
        self.all_guilds = guilds
        
        # Chia dữ liệu thành các trang
        self.guild_pages = [guilds[i:i + 25] for i in range(0, len(guilds), 25)]
        self.agent_pages = [agents[i:i + 25] for i in range(0, len(agents), 25)]
        
        # Theo dõi trang và lựa chọn hiện tại
        self.current_guild_page = 0
        self.current_agent_page = 0
        self.selected_guild_ids = set()
        self.selected_user_ids = set()

        self.update_view()

    def update_view(self):
        """Xóa và dựng lại giao diện dựa trên trạng thái hiện tại."""
        self.clear_items()

        # --- Menu Chọn Server ---
        guild_options = [
            discord.SelectOption(
                label=g.name, value=str(g.id), default=(g.id in self.selected_guild_ids)
            ) for g in self.guild_pages[self.current_guild_page]
        ]
        guild_placeholder = f"Bước 1: Chọn Server (Trang {self.current_guild_page + 1}/{len(self.guild_pages)})"
        guild_select = discord.ui.Select(
            placeholder=guild_placeholder, min_values=0, max_values=len(guild_options), options=guild_options, row=0
        )
        async def guild_callback(interaction: discord.Interaction):
            if interaction.user.id != self.author.id: return
            ids_on_this_page = {int(opt.value) for opt in guild_options}
            self.selected_guild_ids.difference_update(ids_on_this_page)
            for gid in interaction.data["values"]:
                self.selected_guild_ids.add(int(gid))
            self.update_view()
            await interaction.response.edit_message(view=self)
            await interaction.followup.send(f"✅ Đã cập nhật! Hiện đã chọn **{len(self.selected_guild_ids)}** server.", ephemeral=True)
        guild_select.callback = guild_callback
        self.add_item(guild_select)

        # --- Nút Điều Hướng & Chọn Tất Cả Server ---
        # Nút chọn tất cả
        all_selected = len(self.selected_guild_ids) == len(self.all_guilds)
        select_all_button = discord.ui.Button(
            label="Bỏ chọn Tất Cả" if all_selected else "Chọn Tất Cả Server", 
            style=discord.ButtonStyle.danger if all_selected else discord.ButtonStyle.primary, 
            row=1
        )
        async def select_all_callback(interaction: discord.Interaction):
            if interaction.user.id != self.author.id: return
            if all_selected:
                self.selected_guild_ids.clear()
            else:
                self.selected_guild_ids = {g.id for g in self.all_guilds}
            self.update_view()
            await interaction.response.edit_message(view=self)
        select_all_button.callback = select_all_callback
        self.add_item(select_all_button)

        # Nút phân trang server
        if len(self.guild_pages) > 1:
            prev_guild_button = discord.ui.Button(label="◀️ Trước", style=discord.ButtonStyle.secondary, row=1, disabled=(self.current_guild_page == 0))
            next_guild_button = discord.ui.Button(label="Sau ▶️", style=discord.ButtonStyle.secondary, row=1, disabled=(self.current_guild_page >= len(self.guild_pages) - 1))
            async def prev_guild_callback(interaction: discord.Interaction):
                if interaction.user.id != self.author.id: return
                self.current_guild_page -= 1; self.update_view()
                await interaction.response.edit_message(view=self)
            async def next_guild_callback(interaction: discord.Interaction):
                if interaction.user.id != self.author.id: return
                self.current_guild_page += 1; self.update_view()
                await interaction.response.edit_message(view=self)
            prev_guild_button.callback = prev_guild_callback
            next_guild_button.callback = next_guild_callback
            self.add_item(prev_guild_button)
            self.add_item(next_guild_button)

        # --- Menu Chọn Agent ---
        agent_options = [
            discord.SelectOption(
                label=str(agent.get('username', agent.get('id'))), value=str(agent.get('id')), default=(int(agent.get('id')) in self.selected_user_ids)
            ) for agent in self.agent_pages[self.current_agent_page]
        ]
        agent_placeholder = f"Bước 2: Chọn Agent để Kick (Trang {self.current_agent_page + 1}/{len(self.agent_pages)})"
        agent_select = discord.ui.Select(placeholder=agent_placeholder, min_values=0, max_values=len(agent_options), options=agent_options, row=2)
        async def agent_callback(interaction: discord.Interaction):
            if interaction.user.id != self.author.id: return
            ids_on_this_page = {int(opt.value) for opt in agent_options}
            self.selected_user_ids.difference_update(ids_on_this_page)
            for uid in interaction.data["values"]:
                self.selected_user_ids.add(int(uid))
            self.update_view()
            await interaction.message.edit(view=self)
            await interaction.followup.send(f"✅ Đã cập nhật! Hiện đã chọn **{len(self.selected_user_ids)}** agent.", ephemeral=True)
        agent_select.callback = agent_callback
        self.add_item(agent_select)
        
        # --- Nút Điều Hướng Agent ---
        if len(self.agent_pages) > 1:
            prev_agent_button = discord.ui.Button(label="◀️ Trước", style=discord.ButtonStyle.secondary, row=3, disabled=(self.current_agent_page == 0))
            next_agent_button = discord.ui.Button(label="Sau ▶️", style=discord.ButtonStyle.secondary, row=3, disabled=(self.current_agent_page >= len(self.agent_pages) - 1))
            async def prev_agent_callback(interaction: discord.Interaction):
                if interaction.user.id != self.author.id: return
                self.current_agent_page -= 1; self.update_view()
                await interaction.response.edit_message(view=self)
            async def next_agent_callback(interaction: discord.Interaction):
                if interaction.user.id != self.author.id: return
                self.current_agent_page += 1; self.update_view()
                await interaction.response.edit_message(view=self)
            prev_agent_button.callback = prev_agent_callback
            next_agent_button.callback = next_agent_callback
            self.add_item(prev_agent_button)
            self.add_item(next_agent_button)

        # --- Nút hành động cuối cùng ---
        button_label = f"Kick ({len(self.selected_user_ids)} agents) khỏi ({len(self.selected_guild_ids)} servers)"
        kick_button = discord.ui.Button(
            label=button_label, style=discord.ButtonStyle.danger, emoji="👢", row=4, 
            disabled=(not self.selected_guild_ids or not self.selected_user_ids)
        )
        
        async def kick_callback(interaction: discord.Interaction):
            if interaction.user.id != self.author.id: return
            
            for item in self.children: item.disabled = True
            await interaction.response.edit_message(view=self)
            
            await interaction.followup.send(
                f"👢 **Bắt đầu quá trình kick {len(self.selected_user_ids)} agent khỏi {len(self.selected_guild_ids)} server...**"
            )
            
            success_count, fail_count, failed_kicks = 0, 0, []
            reason = f"Bị kick bởi {interaction.user.name} (ID: {interaction.user.id})"
            
            for guild_id in self.selected_guild_ids:
                guild = bot.get_guild(guild_id)
                if not guild:
                    fail_count += len(self.selected_user_ids)
                    failed_kicks.append(f"Tất cả agents -> Server ID `{guild_id}` (Không tìm thấy server)")
                    continue
                    
                for user_id in self.selected_user_ids:
                    member_to_kick = guild.get_member(user_id)
                    if not member_to_kick:
                        fail_count += 1
                        failed_kicks.append(f"<@{user_id}> -> `{guild.name}` (Không có trong server)")
                        continue
                    
                    try:
                        await guild.kick(member_to_kick, reason=reason)
                        success_count += 1
                    except discord.Forbidden:
                        fail_count += 1
                        failed_kicks.append(f"<@{user_id}> -> `{guild.name}` (Thiếu quyền)")
                    except Exception as e:
                        fail_count += 1
                        failed_kicks.append(f"<@{user_id}> -> `{guild.name}` (Lỗi: {e})")
            
            embed = discord.Embed(title="Báo Cáo Kick Hàng Loạt", color=0xff0000)
            embed.add_field(name="✅ Lượt Kick Thành Công", value=f"{success_count}", inline=True)
            embed.add_field(name="❌ Lượt Kick Thất Bại", value=f"{fail_count}", inline=True)
            
            if failed_kicks:
                error_details = "\n".join(failed_kicks)
                if len(error_details) > 1024:
                    error_details = error_details[:1020] + "\n..."
                embed.add_field(name="Chi tiết thất bại", value=error_details, inline=False)
                
            await interaction.followup.send(embed=embed)

        kick_button.callback = kick_callback
        self.add_item(kick_button)
        
# --- Modal 1: Nhập số lượng kênh ---
# --- View để chọn số lượng kênh ---
class QuantityView(discord.ui.View):
    def __init__(self, selected_guilds: list[discord.Guild], author: discord.User):
        super().__init__(timeout=300)
        self.selected_guilds = selected_guilds
        self.author = author

    @discord.ui.button(label="1 Kênh", style=discord.ButtonStyle.secondary)
    async def one_channel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author.id:
            return await interaction.response.send_message("❌ Chỉ người tạo lệnh mới có thể sử dụng!", ephemeral=True)
        await interaction.response.send_modal(NamesModal(self.selected_guilds, 1))

    @discord.ui.button(label="2 Kênh", style=discord.ButtonStyle.secondary)
    async def two_channels(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author.id:
            return await interaction.response.send_message("❌ Chỉ người tạo lệnh mới có thể sử dụng!", ephemeral=True)
        await interaction.response.send_modal(NamesModal(self.selected_guilds, 2))

    @discord.ui.button(label="3 Kênh", style=discord.ButtonStyle.secondary)
    async def three_channels(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author.id:
            return await interaction.response.send_message("❌ Chỉ người tạo lệnh mới có thể sử dụng!", ephemeral=True)
        await interaction.response.send_modal(NamesModal(self.selected_guilds, 3))

    @discord.ui.button(label="4 Kênh", style=discord.ButtonStyle.secondary)
    async def four_channels(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author.id:
            return await interaction.response.send_message("❌ Chỉ người tạo lệnh mới có thể sử dụng!", ephemeral=True)
        await interaction.response.send_modal(NamesModal(self.selected_guilds, 4))

    @discord.ui.button(label="5 Kênh", style=discord.ButtonStyle.secondary)
    async def five_channels(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author.id:
            return await interaction.response.send_message("❌ Chỉ người tạo lệnh mới có thể sử dụng!", ephemeral=True)
        await interaction.response.send_modal(NamesModal(self.selected_guilds, 5))

# --- Modal để nhập tên riêng cho từng kênh ---
class NamesModal(discord.ui.Modal):
    def __init__(self, selected_guilds: list[discord.Guild], quantity: int):
        super().__init__(title=f"Nhập Tên Cho {quantity} Kênh")
        self.selected_guilds = selected_guilds
        self.quantity = quantity
        
        # Tạo các TextInput fields dựa trên số lượng
        if quantity >= 1:
            self.name1 = discord.ui.TextInput(
                label="Tên Kênh #1",
                placeholder="Nhập tên cho kênh thứ 1...",
                required=True
            )
            self.add_item(self.name1)
        
        if quantity >= 2:
            self.name2 = discord.ui.TextInput(
                label="Tên Kênh #2", 
                placeholder="Nhập tên cho kênh thứ 2...",
                required=True
            )
            self.add_item(self.name2)
            
        if quantity >= 3:
            self.name3 = discord.ui.TextInput(
                label="Tên Kênh #3",
                placeholder="Nhập tên cho kênh thứ 3...", 
                required=True
            )
            self.add_item(self.name3)
            
        if quantity >= 4:
            self.name4 = discord.ui.TextInput(
                label="Tên Kênh #4",
                placeholder="Nhập tên cho kênh thứ 4...",
                required=True
            )
            self.add_item(self.name4)
            
        if quantity >= 5:
            self.name5 = discord.ui.TextInput(
                label="Tên Kênh #5",
                placeholder="Nhập tên cho kênh thứ 5...",
                required=True
            )
            self.add_item(self.name5)

    async def on_submit(self, interaction: discord.Interaction):
        # Lấy tên từ các ô nhập liệu dựa trên số lượng
        channel_names = []
        
        if hasattr(self, 'name1'):
            channel_names.append(self.name1.value)
        if hasattr(self, 'name2'):
            channel_names.append(self.name2.value)
        if hasattr(self, 'name3'):
            channel_names.append(self.name3.value)
        if hasattr(self, 'name4'):
            channel_names.append(self.name4.value)
        if hasattr(self, 'name5'):
            channel_names.append(self.name5.value)
        
        await interaction.response.send_message(f"✅ **Đã nhận lệnh!** Chuẩn bị tạo **{len(channel_names)}** kênh trong **{len(self.selected_guilds)}** server...", ephemeral=True)

        total_success = 0
        total_fail = 0
        
        for guild in self.selected_guilds:
            await asyncio.sleep(2)
            for name in channel_names:
                try:
                    await guild.create_text_channel(name=name)
                    total_success += 1
                except discord.Forbidden:
                    total_fail += 1
                    print(f"Lỗi quyền: Không thể tạo kênh '{name}' trong server {guild.name}")
                except Exception as e:
                    total_fail += 1
                    print(f"Lỗi không xác định khi tạo kênh '{name}': {e}")
        
        await interaction.followup.send(f"**Báo cáo hoàn tất:**\n✅ Đã tạo thành công: **{total_success}** kênh.\n❌ Thất bại: **{total_fail}** kênh.")

# --- View để chọn số lượng kênh ---
class QuantityView(discord.ui.View):
    def __init__(self, selected_guilds: list[discord.Guild], author: discord.User):
        super().__init__(timeout=300)
        self.selected_guilds = selected_guilds
        self.author = author

    @discord.ui.button(label="1 Kênh", style=discord.ButtonStyle.secondary)
    async def one_channel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author.id:
            return await interaction.response.send_message("❌ Chỉ người tạo lệnh mới có thể sử dụng!", ephemeral=True)
        await interaction.response.send_modal(NamesModal(self.selected_guilds, 1))

    @discord.ui.button(label="2 Kênh", style=discord.ButtonStyle.secondary)
    async def two_channels(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author.id:
            return await interaction.response.send_message("❌ Chỉ người tạo lệnh mới có thể sử dụng!", ephemeral=True)
        await interaction.response.send_modal(NamesModal(self.selected_guilds, 2))

    @discord.ui.button(label="3 Kênh", style=discord.ButtonStyle.secondary)
    async def three_channels(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author.id:
            return await interaction.response.send_message("❌ Chỉ người tạo lệnh mới có thể sử dụng!", ephemeral=True)
        await interaction.response.send_modal(NamesModal(self.selected_guilds, 3))

    @discord.ui.button(label="4 Kênh", style=discord.ButtonStyle.secondary)
    async def four_channels(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author.id:
            return await interaction.response.send_message("❌ Chỉ người tạo lệnh mới có thể sử dụng!", ephemeral=True)
        await interaction.response.send_modal(NamesModal(self.selected_guilds, 4))

    @discord.ui.button(label="5 Kênh", style=discord.ButtonStyle.secondary)
    async def five_channels(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author.id:
            return await interaction.response.send_message("❌ Chỉ người tạo lệnh mới có thể sử dụng!", ephemeral=True)
        await interaction.response.send_modal(NamesModal(self.selected_guilds, 5))

# --- Modal để nhập tên riêng cho từng kênh ---
class NamesModal(discord.ui.Modal):
    def __init__(self, selected_guilds: list[discord.Guild], quantity: int):
        super().__init__(title=f"Nhập Tên Cho {quantity} Kênh")
        self.selected_guilds = selected_guilds
        self.quantity = quantity
        
        # Tạo các TextInput fields dựa trên số lượng
        if quantity >= 1:
            self.name1 = discord.ui.TextInput(
                label="Tên Kênh #1",
                placeholder="Nhập tên cho kênh thứ 1...",
                required=True
            )
            self.add_item(self.name1)
        
        if quantity >= 2:
            self.name2 = discord.ui.TextInput(
                label="Tên Kênh #2", 
                placeholder="Nhập tên cho kênh thứ 2...",
                required=True
            )
            self.add_item(self.name2)
            
        if quantity >= 3:
            self.name3 = discord.ui.TextInput(
                label="Tên Kênh #3",
                placeholder="Nhập tên cho kênh thứ 3...", 
                required=True
            )
            self.add_item(self.name3)
            
        if quantity >= 4:
            self.name4 = discord.ui.TextInput(
                label="Tên Kênh #4",
                placeholder="Nhập tên cho kênh thứ 4...",
                required=True
            )
            self.add_item(self.name4)
            
        if quantity >= 5:
            self.name5 = discord.ui.TextInput(
                label="Tên Kênh #5",
                placeholder="Nhập tên cho kênh thứ 5...",
                required=True
            )
            self.add_item(self.name5)

    async def on_submit(self, interaction: discord.Interaction):
        # Lấy tên từ các ô nhập liệu dựa trên số lượng
        channel_names = []
        
        if hasattr(self, 'name1'):
            channel_names.append(self.name1.value)
        if hasattr(self, 'name2'):
            channel_names.append(self.name2.value)
        if hasattr(self, 'name3'):
            channel_names.append(self.name3.value)
        if hasattr(self, 'name4'):
            channel_names.append(self.name4.value)
        if hasattr(self, 'name5'):
            channel_names.append(self.name5.value)
        
        await interaction.response.send_message(f"✅ **Đã nhận lệnh!** Chuẩn bị tạo **{len(channel_names)}** kênh trong **{len(self.selected_guilds)}** server...", ephemeral=True)

        total_success = 0
        total_fail = 0
        
        for guild in self.selected_guilds:
            for name in channel_names:
                try:
                    await guild.create_text_channel(name=name)
                    total_success += 1
                except discord.Forbidden:
                    total_fail += 1
                    print(f"Lỗi quyền: Không thể tạo kênh '{name}' trong server {guild.name}")
                except Exception as e:
                    total_fail += 1
                    print(f"Lỗi không xác định khi tạo kênh '{name}': {e}")
        
        await interaction.followup.send(f"**Báo cáo hoàn tất:**\n✅ Đã tạo thành công: **{total_success}** kênh.\n❌ Thất bại: **{total_fail}** kênh.")

# --- View để chọn server và bắt đầu quy trình (PHIÊN BẢN NÂNG CẤP) ---
class CreateChannelView(discord.ui.View):
    def __init__(self, author: discord.User, guilds: list[discord.Guild]):
        super().__init__(timeout=600)
        self.author = author
        self.all_guilds = guilds
        
        # Chia danh sách server thành các trang, mỗi trang 25 server
        self.guild_pages = [self.all_guilds[i:i + 25] for i in range(0, len(self.all_guilds), 25)]
        
        # Theo dõi trạng thái
        self.current_guild_page = 0
        self.selected_guild_ids = set()

        # Dựng giao diện ban đầu
        self.update_view()

    def update_view(self):
        """Xóa và dựng lại toàn bộ giao diện dựa trên trạng thái hiện tại."""
        self.clear_items()

        # --- Menu Chọn Server ---
        current_options = [
            discord.SelectOption(
                label=g.name, 
                value=str(g.id),
                # Đánh dấu những server đã được chọn trước đó
                default=(g.id in self.selected_guild_ids)
            ) 
            for g in self.guild_pages[self.current_guild_page]
        ]
        
        placeholder = f"Bước 1: Chọn Server (Trang {self.current_guild_page + 1}/{len(self.guild_pages)})"
        
        # min_values=0 cho phép bỏ chọn tất cả trong menu hiện tại
        guild_select = discord.ui.Select(
            placeholder=placeholder, 
            min_values=0, 
            max_values=len(current_options), 
            options=current_options, 
            row=0
        )
        
        async def guild_callback(interaction: discord.Interaction):
            if interaction.user.id != self.author.id:
                return await interaction.response.send_message("❌ Bạn không có quyền tương tác!", ephemeral=True)
            
            # Xóa các lựa chọn cũ từ trang này để xử lý việc bỏ chọn
            ids_on_this_page = {int(opt.value) for opt in current_options}
            self.selected_guild_ids.difference_update(ids_on_this_page)
            
            # Thêm các lựa chọn mới từ tương tác
            for gid in interaction.data["values"]:
                self.selected_guild_ids.add(int(gid))
                
            # Cập nhật lại giao diện để hiển thị đúng các lựa chọn và trạng thái nút
            self.update_view()
            await interaction.response.edit_message(view=self)
            await interaction.followup.send(f"✅ Cập nhật! Đã chọn **{len(self.selected_guild_ids)}** server.", ephemeral=True)

        guild_select.callback = guild_callback
        self.add_item(guild_select)

        # --- Nút Điều Hướng Server ---
        if len(self.guild_pages) > 1:
            prev_button = discord.ui.Button(label="◀️ Trang Trước", style=discord.ButtonStyle.secondary, row=1, disabled=(self.current_guild_page == 0))
            next_button = discord.ui.Button(label="Trang Tiếp ▶️", style=discord.ButtonStyle.secondary, row=1, disabled=(self.current_guild_page >= len(self.guild_pages) - 1))

            async def prev_callback(interaction: discord.Interaction):
                if interaction.user.id != self.author.id: return
                self.current_guild_page -= 1
                self.update_view()
                await interaction.response.edit_message(view=self)
            
            async def next_callback(interaction: discord.Interaction):
                if interaction.user.id != self.author.id: return
                self.current_guild_page += 1
                self.update_view()
                await interaction.response.edit_message(view=self)
            
            prev_button.callback = prev_callback
            next_button.callback = next_callback
            self.add_item(prev_button)
            self.add_item(next_button)
            
        # --- Nút Hành Động Cuối Cùng ---
        # Label của nút sẽ thay đổi để hiển thị số lượng server đã chọn
        button_label = f"Bước 2: Chọn Số Lượng Kênh ({len(self.selected_guild_ids)} server)"
        proceed_button = discord.ui.Button(
            label=button_label,
            style=discord.ButtonStyle.success,
            row=4,
            # Nút bị vô hiệu hóa nếu chưa chọn server nào
            disabled=(not self.selected_guild_ids)
        )
        
        async def proceed_callback(interaction: discord.Interaction):
            if interaction.user.id != self.author.id:
                return await interaction.response.send_message("❌ Chỉ người tạo lệnh mới có thể sử dụng!", ephemeral=True)
            
            # Vô hiệu hóa giao diện cũ trước khi gửi cái mới
            for item in self.children:
                item.disabled = True
            await interaction.message.edit(view=self)

            # Lấy các đối tượng guild từ các ID đã chọn
            selected_guilds = [g for g in self.all_guilds if g.id in self.selected_guild_ids]

            embed = discord.Embed(
                title="🔢 Chọn Số Lượng Kênh",
                description=f"Bạn đã chọn **{len(selected_guilds)}** server.\nHãy chọn số lượng kênh muốn tạo trong mỗi server:",
                color=0x00ff00
            )
            
            # Gửi tin nhắn mới (ephemeral) với các nút chọn số lượng
            view = QuantityView(selected_guilds, self.author)
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

        proceed_button.callback = proceed_callback
        self.add_item(proceed_button)


@bot.command(name='create', help='(Chủ bot) Tạo nhiều kênh trong nhiều server.')
@commands.is_owner()
async def create(ctx):
    """Mở giao diện tạo kênh hàng loạt."""
    # Sắp xếp server giống như lệnh deploy để có thứ tự nhất quán
    sorted_guilds = sorted(bot.guilds, key=lambda g: g.me.joined_at)
    
    view = CreateChannelView(ctx.author, sorted_guilds)
    
    embed = discord.Embed(
        title="🛠️ Bảng Điều Khiển Tạo Kênh",
        description="Sử dụng các công cụ bên dưới để tạo kênh hàng loạt.",
        color=discord.Color.blue()
    )
    await ctx.send(embed=embed, view=view)

# --- Getid ---
class ChannelNameModal(discord.ui.Modal, title="Nhập Tên Kênh Cần Tìm"):
    def __init__(self, selected_guilds: list[discord.Guild]):
        super().__init__()
        self.selected_guilds = selected_guilds

    channel_name = discord.ui.TextInput(
        label="Tên kênh bạn muốn tìm ID",
        placeholder="Nhập chính xác tên kênh, không bao gồm dấu #",
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.send_message(f"🔎 Đang tìm kiếm các kênh có tên `{self.channel_name.value}`...", ephemeral=True)
        
        results = {}
        target_name = self.channel_name.value.lower().strip()

        for guild in self.selected_guilds:
            found_channels = []
            for channel in guild.text_channels:
                if channel.name.lower() == target_name:
                    found_channels.append(channel.id)
            
            if found_channels:
                results[guild.name] = found_channels

        # Tạo Embed kết quả
        if not results:
            embed = discord.Embed(
                title="Không Tìm Thấy Kết Quả",
                description=f"Không tìm thấy kênh nào có tên `{self.channel_name.value}` trong các server đã chọn.",
                color=discord.Color.red()
            )
        else:
            embed = discord.Embed(
                title=f"Kết Quả Tìm Kiếm cho Kênh '{self.channel_name.value}'",
                color=discord.Color.green()
            )
            for guild_name, channel_ids in results.items():
                id_string = "\n".join([f"`{channel_id}`" for channel_id in channel_ids])
                embed.add_field(name=f"🖥️ Server: {guild_name}", value=id_string, inline=False)
        
        await interaction.followup.send(embed=embed)
        
# --- View để lấy ID kênh (PHIÊN BẢN NÂNG CẤP VỚI PHÂN TRANG) ---
class GetIdPaginatedView(discord.ui.View):
    def __init__(self, author: discord.User, guilds: list[discord.Guild]):
        super().__init__(timeout=600)
        self.author = author
        self.all_guilds = guilds
        
        # Chia danh sách server thành các trang, mỗi trang 25 server
        self.guild_pages = [self.all_guilds[i:i + 25] for i in range(0, len(self.all_guilds), 25)]
        
        # Theo dõi trạng thái
        self.current_page = 0
        self.selected_guild_ids = set()

        # Dựng giao diện ban đầu
        self.update_view()

    def update_view(self):
        """Xóa và dựng lại toàn bộ giao diện dựa trên trạng thái hiện tại."""
        self.clear_items()

        # --- Menu Chọn Server ---
        current_options = [
            discord.SelectOption(
                label=g.name, 
                value=str(g.id),
                default=(g.id in self.selected_guild_ids) # Đánh dấu server đã chọn
            ) 
            for g in self.guild_pages[self.current_page]
        ]
        
        placeholder = f"Bước 1: Chọn Server (Trang {self.current_page + 1}/{len(self.guild_pages)})"
        
        guild_select = discord.ui.Select(
            placeholder=placeholder, 
            min_values=0, # Cho phép bỏ chọn tất cả
            max_values=len(current_options), 
            options=current_options, 
            row=0
        )
        
        async def guild_callback(interaction: discord.Interaction):
            if interaction.user.id != self.author.id: return
            
            # Xóa các lựa chọn cũ từ trang này để xử lý việc bỏ chọn
            ids_on_this_page = {int(opt.value) for opt in current_options}
            self.selected_guild_ids.difference_update(ids_on_this_page)
            
            # Thêm các lựa chọn mới
            for gid in interaction.data["values"]:
                self.selected_guild_ids.add(int(gid))
                
            # Cập nhật lại giao diện
            self.update_view()
            await interaction.response.edit_message(view=self)
            await interaction.followup.send(f"✅ Đã cập nhật! Hiện đã chọn **{len(self.selected_guild_ids)}** server.", ephemeral=True)

        guild_select.callback = guild_callback
        self.add_item(guild_select)

        # --- Nút Điều Hướng Server ---
        if len(self.guild_pages) > 1:
            prev_button = discord.ui.Button(label="◀️ Trang Trước", style=discord.ButtonStyle.secondary, row=1, disabled=(self.current_page == 0))
            next_button = discord.ui.Button(label="Trang Tiếp ▶️", style=discord.ButtonStyle.secondary, row=1, disabled=(self.current_page >= len(self.guild_pages) - 1))

            async def prev_callback(interaction: discord.Interaction):
                if interaction.user.id != self.author.id: return
                self.current_page -= 1
                self.update_view()
                await interaction.response.edit_message(view=self)
            
            async def next_callback(interaction: discord.Interaction):
                if interaction.user.id != self.author.id: return
                self.current_page += 1
                self.update_view()
                await interaction.response.edit_message(view=self)
            
            prev_button.callback = prev_callback
            next_button.callback = next_callback
            self.add_item(prev_button)
            self.add_item(next_button)
            
        # --- Nút Hành Động Cuối Cùng ---
        button_label = f"Bước 2: Nhập Tên Kênh ({len(self.selected_guild_ids)} server)"
        proceed_button = discord.ui.Button(
            label=button_label,
            style=discord.ButtonStyle.primary,
            emoji="🔎",
            row=2,
            disabled=(not self.selected_guild_ids) # Vô hiệu hóa nếu chưa chọn server
        )
        
        async def proceed_callback(interaction: discord.Interaction):
            if interaction.user.id != self.author.id: return
            
            # Lấy các đối tượng guild từ các ID đã chọn
            selected_guilds = [g for g in self.all_guilds if g.id in self.selected_guild_ids]
            
            # Mở Modal để người dùng nhập tên kênh
            modal = ChannelNameModal(selected_guilds)
            await interaction.response.send_modal(modal)

        proceed_button.callback = proceed_callback
        self.add_item(proceed_button)
        
# --- DISCORD BOT EVENTS ---
@bot.event
async def on_ready():
    print(f'✅ Bot đăng nhập thành công: {bot.user.name}')
    print(f'🔗 Web server: {RENDER_URL}')
    print(f'🔑 Redirect URI: {REDIRECT_URI}')
    
    # Check storage status
    db_status = "Connected" if get_db_connection() else "Unavailable"
    jsonbin_status = "Connected" if JSONBIN_API_KEY else "Not configured"
    print(f'💾 Database: {db_status}')
    print(f'🌐 JSONBin.io: {jsonbin_status}')
    
    
    try:
        synced = await bot.tree.sync()
        print(f"✅ Đã đồng bộ {len(synced)} lệnh slash.")
    except Exception as e:
        print(f"❌ Không thể đồng bộ lệnh slash: {e}")
    print('------')

@bot.event
async def on_message(message):
    if message.author == bot.user:
        return
    
    # Xử lý các lệnh !command
    await bot.process_commands(message)

@bot.event
async def on_message_edit(before, after):
    """Xử lý khi tin nhắn được CHỈNH SỬA."""
    if after.author == bot.user:
        return
    
# --- DISCORD BOT COMMANDS ---
@bot.command(name='ping', help='Kiểm tra độ trễ kết nối của bot.')
async def ping(ctx):
    latency = round(bot.latency * 1000)
    await ctx.send(f'🏓 Pong! Độ trễ là {latency}ms.')

@bot.command(name='auth', help='Lấy link ủy quyền để bot có thể thêm bạn vào server.')
async def auth(ctx):
    auth_url = (
        f'https://discord.com/api/oauth2/authorize?client_id={CLIENT_ID}'
        f'&redirect_uri={REDIRECT_URI}&response_type=code&scope=identify%20guilds.join'
    )
    embed = discord.Embed(
        title="🔐 Ủy quyền cho Bot",
        description=f"Nhấp vào link bên dưới để cho phép bot thêm bạn vào các server:",
        color=0x00ff00
    )
    embed.add_field(name="🔗 Link ủy quyền", value=f"[Nhấp vào đây]({auth_url})", inline=False)
    embed.add_field(name="📌 Lưu ý", value="Token sẽ được lưu an toàn vào cloud storage", inline=False)
    await ctx.send(embed=embed)

@bot.command(name='add_me', help='Thêm bạn vào tất cả các server của bot.')
async def add_me(ctx):
    user_id = ctx.author.id
    await ctx.send(f"✅ Bắt đầu quá trình thêm {ctx.author.mention} vào các server...")
    
    access_token = get_user_access_token(user_id)
    if not access_token:
        embed = discord.Embed(
            title="❌ Chưa ủy quyền",
            description="Bạn chưa ủy quyền cho bot. Hãy sử dụng lệnh `!auth` trước.",
            color=0xff0000
        )
        await ctx.send(embed=embed)
        return
    
    success_count = 0
    fail_count = 0
    
    for guild in bot.guilds:
        await asyncio.sleep(2)
        try:
            member = guild.get_member(user_id)
            if member:
                print(f"👍 {ctx.author.name} đã có trong server {guild.name}")
                success_count += 1
                continue
            
            success, message = await add_member_to_guild(guild.id, user_id, access_token)
            
            if success:
                print(f"👍 Thêm thành công {ctx.author.name} vào server {guild.name}: {message}")
                success_count += 1
            else:
                print(f"👎 Lỗi khi thêm vào {guild.name}: {message}")
                fail_count += 1
                
        except Exception as e:
            print(f"👎 Lỗi không xác định khi thêm vào {guild.name}: {e}")
            fail_count += 1
    
    embed = discord.Embed(title="📊 Kết quả", color=0x00ff00)
    embed.add_field(name="✅ Thành công", value=f"{success_count} server", inline=True)
    embed.add_field(name="❌ Thất bại", value=f"{fail_count} server", inline=True)
    await ctx.send(embed=embed)

@bot.command(name='check_token', help='Kiểm tra xem bạn đã ủy quyền chưa.')
async def check_token(ctx):
    user_id = ctx.author.id
    token = get_user_access_token(user_id)
    
    if token:
        embed = discord.Embed(
            title="✅ Đã ủy quyền", 
            description="Bot đã có token của bạn và có thể thêm bạn vào server",
            color=0x00ff00
        )
        embed.add_field(name="💾 Lưu trữ", value="Token được lưu an toàn trên cloud", inline=False)
    else:
        embed = discord.Embed(
            title="❌ Chưa ủy quyền", 
            description="Bạn chưa ủy quyền cho bot. Hãy sử dụng `!auth`",
            color=0xff0000
        )
    
    await ctx.send(embed=embed)

@bot.command(name='status', help='Kiểm tra trạng thái bot và storage.')
async def status(ctx):
    # Test database connection
    db_connection = get_db_connection()
    db_status = "✅ Connected" if db_connection else "❌ Unavailable"
    if db_connection:
        db_connection.close()
    
    # Test JSONBin connection
    jsonbin_status = "✅ Configured" if JSONBIN_API_KEY else "❌ Not configured"
    
    embed = discord.Embed(title="🤖 Trạng thái Bot", color=0x0099ff)
    embed.add_field(name="📊 Server", value=f"{len(bot.guilds)} server", inline=True)
    embed.add_field(name="👥 Người dùng", value=f"{len(bot.users)} user", inline=True)
    embed.add_field(name="💾 Database", value=db_status, inline=True)
    embed.add_field(name="🌐 JSONBin.io", value=jsonbin_status, inline=True)
    embed.add_field(name="🌍 Web Server", value=f"[Truy cập]({RENDER_URL})", inline=False)
    await ctx.send(embed=embed)
    
@bot.command(name='force_add', help='(Chủ bot) Thêm một người dùng bất kỳ vào tất cả các server.')
@commands.is_owner()
async def force_add(ctx, user_to_add: discord.User):
    """
    Lệnh chỉ dành cho chủ bot để thêm một người dùng bất kỳ vào các server.
    Cách dùng: !force_add <User_ID> hoặc !force_add @TênNgườiDùng
    """
    user_id = user_to_add.id
    await ctx.send(f"✅ Đã nhận lệnh! Bắt đầu quá trình thêm {user_to_add.mention} vào các server...")
    
    access_token = get_user_access_token(user_id)
    if not access_token:
        embed = discord.Embed(
            title="❌ Người dùng chưa ủy quyền",
            description=f"Người dùng {user_to_add.mention} chưa ủy quyền cho bot. Hãy yêu cầu họ sử dụng lệnh `!auth` trước.",
            color=0xff0000
        )
        await ctx.send(embed=embed)
        return
    
    success_count = 0
    fail_count = 0
    
    for guild in bot.guilds:
        await asyncio.sleep(2)
        try:
            member = guild.get_member(user_id)
            if member:
                print(f"👍 {user_to_add.name} đã có trong server {guild.name}")
                success_count += 1
                continue
            
            success, message = await add_member_to_guild(guild.id, user_id, access_token)
            
            if success:
                print(f"👍 Thêm thành công {user_to_add.name} vào server {guild.name}: {message}")
                success_count += 1
            else:
                print(f"👎 Lỗi khi thêm vào {guild.name}: {message}")
                fail_count += 1
                
        except Exception as e:
            print(f"👎 Lỗi không xác định khi thêm vào {guild.name}: {e}")
            fail_count += 1
    
    embed = discord.Embed(title=f"📊 Kết quả thêm {user_to_add.name}", color=0x00ff00)
    embed.add_field(name="✅ Thành công", value=f"{success_count} server", inline=True)
    embed.add_field(name="❌ Thất bại", value=f"{fail_count} server", inline=True)
    await ctx.send(embed=embed)

@force_add.error
async def force_add_error(ctx, error):
    if isinstance(error, commands.NotOwner):
        await ctx.send("🚫 Lỗi: Bạn không có quyền sử dụng lệnh này!")
    elif isinstance(error, commands.UserNotFound):
        await ctx.send(f"❌ Lỗi: Không tìm thấy người dùng được chỉ định.")
    else:
        print(f"Lỗi khi thực thi lệnh force_add: {error}")
        await ctx.send(f"Đã có lỗi xảy ra khi thực thi lệnh. Vui lòng kiểm tra console.")
        
@bot.command(name='invite', help='(Chủ bot) Mở giao diện để chọn server mời người dùng vào.')
@commands.is_owner()
async def invite(ctx, user_to_add: discord.User):
    """
    Mở một giao diện tương tác để chọn server mời người dùng.
    """
    if not user_to_add:
        await ctx.send("Không tìm thấy người dùng này.")
        return
        
    # Tạo giao diện (View) và truyền các thông tin cần thiết
    view = ServerSelectView(author=ctx.author, target_user=user_to_add, guilds=bot.guilds)
    
    embed = discord.Embed(
        title=f"💌 Mời {user_to_add.name}",
        description="Hãy chọn các server bạn muốn mời người này vào từ menu bên dưới, sau đó nhấn nút 'Summon'.",
        color=0x0099ff
    )
    embed.set_thumbnail(url=user_to_add.display_avatar.url)
    
    await ctx.send(embed=embed, view=view)

# --- SLASH COMMANDS ---
@bot.tree.command(name="help", description="Hiển thị thông tin về các lệnh của bot")
async def help_slash(interaction: discord.Interaction):
    embed = discord.Embed(
        title="📝 Bảng Lệnh Của Bot Mật Vụ",
        description="Dưới đây là danh sách các mật lệnh có sẵn.",
        color=0x0099ff
    )
    embed.set_thumbnail(url=bot.user.display_avatar.url)
    
    # Lệnh cho mọi người
    embed.add_field(name="🕵️ Lệnh Cơ Bản (Dành cho mọi Điệp viên)", value="----------------------------------", inline=False)
    embed.add_field(name="`!auth`", value="Lấy link ủy quyền để gia nhập mạng lưới.", inline=True)
    embed.add_field(name="`!add_me`", value="Tự triển khai bản thân đến tất cả server.", inline=True)
    embed.add_field(name="`!check_token`", value="Kiểm tra trạng thái ủy quyền của bạn.", inline=True)
    embed.add_field(name="`!status`", value="Xem trạng thái hoạt động của bot và hệ thống.", inline=True)
    embed.add_field(name="`!ping`", value="Kiểm tra độ trễ của bot.", inline=True)
    
    # Lệnh chỉ dành cho chủ bot
    if await bot.is_owner(interaction.user):
        embed.add_field(name="👑 Lệnh Chỉ Huy (Chỉ dành cho Owner)", value="----------------------------------", inline=False)
        embed.add_field(name="`!roster`", value="Xem danh sách điệp viên.", inline=True)
        embed.add_field(name="`!deploy`", value="Thêm NHIỀU điệp viên vào MỘT server.", inline=True)
        embed.add_field(name="`!invite <User>`", value="Thêm MỘT điệp viên vào NHIỀU server.", inline=True)
        embed.add_field(name="`!remove <User>`", value="Xóa dữ liệu của một điệp viên.", inline=True)
        embed.add_field(name="`!force_add <User>`", value="Ép thêm điệp viên vào TẤT CẢ server.", inline=True)
        embed.add_field(name="`!storage_info`", value="Xem thông tin các hệ thống lưu trữ.", inline=True)

    embed.set_footer(text="Hãy chọn một mật lệnh để bắt đầu chiến dịch.")
    await interaction.response.send_message(embed=embed, ephemeral=True)
    
@bot.command(name='help', help='Hiển thị bảng trợ giúp về các lệnh.')
async def help(ctx):
    embed = discord.Embed(
        title="📝 Bảng Lệnh Của Bot Mật Vụ",
        description="Dưới đây là danh sách các mật lệnh có sẵn.",
        color=0x0099ff
    )
    embed.set_thumbnail(url=bot.user.display_avatar.url)
    
    # Lệnh cho mọi người
    embed.add_field(name="🕵️ Lệnh Cơ Bản (Dành cho mọi Điệp viên)", value="----------------------------------", inline=False)
    embed.add_field(name="`!auth`", value="Lấy link ủy quyền để gia nhập mạng lưới.", inline=True)
    embed.add_field(name="`!add_me`", value="Tự triển khai bản thân đến tất cả server.", inline=True)
    embed.add_field(name="`!check_token`", value="Kiểm tra trạng thái ủy quyền của bạn.", inline=True)
    embed.add_field(name="`!status`", value="Xem trạng thái hoạt động của bot và hệ thống.", inline=True)
    embed.add_field(name="`!ping`", value="Kiểm tra độ trễ của bot.", inline=True)

    # Lệnh chỉ dành cho chủ bot
    if await bot.is_owner(ctx.author):
        embed.add_field(name="👑 Lệnh Chỉ Huy (Chỉ dành cho Owner)", value="----------------------------------", inline=False)
        embed.add_field(name="`!roster`", value="Xem danh sách điệp viên.", inline=True)
        embed.add_field(name="`!deploy`", value="Thêm NHIỀU điệp viên vào MỘT server.", inline=True)
        embed.add_field(name="`!invite <User>`", value="Thêm MỘT điệp viên vào NHIỀU server.", inline=True)
        embed.add_field(name="`!remove <User>`", value="Xóa dữ liệu của một điệp viên.", inline=True)
        embed.add_field(name="`!force_add <User>`", value="Ép thêm điệp viên vào TẤT CẢ server.", inline=True)
        embed.add_field(name="`!storage_info`", value="Xem thông tin các hệ thống lưu trữ.", inline=True)

    embed.set_footer(text="Hãy chọn một mật lệnh để bắt đầu chiến dịch.")
    await ctx.send(embed=embed)

# --- ADDITIONAL JSONBIN MANAGEMENT COMMANDS ---
@bot.command(name='storage_info', help='(Chủ bot) Hiển thị thông tin về storage systems.')
@commands.is_owner()
async def storage_info(ctx):
    """Hiển thị thông tin chi tiết về các storage systems"""
    
    # Test Database
    db_connection = get_db_connection()
    if db_connection:
        try:
            cursor = db_connection.cursor()
            cursor.execute("SELECT COUNT(*) FROM user_tokens")
            db_count = cursor.fetchone()[0]
            cursor.close()
            db_connection.close()
            db_info = f"✅ Connected ({db_count} tokens)"
        except:
            db_info = "❌ Connection Error"
    else:
        db_info = "❌ Not Available"
    
    # Test JSONBin
    if JSONBIN_API_KEY and JSONBIN_BIN_ID:
        try:
            data = jsonbin_storage.read_data()
            jsonbin_count = len(data) if isinstance(data, dict) else 0
            jsonbin_info = f"✅ Connected ({jsonbin_count} tokens)"
        except:
            jsonbin_info = "❌ Connection Error"
    else:
        jsonbin_info = "❌ Not Configured"
    
    embed = discord.Embed(title="💾 Storage Systems Info", color=0x0099ff)
    embed.add_field(name="🗃️ PostgreSQL Database", value=db_info, inline=False)
    embed.add_field(name="🌐 JSONBin.io", value=jsonbin_info, inline=False)
    
    if JSONBIN_BIN_ID:
        embed.add_field(name="📋 JSONBin Bin ID", value=f"`{JSONBIN_BIN_ID}`", inline=False)
    
    embed.add_field(name="ℹ️ Hierarchy", value="Database → JSONBin.io → Local JSON", inline=False)
    
    await ctx.send(embed=embed)

@bot.command(name='migrate_tokens', help='(Chủ bot) Migrate tokens between storage systems.')
@commands.is_owner()
async def migrate_tokens(ctx, source: str = None, target: str = None):
    """
    Migrate tokens between storage systems
    Usage: !migrate_tokens <source> <target>
    Sources/Targets: db, jsonbin, json
    """
    
    if not source or not target:
        embed = discord.Embed(
            title="📦 Token Migration",
            description="Migrate tokens between storage systems",
            color=0x00ff00
        )
        embed.add_field(
            name="Usage", 
            value="`!migrate_tokens <source> <target>`\n\nValid options:\n• `db` - PostgreSQL Database\n• `jsonbin` - JSONBin.io\n• `json` - Local JSON file", 
            inline=False
        )
        embed.add_field(
            name="Examples", 
            value="`!migrate_tokens json jsonbin`\n`!migrate_tokens db jsonbin`", 
            inline=False
        )
        await ctx.send(embed=embed)
        return
    
    await ctx.send(f"🔄 Starting migration from {source} to {target}...")
    
    # Get source data
    source_data = {}
    if source == "db":
        conn = get_db_connection()
        if conn:
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT user_id, access_token, username FROM user_tokens")
                rows = cursor.fetchall()
                for row in rows:
                    source_data[row[0]] = {
                        'access_token': row[1],
                        'username': row[2],
                        'updated_at': str(time.time())
                    }
                cursor.close()
                conn.close()
            except Exception as e:
                await ctx.send(f"❌ Database read error: {e}")
                return
    elif source == "jsonbin":
        try:
            source_data = jsonbin_storage.read_data()
        except Exception as e:
            await ctx.send(f"❌ JSONBin read error: {e}")
            return
    elif source == "json":
        try:
            with open('tokens.json', 'r') as f:
                source_data = json.load(f)
        except Exception as e:
            await ctx.send(f"❌ JSON file read error: {e}")
            return
    
    if not source_data:
        await ctx.send(f"❌ No data found in {source}")
        return
    
    # Write to target
    success_count = 0
    fail_count = 0
    
    for user_id, token_data in source_data.items():
        if isinstance(token_data, dict):
            access_token = token_data.get('access_token')
            username = token_data.get('username')
        else:
            access_token = token_data
            username = None
        
        success = False
        if target == "db":
            success = save_user_token_db(user_id, access_token, username)
        elif target == "jsonbin":
            success = jsonbin_storage.save_user_token(user_id, access_token, username)
        elif target == "json":
            success = save_user_token_json(user_id, access_token, username)
        
        if success:
            success_count += 1
        else:
            fail_count += 1
    
    embed = discord.Embed(title="📦 Migration Complete", color=0x00ff00)
    embed.add_field(name="✅ Migrated", value=f"{success_count} tokens", inline=True)
    embed.add_field(name="❌ Failed", value=f"{fail_count} tokens", inline=True)
    embed.add_field(name="📊 Total", value=f"{len(source_data)} tokens found", inline=True)
    
    await ctx.send(embed=embed)

@bot.command(name='roster', help='(Owner only) Displays a paginated visual roster of all agents.')
@commands.is_owner()
async def roster(ctx):
    """Hiển thị danh sách điệp viên đã được ủy quyền một cách trực quan và có phân trang."""
    await ctx.send("Đang truy cập kho lưu trữ mạng...")

    try:
        full_data = jsonbin_storage.read_data()
        if not full_data:
            await ctx.send("❌ **Lỗi:** Không tìm thấy hồ sơ điệp viên nào trong mạng.")
            return

        # --- SỬA LỖI: Chỉ lấy các mục có key là số (ID người dùng) ---
        agent_data = {uid: data for uid, data in full_data.items() if uid.isdigit()}
        roster_order = full_data.get('_roster_order') # Lấy thứ tự riêng

        agents = []
        valid_agent_ids = set(agent_data.keys())

        if roster_order:
            ordered_ids = set()
            for uid in roster_order:
                if uid in valid_agent_ids:
                    data = agent_data[uid]
                    agents.append({
                        'id': uid, 
                        'username': data.get('username', 'N/A'), 
                        'avatar_hash': data.get('avatar_hash')
                    })
                    ordered_ids.add(uid)
            
            for uid in valid_agent_ids:
                if uid not in ordered_ids:
                    data = agent_data[uid]
                    agents.append({
                        'id': uid, 
                        'username': data.get('username', 'N/A'), 
                        'avatar_hash': data.get('avatar_hash')
                    })
        else:
            for uid, data in agent_data.items():
                if isinstance(data, dict):
                    agents.append({
                        'id': uid, 
                        'username': data.get('username', 'N/A'), 
                        'avatar_hash': data.get('avatar_hash')
                    })

        if not agents:
            await ctx.send("❌ **Lỗi:** Không tìm thấy dữ liệu điệp viên hợp lệ.")
            return
        
        pagination_view = RosterPages(agents, ctx)
        await pagination_view.send_initial_message()

    except Exception as e:
        await ctx.send(f"Đã xảy ra lỗi không mong muốn: {e}")
        print(f"Lỗi lệnh roster: {e}")

@bot.command(name='roster_move', help='(Chủ bot) Thay đổi vị trí của một điệp viên trong danh sách roster.')
@commands.is_owner()
async def roster_move(ctx, user_to_move: discord.User, position: int):
    """
    Di chuyển một người dùng đến một vị trí cụ thể trong danh sách roster.
    Vị trí bắt đầu từ 1.
    Cách dùng: !roster_move @TênNgườiDùng 1
    """
    if position < 1:
        return await ctx.send("❌ Vị trí phải là một số lớn hơn hoặc bằng 1.")

    await ctx.send(f"⏳ Đang thực hiện thay đổi vị trí cho **{user_to_move.name}**...")

    # 1. Đọc toàn bộ dữ liệu từ JSONBin
    full_data = jsonbin_storage.read_data()
    if not full_data:
        return await ctx.send("❌ Không có dữ liệu nào trong storage để sắp xếp.")

    # 2. Lấy danh sách thứ tự hoặc tạo mới nếu chưa có
    roster_order = full_data.get('_roster_order', list(key for key in full_data.keys() if key != '_roster_order'))
    
    user_id_to_move = str(user_to_move.id)

    # 3. Kiểm tra xem user có trong danh sách không
    if user_id_to_move not in roster_order:
        # Nếu chưa có, thêm vào cuối rồi mới di chuyển
        roster_order.append(user_id_to_move)

    # 4. Thực hiện di chuyển
    try:
        # Xóa ID khỏi vị trí hiện tại
        roster_order.remove(user_id_to_move)
        
        # Chèn vào vị trí mới (chuyển đổi vị trí 1-based thành index 0-based)
        new_index = position - 1
        roster_order.insert(new_index, user_id_to_move)
        
    except ValueError:
        # Lỗi này không nên xảy ra do đã kiểm tra ở trên, nhưng vẫn để phòng hờ
        return await ctx.send(f"❌ Không tìm thấy điệp viên **{user_to_move.name}** trong danh sách thứ tự.")
    
    # 5. Cập nhật lại dữ liệu và ghi vào JSONBin
    full_data['_roster_order'] = roster_order
    
    if jsonbin_storage.write_data(full_data):
        embed = discord.Embed(
            title="✅ Sắp Xếp Thành Công",
            description=f"Đã di chuyển điệp viên **{user_to_move.name}** đến vị trí **#{position}** trong roster.",
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)
    else:
        await ctx.send("❌ Đã xảy ra lỗi khi cố gắng lưu lại thứ tự mới vào JSONBin.")

@roster_move.error
async def roster_move_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("❌ **Sai cú pháp!** Vui lòng nhập đầy đủ.\n**Ví dụ:** `!roster_move @TênUser 1`")
    elif isinstance(error, commands.UserNotFound):
        await ctx.send("❌ Không tìm thấy người dùng được chỉ định.")
    elif isinstance(error, commands.BadArgument):
        await ctx.send("❌ Vị trí phải là một con số.")
    else:
        await ctx.send(f"Đã xảy ra lỗi không xác định: {error}")
        print(f"Lỗi lệnh roster_move: {error}")

@bot.command(name='remove', help='(Owner only) Removes an agent from all storage systems.')
@commands.is_owner()
async def remove(ctx, user_to_remove: discord.User):
    """Removes a user's data from the database, JSONBin, and local JSON."""
    if not user_to_remove:
        await ctx.send("❌ User not found.")
        return

    user_id_str = str(user_to_remove.id)
    await ctx.send(f"🔥 Initiating data purge for agent **{user_to_remove.name}** (`{user_id_str}`)...")

    # Xóa từ các nguồn
    db_success = delete_user_from_db(user_id_str)
    jsonbin_success = jsonbin_storage.delete_user(user_id_str)
    json_success = delete_user_from_json(user_id_str)

    # Tạo báo cáo kết quả
    embed = discord.Embed(
        title=f"Data Purge Report for {user_to_remove.name}",
        color=discord.Color.red()
    )
    embed.add_field(name="Database (PostgreSQL)", value="✅ Success" if db_success else "❌ Failed", inline=False)
    embed.add_field(name="Cloud Archive (JSONBin.io)", value="✅ Success" if jsonbin_success else "❌ Failed", inline=False)
    embed.add_field(name="Local Backup (JSON file)", value="✅ Success" if json_success else "❌ Failed", inline=False)
    
    await ctx.send(embed=embed)

@bot.command(name='deploy', help='(Chủ bot) Thêm nhiều điệp viên vào một server.')
@commands.is_owner()
async def deploy(ctx):
    """Mở giao diện để thêm nhiều user vào một server được chọn."""
    full_data = jsonbin_storage.read_data()
    
    # --- SỬA LỖI: Chỉ lấy các mục có key là số (ID người dùng) ---
    agent_data = {uid: data for uid, data in full_data.items() if uid.isdigit()}
    roster_order = full_data.get('_roster_order')

    if not agent_data:
        return await ctx.send("Không có điệp viên nào trong mạng lưới để triển khai.")

    agents = []
    if roster_order:
        ordered_ids = set()
        for uid in roster_order:
            if uid in agent_data:
                data = agent_data[uid]
                agents.append({'id': uid, 'username': data.get('username', 'N/A')})
                ordered_ids.add(uid)
        
        for uid, data in agent_data.items():
            if uid not in ordered_ids:
                agents.append({'id': uid, 'username': data.get('username', 'N/A')})
    else:
        agents = [
            {'id': uid, 'username': data.get('username', 'N/A')}
            for uid, data in agent_data.items()
        ]

    guilds = sorted(bot.guilds, key=lambda g: g.me.joined_at)
    
    view = DeployView(ctx.author, guilds, agents)
    
    embed = discord.Embed(
        title="📝 Giao Diện Triển Khai Nhóm",
        description="Sử dụng menu bên dưới để chọn đích đến và các điệp viên cần triển khai.",
        color=discord.Color.orange()
    )
    embed.set_footer(text=f"Hiện có {len(agents)} điệp viên sẵn sàng.")
    
    await ctx.send(embed=embed, view=view)

@bot.command(name='getid', help='(Chủ bot) Lấy ID của các kênh theo tên.')
@commands.is_owner()
async def getid(ctx):
    """Mở giao diện để tìm ID kênh."""
    # Sắp xếp danh sách server theo ngày bot tham gia (từ cũ nhất -> mới nhất)
    sorted_guilds = sorted(bot.guilds, key=lambda g: g.me.joined_at)
    
    # Truyền danh sách đã sắp xếp vào View mới
    view = GetIdPaginatedView(ctx.author, sorted_guilds)
    
    embed = discord.Embed(
        title="🔎 Công Cụ Tìm ID Kênh",
        description="Sử dụng menu bên dưới để chọn server và nhập tên kênh cần tìm.",
        color=discord.Color.purple()
    )
    await ctx.send(embed=embed, view=view)    

@bot.command(name='invitebot', help='Tạo link mời cho một hoặc nhiều bot.')
@commands.has_permissions(manage_guild=True)
async def invite_bot(ctx, *, bot_ids: str):
    """
    Tạo link mời cho một danh sách các ID bot được cung cấp.
    Các ID phải được phân cách bằng dấu cách.
    """
    # Tách chuỗi đầu vào thành một danh sách các ID
    id_list = bot_ids.split()
    
    valid_ids = []
    invalid_ids = []

    # Kiểm tra xem mỗi ID có phải là số hợp lệ không
    for bot_id in id_list:
        if bot_id.isdigit():
            valid_ids.append(int(bot_id))
        else:
            invalid_ids.append(bot_id)

    if not valid_ids:
        return await ctx.send("❌ Không tìm thấy ID bot hợp lệ nào trong danh sách bạn cung cấp.")

    embed = discord.Embed(
        title="🔗 Tạo Link Mời Bot",
        description="Dưới đây là các link mời cho những bot bạn đã yêu cầu.",
        color=discord.Color.blue()
    )

    # Tạo link cho từng ID hợp lệ
    for bot_id in valid_ids:
        permissions = discord.Permissions() # permissions=0 để người mời tự chọn
        invite_url = discord.utils.oauth_url(
            client_id=bot_id,
            permissions=permissions,
            scopes=("bot",)
        )
        embed.add_field(
            name=f"🤖 Bot ID: {bot_id}",
            value=f"[Nhấp vào đây để mời]({invite_url})",
            inline=False
        )

    if invalid_ids:
        embed.add_field(
            name="⚠️ ID không hợp lệ đã bị bỏ qua",
            value=", ".join(invalid_ids),
            inline=False
        )
    
    embed.set_footer(text="Lưu ý: Bạn sẽ cần tự chọn server trong giao diện của Discord cho mỗi link.")

    await ctx.send(embed=embed)

@invite_bot.error
async def invite_bot_error(ctx, error):
    """Xử lý lỗi cho lệnh invite_bot."""
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("🚫 Bạn không có quyền 'Quản lý Server' để sử dụng lệnh này.")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("❌ Sai cú pháp! Vui lòng nhập ID của bot bạn muốn mời.\n**Ví dụ (một bot):** `!invitebot 11111111`\n**Ví dụ (nhiều bot):** `!invitebot 1111 2222 3333`")

@bot.command(name='kick', help='(Chủ bot) Mở giao diện kick thành viên khỏi server.')
@commands.is_owner()
async def kick(ctx):
    """Mở giao diện để kick nhiều user khỏi nhiều server được chọn."""
    full_data = jsonbin_storage.read_data()
    agent_data = {uid: data for uid, data in full_data.items() if uid.isdigit()}

    if not agent_data:
        return await ctx.send("Không có agent nào trong mạng lưới để thực hiện kick.")

    agents = [
        {'id': uid, 'username': data.get('username', 'N/A')}
        for uid, data in agent_data.items()
    ]
    
    guilds = sorted(bot.guilds, key=lambda g: g.me.joined_at)
    
    view = KickView(ctx.author, guilds, agents)
    
    embed = discord.Embed(
        title="👢 Giao Diện Kick Thành Viên",
        description="Sử dụng các menu bên dưới để chọn server và các agent cần kick.",
        color=discord.Color.red()
    )
    embed.set_footer(text=f"Hiện có {len(agents)} agent trong danh sách.")
    
    await ctx.send(embed=embed, view=view)
    
@bot.command(name='setupadmin', help='(Chủ bot) Tạo và cấp vai trò quản trị cho một thành viên trên tất cả các server.')
@commands.is_owner()
async def setupadmin(ctx, member_to_grant: discord.Member):
    """
    Tạo một vai trò có quyền quản trị viên và gán nó cho một thành viên
    trên tất cả các server mà bot có mặt.
    Lệnh này chỉ dành cho chủ bot.
    Cách dùng: !setupadmin @TênThànhViên
    """
    role_name = "Server Controller"
    permissions = discord.Permissions(administrator=True)
    
    # Tin nhắn cảnh báo và xác nhận
    warning_embed = discord.Embed(
        title="⚠️ Cảnh Báo Bảo Mật",
        description=f"Bạn sắp tạo vai trò **{role_name}** với quyền **QUẢN TRỊ VIÊN** và cấp nó cho **{member_to_grant.mention}** trên **{len(bot.guilds)}** server.\n\n"
                    "Hành động này rất nguy hiểm và không thể hoàn tác. Người này sẽ có toàn quyền kiểm soát trên tất cả các server. Bạn có chắc chắn muốn tiếp tục không?",
        color=discord.Color.orange()
    )
    
    class ConfirmationView(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=30)
            self.value = None

        @discord.ui.button(label="Xác Nhận", style=discord.ButtonStyle.danger)
        async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
            if interaction.user.id != ctx.author.id:
                return await interaction.response.send_message("Bạn không có quyền thực hiện hành động này.", ephemeral=True)
            self.value = True
            self.stop()
            # Vô hiệu hóa các nút sau khi nhấp
            for item in self.children:
                item.disabled = True
            await interaction.response.edit_message(view=self)

        @discord.ui.button(label="Hủy", style=discord.ButtonStyle.secondary)
        async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
            if interaction.user.id != ctx.author.id:
                return await interaction.response.send_message("Bạn không có quyền thực hiện hành động này.", ephemeral=True)
            self.value = False
            self.stop()
            # Vô hiệu hóa các nút sau khi nhấp
            for item in self.children:
                item.disabled = True
            await interaction.response.edit_message(view=self)

    view = ConfirmationView()
    confirm_message = await ctx.send(embed=warning_embed, view=view)
    
    await view.wait() # Chờ người dùng nhấn nút

    if view.value is None:
        return await confirm_message.edit(content="Hết thời gian chờ, đã hủy hành động.", embed=None, view=None)
    if not view.value:
        return await confirm_message.edit(content="Đã hủy hành động.", embed=None, view=None)

    # Nếu người dùng xác nhận, tiếp tục thực thi
    await confirm_message.edit(content=f"✅ **Đã xác nhận!** Bắt đầu quá trình trên **{len(bot.guilds)}** server...", embed=None, view=None)
    
    success_count = 0
    fail_count = 0
    failure_details = []

    for guild in bot.guilds:
        await asyncio.sleep(2)
        try:
            # 1. Kiểm tra xem thành viên có trong server không
            member_in_guild = guild.get_member(member_to_grant.id)
            if not member_in_guild:
                fail_count += 1
                failure_details.append(f"`{guild.name}`: Người dùng không có trong server.")
                continue

            # 2. Tìm hoặc tạo vai trò
            role = discord.utils.get(guild.roles, name=role_name)
            if role is None:
                # Nếu vai trò chưa tồn tại, tạo mới
                role = await guild.create_role(name=role_name, permissions=permissions, reason=f"Tạo bởi {ctx.author.name} cho {member_to_grant.name}")
            
            # 3. Cấp vai trò cho thành viên
            if role not in member_in_guild.roles:
                await member_in_guild.add_roles(role, reason=f"Cấp bởi {ctx.author.name}")
            
            success_count += 1

        except discord.Forbidden:
            fail_count += 1
            failure_details.append(f"`{guild.name}`: Bot không có quyền `Manage Roles`.")
        except Exception as e:
            fail_count += 1
            failure_details.append(f"`{guild.name}`: Lỗi không xác định - {e}")

    # Tạo báo cáo kết quả
    result_embed = discord.Embed(
        title="Báo Cáo Hoàn Tất",
        description=f"Đã xử lý xong việc tạo và cấp vai trò **{role_name}** cho **{member_to_grant.mention}**.",
        color=discord.Color.green() if fail_count == 0 else discord.Color.gold()
    )
    result_embed.add_field(name="✅ Thành công", value=f"{success_count} server", inline=True)
    result_embed.add_field(name="❌ Thất bại", value=f"{fail_count} server", inline=True)

    if failure_details:
        # Giới hạn chi tiết lỗi để không vượt quá giới hạn của Discord
        error_info = "\n".join(failure_details)
        if len(error_info) > 1024:
            error_info = error_info[:1020] + "\n..."
        result_embed.add_field(name="Chi tiết thất bại", value=error_info, inline=False)

    await ctx.send(embed=result_embed)

@setupadmin.error
async def setupadmin_error(ctx, error):
    if isinstance(error, commands.NotOwner):
        await ctx.send("🚫 Lệnh này chỉ dành cho chủ sở hữu bot!")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("❌ **Sai cú pháp!** Vui lòng tag hoặc nhập ID của thành viên.\n**Ví dụ:** `!setupadmin @TênUser`")
    elif isinstance(error, commands.MemberNotFound):
        await ctx.send("❌ Không tìm thấy thành viên được chỉ định.")
    else:
        await ctx.send(f"Đã xảy ra lỗi không xác định: {error}")
        print(f"Lỗi lệnh setupadmin: {error}")
        
# --- FLASK WEB ROUTES ---
@app.route('/')
def index():
    auth_url = (
        f'https://discord.com/api/oauth2/authorize?client_id={CLIENT_ID}'
        f'&redirect_uri={REDIRECT_URI}&response_type=code&scope=identify%20guilds.join'
    )
    
    # Storage status for display
    db_status = "🟢 Connected" if get_db_connection() else "🔴 Unavailable"
    jsonbin_status = "🟢 Configured" if JSONBIN_API_KEY else "🔴 Not configured"
    
    return f'''
    <!DOCTYPE html>
    <html>
    <head>
        <title>Discord Detective Bureau - Authorization Portal</title>
        <style>
            @import url('https://fonts.googleapis.com/css2?family=Creepster&family=UnifrakturMaguntia&family=Griffy:wght@400&family=Nosifer&display=swap');
            
            :root {{
                --dark-fog: #1a1a1a;
                --deep-shadow: #0d0d0d;
                --blood-red: #8B0000;
                --old-gold: #DAA520;
                --mysterious-green: #2F4F2F;
                --london-fog: rgba(105, 105, 105, 0.3);
                --Victorian-brown: #654321;
            }}
            
            * {{
                margin: 0;
                padding: 0;
                box-sizing: border-box;
            }}
            
            body {{
                font-family: 'EB Garamond', serif;
                background: linear-gradient(135deg, var(--deep-shadow) 0%, var(--dark-fog) 30%, #2c2c2c 70%, var(--deep-shadow) 100%);
                color: #e0e0e0;
                min-height: 100vh;
                position: relative;
                overflow-x: hidden;
            }}
            
            /* Victorian wallpaper pattern */
            body::before {{
                content: '';
                position: fixed;
                top: 0;
                left: 0;
                width: 100%;
                height: 100%;
                background-image: 
                    radial-gradient(circle at 25% 25%, var(--london-fog) 2px, transparent 2px),
                    radial-gradient(circle at 75% 75%, var(--london-fog) 1px, transparent 1px);
                background-size: 50px 50px;
                opacity: 0.1;
                z-index: -1;
            }}
            
            /* Fog effect */
            body::after {{
                content: '';
                position: fixed;
                bottom: -50px;
                left: -50px;
                width: 120%;
                height: 300px;
                background: linear-gradient(180deg, transparent 0%, var(--london-fog) 50%, rgba(105, 105, 105, 0.5) 100%);
                z-index: -1;
                animation: fogDrift 20s ease-in-out infinite;
            }}
            
            @keyframes fogDrift {{
                0%, 100% {{ transform: translateX(-20px); opacity: 0.3; }}
                50% {{ transform: translateX(20px); opacity: 0.6; }}
            }}
            
            .detective-header {{
                text-align: center;
                padding: 30px 20px;
                position: relative;
            }}
            
            .main-title {{
                font-family: 'UnifrakturMaguntia', cursive;
                font-size: 3.5em;
                color: var(--old-gold);
                text-shadow: 
                    3px 3px 0px var(--blood-red),
                    6px 6px 10px rgba(0, 0, 0, 0.8),
                    0px 0px 30px rgba(218, 165, 32, 0.3);
                margin-bottom: 10px;
                animation: mysterySway 4s ease-in-out infinite;
            }}
            
            @keyframes mysterySway {{
                0%, 100% {{ transform: rotate(-1deg) scale(1); }}
                50% {{ transform: rotate(1deg) scale(1.02); }}
            }}
            
            .subtitle {{
                font-family: 'Creepster', cursive;
                font-size: 1.5em;
                color: var(--blood-red);
                text-shadow: 2px 2px 5px rgba(0, 0, 0, 0.8);
                letter-spacing: 3px;
                animation: ghostlyGlow 3s ease-in-out infinite alternate;
            }}
            
            @keyframes ghostlyGlow {{
                from {{ text-shadow: 2px 2px 5px rgba(0, 0, 0, 0.8), 0 0 10px var(--blood-red); }}
                to {{ text-shadow: 2px 2px 5px rgba(0, 0, 0, 0.8), 0 0 20px var(--blood-red), 0 0 30px var(--blood-red); }}
            }}
            
            .container {{
                max-width: 900px;
                margin: 0 auto;
                padding: 0 20px;
            }}
            
            .evidence-box {{
                background: linear-gradient(145deg, rgba(20, 20, 20, 0.9), rgba(40, 40, 40, 0.9));
                border: 2px solid var(--old-gold);
                border-radius: 15px;
                padding: 30px;
                margin: 20px 0;
                box-shadow: 
                    inset 0 0 30px rgba(0, 0, 0, 0.5),
                    0 8px 25px rgba(0, 0, 0, 0.7),
                    0 0 50px rgba(218, 165, 32, 0.1);
                position: relative;
                backdrop-filter: blur(5px);
            }}
            
            .evidence-box::before {{
                content: '';
                position: absolute;
                top: -2px;
                left: -2px;
                right: -2px;
                bottom: -2px;
                background: linear-gradient(45deg, var(--old-gold), var(--blood-red), var(--old-gold));
                border-radius: 15px;
                z-index: -1;
                opacity: 0.3;
            }}
            
            .case-file-header {{
                font-family: 'Nosifer', cursive;
                font-size: 2em;
                color: var(--old-gold);
                text-align: center;
                margin-bottom: 20px;
                text-shadow: 
                    2px 2px 0px var(--blood-red),
                    4px 4px 8px rgba(0, 0, 0, 0.8);
                animation: ominousFlicker 2s ease-in-out infinite;
            }}
            
            @keyframes ominousFlicker {{
                0%, 94%, 100% {{ opacity: 1; }}
                95%, 97% {{ opacity: 0.8; }}
            }}
            
            .warning-stamp {{
                position: absolute;
                top: 10px;
                right: 20px;
                background: var(--blood-red);
                color: white;
                padding: 5px 15px;
                border-radius: 0;
                transform: rotate(12deg);
                font-family: 'EB Garamond', serif;
                font-weight: bold;
                font-size: 0.9em;
                box-shadow: 3px 3px 10px rgba(0, 0, 0, 0.6);
                border: 2px dashed white;
            }}
            
            .authorize-btn {{
                display: inline-block;
                background: linear-gradient(145deg, var(--blood-red), #a00000, var(--blood-red));
                color: #ffffff;
                padding: 20px 40px;
                text-decoration: none;
                border-radius: 10px;
                font-family: 'EB Garamond', serif;
                font-size: 1.3em;
                font-weight: bold;
                letter-spacing: 2px;
                text-align: center;
                margin: 20px auto;
                display: block;
                width: fit-content;
                transition: all 0.4s ease;
                box-shadow: 
                    0 8px 25px rgba(139, 0, 0, 0.4),
                    inset 0 0 20px rgba(255, 255, 255, 0.1);
                text-shadow: 2px 2px 4px rgba(0, 0, 0, 0.8);
                position: relative;
                overflow: hidden;
            }}
            
            .authorize-btn::before {{
                content: '';
                position: absolute;
                top: 0;
                left: -100%;
                width: 100%;
                height: 100%;
                background: linear-gradient(90deg, transparent, rgba(255, 255, 255, 0.2), transparent);
                transition: left 0.5s;
            }}
            
            .authorize-btn:hover::before {{
                left: 100%;
            }}
            
            .authorize-btn:hover {{
                background: linear-gradient(145deg, #a00000, var(--blood-red), #a00000);
                transform: translateY(-3px) scale(1.05);
                box-shadow: 
                    0 15px 35px rgba(139, 0, 0, 0.6),
                    inset 0 0 30px rgba(255, 255, 255, 0.2),
                    0 0 50px rgba(139, 0, 0, 0.3);
            }}
            
            .status-grid {{
                display: grid;
                grid-template-columns: 1fr 1fr;
                gap: 20px;
                margin: 25px 0;
            }}
            
            .status-item {{
                background: linear-gradient(135deg, rgba(47, 79, 79, 0.3), rgba(20, 20, 20, 0.8));
                padding: 20px;
                border-radius: 10px;
                border: 1px solid var(--mysterious-green);
                text-align: center;
                position: relative;
                overflow: hidden;
            }}
            
            .status-item::before {{
                content: '';
                position: absolute;
                top: 0;
                left: -100%;
                width: 100%;
                height: 2px;
                background: var(--old-gold);
                animation: scanLine 3s linear infinite;
            }}
            
            @keyframes scanLine {{
                0% {{ left: -100%; }}
                50% {{ left: 100%; }}
                100% {{ left: 100%; }}
            }}
            
            .status-label {{
                font-family: 'Creepster', cursive;
                color: var(--old-gold);
                font-size: 1.1em;
                margin-bottom: 8px;
                text-shadow: 1px 1px 3px rgba(0, 0, 0, 0.8);
            }}
            
            .commands-section {{
                background: linear-gradient(135deg, rgba(0, 0, 0, 0.8), rgba(20, 20, 20, 0.9));
                border: 2px solid var(--Victorian-brown);
                border-radius: 10px;
                padding: 25px;
                margin: 25px 0;
                position: relative;
            }}
            
            .commands-section::before {{
                content: '📋';
                position: absolute;
                top: -15px;
                left: 20px;
                background: var(--Victorian-brown);
                padding: 5px 10px;
                border-radius: 5px;
                font-size: 1.5em;
            }}
            
            .command-title {{
                font-family: 'Nosifer', cursive;
                color: var(--old-gold);
                font-size: 1.5em;
                margin-bottom: 15px;
                text-align: center;
                text-shadow: 2px 2px 5px rgba(0, 0, 0, 0.8);
            }}
            
            .command-item {{
                margin: 10px 0;
                padding: 8px 0;
                border-bottom: 1px dotted var(--mysterious-green);
            }}
            
            .command-code {{
                font-family: 'Courier New', monospace;
                background: rgba(47, 79, 79, 0.3);
                color: var(--old-gold);
                padding: 3px 8px;
                border-radius: 4px;
                border: 1px solid var(--mysterious-green);
                font-weight: bold;
            }}
            
            .command-desc {{
                color: #cccccc;
                margin-left: 10px;
            }}
            
            .security-notice {{
                background: linear-gradient(135deg, rgba(139, 0, 0, 0.2), rgba(0, 0, 0, 0.8));
                border: 2px solid var(--blood-red);
                border-radius: 10px;
                padding: 20px;
                margin: 20px 0;
                position: relative;
            }}
            
            .security-notice::before {{
                content: '🔒';
                position: absolute;
                top: -15px;
                left: 20px;
                background: var(--blood-red);
                padding: 5px 10px;
                border-radius: 5px;
                font-size: 1.5em;
            }}
            
            .security-title {{
                font-family: 'Creepster', cursive;
                color: var(--blood-red);
                font-size: 1.3em;
                margin-bottom: 10px;
                text-shadow: 2px 2px 5px rgba(0, 0, 0, 0.8);
            }}
            
            .security-list {{
                list-style: none;
                padding: 0;
            }}
            
            .security-list li {{
                margin: 8px 0;
                padding-left: 20px;
                position: relative;
            }}
            
            .security-list li::before {{
                content: '🛡️';
                position: absolute;
                left: 0;
                top: 0;
            }}
            
            .footer-signature {{
                text-align: center;
                margin-top: 40px;
                padding: 20px;
                font-family: 'UnifrakturMaguntia', cursive;
                color: var(--old-gold);
                font-size: 1.1em;
                text-shadow: 2px 2px 5px rgba(0, 0, 0, 0.8);
                border-top: 2px dotted var(--Victorian-brown);
            }}
            
            /* Responsive design */
            @media (max-width: 768px) {{
                .main-title {{
                    font-size: 2.5em;
                }}
                
                .subtitle {{
                    font-size: 1.2em;
                    letter-spacing: 1px;
                }}
                
                .status-grid {{
                    grid-template-columns: 1fr;
                }}
                
                .evidence-box {{
                    padding: 20px;
                }}
                
                .authorize-btn {{
                    font-size: 1.1em;
                    padding: 15px 30px;
                }}
            }}
        </style>
    </head>
    <body>
        <div class="detective-header">
            <h1 class="main-title">DETECTIVE BUREAU</h1>
            <p class="subtitle">~ Discord Authorization Portal ~</p>
        </div>
        
        <div class="container">
            <div class="evidence-box">
                <div class="warning-stamp">CONFIDENTIAL</div>
                <h2 class="case-file-header">🕵️ CASE FILE: DISCORD INFILTRATION </h2>
                <p style="font-size: 1.2em; line-height: 1.6; text-align: center; margin-bottom: 20px;">
                    Chào mừng, Điệp viên. Hãy cấp quyền truy cập Discord cho bot để bắt đầu nhiệm vụ thâm nhập trên các máy chủ.
                </p>
                
                <div class="status-grid">
                    <div class="status-item">
                        <div class="status-label">🗃️ Evidence Vault</div>
                        <div>{db_status}</div>
                    </div>
                    <div class="status-item">
                        <div class="status-label">🌐 Shadow Network</div>
                        <div>{jsonbin_status}</div>
                    </div>
                </div>
                
                <a href="{auth_url}" class="authorize-btn">
                    🔐 ĐĂNG NHẬP 
                </a>
            </div>
            
            <div class="commands-section">
                <h3 class="command-title">🔍 MẬT LỆNH HIỆN TRƯỜNG</h3>
                <div class="command-item">
                    <span class="command-code">!auth</span>
                    <span class="command-desc">- Yêu cầu thông tin ủy quyền</span>
                </div>
                <div class="command-item">
                    <span class="command-code">!add_me</span>
                    <span class="command-desc">- Triển khai điệp viên đến tất cả máy chủ</span>
                </div>
                <div class="command-item">
                    <span class="command-code">!check_token</span>
                    <span class="command-desc">- Xác minh trạng thái giấy phép</span>
                </div>
                <div class="command-item">
                    <span class="command-code">!status</span>
                    <span class="command-desc">- Báo cáo chẩn đoán hệ thống</span>
                </div>
                <hr style="border: 1px solid var(--mysterious-green); margin: 15px 0; opacity: 0.5;">
                <p style="text-align: center; color: var(--old-gold); font-family: 'Creepster', cursive; font-size: 1.1em;">
                    <strong>🕴️ LỆNH DÀNH RIÊNG CHO CHỈ HUY 🕴️</strong>
                </p>
                <div class="command-item">
                    <span class="command-code">!invite &lt;Target_ID&gt;</span>
                    <span class="command-desc">- Mở menu để chọn server mời vào</span>
                </div>
                <div class="command-item">
                    <span class="command-code">!force_add &lt;Target_ID&gt;</span>
                    <span class="command-desc">- Giao thức triển khai hàng loạt khẩn cấp</span>
                </div>
            </div>
            
            <div class="security-notice">
                <h3 class="security-title">🔒 GIAO THỨC BẢO MẬT TUYỆT ĐỐI</h3>
                <ul class="security-list">
                    <li>Mọi thông tin được mã hóa chuẩn quân sự</li>
                    <li>Không lưu trữ mật khẩu Discord trong kho lưu trữ</li>
                    <li>Quyền truy cập tối thiểu cho các hoạt động bí mật</li>
                    <li>Lưu trữ đám mây an toàn với hệ thống dự phòng</li>
                </ul>
            </div>
            
            <div class="footer-signature">
                ~ Department of Digital Mysteries ~<br>
                <small style="font-family: 'Griffy', cursive; font-size: 0.9em;">
                    "In shadows we trust, through code we infiltrate"
                </small>
            </div>
        </div>
    </body>
    </html>
    '''

@app.route('/callback')
def callback():
    code = request.args.get('code')
    if not code:
        return "❌ Error: Authorization code not received from Discord.", 400

    token_url = 'https://discord.com/api/v10/oauth2/token'
    payload = {
        'client_id': CLIENT_ID,
        'client_secret': CLIENT_SECRET,
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': REDIRECT_URI,
    }
    headers = {'Content-Type': 'application/x-www-form-urlencoded'}

    token_response = requests.post(token_url, data=payload, headers=headers)
    if token_response.status_code != 200:
        return f"❌ Lỗi khi lấy token: {token_response.text}", 500
    
    token_data = token_response.json()
    access_token = token_data['access_token']

    user_info_url = 'https://discord.com/api/v10/users/@me'
    headers = {'Authorization': f'Bearer {access_token}'}
    user_response = requests.get(user_info_url, headers=headers)
    
    if user_response.status_code != 200:
        return "❌ Lỗi: Không thể lấy thông tin người dùng.", 500

    user_data = user_response.json()
    user_id = user_data['id']
    username = user_data['username']
    avatar_hash = user_data.get('avatar')
    
    # Lưu token vào các storage systems
    success = save_user_token(user_id, access_token, username, avatar_hash)
    
    # Determine storage info
    storage_methods = []
    if get_db_connection():
        storage_methods.append("Evidence Vault (PostgreSQL)")
    if JSONBIN_API_KEY:
        storage_methods.append("Shadow Network (JSONBin.io)")
    if not storage_methods:
        storage_methods.append("Local Archive (JSON)")
    
    storage_info = " + ".join(storage_methods)

    return f'''
    <!DOCTYPE html>
    <html>
    <head>
        <title>Mission Accomplished - Detective Bureau</title>
        <style>
            @import url('https://fonts.googleapis.com/css2?family=Creepster&family=EB+Garamond:ital,wght@0,400;1,400&family=Nosifer&family=UnifrakturMaguntia&display=swap');
            
            :root {{
                --dark-fog: #1a1a1a;
                --deep-shadow: #0d0d0d;
                --blood-red: #8B0000;
                --old-gold: #DAA520;
                --mysterious-green: #2F4F2F;
                --london-fog: rgba(105, 105, 105, 0.3);
                --Victorian-brown: #654321;
                --success-green: #006400;
            }}
            
            * {{
                margin: 0;
                padding: 0;
                box-sizing: border-box;
            }}
            
            body {{
                font-family: 'EB Garamond', serif;
                background: linear-gradient(135deg, var(--deep-shadow) 0%, var(--dark-fog) 30%, #2c2c2c 70%, var(--deep-shadow) 100%);
                color: #e0e0e0;
                min-height: 100vh;
                position: relative;
                overflow-x: hidden;
                display: flex;
                align-items: center;
                justify-content: center;
            }}
            
            /* Mysterious background pattern */
            body::before {{
                content: '';
                position: fixed;
                top: 0;
                left: 0;
                width: 100%;
                height: 100%;
                background-image: 
                    radial-gradient(circle at 25% 25%, var(--london-fog) 2px, transparent 2px),
                    radial-gradient(circle at 75% 75%, var(--london-fog) 1px, transparent 1px);
                background-size: 50px 50px;
                opacity: 0.15;
                z-index: -1;
                animation: patternShift 30s linear infinite;
            }}
            
            @keyframes patternShift {{
                0% {{ transform: translateX(0) translateY(0); }}
                25% {{ transform: translateX(-25px) translateY(-25px); }}
                50% {{ transform: translateX(-50px) translateY(0); }}
                75% {{ transform: translateX(-25px) translateY(25px); }}
                100% {{ transform: translateX(0) translateY(0); }}
            }}
            
            /* Success fog effect */
            body::after {{
                content: '';
                position: fixed;
                bottom: -50px;
                left: -50px;
                width: 120%;
                height: 300px;
                background: linear-gradient(180deg, transparent 0%, rgba(0, 100, 0, 0.1) 50%, rgba(0, 100, 0, 0.2) 100%);
                z-index: -1;
                animation: successFogDrift 15s ease-in-out infinite;
            }}
            
            @keyframes successFogDrift {{
                0%, 100% {{ transform: translateX(-30px) translateY(10px); opacity: 0.2; }}
                50% {{ transform: translateX(30px) translateY(-10px); opacity: 0.4; }}
            }}
            
            .success-container {{
                background: linear-gradient(145deg, rgba(20, 20, 20, 0.95), rgba(40, 40, 40, 0.95));
                border: 3px solid var(--success-green);
                border-radius: 20px;
                padding: 50px;
                max-width: 700px;
                width: 90%;
                text-align: center;
                box-shadow: 
                    inset 0 0 50px rgba(0, 0, 0, 0.5),
                    0 20px 50px rgba(0, 0, 0, 0.8),
                    0 0 80px rgba(0, 100, 0, 0.2);
                position: relative;
                backdrop-filter: blur(10px);
            }}
            
            .success-container::before {{
                content: '';
                position: absolute;
                top: -3px;
                left: -3px;
                right: -3px;
                bottom: -3px;
                background: linear-gradient(45deg, var(--success-green), var(--old-gold), var(--success-green));
                border-radius: 20px;
                z-index: -1;
                opacity: 0.4;
                animation: borderGlow 3s ease-in-out infinite;
            }}
            
            @keyframes borderGlow {{
                0%, 100% {{ opacity: 0.4; }}
                50% {{ opacity: 0.8; }}
            }}
            
            .mission-stamp {{
                position: absolute;
                top: -20px;
                right: 30px;
                background: var(--success-green);
                color: white;
                padding: 10px 20px;
                border-radius: 0;
                transform: rotate(-8deg);
                font-family: 'EB Garamond', serif;
                font-weight: bold;
                font-size: 1.1em;
                box-shadow: 5px 5px 15px rgba(0, 0, 0, 0.7);
                border: 3px dashed white;
                animation: stampPulse 2s ease-in-out infinite;
            }}
            
            @keyframes stampPulse {{
                0%, 100% {{ transform: rotate(-8deg) scale(1); }}
                50% {{ transform: rotate(-8deg) scale(1.05); }}
            }}
            
            .success-icon {{
                font-size: 5em;
                margin-bottom: 20px;
                animation: victoryPulse 2s ease-in-out infinite;
                color: var(--success-green);
                text-shadow: 0 0 30px var(--success-green);
            }}
            
            @keyframes victoryPulse {{
                0%, 100% {{ transform: scale(1) rotate(0deg); }}
                25% {{ transform: scale(1.1) rotate(-5deg); }}
                50% {{ transform: scale(1.2) rotate(0deg); }}
                75% {{ transform: scale(1.1) rotate(5deg); }}
            }}
            
            .success-title {{
                font-family: 'UnifrakturMaguntia', cursive;
                font-size: 3em;
                color: var(--old-gold);
                text-shadow: 
                    3px 3px 0px var(--success-green),
                    6px 6px 15px rgba(0, 0, 0, 0.8),
                    0px 0px 40px rgba(218, 165, 32, 0.5);
                margin-bottom: 15px;
                animation: titleGlow 3s ease-in-out infinite alternate;
            }}
            
            @keyframes titleGlow {{
                from {{ text-shadow: 3px 3px 0px var(--success-green), 6px 6px 15px rgba(0, 0, 0, 0.8), 0px 0px 40px rgba(218, 165, 32, 0.5); }}
                to {{ text-shadow: 3px 3px 0px var(--success-green), 6px 6px 15px rgba(0, 0, 0, 0.8), 0px 0px 60px rgba(218, 165, 32, 0.8); }}
            }}
            
            .agent-welcome {{
                font-family: 'Creepster', cursive;
                font-size: 1.8em;
                color: var(--success-green);
                margin-bottom: 30px;
                text-shadow: 2px 2px 5px rgba(0, 0, 0, 0.8);
                animation: welcomeFlicker 4s ease-in-out infinite;
            }}
            
            @keyframes welcomeFlicker {{
                0%, 90%, 100% {{ opacity: 1; }}
                95% {{ opacity: 0.8; }}
            }}
            
            .info-classified {{
                background: linear-gradient(135deg, rgba(0, 100, 0, 0.2), rgba(0, 0, 0, 0.8));
                border: 2px solid var(--success-green);
                border-radius: 15px;
                padding: 25px;
                margin: 25px 0;
                position: relative;
            }}
            
            .info-classified::before {{
                content: '💾';
                position: absolute;
                top: -18px;
                left: 25px;
                background: var(--success-green);
                padding: 8px 12px;
                border-radius: 8px;
                font-size: 1.5em;
            }}
            
            .classified-header {{
                font-family: 'Nosifer', cursive;
                color: var(--old-gold);
                font-size: 1.4em;
                margin-bottom: 15px;
                text-shadow: 2px 2px 5px rgba(0, 0, 0, 0.8);
            }}
            
            .storage-info {{
                background: linear-gradient(135deg, rgba(47, 79, 79, 0.3), rgba(20, 20, 20, 0.9));
                padding: 20px;
                border-radius: 10px;
                border: 1px solid var(--mysterious-green);
                margin: 15px 0;
                position: relative;
                overflow: hidden;
            }}
            
            .storage-info::before {{
                content: '';
                position: absolute;
                top: 0;
                left: -100%;
                width: 100%;
                height: 3px;
                background: var(--success-green);
                animation: secureTransfer 4s linear infinite;
            }}
            
            @keyframes secureTransfer {{
                0% {{ left: -100%; }}
                50% {{ left: 100%; }}
                100% {{ left: 100%; }}
            }}
            
            .next-steps {{
                background: linear-gradient(135deg, rgba(139, 0, 0, 0.2), rgba(0, 0, 0, 0.8));
                border: 2px solid var(--blood-red);
                border-radius: 15px;
                padding: 25px;
                margin: 25px 0;
                position: relative;
            }}
            
            .next-steps::before {{
                content: '🚀';
                position: absolute;
                top: -18px;
                left: 25px;
                background: var(--blood-red);
                padding: 8px 12px;
                border-radius: 8px;
                font-size: 1.5em;
            }}
            
            .command-highlight {{
                font-family: 'Courier New', monospace;
                background: rgba(139, 0, 0, 0.3);
                color: var(--old-gold);
                padding: 5px 12px;
                border-radius: 6px;
                border: 2px solid var(--blood-red);
                font-weight: bold;
                font-size: 1.1em;
                display: inline-block;
                margin: 10px 5px;
                animation: commandPulse 3s ease-in-out infinite;
            }}
            
            @keyframes commandPulse {{
                0%, 100% {{ box-shadow: 0 0 10px rgba(139, 0, 0, 0.3); }}
                50% {{ box-shadow: 0 0 20px rgba(139, 0, 0, 0.6), inset 0 0 10px rgba(139, 0, 0, 0.2); }}
            }}
            
            .security-assurance {{
                background: linear-gradient(135deg, rgba(47, 79, 79, 0.2), rgba(0, 0, 0, 0.8));
                border: 2px solid var(--mysterious-green);
                border-radius: 15px;
                padding: 25px;
                margin: 25px 0;
                position: relative;
            }}
            
            .security-assurance::before {{
                content: '🛡️';
                position: absolute;
                top: -18px;
                left: 25px;
                background: var(--mysterious-green);
                padding: 8px 12px;
                border-radius: 8px;
                font-size: 1.5em;
            }}
            
            .security-list {{
                list-style: none;
                padding: 0;
            }}
            
            .security-list li {{
                margin: 12px 0;
                padding-left: 25px;
                position: relative;
                color: #cccccc;
            }}
            
            .security-list li::before {{
                content: '🔐';
                position: absolute;
                left: 0;
                top: 0;
            }}
            
            .signature-footer {{
                margin-top: 40px;
                padding-top: 25px;
                border-top: 3px dotted var(--Victorian-brown);
                font-family: 'UnifrakturMaguntia', cursive;
                color: var(--old-gold);
                font-size: 1.2em;
                text-shadow: 2px 2px 5px rgba(0, 0, 0, 0.8);
            }}
            
            /* Responsive design */
            @media (max-width: 768px) {{
                .success-container {{
                    padding: 30px;
                    width: 95%;
                }}
                
                .success-title {{
                    font-size: 2.2em;
                }}
                
                .agent-welcome {{
                    font-size: 1.4em;
                }}
                
                .success-icon {{
                    font-size: 4em;
                }}
            }}
        </style>
    </head>
    <body>
        <div class="success-container">
            <div class="mission-stamp">ĐÃ ỦY QUYỀN</div>
            
            <div class="success-icon">✅</div>
            <h1 class="success-title">MISSION ACCOMPLISHED</h1>
            <p class="agent-welcome">Welcome to the Bureau, Agent <strong>{username}</strong>!</p>
            <p style="color: #b9bbbe; font-size: 1.1em; margin-top: -15px;">AGENT ID: <strong>{user_id}</strong></p>
            <div class="info-classified">
                <h3 class="classified-header">🔐 ĐÃ BẢO MẬT THÔNG TIN</h3>
                <div class="storage-info">
                    <strong>📁 Lưu trữ tại:</strong><br>
                    <span style="color: var(--success-green); font-weight: bold;">{storage_info}</span>
                </div>
            </div>
            
            <div class="next-steps">
                <h3 class="classified-header" style="color: var(--blood-red);">🎯 TRIỂN KHAI TỨC THỜI</h3>
                <p style="margin-bottom: 15px; color: #cccccc;">Giấy phép của bạn đã có hiệu lực. Triển khai bằng lệnh:</p>
                <div class="command-highlight">!add_me</div>
                <p style="margin-top: 15px; color: #cccccc; font-size: 0.9em;">Thực thi lệnh này ở bất kỳ kênh nào có sự hiện diện của bot giám sát.</p>
            </div>
            
            <div class="security-assurance">
                <h3 class="classified-header" style="color: var(--mysterious-green);">🛡️ GIAO THỨC BẢO MẬT KÍCH HOẠT</h3>
                <ul class="security-list">
                    <li>Thông tin được mã hóa với cấp độ lượng tử</li>
                    <li>Không lưu giữ mật khẩu Discord trong hệ thống</li>
                    <li>Dấu chân truy cập tối thiểu cho hoạt động bí mật</li>
                    <li>Hệ thống lưu trữ đám mây an toàn, đa dự phòng</li>
                </ul>
            </div>
            
            <div class="signature-footer">
                <p>~ Operation Successfully Initiated ~</p>
                <small style="font-family: 'Griffy', cursive; font-size: 0.8em; color: var(--mysterious-green);">
                    "Your digital identity is now part of our covert network"
                </small>
            </div>
        </div>
    </body>
    </html>
    '''

@app.route('/health')
def health():
    """Health check endpoint với thông tin chi tiết"""
    db_connection = get_db_connection()
    db_status = db_connection is not None
    if db_connection:
        db_connection.close()
    
    # Test JSONBin connection
    jsonbin_status = False
    if JSONBIN_API_KEY and JSONBIN_BIN_ID:
        try:
            test_data = jsonbin_storage.read_data()
            jsonbin_status = True
        except:
            jsonbin_status = False
    
    return {
        "status": "ok", 
        "bot_connected": bot.is_ready(),
        "storage": {
            "database_connected": db_status,
            "jsonbin_configured": JSONBIN_API_KEY is not None,
            "jsonbin_working": jsonbin_status,
            "has_psycopg2": HAS_PSYCOPG2
        },
        "servers": len(bot.guilds) if bot.is_ready() else 0,
        "users": len(bot.users) if bot.is_ready() else 0
    }

# --- ADMIN DASHBOARD ROUTES (THÊM MỚI) ---
@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        if request.form.get('password') == ADMIN_PASSWORD:
            session['logged_in'] = True
            return redirect(url_for('admin_dashboard'))
        return "<h3>Sai mật khẩu! <a href='/admin/login'>Thử lại</a></h3>"
    return '''
    <body style="background:#111;color:#d4af37;font-family:serif;display:flex;justify-content:center;align-items:center;height:100vh;">
        <form method="post" style="border:1px solid #d4af37;padding:40px;text-align:center;">
            <h2>🕵️ ADMIN ACCESS</h2>
            <input type="password" name="password" placeholder="Passcode" style="padding:10px;">
            <button type="submit" style="padding:10px;background:#800;color:white;border:none;cursor:pointer;">UNLOCK</button>
        </form>
    </body>
    '''

@app.route('/admin/logout')
def admin_logout():
    session.pop('logged_in', None)
    return redirect(url_for('index'))

@app.route('/admin/dashboard')
@login_required
def admin_dashboard():
    # Đọc dữ liệu từ JSONBin
    full_data = jsonbin_storage.read_data()
    agent_data = {uid: data for uid, data in full_data.items() if uid.isdigit()}
    
    # Xử lý thứ tự sắp xếp (Roster Order)
    order = full_data.get('_roster_order', [])
    agents = []
    seen = set()
    
    # Thêm agents theo thứ tự đã lưu
    for uid in order:
        if uid in agent_data:
            d = agent_data[uid]
            agents.append({
                'id': uid, 
                'username': d.get('username', 'N/A'), 
                'avatar': f"https://cdn.discordapp.com/avatars/{uid}/{d.get('avatar_hash')}.png" if d.get('avatar_hash') else ""
            })
            seen.add(uid)
            
    # Thêm các agents còn lại (chưa được sắp xếp)
    for uid, d in agent_data.items():
        if uid not in seen:
            agents.append({
                'id': uid, 
                'username': d.get('username', 'N/A'), 
                'avatar': f"https://cdn.discordapp.com/avatars/{uid}/{d.get('avatar_hash')}.png" if d.get('avatar_hash') else ""
            })
            
    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Roster Command Center</title>
        <script src="https://cdnjs.cloudflare.com/ajax/libs/Sortable/1.14.0/Sortable.min.js"></script>
        <style>
            body { background: #1a1a1a; color: #e0e0e0; font-family: sans-serif; padding: 20px; max-width: 800px; margin: 0 auto; }
            .header { display: flex; justify-content: space-between; align-items: center; border-bottom: 2px solid #DAA520; padding-bottom: 10px; margin-bottom: 20px; }
            .btn { padding: 8px 16px; border: none; cursor: pointer; color: white; text-decoration: none; font-weight: bold; }
            .btn-save { background: #006400; }
            .btn-del { background: #8B0000; font-size: 0.8em; }
            .btn-logout { background: #555; }
            .agent-card { 
                background: #2c2c2c; border: 1px solid #444; margin-bottom: 8px; padding: 10px; 
                display: flex; align-items: center; justify-content: space-between; cursor: move;
            }
            .agent-info { display: flex; align-items: center; gap: 15px; }
            .avatar { width: 35px; height: 35px; border-radius: 50%; border: 1px solid #DAA520; }
        </style>
    </head>
    <body>
        <div class="header">
            <h2 style="color:#DAA520;margin:0;">🕵️ ROSTER COMMAND</h2>
            <div>
                <button onclick="saveOrder()" class="btn btn-save">💾 LƯU THỨ TỰ</button>
                <a href="/admin/logout" class="btn btn-logout">Thoát</a>
            </div>
        </div>
        <div id="roster-list">
            {% for agent in agents %}
            <div class="agent-card" data-id="{{ agent.id }}">
                <div class="agent-info">
                    <span style="color:#888;">☰</span>
                    <img src="{{ agent.avatar }}" class="avatar" onerror="this.src='https://cdn.discordapp.com/embed/avatars/0.png'">
                    <div>
                        <div style="font-weight:bold;color:#DAA520;">{{ agent.username }}</div>
                        <div style="font-size:0.8em;color:#888;">{{ agent.id }}</div>
                    </div>
                </div>
                <button onclick="deleteAgent('{{ agent.id }}')" class="btn btn-del">🗑️ XÓA</button>
            </div>
            {% endfor %}
        </div>
        <script>
            new Sortable(document.getElementById('roster-list'), { animation: 150 });

            async function saveOrder() {
                const order = Array.from(document.querySelectorAll('.agent-card')).map(e => e.getAttribute('data-id'));
                const res = await fetch('/admin/api/reorder', {
                    method: 'POST', headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({order: order})
                });
                alert((await res.json()).success ? '✅ Đã lưu!' : '❌ Lỗi!');
            }

            async function deleteAgent(id) {
                if(!confirm('Xóa sạch dữ liệu của agent này?')) return;
                const res = await fetch('/admin/api/delete/' + id, { method: 'POST' });
                if((await res.json()).success) {
                    document.querySelector(`.agent-card[data-id="${id}"]`).remove();
                } else {
                    alert('❌ Lỗi xóa!');
                }
            }
        </script>
    </body>
    </html>
    """, agents=agents)

@app.route('/admin/api/reorder', methods=['POST'])
@login_required
def api_reorder():
    full_data = jsonbin_storage.read_data()
    full_data['_roster_order'] = request.json.get('order', [])
    jsonbin_storage.write_data(full_data)
    return jsonify({"success": True})

@app.route('/admin/api/delete/<uid>', methods=['POST'])
@login_required
def api_delete_user(uid):
    # Gọi các hàm xóa từ các nguồn dữ liệu có sẵn trong file của bạn
    try: delete_user_from_db(uid) 
    except: pass
    try: jsonbin_storage.delete_user(uid)
    except: pass
    try: delete_user_from_json(uid)
    except: pass
    return jsonify({"success": True})
    
# --- THREADING FUNCTION ---
def run_flask():
    """Chạy Flask server"""
    app.run(host='0.0.0.0', port=PORT, debug=False)

# --- MAIN EXECUTION ---
if __name__ == '__main__':
    print("🚀 Đang khởi động Discord Bot + Web Server...")
    print(f"🔧 PORT: {PORT}")
    print(f"🔧 Render URL: {RENDER_URL}")
    
    # Initialize database
    database_initialized = init_database()
    
    # Test JSONBin connection
    if JSONBIN_API_KEY:
        print("🌐 Testing JSONBin.io connection...")
        try:
            test_data = jsonbin_storage.read_data()
            print(f"✅ JSONBin.io connected successfully")
            if isinstance(test_data, dict) and len(test_data) > 0:
                print(f"📊 Found {len(test_data)} existing tokens in JSONBin")
        except Exception as e:
            print(f"⚠️ JSONBin.io connection issue: {e}")
    else:
        print("⚠️ JSONBin.io not configured")

    @bot.event
    async def setup_hook():
        """Hàm này được gọi tự động trước khi bot đăng nhập."""
        print("🔧 Đang load các module mở rộng (cogs)...")
        try:
            await bot.load_extension('channel_tracker') # Tên file mới không có .py
            print("✅ Đã load thành công module 'channel_tracker'.")
        except Exception as e:
            print(f"❌ Lỗi khi load module 'channel_tracker': {e}")

    try:
        # Start Flask server in separate thread
        flask_thread = threading.Thread(target=run_flask, daemon=True)
        flask_thread.start()
        print(f"🌐 Web server started on port {PORT}")
        
        # Wait for Flask to start
        time.sleep(2)
        
        # Start Discord bot in main thread
        print("🤖 Starting Discord bot...")
        bot.run(DISCORD_TOKEN)
        
    except Exception as e:
        print(f"❌ Startup error: {e}")
        print("🔄 Keeping web server alive...")
        while True:
            time.sleep(60)


