import os
import re
import sys
import time
import math
import random
import json
import asyncio
import logging
import urllib.request
from dotenv import load_dotenv
from telethon import TelegramClient, events, Button
from telethon.sessions import StringSession
from telethon.tl.types import PeerChannel, DocumentAttributeVideo
from telethon.errors import FloodWaitError, MessageIdInvalidError

# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("SaveRestricted")

# Load environment variables
load_dotenv()

API_ID = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")

if not API_ID or not API_HASH:
    print("\n" + "="*60)
    print("❌ ERROR: Missing API_ID or API_HASH in your .env file!")
    print("Please create a .env file and fill in your credentials from https://my.telegram.org")
    print("Refer to .env.example for structure.")
    print("="*60 + "\n")
    sys.exit(1)

# Ensure api_id is an integer
try:
    API_ID = int(API_ID)
except ValueError:
    print("\n❌ ERROR: API_ID in .env must be an integer!\n")
    sys.exit(1)

# Initialize the Telethon TelegramClient
# If TELEGRAM_SESSION environment variable is set (ideal for Render deployment), use StringSession.
# Otherwise, fall back to the local "userbot.session" file.
session_str = os.getenv("TELEGRAM_SESSION")
if session_str:
    logger.info("Initializing client using TELEGRAM_SESSION environment variable.")
    client = TelegramClient(StringSession(session_str), API_ID, API_HASH)
else:
    logger.info("Initializing client using local 'userbot' session file.")
    client = TelegramClient("userbot", API_ID, API_HASH)
bot_client = None

# Custom upload target configuration
UPLOAD_CHAT_ENTITY = 'me'

# Chronological cloning state configuration
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "clone_state.json")
clone_state = {}
active_clone_task = None

async def safe_edit_msg(msg, text, buttons=None):
    if not msg:
        return None
    try:
        return await msg.edit(text, buttons=buttons)
    except FloodWaitError as e:
        logger.warning(f"Failed to edit message due to FloodWait ({e.seconds}s). Falling back to sending a new message.")
        try:
            return await msg.respond(text, buttons=buttons)
        except Exception as ex:
            logger.error(f"Failed fallback respond call: {ex}")
            return msg
    except Exception as e:
        logger.warning(f"Failed to edit message: {e}. Falling back to sending a new message.")
        try:
            return await msg.respond(text, buttons=buttons)
        except Exception as ex:
            logger.error(f"Failed fallback respond call: {ex}")
            return msg

async def safe_cancel_active_task():
    global active_clone_task
    if active_clone_task and not active_clone_task.done():
        logger.info("Cancelling active clone task to prevent duplicate loops...")
        active_clone_task.cancel()
        try:
            await active_clone_task
        except asyncio.CancelledError:
            logger.info("Active clone task successfully cancelled.")
        except Exception as e:
            logger.warning(f"Error while cancelling active task: {e}")
        finally:
            active_clone_task = None

async def restart_bot():
    logger.info("Restarting bot process...")
    # 1. Cancel active clone task if any
    global active_clone_task
    if active_clone_task and not active_clone_task.done():
        active_clone_task.cancel()
        try:
            await active_clone_task
        except asyncio.CancelledError:
            pass
            
    # 2. Disconnect clients
    try:
        await client.disconnect()
    except Exception:
        pass
    if bot_client:
        try:
            await bot_client.disconnect()
        except Exception:
            pass
            
    # 3. Execv to restart process
    os.execv(sys.executable, [sys.executable] + sys.argv)

async def refresh_bot(status_msg):
    global active_clone_task, clone_state
    
    try:
        await status_msg.edit("🔄 **Refreshing bot...**\n1. Reloading environment variables...")
        # Reload .env
        load_dotenv(override=True)
        
        await status_msg.edit("🔄 **Refreshing bot...**\n2. Re-resolving destination chat...")
        # Re-resolve upload chat
        await resolve_upload_chat()
        
        await status_msg.edit("🔄 **Refreshing bot...**\n3. Loading clone state...")
        # Reload clone state
        load_clone_state()
        
        await status_msg.edit("🔄 **Refreshing bot...**\n4. Re-fetching dialogs...")
        # Refresh client dialogs/cache
        try:
            await client.get_dialogs(limit=20)
        except Exception as e:
            logger.warning(f"Failed to refresh userbot dialogs: {e}")
            
        if bot_client:
            try:
                await bot_client.get_dialogs(limit=20)
            except Exception as e:
                logger.warning(f"Failed to refresh controller bot dialogs: {e}")
        
        dest_desc = "Saved Messages" if UPLOAD_CHAT_ENTITY == 'me' else getattr(UPLOAD_CHAT_ENTITY, 'title', str(UPLOAD_CHAT_ENTITY))
        await status_msg.edit(
            f"✅ **Bot Refreshed Successfully!**\n\n"
            f"📥 **Destination Chat:** `{dest_desc}`\n"
            f"📂 **Clone State:** `{clone_state.get('status', 'idle')}`"
        )
    except Exception as e:
        logger.error(f"Error during refresh: {e}", exc_info=True)
        await status_msg.edit(f"❌ **Failed to refresh bot:** `{str(e)}`")

def load_clone_state():
    global clone_state
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                clone_state = json.load(f)
                logger.info(f"Loaded clone state: {clone_state}")
        except Exception as e:
            logger.error(f"Failed to load clone state: {e}")

def save_clone_state():
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(clone_state, f, indent=4)
            logger.info("Saved clone state to file.")
    except Exception as e:
        logger.error(f"Failed to save clone state: {e}")

async def resolve_upload_chat():
    global UPLOAD_CHAT_ENTITY
    upload_chat_str = os.getenv("UPLOAD_CHAT", "me").strip()
    if not upload_chat_str or upload_chat_str.lower() == 'me':
        UPLOAD_CHAT_ENTITY = 'me'
        logger.info("Upload destination: Saved Messages ('me')")
        return

    # Check if it's a private chat invite link (e.g. https://t.me/+PXd5MfX_EPA0ZjNl)
    invite_match = re.search(r'(?:https?://)?(?:t\.me/)(?:\+|joinchat/)([a-zA-Z0-9_-]+)', upload_chat_str)
    if invite_match:
        invite_hash = invite_match.group(1)
        logger.info(f"Resolving private chat invite link with hash: {invite_hash}")
        try:
            from telethon.tl.functions.messages import CheckChatInviteRequest, ImportChatInviteRequest
            from telethon.tl.types import ChatInviteAlready
            
            # Check if we are already in the channel or not
            invite_info = await client(CheckChatInviteRequest(invite_hash))
            if isinstance(invite_info, ChatInviteAlready):
                UPLOAD_CHAT_ENTITY = invite_info.chat
                logger.info(f"Already in target private channel: {getattr(invite_info.chat, 'title', 'Private Channel')} (ID: {invite_info.chat.id})")
            else:
                # Import/Join invite link
                updates = await client(ImportChatInviteRequest(invite_hash))
                # Refresh local cache so the newly joined channel is resolved immediately
                try:
                    await client.get_dialogs()
                except Exception:
                    pass
                if hasattr(updates, 'chats') and updates.chats:
                    UPLOAD_CHAT_ENTITY = updates.chats[0]
                    logger.info(f"Successfully joined target private channel: {getattr(UPLOAD_CHAT_ENTITY, 'title', 'Private Channel')} (ID: {UPLOAD_CHAT_ENTITY.id})")
                else:
                    UPLOAD_CHAT_ENTITY = await client.get_entity(invite_info.chat.id)
                    logger.info(f"Joined target private channel: ID {invite_info.chat.id}")
        except Exception as e:
            logger.error(f"Error resolving private invite link: {e}", exc_info=True)
            try:
                # Attempt to get entity directly in case we can access it
                UPLOAD_CHAT_ENTITY = await client.get_entity(upload_chat_str)
                logger.info("Successfully resolved upload chat as entity.")
            except Exception as ex:
                logger.error(f"Failed fallback resolution of UPLOAD_CHAT: {ex}")
                UPLOAD_CHAT_ENTITY = 'me'
                logger.info("Defaulting upload destination to Saved Messages ('me') due to error.")
    else:
        # Handle public channel username or numeric ID
        try:
            if upload_chat_str.startswith("-100") or upload_chat_str.isdigit():
                chat_id = int(upload_chat_str)
                UPLOAD_CHAT_ENTITY = await client.get_entity(chat_id)
            else:
                UPLOAD_CHAT_ENTITY = await client.get_entity(upload_chat_str)
            logger.info(f"Resolved upload chat: {getattr(UPLOAD_CHAT_ENTITY, 'title', str(UPLOAD_CHAT_ENTITY))}")
        except Exception as e:
            logger.error(f"Failed to resolve UPLOAD_CHAT '{upload_chat_str}': {e}")
            UPLOAD_CHAT_ENTITY = 'me'
            logger.info("Defaulting upload destination to Saved Messages ('me') due to error.")

# Regex to match public and private telegram message links
LINK_REGEX = r'(?:https?://)?(?:t\.me/)(?:c/(\d+)|([a-zA-Z0-9_]{5,}))/(\d+)'

# Temporary directory for media downloads
DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)


class ProgressTracker:
    """Class to track download/upload progress with speed, ETA, and ASCII progress bar.
    Throttles message edits to at most once every 2 seconds to avoid Telegram FloodWaits.
    """
    def __init__(self, status_msg, action_name="Processing", total_items=1, current_item=1):
        self.status_msg = status_msg
        self.action_name = action_name
        self.last_update_time = time.time()
        self.start_time = time.time()
        self.total_items = total_items
        self.current_item = current_item
        
        # Safely determine numeric total items for display checks
        try:
            self.is_numeric = isinstance(total_items, (int, float)) or (isinstance(total_items, str) and str(total_items).strip().isdigit())
            self.total_items_val = int(total_items) if self.is_numeric else 0
        except (ValueError, TypeError):
            self.is_numeric = False
            self.total_items_val = 0
        self.last_percentage = 0.0

    async def progress_callback(self, current, total):
        if not total:
            return
            
        now = time.time()
        percentage = (current / total) * 100
        time_elapsed = now - self.last_update_time
        percent_diff = percentage - self.last_percentage
        
        # Throttled progress update: max once every 5.0 seconds and at least 10% change OR when complete
        if current < total and (time_elapsed < 5.0 or percent_diff < 10.0):
            return
            
        self.last_update_time = now
        self.last_percentage = percentage
        elapsed = now - self.start_time
        percentage = (current / total) * 100
        
        # Calculate speed and ETA
        speed = current / elapsed if elapsed else 0
        if speed > 0:
            eta_seconds = (total - current) / speed
            if eta_seconds >= 60:
                eta_str = f"{int(eta_seconds // 60)}m {int(eta_seconds % 60)}s"
            else:
                eta_str = f"{int(eta_seconds)}s"
        else:
            eta_str = "Calculating..."
            
        # Format sizes
        curr_mb = current / (1024 * 1024)
        total_mb = total / (1024 * 1024)
        speed_mb = speed / (1024 * 1024)
        
        # Build premium ASCII progress bar
        bar_length = 12
        filled_length = int(round(bar_length * current / float(total)))
        bar = '█' * filled_length + '░' * (bar_length - filled_length)
        
        # Format the status message
        batch_prefix = f"🔄 **[Batch: {self.current_item}/{self.total_items}]**\n" if not getattr(self, 'is_numeric', False) or getattr(self, 'total_items_val', 0) > 1 else ""
        
        text = (
            f"{batch_prefix}"
            f"⚡ **{self.action_name}**\n\n"
            f"📁 Progress: `[{bar}]` **{percentage:.1f}%**\n"
            f"💾 Size: `{curr_mb:.2f} MB` / `{total_mb:.2f} MB`\n"
            f"⚡ Speed: `{speed_mb:.2f} MB/s`\n"
            f"⏳ ETA: `{eta_str}`"
        )
        
        try:
            # Edit the status message safely
            new_msg = await safe_edit_msg(self.status_msg, text)
            if new_msg:
                self.status_msg = new_msg
        except Exception:
            # Silently ignore errors (e.g. message text not changed, or quick FloodWait)
            pass

def parse_telegram_link(link: str):
    """Parses a telegram message link to extract chat and message parameters.
    Returns: (is_private, chat_identifier, message_id) or None
    """
    match = re.search(LINK_REGEX, link)
    if not match:
        return None
        
    private_chat_id = match.group(1)
    public_username = match.group(2)
    message_id = int(match.group(3))
    
    if private_chat_id:
        return True, int(private_chat_id), message_id
    else:
        return False, public_username, message_id

async def download_and_upload_video(chat_entity, message_id: int, status_msg, link_str: str, batch_info=(1, 1), file_status_msg=None):
    """Downloads a video message locally and uploads it back to Saved Messages.
    Returns: True if success, False otherwise.
    """
    current_item, total_items = batch_info
    
    # Safe check for total_items type to support string values like "Ongoing"
    try:
        is_numeric = isinstance(total_items, (int, float)) or (isinstance(total_items, str) and total_items.strip().isdigit())
        total_val = int(total_items) if is_numeric else 0
    except (ValueError, TypeError):
        is_numeric = False
        total_val = 0
        
    batch_str = f"[{current_item}/{total_items}] " if not is_numeric or total_val > 1 else ""
    
    # Determine if we should create a separate message or edit status_msg directly
    is_multi = False
    try:
        # If total_items is not an integer or is greater than 1, it's a multi-file batch/clone process
        if isinstance(total_items, str) and not total_items.strip().isdigit():
            is_multi = True
        elif int(total_items) > 1:
            is_multi = True
    except (ValueError, TypeError):
        is_multi = True
 
    file_path = None
    thumb_path = None
    created_here = False
    
    try:
        if is_multi:
            if not file_status_msg:
                # Fallback to editing status_msg if possible
                file_status_msg = await safe_edit_msg(status_msg, f"🔍 {batch_str}Fetching message metadata for ID `{message_id}`...")
                if not file_status_msg:
                    # Fallback to respond if edit fails
                    file_status_msg = await status_msg.respond(f"🔍 {batch_str}Fetching message metadata for ID `{message_id}`...")
                    created_here = True
            else:
                new_msg = await safe_edit_msg(file_status_msg, f"🔍 {batch_str}Fetching message metadata for ID `{message_id}`...")
                if new_msg:
                    file_status_msg = new_msg
        else:
            file_status_msg = status_msg
            new_msg = await safe_edit_msg(file_status_msg, f"🔍 {batch_str}Fetching message metadata for ID `{message_id}`...")
            if new_msg:
                file_status_msg = new_msg

        # 1. Fetch the message
        msg = await client.get_messages(chat_entity, ids=message_id)
        
        if not msg:
            await file_status_msg.edit(f"❌ {batch_str}Error: Message `{message_id}` not found.")
            return False
            
        if not msg.media:
            await file_status_msg.edit(f"❌ {batch_str}Error: Message `{message_id}` contains no media/files.")
            return False

        # Check if the media is a video/document
        if not hasattr(msg.media, 'document') and not hasattr(msg.media, 'video'):
            # Attempt to download whatever media is there (photo, audio, etc.) if requested, 
            # but primary focus is videos/files
            pass
            
        # Get filename and extension
        ext = msg.file.ext or ".mp4"
        raw_name = msg.file.name
        if raw_name:
            # Clean filename from any illegal characters for file systems
            file_name = re.sub(r'[\\/*?:"<>|]', "", raw_name)
        else:
            file_name = f"video_{message_id}{ext}"
            
        file_path = os.path.join(DOWNLOAD_DIR, file_name)
        
        # 2. Download Media chunk-by-chunk
        logger.info(f"Starting download of file: {file_name} (Size: {msg.file.size} bytes)")
        tracker = ProgressTracker(file_status_msg, "📥 Downloading Video", total_items, current_item)
        
        start_t = time.time()
        await client.download_media(
            msg,
            file=file_path,
            progress_callback=tracker.progress_callback
        )
        download_duration = time.time() - start_t
        logger.info(f"Download complete for {file_name} in {download_duration:.1f}s")
        
        # Extract video attributes and download thumbnail if it is a video
        duration = None
        width = None
        height = None
        thumb_path = None
        attributes = []
        
        if msg.video:
            # Try to get the existing DocumentAttributeVideo from the message
            if msg.media and hasattr(msg.media, 'document') and msg.media.document:
                for attr in msg.media.document.attributes:
                    if isinstance(attr, DocumentAttributeVideo):
                        duration = attr.duration
                        width = attr.w
                        height = attr.h
                        break
            
            # Fallback to msg.file helper properties
            if duration is None:
                duration = msg.file.duration
            if width is None:
                width = msg.file.width
            if height is None:
                height = msg.file.height
                
            # Use safe fallbacks/conversions
            duration = int(duration) if duration else 0
            width = int(width) if width else 0
            height = int(height) if height else 0

            attributes.append(
                DocumentAttributeVideo(
                    duration=duration,
                    w=width,
                    h=height,
                    supports_streaming=True
                )
            )
            
            # Download the original thumbnail to keep it
            try:
                thumb_name = f"thumb_{message_id}.jpg"
                thumb_target = os.path.join(DOWNLOAD_DIR, thumb_name)
                thumb_path = await client.download_media(msg, file=thumb_target, thumb=-1)
                if thumb_path:
                    logger.info(f"Downloaded video thumbnail to: {thumb_path}")
            except Exception as e:
                logger.warning(f"Could not download thumbnail for message {message_id}: {e}")
                thumb_path = None
        
        # 3. Upload Media back to Saved Messages
        dest_name = "Saved Messages" if UPLOAD_CHAT_ENTITY == 'me' else "Target Channel"
        tracker_upload = ProgressTracker(file_status_msg, f"📤 Uploading Video to {dest_name}", total_items, current_item)
        
        caption = (
            f"🎥 **Restricted Video Downloaded**\n\n"
            f"🔗 **Source Link:** [Click here]({link_str})\n"
            f"📁 **File Name:** `{file_name}`\n"
            f"📦 **Size:** `{msg.file.size / (1024*1024):.2f} MB`\n\n"
            f"_Downloaded safely via SaveRestricted Userbot_"
        )
        
        logger.info(f"Starting upload of file: {file_path}")
        await client.send_file(
            UPLOAD_CHAT_ENTITY,
            file_path,
            caption=caption,
            thumb=thumb_path,
            attributes=attributes if attributes else None,
            progress_callback=tracker_upload.progress_callback,
            supports_streaming=True # Makes the video immediately streamable in Telegram UI
        )

        
        # Clean up local files immediately to save disk space
        if os.path.exists(file_path):
            os.remove(file_path)
        if thumb_path and os.path.exists(thumb_path):
            os.remove(thumb_path)
            
        # Delete the file progress message only if we created it here
        if created_here and file_status_msg:
            try:
                await file_status_msg.delete()
            except Exception:
                pass
                
        return True
        
    except FloodWaitError as e:
        logger.warning(f"Hit Telegram FloodWait: must wait {e.seconds} seconds.")
        if e.seconds > 180:
            logger.warning(f"FloodWait duration too long ({e.seconds}s). Raising exception to pause the clone loop.")
            raise e
            
        target_msg = file_status_msg if file_status_msg else status_msg
        try:
            await target_msg.edit(f"⚠️ Throttled by Telegram! Sleeping for `{e.seconds}` seconds...")
        except Exception:
            pass
        await asyncio.sleep(e.seconds)
        if is_multi and file_status_msg:
            try:
                await file_status_msg.delete()
            except Exception:
                pass
        # Retry once
        return await download_and_upload_video(chat_entity, message_id, status_msg, link_str, batch_info)
        
    except asyncio.CancelledError:
        logger.info(f"Task cancelled during download/upload of message ID {message_id}")
        if is_multi and file_status_msg:
            try:
                await file_status_msg.delete()
            except Exception:
                pass
        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
            except Exception:
                pass
        if thumb_path and os.path.exists(thumb_path):
            try:
                os.remove(thumb_path)
            except Exception:
                pass
        raise
        
    except Exception as e:
        logger.error(f"Failed to process message ID {message_id}: {str(e)}", exc_info=True)
        target_msg = file_status_msg if file_status_msg else status_msg
        try:
            await target_msg.edit(f"❌ {batch_str}Failed to download message ID `{message_id}`.\nError: `{str(e)}`")
        except Exception:
            pass
        # Clean up if files were partially downloaded or left
        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
            except Exception:
                pass
        if thumb_path and os.path.exists(thumb_path):
            try:
                os.remove(thumb_path)
            except Exception:
                pass
        return False

async def run_clone_loop(status_msg):
    global clone_state, active_clone_task
    try:
        source_chat = clone_state.get("source_chat_id")
        is_private = clone_state.get("is_private")
        
        # Force cast start_msg_id to int to prevent any legacy str vs int TypeErrors
        try:
            start_msg_id = int(clone_state.get("start_msg_id")) if clone_state.get("start_msg_id") is not None else None
        except (ValueError, TypeError):
            start_msg_id = None
            
        if not source_chat or start_msg_id is None:
            logger.error("Source chat or start message ID missing from clone state.")
            await safe_edit_msg(status_msg, "❌ **Error:** Invalid clone state configuration.")
            return
            
        status_msg = await safe_edit_msg(status_msg, "🔄 **Step 1/3: Resolving source channel entity...**")
        
        # 1. Resolve source chat entity (Directly try standard resolution first to save time and network calls)
        chat_entity = None
        try:
            if is_private:
                # Try to resolve using full channel ID with -100 prefix first (very reliable if already joined/cached)
                try:
                    chat_entity = await asyncio.wait_for(client.get_entity(int(f"-100{source_chat}")), timeout=15)
                except Exception:
                    peer = PeerChannel(int(source_chat))
                    chat_entity = await asyncio.wait_for(client.get_entity(peer), timeout=15)
            else:
                chat_entity = await asyncio.wait_for(client.get_entity(source_chat), timeout=15)
        except Exception as e:
            # Fallback: only fetch recent dialogs if direct resolution fails
            logger.info(f"Direct resolution failed ({e}). Refreshing recent dialogs as fallback...")
            status_msg = await safe_edit_msg(status_msg, "🔄 **Step 1/3: Channel not cached. Fetching recent dialogs as fallback...**")
            try:
                # Limit dialogs to most recent 80 to make it extremely fast and lightweight
                await asyncio.wait_for(client.get_dialogs(limit=80), timeout=20)
                if is_private:
                    try:
                        chat_entity = await asyncio.wait_for(client.get_entity(int(f"-100{source_chat}")), timeout=15)
                    except Exception:
                        peer = PeerChannel(int(source_chat))
                        chat_entity = await asyncio.wait_for(client.get_entity(peer), timeout=15)
                else:
                    chat_entity = await asyncio.wait_for(client.get_entity(source_chat), timeout=15)
            except Exception as err:
                clone_state["status"] = "stopped"
                save_clone_state()
                await safe_edit_msg(status_msg, f"❌ **Failed to access source channel:** `{str(err)}`\nMake sure your account is a member of the channel.")
                return

        status_msg = await safe_edit_msg(status_msg, "🔄 **Step 2/3: Fetching latest message details...**")

        # 2. Retrieve the latest message ID to calculate progress and messages remaining
        try:
            latest_msgs = await asyncio.wait_for(client.get_messages(chat_entity, limit=1), timeout=15)
            if latest_msgs:
                clone_state["latest_msg_id"] = int(latest_msgs[0].id)
            else:
                clone_state["latest_msg_id"] = int(start_msg_id)
        except Exception as e:
            logger.warning(f"Could not retrieve latest message ID: {e}")
            clone_state["latest_msg_id"] = int(start_msg_id)
            
        status_msg = await safe_edit_msg(status_msg, "🔄 **Step 3/3: Initializing chronological download stream...**")
        
        # Determine starting message ID
        try:
            if clone_state.get("last_processed_id") is not None:
                start_from = int(clone_state["last_processed_id"])
            else:
                start_from = int(start_msg_id)
        except (ValueError, TypeError):
            start_from = start_msg_id
            
        clone_state["status"] = "running"
        save_clone_state()

        # exclusive range in iter_messages min_id parameter
        min_id = start_from - 1 if start_from and start_from > 0 else 0
        
        logger.info(f"Starting cloning from message ID {start_from} (min_id: {min_id})")
        
        last_overall_update_time = 0
        skipped_count_since_update = 0
        file_status_msg = None
        
        try:
            async for msg in client.iter_messages(chat_entity, min_id=min_id, reverse=True):
                # Check if task was paused/stopped
                if clone_state.get("status") in ("paused", "stopped"):
                    logger.info("Clone loop paused/stopped by user action.")
                    break
                    
                # Analytics calculation
                latest_id = int(clone_state.get("latest_msg_id", start_msg_id))
                total_to_process = max(1, latest_id - start_msg_id + 1)
                current_position = int(msg.id) - start_msg_id + 1
                remaining = max(0, latest_id - int(msg.id))
                percentage = min(100.0, max(0.0, (current_position / total_to_process) * 100))
                
                # Premium ASCII progress bar
                bar_length = 12
                filled_length = int(round(bar_length * percentage / 100.0))
                bar = '█' * filled_length + '░' * (bar_length - filled_length)
                
                # Check if message has media
                has_media = msg.media is not None
                
                # Show live stats in edit
                dest_desc = "Saved Messages" if UPLOAD_CHAT_ENTITY == 'me' else getattr(UPLOAD_CHAT_ENTITY, 'title', str(UPLOAD_CHAT_ENTITY))
                
                now = time.time()
                # Only update overall status if:
                # - It's the first run (last_overall_update_time == 0)
                # - OR we are about to download/upload media (has_media is True)
                # - OR 12 seconds have passed since the last edit
                # - OR we have skipped 10 messages since the last edit
                should_edit_status = False
                if last_overall_update_time == 0 or has_media:
                    should_edit_status = True
                elif now - last_overall_update_time >= 12.0:
                    should_edit_status = True
                elif skipped_count_since_update >= 10:
                    should_edit_status = True

                if should_edit_status:
                    last_overall_update_time = now
                    skipped_count_since_update = 0
                    try:
                        new_msg = await safe_edit_msg(
                            status_msg,
                            f"🔄 **Cloning Channel in Progress...**\n\n"
                            f"📊 **Clone Analytics:**\n"
                            f"• 📁 **Source Chat:** `{source_chat}`\n"
                            f"• 📥 **Destination:** `{dest_desc}`\n"
                            f"• 🎥 **Videos Cloned:** `{clone_state.get('total_cloned', 0)}`\n"
                            f"• ⏩ **Skipped (No Media):** `{clone_state.get('total_skipped', 0)}`\n"
                            f"• ⏳ **Messages Remaining:** `{remaining}`\n"
                            f"• 📈 **Milestone Batch:** `{clone_state.get('batch_count', 0)}/60`\n"
                            f"• 📊 **Overall Progress:** `[{bar}]` **{percentage:.1f}%**\n\n"
                            f"_Current Message ID: `{msg.id}`_",
                            buttons=[
                                [Button.inline("⏸️ Pause", b"clone_pause"), Button.inline("🛑 Stop", b"clone_stop")]
                            ] if bot_client else None
                        )
                        if new_msg:
                            status_msg = new_msg
                    except Exception as e:
                        logger.warning(f"Failed to edit overall status message: {e}")
                else:
                    skipped_count_since_update += 1
                
                if has_media:
                    if is_private:
                        msg_link = f"https://t.me/c/{source_chat}/{msg.id}"
                    else:
                        msg_link = f"https://t.me/{source_chat}/{msg.id}"
                        
                    # Initialize the persistent separate progress message if not already done
                    if not file_status_msg:
                        try:
                            # Try to send a new status message
                            file_status_msg = await status_msg.respond("🔄 **Initializing file progress stream...**")
                        except Exception as e:
                            logger.warning(f"Could not create separate progress message due to error: {e}. Reusing overall status message.")
                            
                    # Pass either file_status_msg or status_msg (as fallback)
                    active_progress_msg = file_status_msg if file_status_msg else status_msg
                    
                    success = await download_and_upload_video(
                        chat_entity,
                        msg.id,
                        status_msg,
                        msg_link,
                        batch_info=(clone_state.get("total_cloned", 0) + 1, "Ongoing"),
                        file_status_msg=active_progress_msg
                    )
                    
                    if success:
                        clone_state["total_cloned"] = clone_state.get("total_cloned", 0) + 1
                        clone_state["batch_count"] = clone_state.get("batch_count", 0) + 1
                    else:
                        clone_state["total_skipped"] = clone_state.get("total_skipped", 0) + 1
                else:
                    clone_state["total_skipped"] = clone_state.get("total_skipped", 0) + 1
                    
                clone_state["last_processed_id"] = msg.id
                save_clone_state()
                
                # Milestone Throttling
                if clone_state.get("batch_count", 0) >= 60:
                    clone_state["batch_count"] = 0
                    save_clone_state()
                    
                    sleep_time = random.randint(300, 600)
                    logger.info(f"60 videos milestone reached. Sleeping for {sleep_time} seconds...")
                    
                    # Sleep in increments of 5 seconds to support responsive pausing
                    for elapsed in range(0, sleep_time, 5):
                        if clone_state.get("status") in ("paused", "stopped"):
                            break
                        remaining_sleep = sleep_time - elapsed
                        new_msg = await safe_edit_msg(
                            status_msg,
                            f"💤 **Milestone Cooldown (60 Videos Completed)**\n\n"
                            f"Sleeping to prevent Telegram flood/ban restrictions.\n"
                            f"⏳ **Time Remaining:** `{remaining_sleep // 60}m {remaining_sleep % 60}s`\n"
                            f"📊 **Total Videos Cloned:** `{clone_state.get('total_cloned', 0)}`\n\n"
                            f"_System is resting safely..._",
                            buttons=[
                                [Button.inline("⏸️ Pause", b"clone_pause"), Button.inline("🛑 Stop", b"clone_stop")]
                            ] if bot_client else None
                        )
                        if new_msg:
                            status_msg = new_msg
                        await asyncio.sleep(5)
                        
                    if clone_state.get("status") in ("paused", "stopped"):
                        break
                else:
                    # Polite delay between iterations
                    await asyncio.sleep(3.0)
                    
            # Clean up the persistent separate progress message if it was created
            if file_status_msg:
                try:
                    await file_status_msg.delete()
                except Exception:
                    pass
                    
            # Successful complete
            if clone_state.get("status") == "running":
                clone_state["status"] = "completed"
                save_clone_state()
                await safe_edit_msg(
                    status_msg,
                    f"✅ **Cloning Completed Successfully!**\n\n"
                    f"📊 **Final Analytics:**\n"
                    f"• 📁 **Source Chat:** `{source_chat}`\n"
                    f"• 🎥 **Videos Cloned:** `{clone_state.get('total_cloned', 0)}`\n"
                    f"• ⏩ **Skipped (No Media):** `{clone_state.get('total_skipped', 0)}`\n\n"
                    f"All messages in this channel have been processed!",
                    buttons=None
                )
                # Remove clone state file on success
                if os.path.exists(STATE_FILE):
                    os.remove(STATE_FILE)
                clone_state.clear()
                
        except Exception as inner_e:
            # Clean up progress message if exception raised inside the iteration loop
            if file_status_msg:
                try:
                    await file_status_msg.delete()
                except Exception:
                    pass
            raise inner_e
            
    except Exception as e:
        import traceback
        tb_str = traceback.format_exc()
        logger.error(f"Error in cloning loop:\n{tb_str}")
        clone_state["status"] = "paused"
        save_clone_state()
        
        # Limit traceback length to prevent Telegram message length limits (max 4096 chars)
        tb_display = tb_str[-1500:] if len(tb_str) > 1500 else tb_str
        
        error_text = (
            f"⚠️ **Cloning Interrupted by Error**\n\n"
            f"**Error:** `{str(e)}`\n\n"
            f"**Traceback:**\n```python\n{tb_display}\n```\n"
            f"Progress has been saved at Message ID `{clone_state.get('last_processed_id')}`.\n"
            f"You can resume anytime."
        )
        buttons = [
            [Button.inline("⏯️ Resume", b"clone_resume"), Button.inline("❌ Cancel", b"clone_cancel")]
        ] if bot_client else None

        try:
            await status_msg.edit(error_text, buttons=buttons)
        except Exception:
            try:
                await status_msg.respond(error_text, buttons=buttons)
            except Exception as ex:
                logger.error(f"Failed to send clone loop error fallback message: {ex}")

# Event Handler: Listen to all incoming messages from yourself in Saved Messages
@client.on(events.NewMessage(chats='me'))

async def handle_new_message(event):
    text = event.raw_text.strip()
    if not text:
        return
        
    # --- Command: Restart ---
    if text == "/restart":
        await event.reply("🔄 **Restarting SaveRestricted bot...**")
        await restart_bot()
        return

    # --- Command: Refresh ---
    if text == "/refresh":
        status = await event.reply("🔄 **Refreshing bot configuration & connection...**")
        await refresh_bot(status)
        return

    # --- Command: Batch Download ---
    if text.startswith("/batch"):
        parts = text.split()
        if len(parts) != 3:
            await event.reply(
                "❌ **Usage:** `/batch <start_link> <end_link>`\n\n"
                "Example:\n`/batch https://t.me/c/12345/10 https://t.me/c/12345/20`"
            )
            return
            
        start_link, end_link = parts[1], parts[2]
        
        start_parsed = parse_telegram_link(start_link)
        end_parsed = parse_telegram_link(end_link)
        
        if not start_parsed or not end_parsed:
            await event.reply("❌ **Error:** One or both links are invalid Telegram message URLs.")
            return
            
        is_priv_start, chat_start, start_id = start_parsed
        is_priv_end, chat_end, end_id = end_parsed
        
        if chat_start != chat_end:
            await event.reply("❌ **Error:** Both links must belong to the exact same channel/chat.")
            return
            
        # Ensure correct range order
        min_id = min(start_id, end_id)
        max_id = max(start_id, end_id)
        
        total_items = max_id - min_id + 1
        
        if total_items > 60:
            await event.reply("⚠️ **Warning:** Batch size is limited to 60 messages at once to avoid account safety bans.")
            return
            
        # Inform user starting batch
        status = await event.reply(f"🔄 **Starting Batch Download:** `{total_items}` messages in range `{min_id}` to `{max_id}`...")
        
        # Get Chat Entity
        try:
            if is_priv_start:
                peer = PeerChannel(chat_start)
                chat_entity = await client.get_entity(peer)
            else:
                chat_entity = await client.get_entity(chat_start)
        except Exception as e:
            await status.edit(f"❌ **Failed to access channel:** `{str(e)}`\nMake sure your account is a member of the channel.")
            return
            
        success_count = 0
        
        for idx, msg_id in enumerate(range(min_id, max_id + 1), start=1):
            logger.info(f"Batch processing message ID {msg_id} ({idx}/{total_items})")
            
            # Construct a dummy original link string for the message caption
            if is_priv_start:
                msg_link = f"https://t.me/c/{chat_start}/{msg_id}"
            else:
                msg_link = f"https://t.me/{chat_start}/{msg_id}"
                
            success = await download_and_upload_video(
                chat_entity, 
                msg_id, 
                status, 
                msg_link, 
                batch_info=(idx, total_items)
            )
            
            if success:
                success_count += 1
                
            # Add polite anti-flood sleep between files
            await asyncio.sleep(2.5)
            
        await status.edit(
            f"✅ **Batch Processing Completed!**\n\n"
            f"📊 **Results:** Successfully saved `{success_count}` of `{total_items}` videos."
        )
        return

    # --- Direct Single Link Processing ---
    parsed = parse_telegram_link(text)
    if parsed:
        is_private, chat_identifier, message_id = parsed
        
        # Create a status message to show live progress
        status = await event.reply("🔍 **Link detected!** Querying Telegram servers for media info...")
        
        # Get Chat Entity
        try:
            if is_private:
                # Private channels are accessed using PeerChannel object
                peer = PeerChannel(chat_identifier)
                chat_entity = await client.get_entity(peer)
            else:
                # Public channels are accessed directly via username string
                chat_entity = await client.get_entity(chat_identifier)
        except Exception as e:
            await status.edit(
                f"❌ **Access Denied / Error:** `{str(e)}`\n\n"
                f"Please ensure:\n"
                f"1. Your logged-in Telegram account is a member of this channel.\n"
                f"2. The link is completely correct."
            )
            return
            
        success = await download_and_upload_video(chat_entity, message_id, status, text)
        if success:
            await status.delete()  # Remove progress message on full success

async def handle_bot_message(event):
    global active_clone_task, clone_state
    if not event.is_private:
        return
        
    text = event.raw_text.strip()
    if not text:
        return
        
    # Command: /restart
    if text == "/restart":
        await event.reply("🔄 **Restarting SaveRestricted bot...**")
        await restart_bot()
        return

    # Command: /refresh
    if text == "/refresh":
        status = await event.reply("🔄 **Refreshing bot configuration & connection...**")
        await refresh_bot(status)
        return

    # Command: /clone
    if text.startswith("/clone"):
        parts = text.split(maxsplit=1)
        if len(parts) != 2:
            await event.reply(
                "❌ **Usage:** `/clone <link_of_any_message_of_the_channel>`\n\n"
                "Example:\n`/clone https://t.me/c/12345/100`"
            )
            return
            
        link = parts[1].strip()
        parsed = parse_telegram_link(link)
        if not parsed:
            await event.reply("❌ **Invalid Link:** Please provide a valid Telegram message link from the channel.")
            return
            
        is_private, chat_identifier, message_id = parsed
        
        # Check if clone is already running
        if active_clone_task and not active_clone_task.done() and clone_state.get("status") == "running":
            await event.reply("⚠️ **Another clone task is currently in progress.** Please pause or stop it first.")
            return
            
        # Load clone state and check if it's the same channel
        load_clone_state()
        
        dest_desc = "Saved Messages" if UPLOAD_CHAT_ENTITY == 'me' else getattr(UPLOAD_CHAT_ENTITY, 'title', str(UPLOAD_CHAT_ENTITY))
        
        if clone_state.get("source_chat_id") == str(chat_identifier):
            buttons = [
                [Button.inline("⏯️ Resume", b"clone_resume"), Button.inline("🆕 Start Fresh", b"clone_fresh")],
                [Button.inline("❌ Cancel", b"clone_cancel")]
            ]
            await event.reply(
                f"📂 **Existing Clone Progress Found!**\n\n"
                f"• **Source Chat:** `{chat_identifier}`\n"
                f"• **Last Processed ID:** `{clone_state.get('last_processed_id')}`\n"
                f"• **Videos Cloned:** `{clone_state.get('total_cloned', 0)}`\n"
                f"• **Skipped:** `{clone_state.get('total_skipped', 0)}`\n"
                f"• **Destination:** `{dest_desc}`\n\n"
                f"Would you like to resume from where you left off or start fresh from message ID `{message_id}`?",
                buttons=buttons
            )
        else:
            # Set new clone state
            clone_state.update({
                "source_chat_id": str(chat_identifier),
                "is_private": is_private,
                "start_msg_id": message_id,
                "last_processed_id": None,
                "total_cloned": 0,
                "total_skipped": 0,
                "status": "stopped",
                "batch_count": 0
            })
            save_clone_state()
            
            buttons = [
                [Button.inline("▶️ Start Clone", b"clone_start"), Button.inline("❌ Cancel", b"clone_cancel")]
            ]
            await event.reply(
                f"📥 **Ready to Clone Channel!**\n\n"
                f"• **Source Chat:** `{chat_identifier}`\n"
                f"• **Start Message ID:** `{message_id}`\n"
                f"• **Destination:** `{dest_desc}`\n\n"
                f"Click **Start Clone** to begin.",
                buttons=buttons
            )
            
    # Command: /help or /start
    elif text.startswith("/start") or text.startswith("/help"):
        dest_desc = "Saved Messages" if UPLOAD_CHAT_ENTITY == 'me' else getattr(UPLOAD_CHAT_ENTITY, 'title', str(UPLOAD_CHAT_ENTITY))
        await event.reply(
            "👋 **SaveRestricted Interactive Controller Bot**\n\n"
            "This bot acts as your interactive controller for cloning channels and downloading media.\n\n"
            "💡 **Commands:**\n"
            "• `/clone <message_link>` - Start interactive channel cloning starting from any message\n"
            "• `/restart` - Restart the bot process completely\n"
            "• `/refresh` - Reload configuration and refresh client connection\n"
            "• `/help` - Show this message\n\n"
            f"📥 **Current Upload Destination:** `{dest_desc}`"
        )

async def safe_edit_or_respond(event, text, buttons=None):
    try:
        return await event.edit(text, buttons=buttons)
    except FloodWaitError as e:
        logger.warning(f"Failed to edit callback message due to FloodWait ({e.seconds}s). Falling back to sending a new message.")
        return await event.respond(text, buttons=buttons)
    except Exception as e:
        logger.warning(f"Failed to edit callback message: {e}. Falling back to sending a new message.")
        try:
            return await event.respond(text, buttons=buttons)
        except Exception as ex:
            logger.error(f"Failed fallback respond call: {ex}")
            return None

async def handle_callback(event):
    global active_clone_task, clone_state
    data = event.data
    
    if data == b"clone_start":
        await event.answer("Starting clone loop...", cache_time=0)
        await safe_cancel_active_task()
        msg = await safe_edit_or_respond(event, "🔄 **Initializing clone process...**")
        if msg:
            active_clone_task = asyncio.create_task(run_clone_loop(msg))
        
    elif data == b"clone_resume":
        await event.answer("Resuming clone...", cache_time=0)
        await safe_cancel_active_task()
        # Set running state
        clone_state["status"] = "running"
        save_clone_state()
        msg = await safe_edit_or_respond(event, "🔄 **Resuming clone process...**")
        if msg:
            active_clone_task = asyncio.create_task(run_clone_loop(msg))
        
    elif data == b"clone_fresh":
        await event.answer("Starting fresh clone...", cache_time=0)
        await safe_cancel_active_task()
        # Clear processing state
        clone_state.update({
            "last_processed_id": None,
            "total_cloned": 0,
            "total_skipped": 0,
            "status": "stopped",
            "batch_count": 0
        })
        save_clone_state()
        msg = await safe_edit_or_respond(event, "🔄 **Starting fresh clone process...**")
        if msg:
            active_clone_task = asyncio.create_task(run_clone_loop(msg))
        
    elif data == b"clone_pause":
        await event.answer("Pausing clone...", cache_time=0)
        clone_state["status"] = "paused"
        save_clone_state()
        await asyncio.sleep(1)
        
        dest_desc = "Saved Messages" if UPLOAD_CHAT_ENTITY == 'me' else getattr(UPLOAD_CHAT_ENTITY, 'title', str(UPLOAD_CHAT_ENTITY))
        await safe_edit_or_respond(
            event,
            f"⏸️ **Cloning Paused**\n\n"
            f"• **Source Chat:** `{clone_state.get('source_chat_id')}`\n"
            f"• **Last Processed ID:** `{clone_state.get('last_processed_id')}`\n"
            f"• **Videos Cloned:** `{clone_state.get('total_cloned', 0)}`\n"
            f"• **Skipped:** `{clone_state.get('total_skipped', 0)}`\n"
            f"• **Destination:** `{dest_desc}`\n\n"
            f"You can resume the process when you're ready.",
            buttons=[
                [Button.inline("⏯️ Resume", b"clone_resume"), Button.inline("❌ Cancel", b"clone_cancel")]
            ]
        )
        
    elif data == b"clone_stop":
        await event.answer("Stopping clone...", cache_time=0)
        clone_state["status"] = "stopped"
        save_clone_state()
        await asyncio.sleep(1)
        
        dest_desc = "Saved Messages" if UPLOAD_CHAT_ENTITY == 'me' else getattr(UPLOAD_CHAT_ENTITY, 'title', str(UPLOAD_CHAT_ENTITY))
        await safe_edit_or_respond(
            event,
            f"🛑 **Cloning Stopped & Progress Saved**\n\n"
            f"• **Source Chat:** `{clone_state.get('source_chat_id')}`\n"
            f"• **Last Message ID:** `{clone_state.get('last_processed_id')}`\n"
            f"• **Videos Cloned:** `{clone_state.get('total_cloned', 0)}`\n"
            f"• **Skipped:** `{clone_state.get('total_skipped', 0)}`\n"
            f"• **Destination:** `{dest_desc}`\n\n"
            f"The progress has been saved. You can resume at this point later.",
            buttons=[
                [Button.inline("⏯️ Resume", b"clone_resume"), Button.inline("❌ Cancel", b"clone_cancel")]
            ]
        )
        
    elif data == b"clone_cancel":
        await event.answer("Clone state cleared.", cache_time=0)
        clone_state["status"] = "stopped"
        save_clone_state()
        if os.path.exists(STATE_FILE):
            try:
                os.remove(STATE_FILE)
            except Exception:
                pass
        clone_state.clear()
        await safe_edit_or_respond(event, "❌ **Cloning Cancelled.** Persistent state has been cleared.")

def register_bot_handlers(bot):
    bot.add_event_handler(handle_bot_message, events.NewMessage(incoming=True))
    bot.add_event_handler(handle_callback, events.CallbackQuery())

# Web server to satisfy Render's port binding requirement for Free Web Services
async def start_web_server():
    port = os.getenv("PORT")
    if not port:
        logger.info("PORT environment variable not set. Skipping web server.")
        return
        
    try:
        port_num = int(port)
    except ValueError:
        logger.error(f"Invalid PORT value: {port}")
        return

    logger.info(f"Starting lightweight web server on port {port_num} to keep Render happy...")
    
    async def handle_ping(reader, writer):
        try:
            await reader.read(256)
        except Exception:
            pass
            
        response = (
            "HTTP/1.1 200 OK\r\n"
            "Content-Type: text/plain\r\n"
            "Content-Length: 12\r\n"
            "Connection: close\r\n"
            "\r\n"
            "Bot is alive"
        )
        try:
            writer.write(response.encode('utf-8'))
            await writer.drain()
        except Exception:
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    try:
        server = await asyncio.start_server(handle_ping, '0.0.0.0', port_num)
        logger.info(f"Web server successfully started on port {port_num}")
        # Keep running the server forever
        async with server:
            await server.serve_forever()
    except Exception as e:
        logger.error(f"Failed to start web server on port {port_num}: {e}")

# Background task to self-ping the Render Web Service and prevent spin-down/sleep
async def self_ping_loop():
    url = os.getenv("RENDER_EXTERNAL_URL")
    if not url:
        logger.info("RENDER_EXTERNAL_URL environment variable is not set. Skipping self-ping.")
        return
        
    logger.info(f"Self-ping loop started. Will ping '{url}' every 10 minutes to stay awake.")
    while True:
        # Sleep for 10 minutes (600 seconds)
        await asyncio.sleep(600)
        try:
            logger.info(f"Sending self-ping to {url}...")
            # Use run_in_executor to avoid blocking the event loop
            loop = asyncio.get_running_loop()
            def do_ping():
                try:
                    with urllib.request.urlopen(url, timeout=15) as response:
                        response.read()
                except Exception as err:
                    logger.warning(f"Error in do_ping request: {err}")
            await loop.run_in_executor(None, do_ping)
            logger.info("Self-ping completed successfully.")
        except Exception as e:
            logger.warning(f"Self-ping loop encountered an error: {e}")

async def main():
    global bot_client
    
    # Start Render port binder if PORT is defined
    port = os.getenv("PORT")
    if port:
        asyncio.create_task(start_web_server())
        
    # Start self-ping loop to prevent Render Free Tier from sleeping
    if os.getenv("RENDER_EXTERNAL_URL"):
        asyncio.create_task(self_ping_loop())
        
    print("="*60)
    print("      🚀 SaveRestricted Telegram Userbot Initialization 🚀      ")
    print("="*60)
    print("Connecting to Telegram Userbot...")
    
    # client.start() automatically handles OTP login in the terminal if session doesn't exist
    try:
        await client.start()
    except FloodWaitError as e:
        print("\n" + "="*60)
        print(f"⚠️ WARNING: Userbot client hit Telegram FloodWait during startup!")
        print(f"Need to wait {e.seconds} seconds before userbot can connect.")
        print("Sleeping to satisfy the rate limit...")
        print("="*60 + "\n")
        await asyncio.sleep(e.seconds)
        await client.start()
    
    me = await client.get_me()
    print("\n" + "="*60)
    print(f"✅ Connection Established!")
    print(f"👤 Logged in as Userbot: {me.first_name} (@{me.username or 'No Username'})")
    print(f"🆔 Account ID: {me.id}")
    print("="*60)
    
    # Load persistent clone state
    load_clone_state()
    
    # Resolve the destination private channel or Chat entity
    await resolve_upload_chat()
    
    bot_token = os.getenv("BOT_TOKEN")
    tasks = []
    
    if bot_token:
        print("Connecting to Telegram Controller Bot...")
        bot_client = TelegramClient("bot", API_ID, API_HASH)
        try:
            await bot_client.start(bot_token=bot_token)
        except FloodWaitError as e:
            print("\n" + "="*60)
            print(f"⚠️ WARNING: Bot client hit Telegram FloodWait during startup!")
            print(f"Need to wait {e.seconds} seconds before bot can connect.")
            print("Sleeping to satisfy the rate limit...")
            print("="*60 + "\n")
            await asyncio.sleep(e.seconds)
            await bot_client.start(bot_token=bot_token)
            
        bot_me = await bot_client.get_me()
        
        print("\n" + "="*60)
        print(f"✅ Bot Connection Established!")
        print(f"👤 Logged in as Bot: {bot_me.first_name} (@{bot_me.username})")
        print("="*60)
        
        # Register Bot Commands and Keyboard Handlers
        register_bot_handlers(bot_client)
        tasks.append(bot_client.run_until_disconnected())
        
    tasks.append(client.run_until_disconnected())
    
    dest_desc = "Saved Messages" if UPLOAD_CHAT_ENTITY == 'me' else getattr(UPLOAD_CHAT_ENTITY, 'title', str(UPLOAD_CHAT_ENTITY))
    print(f" 📥 Userbot is actively running and listening in Saved Messages!")
    print(f" 🎯 Target Upload Chat: {dest_desc}")
    if bot_token:
        print(" 🤖 Bot is also running and ready for interactive PM cloning!")
    else:
        print(" 💡 TIP: Set BOT_TOKEN in .env to enable the premium interactive clone command with inline buttons.")
    print("="*60 + "\n")
    
    # Concurrently run both clients
    await asyncio.gather(*tasks)

if __name__ == "__main__":
    # Create event loop and run main
    asyncio.run(main())
