
import os
# Suppress native connection errors which can otherwise print credentialed URLs.
os.environ.setdefault('OPENCV_FFMPEG_LOGLEVEL','-8')
os.environ.setdefault('OPENCV_FFMPEG_CAPTURE_OPTIONS','rtsp_transport;tcp')
