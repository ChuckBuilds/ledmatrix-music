# Music Now Playing Plugin

**Perfect replica of the original LEDMatrix MusicManager** with identical visual appearance, functionality, and behavior.

This plugin displays currently playing music from Spotify or YouTube Music with album art, track information, and sophisticated scrolling - exactly as in the original LEDMatrix system.

## 🎵 Features

### Visual Layout (Identical to Original)
- **Album art on left half**: Full-height album artwork display with enhanced contrast and saturation
- **Text on right half**: Title, artist, and album with precise positioning
- **Progress bar**: Real-time playback progress at the bottom
- **Exact fonts**: Uses display manager's small_font and bdf_5x7_font for authentic appearance
- **Perfect colors**: White for titles, dim white for artists, dimmer for albums

### Advanced Scrolling System
- **Independent scrolling**: Title, artist, and album scroll separately with different timing
- **Sophisticated logic**: Text scrolls only when needed, with proper wraparound
- **Configurable speed**: Adjustable scroll divisor for fine-tuning
- **Smooth animation**: Frame-by-frame scrolling with tick-based updates

### State Management (Thread-Safe)
- **Threading locks**: All track info access protected with locks
- **Event-driven updates**: YTM real-time updates via Socket.IO events
- **Polling system**: Background polling with configurable intervals
- **Connection management**: Automatic YTM reconnection and state synchronization

### Music Sources
- **Spotify Integration**: Full OAuth authentication with local token caching
- **YouTube Music Integration**: Real-time Socket.IO connection with companion server
- **Source switching**: Seamless switching between music sources
- **Authentication**: Plugin-local auth files for easy setup

## 🚀 Installation & Setup

### 1. Plugin Installation

The plugin should be installed in your LEDMatrix `plugins/` directory:

```
LEDMatrix/
├── plugins/
│   └── music/          # This plugin
│       ├── manager.py
│       ├── spotify_client.py
│       ├── ytm_client.py
│       ├── manifest.json
│       ├── config_schema.json
│       └── authenticate_*.py
```

### 2. Configuration

Add the music plugin to your LEDMatrix configuration:

```json
{
  "music": {
    "enabled": true,
    "POLLING_INTERVAL_SECONDS": 2,
    "preferred_source": "spotify",
    "YTM_COMPANION_URL": "http://localhost:9863",
    "show_album_art": true,
    "show_progress_bar": true,
    "show_album_name": true,
    "text_scroll_speed": 5,
    "max_album_art_size": 32
  },
  "spotify": {
    "SPOTIFY_CLIENT_ID": "your_client_id",
    "SPOTIFY_CLIENT_SECRET": "your_client_secret",
    "SPOTIFY_REDIRECT_URI": "http://localhost:8888/callback"
  }
}
```

### 3. Authentication Setup

#### For Spotify
1. Get API credentials from [Spotify Developer Dashboard](https://developer.spotify.com/dashboard)
2. Run the authentication script:
   ```bash
   cd plugins/music
   python authenticate_spotify.py
   ```
3. Follow the browser authentication flow
4. Token is cached in `spotify_auth.json`

#### For YouTube Music
1. Install and configure [WebNowPlaying-Redux companion](https://github.com/keifufu/WebNowPlaying-Redux)
2. Generate authentication token:
   ```bash
   cd plugins/music
   python authenticate_ytm.py
   ```
3. Token is stored in `ytm_auth.json`

## ⚙️ Configuration Options

### Core Settings
- `music.enabled`: Enable/disable music display
- `music.preferred_source`: Music source (`spotify` or `ytm`)
- `music.POLLING_INTERVAL_SECONDS`: Update frequency (1-30 seconds)
- `music.YTM_COMPANION_URL`: YouTube Music companion server URL

### Visual Settings
- `music.show_album_art`: Display album artwork (default: true)
- `music.show_progress_bar`: Show playback progress (default: true)
- `music.show_album_name`: Display album name (default: true)
- `music.text_scroll_speed`: Scrolling speed (1-20, lower = faster)
- `music.max_album_art_size`: Maximum artwork size (16-64px)
### Display Settings
- `display_duration`: How long to display music (1-300 seconds)
- `update_interval`: Update frequency for plugin manager (1-30 seconds)

## 🎨 Visual Layout

The plugin displays music information with the exact same layout as the original MusicManager:

```
┌─────────────────┐ ┌─────────────────────────────────────┐
│                 │ │ SONG TITLE (White)                  │
│     ALBUM       │ │                                     │
│     ARTWORK     │ │ Artist Name (Dim White)             │
│                 │ │                                     │
│   (Enhanced &   │ │ Album Name (Dimmest)                │
│   Resized)      │ │                                     │
│                 │ └─────────────────────────────────────┘
│                 │ ┌─────────────────────────────────────┐
│                 │ │█░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░│
│                 │ │    (Progress Bar)                   │
└─────────────────┘ └─────────────────────────────────────┘
```

### Features:
- **Album art fills left half**: Automatically resized and enhanced with contrast/saturation
- **Three text lines**: Title, artist, album with independent scrolling
- **Progress visualization**: Real-time playback progress bar
- **Responsive layout**: Adapts to any display size while maintaining proportions

## 🔧 Technical Details

### Threading & State Management
- **Thread-safe operations**: All track info access protected with locks
- **Event-driven updates**: YTM real-time updates via Socket.IO queue
- **Polling system**: Background updates with configurable intervals
- **Connection management**: Automatic reconnection and state sync

### Image Processing
- **Smart caching**: Album art cached and reused to avoid redundant downloads
- **Image enhancement**: Contrast and saturation adjustments for LED visibility
- **Format conversion**: RGB conversion for matrix compatibility
- **Error handling**: Graceful fallbacks when images fail to load

### Scrolling System
- **Independent scrolling**: Each text field scrolls separately
- **Width detection**: Only scrolls when text exceeds available space
- **Smooth animation**: Frame-by-frame updates with configurable speed
- **Wraparound logic**: Seamless continuous scrolling with proper spacing

## 🎯 Perfect Replica Features

This plugin is a **100% faithful recreation** of the original LEDMatrix MusicManager:

### ✅ Identical Visual Appearance
- **Exact layout**: Album art fills left half, text on right half
- **Same fonts**: Uses display manager's `small_font` and `bdf_5x7_font`
- **Perfect colors**: White titles, dim white artists, dimmer albums
- **Progress bar**: Real-time playback progress at bottom
- **Responsive design**: Adapts to any display size

### ✅ Sophisticated State Management
- **Threading locks**: All operations are thread-safe
- **Event queues**: YTM real-time updates with proper queuing
- **Polling system**: Background updates with exact timing
- **Connection management**: Automatic reconnection and sync

### ✅ Advanced Display Logic
- **Force refresh**: Event-driven display updates
- **Nothing Playing state**: Proper handling when no music plays
- **Album art caching**: Smart caching with invalidation
- **Image processing**: Enhanced contrast and saturation

## 🚀 Quick Setup

### Spotify (Recommended)
```bash
cd plugins/music
python authenticate_spotify.py
```

### YouTube Music
```bash
cd plugins/music
python authenticate_ytm.py
```

### Configuration
```json
{
  "music": {
    "enabled": true,
    "preferred_source": "spotify",
    "POLLING_INTERVAL_SECONDS": 2
  }
}
```

### YouTube Music Setup

1. **Install Companion App**:
   - Download and install the YTM Companion desktop app
   - Or use WebNowPlaying-Redux with browser extension
   - Start the companion server (default port: 9863)

2. **Authenticate**:
   ```bash
   cd plugins/music
   python authenticate_ytm.py
   ```
   
   Follow the prompts:
   - Enter your YTM Companion URL (or press Enter for `http://localhost:9863`)
   - When prompted, approve the authentication request in your YTM Desktop App
   - You have 30 seconds to approve
   
   This will create `ytm_auth.json` in the plugin directory.

3. **Configure URL**:
   ```json
   {
     "music": {
       "enabled": true,
       "preferred_source": "ytm",
       "YTM_COMPANION_URL": "http://YOUR_PC_IP:9863"
     }
   }
   ```

4. **Start Companion**:
   - Ensure companion server is running
   - Play music in YouTube Music
   - Plugin will auto-detect playback

## Display Layout

### With Album Art (64x32 display)
```
┌──────┬────────────────────┐
│      │ Now Playing Title  │
│ Art  │ Artist Name        │
│      │ Album Name         │
└──────┴────────────────────┘
```

### Without Album Art
```
┌────────────────────────────┐
│     Now Playing Title      │
│       Artist Name          │
│       Album Name           │
└────────────────────────────┘
```

## Usage Tips

### Scrolling Text

For long titles that don't fit:
- Enable `scroll_long_text`
- Adjust `scroll_speed` for readability
- Slow speeds (1-2) are more readable
- Fast speeds (5-10) for quick info

### Album Artwork

- Automatically downloads and caches album art
- Resized to fit display
- Slightly dimmed for better text visibility
- Cached per album to reduce network requests

### Polling Interval

- 1-2 seconds: Very responsive, more CPU usage
- 3-5 seconds: Balanced, recommended
- 5-10 seconds: Less responsive, lower resource use

## Troubleshooting

**Nothing displayed:**
- Check that music is actually playing
- Verify preferred_source matches your setup
- Check authentication for Spotify
- Verify companion server URL for YTM

**Spotify not working:**
- Re-run `authenticate_spotify.py` in the plugin directory
- Check that environment variables or config contains valid credentials
- Verify Spotify Premium subscription
- Check that `spotify_auth.json` exists in the plugin directory
- Ensure redirect URI in Spotify dashboard matches your configuration

**YTM not working:**
- Re-run `authenticate_ytm.py` in the plugin directory
- Verify companion server is running
- Check that `ytm_auth.json` exists in the plugin directory
- Check companion URL is correct in config
- Ensure firewall allows connection
- Try opening companion URL in browser (e.g., `http://localhost:9863`)

**Album art not showing:**
- Check internet connection
- Verify `show_album_art` is true
- Some tracks may not have artwork
- Check for image loading errors in logs

**Text scrolling too fast/slow:**
- Adjust `scroll_speed` value
- Try values 1-3 for readability
- Values above 5 may be hard to read

**Lagging/stuttering:**
- Increase `POLLING_INTERVAL_SECONDS`
- Disable album art if not needed
- Check network latency

## Advanced Configuration

### Custom Styling

Modify the display appearance by adjusting font sizes and colors in the code:

```python
# In manager.py
title_font = ImageFont.truetype('path/to/font.ttf', 10)  # Larger title
info_font = ImageFont.truetype('path/to/font.ttf', 8)   # Larger info
```

### Multiple Sources

While only one source is active at a time, you can quickly switch:

```json
{
  "preferred_source": "spotify"  // or "ytm"
}
```

### Performance Tuning

For lower-end devices:
- Increase polling interval to 5+ seconds
- Disable album art
- Disable text scrolling
- Use simpler fonts

## Integration Notes

### Spotify Client

Uses existing `SpotifyClient` from main LEDMatrix codebase:
- Handles OAuth authentication
- Manages token refresh
- Provides playback API access

### YTM Client

Uses existing `YTMClient` from main LEDMatrix codebase:
- Connects to companion server
- Receives real-time updates
- Handles connection errors gracefully

## Examples

### Spotify Configuration
```json
{
  "music": {
    "enabled": true,
    "preferred_source": "spotify",
    "POLLING_INTERVAL_SECONDS": 2,
    "show_album_art": true,
    "scroll_long_text": true,
    "scroll_speed": 2
  }
}
```

### YouTube Music Configuration
```json
{
  "music": {
    "enabled": true,
    "preferred_source": "ytm",
    "YTM_COMPANION_URL": "http://192.168.1.100:9863",
    "POLLING_INTERVAL_SECONDS": 3,
    "show_album_art": true,
    "scroll_long_text": true,
    "scroll_speed": 1
  }
}
```

### Minimal Configuration (No Album Art)
```json
{
  "music": {
    "enabled": true,
    "preferred_source": "ytm",
    "YTM_COMPANION_URL": "http://localhost:9863",
    "show_album_art": false,
    "scroll_long_text": true
  }
}
```

## Plugin Isolation and Security

### Self-Contained Design

This music plugin is fully self-contained. All authentication files are stored within the plugin directory:

- `spotify_auth.json` - Spotify OAuth token
- `ytm_auth.json` - YouTube Music Companion token
- `authenticate_spotify.py` - Spotify authentication script
- `authenticate_ytm.py` - YTM authentication script
- `spotify_client.py` - Spotify API client
- `ytm_client.py` - YTM API client

### Security Notes

**Important:** Authentication files contain sensitive tokens and should be protected:

1. **.gitignore Protection**: The plugin includes a `.gitignore` file that prevents authentication files from being committed to git:
   ```
   spotify_auth.json
   ytm_auth.json
   ```

2. **File Permissions**: Ensure authentication files have appropriate permissions:
   ```bash
   chmod 600 spotify_auth.json ytm_auth.json
   ```

3. **Clean Uninstall**: If you delete this plugin, all authentication data is removed with it. No traces are left in the main LEDMatrix configuration directory.

4. **Environment Variables**: For added security, use environment variables for API credentials instead of storing them in the config file.

### Data Storage Locations

All music plugin data is stored in the plugin directory:

```
plugins/music/
├── authenticate_spotify.py    # Spotify auth script
├── authenticate_ytm.py         # YTM auth script
├── spotify_client.py           # Spotify client
├── ytm_client.py               # YTM client
├── manager.py                  # Plugin main logic
├── manifest.json               # Plugin metadata
├── config_schema.json          # Configuration schema
├── requirements.txt            # Dependencies
├── README.md                   # This file
├── .gitignore                  # Git ignore rules
├── spotify_auth.json           # (created after Spotify auth)
└── ytm_auth.json               # (created after YTM auth)
```

## License

GPL-3.0 License - see main LEDMatrix repository for details.

