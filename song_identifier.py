import asyncio
import io
import os

import musicbrainzngs
import requests
import sounddevice as sd
from discogs_client import Client
from dotenv import load_dotenv
from pydantic import BaseModel, parse_obj_as
from scipy.io.wavfile import write
from shazamio import Shazam

extDataDir = os.getcwd()
load_dotenv(dotenv_path=os.path.join(extDataDir, "song_identifier.env"))

APPLICATION_NAME = "SongIdentifier"
APPLICATION_VERSION = "1.0"

RECORDING_DURATION_SEC = int(os.getenv("RECORDING_DURATION_SEC", 20))
DURATION_FORMAT = "%M:%S"
DISPLAY_OUTPUT_DEVICES = parse_obj_as(bool, os.getenv("DISPLAY_OUTPUT_DEVICES", False))
DEBUG = parse_obj_as(bool, os.getenv("DEBUG", False))

RADIO_LOGIK_METADATA_FILE_PATH = os.getenv("RADIO_LOGIK_METADATA_FILE_PATH")

SPINITRON_ACCESS_TOKEN = os.getenv("SPINITRON_ACCESS_TOKEN")
SPINITRON_API_URL = os.getenv(
    "SPINITRON_API_URL", "https://spinitron.com/api/spin/create-v1"
)
DISCOGS_ACCESS_TOKEN = os.getenv("DISCOGS_ACCESS_TOKEN")
DISCOGS_CLIENT = Client(
    f"{APPLICATION_NAME}/{APPLICATION_VERSION}", user_token=DISCOGS_ACCESS_TOKEN
)
musicbrainzngs.set_useragent(APPLICATION_NAME, APPLICATION_VERSION)


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


def select_sound_device() -> dict:
    devices = list(sd.query_devices())
    valid_devices = []
    for device in devices:
        if DISPLAY_OUTPUT_DEVICES:
            valid_devices.append(device['index'] + 1)
            print(
                f"{device['index'] + 1}: {device['name']}, Input Channels: {device['max_input_channels']}, "
                f"Output Channels: {device['max_output_channels']}, Sample Rate: {device['default_samplerate']}"
            )
        else:
            if device['max_input_channels'] > 0:
                valid_devices.append(device['index'] + 1)
                print(
                        f"{device['index'] + 1}: {device['name']}, Input Channels: {device['max_input_channels']}, "
                        f"Output Channels: {device['max_output_channels']}, Sample Rate: {device['default_samplerate']}"
                    )

    device_number = int(input("Select the required device: "))
    if device_number not in valid_devices:
        raise Exception("Invalid device selected!")
    return devices[device_number - 1]


def listen_to_song_from_device(device: dict) -> bytes:
    print("Start Recording...")
    song = sd.rec(
        int(RECORDING_DURATION_SEC * device['default_samplerate']),
        samplerate=device['default_samplerate'],
        channels=device['max_input_channels'],
        device=device['index'],
    )
    byte_io = io.BytesIO(bytes())
    sd.wait()  # Wait until recording is finished
    write(byte_io, int(device['default_samplerate']), song)  # Save binary data
    result_bytes = byte_io.read()
    print("Song recorded!")
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


def get_song_metadata(title, artist="", isrc="") -> SongMetadata:
    results = DISCOGS_CLIENT.search(title, artist=artist, type="master").page(1)
    song_metadata = SongMetadata()
    song_metadata.title = title
    song_metadata.artist = artist
    if isrc:
        song_metadata.isrc = isrc
        try:
            result = musicbrainzngs.get_recordings_by_isrc(isrc, includes=["releases"])
            recording = result.get("isrc", {}).get("recording-list", [None])[0]
            if recording.get("release-list"):
                release = recording.get("release-list")[0]
                song_metadata.album = release.get("title", "")
        except Exception as e:
            print(f"WARNING: Failed to fetch album of {title} by {artist}. Reason: {e}")

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
                song_metadata.duration = str(sum(
                    [
                        a * b
                        for a, b in zip(
                            [60, 1], map(int, tracklist[0].duration.split(":"))
                        )
                    ]
                ))
            return song_metadata
        except Exception as e:
            print(
                f"WARNING: Could not fetch all metadata. Please inspect missing metadata {song_metadata} . Reason {e}"
            )
            return song_metadata
    else:
        print(f"WARNING: No metadata fetched for {title} by {artist} not found!")
        return song_metadata


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
    headers = {"Authorization": f"Bearer {SPINITRON_ACCESS_TOKEN}"}

    r = requests.get(SPINITRON_API_URL, params=payload, headers=headers)
    r.raise_for_status()
    print(f"Spin created! Response from Spinitron: {r.json()}")


def log_song_for_radio_logik(song_metadata: SongMetadata):
    try:
        with open(RADIO_LOGIK_METADATA_FILE_PATH, "w") as f:
            f.write(f"{song_metadata.artist} - {song_metadata.title}\n")
        print(
            f"Song metadata logged for Radio Logik at {RADIO_LOGIK_METADATA_FILE_PATH}"
        )
    except Exception as e:
        print(f"WARNING! Failed to log song for Radio Logik. Reason: {e}")


if __name__ == "__main__":
    while True:
        try:
            device = select_sound_device()
            song = listen_to_song_from_device(device)
            identified_song: IdentifiedSong = asyncio.run(identify_song(song))
            print(
                f"Shazam identified song: {identified_song.title} by {identified_song.artist}"
            )
            song_metadata: SongMetadata = get_song_metadata(
                identified_song.title, identified_song.artist, identified_song.isrc
            )
            print(f"{song_metadata=}")
            create_spin_for_song(song_metadata)
            log_song_for_radio_logik(song_metadata)

        except Exception as e:
            if DEBUG:
                import traceback
                print(traceback.format_exc())
            print(f"An error occurred: {e}")

        choice = input("Record Next Song [y/n]: ")
        if choice.lower() != "y":
            break