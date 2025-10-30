"""
Music Player Plugin for LEDMatrix

Real-time now playing display for Spotify and YouTube Music with album art,
scrolling text, and progress bars. Migrated from src/old_managers/music_manager.py
with flattened configuration structure for plugin compatibility.
"""

import time
import threading
from enum import Enum, auto
import logging
import json
from io import BytesIO
import requests
from typing import Union, Dict, Any
from PIL import Image, ImageEnhance
import queue

# Import client modules
from spotify_client import SpotifyClient
from ytm_client import YTMClient

# Import the API counter function from web interface
try:
    from web_interface_v2 import increment_api_counter
except ImportError:
    # Fallback if web interface is not available
    def increment_api_counter(kind: str, count: int = 1):  # pylint: disable=unused-argument
        pass

# Import base plugin class
from src.plugin_system.base_plugin import BasePlugin

# Configure logging
logger = logging.getLogger(__name__)

class MusicSource(Enum):
    NONE = auto()
    SPOTIFY = auto()
    YTM = auto()

class MusicPlugin(BasePlugin):
    """
    Music Player Plugin for LEDMatrix
    
    Displays real-time now playing information from Spotify and YouTube Music
    with album art, scrolling text, and progress bars. Supports both sources
    with automatic switching and seamless display updates.
    """
    
    def __init__(self, plugin_id: str, config: Dict[str, Any], 
                 display_manager, cache_manager, plugin_manager):
        """Initialize the music plugin."""
        super().__init__(plugin_id, config, display_manager, cache_manager, plugin_manager)
        
        # Music-specific state
        self.spotify = None
        self.ytm = None
        self.current_track_info = None
        self.current_source = MusicSource.NONE
        self.polling_interval = 2  # Default
        self.preferred_source = "spotify"  # Default
        self.stop_event = threading.Event()
        self.track_info_lock = threading.Lock()
        
        # Display related attributes
        self.album_art_image = None
        self.last_album_art_url = None
        self.scroll_position_title = 0
        self.scroll_position_artist = 0
        self.scroll_position_album = 0
        self.title_scroll_tick = 0
        
        # Track update logging throttling
        self.last_track_log_time = 0
        self.last_logged_track_title = None
        self.track_log_interval = 5.0  # Log track updates every 5 seconds max
        self.artist_scroll_tick = 0
        self.album_scroll_tick = 0
        self.is_music_display_active = False
        self.is_currently_showing_nothing_playing = False
        self._needs_immediate_full_refresh = False
        self.ytm_event_data_queue = queue.Queue(maxsize=1)
        
        self.poll_thread = None
        
        # Additional attributes for display management
        self.last_periodic_refresh_time = 0
        self._last_nothing_playing_log_time = 0
        
        # Priority mode attributes
        self.music_priority_mode = self.config.get("music_priority_mode", False)
        self.priority_duration_seconds = self.config.get("priority_duration_seconds", 30)
        self.music_priority_active = False
        self.music_priority_start_time = 0
        self.last_music_activity_time = 0
        # Timeout for persistent 'Nothing Playing' while in priority mode
        self.nothing_playing_timeout_seconds = self.config.get("nothing_playing_timeout_seconds", 10)
        self._nothing_playing_since_ts = None
        
        # Load configuration with flattened access
        self._load_config()
        self._initialize_clients()
        
        self.logger.info(f"Music plugin initialized - Source: {self.preferred_source}, Enabled: {self.enabled}, Priority Mode: {self.music_priority_mode}")

    def _load_config(self):
        """Load configuration with flattened access (no nested 'music' key)."""
        default_interval = 2
        self.enabled = False  # Assume disabled until config proves otherwise

        if self.config is None:
            self.logger.warning("No config provided to MusicPlugin. Music plugin disabled.")
            return

        try:
            # Flattened config access - no nested 'music' key
            self.enabled = self.config.get("enabled", False)
            if not self.enabled:
                self.logger.info("Music plugin is disabled in config.")
                return

            self.polling_interval = self.config.get("polling_interval_seconds", default_interval)
            configured_source = self.config.get("preferred_source", "spotify").lower()

            if configured_source in ["spotify", "ytm"]:
                self.preferred_source = configured_source
                self.logger.info(f"Music plugin enabled. Polling interval: {self.polling_interval}s. Preferred source: {self.preferred_source}")
            else:
                self.logger.warning(f"Invalid 'preferred_source' ('{configured_source}') in config. Must be 'spotify' or 'ytm'. Music plugin disabled.")
                self.enabled = False
                return

        except Exception as e:
            self.logger.error(f"Error loading music config: {e}. Music plugin disabled.")
            self.enabled = False

    def _initialize_clients(self):
        """Initialize music clients based on configuration."""
        if not self.enabled:
            self.spotify = None
            self.ytm = None
            return

        self.logger.info("Initializing music clients...")

        # Initialize Spotify Client if needed
        if self.preferred_source == "spotify":
            try:
                self.spotify = SpotifyClient()
                if not self.spotify.is_authenticated():
                    self.logger.warning("Spotify client initialized but not authenticated. Please run authenticate_spotify.py if you want to use Spotify.")
                else:
                    self.logger.info("Spotify client authenticated.")
            except Exception as e:
                self.logger.error(f"Failed to initialize Spotify client: {e}")
                self.spotify = None
        else:
            self.spotify = None

        # Initialize YTM Client if needed
        if self.preferred_source == "ytm":
            try:
                self.ytm = YTMClient(update_callback=self._handle_ytm_direct_update)
                self.logger.info(f"YTMClient initialized. Connection will be managed on-demand. Configured URL: {self.ytm.base_url}")
            except Exception as e:
                self.logger.error(f"Failed to initialize YTM client: {e}")
                self.ytm = None
        else:
            self.ytm = None

    def _process_ytm_data_update(self, ytm_data, source_description: str):
        """
        Core processing logic for YTM data.
        Updates self.current_track_info, handles album art, queues data for display,
        and determines if the update is significant.

        Args:
            ytm_data: The raw data from YTM.
            source_description: A string for logging (e.g., "YTM Event", "YTM Activate Sync").

        Returns:
            tuple: (simplified_info, significant_change_detected)
        """
        # Verbose diagnostics about incoming event/state
        try:
            title_log = ytm_data.get('video', {}).get('title') if isinstance(ytm_data, dict) else None
            author_log = ytm_data.get('video', {}).get('author') if isinstance(ytm_data, dict) else None
            state_log = (ytm_data.get('player', {}).get('trackState') == 1) if isinstance(ytm_data, dict) else None
            self.logger.debug(f"_process_ytm_data_update[{source_description}]: incoming title='{title_log}', artist='{author_log}', is_playing={state_log}")
        except Exception:
            pass

        if not ytm_data:
            simplified_info = self.get_simplified_track_info(None, MusicSource.NONE)
        else:
            ytm_player_info = ytm_data.get('player', {})
            is_actually_playing_ytm = (ytm_player_info.get('trackState') == 1) and not ytm_player_info.get('adPlaying', False)
            simplified_info = self.get_simplified_track_info(ytm_data if is_actually_playing_ytm else None,
                                                           MusicSource.YTM if is_actually_playing_ytm else MusicSource.NONE)

        significant_change_detected = False
        processed_a_meaningful_update = False

        with self.track_info_lock:
            current_track_info_before_update_str = json.dumps(self.current_track_info) if self.current_track_info else "None"
            simplified_info_str = json.dumps(simplified_info)
            self.logger.debug(f"MusicPlugin._process_ytm_data_update ({source_description}): PRE-COMPARE - SimplifiedInfo: {simplified_info_str}, CurrentTrackInfo: {current_track_info_before_update_str}")

            if self.current_track_info is None and simplified_info.get('title') != 'Nothing Playing':
                significant_change_detected = True
                self.logger.debug(f"({source_description}): First valid track data, marking as significant.")
            elif self.current_track_info is not None and (
                simplified_info.get('title') != self.current_track_info.get('title') or
                simplified_info.get('artist') != self.current_track_info.get('artist') or
                simplified_info.get('album_art_url') != self.current_track_info.get('album_art_url') or
                simplified_info.get('is_playing') != self.current_track_info.get('is_playing')
            ):
                significant_change_detected = True
                self.logger.debug(f"({source_description}): Significant change (title/artist/art/is_playing) detected.")

            if simplified_info != self.current_track_info:
                processed_a_meaningful_update = True
                old_album_art_url = self.current_track_info.get('album_art_url') if self.current_track_info else None
                
                self.current_track_info = simplified_info
                self.logger.debug(f"MusicPlugin._process_ytm_data_update ({source_description}): POST-UPDATE (inside lock) - self.current_track_info now: {json.dumps(self.current_track_info)}")

                # Determine current source based on this update
                if simplified_info.get('source') == 'YouTube Music' and simplified_info.get('is_playing'):
                    self.current_source = MusicSource.YTM
                elif self.current_source == MusicSource.YTM and not simplified_info.get('is_playing'):
                    self.current_source = MusicSource.NONE
                elif simplified_info.get('source') == 'None':
                    self.current_source = MusicSource.NONE
                
                new_album_art_url = simplified_info.get('album_art_url')

                self.logger.debug(f"({source_description}) Track info comparison: simplified_info != self.current_track_info was TRUE.")
                self.logger.debug(f"({source_description}) Old Album Art URL: {old_album_art_url}, New Album Art URL: {new_album_art_url}")

                if new_album_art_url != old_album_art_url:
                    self.logger.info(f"({source_description}) Album art URL changed. Clearing self.album_art_image to force re-fetch.")
                    self.album_art_image = None
                    self.last_album_art_url = new_album_art_url
                elif not self.last_album_art_url and new_album_art_url:
                    self.logger.info(f"({source_description}) New album art URL appeared. Clearing image.")
                    self.album_art_image = None
                    self.last_album_art_url = new_album_art_url
                elif new_album_art_url is None and old_album_art_url is not None:
                    self.logger.info(f"({source_description}) Album art URL disappeared. Clearing image and URL.")
                    self.album_art_image = None
                    self.last_album_art_url = None
                elif self.current_track_info and self.current_track_info.get('album_art_url') and not self.last_album_art_url:
                    self.last_album_art_url = self.current_track_info.get('album_art_url')
                    self.album_art_image = None

                display_title = self.current_track_info.get('title', 'None')
                
                # Throttle track update logging to reduce spam
                current_time = time.time()
                should_log = False
                
                if (display_title != self.last_logged_track_title or 
                    current_time - self.last_track_log_time >= self.track_log_interval):
                    should_log = True
                    self.last_track_log_time = current_time
                    self.last_logged_track_title = display_title
                
                if should_log:
                    self.logger.info(f"({source_description}) Track info updated. Source: {self.current_source.name}. New Track: {display_title}")
                else:
                    self.logger.debug(f"({source_description}) Track info updated (throttled). Source: {self.current_source.name}. Track: {display_title}")
            else:
                processed_a_meaningful_update = False
                self.logger.debug(f"({source_description}) No change in simplified track info (simplified_info == self.current_track_info).")
                if self.current_track_info is None and simplified_info.get('title') != 'Nothing Playing':
                    significant_change_detected = True
                    processed_a_meaningful_update = True
                    self.current_track_info = simplified_info
                    display_title = simplified_info.get('title', 'None')
                    current_time = time.time()
                    
                    self.logger.info(f"({source_description}) First valid track data received (was None), marking significant. Track: {display_title}")
                    self.last_track_log_time = current_time
                    self.last_logged_track_title = display_title

        # Queueing logic for events
        if source_description in ["YTM Event", "YTM Activate Sync"]:
            try:
                while not self.ytm_event_data_queue.empty():
                    self.ytm_event_data_queue.get_nowait()
                self.ytm_event_data_queue.put_nowait(simplified_info)
                self.logger.debug(f"MusicPlugin._process_ytm_data_update ({source_description}): Put simplified_info (Title: {simplified_info.get('title')}) into ytm_event_data_queue.")
            except queue.Full:
                self.logger.warning(f"MusicPlugin._process_ytm_data_update ({source_description}): ytm_event_data_queue was full.")

        if significant_change_detected:
            self.logger.info(f"({source_description}) Significant track change detected. Signaling for an immediate full refresh of MusicPlugin display.")
            self._needs_immediate_full_refresh = True
        elif processed_a_meaningful_update:
            self.logger.debug(f"({source_description}) Minor track data update (e.g. progress). Display will update without full refresh.")

        return simplified_info, significant_change_detected

    def activate_music_display(self):
        """Activate music display and connect YTM if needed."""
        self.logger.info("Music display activated.")
        self.is_music_display_active = True
        
        # Handle priority mode
        if self.music_priority_mode:
            self._activate_music_priority()
        
        if self.ytm and self.preferred_source == "ytm":
            if not self.ytm.is_connected:
                self.logger.info("Attempting to connect YTM client due to music display activation.")
                if self.ytm.connect_client(timeout=10):
                    self.logger.info("YTM client connected successfully on display activation.")
                    latest_data = self.ytm.get_current_track()
                    if latest_data:
                        self.logger.debug("YTM Activate Sync: Processing current track data after successful connection.")
                        self._process_ytm_data_update(latest_data, "YTM Activate Sync")
                else:
                    self.logger.warning("YTM client failed to connect on display activation.")
            else:
                self.logger.debug("YTM client already connected during music display activation. Syncing state.")
                latest_data = self.ytm.get_current_track()
                if latest_data:
                    self._process_ytm_data_update(latest_data, "YTM Activate Sync")
                else:
                    self.logger.debug("YTM Activate Sync: No track data available from connected YTM client.")
                    self._process_ytm_data_update(None, "YTM Activate Sync (No Data)")

    def deactivate_music_display(self):
        """Deactivate music display and disconnect YTM."""
        self.logger.info("Music display deactivated.")
        self.is_music_display_active = False
        
        # Handle priority mode
        if self.music_priority_mode:
            self._deactivate_music_priority()
        
        if self.ytm and self.ytm.is_connected:
            self.logger.info("Disconnecting YTM client due to music display deactivation.")
            self.ytm.disconnect_client()

    def _activate_music_priority(self):
        """Activate music priority mode - music takes over the display."""
        if not self.music_priority_active:
            self.music_priority_active = True
            self.music_priority_start_time = time.time()
            self.logger.info("🎵 Music priority mode activated - music takes over display")
            
            # Notify plugin manager to switch to music mode
            if hasattr(self.plugin_manager, 'set_priority_mode'):
                self.plugin_manager.set_priority_mode('now_playing', self.priority_duration_seconds)

    def _deactivate_music_priority(self):
        """Deactivate music priority mode - return to normal display rotation."""
        if self.music_priority_active:
            self.music_priority_active = False
            self.logger.info("🎵 Music priority mode deactivated - returning to normal display rotation")
            
            # Notify plugin manager to return to normal mode
            if hasattr(self.plugin_manager, 'clear_priority_mode'):
                self.plugin_manager.clear_priority_mode()

    def _check_music_priority_timeout(self):
        """Check if music priority mode should timeout."""
        if self.music_priority_active:
            current_time = time.time()
            if current_time - self.music_priority_start_time > self.priority_duration_seconds:
                self.logger.info("🎵 Music priority mode timeout - returning to normal display rotation")
                self._deactivate_music_priority()
                return True
        return False

    def _handle_ytm_direct_update(self, ytm_data):
        """Handle a direct state update from YTMClient."""
        raw_title_from_event = ytm_data.get('video', {}).get('title', 'No Title') if isinstance(ytm_data, dict) else 'Data not a dict'
        self.logger.debug(f"MusicPlugin._handle_ytm_direct_update: RAW EVENT DATA - Title: '{raw_title_from_event}'")

        if not self.enabled or not self.is_music_display_active:
            self.logger.debug("Skipping YTM direct update: Plugin disabled or music display not active.")
            return

        if self.preferred_source != "ytm":
            self.logger.debug(f"Skipping YTM direct update: Preferred source is '{self.preferred_source}', not 'ytm'.")
            return
        
        # Process the data and get outcomes
        self._process_ytm_data_update(ytm_data, "YTM Event")

    def _fetch_and_resize_image(self, url: str, target_size: tuple) -> Union[Image.Image, None]:
        """Fetch an image from a URL, resize it, and return a PIL Image object."""
        if not url:
            return None
        try:
            response = requests.get(url, timeout=5)
            response.raise_for_status()
            
            # Increment API counter for music data
            increment_api_counter('music', 1)
            
            img_data = BytesIO(response.content)
            img = Image.open(img_data)
            
            # Ensure image is RGB for compatibility with the matrix
            img = img.convert("RGB") 
            
            img.thumbnail(target_size, Image.Resampling.LANCZOS)

            # Enhance contrast
            enhancer_contrast = ImageEnhance.Contrast(img)
            img = enhancer_contrast.enhance(1.3)

            # Enhance saturation (Color)
            enhancer_saturation = ImageEnhance.Color(img)
            img = enhancer_saturation.enhance(1.3)
            
            final_img = Image.new("RGB", target_size, (0,0,0))
            paste_x = (target_size[0] - img.width) // 2
            paste_y = (target_size[1] - img.height) // 2
            final_img.paste(img, (paste_x, paste_y))
            
            return final_img
        except requests.exceptions.RequestException as e:
            self.logger.error(f"Error fetching image from {url}: {e}")
            return None
        except IOError as e:
            self.logger.error(f"Error processing image from {url}: {e}")
            return None
        except Exception as e:
            self.logger.error(f"Unexpected error fetching/processing image {url}: {e}")
            return None

    def _poll_music_data(self):
        """Continuously poll music sources for updates, respecting preferences."""
        if not self.enabled:
            self.logger.warning("Polling attempted while music plugin is disabled. Stopping polling thread.")
            return

        while not self.stop_event.is_set():
            significant_change_for_callback = False
            simplified_info_for_callback = None

            if self.preferred_source == "spotify" and self.spotify and self.spotify.is_authenticated():
                try:
                    spotify_track = self.spotify.get_current_track()
                    if spotify_track and spotify_track.get('is_playing'):
                        simplified_info_poll = self.get_simplified_track_info(spotify_track, MusicSource.SPOTIFY)
                        
                        with self.track_info_lock:
                            if simplified_info_poll != self.current_track_info:
                                # Check for significant changes
                                significant_change_detected = False
                                if self.current_track_info is None and simplified_info_poll.get('title') != 'Nothing Playing':
                                    significant_change_detected = True
                                    self.logger.debug("Polling Spotify: First valid track data, marking as significant.")
                                elif self.current_track_info is not None and (
                                    simplified_info_poll.get('title') != self.current_track_info.get('title') or
                                    simplified_info_poll.get('artist') != self.current_track_info.get('artist') or
                                    simplified_info_poll.get('album_art_url') != self.current_track_info.get('album_art_url') or
                                    simplified_info_poll.get('is_playing') != self.current_track_info.get('is_playing')
                                ):
                                    significant_change_detected = True
                                    self.logger.debug("Polling Spotify: Significant change (title/artist/art/is_playing) detected.")
                                else:
                                    self.logger.debug("Polling Spotify: Only progress changed, not significant.")
                                
                                self.current_track_info = simplified_info_poll
                                self.current_source = MusicSource.SPOTIFY
                                significant_change_for_callback = significant_change_detected
                                simplified_info_for_callback = simplified_info_poll.copy()
                                
                                if significant_change_detected:
                                    self._needs_immediate_full_refresh = True
                                    self.logger.info("Polling Spotify: Significant change detected.")
                                    
                                    # Handle priority mode for music starting
                                    if self.music_priority_mode and simplified_info_poll.get('is_playing'):
                                        self.last_music_activity_time = time.time()
                                        if not self.music_priority_active:
                                            self.logger.info("🎵 Music started playing - activating priority mode")
                                            self._activate_music_priority()
                                else:
                                    self.logger.debug("Polling Spotify: Minor update (progress only), no full refresh needed.")
                                
                                # Handle album art for Spotify
                                old_album_art_url = self.current_track_info.get('album_art_url_prev_spotify')
                                new_album_art_url = simplified_info_poll.get('album_art_url')
                                if new_album_art_url != old_album_art_url:
                                    self.album_art_image = None
                                    self.last_album_art_url = new_album_art_url
                                self.current_track_info['album_art_url_prev_spotify'] = new_album_art_url

                                self.logger.debug(f"Polling Spotify: Active track - {spotify_track.get('item', {}).get('name')}")
                            else:
                                self.logger.debug("Polling Spotify: No change in simplified track info.")
                        
                    else:
                        self.logger.debug("Polling Spotify: No active track or player paused.")
                        # If Spotify was playing and now it's not
                        with self.track_info_lock:
                            if self.current_source == MusicSource.SPOTIFY:
                                simplified_info_for_callback = self.get_simplified_track_info(None, MusicSource.NONE)
                                self.current_track_info = simplified_info_for_callback
                                self.current_source = MusicSource.NONE
                                significant_change_for_callback = True
                                self._needs_immediate_full_refresh = True
                                self.album_art_image = None
                                self.last_album_art_url = None
                                self.logger.info("Polling Spotify: Player stopped. Updating to Nothing Playing.")
                                
                                # Handle priority mode for music stopping
                                if self.music_priority_mode and self.music_priority_active:
                                    self.logger.info("🎵 Music stopped playing - deactivating priority mode")
                                    self._deactivate_music_priority()

                except Exception as e:
                    self.logger.error(f"Error polling Spotify: {e}")
                    if "token" in str(e).lower():
                        self.logger.warning("Spotify auth token issue detected during polling.")
            
            elif self.preferred_source == "ytm" and self.ytm:
                if self.ytm.is_connected:
                    try:
                        ytm_track_data = self.ytm.get_current_track()
                        simplified_info_for_callback, significant_change_for_callback = self._process_ytm_data_update(ytm_track_data, "YTM Poll")
                        if significant_change_for_callback:
                            self.logger.debug(f"Polling YTM: Change detected via _process_ytm_data_update. Title: {simplified_info_for_callback.get('title')}")
                            
                            # Handle priority mode for music starting
                            if self.music_priority_mode and simplified_info_for_callback and simplified_info_for_callback.get('is_playing'):
                                self.last_music_activity_time = time.time()
                                if not self.music_priority_active:
                                    self.logger.info("🎵 Music started playing - activating priority mode")
                                    self._activate_music_priority()
                        else:
                            self.logger.debug(f"Polling YTM: No change detected via _process_ytm_data_update. Title: {simplified_info_for_callback.get('title')}")

                    except Exception as e:
                        self.logger.error(f"Error during YTM poll processing: {e}")
                else:
                    self.logger.debug("Skipping YTM poll: Client not connected. Will attempt reconnect on next cycle if display active.")
                    if self.is_music_display_active:
                        self.logger.info("YTM is preferred and display active, attempting reconnect during poll cycle.")
                        if self.ytm.connect_client(timeout=5):
                            self.logger.info("YTM reconnected during poll cycle. Will process data on next poll/event.")
                            latest_data = self.ytm.get_current_track()
                            if latest_data:
                                simplified_info_for_callback, significant_change_for_callback = self._process_ytm_data_update(latest_data, "YTM Poll Reconnect Sync")
                        else:
                            self.logger.warning("YTM failed to reconnect during poll cycle.")
                            with self.track_info_lock:
                                if self.current_source == MusicSource.YTM:
                                    simplified_info_for_callback = self.get_simplified_track_info(None, MusicSource.NONE)
                                    self.current_track_info = simplified_info_for_callback
                                    self.current_source = MusicSource.NONE
                                    significant_change_for_callback = True
                                    self.album_art_image = None
                                    self.last_album_art_url = None
                                    self.logger.info("Polling YTM: Reconnect failed. Updating to Nothing Playing.")
                                    
                                    # Handle priority mode for music stopping
                                    if self.music_priority_mode and self.music_priority_active:
                                        self.logger.info("🎵 Music stopped playing - deactivating priority mode")
                                        self._deactivate_music_priority()
            
            time.sleep(self.polling_interval)

    def get_simplified_track_info(self, track_data, source):
        """Provide a consistent format for track info regardless of source."""
        
        # Default "Nothing Playing" structure
        nothing_playing_info = {
            'source': 'None',
            'title': 'Nothing Playing',
            'artist': '',
            'album': '',
            'album_art_url': None,
            'duration_ms': 0,
            'progress_ms': 0,
            'is_playing': False,
        }

        if source == MusicSource.SPOTIFY and track_data:
            item = track_data.get('item', {})
            is_playing_spotify = track_data.get('is_playing', False)

            if not item or not is_playing_spotify:
                return nothing_playing_info.copy()

            return {
                'source': 'Spotify',
                'title': item.get('name'),
                'artist': ', '.join([a['name'] for a in item.get('artists', [])]),
                'album': item.get('album', {}).get('name'),
                'album_art_url': item.get('album', {}).get('images', [{}])[0].get('url') if item.get('album', {}).get('images') else None,
                'duration_ms': item.get('duration_ms'),
                'progress_ms': track_data.get('progress_ms'),
                'is_playing': is_playing_spotify,
            }
        elif source == MusicSource.YTM and track_data:
            video_info = track_data.get('video', {})
            player_info = track_data.get('player', {})

            title = video_info.get('title')
            artist = video_info.get('author')
            thumbnails = video_info.get('thumbnails', [])
            album_art_url = thumbnails[0].get('url') if thumbnails else None

            # Primary conditions for "Nothing Playing" for YTM
            if player_info.get('adPlaying', False):
                self.logger.debug("YTM (get_simplified_track_info): Ad is playing, reporting as Nothing Playing.")
                return nothing_playing_info.copy()
            
            if not title or not artist:
                self.logger.debug(f"YTM (get_simplified_track_info): No title ('{title}') or artist ('{artist}'), reporting as Nothing Playing.")
                return nothing_playing_info.copy()

            # Determine playback state
            track_state = player_info.get('trackState')
            is_playing_ytm = (track_state == 1)

            album = video_info.get('album')
            duration_seconds = video_info.get('durationSeconds')
            duration_ms = int(duration_seconds * 1000) if duration_seconds is not None else 0
            progress_seconds = player_info.get('videoProgress')
            progress_ms = int(progress_seconds * 1000) if progress_seconds is not None else 0

            return {
                'source': 'YouTube Music',
                'title': title,
                'artist': artist,
                'album': album if album else '',
                'album_art_url': album_art_url,
                'duration_ms': duration_ms,
                'progress_ms': progress_ms,
                'is_playing': is_playing_ytm,
            }
        else:
            return nothing_playing_info.copy()

    def get_current_display_info(self):
        """Return the currently stored track information for display."""
        with self.track_info_lock:
            return self.current_track_info.copy() if self.current_track_info else None

    def start_polling(self):
        """Start polling for music data."""
        if not self.enabled:
            self.logger.info("Music plugin disabled, polling not started.")
            return

        if not self.poll_thread or not self.poll_thread.is_alive():
            if not self.spotify and not self.ytm:
                self.logger.warning("Cannot start polling: No music clients initialized or available.")
                return

            self.stop_event.clear()
            self.poll_thread = threading.Thread(target=self._poll_music_data, daemon=True)
            self.poll_thread.start()
            self.logger.info("Music polling started.")

    def stop_polling(self):
        """Stop the music polling thread."""
        self.logger.info("Music plugin: Stopping polling thread...")
        self.stop_event.set()
        if self.poll_thread and self.poll_thread.is_alive():
            self.poll_thread.join(timeout=self.polling_interval + 1)
        if self.poll_thread and self.poll_thread.is_alive():
            self.logger.warning("Music plugin: Polling thread did not terminate cleanly.")
        else:
            self.logger.info("Music plugin: Polling thread stopped.")
        self.poll_thread = None
        
        if self.ytm:
            self.logger.info("MusicPlugin: Shutting down YTMClient resources.")
            if self.ytm.is_connected:
                self.ytm.disconnect_client()
            self.ytm.shutdown()

    def update(self) -> None:
        """Update music data - called by plugin system."""
        if not self.enabled:
            return
            
        # Start polling if not already running
        if not self.poll_thread or not self.poll_thread.is_alive():
            self.start_polling()

    def display(self, force_clear: bool = False) -> None:
        """Display music information - called by plugin system."""
        # Check for priority mode timeout
        if self.music_priority_mode:
            self._check_music_priority_timeout()
            
        perform_full_refresh_this_cycle = force_clear
        art_url_currently_in_cache = None
        image_currently_in_cache = None
        
        # Ensure music display is activated on first entry so YTM can connect
        if not self.is_music_display_active:
            self.logger.debug("MusicPlugin.display: Activating music display on entry (ensures YTM connection attempt).")
            self.activate_music_display()

        # Check if an event previously signaled a need for immediate refresh
        initial_data_from_queue_due_to_event = None
        if self._needs_immediate_full_refresh:
            self.logger.debug("MusicPlugin.display: _needs_immediate_full_refresh is True (event-driven).")
            perform_full_refresh_this_cycle = True
            try:
                initial_data_from_queue_due_to_event = self.ytm_event_data_queue.get_nowait()
                self.logger.info(f"MusicPlugin.display: Got data from ytm_event_data_queue (due to event flag): Title {initial_data_from_queue_due_to_event.get('title') if initial_data_from_queue_due_to_event else 'None'}")
            except queue.Empty:
                self.logger.warning("MusicPlugin.display: _needs_immediate_full_refresh was true, but queue empty. Will refresh with current_track_info.")
            self._needs_immediate_full_refresh = False

        current_track_info_snapshot = None

        if perform_full_refresh_this_cycle:
            log_msg_detail = f"force_clear_from_DC={force_clear}, event_driven_refresh_attempted={'Yes' if initial_data_from_queue_due_to_event is not None else 'No'}"
            self.logger.debug(f"MusicPlugin.display: Performing full refresh cycle. Details: {log_msg_detail}")
            
            self.display_manager.clear()
            self.activate_music_display()
            self.last_periodic_refresh_time = time.time()
            
            data_from_queue_post_activate = None
            try:
                data_from_queue_post_activate = self.ytm_event_data_queue.get_nowait()
                self.logger.info(f"MusicPlugin.display (Full Refresh): Got data from queue POST activate_music_display: Title {data_from_queue_post_activate.get('title') if data_from_queue_post_activate else 'None'}")
            except queue.Empty:
                self.logger.debug("MusicPlugin.display (Full Refresh): Queue empty POST activate_music_display.")

            if data_from_queue_post_activate:
                current_track_info_snapshot = data_from_queue_post_activate
            elif initial_data_from_queue_due_to_event: 
                current_track_info_snapshot = initial_data_from_queue_due_to_event
                self.logger.debug("MusicPlugin.display (Full Refresh): Using data from initial event queue for snapshot.")
            else:
                with self.track_info_lock:
                    current_track_info_snapshot = self.current_track_info.copy() if self.current_track_info else None
                self.logger.debug("MusicPlugin.display (Full Refresh): Using self.current_track_info for snapshot.")
        else:
            with self.track_info_lock:
                current_track_info_snapshot = self.current_track_info.copy() if self.current_track_info else None

        # Update cache variables after snapshot is finalized
        with self.track_info_lock:
            art_url_currently_in_cache = self.last_album_art_url
            image_currently_in_cache = self.album_art_image

        snapshot_title_for_log = current_track_info_snapshot.get('title', 'N/A') if current_track_info_snapshot else 'N/A'
        if perform_full_refresh_this_cycle: 
            self.logger.debug(f"MusicPlugin.display (Full Refresh Render): Using snapshot - Title: '{snapshot_title_for_log}'")
        
        # Nothing Playing Logic
        if not current_track_info_snapshot or current_track_info_snapshot.get('title') == 'Nothing Playing':
            if not hasattr(self, '_last_nothing_playing_log_time') or time.time() - getattr(self, '_last_nothing_playing_log_time', 0) > 10:
                # Add rich diagnostic context so we can see exactly why we're showing Nothing Playing
                debug_ctx = {
                    'preferred_source': self.preferred_source,
                    'is_music_display_active': self.is_music_display_active,
                    'ytm_connected': bool(self.ytm and self.ytm.is_connected),
                    'have_current_track_info': bool(self.current_track_info),
                    'snapshot_exists': bool(current_track_info_snapshot),
                }
                if current_track_info_snapshot:
                    debug_ctx.update({
                        'snapshot_title': current_track_info_snapshot.get('title'),
                        'snapshot_artist': current_track_info_snapshot.get('artist'),
                        'snapshot_is_playing': current_track_info_snapshot.get('is_playing'),
                        'snapshot_source': current_track_info_snapshot.get('source'),
                    })
                self.logger.debug(f"Music Screen (MusicPlugin): Nothing playing. Context: {debug_ctx}")
                self._last_nothing_playing_log_time = time.time()

            # Track 'Nothing Playing' duration to allow early exit from priority mode
            now_ts = time.time()
            if self._nothing_playing_since_ts is None:
                self._nothing_playing_since_ts = now_ts
            if self.music_priority_mode and self.music_priority_active:
                elapsed_np = now_ts - (self._nothing_playing_since_ts or now_ts)
                if elapsed_np >= self.nothing_playing_timeout_seconds:
                    self.logger.info("🎵 Nothing playing timeout reached - exiting music priority mode to resume rotation")
                    self._deactivate_music_priority()
                    self._nothing_playing_since_ts = None
                    # Render once more to show cleared state, then return
                    # (DisplayController will rotate modes)
                    return

            if not self.is_currently_showing_nothing_playing or perform_full_refresh_this_cycle:
                if perform_full_refresh_this_cycle or not self.is_currently_showing_nothing_playing:
                    self.display_manager.clear()
                
                text_width = self.display_manager.get_text_width("Nothing Playing", self.display_manager.regular_font)
                x_pos = (self.display_manager.matrix.width - text_width) // 2
                y_pos = (self.display_manager.matrix.height // 2) - 4
                self.display_manager.draw_text("Nothing Playing", x=x_pos, y=y_pos, font=self.display_manager.regular_font)
                self.display_manager.update_display()
                self.is_currently_showing_nothing_playing = True

            with self.track_info_lock: 
                self.scroll_position_title = 0
                self.scroll_position_artist = 0
                self.scroll_position_album = 0
                self.title_scroll_tick = 0
                self.artist_scroll_tick = 0
                self.album_scroll_tick = 0
                if self.album_art_image is not None or self.last_album_art_url is not None:
                    self.logger.debug("Clearing album art cache as 'Nothing Playing' is displayed.")
                    self.album_art_image = None
                    self.last_album_art_url = None
            return

        self.is_currently_showing_nothing_playing = False 
        # Reset NP timer when we have valid track info
        self._nothing_playing_since_ts = None

        if perform_full_refresh_this_cycle: 
            title_being_displayed = current_track_info_snapshot.get('title','N/A') if current_track_info_snapshot else "N/A"
            self.logger.debug(f"MusicPlugin: Resetting scroll positions for track '{title_being_displayed}' due to full refresh signal (periodic or event-driven).")
            self.scroll_position_title = 0
            self.scroll_position_artist = 0
            self.scroll_position_album = 0

        if not self.is_music_display_active and not perform_full_refresh_this_cycle: 
            self.logger.warning("MusicPlugin.display called when music display not active and not a full refresh. Aborting draw.")
            return
        elif not self.is_music_display_active and perform_full_refresh_this_cycle:
            pass

        if not perform_full_refresh_this_cycle: 
            self.display_manager.draw.rectangle([0, 0, self.display_manager.matrix.width, self.display_manager.matrix.height], fill=(0, 0, 0))

        matrix_height = self.display_manager.matrix.height
        matrix_width = self.display_manager.matrix.width
        
        # Album art should always fill the full height of the display
        album_art_size = matrix_height
        
        album_art_target_size = (album_art_size, album_art_size)
        album_art_x = 0
        album_art_y = 0
        text_area_x_start = album_art_x + album_art_size + 2
        text_area_width = matrix_width - text_area_x_start - 1 

        image_to_render_this_cycle = None
        target_art_url_for_current_track = current_track_info_snapshot.get('album_art_url')

        if target_art_url_for_current_track:
            if image_currently_in_cache and art_url_currently_in_cache == target_art_url_for_current_track:
                image_to_render_this_cycle = image_currently_in_cache
            else:
                self.logger.info(f"MusicPlugin: Fetching album art for: {target_art_url_for_current_track}")
                fetched_image = self._fetch_and_resize_image(target_art_url_for_current_track, album_art_target_size)
                if fetched_image:
                    self.logger.info(f"MusicPlugin: Album art for {target_art_url_for_current_track} fetched successfully.")
                    with self.track_info_lock:
                        latest_known_art_url_in_live_info = self.current_track_info.get('album_art_url') if self.current_track_info else None
                        if target_art_url_for_current_track == latest_known_art_url_in_live_info:
                            self.album_art_image = fetched_image
                            self.last_album_art_url = target_art_url_for_current_track 
                            image_to_render_this_cycle = fetched_image
                            self.logger.debug(f"Cached and will render new art for {target_art_url_for_current_track}")
                        else:
                            self.logger.info(f"MusicPlugin: Discarding fetched art for {target_art_url_for_current_track}; "
                                        f"track changed to '{self.current_track_info.get('title', 'N/A')}' "
                                        f"with art '{latest_known_art_url_in_live_info}' during fetch.")
                else:
                    self.logger.warning(f"MusicPlugin: Failed to fetch or process album art for {target_art_url_for_current_track}.")
                    with self.track_info_lock:
                        if self.last_album_art_url == target_art_url_for_current_track:
                            self.album_art_image = None 
        else:
            with self.track_info_lock:
                if self.album_art_image is not None or self.last_album_art_url is not None:
                    self.album_art_image = None
                    self.last_album_art_url = None 

        if image_to_render_this_cycle:
            self.display_manager.image.paste(image_to_render_this_cycle, (album_art_x, album_art_y))
        else:
            self.display_manager.draw.rectangle([album_art_x, album_art_y, 
                                                 album_art_x + album_art_size -1, album_art_y + album_art_size -1],
                                                 outline=(50,50,50), fill=(10,10,10))

        title = current_track_info_snapshot.get('title', ' ')
        artist = current_track_info_snapshot.get('artist', ' ')
        album = current_track_info_snapshot.get('album', ' ')
        
        # Debug logging for album display
        self.logger.debug(f"MusicPlugin.display: Track info - Title: '{title}', Artist: '{artist}', Album: '{album}'") 

        font_title = self.display_manager.small_font
        font_artist_album = self.display_manager.bdf_5x7_font

        # Calculate y positions as percentages of display height for scaling
        matrix_height = self.display_manager.matrix.height
        
        # Define positions as percentages (0.0 to 1.0) - these scale with display size
        ARTIST_Y_PERCENT = 0.34  # 34% from top  
        ALBUM_Y_PERCENT = 0.60   # 60% from top
        
        # Calculate dynamic font heights based on display size
        # For smaller displays (32px), use smaller line heights
        # For larger displays, scale up proportionally
        if matrix_height <= 32:
            LINE_HEIGHT_BDF = 7  # Optimized for 32px matrix
            FIXED_BDF_BASELINE_SHIFT = 6
        elif matrix_height <= 64:
            LINE_HEIGHT_BDF = 8  # Standard for 64px matrix
            FIXED_BDF_BASELINE_SHIFT = 7
        else:
            # For larger displays, scale proportionally
            LINE_HEIGHT_BDF = max(8, int(matrix_height * 0.125))  # 12.5% of height, min 8
            FIXED_BDF_BASELINE_SHIFT = max(6, int(matrix_height * 0.19))  # 19% of height, min 6
        
        # Calculate positions with proper scaling
        y_pos_title_top = max(1, int(matrix_height * 0.03))  # 3% from top, min 1px
        y_pos_artist_top = int(matrix_height * ARTIST_Y_PERCENT) + FIXED_BDF_BASELINE_SHIFT
        y_pos_album_top = int(matrix_height * ALBUM_Y_PERCENT) + FIXED_BDF_BASELINE_SHIFT
        
        # Debug logging for scaling calculations
        self.logger.debug(f"MusicPlugin.display: Display scaling - matrix: {matrix_width}x{matrix_height}, album_art: {album_art_size}px, LINE_HEIGHT_BDF: {LINE_HEIGHT_BDF}, positions - title: {y_pos_title_top}, artist: {y_pos_artist_top}, album: {y_pos_album_top}")
        
        TEXT_SCROLL_DIVISOR = 5

        # Title
        title_width = self.display_manager.get_text_width(title, font_title)
        current_title_display_text = title
        if title_width > text_area_width:
            if self.scroll_position_title >= len(title):
                self.scroll_position_title = 0
            current_title_display_text = title[self.scroll_position_title:] + "   " + title[:self.scroll_position_title]
        
        self.display_manager.draw_text(current_title_display_text, 
                                     x=text_area_x_start, y=y_pos_title_top, color=(255, 255, 255), font=font_title)
        if title_width > text_area_width:
            self.title_scroll_tick += 1
            if self.title_scroll_tick % TEXT_SCROLL_DIVISOR == 0:
                self.scroll_position_title = (self.scroll_position_title + 1) % len(title)
                self.title_scroll_tick = 0 
        else:
            self.scroll_position_title = 0
            self.title_scroll_tick = 0

        # Artist
        artist_width = self.display_manager.get_text_width(artist, font_artist_album)
        current_artist_display_text = artist
        if artist_width > text_area_width:
            if self.scroll_position_artist >= len(artist):
                self.scroll_position_artist = 0
            current_artist_display_text = artist[self.scroll_position_artist:] + "   " + artist[:self.scroll_position_artist]

        self.display_manager.draw_text(current_artist_display_text, 
                                      x=text_area_x_start, y=y_pos_artist_top, color=(180, 180, 180), font=font_artist_album)
        if artist_width > text_area_width:
            self.artist_scroll_tick += 1
            if self.artist_scroll_tick % TEXT_SCROLL_DIVISOR == 0:
                self.scroll_position_artist = (self.scroll_position_artist + 1) % len(artist)
                self.artist_scroll_tick = 0
        else:
            self.scroll_position_artist = 0
            self.artist_scroll_tick = 0
            
        # Album
        available_height_for_album = matrix_height - y_pos_album_top
        self.logger.debug(f"MusicPlugin.display: Album display check - matrix_height: {matrix_height}, y_pos_album_top: {y_pos_album_top}, available_height: {available_height_for_album}, LINE_HEIGHT_BDF: {LINE_HEIGHT_BDF}")
        
        if available_height_for_album >= LINE_HEIGHT_BDF: 
            album_width = self.display_manager.get_text_width(album, font_artist_album)
            self.logger.debug(f"MusicPlugin.display: Album '{album}' - width: {album_width}, text_area_width: {text_area_width}")
            
            # Display album if it fits or can be scrolled (maintains original behavior but adds scrolling)
            if album_width <= text_area_width:
                # Album fits without scrolling - display normally
                self.logger.debug(f"MusicPlugin.display: Drawing album '{album}' at ({text_area_x_start}, {y_pos_album_top}) - fits without scrolling")
                self.display_manager.draw_text(album, 
                                             x=text_area_x_start, y=y_pos_album_top, color=(150, 150, 150), font=font_artist_album)
                self.scroll_position_album = 0
                self.album_scroll_tick = 0
            elif album_width > text_area_width:
                # Album is too wide - scroll it
                current_album_display_text = album
                if self.scroll_position_album >= len(album):
                    self.scroll_position_album = 0
                current_album_display_text = album[self.scroll_position_album:] + "   " + album[:self.scroll_position_album]
                
                self.logger.debug(f"MusicPlugin.display: Drawing scrolling album '{current_album_display_text}' at ({text_area_x_start}, {y_pos_album_top}) - position: {self.scroll_position_album}")
                self.display_manager.draw_text(current_album_display_text, 
                                             x=text_area_x_start, y=y_pos_album_top, color=(150, 150, 150), font=font_artist_album)
                self.album_scroll_tick += 1
                if self.album_scroll_tick % TEXT_SCROLL_DIVISOR == 0:
                    self.scroll_position_album = (self.scroll_position_album + 1) % len(album)
                    self.album_scroll_tick = 0
        else:
            self.logger.debug(f"MusicPlugin.display: Album '{album}' not displayed - insufficient height (available: {available_height_for_album}, needed: {LINE_HEIGHT_BDF})")

        # Progress Bar - scale with display size
        if matrix_height <= 32:
            progress_bar_height = 3  # Standard for small displays
        elif matrix_height <= 64:
            progress_bar_height = 4  # Slightly thicker for medium displays
        else:
            progress_bar_height = max(4, int(matrix_height * 0.06))  # 6% of height, min 4px for large displays
        
        progress_bar_y = matrix_height - progress_bar_height - 1
        duration_ms = current_track_info_snapshot.get('duration_ms', 0)
        progress_ms = current_track_info_snapshot.get('progress_ms', 0)

        if duration_ms > 0:
            bar_total_width = text_area_width
            filled_ratio = progress_ms / duration_ms
            filled_width = int(filled_ratio * bar_total_width)

            self.display_manager.draw.rectangle([
                text_area_x_start, progress_bar_y, 
                text_area_x_start + bar_total_width -1, progress_bar_y + progress_bar_height -1
            ], outline=(60, 60, 60), fill=(30,30,30)) 
            
            if filled_width > 0:
                self.display_manager.draw.rectangle([
                    text_area_x_start, progress_bar_y, 
                    text_area_x_start + filled_width -1, progress_bar_y + progress_bar_height -1
                ], fill=(200, 200, 200)) 

        self.display_manager.update_display()

    def cleanup(self) -> None:
        """Clean up resources when plugin is unloaded."""
        self.logger.info("Music plugin: Cleaning up resources...")
        self.stop_polling()
        super().cleanup()
