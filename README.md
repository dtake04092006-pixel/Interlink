# 🤖 Bot Interlink

Discord Bot với web interface để thêm users vào multiple servers.

## Features
- 🔐 OAuth2 authentication
- 💾 PostgreSQL persistent storage 
- 🌐 Web interface
- 🤖 Discord bot commands

## Commands
- `!auth` - Get authorization link
- `!add_me` - Add yourself to servers
- `!check_token` - Check token status
- `!status` - Bot status
- `!ping` - Test connection

## Setup
1. Create Discord Application
2. Deploy to Render
3. Set environment variables
4. Setup PostgreSQL database

## Environment Variables
- `DISCORD_TOKEN`
- `DISCORD_CLIENT_ID` 
- `DISCORD_CLIENT_SECRET`
- `DATABASE_URL`
