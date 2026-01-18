import yt_dlp
import os

url = "https://twitter-ero-video-ranking.com/zh-CN/movie/_Mq5ig_T8KZSX_YG"
output_template = "test_download.%(ext)s"

ydl_opts = {
    'outtmpl': output_template,
    'format': 'bestvideo+bestaudio/best',
    'merge_output_format': 'mp4',
    'quiet': False, # Show output for debugging
    'no_warnings': False,
    'http_headers': {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Referer': 'https://twitter-ero-video-ranking.com/',
    }
}

try:
    if os.path.exists("test_download.mp4"):
        os.remove("test_download.mp4")
    
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])
    print("Download Success!")
except Exception as e:
    print(f"Download Failed: {e}")
