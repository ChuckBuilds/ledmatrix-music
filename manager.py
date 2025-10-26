"""
Music Plugin for LEDMatrix

Perfect replica of the original MusicManager with identical visual appearance,
functionality, and behavior. Displays now playing music from Spotify or YouTube Music
with album art, track info, and smooth scrolling exactly as in the original system.

Features:
- Exact visual layout: Album art on left half, text on right half
- Sophisticated scrolling system for title, artist, and album independently
- Playback progress bar at bottom
- Real-time YTM integration with event-driven updates
- Threading and state management identical to original
- Font system using display manager fonts
- Color scheme matching original (white title, dim white artist, dimmer album)
- Force refresh and immediate update capabilities

API Version: 1.0.0
"""

import time
import threading
from enum import Enum, auto
import logging
import json
import os
from io import BytesIO
import requests
from typing import Union, Dict, Any
from PIL import Image, ImageEnhance
import queue

# Import music clients from plugin directory
import sys

# Add plugin directory to path to import local clients
PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
if PLUGIN_DIR not in sys.path:
    sys.path.insert(0, PLUGIN_DIR)

try:
    from spotify_client import SpotifyClient
    from ytm_client import YTMClient
except ImportError:
    SpotifyClient = None
    YTMClient = None

from src.plugin_system.base_plugin import BasePlugin

logger = logging.getLogger(__name__)


class MusicSource(Enum):
    NONE = auto()
    SPOTIFY = auto()
    YTM = auto()


class MusicPlugin(BasePlugin):
    """
    Perfect replica of the original MusicManager with identical functionality.

    This plugin mirrors the exact behavior, visual appearance, and state management
    of the original MusicManager from the LEDMatrix system.
    """

    def __init__(self, plugin_id: str, config: Dict[str, Any],
                 display_manager, cache_manager, plugin_manager):
        """Initialize the music plugin with exact same logic as original."""
        super().__init__(plugin_id, config, display_manager, cache_manager, plugin_manager)

        self.spotify = None
        self.ytm = None
        self.current_track_info = None
        self.current_source = MusicSource.NONE
        self.polling_interval = 2  # Default
        self.enabled = False  # Default
        self.preferred_source = "spotify"  # Default changed from "auto"
        self.stop_event = threading.Event()
        self.track_info_lock = threading.Lock()  # Added lock

        # Display related attributes moved from DisplayController
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
        self.is_music_display_active = False  # New state variable
        self.is_currently_showing_nothing_playing = False  # To prevent flashing
        self._needs_immediate_full_refresh = False  # Flag for forcing refresh from YTM updates
        self.ytm_event_data_queue = queue.Queue(maxsize=1)  # Queue for event data

        self.poll_thread = None

        # Load config first
        self._load_config()
        # Initialize clients based on loaded config
        self._initialize_clients()

        self.logger.info("Music plugin initialized - Enabled: %s, Source: %s", self.enabled, self.preferred_source)
    
    def _load_config(self):
        """Load configuration exactly as in original MusicManager."""
        default_interval = 2
        self.enabled = False  # Assume disabled until config proves otherwise

        # Use the config that was already loaded and passed to us
        if self.config is None:
            self.logger.warning("No config provided to MusicPlugin. Music plugin disabled.")
            return

        try:
            music_config = self.config.get("music", {})

            self.enabled = music_config.get("enabled", False)
            if not self.enabled:
                self.logger.info("Music plugin is disabled in config.json (top level 'enabled': false).")
                return  # Don't proceed further if disabled

            self.polling_interval = music_config.get("POLLING_INTERVAL_SECONDS", default_interval)
            configured_source = music_config.get("preferred_source", "spotify").lower()

            if configured_source in ["spotify", "ytm"]:
                self.preferred_source = configured_source
                self.logger.info(f"Music plugin enabled. Polling interval: {self.polling_interval}s. Preferred source: {self.preferred_source}")
            else:
                self.logger.warning("Invalid 'preferred_source' ('%s') in config.json. Must be 'spotify' or 'ytm'. Music plugin disabled.", configured_source)
                self.enabled = False
                return

        except Exception as e:
            self.logger.error("Error loading music config: %s. Music plugin disabled.", e)
            self.enabled = False

    def _initialize_clients(self):
        """Initialize music clients exactly as in original."""
        # Only initialize if the plugin is enabled
        if not self.enabled:
            self.spotify = None
            self.ytm = None
            return

        self.logger.info("Initializing music clients...")

        # Initialize Spotify Client if needed
        if self.preferred_source == "spotify":
            try:
                # Get credentials from config
                client_id = self.config.get('spotify_client_id') or os.environ.get('SPOTIFY_CLIENT_ID')
                client_secret = self.config.get('spotify_client_secret') or os.environ.get('SPOTIFY_CLIENT_SECRET')
                redirect_uri = self.config.get('spotify_redirect_uri', 'http://localhost:8888/callback')

                if client_id and client_secret and redirect_uri:
                    self.spotify = SpotifyClient(client_id, client_secret, redirect_uri)
                    if not self.spotify.is_authenticated():
                        self.logger.warning("Spotify client initialized but not authenticated. Please run authenticate_spotify.py if you want to use Spotify.")
                    else:
                        self.logger.info("Spotify client authenticated.")
                else:
                    self.logger.warning("Spotify credentials not provided. Please set credentials in config or environment variables.")
                    self.spotify = None
            except Exception as e:
                self.logger.error(f"Failed to initialize Spotify client: {e}")
                self.spotify = None
        else:
            self.spotify = None  # Ensure it's None if not preferred

        # Initialize YTM Client if needed
        if self.preferred_source == "ytm":
            try:
                ytm_url = self.config.get('YTM_COMPANION_URL', 'http://localhost:9863')
                self.ytm = YTMClient(base_url=ytm_url, update_callback=self._handle_ytm_direct_update)
                self.logger.info(f"YTMClient initialized. Connection will be managed on-demand. Configured URL: {ytm_url}")
            except Exception as e:
                self.logger.error(f"Failed to initialize YTM client: {e}")
                self.ytm = None
        else:
            self.ytm = None  # Ensure it's None if not preferred
    def activate_music_display(self):
        """Activate music display - exact replica of original method."""
        self.logger.info("Music display activated.")
        self.is_music_display_active = True
        if self.ytm and self.preferred_source == "ytm":
            if not self.ytm.is_connected:
                self.logger.info("Attempting to connect YTM client due to music display activation.")
                if self.ytm.connect_client(timeout=10):
                    self.logger.info("YTM client connected successfully on display activation.")
                    # YTM often sends an immediate state update on connect, handled by _handle_ytm_direct_update.
                    # If not, or to be sure, we can fetch current state.
                    latest_data = self.ytm.get_current_track()
                    if latest_data:
                        self.logger.debug("YTM Activate Sync: Processing current track data after successful connection.")
                        self._process_ytm_data_update(latest_data, "YTM Activate Sync")
                else:
                    self.logger.warning("YTM client failed to connect on display activation.")
            else:  # Already connected
                self.logger.debug("YTM client already connected during music display activation. Syncing state.")
                latest_data = self.ytm.get_current_track()  # Get latest from YTMClient's cache
                if latest_data:
                    self._process_ytm_data_update(latest_data, "YTM Activate Sync")
                else:
                    self.logger.debug("YTM Activate Sync: No track data available from connected YTM client.")
                    # Process "Nothing Playing" to ensure state is clean if YTM has nothing.
                    self._process_ytm_data_update(None, "YTM Activate Sync (No Data)")

    def deactivate_music_display(self):
        """Deactivate music display - exact replica of original method."""
        self.logger.info("Music display deactivated.")
        self.is_music_display_active = False
        if self.ytm and self.ytm.is_connected:
            self.logger.info("Disconnecting YTM client due to music display deactivation.")
            self.ytm.disconnect_client()

    def _handle_ytm_direct_update(self, ytm_data):
        """Handle direct state updates from YTM - exact replica of original."""
        if not ytm_data:
            return

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

        # The display update will be handled by the display loop picking up the queue/flag

    def _process_ytm_data_update(self, ytm_data, source_description: str):
        """
        Core processing logic for YTM data - exact replica of original.

        Updates self.current_track_info, handles album art, queues data for display,
        and determines if the update is significant.
        """
        if not ytm_data:  # Handle case where ytm_data might be None
            simplified_info = self.get_simplified_track_info(None, MusicSource.NONE)
        else:
            ytm_player_info = ytm_data.get('player', {})
            is_actually_playing_ytm = (ytm_player_info.get('trackState') == 1) and not ytm_player_info.get('adPlaying', False)
            simplified_info = self.get_simplified_track_info(ytm_data if is_actually_playing_ytm else None,
                                                           MusicSource.YTM if is_actually_playing_ytm else MusicSource.NONE)

        significant_change_detected = False
        processed_a_meaningful_update = False  # Renamed from has_changed

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

                self.current_track_info = simplified_info  # Update main state
                self.logger.debug(f"MusicPlugin._process_ytm_data_update ({source_description}): POST-UPDATE (inside lock) - self.current_track_info now: {json.dumps(self.current_track_info)}")

                # Determine current source based on this update
                if simplified_info.get('source') == 'YouTube Music' and simplified_info.get('is_playing'):
                    self.current_source = MusicSource.YTM
                elif self.current_source == MusicSource.YTM and not simplified_info.get('is_playing'):  # YTM stopped
                    self.current_source = MusicSource.NONE
                elif simplified_info.get('source') == 'None':
                    self.current_source = MusicSource.NONE

                new_album_art_url = simplified_info.get('album_art_url')

                self.logger.debug(f"({source_description}) Track info comparison: simplified_info != self.current_track_info was TRUE.")
                self.logger.debug(f"({source_description}) Old Album Art URL: {old_album_art_url}, New Album Art URL: {new_album_art_url}")

                if new_album_art_url != old_album_art_url:
                    self.logger.info(f"({source_description}) Album art URL changed. Clearing self.album_art_image to force re-fetch.")
                    self.album_art_image = None  # Clear cached image
                    self.last_album_art_url = new_album_art_url  # Update last known URL
                elif not self.last_album_art_url and new_album_art_url:  # New art URL appeared
                    self.logger.info(f"({source_description}) New album art URL appeared. Clearing image.")
                    self.album_art_image = None
                    self.last_album_art_url = new_album_art_url
                elif new_album_art_url is None and old_album_art_url is not None:  # Art URL disappeared
                    self.logger.info(f"({source_description}) Album art URL disappeared. Clearing image and URL.")
                    self.album_art_image = None
                    self.last_album_art_url = None

                display_title = self.current_track_info.get('title', 'None')

                # Throttle track update logging to reduce spam
                current_time = time.time()
                should_log = False

                # Log if track title changed or if enough time has passed
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
                # simplified_info IS THE SAME as self.current_track_info
                processed_a_meaningful_update = False
                self.logger.debug(f"({source_description}) No change in simplified track info (simplified_info == self.current_track_info).")
                if self.current_track_info is None and simplified_info.get('title') != 'Nothing Playing':
                    # This ensures that if current_track_info was None and simplified_info is valid,
                    # it's treated as processed and current_track_info gets set.
                    significant_change_detected = True  # First load is always significant
                    processed_a_meaningful_update = True
                    self.current_track_info = simplified_info
                    # Also log first valid track data with throttling
                    display_title = simplified_info.get('title', 'None')
                    current_time = time.time()

                    # For first valid data, always log but update throttling variables
                    self.logger.info(f"({source_description}) First valid track data received (was None), marking significant. Track: {display_title}")
                    self.last_track_log_time = current_time
                    self.last_logged_track_title = display_title

        # Queueing logic - for events or activate_display syncs, not for polling.
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
        elif processed_a_meaningful_update:  # A change occurred but wasn't "significant" (e.g. just progress)
            self.logger.debug(f"({source_description}) Minor track data update (e.g. progress). Display will update without full refresh.")

        return simplified_info, significant_change_detected
    
    def update(self) -> None:
        """Update method required by BasePlugin - starts/stops polling."""
        if not self.enabled:
            return

        # Only start polling if enabled and not already running
        if not self.poll_thread or not self.poll_thread.is_alive():
            if self.spotify or self.ytm:
                self.stop_event.clear()
                self.poll_thread = threading.Thread(target=self._poll_music_data, daemon=True)
                self.poll_thread.start()
                self.logger.info("Music polling started.")

    def _poll_music_data(self):
        """Continuously polls music sources for updates - exact replica of original."""
        if not self.enabled:
             self.logger.warning("Polling attempted while music plugin is disabled. Stopping polling thread.")
             return

        while not self.stop_event.is_set():
            source_for_callback = MusicSource.NONE  # Used to determine if callback is needed
            significant_change_for_callback = False
            simplified_info_for_callback = None

            if self.preferred_source == "spotify" and self.spotify and self.spotify.is_authenticated():
                try:
                    spotify_track = self.spotify.get_current_track()
                    if spotify_track and spotify_track.get('is_playing'):
                        polled_track_info_data = spotify_track
                        source_for_callback = MusicSource.SPOTIFY
                        simplified_info_poll = self.get_simplified_track_info(polled_track_info_data, MusicSource.SPOTIFY)

                        with self.track_info_lock:
                            if simplified_info_poll != self.current_track_info:
                                # Check for significant changes (same logic as YTM)
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
                                    self._needs_immediate_full_refresh = True  # Reset display state
                                    self.logger.info("Polling Spotify: Significant change detected.")
                                else:
                                    self.logger.debug("Polling Spotify: Minor update (progress only), no full refresh needed.")

                                # Handle album art for Spotify if needed
                                new_album_art_url = simplified_info_poll.get('album_art_url')
                                if new_album_art_url != self.last_album_art_url:
                                     self.album_art_image = None
                                     self.last_album_art_url = new_album_art_url

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
                                self._needs_immediate_full_refresh = True  # Reset display state
                                self.album_art_image = None  # Clear art
                                self.last_album_art_url = None
                                self.logger.info("Polling Spotify: Player stopped. Updating to Nothing Playing.")

                except Exception as e:
                    self.logger.error(f"Error polling Spotify: {e}")
                    if "token" in str(e).lower():
                        self.logger.warning("Spotify auth token issue detected during polling.")

            elif self.preferred_source == "ytm" and self.ytm:  # YTM is preferred
                if self.ytm.is_connected:
                    try:
                        ytm_track_data = self.ytm.get_current_track()  # Data from YTMClient's cache
                        # Let _process_ytm_data_update handle the logic
                        simplified_info_for_callback, significant_change_for_callback = self._process_ytm_data_update(ytm_track_data, "YTM Poll")
                        source_for_callback = MusicSource.YTM  # Mark that YTM was polled
                        # Note: _process_ytm_data_update updates self.current_track_info
                        if significant_change_for_callback:
                             self.logger.debug(f"Polling YTM: Change detected via _process_ytm_data_update. Title: {simplified_info_for_callback.get('title')}")
                        else:
                             self.logger.debug(f"Polling YTM: No change detected via _process_ytm_data_update. Title: {simplified_info_for_callback.get('title')}")

                    except Exception as e:
                        self.logger.error(f"Error during YTM poll processing: {e}")
                else:  # YTM not connected
                    self.logger.debug("Skipping YTM poll: Client not connected. Will attempt reconnect on next cycle if display active.")
                    if self.is_music_display_active:
                        self.logger.info("YTM is preferred and display active, attempting reconnect during poll cycle.")
                        if self.ytm.connect_client(timeout=5):
                            self.logger.info("YTM reconnected during poll cycle. Will process data on next poll/event.")
                            # Potentially sync state right here?
                            latest_data = self.ytm.get_current_track()
                            if latest_data:
                                simplified_info_for_callback, significant_change_for_callback = self._process_ytm_data_update(latest_data, "YTM Poll Reconnect Sync")
                                source_for_callback = MusicSource.YTM
                        else:
                            self.logger.warning("YTM failed to reconnect during poll cycle.")
                            # If YTM was the source, and failed to reconnect, set to Nothing Playing
                            with self.track_info_lock:
                                if self.current_source == MusicSource.YTM:
                                    simplified_info_for_callback = self.get_simplified_track_info(None, MusicSource.NONE)
                                    self.current_track_info = simplified_info_for_callback
                                    self.current_source = MusicSource.NONE
                                    significant_change_for_callback = True
                                    self.album_art_image = None
                                    self.last_album_art_url = None
                                    self.logger.info("Polling YTM: Reconnect failed. Updating to Nothing Playing.")

            time.sleep(self.polling_interval)

    def get_simplified_track_info(self, track_data, source):
        """Provides a consistent format for track info regardless of source - exact replica of original."""
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

            # Primary conditions for "Nothing Playing" for YTM:
            # 1. An ad is currently playing.
            # 2. Essential metadata (title or artist) is missing from the source data.
            if player_info.get('adPlaying', False):
                self.logger.debug("YTM (get_simplified_track_info): Ad is playing, reporting as Nothing Playing.")
                return nothing_playing_info.copy()

            if not title or not artist:
                self.logger.debug(f"YTM (get_simplified_track_info): No title ('{title}') or artist ('{artist}'), reporting as Nothing Playing.")
                return nothing_playing_info.copy()

            # If we've reached this point, we have a title and artist, and it's not an ad.
            # Proceed to determine the accurate playback state and construct full track details.
            track_state = player_info.get('trackState')
            # is_playing_ytm is True ONLY if trackState is 1 (actively playing).
            # Other states: 0 (loading/buffering), 2 (paused), 3 (stopped/ended) will result in is_playing_ytm = False.
            is_playing_ytm = (track_state == 1)

            # self.logger.debug(f"[get_simplified_track_info YTM] Title: {title}, Artist: {artist}, TrackState: {track_state}, IsPlayingYTM: {is_playing_ytm}")

            album = video_info.get('album')
            duration_seconds = video_info.get('durationSeconds')
            duration_ms = int(duration_seconds * 1000) if duration_seconds is not None else 0
            progress_seconds = player_info.get('videoProgress')
            progress_ms = int(progress_seconds * 1000) if progress_seconds is not None else 0

            return {
                'source': 'YouTube Music',
                'title': title,
                'artist': artist,
                'album': album if album else '',  # Ensure album is not None
                'album_art_url': album_art_url,
                'duration_ms': duration_ms,
                'progress_ms': progress_ms,
                'is_playing': is_playing_ytm,  # This now accurately reflects if YTM reports the track as playing
            }
        else:
            # This covers cases where source is NONE, or track_data is None for Spotify/YTM
            return nothing_playing_info.copy()

    def get_current_display_info(self):
        """Returns the currently stored track information for display - exact replica of original."""
        with self.track_info_lock:
            return self.current_track_info.copy() if self.current_track_info else None
    def _fetch_and_resize_image(self, url: str, target_size: tuple) -> Union[Image.Image, None]:
        """Fetches an image from a URL, resizes it, and returns a PIL Image object - exact replica of original."""
        if not url:
            return None
        try:
            response = requests.get(url, timeout=5)  # 5-second timeout for image download
            response.raise_for_status()  # Raise an exception for bad status codes

            img_data = BytesIO(response.content)
            img = Image.open(img_data)

            # Ensure image is RGB for compatibility with the matrix
            img = img.convert("RGB")

            img.thumbnail(target_size, Image.Resampling.LANCZOS)

            # Enhance contrast
            enhancer_contrast = ImageEnhance.Contrast(img)
            img = enhancer_contrast.enhance(1.3)  # Adjust 1.3 as needed

            # Enhance saturation (Color)
            enhancer_saturation = ImageEnhance.Color(img)
            img = enhancer_saturation.enhance(1.3)  # Adjust 1.3 as needed

            final_img = Image.new("RGB", target_size, (0,0,0))  # Black background
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
    
    def display(self, force_clear: bool = False) -> None:
        """
        Render the music display - exact replica of original MusicManager display method.

        This method implements the exact same visual layout, scrolling, and display logic
        as the original MusicManager from the LEDMatrix system.
        """
        perform_full_refresh_this_cycle = force_clear
        art_url_currently_in_cache = None  # Initialize to None
        image_currently_in_cache = None   # Initialize to None

        # Check if an event previously signaled a need for immediate refresh (and populated the queue)
        initial_data_from_queue_due_to_event = None
        if self._needs_immediate_full_refresh:
            self.logger.debug("MusicPlugin.display: _needs_immediate_full_refresh is True (event-driven).")
            perform_full_refresh_this_cycle = True  # An event demanding refresh also implies a full refresh
            try:
                # Try to get data now, it's the freshest from the event
                initial_data_from_queue_due_to_event = self.ytm_event_data_queue.get_nowait()
                self.logger.info(f"MusicPlugin.display: Got data from ytm_event_data_queue (due to event flag): Title {initial_data_from_queue_due_to_event.get('title') if initial_data_from_queue_due_to_event else 'None'}")
            except queue.Empty:
                self.logger.warning("MusicPlugin.display: _needs_immediate_full_refresh was true, but queue empty. Will refresh with current_track_info.")
            self._needs_immediate_full_refresh = False  # Consume the event flag

        current_track_info_snapshot = None

        if perform_full_refresh_this_cycle:
            log_msg_detail = f"force_clear_from_plugin={force_clear}, event_driven_refresh_attempted={'Yes' if initial_data_from_queue_due_to_event is not None else 'No'}"
            self.logger.debug(f"MusicPlugin.display: Performing full refresh cycle. Details: {log_msg_detail}")

            self.display_manager.clear()
            self.activate_music_display()  # Call this BEFORE snapshotting data for this cycle.
                                        # This might trigger YTM events if it reconnects.
            self.last_periodic_refresh_time = time.time()  # Update timer *after* potential processing in activate

            data_from_queue_post_activate = None
            # Check queue again, activate_music_display might have put fresh data via _process_ytm_data_update
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
        else:  # This is the correctly paired else for 'if perform_full_refresh_this_cycle:'
            with self.track_info_lock:
                current_track_info_snapshot = self.current_track_info.copy() if self.current_track_info else None

        # --- Update cache variables after snapshot is finalized ---
        with self.track_info_lock:  # Ensure thread-safe access to shared cache attributes
            art_url_currently_in_cache = self.last_album_art_url
            image_currently_in_cache = self.album_art_image

        snapshot_title_for_log = current_track_info_snapshot.get('title', 'N/A') if current_track_info_snapshot else 'N/A'
        if perform_full_refresh_this_cycle:
             self.logger.debug(f"MusicPlugin.display (Full Refresh Render): Using snapshot - Title: '{snapshot_title_for_log}'")

        # --- Original Nothing Playing Logic ---
        if not current_track_info_snapshot or current_track_info_snapshot.get('title') == 'Nothing Playing':
            if not hasattr(self, '_last_nothing_playing_log_time') or time.time() - getattr(self, '_last_nothing_playing_log_time', 0) > 30:
                self.logger.debug("Music Screen (MusicPlugin): Nothing playing or info explicitly 'Nothing Playing'.")
                self._last_nothing_playing_log_time = time.time()

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

        if perform_full_refresh_this_cycle:
            title_being_displayed = current_track_info_snapshot.get('title','N/A') if current_track_info_snapshot else "N/A"
            self.logger.debug(f"MusicPlugin: Resetting scroll positions for track '{title_being_displayed}' due to full refresh signal (periodic or event-driven).")
            self.scroll_position_title = 0
            self.scroll_position_artist = 0
            self.scroll_position_album = 0

        if not self.is_music_display_active and not perform_full_refresh_this_cycle:
             # If display wasn't active, and this isn't a full refresh cycle that would activate it,
             # then we shouldn't proceed to draw music. This case might be rare if DisplayController
             # manages music display activation properly on mode switch.
             self.logger.warning("MusicPlugin.display called when music display not active and not a full refresh. Aborting draw.")
             return
        elif not self.is_music_display_active and perform_full_refresh_this_cycle:
             # This is handled by activate_music_display() called within the full_refresh_this_cycle block
             pass

        if not perform_full_refresh_this_cycle:
            self.display_manager.draw.rectangle([0, 0, self.display_manager.matrix.width, self.display_manager.matrix.height], fill=(0, 0, 0))

        matrix_height = self.display_manager.matrix.height
        album_art_size = matrix_height  # Was matrix_height - 2
        album_art_target_size = (album_art_size, album_art_size)
        album_art_x = 0  # Was 1
        album_art_y = 0  # Was 1
        text_area_x_start = album_art_x + album_art_size + 2
        text_area_width = self.display_manager.matrix.width - text_area_x_start - 1

        image_to_render_this_cycle = None
        target_art_url_for_current_track = current_track_info_snapshot.get('album_art_url')

        if target_art_url_for_current_track:
            if image_currently_in_cache and art_url_currently_in_cache == target_art_url_for_current_track:
                image_to_render_this_cycle = image_currently_in_cache
                # self.logger.debug(f"Using cached album art for {target_art_url_for_current_track}") # Can be noisy
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
            # self.logger.debug(f"No album art URL for track: {current_track_info_snapshot.get('title', 'N/A')}. Clearing cache.")
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

        font_title = self.display_manager.small_font
        font_artist_album = self.display_manager.bdf_5x7_font

        # Get line height for the TTF title font
        ascent, descent = font_title.getmetrics()

        # Use a static value for the BDF font's line height
        LINE_HEIGHT_BDF = 8  # Fixed pixel height for 5x7 BDF font

        # Calculate y positions as percentages of display height for scaling
        matrix_height = self.display_manager.matrix.height

        # Define positions as percentages (0.0 to 1.0)
        ARTIST_Y_PERCENT = 0.34  # 34% from top
        ALBUM_Y_PERCENT = 0.60   # 60% from top

        # Use fixed positioning to ensure consistency across all songs
        # Add a consistent font baseline shift for BDF fonts (not dynamic)
        FIXED_BDF_BASELINE_SHIFT = 6  # Fixed shift for proper BDF font positioning

        y_pos_title_top = 1
        y_pos_artist_top = int(matrix_height * ARTIST_Y_PERCENT) + FIXED_BDF_BASELINE_SHIFT
        y_pos_album_top = int(matrix_height * ALBUM_Y_PERCENT) + FIXED_BDF_BASELINE_SHIFT

        TEXT_SCROLL_DIVISOR = 5

        # --- Title ---
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

        # --- Artist ---
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

        # --- Album ---
        if (matrix_height - y_pos_album_top) >= LINE_HEIGHT_BDF:
            album_width = self.display_manager.get_text_width(album, font_artist_album)
            # Display album if it fits or can be scrolled (maintains original behavior but adds scrolling)
            if album_width <= text_area_width:
                # Album fits without scrolling - display normally
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

                self.display_manager.draw_text(current_album_display_text,
                                             x=text_area_x_start, y=y_pos_album_top, color=(150, 150, 150), font=font_artist_album)
                self.album_scroll_tick += 1
                if self.album_scroll_tick % TEXT_SCROLL_DIVISOR == 0:
                    self.scroll_position_album = (self.scroll_position_album + 1) % len(album)
                    self.album_scroll_tick = 0

        # --- Progress Bar ---
        progress_bar_height = 3
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

    def stop_polling(self):
        """Stops the music polling thread - exact replica of original."""
        self.logger.info("Music plugin: Stopping polling thread...")
        self.stop_event.set()
        if self.poll_thread and self.poll_thread.is_alive():
            self.poll_thread.join(timeout=self.polling_interval + 1)  # Wait for thread to finish
        if self.poll_thread and self.poll_thread.is_alive():
            self.logger.warning("Music plugin: Polling thread did not terminate cleanly.")
        else:
            self.logger.info("Music plugin: Polling thread stopped.")
        self.poll_thread = None  # Clear the thread object
        # Also ensure YTM client is disconnected when polling stops completely
        if self.ytm:
            self.logger.info("MusicPlugin: Shutting down YTMClient resources.")
            if self.ytm.is_connected:
                 self.ytm.disconnect_client()
            self.ytm.shutdown()  # Call the new shutdown method for the executor

    def on_enable(self) -> None:
        """Called when plugin is enabled - exact replica of original."""
        super().on_enable()
        # Start polling when enabled
        self.update()

    def on_disable(self) -> None:
        """Called when plugin is disabled - exact replica of original."""
        super().on_disable()
        self.stop_polling()
    def get_display_duration(self) -> float:
        """Get display duration from config."""
        return self.config.get('display_duration', 10.0)

    def get_info(self) -> Dict[str, Any]:
        """Return plugin info for web UI - exact replica of original."""
        info = super().get_info()
        with self.track_info_lock:
            info.update({
                'preferred_source': self.preferred_source,
                'is_playing': bool(self.current_track_info and self.current_track_info.get('is_playing')),
                'current_track': self.current_track_info.get('title') if self.current_track_info else None,
                'current_artist': self.current_track_info.get('artist') if self.current_track_info else None,
                'current_album': self.current_track_info.get('album') if self.current_track_info else None,
                'current_source': self.current_source.name if self.current_source else 'NONE',
                'is_connected': {
                    'spotify': self.spotify.is_authenticated() if self.spotify else False,
                    'ytm': self.ytm.is_connected if self.ytm else False
                }
            })
        return info

    def cleanup(self) -> None:
        """Cleanup resources - exact replica of original."""
        self.stop_polling()
        self.current_track_info = None
        self.album_art_image = None
        self.logger.info("Music plugin cleaned up")

    def validate_config(self) -> bool:
        """Validate plugin configuration."""
        if not super().validate_config():
            return False

        # Validate music-specific config
        music_config = self.config.get('music', {})
        if not isinstance(music_config.get('enabled', False), bool):
            self.logger.error("'music.enabled' must be a boolean")
            return False

        preferred_source = music_config.get('preferred_source', 'spotify')
        if preferred_source not in ['spotify', 'ytm']:
            self.logger.error(f"'music.preferred_source' must be 'spotify' or 'ytm', got '{preferred_source}'")
            return False

        return True

