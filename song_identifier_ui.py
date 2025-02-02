# macOS packaging support
from multiprocessing import freeze_support  # noqa

freeze_support()  # noqa

# all your other imports and code

from nicegui import native, ui
import asyncio
import io
import configparser
import logging
import logging.handlers
import pathlib


import musicbrainzngs
import requests
import sounddevice as sd
from discogs_client import Client
from pydantic import BaseModel
from scipy.io.wavfile import write
from shazamio import Shazam

config = configparser.ConfigParser()
config.read(str(pathlib.Path('~/song_identifier.ini').expanduser()))

DISPLAY_OUTPUT_DEVICES = False
DURATION = 20  # secs

APPLICATION_NAME = "SongIdentifier"
APPLICATION_VERSION = "2.0"

DISCOGS_CLIENT = Client(
    f"{APPLICATION_NAME}/{APPLICATION_VERSION}",
    user_token=config.get(
        "DEFAULT",
        "DiscogsAccessToken",
        fallback="",
    ),
)

musicbrainzngs.set_useragent(APPLICATION_NAME, APPLICATION_VERSION)

LOG_FILE_NAME = str(pathlib.Path('~/song_identifier.log').expanduser())
LOGGING_LEVEL = logging.DEBUG

formatter = logging.Formatter('%(asctime)s %(name)s %(levelname)s %(message)s')
handler = logging.handlers.TimedRotatingFileHandler(LOG_FILE_NAME, when="midnight", backupCount=7)
handler.setFormatter(formatter)
logger = logging.getLogger("song_identifier")
logger.addHandler(handler)
logger.setLevel(LOGGING_LEVEL)


class Device(BaseModel):
    index: int
    name: str
    max_input_channels: int
    max_output_channels: int
    default_samplerate: int


class IdentifiedSong(BaseModel):
    title: str = ""
    artist: str = ""
    isrc: str = ""


class SongMetadata(BaseModel):
    title: str = ""
    artist: str = ""
    album: str = ""
    duration: str = ""
    year: str = ""
    label: str = ""
    genre: str = ""
    isrc: str = ""


SELECTED_DEVICE = None
RECORDING_TASK = None
PROGRESS_NOTIF = None


# Bizness Logic
def get_sound_devices() -> list[dict]:
    devices = list(sd.query_devices())
    valid_devices = []
    for device in devices:
        valid_devices.append(
            {
                "index": device["index"],
                "name": device["name"],
                "max_input_channels": device["max_input_channels"],
                "max_output_channels": device["max_output_channels"],
                "default_samplerate": device["default_samplerate"],
            }
        )
    logger.debug(f"{valid_devices=}")
    return valid_devices


ui.colors(
    primary="#000000",
    secondary="#343434",
    accent="#FF4081",
    positive="#4CAF50",
    negative="#FF1744",
    info="#2196F3",
    warning="#FFC107",
)


def save_config():
    global DISCOGS_CLIENT
    config["DEFAULT"]["SpinitronAccessToken"] = spinitron_at_input.value.strip()
    config["DEFAULT"]["SpinitronApiUrl"] = spinitron_url_input.value.strip()
    config["DEFAULT"]["DiscogsAccessToken"] = discogs_at_input.value.strip()
    config["DEFAULT"]["RadioLogikPath"] = radiologik_path_input.value.strip()
    DISCOGS_CLIENT = Client(
        f"{APPLICATION_NAME}/{APPLICATION_VERSION}",
        user_token=discogs_at_input.value.strip(),
    )
    with open(str(pathlib.Path('~/song_identifier.ini').expanduser()), "w") as config_file:
        config.write(config_file)
    dialog.close()


def missing_config():
    try:
        if (
            config["DEFAULT"]["SpinitronAccessToken"]
            and config["DEFAULT"]["SpinitronApiUrl"]
            and config["DEFAULT"]["DiscogsAccessToken"]
            and config["DEFAULT"]["RadioLogikPath"]
        ):
            return False
        return True
    except Exception:
        return True


def open_settings():
    config.read(str(pathlib.Path('~/song_identifier.ini').expanduser()))
    spinitron_at_input.set_value(
        config.get("DEFAULT", "SpinitronAccessToken", fallback="")
    )
    spinitron_url_input.set_value(
        config.get(
            "DEFAULT",
            "SpinitronApiUrl",
            fallback="https://spinitron.com/api/spin/create-v1",
        )
    )
    discogs_at_input.set_value(
        config.get(
            "DEFAULT",
            "DiscogsAccessToken",
            fallback="",
        )
    )
    radiologik_path_input.set_value(
        config.get(
            "DEFAULT",
            "RadioLogikPath",
            fallback="",
        )
    )
    spinitron_at_input.update()
    spinitron_url_input.update()
    discogs_at_input.update()
    radiologik_path_input.update()
    dialog.open()


with ui.dialog() as dialog, ui.card().style("width: 750px; max-width: none"):
    with ui.row().classes("w-full align-middle"):
        spinitron_at_input = ui.input(
            label="Spinitron Access Token",
        )
        spinitron_at_input.classes("w-full")
        spinitron_url_input = ui.input(
            label="Spinitron API URL",
        )
        spinitron_url_input.classes("w-full")
        discogs_at_input = ui.input(
            label="Discogs Access Token",
        )
        discogs_at_input.classes("w-full")
        radiologik_path_input = ui.input(
            label="Radiologik Path",
        )
        radiologik_path_input.classes("w-full")
    with ui.row().classes("w-full align-middle flex-row-reverse"):
        ui.button("Save", on_click=save_config)
        ui.button("Close", on_click=dialog.close, color="secondary")

with ui.row().classes("w-full align-middle"):
    ui.label("Song Identifier").style("font-size: 1.5rem;")
    ui.space()
    ui.button(icon="settings", on_click=open_settings).tooltip("Settings")

grid = ui.aggrid(
    {
        "defaultColDef": {"flex": 1},
        "columnDefs": [
            {"headerName": "Sr. No.", "field": "index", "hide": True},
            {"headerName": "Device Name", "field": "name", "checkboxSelection": True},
            {"headerName": "Input Channels", "field": "max_input_channels"},
            {"headerName": "Output Channels", "field": "max_output_channels"},
            {"headerName": "Sample Rate (Hz)", "field": "default_samplerate"},
        ],
        "rowSelection": "single",
    }
).classes("max-h-40")


def update_device(event):
    grid.options["rowData"] = [
        row for row in sound_devices if event.value or row["max_input_channels"] > 0
    ]
    grid.update()


checkbox = ui.checkbox(
    "Show output devices", value=DISPLAY_OUTPUT_DEVICES, on_change=update_device
)
checkbox.disable()


def cool_callback(v):
    global PROGRESS_NOTIF
    if v.value > 1:
        time_bar_timer.deactivate()
        cancel_button.disable()
        PROGRESS_NOTIF = ui.notification(
            timeout=None, message="Fetching song information..."
        )
        PROGRESS_NOTIF.spinner = True


time_bar_timer = ui.timer(
    1.0, lambda: time_bar.set_value(time_bar.value + (1.0 / DURATION)), active=False
)


def listen_to_song_from_device() -> bytes:
    logger.info("Start Recording...")
    song = sd.rec(
        int(DURATION * SELECTED_DEVICE.default_samplerate),
        samplerate=SELECTED_DEVICE.default_samplerate,
        channels=SELECTED_DEVICE.max_input_channels,
        device=SELECTED_DEVICE.index,
    )
    byte_io = io.BytesIO(bytes())
    sd.wait()  # Wait until recording is finished
    write(byte_io, int(SELECTED_DEVICE.default_samplerate), song)  # Save binary data
    result_bytes = byte_io.read()
    logger.info("Song recorded!")
    return result_bytes


async def identify_song(song: bytes) -> IdentifiedSong:
    shazam = Shazam()
    out = await shazam.recognize(song)
    identified_song = IdentifiedSong()
    track = out.get("track", {})
    if not track:
        raise Exception("Shazam was unable to identify the song!")
    identified_song.title = track.get("title", "")
    identified_song.artist = track.get("subtitle", "")
    identified_song.isrc = track.get("isrc", "")
    return identified_song


def get_song_metadata(title, artist="", isrc="") -> tuple[SongMetadata, str]:
    results = DISCOGS_CLIENT.search(title, artist=artist, type="master").page(1)
    song_metadata = SongMetadata()
    song_metadata.title = title
    song_metadata.artist = artist
    err = ""
    if isrc:
        song_metadata.isrc = isrc
        try:
            result = musicbrainzngs.get_recordings_by_isrc(isrc, includes=["releases"])
            recording = result.get("isrc", {}).get("recording-list", [None])[0]
            if recording.get("release-list"):
                release = recording.get("release-list")[0]
                song_metadata.album = release.get("title", "")
        except Exception as e:
            logger.warning(f"Failed to fetch album of {title} by {artist}. Reason: {e}")
            err = f"Failed to fetch album of {title} by {artist}"

    if results:
        try:
            master = results[0]
            release = master.main_release
            tracklist = master.tracklist
            data = master.data
            # song_metadata.title = master.title
            song_metadata.year = str(master.year)
            song_metadata.genre = ", ".join(master.genres)
            # song_metadata.artist = release.artists_sort
            if data.get("label"):
                song_metadata.label = data.get("label")[0]
            if tracklist:
                # song_metadata.duration = tracklist[0].duration
                song_metadata.duration = str(
                    sum(
                        [
                            a * b
                            for a, b in zip(
                                [60, 1], map(int, tracklist[0].duration.split(":"))
                            )
                        ]
                    )
                )
            return song_metadata, err
        except Exception as e:
            err = f"Could not fetch all metadata. Please inspect missing metadata {song_metadata} . Reason {e}"
            logger.warning(err)
            return song_metadata, f"Could not fetch all metadata. Please inspect missing metadata for {title} by {artist}!"
    else:
        err = f"No metadata fetched for {title} by {artist}!"
        return song_metadata, err


def create_spin_for_song(song_metadata: SongMetadata):
    payload = {
        "sd": song_metadata.duration,
        "aw": song_metadata.artist,
        "dn": song_metadata.album,
        "dr": song_metadata.year,
        "ln": song_metadata.label,
        "dl": song_metadata.genre,
        "sn": song_metadata.title,
        # "isrc": song_metadata.isrc
    }
    headers = {
        "Authorization": f"Bearer {config.get("DEFAULT", "SpinitronAccessToken", fallback="")}"
    }

    r = requests.get(
        config.get(
            "DEFAULT",
            "SpinitronApiUrl",
            fallback="https://spinitron.com/api/spin/create-v1",
        ),
        params=payload,
        headers=headers,
    )
    if r.status_code == 401:
        raise Exception("Authentication failed! Please verify Spinitron Access Token.")

    r.raise_for_status()
    
    logger.info(f"Spin created! Response from Spinitron: {r.json()}")


def log_song_for_radio_logik(song_metadata: SongMetadata):
    radio_logik_path = config.get(
        "DEFAULT",
        "RadioLogikPath",
    )
    try:
        with open(radio_logik_path, "w") as f:
            f.write(f"{song_metadata.artist} - {song_metadata.title}\n")
        logger.info(f"Song metadata logged for Radiologik at {radio_logik_path}")
    except Exception as e:
        logger.warning(f"Failed to log song for Radiologik. Reason: {e}")


async def start_record():
    global RECORDING_TASK
    if missing_config():
        ui.notification(
            timeout=2,
            message="Please configure Song Identifier settings!",
            close_button="✖",
            type="warning",
        )

    elif not SELECTED_DEVICE:
        ui.notification(
            timeout=2,
            message="Please select an audio input device!",
            close_button="✖",
            type="warning",
        )

    else:
        try:
            reset_labels()
            cancel_button.enable()
            start_stop_button.disable()
            time_bar_timer.activate()
            RECORDING_TASK = asyncio.create_task(
                asyncio.to_thread(listen_to_song_from_device)
            )
            song = await RECORDING_TASK
            identified_song: IdentifiedSong = await identify_song(song)
            logger.info(
                f"Shazam identified song: {identified_song.title} by {identified_song.artist}"
            )
            song_metadata, err = await asyncio.to_thread(
                get_song_metadata,
                identified_song.title,
                identified_song.artist,
                identified_song.isrc,
            )

            logger.info(f"{song_metadata=}")
            PROGRESS_NOTIF.message = "Creating Spinitron spin..."

            await asyncio.to_thread(create_spin_for_song, song_metadata)
            PROGRESS_NOTIF.message = "Writing metadata for Radiologik..."
            await asyncio.to_thread(log_song_for_radio_logik, song_metadata)

            start_stop_button.enable()
            time_bar_timer.deactivate()
            time_bar.set_value(0)
            title_label.set_content(f"**Title:** {song_metadata.title}")
            duration_label.set_content(
                f"**Duration:** {convert_human_readable(int(song_metadata.duration)) if song_metadata.duration else "N/A"}"
            )
            artist_label.set_content(f"**Artist:** {song_metadata.artist or "N/A"}")
            album_label.set_content(f"**Album:** {song_metadata.album or "N/A"}")
            year_label.set_content(f"**Year:** {song_metadata.year or "N/A"}")
            label_label.set_content(f"**Label:** {song_metadata.label or "N/A"}")
            genre_label.set_content(f"**Genre:** {song_metadata.genre or "N/A"}")
            isrc_label.set_content(f"**ISRC:** {song_metadata.isrc or "N/A"}")

            if PROGRESS_NOTIF:

                if err:
                    PROGRESS_NOTIF.type = "positive"
                    PROGRESS_NOTIF.spinner = False
                    PROGRESS_NOTIF.icon = "warning"
                    PROGRESS_NOTIF.message = f"Completed with a warning: {err}"
                    PROGRESS_NOTIF.close_button = "✖"
                    await asyncio.sleep(5)
                    PROGRESS_NOTIF.dismiss()

                else:
                    PROGRESS_NOTIF.type = "positive"
                    PROGRESS_NOTIF.spinner = False
                    PROGRESS_NOTIF.message = "Completed successfully!"
                    PROGRESS_NOTIF.close_button = "✖"
                    await asyncio.sleep(5)
                    PROGRESS_NOTIF.dismiss()

        except Exception as e:
            if PROGRESS_NOTIF:
                PROGRESS_NOTIF.type = "negative"
                PROGRESS_NOTIF.spinner = False
                PROGRESS_NOTIF.icon = "error"
                PROGRESS_NOTIF.message = f"An error occurred: {str(e)}"
                PROGRESS_NOTIF.close_button = "✖"
                await asyncio.sleep(5)
                PROGRESS_NOTIF.dismiss()

        finally:
            start_stop_button.enable()
            cancel_button.disable()
            time_bar_timer.deactivate()
            time_bar.set_value(0)


def cancel_record():
    start_stop_button.enable()
    cancel_button.disable()
    time_bar_timer.deactivate()
    time_bar.set_value(0)
    if RECORDING_TASK:
        RECORDING_TASK.cancel()


def set_selected_device(d):
    global SELECTED_DEVICE
    SELECTED_DEVICE = Device(
        index=d.args["data"]["index"],
        name=d.args["data"]["name"],
        max_input_channels=d.args["data"]["max_input_channels"],
        max_output_channels=d.args["data"]["max_output_channels"],
        default_samplerate=d.args["data"]["default_samplerate"],
    )


def set_duration(d):
    global DURATION
    if d.value:
        DURATION = int(d.value)


with ui.row(align_items="center").classes("w-full no-wrap"):
    start_stop_button = ui.button(icon="circle", on_click=start_record)
    start_stop_button.tooltip("Record")
    a = ui.number(
        label="Duration (sec)",
        value=DURATION,
        precision=0,
        min=1,
        on_change=set_duration,
    )
    time_bar = ui.linear_progress(show_value=False)
    time_bar.on_value_change(cool_callback)
    cancel_button = ui.button(icon="cancel", on_click=cancel_record)
    cancel_button.tooltip("Cancel")

with ui.grid(columns=16).classes("w-full gap-0"):
    title_label = ui.markdown("**Title:** N/A").classes("col-span-8 border p-1")
    artist_label = ui.markdown("**Artist:** N/A").classes("col-span-8 border p-1")
    album_label = ui.markdown("**Album:** N/A").classes("col-span-8 border p-1")
    duration_label = ui.markdown("**Duration:** N/A").classes("col-span-8 border p-1")
    year_label = ui.markdown("**Year:** N/A").classes("col-span-8 border p-1")
    label_label = ui.markdown("**Label:** N/A").classes("col-span-8 border p-1")
    genre_label = ui.markdown("**Genre:** N/A").classes("col-span-8 border p-1")
    isrc_label = ui.markdown("**ISRC:** N/A").classes("col-span-8 border p-1")


def reset_labels():
    title_label.set_content("**Title:** N/A")
    artist_label.set_content("**Artist:** N/A")
    album_label.set_content("**Album:** N/A")
    duration_label.set_content("**Duration:** N/A")
    year_label.set_content("**Year:** N/A")
    label_label.set_content("**Label:** N/A")
    genre_label.set_content("**Genre:** N/A")
    isrc_label.set_content("**ISRC:** N/A")


def convert_human_readable(seconds):
    min, sec = divmod(seconds, 60)
    hour, min = divmod(min, 60)
    return "%02d:%02d:%02d" % (hour, min, sec)


cancel_button.disable()
sound_devices = get_sound_devices()
grid.options["rowData"] = [
    row
    for row in sound_devices
    if DISPLAY_OUTPUT_DEVICES or row["max_input_channels"] > 0
]
grid.update()
grid.on("rowSelected", set_selected_device)
checkbox.enable()


if __name__ in {"__main__", "__mp_main__"}:
    ui.run(reload=False, native=True, window_size=(900,700), port=native.find_open_port(), title="Song Identifier")
    # ui.run()
