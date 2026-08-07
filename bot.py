import os
import json
import asyncio
import time
from datetime import datetime
import discord
from discord.ext import commands, tasks
from discord import app_commands
import yt_dlp

# ==========================================
# 1. 디스코드 봇 토큰 직접 설정
# ==========================================
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix='!', intents=intents)

# ==========================================
# 📁 데이터 저장/불러오기 (JSON)
# ==========================================
SETTINGS_FILE = 'server_settings.json'
_settings_cache = {}

def load_settings() -> dict:
    global _settings_cache
    if not _settings_cache:
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                try:
                    _settings_cache = json.load(f)
                except json.JSONDecodeError:
                    _settings_cache = {}
        else:
            _settings_cache = {}
    return _settings_cache

def save_settings(data: dict = None):
    global _settings_cache
    if data is not None:
        _settings_cache = data
    with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
        json.dump(_settings_cache, f, ensure_ascii=False, indent=4)

def get_guild_data(guild_id: int) -> dict:
    settings = load_settings()
    gid = str(guild_id)
    
    default_structure = {
        "roles": {str(i): None for i in range(1, 6)},
        "channels": {
            "warn": None,
            "warn_remove": None,
            "appeal": None,
            "blacklist": None,
            "music": None
        },
        "music_msg_id": None
    }
    
    if gid not in settings:
        settings[gid] = default_structure
        save_settings()
    else:
        settings[gid].setdefault("roles", default_structure["roles"])
        settings[gid].setdefault("channels", default_structure["channels"])
        settings[gid].setdefault("music_msg_id", None)
        for i in range(1, 6):
            settings[gid]["roles"].setdefault(str(i), None)
            
    return settings[gid]

def update_guild_data(guild_id: int, new_data: dict):
    settings = load_settings()
    settings[str(guild_id)] = new_data
    save_settings()

# ==========================================
# 🎶 음악 시스템 (yt-dlp + Queue + 대시보드)
# ==========================================
ytdl_format_options = {
    'format': 'bestaudio/best',
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'ytsearch',
    'source_address': '0.0.0.0',
}

ffmpeg_options = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn',
}

ytdl = yt_dlp.YoutubeDL(ytdl_format_options)

class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get('title', '제목 없음')
        self.url = data.get('webpage_url') or data.get('url', '')
        self.thumbnail = data.get('thumbnail')

    @classmethod
    async def from_data(cls, data, *, loop=None, stream=True):
        filename = data['url'] if stream else ytdl.prepare_filename(data)
        return cls(discord.FFmpegPCMAudio(filename, **ffmpeg_options), data=data)

class GuildMusicState:
    def __init__(self):
        self.queue = []
        self.current = None
        self.is_looping = False
        self.start_time = None
        self.pause_start = None
        self.total_paused_duration = 0

    def start_timer(self):
        self.start_time = time.time()
        self.pause_start = None
        self.total_paused_duration = 0

    def reset_timer(self):
        self.start_time = None
        self.pause_start = None
        self.total_paused_duration = 0

music_states = {}

def get_music_state(guild_id: int) -> GuildMusicState:
    if guild_id not in music_states:
        music_states[guild_id] = GuildMusicState()
    return music_states[guild_id]

DEFAULT_BANNER = "https://i.imgur.com/8Qx2Z5G.png"

def get_elapsed_sec(state: GuildMusicState, vc) -> int:
    if not state.start_time:
        return 0
    current_time = state.pause_start if (vc and vc.is_paused() and state.pause_start) else time.time()
    elapsed = current_time - state.start_time - state.total_paused_duration
    return max(0, int(elapsed))

def make_progress_bar(elapsed_sec: int, duration_sec: int, length: int = 12) -> str:
    if duration_sec <= 0:
        return "━" * length
    progress = min(1.0, max(0.0, elapsed_sec / duration_sec))
    filled_length = int(length * progress)
    bar = "━" * filled_length + "🔘" + "━" * max(0, length - filled_length - 1)
    return bar

def create_music_embed(guild_id: int, guild: discord.Guild) -> discord.Embed:
    state = get_music_state(guild_id)
    vc = guild.voice_client
    
    if state.current:
        embed = discord.Embed(
            title=state.current['title'],
            url=state.current.get('url', ''),
            color=discord.Color.blue()
        )
        embed.set_author(name="💽 현재 재생 중")

        duration_sec = state.current.get('duration', 0)
        is_paused = vc.is_paused() if vc else False
        is_looping = state.is_looping

        elapsed = get_elapsed_sec(state, vc)
        em, es = divmod(elapsed, 60)
        dm, ds = divmod(duration_sec, 60)

        bar = make_progress_bar(elapsed, duration_sec, length=12)

        if is_paused:
            time_display = f"⏸️ `{em:02d}:{es:02d}` {bar} `{dm:02d}:{ds:02d}` (일시정지)"
        elif duration_sec > 0:
            time_display = f"▶️ `{em:02d}:{es:02d}` {bar} `{dm:02d}:{ds:02d}`"
        else:
            start_ts = int(state.start_time) if state.start_time else int(time.time())
            time_display = f"🔴 **라이브 스트림** (<t:{start_ts}:R>)"

        embed.add_field(name="⏱️ 재생 정보", value=time_display, inline=False)
        embed.add_field(name="요청자", value=state.current['requester'].mention, inline=True)
        embed.add_field(name="대기열", value=f"{len(state.queue)}개", inline=True)

        embed.add_field(name="반복", value="🟩 켜짐" if is_looping else "🟥 꺼짐", inline=True)
        embed.add_field(name="일시정지", value="🟩 정지됨" if is_paused else "🟥 재생중", inline=True)

        if state.current.get('thumbnail'):
            embed.set_image(url=state.current['thumbnail'])
    else:
        embed = discord.Embed(
            title="현재 재생중인 곡이 없어요.",
            description="**제목 또는 링크를 입력하여 음악을 재생해보세요!**",
            color=discord.Color.dark_theme()
        )
        embed.set_image(url=DEFAULT_BANNER)
        icon_url = guild.icon.url if guild.icon else None
        embed.set_footer(text=f"{guild.name} • 음악 컨트롤러", icon_url=icon_url)
    
    return embed

async def update_music_dashboard(guild: discord.Guild):
    gdata = get_guild_data(guild.id)
    ch_id = gdata["channels"].get("music")
    msg_id = gdata.get("music_msg_id")

    if not ch_id or not msg_id:
        return

    channel = guild.get_channel(ch_id)
    if not channel:
        return

    try:
        msg = await channel.fetch_message(msg_id)
        embed = create_music_embed(guild.id, guild)
        await msg.edit(embed=embed, view=MusicControlView(guild.id))
    except discord.NotFound:
        pass
    except Exception as e:
        print(f"[대시보드 업데이트 에러]: {e}")

@tasks.loop(seconds=3)
async def music_dashboard_loop():
    for guild in bot.guilds:
        state = get_music_state(guild.id)
        if state.current and guild.voice_client and guild.voice_client.is_playing():
            try:
                await update_music_dashboard(guild)
            except Exception as e:
                print(f"[대시보드 루프 에러]: {e}")

class MusicControlView(discord.ui.View):
    def __init__(self, guild_id: int):
        super().__init__(timeout=None)
        self.guild_id = guild_id

    @discord.ui.button(label="스킵", style=discord.ButtonStyle.secondary, emoji="⏭️", custom_id="m_skip")
    async def skip_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = interaction.guild.voice_client
        if vc and (vc.is_playing() or vc.is_paused()):
            vc.stop()
            await interaction.response.send_message("⏭️ 곡을 스킵했습니다.", ephemeral=True)
        else:
            await interaction.response.send_message("⚠️ 재생 중인 곡이 없습니다.", ephemeral=True)

    @discord.ui.button(label="정지", style=discord.ButtonStyle.secondary, emoji="⏹️", custom_id="m_stop")
    async def stop_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        state = get_music_state(self.guild_id)
        state.queue.clear()
        state.current = None
        state.reset_timer()
        
        vc = interaction.guild.voice_client
        if vc:
            await vc.disconnect()
            
        await interaction.response.send_message("⏹️ 음악 재생을 정지하고 채널에서 퇴장했습니다.", ephemeral=True)
        await update_music_dashboard(interaction.guild)

    @discord.ui.button(label="일시정지/재생", style=discord.ButtonStyle.secondary, emoji="⏯️", custom_id="m_pause")
    async def pause_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = interaction.guild.voice_client
        state = get_music_state(self.guild_id)
        
        if not vc:
            await interaction.response.send_message("⚠️ 음성 채널에 접속해있지 않습니다.", ephemeral=True)
            return

        if vc.is_playing():
            vc.pause()
            state.pause_start = time.time()
            await interaction.response.send_message("⏸️ 일시정지되었습니다.", ephemeral=True)
        elif vc.is_paused():
            vc.resume()
            if state.pause_start:
                state.total_paused_duration += (time.time() - state.pause_start)
                state.pause_start = None
            await interaction.response.send_message("▶️ 재생을 다시 시작합니다.", ephemeral=True)
        else:
            await interaction.response.send_message("⚠️ 재생 중인 음악이 없습니다.", ephemeral=True)
            return
            
        await update_music_dashboard(interaction.guild)

    @discord.ui.button(label="반복", style=discord.ButtonStyle.secondary, emoji="🔁", custom_id="m_loop")
    async def loop_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        state = get_music_state(self.guild_id)
        state.is_looping = not state.is_looping
        status = "ON 🔁" if state.is_looping else "OFF ➡️"
        await interaction.response.send_message(f"반복 재생이 {status} 설정되었습니다.", ephemeral=True)
        await update_music_dashboard(interaction.guild)

    @discord.ui.button(label="옵션", style=discord.ButtonStyle.secondary, emoji="📜", custom_id="m_option")
    async def option_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        state = get_music_state(self.guild_id)
        if not state.queue:
            await interaction.response.send_message("📜 대기열이 비어 있습니다.", ephemeral=True)
            return

        queue_list = "\n".join([f"**{i+1}.** {song['title']}" for i, song in enumerate(state.queue[:10])])
        embed = discord.Embed(title="🎶 대기열 목록 (상위 10곡)", description=queue_list, color=discord.Color.gold())
        await interaction.response.send_message(embed=embed, ephemeral=True)

def play_next(guild: discord.Guild):
    state = get_music_state(guild.id)
    vc = guild.voice_client

    if not vc or not vc.is_connected():
        state.reset_timer()
        state.current = None
        return

    if state.is_looping and state.current:
        state.queue.insert(0, state.current)

    if state.queue:
        state.current = state.queue.pop(0)
        state.start_timer()
        
        async def _play():
            try:
                player = await YTDLSource.from_data(state.current['raw_data'], loop=bot.loop, stream=True)
                vc.play(player, after=lambda e: play_next(guild))
                await update_music_dashboard(guild)
            except Exception as e:
                print(f"[재생 오류]: {e}")
                play_next(guild)

        asyncio.run_coroutine_threadsafe(_play(), bot.loop)
    else:
        state.current = None
        state.reset_timer()
        asyncio.run_coroutine_threadsafe(update_music_dashboard(guild), bot.loop)

# ==========================================
# 📩 이의제기 시스템
# ==========================================
class AppealModal(discord.ui.Modal, title='경고 이의제기 접수'):
    def __init__(self, guild_id: int):
        super().__init__()
        self.guild_id = guild_id

    reason = discord.ui.TextInput(label='소명 및 이의제기 사유', style=discord.TextStyle.paragraph, required=True, max_length=500)

    async def on_submit(self, interaction: discord.Interaction):
        gdata = get_guild_data(self.guild_id)
        appeal_ch_id = gdata["channels"].get("appeal")
        guild = bot.get_guild(self.guild_id)

        if not guild or not appeal_ch_id or not (channel := guild.get_channel(appeal_ch_id)):
            await interaction.response.send_message("⚠️ 이의제기 채널 설정을 확인할 수 없습니다.", ephemeral=True)
            return

        embed = discord.Embed(title="🟡 관리자 경고 이의제기 접수", color=discord.Color.gold(), timestamp=discord.utils.utcnow())
        embed.add_field(name="👤 신청자", value=f"{interaction.user.mention} ({interaction.user.name})", inline=True)
        embed.add_field(name="🆔 고유 아이디", value=f"{interaction.user.id}", inline=True)
        embed.add_field(name="📝 소명 내용", value=self.reason.value, inline=False)
        
        if interaction.user.display_avatar:
            embed.set_thumbnail(url=interaction.user.display_avatar.url)

        await channel.send(embed=embed)
        await interaction.response.send_message("✅ 이의제기가 성공적으로 접수되었습니다.", ephemeral=True)

class AppealButtonView(discord.ui.View):
    def __init__(self, guild_id: int = 0):
        super().__init__(timeout=None)
        self.guild_id = guild_id

    @discord.ui.button(label="이의제기 하기", style=discord.ButtonStyle.primary, emoji="📩", custom_id="persistent_appeal_button")
    async def appeal_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        target_guild_id = self.guild_id if self.guild_id != 0 else (interaction.guild_id or 0)
        await interaction.response.send_modal(AppealModal(target_guild_id))

# ==========================================
# ⚙️ 설정 대시보드 시스템 (메인 및 단계 선택 뷰)
# ==========================================

async def return_to_main_dashboard(interaction: discord.Interaction):
    gdata = get_guild_data(interaction.guild_id)
    
    roles_text = "\n".join([f"• 경고 {i}단계: " + (f"<@&{gdata['roles'][str(i)]}>" if gdata['roles'].get(str(i)) else "미설정") for i in range(1, 6)])
    channels_text = (
        f"• 경고 채널: " + (f"<#{gdata['channels']['warn']}>" if gdata['channels'].get('warn') else "설정 안됨 (로그 비활성)") + "\n" +
        f"• 경고차감 채널: " + (f"<#{gdata['channels']['warn_remove']}>" if gdata['channels'].get('warn_remove') else "설정 안됨 (로그 비활성)") + "\n" +
        f"• 이의제기 채널: " + (f"<#{gdata['channels']['appeal']}>" if gdata['channels'].get('appeal') else "설정 안됨 (로그 비활성)") + "\n" +
        f"• 블랙리스트 채널: " + (f"<#{gdata['channels']['blacklist']}>" if gdata['channels'].get('blacklist') else "설정 안됨 (로그 비활성)")
    )

    embed = discord.Embed(
        title="⚙️ 경고 시스템 설정 대시보드",
        description="아래 버튼을 클릭하여 역할 및 각 로그 채널을 손쉽게 설정하세요.",
        color=discord.Color.blue()
    )
    embed.add_field(name="🎭 횟수별 역할 부여", value=roles_text, inline=False)
    embed.add_field(name="📋 로그 채널 설정 현황", value=channels_text, inline=False)

    try:
        await interaction.response.edit_message(content=None, embed=embed, view=SettingsDashboardView())
    except Exception:
        try:
            await interaction.message.edit(content=None, embed=embed, view=SettingsDashboardView())
        except Exception:
            pass

# 대시보드 임베드를 만들어 반환해주는 공통 함수
def get_dashboard_embed(guild_id: int) -> discord.Embed:
    gdata = get_guild_data(guild_id)
    roles_text = "\n".join([f"• 경고 {i}단계: " + (f"<@&{gdata['roles'][str(i)]}>" if gdata['roles'].get(str(i)) else "미설정") for i in range(1, 6)])
    channels_text = (
        f"• 경고 채널: " + (f"<#{gdata['channels']['warn']}>" if gdata['channels'].get('warn') else "설정 안됨 (로그 비활성)") + "\n" +
        f"• 경고차감 채널: " + (f"<#{gdata['channels']['warn_remove']}>" if gdata['channels'].get('warn_remove') else "설정 안됨 (로그 비활성)") + "\n" +
        f"• 이의제기 채널: " + (f"<#{gdata['channels']['appeal']}>" if gdata['channels'].get('appeal') else "설정 안됨 (로그 비활성)") + "\n" +
        f"• 블랙리스트 채널: " + (f"<#{gdata['channels']['blacklist']}>" if gdata['channels'].get('blacklist') else "설정 안됨 (로그 비활성)")
    )

    embed = discord.Embed(
        title="⚙️ 경고 시스템 설정 대시보드",
        description="아래 메뉴를 선택하여 역할 및 각 로그 채널을 손쉽게 설정하세요.",
        color=discord.Color.blue()
    )
    embed.add_field(name="🎭 횟수별 역할 부여", value=roles_text, inline=False)
    embed.add_field(name="📋 로그 채널 설정 현황", value=channels_text, inline=False)
    return embed

class SettingsDashboardView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="횟수별 역할 부여", style=discord.ButtonStyle.secondary, emoji="🎭", row=0)
    async def count_role_setting(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("⚠️ 관리자만 설정할 수 있습니다!", ephemeral=True)
            return
        
        view = RoleStepSelectView()
        embed = get_dashboard_embed(interaction.guild_id)
        embed.description = "✨ **[횟수별 역할 부여]** 설정할 경고 단계를 선택해주세요."
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="로그 채널 설정", style=discord.ButtonStyle.secondary, emoji="📋", row=0)
    async def log_channel_setting(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("⚠️ 관리자만 설정할 수 있습니다!", ephemeral=True)
            return
        
        view = LogCategorySelectView()
        embed = get_dashboard_embed(interaction.guild_id)
        embed.description = "✨ **[로그 채널 설정]** 설정할 로그 채널 종류를 선택해주세요."
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="닫기", style=discord.ButtonStyle.danger, emoji="✖️", row=1)
    async def close_dashboard(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await interaction.message.delete()
        except Exception:
            await interaction.response.send_message("✅ 대시보드를 닫았습니다.", ephemeral=True)

class RoleStepSelectView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)

    @discord.ui.select(
        placeholder="설정할 경고 단계를 선택하세요...",
        options=[discord.SelectOption(label=f"경고 {i}단계 역할", value=str(i), emoji=f"{i}️⃣") for i in range(1, 6)]
    )
    async def select_callback(self, interaction: discord.Interaction, select: discord.ui.Select):
        step = select.values[0]
        view = RoleSelectView(step)
        embed = get_dashboard_embed(interaction.guild_id)
        embed.description = f"✨ 경고 **{step}단계**에 부여할 **역할**을 아래 메뉴에서 선택해주세요."
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="돌아가기", style=discord.ButtonStyle.grey, emoji="⬅️", row=1)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await return_to_main_dashboard(interaction)

class RoleSelectView(discord.ui.View):
    def __init__(self, step: str):
        super().__init__(timeout=180)
        self.step = step
        self.add_item(RoleSelectDropdown(step))

    @discord.ui.button(label="돌아가기", style=discord.ButtonStyle.grey, emoji="⬅️", row=1)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = RoleStepSelectView()
        embed = get_dashboard_embed(interaction.guild_id)
        embed.description = "✨ **[횟수별 역할 부여]** 설정할 경고 단계를 선택해주세요."
        await interaction.response.edit_message(embed=embed, view=view)

class RoleSelectDropdown(discord.ui.RoleSelect):
    def __init__(self, step: str):
        self.step = step
        super().__init__(placeholder="역할을 선택하세요...", min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        selected_role = self.values[0]
        gdata = get_guild_data(interaction.guild_id)
        gdata["roles"][self.step] = selected_role.id
        update_guild_data(interaction.guild_id, gdata)
        
        await interaction.response.send_message(f"✅ 경고 {self.step}단계 역할이 성공적으로 지정되었습니다: {selected_role.mention}", ephemeral=True)
        await return_to_main_dashboard(interaction)

class LogCategorySelectView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)

    @discord.ui.select(
        placeholder="설정할 로그 채널 종류를 선택하세요...",
        options=[
            discord.SelectOption(label="경고 로그 채널", value="warn", emoji="🔴"),
            discord.SelectOption(label="경고차감 로그 채널", value="warn_remove", emoji="🟢"),
            discord.SelectOption(label="이의제기 접수 채널", value="appeal", emoji="🟡"),
            discord.SelectOption(label="블랙리스트 로그 채널", value="blacklist", emoji="⬛"),
        ]
    )
    async def select_callback(self, interaction: discord.Interaction, select: discord.ui.Select):
        category = select.values[0]
        names = {"warn": "경고 로그", "warn_remove": "경고차감 로그", "appeal": "이의제기 접수", "blacklist": "블랙리스트 로그"}
        view = ChannelSelectView(category)
        embed = get_dashboard_embed(interaction.guild_id)
        embed.description = f"✨ `{names.get(category)}` 채널로 지정할 **텍스트 채널**을 아래 메뉴에서 선택해주세요."
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="돌아가기", style=discord.ButtonStyle.grey, emoji="⬅️", row=1)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await return_to_main_dashboard(interaction)

class ChannelSelectView(discord.ui.View):
    def __init__(self, category: str):
        super().__init__(timeout=180)
        self.category = category
        self.add_item(ChannelSelectDropdown(category))

    @discord.ui.button(label="돌아가기", style=discord.ButtonStyle.grey, emoji="⬅️", row=1)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = LogCategorySelectView()
        embed = get_dashboard_embed(interaction.guild_id)
        embed.description = "✨ **[로그 채널 설정]** 설정할 로그 채널 종류를 선택해주세요."
        await interaction.response.edit_message(embed=embed, view=view)

class ChannelSelectDropdown(discord.ui.ChannelSelect):
    def __init__(self, category: str):
        self.category = category
        super().__init__(placeholder="채널을 선택하세요...", min_values=1, max_values=1, channel_types=[discord.ChannelType.text])

    async def callback(self, interaction: discord.Interaction):
        selected_channel = self.values[0]
        gdata = get_guild_data(interaction.guild_id)
        gdata["channels"][self.category] = selected_channel.id
        update_guild_data(interaction.guild_id, gdata)
        
        await interaction.response.send_message(f"✅ 성공적으로 지정되었습니다: {selected_channel.mention}", ephemeral=True)
        await return_to_main_dashboard(interaction)

# ==========================================
# 🎨 실시간 임베드 빌더 시스템
# ==========================================
class EmbedBuilderState:
    def __init__(self):
        self.title = "제목을 입력해주세요"
        self.description = "임베드의 설명입니다."
        self.color = discord.Color.blue()
        self.url = None
        self.image_url = None

    def build_embed(self) -> discord.Embed:
        kwargs = {"title": self.title, "description": self.description, "color": self.color}
        if self.url: kwargs["url"] = self.url
        emb = discord.Embed(**kwargs)
        if self.image_url: emb.set_image(url=self.image_url)
        return emb

class EmbedTitleModal(discord.ui.Modal, title="제목 수정"):
    val = discord.ui.TextInput(label="임베드 제목", required=True, max_length=256)
    def __init__(self, builder_view):
        super().__init__()
        self.builder_view = builder_view
        self.val.default = builder_view.state.title

    async def on_submit(self, interaction: discord.Interaction):
        self.builder_view.state.title = self.val.value
        await self.builder_view.update_preview(interaction)

class EmbedDescModal(discord.ui.Modal, title="설명 수정"):
    val = discord.ui.TextInput(label="임베드 설명", style=discord.TextStyle.paragraph, required=True, max_length=4000)
    def __init__(self, builder_view):
        super().__init__()
        self.builder_view = builder_view
        self.val.default = builder_view.state.description

    async def on_submit(self, interaction: discord.Interaction):
        self.builder_view.state.description = self.val.value
        await self.builder_view.update_preview(interaction)

class EmbedColorModal(discord.ui.Modal, title="색상 수정"):
    val = discord.ui.TextInput(label="색상 코드 (#HEX 또는 blue/red/green/gold)", required=True, max_length=20)
    def __init__(self, builder_view):
        super().__init__()
        self.builder_view = builder_view

    async def on_submit(self, interaction: discord.Interaction):
        text = self.val.value.strip().lower()
        color_map = {"blue": discord.Color.blue(), "red": discord.Color.red(), "green": discord.Color.green(), "gold": discord.Color.gold()}
        if text in color_map:
            self.builder_view.state.color = color_map[text]
        else:
            try:
                if text.startswith("#"): text = text[1:]
                self.builder_view.state.color = discord.Color(int(text, 16))
            except ValueError:
                await interaction.response.send_message("⚠️ 올바르지 않은 색상 코드 형식입니다.", ephemeral=True)
                return
        await self.builder_view.update_preview(interaction)

class EmbedUrlModal(discord.ui.Modal, title="URL 수정"):
    val = discord.ui.TextInput(label="URL (빈칸 입력 시 제거)", required=False, max_length=500)
    def __init__(self, builder_view):
        super().__init__()
        self.builder_view = builder_view
        if builder_view.state.url: self.val.default = builder_view.state.url

    async def on_submit(self, interaction: discord.Interaction):
        self.builder_view.state.url = self.val.value.strip() if self.val.value.strip() else None
        await self.builder_view.update_preview(interaction)

class EmbedImageModal(discord.ui.Modal, title="이미지 수정"):
    val = discord.ui.TextInput(label="이미지 링크 (빈칸 입력 시 제거)", required=False, max_length=500)
    def __init__(self, builder_view):
        super().__init__()
        self.builder_view = builder_view
        if builder_view.state.image_url: self.val.default = builder_view.state.image_url

    async def on_submit(self, interaction: discord.Interaction):
        self.builder_view.state.image_url = self.val.value.strip() if self.val.value.strip() else None
        await self.builder_view.update_preview(interaction)

class EmbedBuilderView(discord.ui.View):
    def __init__(self, author_id: int, channel: discord.TextChannel):
        super().__init__(timeout=600)
        self.author_id = author_id
        self.state = EmbedBuilderState()
        self.channel = channel
        self.message = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.author_id

    async def send_initial(self):
        self.message = await self.channel.send(embed=self.state.build_embed(), view=self)

    async def update_preview(self, interaction: discord.Interaction):
        embed = self.state.build_embed()
        if self.message:
            try:
                await self.message.edit(embed=embed, view=self)
            except Exception:
                self.message = await self.channel.send(embed=embed, view=self)
        else:
            self.message = await self.channel.send(embed=embed, view=self)

        if not interaction.response.is_done():
            await interaction.response.defer()

    @discord.ui.select(
        placeholder="수정할 항목을 선택해주세요...",
        options=[
            discord.SelectOption(label="제목", value="title", emoji="✏️"),
            discord.SelectOption(label="설명", value="desc", emoji="📝"),
            discord.SelectOption(label="색상", value="color", emoji="🎨"),
            discord.SelectOption(label="URL", value="url", emoji="🔗"),
            discord.SelectOption(label="이미지", value="image", emoji="🖼️"),
        ], row=0
    )
    async def select_callback(self, interaction: discord.Interaction, select: discord.ui.Select):
        c = select.values[0]
        if c == "title": await interaction.response.send_modal(EmbedTitleModal(self))
        elif c == "desc": await interaction.response.send_modal(EmbedDescModal(self))
        elif c == "color": await interaction.response.send_modal(EmbedColorModal(self))
        elif c == "url": await interaction.response.send_modal(EmbedUrlModal(self))
        elif c == "image": await interaction.response.send_modal(EmbedImageModal(self))

    @discord.ui.button(label="완료 (하단 고정)", style=discord.ButtonStyle.success, row=1)
    async def done_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        final_embed = self.state.build_embed()
        if self.message:
            try: await self.message.delete()
            except Exception: pass
        await interaction.response.send_message("✅ 생성 완료!", ephemeral=True)
        sent_message = await self.channel.send(embed=final_embed)
        sticky_embeds[interaction.channel.id] = {"embed": final_embed, "message": sent_message}
        active_builders.pop(interaction.channel.id, None)
        self.stop()

    @discord.ui.button(label="취소", style=discord.ButtonStyle.danger, row=1)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.message:
            try: await self.message.delete()
            except Exception: pass
        await interaction.response.send_message("❌ 취소되었습니다.", ephemeral=True)
        active_builders.pop(interaction.channel.id, None)
        self.stop()

active_builders = {}
sticky_embeds = {}
sticky_tasks = {}

async def handle_sticky_message(channel_id: int, channel: discord.TextChannel):
    await asyncio.sleep(2.0)
    sticky_data = sticky_embeds.get(channel_id)
    if sticky_data and sticky_data.get("message"):
        try: await sticky_data["message"].delete()
        except Exception: pass
        try:
            new_msg = await channel.send(embed=sticky_data["embed"])
            sticky_embeds[channel_id]["message"] = new_msg
        except Exception: pass
    sticky_tasks.pop(channel_id, None)

# ==========================================
# 💬 메시지 이벤트 처리
# ==========================================
@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return

    gdata = get_guild_data(message.guild.id)
    music_ch_id = gdata["channels"].get("music")

    if music_ch_id and message.channel.id == music_ch_id:
        query = message.content.strip()
        try:
            await message.delete()
        except Exception:
            pass

        if not message.author.voice or not message.author.voice.channel:
            info_msg = await message.channel.send(f"⚠️ {message.author.mention} 먼저 음성 채널에 입장해 주세요!")
            await asyncio.sleep(3)
            await info_msg.delete()
            return

        vc = message.guild.voice_client
        if not vc:
            vc = await message.author.voice.channel.connect()
        elif vc.channel != message.author.voice.channel:
            await vc.move_to(message.author.voice.channel)

        try:
            info = await bot.loop.run_in_executor(None, lambda: ytdl.extract_info(query, download=False))
            if 'entries' in info and info['entries']:
                info = info['entries'][0]

            song_data = {
                'title': info.get('title', '제목 없음'),
                'url': info.get('webpage_url', query),
                'query': query,
                'raw_data': info,
                'thumbnail': info.get('thumbnail'),
                'duration': info.get('duration', 0),
                'requester': message.author
            }

            state = get_music_state(message.guild.id)

            if vc.is_playing() or vc.is_paused():
                state.queue.append(song_data)
                await update_music_dashboard(message.guild)
            else:
                state.current = song_data
                state.start_timer()
                player = await YTDLSource.from_data(info, loop=bot.loop, stream=True)
                vc.play(player, after=lambda e: play_next(message.guild))
                await update_music_dashboard(message.guild)

        except Exception as e:
            print(f"[음악 검색/재생 오류]: {e}")

        return

    if message.channel.id in sticky_embeds:
        ch_id = message.channel.id
        if ch_id in sticky_tasks:
            sticky_tasks[ch_id].cancel()
        sticky_tasks[ch_id] = asyncio.create_task(handle_sticky_message(ch_id, message.channel))

    await bot.process_commands(message)

# ==========================================
# ⚙️ 모달 및 슬래시 커맨드 정의
# ==========================================
class BlacklistModal(discord.ui.Modal, title='블랙리스트 등록'):
    reason = discord.ui.TextInput(label='사유', style=discord.TextStyle.paragraph, required=True, max_length=300)
    def __init__(self, target: discord.Member):
        super().__init__()
        self.target = target

    async def on_submit(self, interaction: discord.Interaction):
        gdata = get_guild_data(interaction.guild_id)
        bl_ch_id = gdata["channels"].get("blacklist")
        if not bl_ch_id or not (bl_channel := interaction.guild.get_channel(bl_ch_id)):
            await interaction.response.send_message("⚠️ 블랙리스트 채널이 설정되지 않았습니다.", ephemeral=True)
            return

        try:
            await interaction.guild.ban(self.target, reason=self.reason.value)
        except Exception:
            await interaction.response.send_message("⚠️ 권한이 부족하여 대상을 차단하지 못했습니다.", ephemeral=True)
            return

        today = datetime.now().strftime("%Y-%m-%d")
        desc = (
            f"# {today} 블랙리스트 안내\n\n"
            f"사용자명 : {self.target.mention}\n"
            f"사용자명 : @{self.target.name}\n"
            f"고유 아이디 : {self.target.id}\n"
            f"사유 : {self.reason.value}\n\n"
            f"블랙리스트 관련 이의제기 및 질문은 {interaction.user.mention}에게 문의 바랍니다."
        )

        embed = discord.Embed(description=desc, color=discord.Color.dark_theme())
        if interaction.channel_id == bl_channel.id:
            await interaction.response.send_message(embed=embed)
        else:
            await bl_channel.send(embed=embed)
            await interaction.response.send_message("✅ 블랙리스트 등록 완료", ephemeral=True)

class WarnModal(discord.ui.Modal, title='관리자 경고 부여'):
    reason = discord.ui.TextInput(label='사유', style=discord.TextStyle.paragraph, required=True, max_length=300)
    def __init__(self, target: discord.Member, count: int):
        super().__init__()
        self.target = target
        self.count = count

    async def on_submit(self, interaction: discord.Interaction):
        gdata = get_guild_data(interaction.guild_id)
        warn_ch_id = gdata["channels"].get("warn")
        if not warn_ch_id or not (warn_channel := interaction.guild.get_channel(warn_ch_id)):
            await interaction.response.send_message("⚠️ 경고 채널이 설정되지 않았습니다.", ephemeral=True)
            return

        role_id = gdata["roles"].get(str(self.count))
        if role_id and (role := interaction.guild.get_role(role_id)):
            try: await self.target.add_roles(role)
            except Exception: pass

        dm_embed = discord.Embed(title="⚠️ 경고장", description=f"**{interaction.guild.name}** 서버에서 경고 {self.count}회가 부여되었습니다.", color=discord.Color.red())
        dm_embed.add_field(name="사유", value=self.reason.value)
        try: await self.target.send(embed=dm_embed, view=AppealButtonView(interaction.guild_id))
        except Exception: pass

        embed = discord.Embed(title="🔴 경고 부여 완료", color=discord.Color.red())
        embed.add_field(name="대상", value=self.target.mention, inline=True)
        embed.add_field(name="횟수", value=f"{self.count}회", inline=True)
        embed.add_field(name="사유", value=self.reason.value, inline=False)
        
        if interaction.channel_id == warn_channel.id: await interaction.response.send_message(embed=embed)
        else:
            await warn_channel.send(embed=embed)
            await interaction.response.send_message("✅ 경고 전송 완료", ephemeral=True)

class WarnRemoveModal(discord.ui.Modal, title='관리자 경고 차감'):
    reason = discord.ui.TextInput(label='사유', style=discord.TextStyle.paragraph, required=True, max_length=300)
    def __init__(self, target: discord.Member, count: int):
        super().__init__()
        self.target = target
        self.count = count

    async def on_submit(self, interaction: discord.Interaction):
        gdata = get_guild_data(interaction.guild_id)
        remove_ch_id = gdata["channels"].get("warn_remove")
        if not remove_ch_id or not (remove_channel := interaction.guild.get_channel(remove_ch_id)):
            await interaction.response.send_message("⚠️ 경고 차감 채널이 설정되지 않았습니다.", ephemeral=True)
            return

        role_id = gdata["roles"].get(str(self.count))
        if role_id and (role := interaction.guild.get_role(role_id)):
            try: await self.target.remove_roles(role)
            except Exception: pass

        embed = discord.Embed(title="🟢 경고 차감 완료", color=discord.Color.green())
        embed.add_field(name="대상", value=self.target.mention, inline=True)
        embed.add_field(name="차감 횟수", value=f"{self.count}회", inline=True)
        embed.add_field(name="사유", value=self.reason.value, inline=False)

        if interaction.channel_id == remove_channel.id: await interaction.response.send_message(embed=embed)
        else:
            await remove_channel.send(embed=embed)
            await interaction.response.send_message("✅ 차감 완료", ephemeral=True)

@bot.event
async def on_ready():
    print(f'🤖 로그인 완료: {bot.user}')
    bot.add_view(AppealButtonView(guild_id=0))
    
    settings = load_settings()
    for gid in settings:
        if gid.isdigit():
            bot.add_view(MusicControlView(int(gid)))

    if not music_dashboard_loop.is_running():
        music_dashboard_loop.start()

    try:
        synced = await bot.tree.sync()
        print(f"✅ {len(synced)}개의 슬래시 명령어 동기화 완료!")
    except Exception as e:
        print(f"❌ 동기화 에러: {e}")

@bot.tree.command(name="역할채널지정", description="경고 및 로그 채널과 단계별 역할을 설정하는 대시보드를 엽니다.")
async def set_role_channel_dashboard(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("⚠️ 관리자 권한이 필요합니다.", ephemeral=True)
        return

    embed = get_dashboard_embed(interaction.guild_id)
    await interaction.response.send_message(embed=embed, view=SettingsDashboardView())

@bot.tree.command(name="음악채널지정", description="현재 채널을 음악 대시보드 채널로 지정합니다.")
async def set_music_channel(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("⚠️ 관리자 권한이 필요합니다.", ephemeral=True)
        return

    gdata = get_guild_data(interaction.guild_id)
    gdata["channels"]["music"] = interaction.channel_id

    old_msg_id = gdata.get("music_msg_id")
    if old_msg_id:
        try:
            old_msg = await interaction.channel.fetch_message(old_msg_id)
            await old_msg.delete()
        except Exception:
            pass

    embed = create_music_embed(interaction.guild_id, interaction.guild)
    view = MusicControlView(interaction.guild_id)

    await interaction.response.send_message("✅ 음악 채널 설정 완료!", ephemeral=True)
    msg = await interaction.channel.send(embed=embed, view=view)

    gdata["music_msg_id"] = msg.id
    update_guild_data(interaction.guild_id, gdata)

@bot.tree.command(name="경고등록", description="특정 유저에게 경고를 부여합니다.")
@app_commands.describe(member="대상 유저", count="경고 단계 (1~5)")
@app_commands.choices(count=[app_commands.Choice(name=f"{i}회", value=i) for i in range(1, 6)])
async def warn_add_sub(interaction: discord.Interaction, member: discord.Member, count: app_commands.Choice[int]):
    if not interaction.user.guild_permissions.kick_members:
        await interaction.response.send_message("⚠️ 권한이 없습니다.", ephemeral=True)
        return
    await interaction.response.send_modal(WarnModal(target=member, count=count.value))

@bot.tree.command(name="경고빼기", description="특정 유저의 경고를 차감합니다.")
@app_commands.describe(member="대상 유저", count="차감 단계 (1~5)")
@app_commands.choices(count=[app_commands.Choice(name=f"{i}회 차감", value=i) for i in range(1, 6)])
async def warn_remove_sub(interaction: discord.Interaction, member: discord.Member, count: app_commands.Choice[int]):
    if not interaction.user.guild_permissions.kick_members:
        await interaction.response.send_message("⚠️ 권한이 없습니다.", ephemeral=True)
        return
    await interaction.response.send_modal(WarnRemoveModal(target=member, count=count.value))

@bot.tree.command(name="블랙", description="유저를 블랙리스트에 등록하고 차단합니다.")
@app_commands.describe(member="대상 유저")
async def warn_blacklist_sub(interaction: discord.Interaction, member: discord.Member):
    if not interaction.user.guild_permissions.ban_members:
        await interaction.response.send_message("⚠️ 권한이 없습니다.", ephemeral=True)
        return
    await interaction.response.send_modal(BlacklistModal(target=member))

@bot.tree.command(name="임베드생성", description="실시간 미리보기를 통해 커스텀 임베드 메시지를 생성합니다.")
async def embed_make_sub(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.manage_messages:
        await interaction.response.send_message("⚠️ 권한이 없습니다.", ephemeral=True)
        return
    if interaction.channel.id in active_builders:
        await interaction.response.send_message("⚠️ 이미 진행 중인 빌더가 있습니다.", ephemeral=True)
        return
    await interaction.response.send_message("✨ 임베드 빌더를 시작합니다!", ephemeral=True)
    view = EmbedBuilderView(interaction.user.id, interaction.channel)
    active_builders[interaction.channel.id] = view
    await view.send_initial()

@bot.tree.command(name="임베드해제", description="현재 채널에 고정되어 있는 커스텀 임베드 메시지를 삭제하고 고정을 해제합니다.")
async def embed_remove_sub(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.manage_messages:
        await interaction.response.send_message("⚠️ 권한이 없습니다.", ephemeral=True)
        return

    channel_id = interaction.channel_id

    if channel_id in sticky_embeds:
        if channel_id in sticky_tasks:
            sticky_tasks[channel_id].cancel()
            del sticky_tasks[channel_id]

        sticky_data = sticky_embeds[channel_id]
        if sticky_data.get("message"):
            try:
                await sticky_data["message"].delete()
            except Exception:
                pass

        del sticky_embeds[channel_id]
        await interaction.response.send_message("🗑️ 고정 임베드가 삭제되고 해제되었습니다.", ephemeral=True)
    else:
        await interaction.response.send_message("⚠️ 현재 이 채널에 고정된 임베드 메시지가 없습니다.", ephemeral=True)

@bot.tree.command(name="청소", description="채팅방 메시지를 일괄 삭제합니다.")
@app_commands.describe(amount="삭제할 개수 (1~100)")
@app_commands.rename(amount="개수")
async def purge_command(interaction: discord.Interaction, amount: app_commands.Range[int, 1, 100]):
    if not interaction.user.guild_permissions.manage_messages:
        await interaction.response.send_message("⚠️ 권한이 없습니다.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    try:
        deleted = await interaction.channel.purge(limit=amount, bulk=True)
        await interaction.followup.send(f"🧹 {interaction.user.mention} 님에 의해 메시지 {len(deleted)}개가 삭제되었습니다.", ephemeral=True)
    except discord.HTTPException:
        await interaction.followup.send("⚠️ 14일이 지난 메시지는 일괄 삭제할 수 없습니다.", ephemeral=True)

# .env 로직을 지우고 토큰을 직접 입력합니다.
import os
token = os.environ.get("DISCORD_TOKEN")
bot.run(token)