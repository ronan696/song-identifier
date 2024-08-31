import asyncio
import io
import os

import musicbrainzngs
import pyaudio
import requests
import sounddevice as sd
from discogs_client import Client
from dotenv import load_dotenv
from pydantic import BaseModel
from scipy.io.wavfile import write
from shazamio import Shazam

extDataDir = os.getcwd()
load_dotenv(dotenv_path=os.path.join(extDataDir, "song_identifier.env"))

APPLICATION_NAME = "SongIdentifier"
APPLICATION_VERSION = "1.0"

SAMPLE_RATE = int(os.getenv("SAMPLE_RATE", 44100))
RECORDING_DURATION_SEC = int(os.getenv("RECORDING_DURATION_SEC", 10))
NUMBER_OF_CHANNELS = int(os.getenv("NUMBER_OF_CHANNELS", 1))
DURATION_FORMAT = "%M:%S"

CHUNK = 1024
FORMAT = pyaudio.paInt16

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
    duration: int = 0
    year: int = 0
    label: str = ""
    genre: str = ""
    isrc: str = ""


def select_sound_device() -> int:
    devices = sd.query_devices()

    for device in devices:
        print(f"{device['index'] + 1}: {device['name']}")

    device_number = int(input("Select the required device: "))
    return device_number - 1


def listen_to_song_from_device(device: int) -> bytes:
    print("Start Recording...")
    song = sd.rec(
        int(RECORDING_DURATION_SEC * SAMPLE_RATE),
        samplerate=SAMPLE_RATE,
        channels=NUMBER_OF_CHANNELS,
        device=device,
    )
    byte_io = io.BytesIO(bytes())
    sd.wait()  # Wait until recording is finished
    write(byte_io, SAMPLE_RATE, song)  # Save binary data
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
            song_metadata.year = master.year
            song_metadata.genre = ", ".join(master.genres)
            # song_metadata.artist = release.artists_sort
            if data.get("label"):
                song_metadata.label = data.get("label")[0]
            if tracklist:
                # song_metadata.duration = tracklist[0].duration
                song_metadata.duration = sum(
                    [
                        a * b
                        for a, b in zip(
                            [60, 1], map(int, tracklist[0].duration.split(":"))
                        )
                    ]
                )
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
    try:
        device = select_sound_device()
        while True:
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

            choice = input("Record Next Song [y/n]: ")
            if choice.lower() != "y":
                break

    except Exception as e:
        print(f"An error occurred: {e}")
