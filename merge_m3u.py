import asyncio
import aiohttp
import socket
import argparse
from concurrent.futures import ThreadPoolExecutor

BLOCKLIST = ["ngenix.net", "zabava"]
MAX_CONCURRENT_CHECKS = 5
semaphore = asyncio.Semaphore(MAX_CONCURRENT_CHECKS)

def is_blocked(url):
    return any(blocked_domain in url for blocked_domain in BLOCKLIST)

async def download_m3u(url):
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            response.raise_for_status()
            return await response.text()

def extract_url_tvg(m3u_data):
    for line in m3u_data.splitlines():
        if line.startswith("#EXTM3U") and "url-tvg=" in line:
            return line
    return "#EXTM3U"

def normalize_category(category):
    if category in ["Кинозал", "Русский кинозал", "Кинозалы", "Кино и сериалы"]:
        return "Кино и Сериалы"
    if category in ["Общественные", "Новостные", "Новости", "Информационные"]:
        return "Эфирные"
    if category in ["Наш спорт"]:
        return "Спортивные"
    if category in ["Досуг"]:
        return "Хобби и увлечения"
    if category in ["Региoнальные"]:
        return "Региональные"
    if category in ["Христианские"]:
        return "Религиозные"
    return category

async def is_stream_working(url, user_agent=None, attempts=3):
    headers = {'User-Agent': user_agent} if user_agent else {}

    if is_blocked(url):
        print(f"URL {url} is blocked")
        return False

    async with semaphore:
        if url.startswith("rtmp://"):
            return await check_rtmp_stream(url, attempts)

        async with aiohttp.ClientSession() as session:
            for attempt in range(1, attempts + 1):
                try:
                    async with session.head(url, headers=headers, timeout=5) as response:
                        if response.status in [200, 301, 302, 307]:
                            print(f"Attempt {attempt}/{attempts} for {url} - Status: {response.status} (Working)")
                            return True
                        else:
                            print(f"Attempt {attempt}/{attempts} for {url} - Status: {response.status}")
                except (aiohttp.ClientError, asyncio.TimeoutError):
                    print(f"Attempt {attempt}/{attempts} for {url} - Failed")
    return False

async def check_rtmp_stream(url, attempts):
    cleaned_url = url.replace("rtmp://", "rtmp://")
    host = cleaned_url.split("/")[2].split(":")[0]
    port = int(cleaned_url.split("/")[2].split(":")[1]) if ":" in cleaned_url.split("/")[2] else 1935

    loop = asyncio.get_event_loop()
    for attempt in range(1, attempts + 1):
        try:
            await loop.run_in_executor(None, socket.create_connection, (host, port), 5)
            print(f"Attempt {attempt}/{attempts} for {url} - Working (RTMP/TCP)")
            return True
        except (socket.timeout, ConnectionRefusedError, OSError):
            print(f"Attempt {attempt}/{attempts} for {url} - Failed (RTMP/TCP)")
    return False

def parse_m3u(m3u_data):
    channels = []
    lines = m3u_data.splitlines()
    current_channel = None
    user_agent = None

    for line in lines:
        if line.startswith("#EXTINF"):
            if 'group-title="Взрослые"' in line:
                current_channel = None
                continue

            if 'group-title="' in line:
                group_title = line.split('group-title="')[1].split('"')[0]
                normalized_category = normalize_category(group_title)
                line = line.replace(f'group-title="{group_title}"', f'group-title="{normalized_category}"')
            else:
                normalized_category = None

            tvg_id = line.split('tvg-id="')[1].split('"')[0] if 'tvg-id' in line else None
            current_channel = {"info": line, "stream": None, "tvg-id": tvg_id, "category": normalized_category}
        
        elif line.startswith("#EXTVLCOPT:http-user-agent="):
            user_agent = line.split("=")[1].strip()

        elif line and current_channel:
            current_channel["stream"] = line
            current_channel["user_agent"] = user_agent
            channels.append(current_channel)
            current_channel = None
            user_agent = None

    return channels

async def check_channels(channels):
    tasks = []
    for channel in channels:
        task = is_stream_working(channel["stream"], user_agent=channel.get("user_agent"))
        tasks.append(task)
    return await asyncio.gather(*tasks)

async def merge_m3u_channels_async(channels1, channels2):
    all_channels = channels1 + channels2
    valid_channels = []
    existing_urls = set()

    results = await check_channels(all_channels)
    for channel, is_working in zip(all_channels, results):
        stream_url = channel.get("stream")
        
        if is_working and stream_url not in existing_urls:
            valid_channels.append(channel)
            existing_urls.add(stream_url)
        elif not is_working:
            print(f"Channel {channel['tvg-id']} - No working stream found")
        else:
            print(f"Channel {channel['tvg-id']} - Duplicate stream found, skipping")

    return valid_channels

def extract_channel_name(extinf_line):
    return extinf_line.split(",", 1)[1].strip()

def write_m3u(filename, channels, url_tvg):
    with open(filename, "w", encoding="utf-8") as f:
        f.write(f"{url_tvg}\n")
        sorted_channels = sorted(channels, key=lambda channel: extract_channel_name(channel['info']))
        for channel in sorted_channels:
            f.write(f"{channel['info']}\n")
            if channel.get("user_agent"):
                f.write(f"#EXTVLCOPT:http-user-agent={channel['user_agent']}\n")
            f.write(f"{channel['stream']}\n")

async def main():
    parser = argparse.ArgumentParser(description="Merge and validate IPTV m3u playlists.")
    parser.add_argument("url1", help="URL to the first m3u file")
    parser.add_argument("url2", help="URL to the second m3u file")
    parser.add_argument("url3", help="URL to the third m3u file with Torrent TV channels")
    parser.add_argument(
        "-o", "--output", default="merged_channels.m3u",
        help="Output filename for the merged m3u file (default: merged_channels.m3u)"
    )
    args = parser.parse_args()

    url1 = args.url1
    url2 = args.url2
    url3 = args.url3
    output_filename = args.output

    m3u_data1 = await download_m3u(url1)
    m3u_data2 = await download_m3u(url2)
    m3u_data3 = await download_m3u(url3)

    url_tvg = extract_url_tvg(m3u_data1)

    channels1 = parse_m3u(m3u_data1)
    channels2 = parse_m3u(m3u_data2)
    channels3 = parse_m3u(m3u_data3)

    torrent_tv_channels = [
        channel for channel in channels3
        if channel.get("category") == "↕️ Торрент ТВ ↕️"
    ]

    merged_channels = await merge_m3u_channels_async(channels1, channels2)

    merged_channels.extend(torrent_tv_channels)

    write_m3u(output_filename, merged_channels, url_tvg)
    print(f"Merged playlist written to {output_filename}")


if __name__ == "__main__":
    asyncio.run(main())
