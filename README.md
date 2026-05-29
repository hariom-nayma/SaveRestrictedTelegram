# 🚀 SaveRestricted Telegram Userbot

A secure, premium, and local Python-based Telegram Userbot designed to download video content from channels that have "Restrict Saving Content" enabled. By using the official MTProto protocol via your own Telegram account session, this script acts as a normal client, downloading media chunks directly and transferring them streamably back to your **Saved Messages** chat.

---

## ✨ Features

* **🛡️ Security-First & Local:** Runs entirely on your local machine. No external servers or bots ever see your session keys or downloaded files.
* **💬 Private Control Interface:** Listens only to you in your personal **Saved Messages** chat. Nobody else can trigger or control your bot.
* **📈 High-Fidelity Progress Bars:** Shows real-time, throttled ASCII progress indicators for both downloading and uploading (includes percentage, MBs transferred, speeds in MB/s, and dynamic ETAs).
* **⚡ 10x Transfer Speeds:** Utilizes `cryptg` C-bindings to speed up Telegram's AES-256-IGE file decryption/encryption, delivering maximum bandwidth.
* **📺 Streamable Support:** Uploads videos with `supports_streaming=True`, allowing you to play the returned videos immediately in Telegram without waiting for a full download.
* **📦 Sequential Range Batching:** Supports `/batch <start_link> <end_link>` to automatically loop through a range of channel messages (up to 60 at a time) for course or channel archiving.
* **🧹 Storage Clean-up:** Automatically deletes temporary download files from your local drive the second they are uploaded back to your Telegram account.

---

## 🔑 Prerequisites

1. **Python 3.8+** (Installed: Python 3.14)
2. **Telegram API ID & API Hash**:
   * Log in to **[https://my.telegram.org](https://my.telegram.org)**.
   * Go to **API development tools**.
   * Fill out the simple application form (e.g., App Title/Short Name: `SaveRestricted`).
   * Copy the **App api_id** and **App api_hash**.

---

## ⚙️ Installation & Setup

1. **Configure Environment Variables**:
   * Duplicate the `.env.example` file in this directory and rename it to `.env`.
   * Open the `.env` file and enter your `API_ID` and `API_HASH`:
     ```env
     API_ID=12345678
     API_HASH=abcdef1234567890abcdef1234567890
     ```

2. **Run the Userbot**:
   * Open your command prompt (Terminal/PowerShell) in this folder.
   * Run the script using the pre-configured virtual environment:
     ```powershell
     .\venv\Scripts\python.exe main.py
     ```
   * **First Run Login Flow:**
     * The terminal will ask you to enter your **Phone Number** (with country code, e.g., `+1234567890`).
     * Telegram will send a login code (OTP) via Telegram. Enter this code into the terminal prompt.
     * If you have Two-Step Verification (2FA) active, the terminal will prompt you for your password.
     * Once authenticated, a secure local file `userbot.session` is created. **You will not need to log in again** as long as this file exists!

---

## 📖 Usage Guide

Once started, the terminal will display a startup banner indicating the bot is listening. Now, open Telegram and head to your **Saved Messages** chat.

### 🎥 1. Download a Single Restricted Video
Simply paste a message link from any public channel or any private channel that you are a member of:
* **Private Link Format:** `https://t.me/c/1234567890/4321`
* **Public Link Format:** `https://t.me/my_channel/4321`

**What happens next:**
1. The userbot detects the link and replies in Saved Messages: `🔍 Link detected! Querying Telegram...`
2. It downloads the video, updating you with a live progress bar.
3. It uploads the video back to you as a streamable Telegram video file.
4. It deletes the temporary progress message and cleans the local file from your disk.

---

### 📦 2. Download a Sequence Batch of Videos
Archiving a course or saving multiple videos from a channel is incredibly easy:
* Send the command:
  ```text
  /batch <start_message_link> <end_message_link>
  ```
  **Example:**
  ```text
  /batch https://t.me/c/1234567890/100 https://t.me/c/1234567890/115
  ```

**Rules for Batching:**
* Both links must belong to the **exact same channel/chat**.
* The maximum range is capped at **60 messages** per batch to keep your account safe from Telegram flood limits.
* The bot will download and upload each media file one-by-one, sleeping for `2.5 seconds` between files to act as a polite client.

---

## 🛡️ Account Safety & Limits

* **Flood Limits (`FloodWait`):** Telegram limits rapid successive requests. The bot includes automatic retry-on-flood waits. The `/batch` command includes a deliberate delay of `2.5` seconds between transfers to keep your account in safe standing.
* **Keep Sessions Private:** The `userbot.session` file represents your active Telegram log-in. **Never share this file or upload it to GitHub!**

---

## 🛠️ Troubleshooting

* **ValueError / Access Denied:** 
  * Double check that your account is actually a member of the private channel. If you can't view the messages in your official Telegram app, the userbot won't be able to access them either.
  * If it's a private channel, verify the link format has `/c/` followed by a number.
* **Missing Dependencies:** Ensure you run the script using the virtual environment interpreter (`.\venv\Scripts\python.exe main.py`) so it loads the correct libraries.
