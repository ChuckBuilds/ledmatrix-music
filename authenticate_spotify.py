from spotipy.oauth2 import SpotifyOAuth
import logging
import json
import os
import sys

# Setup basic logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Define paths - must work from plugin directory
LEDMATRIX_ROOT = os.environ.get('LEDMATRIX_ROOT')
if not LEDMATRIX_ROOT:
    # Auto-detect: plugins/ledmatrix-music/../.. = LEDMatrix root
    LEDMATRIX_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
CONFIG_DIR = os.path.join(LEDMATRIX_ROOT, 'config')
SECRETS_PATH = os.path.join(CONFIG_DIR, 'config_secrets.json')
SPOTIFY_AUTH_CACHE_PATH = os.path.join(CONFIG_DIR, 'spotify_auth.json') # Explicit cache path

# Resolve to absolute paths
CONFIG_DIR = os.path.abspath(CONFIG_DIR)
SECRETS_PATH = os.path.abspath(SECRETS_PATH)
SPOTIFY_AUTH_CACHE_PATH = os.path.abspath(SPOTIFY_AUTH_CACHE_PATH)

SCOPE = "user-read-currently-playing user-read-playback-state"

def load_spotify_credentials():
    """Loads Spotify credentials from config_secrets.json."""
    if not os.path.exists(SECRETS_PATH):
        logging.error(f"Secrets file not found at {SECRETS_PATH}")
        return None, None, None

    try:
        with open(SECRETS_PATH, 'r', encoding='utf-8') as f:
            secrets = json.load(f)
            
            # Try new location first: ledmatrix-music plugin section with new field names
            plugin_secrets = secrets.get("ledmatrix-music", {})
            cred_client_id = plugin_secrets.get("spotify_client_id")
            cred_client_secret = plugin_secrets.get("spotify_client_secret")
            cred_redirect_uri = plugin_secrets.get("spotify_redirect_uri")
            
            # Fall back to old location: music section with old field names (backward compatibility)
            if not all([cred_client_id, cred_client_secret]):
                music_secrets = secrets.get("music", {})
                if not cred_client_id:
                    cred_client_id = music_secrets.get("SPOTIFY_CLIENT_ID")
                if not cred_client_secret:
                    cred_client_secret = music_secrets.get("SPOTIFY_CLIENT_SECRET")
                if not cred_redirect_uri:
                    cred_redirect_uri = music_secrets.get("SPOTIFY_REDIRECT_URI")
            
            # Default redirect URI if not found anywhere
            if not cred_redirect_uri:
                cred_redirect_uri = "http://127.0.0.1:8888/callback"
            
            if not all([cred_client_id, cred_client_secret, cred_redirect_uri]):
                logging.error("One or more Spotify credentials missing in config_secrets.json. Check 'ledmatrix-music' or 'music' section.")
                return None, None, None
            return cred_client_id, cred_client_secret, cred_redirect_uri
    except json.JSONDecodeError:
        logging.error(f"Error decoding JSON from {SECRETS_PATH}")
        return None, None, None
    except Exception as e:
        logging.error(f"Error loading Spotify credentials: {e}")
        return None, None, None

if __name__ == "__main__":
    logging.info("Starting Spotify Authentication Process...")

    client_id, client_secret, redirect_uri = load_spotify_credentials()

    if not all([client_id, client_secret, redirect_uri]):
        logging.error("Could not load Spotify credentials. Please check config/config_secrets.json. Exiting.")
        sys.exit(1)

    # Ensure the config directory exists for the cache file
    if not os.path.exists(CONFIG_DIR):
        try:
            logging.info(f"Config directory {CONFIG_DIR} not found. Attempting to create it.")
            os.makedirs(CONFIG_DIR)
            logging.info(f"Successfully created config directory: {CONFIG_DIR}")
        except OSError as e:
            logging.error(f"Fatal: Could not create config directory {CONFIG_DIR}: {e}. Please create it manually. Exiting.")
            sys.exit(1)

    sp_oauth = SpotifyOAuth(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        scope=SCOPE,
        cache_path=SPOTIFY_AUTH_CACHE_PATH, # Use explicit cache path
        open_browser=False
    )

    # Step 1: Get the authorization URL
    auth_url = sp_oauth.get_authorize_url()
    print("-" * 50)
    print("SPOTIFY AUTHORIZATION NEEDED:")
    print("1. Please visit this URL in a browser (on any device):")
    print(f"   {auth_url}")
    print("2. Authorize the application.")
    print("3. You will be redirected to a URL (likely showing an error). Copy that FULL redirected URL.")
    print("-" * 50)

    # Step 2: Get the redirected URL from the user
    redirected_url = input("4. Paste the full redirected URL here and press Enter: ").strip()

    if not redirected_url:
        logging.error("No redirected URL provided. Exiting.")
        sys.exit(1)

    # Step 3: Parse the code from the redirected URL
    try:
        # Spotipy's parse_auth_response_url is not directly part of the public API of SpotifyOAuth
        # for this specific flow where we manually handle the redirect. 
        # We need to extract the 'code' query parameter.
        # A more robust way would be to use urllib.parse, but for simplicity:
        if "?code=" in redirected_url:
            auth_code = redirected_url.split("?code=")[1].split("&")[0]
        elif "&code=" in redirected_url: # Should not happen if code is first param
            auth_code = redirected_url.split("&code=")[1].split("&")[0]
        else:
            logging.error("Could not find 'code=' in the redirected URL. Please ensure you copied the full URL.")
            logging.error(f"Received URL: {redirected_url}")
            sys.exit(1)
            
    except Exception as e:
        logging.error(f"Error parsing authorization code from redirected URL: {e}")
        logging.error(f"Received URL: {redirected_url}")
        sys.exit(1)

    # Step 4: Get the access token using the code and cache it
    try:
        # check_cache=False forces it to use the provided code rather than a potentially stale cached one for this specific step.
        # The token will still be written to the cache_path.
        token_info = sp_oauth.get_access_token(auth_code, check_cache=False)
        if token_info:
            logging.info(f"Spotify authentication successful. Token info cached at {SPOTIFY_AUTH_CACHE_PATH}")
        else:
            logging.error("Failed to obtain Spotify token info with the provided code.")
            logging.error("Please ensure the code was correct and not expired.")
            sys.exit(1)
            
    except Exception as e:
        logging.error(f"Error obtaining Spotify access token: {e}")
        logging.error("This can happen if the authorization code is incorrect, expired, or already used.")
        sys.exit(1)

    logging.info("Spotify Authentication Process Finished.")
