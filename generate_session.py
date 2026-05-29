import os
import sys
from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.sessions import StringSession

def main():
    # Load environment variables
    load_dotenv()

    API_ID = os.getenv("API_ID")
    API_HASH = os.getenv("API_HASH")

    print("="*80)
    print("      🔑 SaveRestricted Telethon String Session Generator 🔑      ")
    print("="*80)

    if not API_ID or not API_HASH:
        print("\n❌ ERROR: Missing API_ID or API_HASH in your local .env file!")
        print("Please ensure your local .env file is set up before running this generator.")
        print("Refer to .env.example for structure.\n")
        sys.exit(1)

    try:
        API_ID = int(API_ID)
    except ValueError:
        print("\n❌ ERROR: API_ID in your .env must be an integer!\n")
        sys.exit(1)

    print("\nStarting Telethon client...")
    print("👉 If prompted, please enter your Telegram Phone Number, the OTP code,")
    print("   and your Two-Step Verification (2FA) password if you have one enabled.\n")

    try:
        # Initializing client with StringSession() generates a fresh in-memory session.
        # Once authenticated, we can extract the session string.
        client = TelegramClient(StringSession(), API_ID, API_HASH)
        
        with client:
            session_str = client.session.save()
            print("\n" + "="*80)
            print("🎉 SUCCESS! Your Telethon String Session has been generated!")
            print("="*80)
            print("\n👇 Copy the entire session string below (single long line):")
            print(f"\n{session_str}\n")
            print("="*80)
            print("⚠️  SECURITY WARNING: Treat this string like a password. Never share it,")
            print("   and NEVER commit it to GitHub. Anyone with this string has FULL access")
            print("   to your Telegram account.")
            print("="*80 + "\n")
            
    except Exception as e:
        print(f"\n❌ An error occurred during session generation: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
