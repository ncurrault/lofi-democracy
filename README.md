# Lofi Beats to Relax/Enjoy Democracy To

A simple project to put background music over 

## Setup

First, download the Department of Elections's JSON file describing its camera feeds from https://sfelections.org/tools/election_video_stream/api/cameras.php. Then use the included Python script to transform these feeds into the relevant M3U8 stream files in a JSON that the frontend can read:


```
python3 scripts/fetch_streams.py --cameras-json cameras.php.json
```



