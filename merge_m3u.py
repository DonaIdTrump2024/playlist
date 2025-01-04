import asyncio
import aiohttp
import socket
import argparse
import streamlink
from concurrent.futures import ThreadPoolExecutor

BLOCKLIST = ["ngenix.net", "zabava", "ott.tricolor.tv","cdn2.ntv.ru","cdn.ntv.ru"]
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

def check_streamlink(url):
    try:
        streams = streamlink.streams(url)
        if streams:
            print(f"Streamlink detected a valid stream for {url}")
            return True
    except Exception as e:
        print(f"Streamlink error for {url}: {e}")
    return False

async def check_streamlink_with_timeout(url, timeout=3):
    try:
        result = await asyncio.wait_for(asyncio.to_thread(streamlink.streams, url), timeout)
        if result:
            print(f"Streamlink detected a valid stream for {url}")
            return True
    except asyncio.TimeoutError:
        print(f"Streamlink check for {url} timed out")
    except Exception as e:
        print(f"Streamlink error for {url}: {e}")
    return False

async def is_stream_working(url, user_agent=None, referer=None, attempts=3):
    headers = {}
    if user_agent:
        headers['User-Agent'] = user_agent
    if referer:
        headers['Referer'] = referer

    if is_blocked(url):
        print(f"URL {url} is blocked")
        return False

    async with semaphore:
        if url.startswith("rtmp://"):
            return await check_rtmp_stream(url, attempts)

        async with aiohttp.ClientSession() as session:
            for attempt in range(1, attempts + 1):
                try:
                    async with session.get(url, headers=headers, timeout=5) as response:
                        content_type = response.headers.get("Content-Type", "").lower()
                        
                        if response.status in [200, 301, 302, 307]:
                            if url.endswith(".m3u8") or "application/vnd.apple.mpegurl" in content_type:
                                content = await response.text()
                                if content.startswith("#EXTM3U"):
                                    print(f"Attempt {attempt}/{attempts} for {url} - Valid m3u8 playlist detected.")
                                    return True
                            
                            if "video" in content_type or content_type in ["application/octet-stream", "application/x-mpegurl"]:
                                print(f"Attempt {attempt}/{attempts} for {url} - Checking MPEG stream.")
                                
                                try:
                                    content_preview = await response.content.read(1024)
                                    if b"#EXTM3U" in content_preview or b"#EXTINF" in content_preview:
                                        print(f"Attempt {attempt}/{attempts} for {url} - Detected playlist markers in preview.")
                                        return True
                                    elif len(content_preview) > 0:
                                        print(f"Attempt {attempt}/{attempts} for {url} - Valid MPEG stream detected based on content length.")
                                        return True
                                    else:
                                        print(f"Attempt {attempt}/{attempts} for {url} - Empty response content.")
                                except Exception as e:
                                    print(f"Attempt {attempt}/{attempts} for {url} - Error reading stream content: {e}")

                            if content_type == "":
                                print(f"Attempt {attempt}/{attempts} for {url} - Empty Content-Type, assuming stream might be working.")
                                return True

                except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                    print(f"Attempt {attempt}/{attempts} for {url} - Error: {e}")

    print(f"Stream {url} failed after {attempts} attempts, falling back to Streamlink.")
    
    return await check_streamlink_with_timeout(url)

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
    referrer = None

    for line in lines:
        if line.startswith("#EXTINF"):
            excluded_groups = [
                'group-title="Взрослые [ВХОД СТРОГО 18+]"',
                'group-title="ИНФО"',
            ]
            if any(excluded_group in line for excluded_group in excluded_groups):
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

        elif line.startswith("#EXTVLCOPT:http-referrer="):
            referrer = line.split("=")[1].strip()

        elif line and current_channel:
            current_channel["stream"] = line
            current_channel["user_agent"] = user_agent
            current_channel["referrer"] = referrer
            channels.append(current_channel)
            current_channel = None
            user_agent = None
            referrer = None

    return channels

async def check_channels(channels):
    tasks = []
    for channel in channels:
        task = is_stream_working(
            channel["stream"],
            user_agent=channel.get("user_agent"),
            referer=channel.get("referrer")
        )
        tasks.append(task)

    results = await asyncio.gather(*tasks)

    for channel, is_working in zip(channels, results):
        if is_working:
            print(f"Channel {channel['tvg-id']} - Stream is working.")
        else:
            print(f"Channel {channel['tvg-id']} - Stream is not working or blocked.")

    return results

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

async def merge_m3u_channels_without_check(channels1):
    valid_channels = []
    existing_urls = set()

    for channel in channels1:
        stream_url = channel.get("stream")
        if stream_url not in existing_urls:
            valid_channels.append(channel)
            existing_urls.add(stream_url)
        else:
            print(f"Duplicate stream found for {stream_url}, skipping.")

    return valid_channels

def write_m3u(filename, channels, url_tvg, format_type):
    """
    Write channels to an M3U file.

    :param filename: Output filename
    :param channels: List of channel dictionaries
    :param url_tvg: TV guide URL line
    :param format_type: 'TiviMate' or 'Televizo' for specific formatting
    """
    with open(filename, "w", encoding="utf-8") as f:
        f.write(f"{url_tvg}\n")

        sorted_channels = sorted(channels, key=lambda channel: extract_channel_name(channel['info']))

        for channel in sorted_channels:
            f.write(f"{channel['info']}\n")

            stream_url = channel['stream']
            if format_type == 'TiviMate':
                if channel.get("user_agent"):
                    stream_url += f'|User-Agent="{channel["user_agent"]}"'

                if channel.get("referrer"):
                    stream_url += f'|Referer="{channel["referrer"]}"'
            elif format_type == 'Televizo':
                if channel.get("referrer"):
                    f.write(f"#EXTVLCOPT:http-referrer={channel['referrer']}\n")

                if channel.get("user_agent"):
                    f.write(f"#EXTVLCOPT:http-user-agent={channel['user_agent']}\n")

            f.write(f"{stream_url}\n")

async def main():
    parser = argparse.ArgumentParser(description="Merge and validate IPTV m3u playlists.")
    parser.add_argument("url1", help="URL to the first m3u file")
    parser.add_argument("url2", help="URL to the second m3u file")
    parser.add_argument("url3", help="URL to the third m3u file with Torrent TV channels")
    args = parser.parse_args()

    m3u_data1 = await download_m3u(args.url1)
    m3u_data2 = await download_m3u(args.url2)
    m3u_data3 = await download_m3u(args.url3)

    url_tvg = extract_url_tvg(m3u_data1)

    channels1 = parse_m3u(m3u_data1)
    channels2 = parse_m3u(m3u_data2)
    channels3 = parse_m3u(m3u_data3)

    torrent_tv_channels_3 = [
        channel for channel in channels3
        if channel.get("category") == "↕️ Торрент ТВ ↕️"
    ]

    merged_channels_12 = await merge_m3u_channels_async(channels1, channels2)

    merged_torrent_tv_channels = await merge_m3u_channels_without_check(
        torrent_tv_channels_3
    )

    merged_channels = merged_channels_12 + merged_torrent_tv_channels

    write_m3u("tivimate.m3u", merged_channels, url_tvg, "TiviMate")
    print("TiviMate playlist written to tivimate.m3u")

    write_m3u("televizo.m3u", merged_channels, url_tvg, "Televizo")
    print("Televizo playlist written to televizo.m3u")

if __name__ == "__main__":
    asyncio.run(main())
