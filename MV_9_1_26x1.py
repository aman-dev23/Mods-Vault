#====================================================
# 🔹 IMPORTS
#====================================================

from dotenv import load_dotenv
import os
import io
import asyncio
import logging
import re
import random
import sqlite3
import math
import discord
from discord import Embed, app_commands, ui
from discord.ext import commands, tasks
from discord.ui import Button, View, Select, Modal, TextInput
from collections import defaultdict, Counter
from datetime import datetime, timedelta, timezone
import time
#====================================================
# 🔹 CONSTANTS & DB PATHS
#====================================================

BASE_DIR = r"C:\Users\amanm\OneDrive\Documents\Mods_Vault_2026"
ENV_PATH = r"C:\Users\amanm\OneDrive\Documents\Mods_Vault_2026/.env"
load_dotenv(ENV_PATH)
TOKEN = os.getenv("MV_TOKEN")
if not TOKEN:
    raise RuntimeError(f"MV_TOKEN not found. Checked path: {ENV_PATH}")

ACTION_LOGS_DB = os.path.join(BASE_DIR, "Action_logs_all_server.db")
MUTE_DB = os.path.join(BASE_DIR, "mute_data.db")
TEMP_ROLE_DB = os.path.join(BASE_DIR, "temporary_roles.db")
SERVER_CONFIG_DB = os.path.join(BASE_DIR, "server_config.db")
PROOF_QUEUE_DB = os.path.join(BASE_DIR, "proof_queue.db")
APPEAL_COOLDOWN = {}      # user_id : unlock_timestamp
COOLDOWN_SECONDS = 180   # 3 minutes

MAX_PROOFS_PER_ACTION = 10
MAX_FILE_SIZE = 7 * 1024 * 1024  # 7 MB
MAX_RETRIES = 3

ALLOWED_CONTENT_PREFIXES = (
    "image/",
    "video/",
    "audio/",
)

PROOF_STORAGE_CHANNEL_IDS = [
    1458815950043353345,
    1458815952165798160,
    1458815954493509823,
    1458815956561297408,
    1458815959052582999,
    1458815961435214020,
    1458815963624509575,
    1458815966287888550,
    1458815968900812974,
    1458815971694477312,
    1458815973636444394,
    1458815976148701226,
    1458815978715615232,
    1458815981395640517,
    1458815983786659882,
    1458815985787207805,
    1458815988756779081,
    1458815990933618813,
    1458815993068523771,
    1458815995203420170,
    1458815997208297485,
    1458815999808639160,
    1458816002564423741,
    1458816004556718134,
    1458816006955991162,
    1458816009514389667,
    1458816011858870304,
    1458816013935185992,
    1458816016455958548,
    1458816018746048619,
    1458816021174423828,
    1458816024207163463,
    1458816026933203158,
    1458816029517156452,
    1458816032574541856,
    1458816035137392762,
    1458816037020504225,
    1458816039679954945,
    1458816041932030104,
    1458816045132288054,
    1458816047582019830,
    1458816050287087790,
    1458816052640350248,
    1458816054686912544,
    1458816057253953578,
    1458816061968220202,
    1458816066041024701,
    1458816068305948858,
    1458816070767870004,
    1458816072890187880,
]

intents = discord.Intents.default()
intents.members = True
intents.messages = True
intents.guilds = True
intents.reactions = True
intents.voice_states = True
intents.message_content = True
intents.moderation = True

bot = commands.Bot(command_prefix="!", intents=intents)
#====================================================
# 🔹 DATABASE HELPERS
#====================================================

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def get_db_connection():
    conn = sqlite3.connect(ACTION_LOGS_DB)  # Changed file name
    conn.row_factory = sqlite3.Row
    return conn


def _connect(db_path):
    return sqlite3.connect(db_path)
    
def enable_wal(db_path):
    try:
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.close()
        logger.info(f"🗄 WAL enabled for {os.path.basename(db_path)}")
    except Exception as e:
        logger.error(f"❌ Failed to enable WAL for {db_path}: {e}")

def get_server_config(guild_id: str):
    conn = sqlite3.connect(SERVER_CONFIG_DB)
    cur = conn.cursor()
    cur.execute("""
        SELECT log_channel_id, action_dm_status, proof_dm_status, appeal_toggle
        FROM server_config WHERE guild_id = ?
    """, (guild_id,))
    row = cur.fetchone()
    conn.close()

    if not row:
        return None

    return {
        "log_channel_id": row[0],
        "action_dm_status": row[1],
        "proof_dm_status": row[2],
        "appeal_toggle": row[3],
    }

def upsert_server_config(guild_id: str, **kwargs):
    conn = sqlite3.connect(SERVER_CONFIG_DB)
    cur = conn.cursor()

    cur.execute("SELECT guild_id FROM server_config WHERE guild_id=?", (guild_id,))
    exists = cur.fetchone()

    if not exists:
        cur.execute("""
            INSERT INTO server_config (
                guild_id,
                log_channel_id,
                action_dm_status,
                proof_dm_status,
                appeal_toggle
            ) VALUES (?, ?, ?, ?, ?)
        """, (
            guild_id,
            kwargs.get("log_channel_id"),
            kwargs.get("action_dm_status", "off"),
            kwargs.get("proof_dm_status", "off"),
            kwargs.get("appeal_toggle", "off"),
        ))
    else:
        for k, v in kwargs.items():
            cur.execute(
                f"UPDATE server_config SET {k}=? WHERE guild_id=?",
                (v, guild_id)
            )

    conn.commit()
    conn.close()
    

def log_moderation_action_to_db(guild, user, action, duration, reason, moderator, proof=None):
    conn = get_db_connection()
    action_id = generate_action_id(str(guild.id))
    time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    
    conn.execute("""
    INSERT INTO moderation_logs (action_id, guild_id, time, display_name, username, user_id, action, duration, reason, moderator, proof)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (action_id, str(guild.id), time, user.display_name, user.name, str(user.id), action, duration, reason, moderator.display_name, proof))
    conn.commit()
    conn.close()
    return action_id

def get_moderation_logs(guild_id, action_id=None):
    conn = get_db_connection()
    cursor = conn.cursor()
    if action_id:
        cursor.execute(
            "SELECT * FROM moderation_logs WHERE guild_id = ? AND action_id = ?",
            (str(guild_id), action_id)
        )
    else:
        cursor.execute(
            "SELECT * FROM moderation_logs WHERE guild_id = ?",
            (str(guild_id),)
        )
    rows = cursor.fetchall()
    conn.close()
    return rows

def update_moderation_log(guild_id, action_id, duration=None, reason=None, proof=None):
    conn = get_db_connection()
    cursor = conn.cursor()
    if duration:
        cursor.execute(
            "UPDATE moderation_logs SET duration = ? WHERE guild_id = ? AND action_id = ?",
            (duration, str(guild_id), action_id)
        )
    if reason:
        cursor.execute(
            "UPDATE moderation_logs SET reason = ? WHERE guild_id = ? AND action_id = ?",
            (reason, str(guild_id), action_id)
        )
    if proof:
        cursor.execute(
            "SELECT proof FROM moderation_logs WHERE guild_id = ? AND action_id = ?",
            (str(guild_id), action_id)
        )
        row = cursor.fetchone()
        existing = row["proof"].split(",") if row and row["proof"] else []
        if proof not in existing and len(existing) < 10:
            existing.append(proof)
            cursor.execute(
                "UPDATE moderation_logs SET proof = ? WHERE guild_id = ? AND action_id = ?",
                (",".join(existing), str(guild_id), action_id)
            )
    conn.commit()
    conn.close()
    


def safe_delete(query, params, db_path):
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(query, params)
        conn.commit()
    except Exception as e:
        print(f"[CLEANUP ERROR] {db_path}: {e}")
    finally:
        try:
            conn.close()
        except:
            pass

def cleanup_server_config(guild_id: str):
    safe_delete(
        "DELETE FROM server_config WHERE guild_id = ?",
        (guild_id,),
        SERVER_CONFIG_DB
    )


def cleanup_mute_data(guild_id: str):
    safe_delete(
        "DELETE FROM mute_data WHERE guild_id = ?",
        (guild_id,),
        MUTE_DB
    )

def cleanup_temp_roles(guild_id: str):
    safe_delete(
        "DELETE FROM temporary_roles WHERE guild_id = ?",
        (guild_id,),
        TEMP_ROLE_DB
    )


def is_within_duration(date_str, duration):
    now = datetime.now(timezone.utc)
    date = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
    if duration == "1D":
        return (now - date).days < 1
    elif duration == "7D":
        return (now - date).days < 7
    elif duration == "30D":
        return (now - date).days < 30
    elif duration == "all":
        return True
    return False

def generate_action_id(guild_id: str):
    conn = sqlite3.connect(ACTION_LOGS_DB)
    cur = conn.cursor()

    cur.execute("BEGIN IMMEDIATE")  # lock safely

    cur.execute(
        "SELECT last_action_id FROM action_counters WHERE guild_id = ?",
        (guild_id,)
    )
    row = cur.fetchone()

    if row:
        new_id = row[0] + 1
        cur.execute(
            "UPDATE action_counters SET last_action_id = ? WHERE guild_id = ?",
            (new_id, guild_id)
        )
    else:
        new_id = 100501
        cur.execute(
            "INSERT INTO action_counters (guild_id, last_action_id) VALUES (?, ?)",
            (guild_id, new_id)
        )

    conn.commit()
    conn.close()
    return new_id

def format_duration(seconds):
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    minutes = (seconds % 3600) // 60
    return f"{int(days)}D {int(hours)}h {int(minutes)}m"


def has_mute_permission(interaction: discord.Interaction) -> bool:
    return (
        interaction.guild is not None
        and isinstance(interaction.user, discord.Member)
        and interaction.user.guild_permissions.mute_members
    )
    
def is_admin(interaction: discord.Interaction) -> bool:
    return (
        interaction.guild is not None
        and isinstance(interaction.user, discord.Member)
        and interaction.user.guild_permissions.administrator
    )

def is_moderator(interaction: discord.Interaction) -> bool:
    return (
        interaction.guild is not None
        and isinstance(interaction.user, discord.Member)
        and interaction.user.guild_permissions.moderate_members
    )

def can_manage_roles(interaction: discord.Interaction) -> bool:
    return (
        interaction.guild is not None
        and isinstance(interaction.user, discord.Member)
        and interaction.user.guild_permissions.manage_roles
    )

def get_appeal_category_id(guild_id: str):
    conn = sqlite3.connect(SERVER_CONFIG_DB)
    cur = conn.cursor()
    cur.execute(
        "SELECT appeal_category_id FROM server_config WHERE guild_id=?",
        (guild_id,)
    )
    row = cur.fetchone()
    conn.close()
    return row[0] if row and row[0] else None


def set_appeal_category_id(guild_id: str, category_id: int):
    conn = sqlite3.connect(SERVER_CONFIG_DB)
    conn.execute(
        "UPDATE server_config SET appeal_category_id=? WHERE guild_id=?",
        (category_id, guild_id)
    )
    conn.commit()
    conn.close()

def mark_appeal_used(guild_id: str, action_id: str):
    conn = sqlite3.connect(ACTION_LOGS_DB)
    cur = conn.cursor()

    cur.execute("""
        UPDATE moderation_logs
        SET appeal_used=1
        WHERE guild_id=? AND action_id=?
    """, (str(guild_id), str(action_id)))

    if cur.rowcount == 0:
        raise RuntimeError("No rows updated for appeal_used")

    conn.commit()
    conn.close()

def is_appeal_used(guild_id: str, action_id: str):
    conn = sqlite3.connect(ACTION_LOGS_DB)
    cur = conn.cursor()

    cur.execute("""
        SELECT appeal_used FROM moderation_logs
        WHERE guild_id=? AND action_id=?
        LIMIT 1
    """, (str(guild_id), str(action_id)))

    row = cur.fetchone()
    conn.close()

    return row is not None and row[0] == 1

def validate_appeal(guild_id, action_id, user_id, moderator_name=None):
    conn = sqlite3.connect(ACTION_LOGS_DB)
    cur = conn.cursor()

    if moderator_name is not None:
        # DM appeal flow (strict check)
        cur.execute("""
            SELECT appeal_used FROM moderation_logs
            WHERE guild_id=?
              AND action_id=?
              AND user_id=?
              AND moderator=?
            LIMIT 1
        """, (
            str(guild_id),
            int(action_id),
            str(user_id),
            moderator_name
        ))
    else:
        # Slash command flow
        cur.execute("""
            SELECT appeal_used FROM moderation_logs
            WHERE guild_id=?
              AND action_id=?
              AND user_id=?
            LIMIT 1
        """, (
            str(guild_id),
            int(action_id),
            str(user_id)
        ))

    row = cur.fetchone()
    conn.close()

    if not row:
        return False, "invalid"   # action not found / not user's

    if row[0] == 1:
        return False, "used"      # appeal already used

    return True, "ok"
    
async def handle_appeal(interaction: discord.Interaction):
    # ✅ ACK FIRST (no interaction failed)
    await interaction.response.defer(ephemeral=True)

    # Must be clicked from DM
    if interaction.guild is not None:
        return await interaction.followup.send(
            "❌ Please use the Appeal button from your DM.",
            ephemeral=True
        )

    if not interaction.message.embeds:
        return await interaction.followup.send(
            "❌ Invalid appeal message.",
            ephemeral=True
        )

    embed = interaction.message.embeds[0]

    # -----------------------------
    # Extract Action ID
    # -----------------------------
    action_id = next(
        (f.value.strip("`") for f in embed.fields if "Action ID" in f.name),
        None
    )

    # -----------------------------
    # Extract Guild ID
    # -----------------------------
    server_field = next(
        (f.value for f in embed.fields if "Server" in f.name),
        None
    )

    if not action_id or not server_field or "\n" not in server_field:
        return await interaction.followup.send(
            "❌ Appeal data missing.",
            ephemeral=True
        )

    guild_id = server_field.split("\n")[-1].strip("`")
    guild = interaction.client.get_guild(int(guild_id))

    if not guild:
        return await interaction.followup.send(
            "❌ Bot is no longer in that server.",
            ephemeral=True
        )

    user = interaction.user

    # -----------------------------
    # 🔒 VALIDATE APPEAL
    # -----------------------------
    valid, reason = validate_appeal(
        guild_id=guild_id,
        action_id=action_id,
        user_id=user.id,
        moderator_name=None
    )

    if not valid:
        if reason == "used":
            return await interaction.followup.send(
                "❌ An appeal for this action has already been submitted and cannot be created again.",
                ephemeral=True
            )
        else:
            return await interaction.followup.send(
                "❌ Invalid Action ID or you are not allowed to appeal this action.",
                ephemeral=True
            )

    # -----------------------------
    # CREATE APPEAL CHANNEL (CRITICAL)
    # -----------------------------
    try:
        channel, created = await create_appeal_channel(
            guild,
            user,
            action_id
        )
    except Exception:
        return await interaction.followup.send(
            "❌ Failed to create appeal. Please contact a moderator.",
            ephemeral=True
        )

    # -----------------------------
    # MARK APPEAL USED (NON-FATAL)
    # -----------------------------
    if created:
        try:
            mark_appeal_used(guild_id, action_id)
        except Exception as e:
            logger.error(
                f"Appeal channel created but failed to update appeal_used "
                f"(guild={guild_id}, action={action_id}): {e}"
            )

    # -----------------------------
    # DM CONFIRMATION
    # -----------------------------
    channel_link = f"https://discord.com/channels/{guild.id}/{channel.id}"
    title = "✅ Appeal Created" if created else "ℹ️ Appeal Already Exists"

    try:
        dm = await user.create_dm()
        dm_embed = discord.Embed(
            title=title,
            description=(
                f"👤 **User:** `{user.name}`\n"
                f"📌 **Action ID:** `{action_id}`\n"
                f"🔗 **Appeal Channel:** [Open Appeal]({channel_link})\n\n"
                "This link will work as long as the appeal exists."
            ),
            color=discord.Color.green()
        )
        await dm.send(embed=dm_embed)
    except discord.Forbidden:
        pass
    except Exception as e:
        logger.error(f"Appeal DM failed: {e}")

    await interaction.followup.send(
        "✅ Appeal processed successfully.",
        ephemeral=True
    )

async def get_or_create_valid_appeal_category(guild: discord.Guild):
    category = None
    category_id = get_appeal_category_id(str(guild.id))

    # Try to fetch category from Discord
    if category_id:
        category = guild.get_channel(int(category_id))
        if category and not isinstance(category, discord.CategoryChannel):
            category = None

    # If category missing or inaccessible → create new
    if category is None:
        if not guild.me.guild_permissions.manage_channels:
            raise discord.Forbidden(None, "Missing Manage Channels permission")

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            guild.me: discord.PermissionOverwrite(
                view_channel=True,
                manage_channels=True
            )
        }

        category = await guild.create_category(
            name="Appeals",
            overwrites=overwrites,
            reason="Auto-created appeal category"
        )

        # Update DB with new category ID
        set_appeal_category_id(str(guild.id), category.id)

    return category



async def create_appeal_channel(guild: discord.Guild, user: discord.Member, action_id: str):
    # -----------------------------
    # Ensure server_config row exists
    # -----------------------------
    conn = sqlite3.connect(SERVER_CONFIG_DB)
    conn.execute(
        "INSERT OR IGNORE INTO server_config (guild_id) VALUES (?)",
        (str(guild.id),)
    )
    conn.commit()
    conn.close()

    # -----------------------------
    # Get or create category
    # -----------------------------
    category = await get_or_create_valid_appeal_category(guild)

    # -----------------------------
    # Prevent duplicate appeals
    # -----------------------------
    for ch in category.channels:
        if ch.name in (f"appeal-{action_id}", f"appeal-{action_id}-closed"):
            return ch, False

    # -----------------------------
    # Channel overwrites
    # -----------------------------
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        user: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True
        ),
        guild.me: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            manage_channels=True
        )
    }

    for role in guild.roles:
        if role.permissions.moderate_members and role < guild.me.top_role:
            overwrites[role] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True
            )

    # -----------------------------
    # ✅ CREATE CHANNEL (ONLY CRITICAL PART)
    # -----------------------------
    channel = await guild.create_text_channel(
        name=f"appeal-{action_id}",
        category=category,
        overwrites=overwrites,
        reason=f"Appeal created by {user}"
    )

    # -----------------------------
    # Post-create message (NON-FATAL)
    # -----------------------------
    try:
        embed = discord.Embed(
            description=(
                f"🟢 **Welcome {user.mention}**\n\n"
                f"📌 **Action ID:** `{action_id}`\n"
                "Support will be with you shortly."
            ),
            color=discord.Color.green()
        )
        embed.set_footer(
            text="⚠️ User cannot communicate while timeout is active."
        )

        await channel.send(
            embed=embed,
            view=AppealCloseView()
        )

    except Exception as e:
        # ❗ DO NOT FAIL APPEAL FOR THIS
        logger.error(f"Appeal channel created but failed to send welcome message: {e}")

    return channel, True

        

_storage_index = 0

async def get_next_storage_channel(bot):
    global _storage_index

    for _ in range(len(PROOF_STORAGE_CHANNEL_IDS)):
        cid = PROOF_STORAGE_CHANNEL_IDS[_storage_index]
        _storage_index = (_storage_index + 1) % len(PROOF_STORAGE_CHANNEL_IDS)

        channel = bot.get_channel(cid)
        if channel:
            return channel

        try:
            channel = await bot.fetch_channel(cid)
            return channel
        except:
            continue

    return None


async def handle_proof_reply(message: discord.Message):
    if not message.reference or not message.guild:
        return

    try:
        replied = await message.channel.fetch_message(
            message.reference.message_id
        )
    except:
        return

    if not replied.embeds:
        return

    embed = replied.embeds[0]
    field = next(
        (f for f in embed.fields if "Action ID" in f.name),
        None
    )
    if not field:
        return

    action_id = field.value.strip("`")

    attachments = message.attachments
    if not attachments:
        return

    # --------------------------------
    # 🔒 FILE TYPE VALIDATION (NEW)
    # --------------------------------
    valid_indexes = []
    rejected_files = []

    for i, a in enumerate(attachments):
        # content_type can be None
        if not a.content_type:
            rejected_files.append(a.filename)
            continue

        # allow only image / video / audio
        if not a.content_type.startswith(ALLOWED_CONTENT_PREFIXES):
            rejected_files.append(a.filename)
            continue

        # size check
        if a.size > MAX_FILE_SIZE:
            rejected_files.append(a.filename)
            continue

        valid_indexes.append(i)

    if not valid_indexes:
        await message.reply(
            "❌ **Invalid proof files detected.**\n\n"
            "**Allowed:** Images, Videos, Audio only\n"
            "**Not allowed:** PDF, ZIP, DB, executables, or other files.",
            delete_after=20
        )
        return

    # --------------------------------
    # 📊 COUNT EXISTING PROOFS
    # --------------------------------
    conn = sqlite3.connect(ACTION_LOGS_DB)
    cur = conn.cursor()
    cur.execute(
        "SELECT proof FROM moderation_logs WHERE action_id=?",
        (action_id,)
    )
    row = cur.fetchone()
    existing = row[0].split(",") if row and row[0] else []
    conn.close()

    if len(existing) + len(valid_indexes) > MAX_PROOFS_PER_ACTION:
        await message.reply(
            f"❌ Max {MAX_PROOFS_PER_ACTION} proofs allowed.\n"
            f"Already: {len(existing)}, You tried: {len(valid_indexes)}",
            delete_after=15
        )
        return

    # --------------------------------
    # 🧾 INSERT INTO QUEUE
    # --------------------------------
    now = int(time.time())
    qdb = sqlite3.connect(PROOF_QUEUE_DB)
    qc = qdb.cursor()

    for idx in valid_indexes:
        qc.execute("""
        INSERT INTO proof_queue
        (guild_id, channel_id, message_id, attachment_index,
         action_id, submitter_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            str(message.guild.id),
            str(message.channel.id),
            str(message.id),
            idx,
            action_id,
            str(message.author.id),
            now
        ))

    qdb.commit()
    qdb.close()

    # --------------------------------
    # ✅ USER FEEDBACK
    # --------------------------------
    await message.reply(
        f"⏳ **Proof accepted & queued**\n"
        f"✅ Accepted: {len(valid_indexes)}\n"
        f"❌ Rejected: {', '.join(rejected_files) if rejected_files else 'None'}\n"
        f"📦 Processing safely, this may take a moment.",
        delete_after=30
    )




#====================================================
# 🔹 DATABASE INITIALIZATION
#====================================================

def init_server_config_db():
    with _connect(SERVER_CONFIG_DB) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS server_config (
                guild_id TEXT PRIMARY KEY,
                log_channel_id INTEGER,
                action_dm_status TEXT DEFAULT 'off',
                proof_dm_status TEXT DEFAULT 'off',
                appeal_toggle TEXT DEFAULT 'off',
                appeal_category_id INTEGER
            )
        """)

def init_action_logs_db():
    with _connect(ACTION_LOGS_DB) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS moderation_logs (
                guild_id TEXT,
                action_id INTEGER,
                time TEXT,
                display_name TEXT,
                username TEXT,
                user_id TEXT,
                action TEXT,
                duration TEXT,
                reason TEXT,
                moderator TEXT,
                proof TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS action_counters (
                guild_id TEXT PRIMARY KEY,
                last_action_id INTEGER NOT NULL
            )
        """)
        conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_unique_action
            ON moderation_logs (guild_id, action_id)
        """)

def init_mute_db():
    with _connect(MUTE_DB) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS mute_data (
                user_id TEXT,
                guild_id TEXT,
                unmute_time INTEGER,
                PRIMARY KEY (user_id, guild_id)
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_guild_mutes ON mute_data (guild_id);"
        )


def init_db():
    with _connect(TEMP_ROLE_DB) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS temporary_roles (
                guild_id INTEGER,
                role_id INTEGER,
                user_id INTEGER,
                remove_time TEXT,
                PRIMARY KEY (guild_id, role_id, user_id)
            )
        """)

def init_proof_queue_db():
    with sqlite3.connect(PROOF_QUEUE_DB) as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS proof_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT,
            channel_id TEXT,
            message_id TEXT,
            attachment_index INTEGER,
            action_id TEXT,
            submitter_id TEXT,
            status TEXT DEFAULT 'pending',
            retry_count INTEGER DEFAULT 0,
            created_at INTEGER
        )
        """)


init_server_config_db()
init_action_logs_db()
init_mute_db()
init_db()
init_proof_queue_db()

enable_wal(PROOF_QUEUE_DB)
enable_wal(SERVER_CONFIG_DB)
enable_wal(ACTION_LOGS_DB)
enable_wal(MUTE_DB)
enable_wal(TEMP_ROLE_DB)




#====================================================
# 🔹 EMBED BUILDERS
#====================================================



def create_role_embed(action, role, success, exists=None, failed=None, missing=None, duration=None):
    embed = discord.Embed(
        color=discord.Color.green() if action == "add" else discord.Color.red(),
        title=f"Role {action.capitalize()} Report"
    )
    embed.add_field(name="Role", value=role.mention, inline=True)
    
    if duration:
        embed.add_field(name="Duration", value=duration, inline=True)
    
    if success:
        embed.add_field(
            name=f"✅ Successfully {action}ed", 
            value=f"{len(success)} users\n" + ", ".join(success[:5]) + ("..." if len(success) > 5 else ""),
            inline=False
        )
    
    if exists:
        embed.add_field(
            name="⚠️ Already Had Role" if action == "add" else "⚠️ Didn't Have Role",
            value=f"{len(exists)} users\n" + ", ".join(exists[:3]) + ("..." if len(exists) > 3 else ""),
            inline=False
        )
    
    if failed:
        embed.add_field(
            name="❌ Failed",
            value=f"{len(failed)} users\n" + "\n".join(failed[:3]) + ("..." if len(failed) > 3 else ""),
            inline=False
        )
    
    ts = int(datetime.now(timezone.utc).timestamp())
    embed.set_footer(text=f"Role will be removed after {duration} ")
    return embed
   #====================================================
# 🔹 CORE UTILITIES 
#====================================================

def parse_duration(duration_str):
    units = {'s':1, 'm':60, 'h':3600, 'd':86400, 'w':604800}
    seconds = 0
    num = []
    for char in duration_str.lower():
        if char.isdigit(): num.append(char)
        elif char in units:
            seconds += int(''.join(num)) * units[char] if num else units[char]
            num = []
    if num: seconds += int(''.join(num)) * 60
    return timedelta(seconds=seconds) if seconds > 0 else None

async def parse_users(interaction, users_str):
    users = []
    for entry in re.split(r"[, ]+", users_str.strip()):
        if not entry: continue
        if match := re.match(r"<@!?(\d+)>", entry): user_id = int(match.group(1))
        elif entry.isdigit(): user_id = int(entry)
        else: user_id = next((m.id for m in interaction.guild.members if m.name.lower() == entry.lower()), None)
        if user_id and (member := interaction.guild.get_member(user_id)) and member not in users: users.append(member)
    return users


def check_intents(bot):
    intents = bot.intents
    missing = []
    if not intents.guilds:
        missing.append("guilds")
    if not intents.members:
        missing.append("members")
    if not intents.moderation:
        missing.append("moderation")
    if not intents.message_content:
        missing.append("message_content")
    return missing


def check_db_health(db_path, table_name=None):
    try:
        conn = sqlite3.connect(db_path, timeout=5)
        cur = conn.cursor()
        if table_name:
            cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table_name,)
            )
            if not cur.fetchone():
                raise RuntimeError(f"Missing table: {table_name}")
        conn.execute("SELECT 1")
        conn.close()
        return True, None
    except Exception as e:
        return False, str(e)


def validate_appeal_by_user(guild_id: str, action_id: str, user_id: int):
    conn = sqlite3.connect(ACTION_LOGS_DB)
    cur = conn.cursor()

    cur.execute("""
        SELECT 1 FROM moderation_logs
        WHERE guild_id=?
          AND action_id=?
          AND user_id=?
        LIMIT 1
    """, (str(guild_id), str(action_id), str(user_id)))

    row = cur.fetchone()
    conn.close()

    return row is not None

            

# Update user DM
async def update_user_dm(guild, action_id, duration, reason):
    logs = get_moderation_logs(guild.id, action_id)
    if not logs:
        return

    user_id = logs[0]["user_id"]
    user = await bot.fetch_user(int(user_id))
    if not user:
        return

    dm_channel = await user.create_dm()

    async for message in dm_channel.history(limit=20):
        if message.embeds:
            embed = message.embeds[0]
            action_id_field = next((field for field in embed.fields if field.name == "📌 Action ID"), None)
            if action_id_field and action_id_field.value.strip("`") == action_id:
                try:
                    reason_index = next((i for i, field in enumerate(embed.fields) if field.name == "📝 Reason"), None)
                    if reason_index is not None:
                        embed.set_field_at(
                            index=reason_index,
                            name="📝 Reason",
                            value=reason,
                            inline=False
                        )

                    duration_index = next((i for i, field in enumerate(embed.fields) if field.name == "📆 Duration"), None)
                    if duration_index is not None:
                        embed.set_field_at(
                            index=duration_index,
                            name="📆 Duration",
                            value=duration,
                            inline=False
                        )

                    await message.edit(embed=embed)
                    break
                except Exception as e:
                    logger.error(f"Failed to update DM message for {user.name}: {e}")
                    break


async def log_moderation_action(guild, user, action, duration, reason, moderator):
    guild_id = str(guild.id)
    cfg = get_server_config(guild_id)
    if not cfg:
        return

    log_channel = bot.get_channel(cfg["log_channel_id"])
    if not log_channel:
        return

    # -------------------------------
    # Log to DB
    # -------------------------------
    action_id = log_moderation_action_to_db(
        guild, user, action, duration, reason, moderator
    )

    ts = int(datetime.now(timezone.utc).timestamp())

    # -------------------------------
    # Server log embed
    # -------------------------------
    log_embed = discord.Embed(
        title="🔹 Moderation Action Logged",
        color=discord.Color.blue()
    )

    log_embed.add_field(
        name="⏳ Action Time",
        value=f"<t:{ts}:F>",
        inline=False
    )
    log_embed.add_field(name="👨‍👩‍👦‍👦 Member", value=user.mention, inline=True)
    log_embed.add_field(name="👤 Moderator", value=moderator.mention, inline=True)
    log_embed.add_field(name="🚨 Action", value=f"**{action}**", inline=True)
    log_embed.add_field(name="📌 Action ID", value=f"`{action_id}`", inline=False)
    log_embed.add_field(name="📝 Reason", value=reason, inline=False)
    log_embed.add_field(name="📆 Duration", value=duration, inline=False)
    log_embed.set_footer(text="⚠️ Reply to this message with proof (if any).")

    if action == "Server Mute":
        view = MuteButtonView(action_id, user.id, guild.id)
        await log_channel.send(embed=log_embed, view=view)
    else:
        await log_channel.send(embed=log_embed)

    # -------------------------------
    # Offender DM
    # -------------------------------
    try:
        if cfg.get("action_dm_status", "on") != "on":
            return

        dm = await user.create_dm()

        dm_embed = discord.Embed(
            title="🔹 Moderation Action Notice",
            color=discord.Color.red()
        )

        dm_embed.add_field(
            name="👤 Moderator",
            value=moderator.mention,
            inline=True
        )
        dm_embed.add_field(
            name="🏠 Server",
            value=f"{guild.name}\n`{guild.id}`",
            inline=True
        )
        dm_embed.add_field(
            name="🚨 Action",
            value=f"**{action}**",
            inline=False
        )
        dm_embed.add_field(
            name="📌 Action ID",
            value=f"`{action_id}`",
            inline=False
        )
        dm_embed.add_field(
            name="📝 Reason",
            value=reason,
            inline=False
        )
        dm_embed.add_field(
            name="📆 Duration",
            value=duration,
            inline=False
        )
        dm_embed.set_footer(
            text="❗ If you believe this is a mistake, please contact the moderators."
        )

        # -------------------------------
        # Appeal toggle check
        # -------------------------------
        appeal_enabled = cfg.get("appeal_toggle", "off") == "on"

        if appeal_enabled:
            await dm.send(embed=dm_embed, view=AppealButtonView())
        else:
            await dm.send(embed=dm_embed)

    except Exception as e:
        logger.error(
            f"[ACTION DM ERROR] Guild={guild.id} User={user.id}: {e}"
        )

class MuteForm(Modal):
    def __init__(self, action_id, user_id, guild_id):
        super().__init__(title="Server Mute Form")
        self.action_id = action_id
        self.user_id = user_id
        self.guild_id = guild_id  
        self.reason = TextInput(label="Reason", placeholder="Enter the reason for the mute", required=False)
        self.duration = TextInput(label="Duration", placeholder="e.g., 1h, 30m, 1d", required=True)
        self.add_item(self.reason)
        self.add_item(self.duration)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            # Get the original embed
            if not interaction.message.embeds:
                await interaction.response.send_message("Error: Could not find the original action information.", ephemeral=True)
                return
                
            original_embed = interaction.message.embeds[0]
            
            # Find action time field
            action_time_field = next((f for f in original_embed.fields if f.name == "⏳ Action Time"), None)
            if not action_time_field:
                await interaction.response.send_message("Error: Could not find action time in the original message.", ephemeral=True)
                return

 
            action_time_utc = datetime.now(timezone.utc)

            # Get form inputs
            duration_str = self.duration.value.strip().lower()
            reason = self.reason.value or "No reason provided"

            # Parse duration
            try:
                seconds = self.parse_duration(duration_str)
                if seconds <= 0:
                    await interaction.response.send_message("Error: Duration must be greater than 0.", ephemeral=True)
                    return
            except ValueError as e:
                await interaction.response.send_message(f"Error: {str(e)}", ephemeral=True)
                return

            # Calculate unmute time in UTC
            unmute_time_utc = action_time_utc + timedelta(seconds=seconds)

            # Save to DB (storing UTC timestamp)
            try:
                with sqlite3.connect(MUTE_DB) as conn:
                    conn.execute(
                        "INSERT OR REPLACE INTO mute_data VALUES (?, ?, ?)",
                        (str(self.user_id), str(self.guild_id), int(unmute_time_utc.timestamp()))
                    )
                    conn.commit()
            except sqlite3.Error as e:
                await interaction.response.send_message("Error: Failed to save mute data to database.", ephemeral=True)
                return

            # Prepare simple duration text without "Until" time
            duration_text = f"{self.duration.value}"

            ts = int(datetime.now(timezone.utc).timestamp())
            action_time_str = f"<t:{ts}:F>"

            # Create response embed
            embed = discord.Embed(
                title="🔹 Mute Updated",
                color=discord.Color.blue()
            )
            embed.add_field(name="⏳ Action Time", value=action_time_str, inline=False)
            embed.add_field(name="👨‍👩‍👦‍👦 Member", value=f"<@{self.user_id}>", inline=True)
            embed.add_field(name="👤 Moderator", value=interaction.user.mention, inline=True)
            embed.add_field(name="📌 Action ID", value=f"`{self.action_id}`", inline=False)
            embed.add_field(name="📝 Reason", value=reason, inline=False)
            embed.add_field(name="📆 Duration", value=duration_text, inline=False)

            await interaction.response.edit_message(embed=embed)
            await update_user_dm(interaction.guild, self.action_id, duration_text, reason)

        except Exception as e:
            print(f"Error in mute form: {repr(e)}")
            await interaction.response.send_message("⚠️ Failed to update mute!", ephemeral=True)

    def parse_duration(self, duration_str: str) -> int:
        """Parse duration string (e.g., 1h30m) into seconds"""
        if not duration_str:
            raise ValueError("Duration cannot be empty")
            
        seconds = 0
        remaining = duration_str
        
        # Parse days
        if 'd' in remaining:
            parts = remaining.split('d', 1)
            try:
                days = int(parts[0])
                if days < 0:
                    raise ValueError("Days cannot be negative")
                seconds += days * 86400
                remaining = parts[1]
            except (ValueError, IndexError):
                raise ValueError("Invalid days format in duration")
        
        # Parse hours
        if 'h' in remaining:
            parts = remaining.split('h', 1)
            try:
                hours = int(parts[0])
                if hours < 0:
                    raise ValueError("Hours cannot be negative")
                seconds += hours * 3600
                remaining = parts[1]
            except (ValueError, IndexError):
                raise ValueError("Invalid hours format in duration")
        
        # Parse minutes
        if 'm' in remaining:
            parts = remaining.split('m', 1)
            try:
                minutes = int(parts[0])
                if minutes < 0:
                    raise ValueError("Minutes cannot be negative")
                seconds += minutes * 60
                remaining = parts[1]
            except (ValueError, IndexError):
                raise ValueError("Invalid minutes format in duration")
        
        # Check if there's any remaining invalid characters
        if remaining.strip():
            raise ValueError(f"Invalid duration format: '{duration_str}'")
        
        return seconds
        

class ActionHistoryView(discord.ui.View):
    def __init__(self, user_actions):
        super().__init__(timeout=60)
        self.add_item(ActionDropdown(user_actions))

class ActionDropdown(discord.ui.Select):
    def __init__(self, user_actions):
        options = [
            discord.SelectOption(
                label=f"{action['action']} ({action['date'].split()[0]})",
                description=action['reason'][:50],
                value=str(idx)
            ) for idx, action in enumerate(user_actions[:10])
        ]
        super().__init__(placeholder="🔍 View other actions...", options=options)

    async def callback(self, interaction):
        selected_action = interaction.client.user_actions_cache[int(self.values[0])]
        embed = create_action_embed(selected_action)
        await interaction.response.edit_message(embed=embed)


    
    
class PremiumStatsView(View):
    def __init__(self, bot, guild):
        super().__init__(timeout=300)
        self.bot, self.guild, self.duration, self.current_page, self.total_pages = bot, guild, "7D", 0, 5
        self.db = sqlite3.connect(ACTION_LOGS_DB)
        self.db.row_factory = sqlite3.Row
        self.setup_ui()
        self.stats = None
    
    def setup_ui(self):
        self.clear_items()
        self.add_item(Button(emoji="◀️", style=discord.ButtonStyle.blurple, row=0, custom_id="prev"))
        self.add_item(Button(label=f"Page {self.current_page+1}/{self.total_pages}", style=discord.ButtonStyle.gray, disabled=True, row=0))
        self.add_item(Button(emoji="▶️", style=discord.ButtonStyle.blurple, row=0, custom_id="next"))
        self.add_item(Button(emoji="🔄", style=discord.ButtonStyle.grey, row=0, custom_id="refresh"))
        self.add_item(Button(emoji="📊", style=discord.ButtonStyle.green, row=1, custom_id="stats"))
        self.add_item(Button(emoji="👮", style=discord.ButtonStyle.blurple, row=1, custom_id="mods"))
        self.add_item(Button(emoji="💀", style=discord.ButtonStyle.blurple, row=1, custom_id="offenders"))
        self.add_item(Select(placeholder="⌛ Time Range", options=[
            discord.SelectOption(label="24 Hours", value="1D"),
            discord.SelectOption(label="7 Days", value="7D"),
            discord.SelectOption(label="30 Days", value="30D"),
            discord.SelectOption(label="All Time", value="all")], row=2, custom_id="duration"))

    def get_cutoff(self):
        durations = {"1D": timedelta(days=1), "7D": timedelta(days=7), "30D": timedelta(days=30), "all": timedelta(days=3650)}
        return (datetime.now(timezone.utc) - durations[self.duration]).strftime("%Y-%m-%d %H:%M:%S")

    async def collect_stats(self):
        try:
            cursor = self.db.cursor()
            if not cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='moderation_logs'").fetchone():
                return None
                
            logs = cursor.execute("""
                SELECT action, moderator, username, time, duration, reason, proof 
                FROM moderation_logs WHERE guild_id=? AND datetime(time) >= datetime(?)""", 
                (str(self.guild.id), self.get_cutoff())).fetchall()
            if not logs: return None

            stats = {
                "total": len(logs), "actions": defaultdict(int), "mods": defaultdict(int), "users": defaultdict(int),
                "hours": defaultdict(int), "days": defaultdict(int), "reasons": defaultdict(int), "durations": defaultdict(int),
                "proofs": 0, "mod_actions": defaultdict(lambda: defaultdict(int)), "user_actions": defaultdict(lambda: defaultdict(int)),
                "action_times": []
            }
            
            for log in logs:
                try:
                    action = log['action'].lower()
                    mod, user = log['moderator'], log['username']
                    dt = datetime.strptime(log['time'], "%Y-%m-%d %H:%M:%S")
                    stats["actions"][action] += 1
                    stats["mods"][mod] += 1
                    stats["users"][user] += 1
                    stats["hours"][dt.hour] += 1
                    stats["days"][dt.weekday()] += 1
                    stats["mod_actions"][mod][action] += 1
                    stats["user_actions"][user][action] += 1
                    stats["action_times"].append(dt)
                    if log['proof']: stats["proofs"] += 1
                    if log['reason']: stats["reasons"][log['reason']] += 1
                    if log['duration'] and log['duration'] != "N/A": stats["durations"][log['duration']] += 1
                except: continue

            stats["top_mods"] = sorted(stats["mods"].items(), key=lambda x: -x[1])
            stats["top_users"] = sorted(stats["users"].items(), key=lambda x: -x[1])
            stats["top_actions"] = sorted(stats["actions"].items(), key=lambda x: -x[1])
            stats["top_reasons"] = sorted(stats["reasons"].items(), key=lambda x: -x[1])
            return stats
        except: return None
        finally: cursor.close()

    def create_action_wheel(self, data):
        if not data: return "No data available"
        total = sum(data.values())
        if total == 0: return "No actions recorded"
        segments = [
            ("Timeout", data.get('timeout', 0), "⏱"),
            ("Mute", data.get('server mute', 0), "▶"),
            ("Unmute",data.get('server unmute',0),"="),
            ("Kick", data.get('kick', 0), "✈"),
            ("Ban", data.get('ban', 0), "⚠")
            
             ]
        wheel = []
        max_segment = max(segments, key=lambda x: x[1])[1]
        for name, count, emoji in segments:
            if count == 0: continue
            percentage = (count / total) * 100
            bar = '❙' * math.ceil((count / max_segment) * 5)
            wheel.append(f"{emoji} {name[:7]:<7}  {bar} {count:>3}   ({percentage:.1f}%)")
        return "```\n" + "\n".join(wheel) + "\n```"

    def create_pie_chart(self, data, title):
        if not data or len(data) == 0: return "No data available"
        try:
            total = sum(v for _, v in data)
            if total == 0: return "No data available"
            slices = []
            current_angle = 0
            for label, value in data[:5]:
                angle = 360 * (value / total)
                slices.append((label, current_angle, current_angle + angle))
                current_angle += angle
            pie = [f"▸ {label[:15]:<15} {(end-start)/360*100:.1f}%" for label, start, end in slices]
            return f"**{title}**\n```\n" + "\n".join(pie) + "\n```"
        except: return "Error generating chart"

    def format_moderator_entry(self, idx, mod, actions):
        total = sum(actions.values())
        return (f"{idx+1}. {mod[:15]:<15}\n"
                f"⚖ {total:<3} | ⏱ {actions.get('timeout',0):<2} | ▶ {actions.get('server mute',0):<2} | "
                f"✈ {actions.get('kick',0):<2} | ⚠ {actions.get('ban',0):<2}")

    def format_offender_entry(self, idx, user, actions):
        total = sum(actions.values())
        return (f"{idx+1}. {user[:15]:<15}\n"
                f"⚖ {total:<3} | ⏱ {actions.get('timeout',0):<2} | ▶ {actions.get('server mute',0):<2} | "
                f"✈ {actions.get('kick',0):<2} | ⚠ {actions.get('ban',0):<2}")

    def format_top_moderator_entry(self, idx, mod, count, actions):
        return (f"{idx+1}. {mod[:30]:<30}{count:<7}\n"
                f"[⏱ {actions.get('timeout',0):<2} ▶{actions.get('server mute',0):<2} "
                f"✈ {actions.get('kick',0):<2} ⚠ {actions.get('ban',0):<2}]")

    def format_top_offender_entry(self, idx, user, count, actions):
        return (f"{idx+1}. {user[:30]:<30}{count:<7}\n"
                f"[⏱ {actions.get('timeout',0):<2} ▶{actions.get('server mute',0):<2} "
                f"✈ {actions.get('kick',0):<2} ⚠ {actions.get('ban',0):<2}]")

    async def generate_embeds(self):
        self.stats = await self.collect_stats()
        if not self.stats:
            return [discord.Embed(title="📊 No Data Available", 
                                description=f"No moderation actions found for {self.duration}", 
                                color=0xff0000)]

        embeds = []
        embed = discord.Embed(title=f"📊 {self.guild.name} Stats Overview", 
                            description=f"⏳ Time range: **{self.duration}**", 
                            color=0x5865F2)
        
        top_mods_text = [self.format_moderator_entry(idx, mod, self.stats["mod_actions"].get(mod, {})) 
                        for idx, (mod, _) in enumerate(self.stats["top_mods"][:3])]
        mods_value = '\n\n'.join(top_mods_text) if top_mods_text else 'No moderators found'
        embed.add_field(name="👮 Top 3 Moderators", value=f"```\n{mods_value}\n```", inline=True)
        
        top_offenders_text = [self.format_offender_entry(idx, user, self.stats["user_actions"].get(user, {})) 
                            for idx, (user, _) in enumerate(self.stats["top_users"][:3])]
        offenders_value = '\n\n'.join(top_offenders_text) if top_offenders_text else 'No offenders found'
        embed.add_field(name="💀 Top 3 Offenders", value=f"```\n{offenders_value}\n```", inline=True)
        
        action_counts = {a: self.stats['actions'].get(a, 0) for a in ['timeout', 'server mute', 'kick', 'ban','server unmute']}
        embed.add_field(name="🎡 Action Distribution Wheel", value=self.create_action_wheel(action_counts), inline=False)
        
        busiest_hour = max(self.stats["hours"].items(), key=lambda x: x[1], default=(None,0))[0]
        busiest_day = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'][max(self.stats["days"].items(), key=lambda x: x[1], default=(0,0))[0]]
        embed.add_field(name="📈 Activity Stats", value=f"```\n🕒 Peak Hour: {busiest_hour or '?'}:00\n📅 Busy Day: {busiest_day}\n⏱ Avg/Day: {self.stats['total']/{'1D':1,'7D':7,'30D':30,'all':30}[self.duration]:.1f}\n```", inline=False)
        embeds.append(embed)
        
        embed = discord.Embed(title="👮 Moderator Rankings", color=0x3498db)
        if self.stats["top_mods"]:
            mods_text = [self.format_top_moderator_entry(idx, mod, count, self.stats["mod_actions"].get(mod, {})) 
                        for idx, (mod, count) in enumerate(self.stats["top_mods"][:10])]
            embed.add_field(name="🏅 Top 10 Moderators", value=f"```\n{'Rank Moderator':<21} {'Total Actions':<13}\n" + "\n".join(mods_text) + "\n```", inline=False)
            
            specialties = [(mod, *max(actions.items(), key=lambda x: x[1], default=("None", 0))) 
                         for mod, actions in self.stats["mod_actions"].items() if actions]
            if specialties:
                spec_text = "\n".join(f"{mod[:18]:<18}{action[:15]:<15}{count}" for mod, action, count in sorted(specialties, key=lambda x: -x[2])[:5])
                embed.add_field(name="🔧 Top Specialties", value=f"```\n{'Moderator':<18}{'Action':<15}Count\n{spec_text}\n```", inline=False)
        embeds.append(embed)
        
        embed = discord.Embed(title="💀 Top Offenders", color=0xe74c3c)
        offenders = [(user, sum(actions.values()), actions) for user, actions in self.stats["user_actions"].items() if actions]
        if offenders:
            off_text = [self.format_top_offender_entry(idx, user, total, actions) 
                      for idx, (user, total, actions) in enumerate(sorted(offenders, key=lambda x: -x[1])[:10])]
            embed.add_field(name="⚠️ Worst Offenders", value=f"```\n{'Rank User':<21} {'Total Actions':<7}\n" + "\n".join(off_text) + "\n```", inline=False)
        embeds.append(embed)
        
        embed = discord.Embed(title="📈 Time Patterns", color=0x9b59b6)
        if self.stats["hours"]:
            embed.add_field(name="🕒 Hourly Activity", value=self.create_pie_chart(sorted(self.stats["hours"].items()), "By Hour"), inline=False)
        if self.stats["days"]:
            days = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]
            embed.add_field(name="📅 Weekly Pattern", value=self.create_pie_chart([(days[d], c) for d, c in sorted(self.stats["days"].items())], "By Day"), inline=False)
        embeds.append(embed)
        
        embed = discord.Embed(title="🔍 Advanced Statistics", color=0x1abc9c)
        if self.stats["top_reasons"]:
            embed.add_field(name="📝 Common Reasons", value=self.create_pie_chart(self.stats["top_reasons"][:5], "Top Reasons"), inline=False)
        if self.stats["durations"]:
            durations_text = "\n".join(f"▸ {duration[:20]:<20} {count}" for duration, count in sorted(self.stats["durations"].items(), key=lambda x: -x[1])[:5])
            embed.add_field(name="⏱ Action Durations", value=f"```\n{durations_text}\n```", inline=False)
        embeds.append(embed)
        return embeds

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        custom_id = interaction.data.get("custom_id")
        if not custom_id: return False

        if custom_id == "prev": self.current_page = max(0, self.current_page - 1)
        elif custom_id == "next": self.current_page = min(self.total_pages - 1, self.current_page + 1)
        elif custom_id == "stats": self.current_page = 0
        elif custom_id == "mods": self.current_page = 1
        elif custom_id == "offenders": self.current_page = 2
        elif custom_id == "refresh":
            await interaction.response.defer()
            embeds = await self.generate_embeds()
            await interaction.edit_original_response(embed=embeds[self.current_page], view=self)
            return False
        elif custom_id == "duration":
            self.duration = interaction.data["values"][0]
            await interaction.response.defer()
            embeds = await self.generate_embeds()
            await interaction.edit_original_response(embed=embeds[self.current_page], view=self)
            return False

        for item in self.children:
            if isinstance(item, Button) and item.label and "Page" in item.label:
                item.label = f"Page {self.current_page+1}/{self.total_pages}"
                break

        embeds = await self.generate_embeds()
        await interaction.response.edit_message(embed=embeds[self.current_page], view=self)
        return False
        
class MuteButtonView(View):
    def __init__(self, action_id, user_id, guild_id):
        super().__init__()
        self.action_id = action_id
        self.user_id = user_id
        self.guild_id = guild_id

    @discord.ui.button(label="Update Mute", style=discord.ButtonStyle.primary)
    async def update_mute(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(MuteForm(self.action_id, self.user_id, self.guild_id))


class RolePagination(ui.View):
    def __init__(self, embeds):
        super().__init__(timeout=180)
        self.embeds = embeds
        
    @ui.button(emoji="⬅️", style=discord.ButtonStyle.blurple)
    async def prev(self, interaction: discord.Interaction, _):
        if self.current_page > 0:
            self.current_page -= 1
            await interaction.response.edit_message(embed=self.embeds[self.current_page])
        
    @ui.button(emoji="➡️", style=discord.ButtonStyle.blurple)
    async def next(self, interaction: discord.Interaction, _):
        if self.current_page < len(self.embeds)-1:
            self.current_page += 1
            await interaction.response.edit_message(embed=self.embeds[self.current_page])


class AppealButtonView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)  # keep forever

    @discord.ui.button(
        label="Appeal",
        style=discord.ButtonStyle.success,
        emoji="⚖️",
        custom_id="appeal_button"
    )
    async def appeal(self, interaction: discord.Interaction, button: discord.ui.Button):

        user_id = interaction.user.id
        now = time.time()

        # ⏱️ Cooldown check
        if user_id in APPEAL_COOLDOWN:
            remaining = int(APPEAL_COOLDOWN[user_id] - now)
            if remaining > 0:
                return await interaction.response.send_message(
                    f"⏳ Please wait **{remaining}s** before using Appeal again.",
                    ephemeral=True
                )

        # Set cooldown
        APPEAL_COOLDOWN[user_id] = now + COOLDOWN_SECONDS

        # Disable button permanently for this message
        button.disabled = True
        button.label = "Appeal Submitted"
        button.style = discord.ButtonStyle.gray

        try:
            # ❗ DO NOT defer here
            # handle_appeal() will handle defer + followups
            await handle_appeal(interaction)

        except Exception:
            logger.exception("Appeal failed")
            try:
                await interaction.followup.send(
                    "❌ Failed to create appeal channel.",
                    ephemeral=True
                )
            except Exception:
                pass

        finally:
            # 🔒 Update DM message so button stays disabled
            try:
                await interaction.message.edit(view=self)
            except Exception:
                pass

def extract_action_id(channel_name: str):
    # appeal-100521-username OR appeal-100521-username-closed
    parts = channel_name.split("-")
    for p in parts:
        if p.isdigit():
            return p
    return None

def get_action_user_id(guild_id: str, action_id: str):
    conn = sqlite3.connect(ACTION_LOGS_DB)
    cur = conn.cursor()

    cur.execute("""
        SELECT user_id FROM moderation_logs
        WHERE guild_id=? AND action_id=?
        LIMIT 1
    """, (str(guild_id), action_id))

    row = cur.fetchone()
    conn.close()

    return int(row[0]) if row else None

class AppealCloseConfirmView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)  # ⏱️ 3 minutes
        self.message = None

    async def on_timeout(self):
        try:
            if self.message:
                await self.message.delete()
        except Exception:
            pass

    @discord.ui.button(label="Close", style=discord.ButtonStyle.red, custom_id="appeal_confirm_close")
    async def confirm(self, interaction: discord.Interaction, _):
        # ✅ ACK FIRST
        await interaction.response.defer()

        channel = interaction.channel
        guild = interaction.guild

        action_id = extract_action_id(channel.name)
        user_id = get_action_user_id(str(guild.id), action_id)
        user = guild.get_member(user_id) if user_id else None

        # 1️⃣ DELETE CONFIRMATION FIRST (FIXES GHOST MESSAGE)
        try:
            if self.message:
                await self.message.delete()
        except Exception:
            pass

        # 2️⃣ REMOVE OFFENDER ACCESS
        if user:
            await channel.set_permissions(user, view_channel=False)

        # 3️⃣ RENAME CHANNEL
        if not channel.name.endswith("-closed"):
            await channel.edit(name=f"{channel.name}-closed")

        # 4️⃣ LOG MESSAGE
        await channel.send(
            embed=discord.Embed(
                description=f"🔒 Appeal closed by {interaction.user.mention}",
                color=discord.Color.red()
            )
        )

        # 5️⃣ SEND MOD CONTROLS
        await channel.send(
            embed=discord.Embed(
                title="Appeal Control (Moderators)",
                description="Use the buttons below to manage this appeal.",
                color=discord.Color.blurple()
            ),
            view=AppealModControlView()
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.gray, custom_id="appeal_confirm_cancel")
    async def cancel(self, interaction: discord.Interaction, _):
        # ✅ ACK FIRST
        await interaction.response.defer()

        # Cancel = delete confirmation only
        try:
            if self.message:
                await self.message.delete()
        except Exception:
            pass


class AppealModControlView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.locked = False

    def lock(self):
        for item in self.children:
            item.disabled = True

    @discord.ui.button(label="Open", style=discord.ButtonStyle.success, custom_id="appeal_mod_open")
    async def open(self, interaction: discord.Interaction, _):
        # ✅ ACK FIRST
        await interaction.response.defer()

        if self.locked:
            return
        if not interaction.user.guild_permissions.moderate_members:
            return

        self.locked = True
        self.lock()
        try:
            await interaction.message.edit(view=self)
        except Exception:
            pass

        channel = interaction.channel
        guild = interaction.guild

        action_id = extract_action_id(channel.name)
        user_id = get_action_user_id(str(guild.id), action_id)
        user = guild.get_member(user_id) if user_id else None

        if channel.name.endswith("-closed"):
            await channel.edit(name=channel.name.replace("-closed", ""))

        if user:
            await channel.set_permissions(user, view_channel=True)

        await channel.send(
            embed=discord.Embed(
                description=f"🟢 Appeal reopened by {interaction.user.mention}",
                color=discord.Color.green()
            )
        )

        # Delete mod control message
        try:
            await interaction.message.delete()
        except Exception:
            pass

    @discord.ui.button(label="Delete", style=discord.ButtonStyle.danger, custom_id="appeal_mod_delete")
    async def delete(self, interaction: discord.Interaction, _):
        # ✅ ACK FIRST
        await interaction.response.defer()

        if self.locked:
            return
        if not interaction.user.guild_permissions.moderate_members:
            return

        self.locked = True
        self.lock()
        try:
            await interaction.message.edit(view=self)
        except Exception:
            pass

        await interaction.channel.delete()

class AppealCloseView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Close", style=discord.ButtonStyle.red, custom_id="appeal_close_button")
    async def close(self, interaction: discord.Interaction, _):
        view = AppealCloseConfirmView()

        # Send NON-ephemeral confirmation message
        await interaction.response.send_message(
            "Are you sure you want to close this appeal?",
            view=view
        )

        # 👇 VERY IMPORTANT: store the message inside the view
        view.message = await interaction.original_response()


class ProofButton(discord.ui.Button):
    def __init__(self, proofs):
        super().__init__(
            label="📁 View Proofs",
            style=discord.ButtonStyle.secondary
        )
        self.proofs = proofs

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        viewer = ProofViewer(self.proofs)
        await viewer.load_current_proof(interaction)

class ProofViewer(discord.ui.View):
    def __init__(self, proofs):
        super().__init__(timeout=300)
        self.proofs = proofs
        self.index = 0
        self.message = None
        self.image_urls = []

        self.prev.disabled = True
        self.next.disabled = len(proofs) <= 1

    async def load_current_proof(self, interaction: discord.Interaction):
        proof_url = self.proofs[self.index]

        # extract channel_id / message_id from jump_url
        parts = proof_url.split("/")
        channel_id = int(parts[-2])
        message_id = int(parts[-1])

        channel = interaction.guild.get_channel(channel_id)
        if not channel:
            channel = await interaction.client.fetch_channel(channel_id)

        msg = await channel.fetch_message(message_id)

        # collect ALL attachments (image / video)
        self.image_urls = [
            att.url
            for att in msg.attachments
            if att.content_type and (
                att.content_type.startswith("image/")
                or att.content_type.startswith("video/")
            )
        ]

        embed = discord.Embed(
            title=f"📁 Proof {self.index + 1}/{len(self.proofs)}",
            color=0x3498db
        )

        if self.image_urls:
            embed.set_image(url=self.image_urls[0])
        else:
            embed.description = "⚠️ No preview available for this proof."

        embed.set_footer(text="Mods Vault • Inquiry")

        self.prev.disabled = self.index == 0
        self.next.disabled = self.index == len(self.proofs) - 1

        if self.message:
            await self.message.edit(embed=embed, view=self)
        else:
            self.message = await interaction.followup.send(
                embed=embed,
                view=self,
                ephemeral=False
            )

    @discord.ui.button(label="⬅️ Prev", style=discord.ButtonStyle.secondary)
    async def prev(self, interaction: discord.Interaction, _):
        await interaction.response.defer()
        self.index -= 1
        await self.load_current_proof(interaction)

    @discord.ui.button(label="➡️ Next", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, _):
        await interaction.response.defer()
        self.index += 1
        await self.load_current_proof(interaction)
#====================================================
# 🔹 BACKGROUND TASKS / LOOPS
#====================================================

@tasks.loop(seconds=2)
async def process_proof_queue():
    conn = sqlite3.connect(PROOF_QUEUE_DB)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("""
        SELECT * FROM proof_queue
        WHERE status='pending'
        ORDER BY created_at ASC
        LIMIT 1
    """)
    job = cur.fetchone()

    if not job:
        conn.close()
        return

    try:
        # Fetch original proof
        proof_channel = await bot.fetch_channel(int(job["channel_id"]))
        msg = await proof_channel.fetch_message(int(job["message_id"]))
        attachment = msg.attachments[job["attachment_index"]]
        data = await attachment.read()

        # Upload to storage server
        storage_channel = await get_next_storage_channel(bot)
        if not storage_channel:
            raise RuntimeError("No proof storage channel available")

        storage_msg = await storage_channel.send(
            file=discord.File(io.BytesIO(data), attachment.filename)
        )
        proof_link = storage_msg.jump_url

        # Save proof + get offender
        adb = sqlite3.connect(ACTION_LOGS_DB)
        ac = adb.cursor()
        ac.execute(
            "SELECT proof, user_id FROM moderation_logs WHERE action_id=?",
            (job["action_id"],)
        )
        row = ac.fetchone()
        proofs = row[0].split(",") if row and row[0] else []
        proofs.append(proof_link)
        proof_count = len(proofs)

        ac.execute(
            "UPDATE moderation_logs SET proof=? WHERE action_id=?",
            (",".join(proofs), job["action_id"])
        )
        adb.commit()
        adb.close()

        # DM offender (if enabled)
        cfg = get_server_config(job["guild_id"])
        if cfg and cfg.get("proof_dm_status") == "on" and row and row[1]:
            try:
                offender = await bot.fetch_user(int(row[1]))
                dm_file = discord.File(io.BytesIO(data), attachment.filename)

                embed = discord.Embed(
                    title="📎 New Proof Submitted",
                    description=(
                        f"**Server:** {proof_channel.guild.name}\n"
                        f"**Action ID:** `{job['action_id']}`\n\n"
                        f"If you believe this action is incorrect, you may appeal."
                    ),
                    color=discord.Color.orange()
                )
                embed.set_image(url=f"attachment://{attachment.filename}")
                embed.set_footer(text="Mods Vault • Proof System")

                await offender.send(embed=embed, file=dm_file)
            except Exception as e:
                logger.warning(
                    f"[PROOF OFFENDER DM FAILED] action={job['action_id']} err={e}"
                )

        # ✅ SUCCESS → notify + DELETE row
        await proof_channel.send(
            f"✅ **Proof processed successfully** for Action `{job['action_id']}` "
            f"(**{proof_count}/10 proofs submitted**).",
            delete_after=20
        )

        cur.execute("DELETE FROM proof_queue WHERE id=?", (job["id"],))
        conn.commit()

    except Exception as e:
        logger.exception(
            f"[PROOF QUEUE ERROR] action={job['action_id']} "
            f"msg={job['message_id']} idx={job['attachment_index']} err={e}"
        )

        retries = job["retry_count"] + 1

        if retries >= MAX_RETRIES:
            # ❌ FINAL FAIL → notify + DELETE row
            try:
                await proof_channel.send(
                    f"❌ **Failed to process a proof** for Action `{job['action_id']}`.\n"
                    f"Please re-submit the proof or contact moderators.",
                    delete_after=30
                )
            except:
                pass

            cur.execute("DELETE FROM proof_queue WHERE id=?", (job["id"],))

        else:
            # 🔁 Retry
            cur.execute(
                "UPDATE proof_queue SET retry_count=? WHERE id=?",
                (retries, job["id"])
            )

        conn.commit()

    conn.close()
    

@tasks.loop(seconds=30)
async def check_expired_mutes():
    try:
        now_ts = int(datetime.now(timezone.utc).timestamp())
        conn = sqlite3.connect(MUTE_DB)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT user_id, guild_id, unmute_time FROM mute_data WHERE unmute_time <= ?",
            (now_ts,)
        )
        rows = cursor.fetchall()
        for user_id, guild_id, unmute_ts in rows:
            guild = bot.get_guild(int(guild_id))
            if not guild:
                # guild gone → safe cleanup
                cursor.execute(
                    "DELETE FROM mute_data WHERE user_id=? AND guild_id=?",
                    (user_id, guild_id)
                )
                continue
            member = guild.get_member(int(user_id))
            if not member:
                # user left server → safe cleanup
                cursor.execute(
                    "DELETE FROM mute_data WHERE user_id=? AND guild_id=?",
                    (user_id, guild_id)
                )
                continue
# 🔴 IMPORTANT PART,User is not in VC→ DO NOT delete DB
            if not member.voice:
                continue
            # User is in VC → unmute
            try:
                if member.voice.mute:
                    await member.edit(mute=False)
            except (discord.Forbidden, discord.HTTPException):
                continue  # try again next loop
            # ✅ Only delete AFTER successful unmute
            cursor.execute(
                "DELETE FROM mute_data WHERE user_id=? AND guild_id=?",
                (user_id, guild_id)
            )
        conn.commit()
        conn.close()
    except Exception:
        pass


class TemporaryRoleManager:
    def __init__(self, bot):
        self.bot = bot

    async def cleanup_on_startup(self):
        conn = sqlite3.connect(TEMP_ROLE_DB)
        c = conn.cursor()
        now = datetime.now(timezone.utc).isoformat()
        c.execute("SELECT * FROM temporary_roles WHERE remove_time <= ?", (now,))
        for record in c.fetchall():
            guild_id, role_id, user_id, _ = record
            guild = self.bot.get_guild(guild_id)
            if guild:
                role, member = guild.get_role(role_id), guild.get_member(user_id)
                if role and member and role in member.roles:
                    await member.remove_roles(role)
            c.execute("DELETE FROM temporary_roles WHERE guild_id=? AND role_id=? AND user_id=?", (guild_id, role_id, user_id))
        conn.commit()
        conn.close()

    async def check_expired_roles(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            conn = sqlite3.connect(TEMP_ROLE_DB)
            c = conn.cursor()
            c.execute("SELECT * FROM temporary_roles ORDER BY remove_time ASC LIMIT 1")
            if next_role := c.fetchone():
                guild_id, role_id, user_id, remove_time = next_role
                if datetime.fromisoformat(remove_time) <= datetime.now(timezone.utc):
                    guild = self.bot.get_guild(guild_id)
                    if guild:
                        role, member = guild.get_role(role_id), guild.get_member(user_id)
                        if role and member and role in member.roles:
                            await member.remove_roles(role)
                    c.execute("DELETE FROM temporary_roles WHERE guild_id=? AND role_id=? AND user_id=?", (guild_id, role_id, user_id))
                    conn.commit()
                    continue
                else:
                    await asyncio.sleep(max(1, (datetime.fromisoformat(remove_time) - datetime.now(timezone.utc)).total_seconds()))
            else:
                await asyncio.sleep(30)
            conn.close()

    async def add_temporary_role(self, guild_id, role_id, user_id, duration):
        conn = sqlite3.connect(TEMP_ROLE_DB)
        c = conn.cursor()
        c.execute("INSERT OR REPLACE INTO temporary_roles VALUES (?, ?, ?, ?)",
                 (guild_id, role_id, user_id, (datetime.now(timezone.utc) + duration).isoformat()))
        conn.commit()
        conn.close()


#====================================================
# 🔹 EVENT HANDLERS
#====================================================
def ensure_appeal_used_column():
    conn = sqlite3.connect(ACTION_LOGS_DB)
    cur = conn.cursor()

    # Check existing columns
    cur.execute("PRAGMA table_info(moderation_logs)")
    columns = [row[1] for row in cur.fetchall()]

    if "appeal_used" not in columns:
        cur.execute(
            "ALTER TABLE moderation_logs ADD COLUMN appeal_used INTEGER"
        )
        conn.commit()
        print("✅ appeal_used column added to moderation_logs")
    else:
        print("ℹ️ appeal_used column already exists")

    conn.close()

def ensure_column_exists(db_path: str, table: str, column: str, column_def: str):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute(f"PRAGMA table_info({table})")
    columns = [row[1] for row in cur.fetchall()]

    if column not in columns:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_def}")
        conn.commit()
        logger.info(f"🧩 Added column '{column}' to {table}")

    conn.close()


@bot.event
async def on_ready():
    bot.add_view(AppealButtonView())
    bot.add_view(AppealCloseView())
    bot.add_view(AppealCloseConfirmView())
    bot.add_view(AppealModControlView())
    print("✅ Persistent appeal views registered")
    if not process_proof_queue.is_running():
    	process_proof_queue.start()
    
    ensure_appeal_used_column()
    ensure_column_exists(
        SERVER_CONFIG_DB,
        "server_config",
        "appeal_toggle",
        "TEXT DEFAULT 'off'"
    )

    ensure_column_exists(
        SERVER_CONFIG_DB,
        "server_config",
        "appeal_category_id",
        "INTEGER"
    )
    logger.info("🚀 Startup health check running...")
    

    # =========================
    # DATABASE HEALTH CHECKS
    cfg_ok, cfg_err = check_db_health(SERVER_CONFIG_DB, "server_config")
    log_ok, log_err = check_db_health(ACTION_LOGS_DB, "moderation_logs")
    mute_ok, mute_err = check_db_health(MUTE_DB, "mute_data")

    cfg_count = 0
    if cfg_ok:
        try:
            with sqlite3.connect(SERVER_CONFIG_DB) as conn:
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) FROM server_config")
                cfg_count = cur.fetchone()[0]
        except Exception as e:
            logger.error(f"❌ Failed to read config count: {e}")

    # =========================
    # INTENT CHECK
    missing_intents = check_intents(bot)

    # =========================
    # HEALTH SUMMARY
    logger.info("🩺 ===== STARTUP HEALTH =====")
    logger.info(f"🤖 Bot User        : {bot.user}")
    logger.info(f"🏠 Guilds Joined   : {len(bot.guilds)}")
    logger.info(f"🗂 Config Entries  : {cfg_count}")
    logger.info(f"📦 Config DB      : {'OK' if cfg_ok else 'FAIL'}")
    logger.info(f"📦 Logs DB        : {'OK' if log_ok else 'FAIL'}")
    logger.info(f"📦 Mute DB        : {'OK' if mute_ok else 'FAIL'}")

    if not cfg_ok:
        logger.error(f"❌ Config DB error: {cfg_err}")
    if not log_ok:
        logger.error(f"❌ Logs DB error: {log_err}")
    if not mute_ok:
        logger.error(f"❌ Mute DB error: {mute_err}")

    if missing_intents:
        logger.warning(f"⚠ Missing intents: {', '.join(missing_intents)}")
    else:
        logger.info("✅ All required intents enabled")

    logger.info("🩺 ===== HEALTH CHECK DONE =====")

    # =========================
    # BACKGROUND TASKS (SAFE)
    try:
        manager = TemporaryRoleManager(bot)
        await manager.cleanup_on_startup()
        bot.loop.create_task(manager.check_expired_roles())
    except Exception as e:
        logger.error(f"❌ TemporaryRoleManager startup failed: {e}")

    try:
        if not check_expired_mutes.is_running():
            check_expired_mutes.start()
    except Exception as e:
        logger.error(f"❌ Mute loop failed to start: {e}")

    # =========================
    # COGS
    try:
        await bot.add_cog(PremiumStats(bot))
    except Exception as e:
        logger.error(f"❌ Failed to load PremiumStats: {e}")

    # =========================
    # SLASH COMMAND SYNC (LAST)
    try:
        await bot.tree.sync()
        logger.info(f"✅ Logged in as {bot.user}")
    except Exception as e:
        logger.error(f"❌ Failed to sync commands: {e}")



@bot.event
async def on_guild_remove(guild: discord.Guild):
    guild_id = str(guild.id)

    print(f"[CLEANUP] Bot removed from guild {guild_id}, starting cleanup")

    cleanup_server_config(guild_id)
    cleanup_mute_data(guild_id)
    cleanup_temp_roles(guild_id)

    print(f"[CLEANUP] Finished cleanup for guild {guild_id}")
    
@bot.event
async def on_member_remove(member: discord.Member):

    # 🚫 If the removed member is the bot itself, ignore completely
    if member.id == bot.user.id:
        return

    # 🚫 Extra safety: guild may be unavailable
    if not member.guild:
        return

    try:
        async for log in member.guild.audit_logs(
            limit=1,
            action=discord.AuditLogAction.kick
        ):
            if log.target and log.target.id == member.id:
                await log_moderation_action(
                    member.guild,
                    member,
                    "Kick",
                    "N/A",
                    log.reason or "No reason provided",
                    log.user
                )
                break

    except (discord.Forbidden, discord.NotFound, discord.HTTPException):
        # Missing perms, guild gone, audit lag — silently ignore
        return
        

@bot.event
async def on_voice_state_update(member, before, after):
    # -------- 1. LOGGING (do NOT return) --------
    if before.mute != after.mute:
        action = "Server Mute" if after.mute else "Server Unmute"
        async for log in member.guild.audit_logs(
            limit=5,
            action=discord.AuditLogAction.member_update
        ):
            if log.target.id == member.id and "mute" in str(log.changes).lower():
                await log_moderation_action(
                    member.guild,
                    member,
                    action,
                    "N/A",
                    log.reason or "No reason provided",
                    log.user
                )
                break  # NOT return

    # -------- 2. UNMUTE CHECK (always runs) --------
    if not member.voice:
        return  # user not in VC, nothing to do

    conn = None
    try:
        conn = sqlite3.connect(MUTE_DB)
        cursor = conn.cursor()

        cursor.execute(
            "SELECT unmute_time FROM mute_data WHERE user_id=? AND guild_id=?",
            (str(member.id), str(member.guild.id))
        )
        row = cursor.fetchone()

        if not row:
            return

        unmute_timestamp = int(row[0])
        now_ts = int(datetime.now(timezone.utc).timestamp())

        if now_ts >= unmute_timestamp:
            await member.edit(mute=False)

            cursor.execute(
                "DELETE FROM mute_data WHERE user_id=? AND guild_id=?",
                (str(member.id), str(member.guild.id))
            )
            conn.commit()

    except Exception:
        pass
    finally:
        if conn:
            conn.close()

@bot.event
async def on_member_ban(guild, member):
    async for log in guild.audit_logs(limit=1, action=discord.AuditLogAction.ban):
        if log.target.id == member.id:
            await log_moderation_action(guild, member, "Ban", "N/A", log.reason or "No reason provided", log.user)
            break
            
            
@bot.event
async def on_disconnect():
    print("⚠️ Discord disconnected (network issue)")

@bot.event
async def on_resumed():
    print("✅ Discord reconnected")
    
    
@bot.event
async def on_message(message):
    if message.reference and message.guild:
        await handle_proof_reply(message)
    await bot.process_commands(message)






@bot.event
async def on_member_update(before, after):
    if before.timed_out_until != after.timed_out_until:
        async for log in after.guild.audit_logs(limit=1, action=discord.AuditLogAction.member_update):
            if log.target.id == after.id:
                action = "Timeout" if after.timed_out_until else "Remove Timeout"
                duration = "Unknown"

                if after.timed_out_until:
                    
                    duration_seconds = (after.timed_out_until - datetime.now(timezone.utc)).total_seconds()
                    duration = format_duration(duration_seconds)

                await log_moderation_action(after.guild, after, action, duration, log.reason or "No reason provided", log.user)
                break


@bot.event
async def on_guild_join(guild: discord.Guild):
    try:
        # ---------- Find who invited the bot ----------
        inviter = None
        try:
            async for entry in guild.audit_logs(
                limit=5,
                action=discord.AuditLogAction.bot_add
            ):
                if entry.target.id == bot.user.id:
                    inviter = entry.user
                    break
        except Exception:
            pass

        # ---------- Create admin-only channel ----------
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            guild.me: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True
            )
        }

        # Allow all Administrator roles
        for role in guild.roles:
            if role.permissions.administrator:
                overwrites[role] = discord.PermissionOverwrite(view_channel=True)

        channel = await guild.create_text_channel(
            name="🛡️│moderation-logs",
            overwrites=overwrites,
            reason="Auto setup: moderation logs & proof submission"
        )

        # ---------- Save default config ----------
        upsert_server_config(
            str(guild.id),
            log_channel_id=str(channel.id),
            action_dm_status="on",
            proof_dm_status="off",
            appeal_toggle="off"
        )

        # ---------- Auto-setup briefing ----------
        embed = discord.Embed(
            title="✅ Auto Setup Complete",
            color=0x2ECC71,
            description=(
                "This channel was created automatically and is **private**.\n"
                "Only members with **Administrator** permission can view it.\n\n"
                "Below is a quick guide to how the bot works 👇"
            )
        )

        embed.add_field(
            name="📌 Moderation Logs",
            value=(
                "• All moderation actions are logged in **this channel**\n"
                "• Each log has a unique **Action ID**\n"
                "• Logs help maintain transparency and moderation history\n"
            ),
            inline=False
        )

        embed.add_field(
            name="⚖️ Appeal System",
            value=(
                "• Users can submit an **appeal** for a moderation action\n"
                "• Appeals are linked using the **Action ID**\n"
                "• Each action can be appealed **only once**\n"
                "• Appeals create a private discussion channel for moderators\n"
            ),
            inline=False
        )

        embed.add_field(
            name="📎 Proof Submission",
            value=(
                "• To attach proof, **reply directly** to an action log\n"
                "• Supported formats: **Image, Video, Audio**\n"
                "• Proofs are linked to the corresponding action\n"
                "🔒 **Privacy Notice:** Submitted proofs are stored in a separate, restricted location. "
                "Access is limited and may be granted to authorized staff only when necessary.\n"
            ),
            inline=False
        )

        embed.add_field(
            name="🔍 Inquiry Command",
            value=(
                "Use `/inquiry` to review past actions:\n"
                "• Search by **Action ID**\n"
                "• Or search by **User** to see recent actions\n"
                "• Any linked proofs (if available) are shown there\n"
            ),
            inline=False
        )

        embed.add_field(
            name="⏳ Auto-Unmute System",
            value=(
                "• Temporary mutes are handled automatically\n"
                "• Use the **Update button** to set or adjust the unmute timer\n"
                "• Example formats: `1h`, `30m`, `2h 15m`\n"
            ),
            inline=False
        )

        embed.add_field(
            name="⚙️ Default Settings",
            value=(
                f"• Log / Proof Channel: {channel.mention}\n"
                "• Action DM: **ON**\n"
                "• Proof DM: **OFF**\n"
                "• Appeal: **OFF**\n\n"
                "You can change these anytime using:\n"
                "`/setup`"
            ),
            inline=False
        )

        embed.set_footer(
            text="No further setup required • Bot is ready to use"
        )

        # ---------- Send briefing ----------
        if inviter:
            await channel.send(content=inviter.mention, embed=embed)
        else:
            await channel.send(embed=embed)

    except Exception as e:
        print(f"[AUTO-SETUP ERROR] {e}")
        
#====================================================
# 🔹 SLASH COMMANDS
#====================================================

@bot.tree.command(name="setup", description="Configure moderation system")
#@app_commands.check(lambda i: i.user.guild_permissions.administrator)
@app_commands.describe(
    log_channel="Logs / proof channel (optional)",
    action_dm="Action DM toggle",
    proof_dm="Proof DM toggle",
    appeal="Appeal system toggle"
)
@app_commands.choices(
    action_dm=[
        app_commands.Choice(name="ON", value="on"),
        app_commands.Choice(name="OFF", value="off")
    ],
    proof_dm=[
        app_commands.Choice(name="ON", value="on"),
        app_commands.Choice(name="OFF", value="off")
    ],
    appeal=[
        app_commands.Choice(name="ON", value="on"),
        app_commands.Choice(name="OFF", value="off")
    ]
)
@app_commands.guild_only()
async def setup(
    interaction: discord.Interaction,
    log_channel: discord.TextChannel = None,
    action_dm: app_commands.Choice[str] = None,
    proof_dm: app_commands.Choice[str] = None,
    appeal: app_commands.Choice[str] = None
):
	


    # 2️⃣ Admin permission
    if not is_admin(interaction):
        await interaction.followup.send(
            "❌ You don't have permission to use this command.",
            ephemeral=True
        )
        return
    await interaction.response.defer(ephemeral=True)

    guild_id = str(interaction.guild.id)
    updates = {}

    if log_channel:
        updates["log_channel_id"] = log_channel.id

    if action_dm:
        updates["action_dm_status"] = action_dm.value

    if proof_dm:
        updates["proof_dm_status"] = proof_dm.value

    if appeal:
        updates["appeal_toggle"] = appeal.value

    if not updates:
        await interaction.followup.send(
            "⚠️ No changes provided.",
            ephemeral=True
        )
        return

    upsert_server_config(guild_id, **updates)

    # --------- UI EMBED ---------
    embed = discord.Embed(
        title="✅ Setup Updated",
        description="Server configuration has been updated successfully.",
        color=discord.Color.green()
    )

    if "log_channel_id" in updates:
        embed.add_field(
            name="📂 Logs Channel",
            value=f"<#{updates['log_channel_id']}>",
            inline=False
        )

    if "action_dm_status" in updates:
        embed.add_field(
            name="📩 Action DM",
            value=(
                "🟢 **ON**\nUser receives DM for moderation actions"
                if updates["action_dm_status"] == "on"
                else
                "🔴 **OFF**\nUser will NOT receive DM for actions"
            ),
            inline=False
        )

    if "proof_dm_status" in updates:
        embed.add_field(
            name="🧾 Proof DM",
            value=(
                "🟢 **ON**\nSubmitted proofs are sent to user DM"
                if updates["proof_dm_status"] == "on"
                else
                "🔴 **OFF**\nProofs will NOT be sent to user DM"
            ),
            inline=False
        )

    if "appeal_toggle" in updates:
        embed.add_field(
            name="📨 Appeal System",
            value=(
                "🟢 **ON**\nUsers can submit appeals"
                if updates["appeal_toggle"] == "on"
                else
                "🔴 **OFF**\nAppeals are disabled"
            ),
            inline=False
        )

    embed.set_footer(text="Use /view to check full configuration")

    await interaction.followup.send(embed=embed, ephemeral=True)

@bot.tree.command(name="view", description="View current logging and DM settings")
@app_commands.guild_only()

async def view_settings(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    # 2️⃣ Admin permission
    if not isinstance(interaction.user, discord.Member) or \
       not interaction.user.guild_permissions.administrator:
        await interaction.followup.send(
            "❌ You don't have permission to use this command.",
            ephemeral=True
        )
        return
    

    guild_id = str(interaction.guild.id)
    cfg = get_server_config(guild_id)

    if not cfg:
        await interaction.followup.send(
            "⚠️ Logging is not configured yet. Use `/setup` first.",
            ephemeral=True
        )
        return

    log_channel_id = cfg.get("log_channel_id")
    action_dm = cfg.get("action_dm_status", "off")
    proof_dm = cfg.get("proof_dm_status", "off")
    appeal_status = cfg.get("appeal_toggle", "off")

    log_channel = f"<#{log_channel_id}>" if log_channel_id else "Not set"

    embed = discord.Embed(
        title="⚙️ Server Configuration",
        description="Current moderation & logging settings for this server.",
        color=discord.Color.blurple()
    )

    embed.add_field(
        name="📂 Logs Channel",
        value=f"{log_channel}\n*Change using `/setup`*",
        inline=False
    )

    embed.add_field(
        name="📩 Action DM",
        value=(
            "🟢 **ON**\nUser receives DM for moderation actions"
            if action_dm == "on"
            else
            "🔴 **OFF**\nUser will NOT receive DM for actions"
        ),
        inline=True
    )

    embed.add_field(
        name="🧾 Proof DM",
        value=(
            "🟢 **ON**\nSubmitted proofs are sent to user DM"
            if proof_dm == "on"
            else
            "🔴 **OFF**\nProofs will NOT be sent to user DM"
        ),
        inline=True
    )

    embed.add_field(
        name="📨 Appeal System",
        value=(
            "🟢 **ON**\nUsers can submit appeals"
            if appeal_status == "on"
            else
            "🔴 **OFF**\nAppeals are disabled"
        ),
        inline=True
    )

    embed.add_field(
        name="ℹ️ To change any of these settings",
        value=(
            "• Use `/setup`"
        ),
        inline=False
    )

    await interaction.followup.send(embed=embed, ephemeral=True)





@bot.tree.command(name="inquiry", description="Fetch moderation details by Action ID or User")
@app_commands.describe(
    action_id="Search by Action ID",
    user="Search by User (shows recent actions)"
)
@app_commands.guild_only()
#@app_commands.check(has_timeout_permission)
async def inquiry(
    interaction: discord.Interaction,
    action_id: str = None,
    user: discord.Member = None
):

    if not is_moderator(interaction):
        await interaction.response.send_message(
            "❌ You need moderator permission.",
            ephemeral=True
        )
        return

    if action_id and user:
        return await interaction.response.send_message(
            "⚠️ Please use either Action ID or User, not both",
            ephemeral=True
        )

    if not action_id and not user:
        return await interaction.response.send_message(
            "⚠️ Please provide either Action ID or User",
            ephemeral=True
        )

    await interaction.response.defer()

    # ---------- helper: convert stored time -> discord timestamp ----------
    

        
    def format_time(ts_value):
        """
        Accepts:
        - int unix timestamp
        - numeric string unix timestamp
        - legacy 'YYYY-MM-DD HH:MM:SS' (UTC)
        Returns:
        - Discord timestamp string
        """
        try:
            if isinstance(ts_value, int):
                ts = ts_value
            elif isinstance(ts_value, str) and ts_value.isdigit():
                ts = int(ts_value)
            else:
                # legacy fallback
                dt = datetime.strptime(ts_value, "%Y-%m-%d %H:%M:%S")
                dt = dt.replace(tzinfo=timezone.utc)
                ts = int(dt.timestamp())

            return f"<t:{ts}:F>"
        except Exception:
            return "Unknown"

    try:
        # =====================================================
        # 🔍 SEARCH BY ACTION ID
        # =====================================================
        if action_id:
            logs = get_moderation_logs(interaction.guild.id, action_id)
            if not logs:
                return await interaction.followup.send(
                    f"⚠️ No record found for `{action_id}`"
                )

            log = dict(logs[0])
            proofs = log['proof'].split(',') if log.get('proof') else []

            embed = discord.Embed(
                title=f"🔍 Case: `{log['action_id']}`",
                color=0x3498db
            )

            embed.add_field(
                name="👤 User",
                value=f"<@{log['user_id']}>",
                inline=True
            )

            embed.add_field(
                name="🛡️ Moderator",
                value=f"<@{log['moderator_id']}>" if log.get("moderator_id") else log.get("moderator", "Unknown"),
                inline=True
            )

            embed.add_field(
                name="⏰ Time",
                value=format_time(log['time']),
                inline=False
            )

            embed.add_field(
                name="🚨 Action",
                value=log.get("action", "Unknown"),
                inline=True
            )

            embed.add_field(
                name="⏱️ Duration",
                value=log.get("duration", "N/A"),
                inline=True
            )

            embed.add_field(
                name="📝 Reason",
                value=log.get("reason", "Not specified"),
                inline=False
            )

            view = discord.ui.View()
            if proofs:
                view.add_item(ProofButton(proofs))

            await interaction.followup.send(embed=embed, view=view)

        # =====================================================
        # 👤 SEARCH BY USER
        # =====================================================
        elif user:
            all_logs = [dict(row) for row in get_moderation_logs(interaction.guild.id)]

            user_logs = [
                log for log in all_logs
                if log.get("user_id") == str(user.id)
            ]

            if not user_logs:
                return await interaction.followup.send(
                    f"⚠️ No records found for {user.mention}"
                )

            # sort by actual time (newest first)
            def sort_key(log):
                try:
                    if isinstance(log['time'], int):
                        return log['time']
                    if isinstance(log['time'], str) and log['time'].isdigit():
                        return int(log['time'])
                    dt = datetime.strptime(log['time'], "%Y-%m-%d %H:%M:%S")
                    return int(dt.replace(tzinfo=timezone.utc).timestamp())
                except Exception:
                    return 0

            user_logs = sorted(user_logs, key=sort_key, reverse=True)[:10]
            first_log = user_logs[0]
            proofs = first_log['proof'].split(',') if first_log.get('proof') else []

            embed = discord.Embed(
                title=f"🔍 Actions for {user.display_name}",
                description="Showing most recent action",
                color=0x3498db
            )

            embed.add_field(
                name="📌 Action ID",
                value=f"`{first_log['action_id']}`",
                inline=False
            )

            embed.add_field(
                name="👤 User",
                value=user.mention,
                inline=True
            )

            embed.add_field(
                name="🛡️ Moderator",
                value=f"<@{first_log['moderator_id']}>" if first_log.get("moderator_id") else first_log.get("moderator", "Unknown"),
                inline=True
            )

            embed.add_field(
                name="⏰ Time",
                value=format_time(first_log['time']),
                inline=False
            )

            embed.add_field(
                name="🚨 Action",
                value=first_log.get("action", "Unknown"),
                inline=True
            )

            embed.add_field(
                name="⏱️ Duration",
                value=first_log.get("duration", "N/A"),
                inline=True
            )

            embed.add_field(
                name="📝 Reason",
                value=first_log.get("reason", "Not specified"),
                inline=False
            )

            class ActionDropdown(discord.ui.Select):
                def __init__(self, logs):
                    options = [
                        discord.SelectOption(
                            label=f"{log['action']} • {format_time(log['time'])}",
                            value=str(idx),
                            description=(log.get("reason") or "")[:50]
                        )
                        for idx, log in enumerate(logs)
                    ]
                    super().__init__(
                        placeholder="Select another action…",
                        options=options
                    )
                    self.logs = logs

                async def callback(self, interaction: discord.Interaction):
                    selected = self.logs[int(self.values[0])]
                    proofs = selected['proof'].split(',') if selected.get('proof') else []

                    new_embed = discord.Embed(
                        title=f"🔍 Actions for {user.display_name}",
                        color=0x3498db
                    )

                    new_embed.add_field(
                        name="📌 Action ID",
                        value=f"`{selected['action_id']}`",
                        inline=False
                    )

                    new_embed.add_field(
                        name="👤 User",
                        value=f"<@{selected['user_id']}>",
                        inline=True
                    )

                    new_embed.add_field(
                        name="🛡️ Moderator",
                        value=f"<@{selected['moderator_id']}>" if selected.get("moderator_id") else selected.get("moderator", "Unknown"),
                        inline=True
                    )

                    new_embed.add_field(
                        name="⏰ Time",
                        value=format_time(selected['time']),
                        inline=False
                    )

                    new_embed.add_field(
                        name="🚨 Action",
                        value=selected.get("action", "Unknown"),
                        inline=True
                    )

                    new_embed.add_field(
                        name="⏱️ Duration",
                        value=selected.get("duration", "N/A"),
                        inline=True
                    )

                    new_embed.add_field(
                        name="📝 Reason",
                        value=selected.get("reason", "Not specified"),
                        inline=False
                    )

                    view = discord.ui.View()
                    view.add_item(self)
                    if proofs:
                        view.add_item(ProofButton(proofs))
                    await interaction.response.edit_message(
                        embed=new_embed,
                        view=view
                    )

            view = discord.ui.View()
            view.add_item(ActionDropdown(user_logs))
            if proofs:
                view.add_item(ProofButton(proofs))

            await interaction.followup.send(embed=embed, view=view)

    except Exception as e:
        await interaction.followup.send(
            "❌ An error occurred while fetching the data"
        )
        print(f"Inquiry Error: {e}\n{traceback.format_exc()}")


@bot.tree.command(name="top_offenders", description="Get the top offenders based on moderation actions.")
@app_commands.describe(
    count="Number of offenders to display (default: 5).",
    duration="Time range: 1D, 7D, 30D, or all (for all-time) (default: all-time)."
)
@app_commands.guild_only()
async def top_offenders(interaction: discord.Interaction, count: int = 5, duration: str = "all"):

    if count not in [5, 10, 20, 30]:
        await interaction.response.send_message("⚠️ Invalid count. Use 5, 10, 20, or 30.", ephemeral=True)
        return

    if duration not in ["1D", "7D", "30D", "all"]:
        await interaction.response.send_message("⚠️ Invalid duration. Use 1D, 7D, 30D, or all.", ephemeral=True)
        return

    await interaction.response.defer()

    offender_stats = defaultdict(lambda: {"timeouts": 0, "mutes": 0, "kicks": 0, "bans": 0})

    logs = get_moderation_logs(interaction.guild.id)
    for log in logs:
        if duration != "all" and not is_within_duration(log["time"], duration):
            continue

        action = log["action"]
        user_name = log["username"]

        if action == "Timeout":
            offender_stats[user_name]["timeouts"] += 1
        elif action == "Mute":
            offender_stats[user_name]["mutes"] += 1
        elif action == "Kick":
            offender_stats[user_name]["kicks"] += 1
        elif action == "Ban":
            offender_stats[user_name]["bans"] += 1

    sorted_offenders = sorted(
        offender_stats.items(),
        key=lambda x: (x[1]["timeouts"] + x[1]["mutes"] + x[1]["kicks"] + x[1]["bans"]),
        reverse=True
    )[:count]

    embed = discord.Embed(
        title=f"🔝 Top {count} Offenders ({duration})",
        description="Here are the users with the most moderation actions:",
        color=discord.Color.red()
    )

    for i, (user_name, stats) in enumerate(sorted_offenders, start=1):
        total_actions = stats["timeouts"] + stats["mutes"] + stats["kicks"] + stats["bans"]
        embed.add_field(
            name=f"#{i} {user_name}",
            value=f"⚖︎ {total_actions} Actions  | ⏱  {stats['timeouts']}  | ▶ {stats['mutes']}  | ✈︎  {stats['kicks']}  | ⚔︎ {stats['bans']}",
            inline=False
        )

    embed.set_footer(text="⚖︎ = Total Actions | ⏱ = Timeout | ▶ = Mute | ✈︎ = Kick | ⚔︎ = Ban.")
    await interaction.followup.send(embed=embed)





@bot.tree.command(name="add_role", description="Add role to user(s) with optional duration")
@app_commands.describe(role="Role to add", user="Single user", users="Multiple users", duration="Duration (1h30m)", reason="Audit log reason")
@app_commands.guild_only()
async def add_role(interaction: discord.Interaction, role: discord.Role, user: discord.Member = None, users: str = None, duration: str = None, reason: str = None):

    if not can_manage_roles(interaction):
        await interaction.response.send_message(
            "❌ You need moderator permission.",
            ephemeral=True
        )
        return
    if role >= interaction.user.top_role: return await interaction.response.send_message("❌ You don't have permission to manage this role.", ephemeral=True)
    if role >= interaction.guild.me.top_role: return await interaction.response.send_message("❌ Bot can't manage this role.", ephemeral=True)
    await interaction.response.defer()
    
    targets = [user] if user else await parse_users(interaction, users) if users else []
    if not targets: return await interaction.followup.send("⚠️ No valid users.", ephemeral=True)
    
    duration_delta = parse_duration(duration) if duration else None
    if duration and not duration_delta: return await interaction.followup.send("⚠️ Invalid duration format.", ephemeral=True)
    
    success, failed, exists = [], [], []
    for member in targets:
        #if role >= member.top_role: failed.append(f"{member.mention} (role too high)"); continue
        if role in member.roles: exists.append(member.mention); continue
        try:
            await member.add_roles(role, reason=reason)
            if duration_delta: await TemporaryRoleManager(bot).add_temporary_role(interaction.guild.id, role.id, member.id, duration_delta)
            success.append(member.mention)
        except: failed.append(f"{member.mention} (error)")
    
    embed = create_role_embed("add", role, success, exists, failed, duration=duration)
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="remove_role", description="Remove role from user(s)")
@app_commands.describe(role="Role to remove", user="Single user", users="Multiple users", reason="Audit log reason")
@app_commands.guild_only()
@app_commands.check(lambda i: i.user.guild_permissions.manage_roles)
async def remove_role(interaction: discord.Interaction, role: discord.Role, user: discord.Member = None, users: str = None, reason: str = None):
    if not interaction.guild: return await interaction.response.send_message("❌ Command only works in servers.", ephemeral=True)
    if role >= interaction.user.top_role: return await interaction.response.send_message("❌ Can't manage this role.", ephemeral=True)
    if role >= interaction.guild.me.top_role: return await interaction.response.send_message("❌ Bot can't manage this role.", ephemeral=True)
    await interaction.response.defer()
    
    targets = [user] if user else await parse_users(interaction, users) if users else []
    if not targets: return await interaction.followup.send("⚠️ No valid users.", ephemeral=True)
    
    success, failed, missing = [], [], []
    for member in targets:
        #if role >= member.top_role: failed.append(f"{member.mention} (role too high)"); continue
        if role not in member.roles: missing.append(member.mention); continue
        try:
            await member.remove_roles(role, reason=reason)
            success.append(member.mention)
        except: failed.append(f"{member.mention} (error)")
    
    embed = create_role_embed("remove", role, success, missing, failed)
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="bulk_role", description="Mass manage roles")
@app_commands.describe(
    action="Add or remove role",
    role="Role to manage",
    duration="Duration for temporary roles (e.g. 1h30m)"
)

@app_commands.choices(action=[
    app_commands.Choice(name="Add", value="add"),
    app_commands.Choice(name="Remove", value="remove")
])
@app_commands.guild_only()
async def bulk_role(interaction: discord.Interaction, action: str, role: discord.Role, duration: str=None):
    
    # Permission checks
    if not interaction.user.guild_permissions.manage_roles:
        return await interaction.followup.send("❌ You need manage_roles permission", ephemeral=True)
    if role >= interaction.user.top_role or role >= interaction.guild.me.top_role:
        return await interaction.followup.send("❌ Can't manage this role", ephemeral=True)
    
    # Interactive input embed
    embed = discord.Embed(
        title=f"🔹 {action.capitalize()} {role.name}",
        description="**Send user list:**\n"
                   "```\n"
                   "@User1 123456789 User3\n"
                   "User4, User5, User6\n"
                   "```\n"
                   "Or attach a `.txt` file",
        color=0x00ff00
    ).set_footer(text="You have 2 minutes to respond")
    
    await interaction.followup.send(embed=embed)
    
    try:
        msg = await bot.wait_for("message", timeout=150, check=lambda m: (
            m.author == interaction.user and 
            m.channel == interaction.channel and 
            (m.content or m.attachments)))
        
        text = msg.content if not msg.attachments else (await msg.attachments[0].read()).decode('utf-8')
        if users := await RoleManager(bot).get_users(interaction.guild, text):
            await RoleManager(bot).process_roles(interaction, role, users, action, duration)
        else:
            await interaction.followup.send("❌ No valid users found", ephemeral=True)
    except asyncio.TimeoutError:
        await interaction.followup.send("⌛ Timeout - Please try again", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)


@bot.tree.command(name="server_mute", description="Mute a user in the server with an optional duration and reason.")

@app_commands.describe(
    user="The user to mute.",
    duration="The duration of the mute (e.g., 1h, 30m).",
    reason="The reason for the mute."
)
@app_commands.guild_only()
async def server_mute(interaction: discord.Interaction, user: discord.Member, duration: str = None, reason: str = "No reason provided"):

    # Moderator permission
    if not isinstance(interaction.user, discord.Member) or \
       not interaction.user.guild_permissions.moderate_members:
        await interaction.response.send_message(
            "❌ You need moderator permission to use this command.",
            ephemeral=True
        )
        return
    await interaction.response.defer()

    duration_seconds = 0
    if duration:
        try:
            if 'h' in duration:
                duration_seconds += int(duration.split('h')[0]) * 3600
                duration = duration.split('h')[1]
            if 'm' in duration:
                duration_seconds += int(duration.split('m')[0]) * 60
        except ValueError:
            await interaction.followup.send("⚠️ Invalid duration format. Use '1h' for 1 hour or '30m' for 30 minutes.")
            return

    try:
        await user.edit(mute=True)
    except discord.Forbidden:
        await interaction.followup.send("⚠️ I don't have permission to mute this user.")
        return
    except discord.HTTPException as e:
        await interaction.followup.send(f"⚠️ Failed to mute the user: {e}")
        return

    confirmation_message = await interaction.followup.send(f"✅ {user.mention} has been muted for {format_duration(duration_seconds) if duration_seconds else 'an indefinite period'}. Reason: {reason}")

    if duration_seconds > 0:
        
        unmute_timestamp = int(datetime.now(timezone.utc).timestamp()) + duration_seconds
        save_mute_data(str(user.id), str(interaction.guild.id), unmute_timestamp)

    action_id = log_moderation_action_to_db(interaction.guild, user, "Server Mute", format_duration(duration_seconds), reason, interaction.user)

    await update_proof_message(interaction.guild, action_id, format_duration(duration_seconds), reason)
    await update_user_dm(interaction.guild, action_id, format_duration(duration_seconds), reason)



class PremiumStats(commands.Cog):
    def __init__(self, bot): self.bot = bot

    @app_commands.command(name="stats", description="Premium moderation statistics dashboard")
    @app_commands.guild_only()
    async def stats(self, interaction: discord.Interaction):
        if not is_moderator(interaction):
            await interaction.response.send_message("❌ You need moderator permission.",
                ephemeral=True
        )
            return
        await interaction.response.defer()
        try:
            view = PremiumStatsView(self.bot, interaction.guild)
            await interaction.followup.send(embed=(await view.generate_embeds())[0], view=view)
        except Exception as e:
            await interaction.followup.send(f"❌ Failed to load stats: {str(e)}", ephemeral=True)

async def setup(bot): await bot.add_cog(PremiumStats(bot))


class RoleManager:
    def __init__(self, bot):
        self.bot = bot

    async def get_users(self, guild, text):
        users = []
        for entry in re.split(r'[\s,\n]+', text.strip()):
            if not entry: continue
            
            try:
                if entry.isdigit():
                    user = guild.get_member(int(entry))
                elif match := re.match(r'<@!?(\d+)>', entry):
                    user = guild.get_member(int(match.group(1)))
                else:
                    user = discord.utils.get(guild.members, name=entry)
                if user:
                    users.append(user)
                else:
                    users.append((entry, "User not found"))
            except:
                users.append((entry, "Invalid format"))
        return users if users else None

    def parse_duration(self, text):
        if not text: return None
        units = {'s':1, 'm':60, 'h':3600, 'd':86400, 'w':604800}
        seconds = 0
        for num, unit in re.findall(r'(\d+)([smhdw])', text.lower()):
            seconds += int(num) * units[unit]
        return timedelta(seconds=seconds) if seconds else None

    async def process_roles(self, ctx, role, users, action, duration=None):
        results = []
        for user in users:
            if not isinstance(user, discord.Member):
                await ctx.channel.send(f"❌ {user[0]}: {user[1]}", delete_after=10)
                results.append(user)
                continue
                
            try:
                if action == "add":
                    await user.add_roles(role)
                    msg = f"✅ Added {role.mention}"
                    if duration and (delta := self.parse_duration(duration)):
                        with sqlite3.connect(TEMP_ROLE_DB) as conn:
                            conn.execute("INSERT OR REPLACE INTO temporary_roles VALUES (?,?,?,?)",
                                       (ctx.guild.id, role.id, user.id, (datetime.now(timezone.utc)+delta).isoformat()))
                        msg += f" for {duration}"
                else:
                    await user.remove_roles(role)
                    with sqlite3.connect(TEMP_ROLE_DB) as conn:
                        conn.execute("DELETE FROM temporary_roles WHERE guild_id=? AND role_id=? AND user_id=?",
                                   (ctx.guild.id, role.id, user.id))
                    msg = f"✅ Removed {role.mention}"
                results.append((user, msg))
                await ctx.channel.send(msg, delete_after=10)
            except Exception as e:
                results.append((user, f"❌ Error: {e}"))
                await ctx.channel.send(f"❌ {user.mention}: {e}", delete_after=10)
        
        # Create paginated embeds
        if not results: return
        
        embeds = []
        for i in range(0, len(results), 10):
            embed = discord.Embed(
                title=f"⚡ {action.capitalize()} Results - {role.name}",
                color=discord.Color.green() if action == "add" else discord.Color.red()
            )
            
            success = [f"{u[0].mention}: {u[1][4:]}" for u in results[i:i+10] if u[1].startswith('✅')]
            if success:
                embed.add_field(name="Successful", value="\n".join(success), inline=False)
            
            failed = [f"{u[0].mention if hasattr(u[0], 'mention') else u[0]}: {u[1]}" 
                     for u in results[i:i+10] if not u[1].startswith('✅')]
            if failed:
                embed.add_field(name="Failed", value="\n".join(failed), inline=False)
            
            embeds.append(embed)
        
        # Send with pagination if needed
        if len(embeds) > 1:
            view = RolePagination(embeds)
            view.current_page = 0
            await ctx.followup.send(embed=embeds[0], view=view)
        else:
            await ctx.followup.send(embed=embeds[0])

@bot.tree.command(
    name="appeal",
    description="Appeal a moderation action using its Action ID"
)
@app_commands.guild_only()
@app_commands.describe(action_id="The Action ID you want to appeal")
async def appeal_slash(interaction: discord.Interaction, action_id: str):

    # ✅ ACK FIRST
    await interaction.response.defer(ephemeral=True)

    if not interaction.guild:
        return await interaction.followup.send(
            "❌ This command can only be used inside a server.",
            ephemeral=True
        )

    guild = interaction.guild
    user = interaction.user

    # -----------------------------
    # 🔒 VALIDATE APPEAL
    # -----------------------------
    valid, reason = validate_appeal(
        guild_id=str(guild.id),
        action_id=action_id,
        user_id=user.id,
        moderator_name=None
    )

    if not valid:
        if reason == "used":
            return await interaction.followup.send(
                "❌ An appeal for this action has already been submitted and cannot be created again.",
                ephemeral=True
            )
        else:
            return await interaction.followup.send(
                "❌ Invalid Action ID or you are not allowed to appeal this action.",
                ephemeral=True
            )

    # -----------------------------
    # CREATE APPEAL CHANNEL
    # -----------------------------
    try:
        channel, created = await create_appeal_channel(
            guild,
            user,
            action_id
        )
    except Exception:
        return await interaction.followup.send(
            "❌ Failed to create appeal. Please contact a moderator.",
            ephemeral=True
        )

    # -----------------------------
    # MARK APPEAL USED (NON-FATAL)
    # -----------------------------
    if created:
        try:
            mark_appeal_used(str(guild.id), action_id)
        except Exception as e:
            logger.error(
                f"Appeal channel created but failed to update appeal_used "
                f"(guild={guild.id}, action={action_id}): {e}"
            )

    # -----------------------------
    # RESPONSE
    # -----------------------------
    channel_link = f"https://discord.com/channels/{guild.id}/{channel.id}"
    msg = (
        "✅ Appeal created successfully."
        if created
        else "ℹ️ An appeal for this action already exists."
    )

    await interaction.followup.send(
        f"{msg}\n🔗 {channel_link}",
        ephemeral=True
    )




#====================================================
# RUN
#====================================================

bot.run(TOKEN)