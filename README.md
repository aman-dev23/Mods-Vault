# 🛡️ Mods Vault

**Mods Vault** is a Discord moderation management bot built to simplify and organize moderation workflows.

It was developed around a real moderation workflow where actions, proofs, appeals, and moderation history had to be tracked manually.

The bot brings these tasks together inside Discord while maintaining persistent records using SQLite.

---

## ✨ Features

### 📋 Moderation Logging

* Logs moderation actions with a unique **Action ID**
* Stores the user, moderator, action, reason, duration, and timestamp
* Keeps moderation history organized per server

### 📎 Proof Management

* Moderators can submit proof by replying to a moderation log
* Supports **images, videos, and audio**
* Maximum **10 proofs per moderation action**
* Maximum proof file size of **7 MB**
* Proofs are processed through a queue before being stored
* Supports multiple proof-storage channels

### 🔎 Moderation History & Search

* Search moderation records using **Action IDs**
* Search moderation history by user
* View stored proofs associated with moderation actions

### ⚖️ Appeal System

* Users can appeal eligible moderation actions
* Appeals are linked to the original **Action ID**
* Prevents repeated appeals for the same action
* Creates private appeal channels for moderators and the user

### ⏱️ Automatic Temporary Mutes

* Supports temporary server mutes
* Mute information is stored persistently
* Automatically removes the mute when the duration expires

### 🎭 Temporary Role Management

* Supports temporary roles
* Roles can be scheduled for automatic removal

### 📊 Moderation Statistics

* View moderation activity over different time ranges
* 24 hours, 7 days, 30 days, and all-time statistics
* Moderator and user activity
* Action distribution
* Reasons and duration statistics

### 💾 Persistent Storage

The bot uses SQLite databases to store:

* Moderation logs
* Mute data
* Temporary roles
* Server configuration
* Proof-processing queue

The database files are created automatically when the bot runs.

---

# 🚀 Setup & Installation

## 1. Clone the repository

Clone this repository using Git:

```bash
git clone YOUR_REPOSITORY_URL
```

Then open the project folder in VS Code.

---

## 2. Install Python

Make sure Python is installed on your system.

You can check with:

```bash
python --version
```

---

## 3. Install the required packages

Install the dependencies with:

```bash
pip install discord.py python-dotenv
```

---

## 4. Create the `.env` file

Create a file named:

```text
.env
```

in the **same folder as the main Python file**.

Your folder should look like:

```text
Mods-Vault/
│
├── MV_main.py
└── .env
```

Inside `.env`, add your Discord bot token:

```env
MV_TOKEN=YOUR_BOT_TOKEN_HERE
```

The bot reads the token using the `MV_TOKEN` environment variable.

**Never share your bot token or upload your real `.env` file to GitHub.**

---

# 📁 Proof Storage Setup

Mods Vault stores submitted proofs inside Discord channels.

Before running the bot, you need to provide the IDs of the Discord channels that should be used for proof storage.

In the main Python file, find:

```python
PROOF_STORAGE_CHANNEL_IDS = [
    ...
]
```

Replace the existing IDs with the IDs of the channel(s) you want the bot to use.

For example:

```python
PROOF_STORAGE_CHANNEL_IDS = [
    123456789012345678,
    987654321098765432,
]
```

You can provide **one channel or multiple channels**.

If multiple channels are provided, the bot cycles through them when storing proofs.

### Important

The bot must have access to these channels and must have permission to:

* View the channel
* Send messages
* Attach files
* Read message history

The proof-processing system uses these configured channel IDs to locate the storage channels and upload submitted proof files there.

---

# 🤖 Discord Bot Setup

Create a Discord application and bot through the Discord Developer Portal.

When configuring the bot, make sure the required intents are enabled.

Mods Vault uses:

* Server Members Intent
* Message Content Intent
* Moderation-related intents
* Guilds
* Messages
* Reactions
* Voice States

The code enables these intents when creating the bot.

After creating the bot, invite it to your Discord server with the permissions required for moderation and channel management.

For the complete feature set, the bot needs appropriate permissions to:

* Moderate members
* Manage roles
* Manage channels
* Send messages
* Read message history
* Attach files
* View channels
* Manage messages where required

---

# ▶️ Running the Bot

Once the setup is complete, run:

```bash
python MV_main.py
```

If your file has a different name, replace `MV_main.py` with the actual filename.

If everything is configured correctly, the bot will connect to Discord.

The SQLite databases will be created automatically in the same project directory.

---

# 🗄️ Database Files

You do **not** need to manually create the database files.

Mods Vault automatically creates databases for:

```text
Action_logs_all_server.db
mute_data.db
temporary_roles.db
server_config.db
proof_queue.db
```

These files contain the bot's runtime data and are generated automatically by the application.

For that reason, database files should generally **not be uploaded to the repository**.

---

# 🔐 Security

Never upload sensitive information such as:

* `.env`
* Discord bot token
* Private server credentials
* Runtime database files containing moderation data

For GitHub, create a `.env.example` file instead:

```env
MV_TOKEN=YOUR_BOT_TOKEN_HERE
```

And add `.env` to `.gitignore`:

```gitignore
.env
*.db
__pycache__/
*.pyc
```

---

# 🧩 Project Structure

A minimal setup looks like:

```text
Mods-Vault/
│
├── MV_main.py
├── .env.example
├── .gitignore
└── README.md
```

The `.env` file and SQLite databases are created/maintained locally and should not be committed to the repository.

---

# ⚠️ Current Limitations

The proof-storage channels are currently configured directly in the Python source code through `PROOF_STORAGE_CHANNEL_IDS`.

When using the bot on another server, replace these IDs with channels accessible to your bot.

---

# 📌 Project Status

Mods Vault is a personal/student project developed to solve a real moderation workflow.

The bot is **not currently hosted as a public service**. The repository contains the source code for running your own instance.

---
