# 📡 TeleMirror Pro • Telegram Channel Bot & Web Admin

A high-performance Telegram Userbot and Web Admin Dashboard built with **Python**, **Telethon**, and **aiohttp** to monitor, clone, transform, and mirror content between Telegram channels in real-time.

---

## 🌐 Deploy to Render (`telemirror.onrender.com`)

You can host TeleMirror Pro on [Render](https://render.com) so your bot runs 24/7 in the cloud with an online web dashboard at `https://telemirror.onrender.com`.

### 🔑 Step 1: Export Your Telegram StringSession
Cloud environments (like Render) require a headless **Telethon StringSession** so the bot can authenticate without asking for interactive SMS verification codes during automated cloud restarts.

Run the exporter tool locally:
```bash
python tools/export_session_string.py
```
*(Or open the local dashboard at `http://localhost:8080`, navigate to **Settings**, and click **"Export From Active Login"**).*

Copy the generated `SESSION_STRING` value.

---

### 📦 Step 2: Push Code to GitHub / GitLab
Initialize git (if not already done) and push to your repository:
```bash
git init
git add .
git commit -m "Prepare TeleMirror for Render deployment"
git branch -M main
git remote add origin https://github.com/<your-username>/telemirror.git
git push -u origin main
```
> [!NOTE]
> `.gitignore` automatically keeps your local `.session` SQLite files and `.env` safe and private.

---

### 🚀 Step 3: Deploy on Render

#### Option A: One-Click Render Blueprint (Recommended)
1. Go to [Render Dashboard](https://dashboard.render.com).
2. Click **New +** -> **Blueprint**.
3. Select your GitHub repository. Render will automatically detect [`render.yaml`](file:///data/data/com.termux/files/home/gov/render.yaml).
4. Enter your environment variables:
   - `SESSION_STRING`: *(Pasted from Step 1)*
   - `API_ID`: *(Your Telegram API ID)*
   - `API_HASH`: *(Your Telegram API Hash)*
   - `PHONE_NUMBER`: *(Your phone number)*
5. Click **Apply**. Render will build and deploy your app.

#### Option B: Manual Web Service Setup
1. On [Render Dashboard](https://dashboard.render.com), click **New +** -> **Web Service**.
2. Connect your Git repository.
3. Configure the service settings:
   - **Name:** `telemirror` *(This gives you `telemirror.onrender.com`)*
   - **Language:** `Python 3`
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `python main.py`
   - **Health Check Path:** `/healthz`
4. In the **Environment Variables** section, add:
   | Variable | Value | Description |
   |---|---|---|
   | `PORT` | `10000` | Port automatically routed by Render |
   | `SESSION_STRING` | *(Your StringSession)* | Enables headless login |
   | `API_ID` | `21547934` | From https://my.telegram.org |
   | `API_HASH` | `your_api_hash` | From https://my.telegram.org |
   | `PHONE_NUMBER` | `+1234567890` | Account phone number |
   | `COPY_MODE` | `clean_copy` | Fresh post (no forward tag) |
   | `MIRROR_EDITS` | `true` | Syncs edited messages |
5. Click **Create Web Service**.

Once deployed, access your live dashboard at:
👉 **`https://telemirror.onrender.com`**

---

## 🌟 Key Features

### 🖥️ 1. Interactive Web Admin Dashboard
- **Web UI**: Full glassmorphism mobile & desktop interface.
- **One-Click Bot Controls**: Start, Stop, and Restart the Telethon userbot engine with live status indicators.
- **Live Metrics & Stats**: Track total messages processed, mirrored, skipped/filtered, and active channels in real time.
- **Real-Time Terminal Streaming**: Live WebSocket terminal logs inside the admin dashboard.

### 📡 2. Channel Management & Routing
- **Source Channels**: Channels to collect posts from.
- **Destination Channels**: Target channels to post to.
- **Telegram Channel Explorer**: Click **"Discover My Telegram Channels"** to browse all channels/groups your account has joined and add them as source or destination with 1 click!

### ✏️ 3. Message Customizer & Promotion Engine
- **🔗 URL & Link Replacement**:
  - **Global Link Override**: Automatically replace **every single URL** in incoming posts with your preferred admin/promotion link (e.g. `https://t.me/my_admin_bot`).
  - **Specific Link Rules**: Map specific links (`t.me/old_promo` -> `https://t.me/my_affiliate_link`).
  - **Strip All Links**: Automatically remove all links from outgoing posts.
- **👤 Username & Admin Handle Customization**:
  - **Global Admin Handle**: Replace all `@mentions` with your preferred `@my_admin` handle.
  - **Specific Handle Rules**: Map `@old_support` -> `@my_support`.
  - **Strip Usernames**: Clean out any mention of source channels or administrators.
- **🏷️ Word & Watermark Replacement**: Replace unwanted brand names or text watermarks.
- **📢 Branding Header & Footer**:
  - Insert custom headers before every post (e.g. `🔥 <b>VIP SIGNALS ALERT</b> 🔥`).
  - Insert custom footers with your contact details & promotion links (e.g. `👉 <b>Contact:</b> @my_admin | <b>Join VIP:</b> https://t.me/mychannel`).
- **🛡️ Keyword Filtering**: Whitelist (only mirror matching posts) or Blacklist (block posts containing unwanted words).
- **🧪 Live Simulator**: Live interactive sandbox to test how messages and links will be transformed before going live!

---

## 📁 Project Structure

```
.
├── main.py                     # Primary launcher (binds to Render $PORT)
├── web_server.py               # aiohttp async Web Server & /healthz check
├── bot_engine.py               # Telethon engine with StringSession support
├── cleaner.py                  # Text transformer & link replacement engine
├── album_manager.py            # Handler for multi-photo/video albums
├── settings_manager.py         # Settings manager with env var & cloud priority
├── config.py                   # Config validator and legacy parser
├── render.yaml                 # Render Blueprint deployment definition
├── Procfile                    # Web service process configuration
├── .gitignore                  # Keeps sessions and secrets private
├── templates/
│   └── index.html              # Modern Web Admin Dashboard
├── tools/
│   ├── export_session_string.py# StringSession exporter for Render
│   └── list_channels.py        # CLI channel discovery tool
├── test_bot.py                 # Automated unit tests
├── requirements.txt            # Python dependencies
└── settings.json               # Active configuration storage
```

---

## 🧪 Running Tests

To verify that all URL replacements, username transformations, health check endpoints, and cloud session handlers work properly:
```bash
python test_bot.py
```
