# song-identifier

## Setup

The below command should be run only once.

```bash
pip install -r requirements.txt
```

## Configuration

Edit the `song_identifier.env` file with the required values before running the script. 

## Usage

The below command can be run to execute the script.

```bash
python song_identifier.py
```

## Packaging

The below commands can be run to build an executable for the script.
```bash
pip install pyinstaller
pyinstaller -F song_identifier.py 
```
