import os
import asyncio
import logging
import sqlite3
import json
from datetime import datetime, timedelta
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler, CallbackQueryHandler
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import requests
from typing import Dict, List

# Set up logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Conversation states
MAIN_MENU, SETUP_BOT, SETUP_CHANNELS, BULK_POSTS, POSTS_PER_DAY = range(5)

# Your bot token
BOT_TOKEN = "8217960293:AAEedCXGMiAHIQavwd-jpFJZqpXvRXBqCLA"

class SMMBot:
    def __init__(self):
        self.setup_database()
        self.scheduler = BackgroundScheduler()
        self.scheduler.start()
        logger.info("🔄 Scheduler started")
    
    def setup_database(self):
        """Initialize SQLite database"""
        self.conn = sqlite3.connect('smm_bot.db', check_same_thread=False)
        self.cursor = self.conn.cursor()
        
        # Create tables
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                bot_token TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS channels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                channel_username TEXT,
                channel_title TEXT,
                is_active BOOLEAN DEFAULT TRUE,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                content_type TEXT,
                file_id TEXT,
                caption TEXT,
                status TEXT DEFAULT 'pending', -- pending, posted
                posted_at DATETIME,
                target_channels TEXT, -- JSON array of channel IDs
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS settings (
                user_id INTEGER PRIMARY KEY,
                posts_per_day INTEGER DEFAULT 1,
                repost_enabled BOOLEAN DEFAULT FALSE,
                post_times TEXT DEFAULT '["09:00"]', -- JSON array of times
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        self.conn.commit()
        logger.info("✅ Database initialized")
    
    def get_main_keyboard(self):
        """Create main menu keyboard"""
        keyboard = [
            [KeyboardButton("🤖 Setup Bot Token"), KeyboardButton("📢 Setup Channels")],
            [KeyboardButton("📤 Add Bulk Posts"), KeyboardButton("📊 Posts Per Day")],
            [KeyboardButton("✅ My Posted Posts"), KeyboardButton("⏳ Pending Posts")],
            [KeyboardButton("🔄 Repost Mode: OFF"), KeyboardButton("🎯 Target Channels")]
        ]
        return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, input_field_placeholder="Choose an option...")
    
    def update_repost_button(self, user_id):
        """Update repost button text based on current setting"""
        self.cursor.execute(
            'SELECT repost_enabled FROM settings WHERE user_id = ?', 
            (user_id,)
        )
        result = self.cursor.fetchone()
        repost_enabled = result[0] if result else False
        
        keyboard = [
            [KeyboardButton("🤖 Setup Bot Token"), KeyboardButton("📢 Setup Channels")],
            [KeyboardButton("📤 Add Bulk Posts"), KeyboardButton("📊 Posts Per Day")],
            [KeyboardButton("✅ My Posted Posts"), KeyboardButton("⏳ Pending Posts")],
            [KeyboardButton(f"🔄 Repost Mode: {'ON' if repost_enabled else 'OFF'}"), KeyboardButton("🎯 Target Channels")]
        ]
        return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Start command - main menu"""
        user_id = update.effective_user.id
        
        # Initialize user settings if not exists
        self.cursor.execute(
            'INSERT OR IGNORE INTO settings (user_id) VALUES (?)',
            (user_id,)
        )
        self.conn.commit()
        
        welcome_text = """
🤖 *SMM Auto-Post Master* 🚀

*Complete Automation - Setup Once, Run Forever!*

🎯 *How It Works:*
1. 🤖 *Setup Bot Token* - Your posting bot
2. 📢 *Setup Channels* - Target channels/groups  
3. 📤 *Add Bulk Posts* - Upload multiple posts
4. 📊 *Set Posts Per Day* - Daily posting frequency
5. ✅ *Monitor* - Track posted/pending posts
6. 🔄 *Repost Mode* - Auto-repeat when finished

⚡ *Features:*
• Multiple channels support
• Bulk media uploading
• Smart scheduling
• Repost automation
• 100% hands-free operation

*Get started by setting up your bot token!* 👇
        """
        
        await update.message.reply_text(
            welcome_text,
            reply_markup=self.get_main_keyboard(),
            parse_mode='Markdown'
        )
        return MAIN_MENU
    
    async def setup_bot_token(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Setup bot token"""
        await update.message.reply_text(
            "🤖 *Setup Your Bot Token:*\n\n"
            "1. Go to @BotFather\n" 
            "2. Create new bot or use existing\n"
            "3. Copy the bot token\n"
            "4. Send it to me\n\n"
            "*Format:* `1234567890:ABCdefGHIjklMNOPqrStuVWXyz`\n\n"
            "🔐 *This bot will be used to post to your channels*",
            parse_mode='Markdown',
            reply_markup=self.get_main_keyboard()
        )
        return SETUP_BOT
    
    async def handle_bot_token(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Process bot token"""
        user_id = update.effective_user.id
        bot_token = update.message.text.strip()
        
        # Validate token format
        if ':' not in bot_token or len(bot_token) < 20:
            await update.message.reply_text(
                "❌ *Invalid bot token format!*\n\n"
                "Please send valid token like:\n"
                "`1234567890:ABCdefGHIjklMNOPqrStuVWXyz`",
                parse_mode='Markdown'
            )
            return SETUP_BOT
        
        # Test the bot token
        try:
            test_url = f"https://api.telegram.org/bot{bot_token}/getMe"
            response = requests.get(test_url, timeout=10)
            
            if response.status_code == 200:
                bot_info = response.json()
                bot_username = bot_info['result']['username']
                
                # Save to database
                self.cursor.execute('''
                    INSERT OR REPLACE INTO users (user_id, bot_token) 
                    VALUES (?, ?)
                ''', (user_id, bot_token))
                self.conn.commit()
                
                await update.message.reply_text(
                    f"✅ *Bot Token Verified!*\n\n"
                    f"🤖 Bot: @{bot_username}\n"
                    f"🔐 Token: `{bot_token[:10]}...`\n\n"
                    f"*Now setup your target channels!* 📢",
                    parse_mode='Markdown',
                    reply_markup=self.get_main_keyboard()
                )
                return MAIN_MENU
            else:
                await update.message.reply_text(
                    "❌ *Invalid bot token!*\n\n"
                    "Please check:\n"
                    "• Token is correct\n"
                    "• Bot exists\n"
                    "• Try again",
                    parse_mode='Markdown'
                )
                return SETUP_BOT
                
        except Exception as e:
            logger.error(f"Token validation error: {e}")
            await update.message.reply_text(
                "❌ *Error validating token!*\n\n"
                "Please check your token and try again.",
                parse_mode='Markdown'
            )
            return SETUP_BOT
    
    async def setup_channels(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Setup target channels"""
        user_id = update.effective_user.id
        
        # Check if bot token is set
        self.cursor.execute(
            'SELECT bot_token FROM users WHERE user_id = ?', 
            (user_id,)
        )
        result = self.cursor.fetchone()
        
        if not result or not result[0]:
            await update.message.reply_text(
                "❌ *Please setup bot token first!*\n\n"
                "I need your bot token to access channels.",
                parse_mode='Markdown',
                reply_markup=self.get_main_keyboard()
            )
            return MAIN_MENU
        
        await update.message.reply_text(
            "📢 *Setup Target Channels:*\n\n"
            "Send channel usernames (one per line):\n\n"
            "*Format:*\n"
            "• Public: `@channelname`\n" 
            "• Private: `-1001234567890`\n\n"
            "*Example:*\n"
            "@my_channel\n"
            "@another_channel\n"
            "-1001234567890\n\n"
            "🔒 *Make sure your bot is admin in all channels!*",
            parse_mode='Markdown'
        )
        return SETUP_CHANNELS
    
    async def handle_channels(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Process channel usernames"""
        user_id = update.effective_user.id
        channels_text = update.message.text.strip()
        
        # Get bot token
        self.cursor.execute(
            'SELECT bot_token FROM users WHERE user_id = ?', 
            (user_id,)
        )
        result = self.cursor.fetchone()
        bot_token = result[0]
        
        channels = [ch.strip() for ch in channels_text.split('\n') if ch.strip()]
        valid_channels = []
        failed_channels = []
        
        for channel in channels:
            # Validate channel access
            try:
                test_url = f"https://api.telegram.org/bot{bot_token}/getChat"
                payload = {"chat_id": channel}
                response = requests.post(test_url, json=payload, timeout=10)
                
                if response.status_code == 200:
                    chat_info = response.json()
                    chat_title = chat_info['result'].get('title', 'Unknown')
                    
                    # Check if bot is admin
                    admin_url = f"https://api.telegram.org/bot{bot_token}/getChatAdministrators"
                    admin_response = requests.post(admin_url, json={"chat_id": channel}, timeout=10)
                    
                    if admin_response.status_code == 200:
                        admins = admin_response.json()['result']
                        bot_id = f"@{bot_token.split(':')[0]}"
                        is_admin = any(str(admin['user']['id']) == bot_token.split(':')[0] for admin in admins)
                        
                        if is_admin:
                            # Save channel
                            self.cursor.execute('''
                                INSERT OR REPLACE INTO channels 
                                (user_id, channel_username, channel_title, is_active) 
                                VALUES (?, ?, ?, ?)
                            ''', (user_id, channel, chat_title, True))
                            valid_channels.append(f"✅ {chat_title} ({channel})")
                        else:
                            failed_channels.append(f"❌ {chat_title} - Bot not admin")
                    else:
                        failed_channels.append(f"❌ {channel} - Cannot check admin")
                else:
                    failed_channels.append(f"❌ {channel} - Cannot access")
                    
            except Exception as e:
                logger.error(f"Channel validation error: {e}")
                failed_channels.append(f"❌ {channel} - Error")
        
        self.conn.commit()
        
        response_text = "📢 *Channel Setup Results:*\n\n"
        
        if valid_channels:
            response_text += "*✅ Connected Channels:*\n" + "\n".join(valid_channels) + "\n\n"
        
        if failed_channels:
            response_text += "*❌ Failed Channels:*\n" + "\n".join(failed_channels) + "\n\n"
        
        response_text += f"*Total: {len(valid_channels)} successful, {len(failed_channels)} failed*\n\n"
        
        if valid_channels:
            response_text += "🎯 *Next:* Add bulk posts or set posting frequency!"
        
        await update.message.reply_text(
            response_text,
            parse_mode='Markdown',
            reply_markup=self.get_main_keyboard()
        )
        return MAIN_MENU
    
    async def add_bulk_posts(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Add bulk posts"""
        user_id = update.effective_user.id
        
        # Check setup
        self.cursor.execute(
            'SELECT COUNT(*) FROM channels WHERE user_id = ? AND is_active = TRUE',
            (user_id,)
        )
        channel_count = self.cursor.fetchone()[0]
        
        if channel_count == 0:
            await update.message.reply_text(
                "❌ *No channels setup!*\n\n"
                "Please setup channels first before adding posts.",
                parse_mode='Markdown',
                reply_markup=self.get_main_keyboard()
            )
            return MAIN_MENU
        
        await update.message.reply_text(
            "📤 *Add Bulk Posts:*\n\n"
            "You can now send multiple photos/videos:\n\n"
            "• Send as many as you want\n"
            "• Add captions if needed\n"
            "• All will be added to queue\n"
            "• Posts will auto-distribute to channels\n\n"
            "*Supported:* Photos, Videos, GIFs\n"
            "*Start sending now...*",
            parse_mode='Markdown'
        )
        return BULK_POSTS
    
    async def handle_bulk_media(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Process bulk media uploads"""
        user_id = update.effective_user.id
        
        try:
            if update.message.photo:
                # Handle photo
                file_id = update.message.photo[-1].file_id
                caption = update.message.caption or ""
                content_type = "photo"
                
            elif update.message.video:
                # Handle video
                file_id = update.message.video.file_id
                caption = update.message.caption or ""
                content_type = "video"
                
            elif update.message.document:
                # Handle document (could be GIF)
                file_id = update.message.document.file_id
                caption = update.message.caption or ""
                content_type = "document"
            else:
                await update.message.reply_text(
                    "❌ Unsupported media type!",
                    reply_markup=self.get_main_keyboard()
                )
                return BULK_POSTS
            
            # Get active channels for this user
            self.cursor.execute(
                'SELECT id FROM channels WHERE user_id = ? AND is_active = TRUE',
                (user_id,)
            )
            channels = self.cursor.fetchall()
            channel_ids = [str(ch[0]) for ch in channels]
            
            # Save post
            self.cursor.execute('''
                INSERT INTO posts 
                (user_id, content_type, file_id, caption, target_channels) 
                VALUES (?, ?, ?, ?, ?)
            ''', (user_id, content_type, file_id, caption, json.dumps(channel_ids)))
            self.conn.commit()
            
            # Get queue count
            self.cursor.execute(
                'SELECT COUNT(*) FROM posts WHERE user_id = ? AND status = "pending"',
                (user_id,)
            )
            queue_count = self.cursor.fetchone()[0]
            
            await update.message.reply_text(
                f"✅ *Media Added to Queue!*\n\n"
                f"📊 Total Pending Posts: *{queue_count}*\n"
                f"🎯 Target Channels: *{len(channel_ids)}*\n\n"
                f"Keep sending more or go back to main menu.",
                parse_mode='Markdown'
            )
            
            # Auto-schedule if not already scheduled
            self.schedule_user_posts(user_id)
            
        except Exception as e:
            logger.error(f"Media processing error: {e}")
            await update.message.reply_text(
                "❌ Error processing media. Please try again.",
                reply_markup=self.get_main_keyboard()
            )
        
        return BULK_POSTS
    
    async def posts_per_day(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Set posts per day"""
        user_id = update.effective_user.id
        
        # Get current setting
        self.cursor.execute(
            'SELECT posts_per_day FROM settings WHERE user_id = ?',
            (user_id,)
        )
        result = self.cursor.fetchone()
        current_ppd = result[0] if result else 1
        
        keyboard = [
            [InlineKeyboardButton("1 Post", callback_data="ppd_1")],
            [InlineKeyboardButton("2 Posts", callback_data="ppd_2")],
            [InlineKeyboardButton("3 Posts", callback_data="ppd_3")],
            [InlineKeyboardButton("4 Posts", callback_data="ppd_4")],
            [InlineKeyboardButton("5 Posts", callback_data="ppd_5")],
            [InlineKeyboardButton("6 Posts", callback_data="ppd_6")],
            [InlineKeyboardButton("8 Posts", callback_data="ppd_8")],
            [InlineKeyboardButton("10 Posts", callback_data="ppd_10")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            f"📊 *Set Posts Per Day:*\n\n"
            f"Current: *{current_ppd} posts daily*\n\n"
            f"Choose how many posts to send each day:\n\n"
            f"💡 *Recommendation:*\n"
            f"• 1-3 posts for normal channels\n"
            f"• 4-6 posts for active channels\n"
            f"• 8-10 posts for high-frequency\n\n"
            f"Select your daily posting frequency:",
            parse_mode='Markdown',
            reply_markup=reply_markup
        )
        return POSTS_PER_DAY
    
    async def handle_ppd_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle posts per day callback"""
        query = update.callback_query
        await query.answer()
        
        user_id = query.from_user.id
        ppd = int(query.data.split('_')[1])
        
        # Update setting
        self.cursor.execute(
            'UPDATE settings SET posts_per_day = ? WHERE user_id = ?',
            (ppd, user_id)
        )
        self.conn.commit()
        
        # Reschedule posts
        self.schedule_user_posts(user_id)
        
        await query.edit_message_text(
            f"✅ *Posts Per Day Updated!*\n\n"
            f"📊 New setting: *{ppd} posts daily*\n\n"
            f"Posts will be automatically distributed throughout the day.\n"
            f"Next posts will use this frequency.",
            parse_mode='Markdown'
        )
        return MAIN_MENU
    
    async def my_posted_posts(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show posted posts"""
        user_id = update.effective_user.id
        
        self.cursor.execute('''
            SELECT p.id, p.content_type, p.caption, p.posted_at, c.channel_title
            FROM posts p
            LEFT JOIN channels c ON json_extract(p.target_channels, '$[0]') = c.id
            WHERE p.user_id = ? AND p.status = 'posted'
            ORDER BY p.posted_at DESC
            LIMIT 20
        ''', (user_id,))
        
        posts = self.cursor.fetchall()
        
        if not posts:
            await update.message.reply_text(
                "📭 *No Posted Posts Yet!*\n\n"
                "Your posted posts will appear here.\n"
                "Add some posts and they'll show up after posting!",
                parse_mode='Markdown',
                reply_markup=self.get_main_keyboard()
            )
            return MAIN_MENU
        
        response_text = "✅ *My Posted Posts:*\n\n"
        
        for i, post in enumerate(posts, 1):
            post_id, content_type, caption, posted_at, channel_title = post
            emoji = "🖼️" if content_type == "photo" else "🎥" if content_type == "video" else "📄"
            time_str = datetime.strptime(posted_at, '%Y-%m-%d %H:%M:%S').strftime('%m/%d %H:%M')
            
            caption_preview = caption[:30] + "..." if caption and len(caption) > 30 else caption or "No caption"
            response_text += f"{i}. {emoji} {caption_preview}\n   📅 {time_str} | 📢 {channel_title or 'Unknown'}\n\n"
        
        self.cursor.execute(
            'SELECT COUNT(*) FROM posts WHERE user_id = ? AND status = "posted"',
            (user_id,)
        )
        total_posted = self.cursor.fetchone()[0]
        
        response_text += f"*Total Posted: {total_posted} posts*"
        
        await update.message.reply_text(
            response_text,
            parse_mode='Markdown',
            reply_markup=self.get_main_keyboard()
        )
        return MAIN_MENU
    
    async def pending_posts(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show pending posts"""
        user_id = update.effective_user.id
        
        self.cursor.execute('''
            SELECT id, content_type, caption, created_at 
            FROM posts 
            WHERE user_id = ? AND status = 'pending'
            ORDER BY id
        ''', (user_id,))
        
        posts = self.cursor.fetchall()
        
        if not posts:
            await update.message.reply_text(
                "🎉 *No Pending Posts!*\n\n"
                "All posts have been posted!\n"
                "Add more posts or enable repost mode to auto-repeat.",
                parse_mode='Markdown',
                reply_markup=self.get_main_keyboard()
            )
            return MAIN_MENU
        
        response_text = "⏳ *Pending Posts:*\n\n"
        
        for i, post in enumerate(posts, 1):
            post_id, content_type, caption, created_at = post
            emoji = "🖼️" if content_type == "photo" else "🎥" if content_type == "video" else "📄"
            
            caption_preview = caption[:30] + "..." if caption and len(caption) > 30 else caption or "No caption"
            response_text += f"{i}. {emoji} {caption_preview}\n"
        
        response_text += f"\n*Total Pending: {len(posts)} posts*"
        
        # Add scheduling info
        self.cursor.execute(
            'SELECT posts_per_day FROM settings WHERE user_id = ?',
            (user_id,)
        )
        ppd = self.cursor.fetchone()[0] if self.cursor.fetchone() else 1
        
        days_remaining = len(posts) / ppd
        response_text += f"\n*Estimated completion: {days_remaining:.1f} days*"
        
        await update.message.reply_text(
            response_text,
            parse_mode='Markdown',
            reply_markup=self.get_main_keyboard()
        )
        return MAIN_MENU
    
    async def toggle_repost_mode(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Toggle repost mode"""
        user_id = update.effective_user.id
        
        # Get current setting
        self.cursor.execute(
            'SELECT repost_enabled FROM settings WHERE user_id = ?',
            (user_id,)
        )
        result = self.cursor.fetchone()
        current_setting = result[0] if result else False
        
        # Toggle setting
        new_setting = not current_setting
        
        self.cursor.execute(
            'UPDATE settings SET repost_enabled = ? WHERE user_id = ?',
            (new_setting, user_id)
        )
        self.conn.commit()
        
        status = "ON" if new_setting else "OFF"
        status_emoji = "🟢" if new_setting else "🔴"
        
        if new_setting:
            message = (
                f"🔄 *Repost Mode: ON* ✅\n\n"
                f"*How it works:*\n"
                f"• When all posts are posted\n"
                f"• System will reset them to pending\n"
                f"• Auto-restart posting cycle\n"
                f"• Infinite loop forever! ♾️\n\n"
                f"🔁 *Your content will auto-repeat forever*"
            )
        else:
            message = (
                f"🔄 *Repost Mode: OFF* ❌\n\n"
                f"Posting will stop when queue is empty."
            )
        
        await update.message.reply_text(
            message,
            parse_mode='Markdown',
            reply_markup=self.update_repost_button(user_id)
        )
        return MAIN_MENU
    
    async def target_channels(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show target channels"""
        user_id = update.effective_user.id
        
        self.cursor.execute('''
            SELECT channel_username, channel_title, is_active 
            FROM channels 
            WHERE user_id = ? 
            ORDER BY created_at
        ''', (user_id,))
        
        channels = self.cursor.fetchall()
        
        if not channels:
            await update.message.reply_text(
                "❌ *No Channels Setup!*\n\n"
                "Please setup channels first to start posting.",
                parse_mode='Markdown',
                reply_markup=self.get_main_keyboard()
            )
            return MAIN_MENU
        
        response_text = "🎯 *Target Channels:*\n\n"
        
        active_count = 0
        for channel in channels:
            username, title, is_active = channel
            status = "🟢 ACTIVE" if is_active else "🔴 INACTIVE"
            if is_active:
                active_count += 1
            response_text += f"• {title}\n  {username} - {status}\n\n"
        
        response_text += f"*Total: {active_count} active channels*"
        
        await update.message.reply_text(
            response_text,
            parse_mode='Markdown',
            reply_markup=self.get_main_keyboard()
        )
        return MAIN_MENU
    
    def schedule_user_posts(self, user_id):
        """Schedule posts for user"""
        # Remove existing jobs
        jobs = self.scheduler.get_jobs()
        for job in jobs:
            if job.id.startswith(f"user_{user_id}"):
                job.remove()
        
        # Get user settings
        self.cursor.execute(
            'SELECT posts_per_day, repost_enabled FROM settings WHERE user_id = ?',
            (user_id,)
        )
        result = self.cursor.fetchone()
        if not result:
            return
        
        posts_per_day, repost_enabled = result
        
        if posts_per_day == 0:
            return
        
        # Calculate posting times (spread throughout day)
        times = self.calculate_post_times(posts_per_day)
        
        # Schedule posts
        for i, time_str in enumerate(times):
            hour, minute = map(int, time_str.split(':'))
            
            trigger = CronTrigger(hour=hour, minute=minute)
            self.scheduler.add_job(
                self.post_scheduled_content,
                trigger,
                args=[user_id],
                id=f"user_{user_id}_post_{i}",
                replace_existing=True
            )
        
        logger.info(f"Scheduled {posts_per_day} posts daily for user {user_id}")
    
    def calculate_post_times(self, posts_per_day):
        """Calculate optimal posting times"""
        if posts_per_day == 1:
            return ["09:00"]
        elif posts_per_day == 2:
            return ["09:00", "17:00"]
        elif posts_per_day == 3:
            return ["09:00", "14:00", "19:00"]
        elif posts_per_day == 4:
            return ["08:00", "12:00", "16:00", "20:00"]
        elif posts_per_day == 5:
            return ["08:00", "11:00", "14:00", "17:00", "20:00"]
        elif posts_per_day == 6:
            return ["08:00", "10:30", "13:00", "15:30", "18:00", "20:30"]
        else:  # 8-10 posts
            return [f"{h:02d}:{m:02d}" for h in range(8, 22) for m in [0, 30]][:posts_per_day]
    
    def post_scheduled_content(self, user_id):
        """Post scheduled content"""
        try:
            # Get next pending post
            self.cursor.execute('''
                SELECT id, content_type, file_id, caption, target_channels 
                FROM posts 
                WHERE user_id = ? AND status = 'pending' 
                ORDER BY id LIMIT 1
            ''', (user_id,))
            
            result = self.cursor.fetchone()
            if not result:
                # No posts left - check repost mode
                self.handle_repost_mode(user_id)
                return
            
            post_id, content_type, file_id, caption, target_channels_json = result
            target_channels = json.loads(target_channels_json)
            
            # Get bot token
            self.cursor.execute(
                'SELECT bot_token FROM users WHERE user_id = ?',
                (user_id,)
            )
            bot_result = self.cursor.fetchone()
            if not bot_result:
                return
            
            bot_token = bot_result[0]
            
            # Post to each target channel
            success_count = 0
            for channel_id in target_channels:
                # Get channel info
                self.cursor.execute(
                    'SELECT channel_username FROM channels WHERE id = ? AND is_active = TRUE',
                    (channel_id,)
                )
                channel_result = self.cursor.fetchone()
                if not channel_result:
                    continue
                
                channel_username = channel_result[0]
                
                # Post to channel
                if self.send_to_channel(bot_token, channel_username, content_type, file_id, caption):
                    success_count += 1
            
            if success_count > 0:
                # Mark as posted
                self.cursor.execute(
                    'UPDATE posts SET status = "posted", posted_at = ? WHERE id = ?',
                    (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), post_id)
                )
                self.conn.commit()
                logger.info(f"Posted content {post_id} to {success_count} channels for user {user_id}")
            
        except Exception as e:
            logger.error(f"Posting error for user {user_id}: {e}")
    
    def handle_repost_mode(self, user_id):
        """Handle repost mode when queue is empty"""
        try:
            # Check if repost mode is enabled
            self.cursor.execute(
                'SELECT repost_enabled FROM settings WHERE user_id = ?',
                (user_id,)
            )
            result = self.cursor.fetchone()
            if not result or not result[0]:
                return
            
            # Reset all posted posts to pending
            self.cursor.execute(
                'UPDATE posts SET status = "pending", posted_at = NULL WHERE user_id = ?',
                (user_id,)
            )
            self.conn.commit()
            
            logger.info(f"Reset posts for repost mode - user {user_id}")
            
        except Exception as e:
            logger.error(f"Repost mode error for user {user_id}: {e}")
    
    def send_to_channel(self, bot_token, channel_username, content_type, file_id, caption):
        """Send content to channel"""
        try:
            if content_type == 'photo':
                url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
                payload = {
                    "chat_id": channel_username,
                    "photo": file_id,
                    "caption": caption,
                    "parse_mode": "HTML"
                }
            elif content_type == 'video':
                url = f"https://api.telegram.org/bot{bot_token}/sendVideo"
                payload = {
                    "chat_id": channel_username,
                    "video": file_id,
                    "caption": caption,
                    "parse_mode": "HTML"
                }
            else:  # document
                url = f"https://api.telegram.org/bot{bot_token}/sendDocument"
                payload = {
                    "chat_id": channel_username,
                    "document": file_id,
                    "caption": caption,
                    "parse_mode": "HTML"
                }
            
            response = requests.post(url, json=payload, timeout=30)
            return response.status_code == 200
            
        except Exception as e:
            logger.error(f"Send to channel error: {e}")
            return False

def run_bot():
    """Run the SMM bot"""
    try:
        smm_bot = SMMBot()
        
        application = Application.builder().token(BOT_TOKEN).build()
        
        conv_handler = ConversationHandler(
            entry_points=[CommandHandler('start', smm_bot.start)],
            states={
                MAIN_MENU: [
                    MessageHandler(filters.Regex('^🤖 Setup Bot Token$'), smm_bot.setup_bot_token),
                    MessageHandler(filters.Regex('^📢 Setup Channels$'), smm_bot.setup_channels),
                    MessageHandler(filters.Regex('^📤 Add Bulk Posts$'), smm_bot.add_bulk_posts),
                    MessageHandler(filters.Regex('^📊 Posts Per Day$'), smm_bot.posts_per_day),
                    MessageHandler(filters.Regex('^✅ My Posted Posts$'), smm_bot.my_posted_posts),
                    MessageHandler(filters.Regex('^⏳ Pending Posts$'), smm_bot.pending_posts),
                    MessageHandler(filters.Regex('^🔄 Repost Mode: (ON|OFF)$'), smm_bot.toggle_repost_mode),
                    MessageHandler(filters.Regex('^🎯 Target Channels$'), smm_bot.target_channels),
                ],
                SETUP_BOT: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, smm_bot.handle_bot_token)
                ],
                SETUP_CHANNELS: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, smm_bot.handle_channels)
                ],
                BULK_POSTS: [
                    MessageHandler(filters.PHOTO | filters.VIDEO | filters.Document.ALL, smm_bot.handle_bulk_media),
                    MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: MAIN_MENU)  # Go back on text
                ],
                POSTS_PER_DAY: [
                    CallbackQueryHandler(smm_bot.handle_ppd_callback, pattern="^ppd_")
                ]
            },
            fallbacks=[CommandHandler('start', smm_bot.start)],
            allow_reentry=True
        )
        
        application.add_handler(conv_handler)
        
        print("🤖 SMM Auto-Post Master Starting...")
        print("✅ Bot is running with your token!")
        print("🚀 Users can setup once and run forever!")
        
        application.run_polling(drop_pending_updates=True)
        
    except Exception as e:
        print(f"❌ Bot error: {e}")
        raise

if __name__ == '__main__':
    print("=" * 60)
    print("🤖 SMM AUTO-POST MASTER - SETUP ONCE, RUN FOREVER!")
    print(f"🔐 Using Bot Token: {BOT_TOKEN[:10]}...")
    print("📢 Multi-Channel Auto-Posting")
    print("🔄 Repost Mode - Infinite Loop")
    print("🎯 Complete Hands-Free Operation")
    print("=" * 60)
    
    run_bot()
