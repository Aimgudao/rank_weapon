import os
import random
from discord.ext import commands
import discord
from flask import Flask
import threading

# --- 設定・定数 ---
TOKEN = os.getenv("DISCORD_TOKEN", "YOUR_BOT_TOKEN_HERE")

# ランク定義 (上から強い順)
RANKS = {
    "レインボー": {"color": "🟣", "text": "紫"},
    "クリムゾン": {"color": "🔴", "text": "赤"},
    "ダイヤモンド": {"color": "🔵", "text": "青"},
    "プラチナ": {"color": "🟢", "text": "薄緑"},
    "ゴールド": {"color": "🟡", "text": "金"},
}
RANK_WEIGHTS = {
    "レインボー": 5,
    "クリムゾン": 4,
    "ダイヤモンド": 3,
    "プラチナ": 2,
    "ゴールド": 1,
}

# 武器定義
WEAPONS = ["AR", "SMG", "Flex", "SR"]

# --- データ管理用グローバル変数 ---
participants_per_guild = {}
priority_pool_per_guild = {}
current_mode_per_guild = {}
panel_message_ids = {}  # サーバーごとのパネルメッセージIDを保持
latest_teams_per_guild = {}  # サーバーごとの直近のチーム分け結果を保持


class CustomMatchBot(commands.Bot):

  def __init__(self):
    intents = discord.Intents.default()
    intents.message_content = True
    intents.members = True
    intents.voice_states = True
    super().__init__(command_prefix="!", intents=intents)

  async def setup_hook(self):
    self.add_view(RegistrationView())
    await self.tree.sync()


bot = CustomMatchBot()


# --- データ取得・初期化ヘルパー ---
def get_guild_participants(guild_id: int):
  if guild_id not in participants_per_guild:
    participants_per_guild[guild_id] = {}
  return participants_per_guild[guild_id]


def get_guild_priority(guild_id: int):
  if guild_id not in priority_pool_per_guild:
    priority_pool_per_guild[guild_id] = []
  return priority_pool_per_guild[guild_id]


def set_guild_priority(guild_id: int, pool: list):
  priority_pool_per_guild[guild_id] = pool


def get_guild_mode(guild_id: int):
  return current_mode_per_guild.get(guild_id, "both")


def set_guild_mode(guild_id: int, mode: str):
  current_mode_per_guild[guild_id] = mode


# --- 参加者一覧・チーム結果のEmbed生成（複数Embed対応） ---
def build_status_embeds(guild_id: int):
  participants = get_guild_participants(guild_id)
  active_list = []
  afk_list = []

  for uid, data in participants.items():
    ps_str = "🎮【PS】" if data["is_ps"] else ""
    rank_info = RANKS.get(data["rank"], {"color": "⚪"})
    entry = f"{rank_info['color']} **{data['name']}** [ {data['weapon']} ] {ps_str}"

    if data["is_afk"]:
      afk_list.append(entry)
    else:
      active_list.append(entry)

  embeds = []

  # 1. JOINパネル
  embed_join = discord.Embed(
      title=f"🟢 JOIN ({len(active_list)}人)",
      description="\n".join(active_list) if active_list else "なし",
      color=0x2F3136,
  )
  embeds.append(embed_join)

  # 2. AFKパネル（メンバーがいる場合のみ追加）
  if afk_list:
    embed_afk = discord.Embed(
        title=f"💤 AFK ({len(afk_list)}人)",
        description="\n".join(afk_list),
        color=0x2F3136,
    )
    embeds.append(embed_afk)

  # 3. チーム分け結果パネル（結果が存在する場合）
  if guild_id in latest_teams_per_guild:
    team_data = latest_teams_per_guild[guild_id]
    
    embed_team_a = discord.Embed(
        title="🟦 チームA",
        description=team_data["team_a_str"] if team_data["team_a_str"] else "なし",
        color=0x3498DB,
    )
    embeds.append(embed_team_a)

    embed_team_b = discord.Embed(
        title="🟥 チームB",
        description=team_data["team_b_str"] if team_data["team_b_str"] else "なし",
        color=0xE74C3C,
    )
    embeds.append(embed_team_b)

    if team_data["excluded_user"]:
      embed_caster = discord.Embed(
          title="🎙️ キャスター",
          description=f"{team_data['excluded_name']}さん (次回優先)",
          color=0x95A5A6,
      )
      embeds.append(embed_caster)

  return embeds


# --- 動的にボタン状態や選択肢を調整するビュークラス ---
class RegistrationView(discord.ui.View):

  def __init__(self, guild_id: int = None, user_id: int = None):
    super().__init__(timeout=None)
    if guild_id:
      participants = get_guild_participants(guild_id)
      is_ps_on = False
      if user_id and user_id in participants:
        is_ps_on = participants[user_id].get("is_ps", False)

      for child in self.children:
        if isinstance(child, discord.ui.Button) and child.custom_id == "btn_ps":
          if is_ps_on:
            child.label = "PlayStationで参加 (ON)"
            child.style = discord.ButtonStyle.green
          else:
            child.label = "PlayStationで参加 (OFF)"
            child.style = discord.ButtonStyle.gray

        elif isinstance(child, discord.ui.Select) and child.custom_id == "toggle_other_afk":
          if not participants:
            child.options = [discord.SelectOption(label="登録者がいません", value="none")]
          else:
            options = []
            for uid, data in participants.items():
              state_text = "AFK" if data["is_afk"] else "Active"
              action_text = "→Active" if data["is_afk"] else "→AFK"
              label = f"{data['name']} ({state_text} {action_text})"
              if len(label) > 100:
                label = label[:97] + "..."
              options.append(discord.SelectOption(label=label, value=str(uid)))
              if len(options) >= 25:
                break
            child.options = options

  @discord.ui.select(
      placeholder="ランクを選択",
      options=[
          discord.SelectOption(label="🟣 レインボー", value="レインボー"),
          discord.SelectOption(label="🔴 クリムゾン", value="クリムゾン"),
          discord.SelectOption(label="🔵 ダイヤモンド", value="ダイヤモンド"),
          discord.SelectOption(label="🟢 プラチナ", value="プラチナ"),
          discord.SelectOption(label="🟡 ゴールド", value="ゴールド"),
      ],
      custom_id="select_rank",
  )
  async def select_rank(
      self, interaction: discord.Interaction, select: discord.ui.Select
  ):
    guild_id = interaction.guild_id
    participants = get_guild_participants(guild_id)
    uid = interaction.user.id

    if uid not in participants:
      participants[uid] = {
          "name": interaction.user.display_name,
          "rank": select.values[0],
          "weapon": "AR",
          "is_ps": False,
          "is_afk": False,
      }
    else:
      participants[uid]["rank"] = select.values[0]

    await refresh_panels(interaction, guild_id)

  @discord.ui.select(
      placeholder="武器を選択",
      options=[
          discord.SelectOption(label="アサルトライフル (AR)", value="AR"),
          discord.SelectOption(label="サブマシンガン (SMG)", value="SMG"),
          discord.SelectOption(label="フレックス (AR/SMG)", value="Flex"),
          discord.SelectOption(label="スナイパー (SR)", value="SR"),
      ],
      custom_id="select_weapon",
  )
  async def select_weapon(
      self, interaction: discord.Interaction, select: discord.ui.Select
  ):
    guild_id = interaction.guild_id
    participants = get_guild_participants(guild_id)
    uid = interaction.user.id

    if uid not in participants:
      participants[uid] = {
          "name": interaction.user.display_name,
          "rank": "ゴールド",
          "weapon": select.values[0],
          "is_ps": False,
          "is_afk": False,
      }
    else:
      participants[uid]["weapon"] = select.values[0]

    await refresh_panels(interaction, guild_id)

  @discord.ui.button(
      label="PlayStationで参加 (OFF)",
      style=discord.ButtonStyle.gray,
      custom_id="btn_ps",
  )
  async def toggle_ps(
      self, interaction: discord.Interaction, button: discord.ui.Button
  ):
    guild_id = interaction.guild_id
    participants = get_guild_participants(guild_id)
    uid = interaction.user.id

    if uid not in participants:
      participants[uid] = {
          "name": interaction.user.display_name,
          "rank": "ゴールド",
          "weapon": "AR",
          "is_ps": True,
          "is_afk": False,
      }
    else:
      participants[uid]["is_ps"] = not participants[uid]["is_ps"]

    await refresh_panels(interaction, guild_id)

  @discord.ui.button(
      label="Active / AFK",
      style=discord.ButtonStyle.blurple,
      custom_id="btn_afk",
  )
  async def toggle_afk(
      self, interaction: discord.Interaction, button: discord.ui.Button
  ):
    guild_id = interaction.guild_id
    participants = get_guild_participants(guild_id)
    uid = interaction.user.id

    if uid in participants:
      participants[uid]["is_afk"] = not participants[uid]["is_afk"]
    else:
      participants[uid] = {
          "name": interaction.user.display_name,
          "rank": "ゴールド",
          "weapon": "AR",
          "is_ps": False,
          "is_afk": True,
      }

    await refresh_panels(interaction, guild_id)

  @discord.ui.button(
      label="チーム編成 / 再編成",
      style=discord.ButtonStyle.red,
      custom_id="btn_match",
  )
  async def start_match(
      self, interaction: discord.Interaction, button: discord.ui.Button
  ):
    guild_id = interaction.guild_id
    if guild_id in latest_teams_per_guild:
      current_mode = get_guild_mode(guild_id)
      if not interaction.response.is_done():
        await interaction.response.defer()
      await execute_team_split(interaction.channel, current_mode)
    else:
      view = MatchModeView()
      await interaction.response.send_message(
          "チーム分け基準を選択してください：", view=view, ephemeral=True
      )

  @discord.ui.select(
      placeholder="管理",
      options=[discord.SelectOption(label="登録者がいません", value="none")],
      custom_id="toggle_other_afk",
  )
  async def toggle_other_afk(
      self, interaction: discord.Interaction, select: discord.ui.Select
  ):
    guild_id = interaction.guild_id
    participants = get_guild_participants(guild_id)

    selected_val = select.values[0]
    if selected_val == "none":
      await interaction.response.defer()
      return

    target_uid = int(selected_val)
    if target_uid in participants:
      participants[target_uid]["is_afk"] = not participants[target_uid]["is_afk"]
      await refresh_panels(interaction, guild_id)
    else:
      await interaction.response.defer()


# --- パネル全体を更新する共通関数 ---
async def refresh_panels(interaction: discord.Interaction, guild_id: int):
  if guild_id in panel_message_ids:
    try:
      msg_id = panel_message_ids[guild_id]
      status_msg = await interaction.channel.fetch_message(msg_id)
      await status_msg.edit(
          embeds=build_status_embeds(guild_id), view=None
      )
    except (discord.NotFound, discord.HTTPException):
      pass

  new_view = RegistrationView(guild_id, interaction.user.id)
  try:
    if interaction.response.is_done():
      await interaction.edit_original_response(content="\u200b", view=new_view)
    else:
      await interaction.response.edit_message(content="\u200b", view=new_view)
  except discord.HTTPException:
    pass


class MatchModeView(discord.ui.View):

  def __init__(self):
    super().__init__(timeout=180)

  async def run_matchmaking(
      self, interaction: discord.Interaction, mode: str
  ):
    guild_id = interaction.guild_id
    set_guild_mode(guild_id, mode)
    if not interaction.response.is_done():
      await interaction.response.defer()
    await execute_team_split(interaction.channel, mode)

  @discord.ui.button(label="ランク＆武器", style=discord.ButtonStyle.primary)
  async def mode_both(
      self, interaction: discord.Interaction, button: discord.ui.Button
  ):
    await self.run_matchmaking(interaction, "both")

  @discord.ui.button(label="ランク", style=discord.ButtonStyle.primary)
  async def mode_rank(
      self, interaction: discord.Interaction, button: discord.ui.Button
  ):
    await self.run_matchmaking(interaction, "rank")

  @discord.ui.button(label="武器", style=discord.ButtonStyle.primary)
  async def mode_weapon(
      self, interaction: discord.Interaction, button: discord.ui.Button
  ):
    await self.run_matchmaking(interaction, "weapon")

  @discord.ui.button(label="ランダム", style=discord.ButtonStyle.primary)
  async def mode_random(
      self, interaction: discord.Interaction, button: discord.ui.Button
  ):
    await self.run_matchmaking(interaction, "random")


# --- チーム分けロジック & パネル上書き更新 ---
async def execute_team_split(channel, mode):
  guild = channel.guild
  guild_id = guild.id
  participants = get_guild_participants(guild_id)
  priority_pool = get_guild_priority(guild_id)

  pool = [uid for uid, data in participants.items() if not data["is_afk"]]

  if len(pool) < 2:
    await channel.send("参加者が足りません (最低2人必要)", delete_after=5)
    return

  excluded_user = None
  excluded_name = None
  if len(pool) % 2 != 0:
    priority_candidates = [uid for uid in pool if uid not in priority_pool]
    if not priority_candidates:
      priority_candidates = pool

    excluded_user = random.choice(priority_candidates)
    excluded_name = participants[excluded_user]["name"]
    pool.remove(excluded_user)
    set_guild_priority(guild_id, [excluded_user])
  else:
    set_guild_priority(guild_id, [])

  if mode == "random":
    random.shuffle(pool)
  elif mode == "rank":
    pool.sort(
        key=lambda uid: RANK_WEIGHTS.get(participants[uid]["rank"], 0),
        reverse=True,
    )
  elif mode == "weapon":
    pool.sort(key=lambda uid: participants[uid]["weapon"])
  else:
    pool.sort(
        key=lambda uid: (
            RANK_WEIGHTS.get(participants[uid]["rank"], 0),
            participants[uid]["weapon"],
        ),
        reverse=True,
    )

  team_a, team_b = [], []
  chunks = [pool[i : i + 2] for i in range(0, len(pool), 2)]
  for idx, chunk in enumerate(chunks):
    if idx % 2 == 0:
      if len(chunk) == 2:
        team_a.append(chunk[0])
        team_b.append(chunk[1])
      else:
        team_a.append(chunk[0])
    else:
      if len(chunk) == 2:
        team_b.append(chunk[0])
        team_a.append(chunk[1])
      else:
        team_b.append(chunk[0])

  # ランクアイコンやメンションを省き、名前（太字）だけに整形
  def format_team(team_list):
    lines = []
    for uid in team_list:
      d = participants[uid]
      lines.append(f"**{d['name']}**")
    return "\n".join(lines) if lines else "なし"

  latest_teams_per_guild[guild_id] = {
      "team_a_str": format_team(team_a),
      "team_b_str": format_team(team_b),
      "excluded_user": excluded_user,
      "excluded_name": excluded_name,
  }

  if guild_id in panel_message_ids:
    try:
      msg_id = panel_message_ids[guild_id]
      status_msg = await channel.fetch_message(msg_id)
      await status_msg.edit(
          embeds=build_status_embeds(guild_id), view=None
      )
    except (discord.NotFound, discord.HTTPException):
      pass

  # --- VC移動処理 ---
  voice_channels = sorted(guild.voice_channels, key=lambda c: c.position)
  if len(voice_channels) < 2:
    return

  vc_a = voice_channels[0]
  vc_b = voice_channels[1]

  ps_users_to_notify = []

  async def move_members(team_members, target_vc):
    for uid in team_members:
      member = guild.get_member(uid)
      if not member or not member.voice:
        continue

      if participants[uid]["is_ps"]:
        ps_users_to_notify.append((member, target_vc))
      else:
        try:
          await member.move_to(target_vc)
        except discord.Forbidden:
          pass

  await move_members(team_a, vc_a)
  await move_members(team_b, vc_b)

  if ps_users_to_notify:
    mentions = " ".join([f"{m.mention}" for m, _ in ps_users_to_notify])
    notice_text = f"{mentions} PlayStationで参加中の方は手動で指定のボイスチャンネルへ移動してください。"
    await channel.send(content=notice_text, delete_after=15)


# --- /カスタムマッチ コマンド ---
@bot.tree.command(
    name="カスタムマッチ",
    description="カスタムマッチの登録パネルを表示し、データをリセットします",
)
async def cmd_custom_match(interaction: discord.Interaction):
  guild_id = interaction.guild_id
  
  if guild_id in panel_message_ids:
    try:
      old_msg = await interaction.channel.fetch_message(panel_message_ids[guild_id])
      await old_msg.delete()
    except (discord.NotFound, discord.HTTPException):
      pass

  participants = get_guild_participants(guild_id)
  participants.clear()
  set_guild_priority(guild_id, [])
  if guild_id in latest_teams_per_guild:
    del latest_teams_per_guild[guild_id]

  view = RegistrationView(guild_id, interaction.user.id)
  await interaction.response.send_message(content="\u200b", view=view)

  status_embeds = build_status_embeds(guild_id)
  status_msg = await interaction.channel.send(
      embeds=status_embeds, view=None
  )

  panel_message_ids[guild_id] = status_msg.id


# --- Renderのスリープ防止用簡易Webサーバー ---
app = Flask(__name__)


@app.route("/")
def home():
  return "Bot is running!"


def run_web():
  port = int(os.environ.get("PORT", 10000))
  app.run(host="0.0.0.0", port=port, debug=False)


if __name__ == "__main__":
  web_thread = threading.Thread(target=run_web)
  web_thread.daemon = True
  web_thread.start()

  bot.run(TOKEN)