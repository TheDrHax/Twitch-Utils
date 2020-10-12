# Python utils for Twitch [![PyPI version](https://badge.fury.io/py/tdh-twitch-utils.svg)](https://badge.fury.io/py/tdh-twitch-utils)

```
pip3 install tdh-twitch-utils
```

This module consists of three different scripts:

## concat

This script uses MPEG-TS timestamps to concatenate multiple
video segments into a single file without re-encoding. It is
most useful for assembling partial stream recordings in case
of interruption or error during stream download. Overlapping
parts will be removed precisely with ffmpeg's concat demuxer.

### Example

```
# download two overlapping segments (60 seconds each)
VOD="YOUR VOD ID"
streamlink -o 1.ts --hls-duration 60 "twitch.tv/videos/$VOD" best
streamlink -o 2.ts --hls-start-offset 30 --hls-duration 60 "twitch.tv/videos/$VOD" best

# concatenate two segments into one video
twitch_utils concat 1.ts 2.ts -o result.mp4

# create one segment
twitch_utils concat 1.ts 2.ts -o result.ts

# pipe concatenated MPEG-TS stream to other applications
twitch_utils concat 1.ts 2.ts -o - | ffmpeg -i - -c copy result.mp4
```

## record

This script can be used to record live streams without waiting
for them to end. It starts to record live stream immediately,
then downloads VOD and concatenates them into full stream recording.

Obviously, this script requires channel to have public VODs.

Algorithm:
1. Check if channel is live and VOD for current stream already exists;
2. Get live VOD ID from Twitch API;
3. Start downloading live stream into file `VOD.end.ts`;
4. Wait 10 minutes and start downloading VOD into file `VOD.start.ts`;
5. Wait for both downloads to finish;
6. Concatenate two parts via `concat` script (see above).

Note: Since Nov 2019 you have to provide your Twitch OAuth token in the command.
Otherwise the script will not be able to detect the ID of the live VOD and
download the beginning of the stream. At the moment, you will need to extract
OAuth token from Twitch's cookie "auth-token". Other options such as providing
your own Client-ID and token are not implemented yet.

This script is just a proof of concept and probably should not be relied upon.

### Example

```
# Record live stream of channel 'blackufa' using 2 threads
twitch_utils record --oauth=YOUR_TOKEN blackufa -j 2
```

## offset

This script performs cross-correlation of two audio files to find
offset between them. First argument is cropped and used as template.
Second argument can have any duration -- it will be divided into
separate chunks to reduce memory usage (otherwise it wouldn't be
possible to use exceptionally big files). Both arguments can be
videos or audio files -- audio track will be extracted and converted.
You can even use HTTP links if `ffprobe` is able to correctly determine
second argument's duration.

### Example

```
# Cut small segment from big video file (offset: 123 seconds)
ffmpeg -ss 123 -i YOUR_FILE.mp4 -t 60 -c copy template.mp4

# Find offset of template.mp4 within YOUR_FILE.mp4
twitch_utils offset template.mp4 YOUR_FILE.mp4
# ... returns 122.99997732426303

# Same command, but result will be rounded to nearest integer
twitch_utils offset template.mp4 YOUR_FILE.mp4 --round
# ... returns 123
```

## mute

This script attempts to separate streamer's voice from background music by using [Spleeter](https://github.com/deezer/spleeter). Only specified time ranges are affected. Output contains the same video, but without music in these parts.

The main purpose of this script is to remove automated Content-ID claims from the video on YouTube without muting the whole section.

The result is similar to "Mute song only (beta)" in YouTube Studio, but this script is much faster and can handle multiple time ranges at once.

### Example

```
# Remove music from 5:00 to 8:00 and from 1:00:00 to 1:05:00
twitch_utils input.mp4 5:00-8:00 1:00:00-1:05:00 -o output.mp4
```