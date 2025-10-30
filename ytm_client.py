import socketio
import logging
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor

# Ensure application-level logging is configured (as it is)
# logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Reduce verbosity of socketio and engineio libraries
logging.getLogger('socketio.client').setLevel(logging.WARNING)
logging.getLogger('socketio.server').setLevel(logging.WARNING)
logging.getLogger('engineio.client').setLevel(logging.WARNING)
logging.getLogger('engineio.server').setLevel(logging.WARNING)

# Define paths - must work from plugin directory
LEDMATRIX_ROOT = os.environ.get('LEDMATRIX_ROOT')
if not LEDMATRIX_ROOT:
    # Auto-detect: plugins/ledmatrix-music/../.. = LEDMatrix root
    LEDMATRIX_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
CONFIG_DIR = os.path.join(LEDMATRIX_ROOT, 'config')
CONFIG_PATH = os.path.join(CONFIG_DIR, 'config.json')
# Resolve to an absolute path
CONFIG_PATH = os.path.abspath(CONFIG_PATH)

# Path for the separate YTM authentication token file
YTM_AUTH_CONFIG_PATH = os.path.join(CONFIG_DIR, 'ytm_auth.json')
YTM_AUTH_CONFIG_PATH = os.path.abspath(YTM_AUTH_CONFIG_PATH)

class YTMClient:
    def __init__(self, update_callback=None):
        self.base_url = None
        self.ytm_token = None
        self.load_config() # Loads URL and token
        self.sio = socketio.Client(
            logger=False, 
            engineio_logger=False,
            reconnection=True,
            reconnection_attempts=0,  # Infinite attempts
            reconnection_delay=1,     # Initial delay in seconds
            reconnection_delay_max=10 # Maximum delay in seconds
        )
        self.last_known_track_data = None
        self.is_connected = False
        self._data_lock = threading.Lock()
        self._connection_event = threading.Event()
        self.external_update_callback = update_callback
        # For offloading external_update_callback to prevent blocking socketio thread
        self._callback_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='ytm_callback_worker')

        @self.sio.event(namespace='/api/v1/realtime')
        def connect():
            logging.info(f"Successfully connected to YTM Companion Socket.IO server at {self.base_url} on namespace /api/v1/realtime")
            self.is_connected = True
            self._connection_event.set()

        @self.sio.event(namespace='/api/v1/realtime')
        def connect_error(data):
            logging.error(f"YTM Companion Socket.IO connection failed for namespace /api/v1/realtime: {data}")
            self.is_connected = False
            self._connection_event.set()

        @self.sio.event(namespace='/api/v1/realtime')
        def disconnect():
            logging.info(f"Disconnected from YTM Companion Socket.IO server at {self.base_url} on namespace /api/v1/realtime")
            self.is_connected = False

        @self.sio.on('state-update', namespace='/api/v1/realtime')
        def on_state_update(data):
            # --- TEMPORARY DIAGNOSTIC LOGGING ---
            # --- END TEMPORARY DIAGNOSTIC LOGGING ---

            # Always update the full last_known_track_data for polling purposes
            with self._data_lock:
                self.last_known_track_data = data

            # Rich diagnostic logging of incoming event
            try:
                title = data.get('video', {}).get('title', 'N/A') if isinstance(data, dict) else 'N/A'
                author = data.get('video', {}).get('author', 'N/A') if isinstance(data, dict) else 'N/A'
                is_playing = (data.get('player', {}).get('trackState') == 1) if isinstance(data, dict) else None
                logging.debug(f"[YTMClient] socket event received: title='{title}', artist='{author}', is_playing={is_playing}")
            except Exception:
                pass

            if self.external_update_callback:
                logging.debug(f"--> Submitting YTM external_update_callback for title: {title} to executor")
                try:
                    # Offload the callback to the executor
                    self._callback_executor.submit(self.external_update_callback, data)
                except Exception as cb_ex:
                    logging.error(f"Error submitting YTMClient external_update_callback to executor: {cb_ex}")

    def load_config(self):
        default_url = "http://localhost:9863"
        self.base_url = default_url # Start with default
        
        # Load base_url from main config.json
        if not os.path.exists(CONFIG_PATH):
            logging.warning(f"Main config file not found at {CONFIG_PATH}. Using default YTM URL: {self.base_url}")
        else:
            try:
                with open(CONFIG_PATH, 'r') as f:
                    loaded_config = json.load(f)
                    # Prefer plugin-scoped config
                    plugin_cfg = loaded_config.get("ledmatrix-music", {})
                    self.base_url = plugin_cfg.get("ytm_companion_url", default_url)
                    if not self.base_url:
                        logging.warning("ytm_companion_url missing or empty in config.json ledmatrix-music section, using default.")
                        self.base_url = default_url
            except json.JSONDecodeError:
                logging.error(f"Error decoding JSON from main config {CONFIG_PATH}. Using default YTM URL.")
            except Exception as e:
                logging.error(f"Error loading ytm_companion_url from main config {CONFIG_PATH}: {e}. Using default YTM URL.")

        logging.debug(f"YTM Companion URL set to: {self.base_url}")

        if self.base_url and self.base_url.startswith("ws://"):
            self.base_url = "http://" + self.base_url[5:]
        elif self.base_url and self.base_url.startswith("wss://"):
            self.base_url = "https://" + self.base_url[6:]

        # Load ytm_token from ytm_auth.json
        self.ytm_token = None # Reset token before trying to load
        if os.path.exists(YTM_AUTH_CONFIG_PATH):
            try:
                with open(YTM_AUTH_CONFIG_PATH, 'r') as f:
                    auth_data = json.load(f)
                    self.ytm_token = auth_data.get("YTM_COMPANION_TOKEN")
                if self.ytm_token:
                    logging.info(f"YTM Companion token loaded from {YTM_AUTH_CONFIG_PATH}.")
                else:
                    logging.warning(f"YTM_COMPANION_TOKEN not found in {YTM_AUTH_CONFIG_PATH}. YTM features may be limited or disabled.")
            except json.JSONDecodeError:
                logging.error(f"Error decoding JSON from YTM auth file {YTM_AUTH_CONFIG_PATH}. YTM features may be limited or disabled.")
            except Exception as e:
                logging.error(f"Error loading YTM auth config {YTM_AUTH_CONFIG_PATH}: {e}. YTM features may be limited or disabled.")
        else:
            logging.warning(f"YTM auth file not found at {YTM_AUTH_CONFIG_PATH}. Run the authentication script to generate it. YTM features may be limited or disabled.")

    def connect_client(self, timeout=10):
        if not self.ytm_token:
            logging.warning("No YTM token loaded. Cannot connect to YTM Socket.IO. Run authentication script.")
            self.is_connected = False
            return False

        if self.is_connected:
            logging.debug("YTM client already connected.")
            return True

        logging.info(f"Attempting to connect to YTM Socket.IO server: {self.base_url}")
        auth_payload = {"token": self.ytm_token}

        try:
            self._connection_event.clear()
            self.sio.connect(
                self.base_url,
                transports=['websocket'],
                wait_timeout=timeout,
                namespaces=['/api/v1/realtime'],
                auth=auth_payload
            )
            event_wait_timeout = timeout + 5
            if not self._connection_event.wait(timeout=event_wait_timeout):
                logging.warning(f"YTM Socket.IO connection event not received within {event_wait_timeout}s (connect timeout was {timeout}s).")
                self.is_connected = False
                return False
            # Connection success/failure is logged by connect/connect_error events
            return self.is_connected
        except socketio.exceptions.ConnectionError as e:
            logging.error(f"YTM Socket.IO connection error: {e}")
            self.is_connected = False
            return False
        except Exception as e:
            logging.error(f"Unexpected error during YTM Socket.IO connection: {e}")
            self.is_connected = False
            return False

    def is_available(self):
        if not self.ytm_token:
            return False
        return self.is_connected

    def get_current_track(self):
        if not self.is_connected:
            return None

        with self._data_lock:
            if self.last_known_track_data:
                return self.last_known_track_data
            else:
                return None

    def disconnect_client(self):
        if self.is_connected:
            self.sio.disconnect()
            logging.info("YTM Socket.IO client disconnected.")
            self.is_connected = False
        else:
            logging.debug("YTM Socket.IO client already disconnected or not connected.")

    def shutdown(self):
        """Shuts down the callback executor."""
        logging.info("YTMClient: Shutting down callback executor...")
        if self._callback_executor:
            self._callback_executor.shutdown(wait=True) # Wait for pending tasks to complete
            self._callback_executor = None # Clear reference
            logging.info("YTMClient: Callback executor shut down.")
        else:
            logging.debug("YTMClient: Callback executor already None or not initialized.")

# Example Usage (for testing - needs to be adapted for Socket.IO async nature)
# if __name__ == '__main__':
# client = YTMClient()
# if client.connect_client(): 
# print("YTM Server is available (Socket.IO).")
# try:
# for _ in range(10): # Poll for a few seconds
# track = client.get_current_track()
# if track:
# print(json.dumps(track, indent=2))
# else:
# print("No track currently playing or error fetching (Socket.IO).")
# time.sleep(2)
# finally:
# client.disconnect_client()
# else:
# print(f"YTM Server not available at {client.base_url} (Socket.IO). Is YTMD running with companion server enabled and token generated?")
