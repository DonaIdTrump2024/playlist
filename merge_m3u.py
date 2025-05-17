import re
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

async def download_m3u(url, attempts=3, timeout=10):
    async with aiohttp.ClientSession() as session:
        for attempt in range(1, attempts + 1):
            try:
                async with session.get(url, timeout=timeout) as response:
                    response.raise_for_status()
                    return await response.text()
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                print(f"Attempt {attempt}/{attempts} failed for {url}: {e}")
                if attempt == attempts:
                    print(f"Error for {url} after {attempts} attempts.")
                    return None
            await asyncio.sleep(2)

def extract_url_tvg():
    return '#EXTM3U url-tvg="https://github.com/DonaIdTrump2024/playlist/releases/download/m3u/myepg.xml.gz"'

def normalize_category(category):
    if category in ["Кинозал", "Русский кинозал", "Кинозалы", "Кино и сериалы","Премьеры,хиты", "Фильмы,сериалы", "Viju", "KINO+"]:
        return "Кино и Сериалы"
    if category in ["Общественные", "Новостные", "Новости", "Информационные","Федеральные плюс","НТВ"]:
        return "Эфирные"
    if category in ["NEWS","UNITED STATES","Live"]:
        return "США"
    if category in ["Наш спорт","SPORTS","NHL","PPV","NBA", "SPORT 🏆", "SPORT 🏆 VPN"]:
        return "Спортивные"
    if category in ["Досуг"]:
        return "Хобби и увлечения"
    if category in ["ПОЗНАВАТЕЛЬНЫЕ"]:
        return "Познавательные"
    if category in ["Региoнальные"]:
        return "Региональные"
    if category in ["МУЗИКА"]:
        return "Музыкальные"
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

async def is_stream_working(url, user_agent=None, referer=None, http_origin=None, attempts=3):
    headers = {}
    if user_agent:
        headers['User-Agent'] = user_agent
    if referer:
        headers['Referer'] = referer
    if http_origin:
        headers['Origin'] = http_origin

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

UNWANTED_TERMS = [
    "FHD", "UA", "720х576", "1280х720", "r1", "r2", "r3", "hd1",
    "tva.org.ua", "tva*org*ua", "[720x576]", "UKR|", "fas-tv*com", "[RU-EN-CZ]", "iptv*org*"
]

UNWANTED_REGEX = re.compile(
    r"|".join([re.escape(term) for term in UNWANTED_TERMS]), re.IGNORECASE
)

def clean_channel_name(name):
    name = re.sub(r"\s*\([^)]*\)", "", name)
    name = UNWANTED_REGEX.sub("", name)
    return name.strip()

def extract_channel_name(extinf_line):
    name = extinf_line.split(",", 1)[1].strip()
    return clean_channel_name(name)

def parse_m3u(m3u_data):
    channels = []
    lines = m3u_data.splitlines()
    current_channel = None
    current_group = None
    user_agent = None
    referrer = None
    http_origin = None

    for line in lines:
        if line.startswith("#EXTINF"):
            excluded_groups = [
                'group-title="Взрослые [ВХОД СТРОГО 18+]"',
                'group-title="ИНФО"',
                'group-title="Региональные"',
                'group-title="Региoнальные"',
                'group-title="Казахстан"',
                'group-title="Беларусь"',
                'group-title="ОБЩИЕ"',
                'group-title="Кухня"',
                'group-title="KIDS"',
                'group-title="ДІТИ"',
                'group-title="РАДІО"',
                'group-title="РЕЛАКС"',
                'group-title="SPANISH"',
                'group-title="Детские"',
                'group-title="Fashion"',
                'Эстония', "Литва", "Латвия", "Грузия", "Армения", "Азербайджан", "МОЛДОВА",
                'group-title="Религиозные"',
                'group-title="Христианские"',
                'group-title="DOCUMENTARY"',
                'group-title="MOVIES"',
                'group-title="ENTERTAINMENT"',
                'group-title="EVENTS"',
                #'[нестабильные]',
                'magenta',
                'РЕЛАКС- расслабление',
                'Криминальная Россия',
                'Следствие вели',
                'Tom & Jerry',
                'Симпсоны',
                'CHILDREN',
                'AMAZON',
                'ТВA'
            ]
            if any(excluded_group in line for excluded_group in excluded_groups):
                current_channel = None
                continue

            if 'group-title="' in line:
                group_title = line.split('group-title="')[1].split('"')[0]
                normalized_category = normalize_category(group_title)
                line = line.replace(f'group-title="{group_title}"', f'group-title="{normalized_category}"')
            elif current_group:
                normalized_category = normalize_category(current_group)
                info_parts = line.split(',', 1)
                info_part = info_parts[0]
                line = f'{info_part} group-title="{normalized_category}",{info_parts[1]}'
            else:
                normalized_category = "Не сортировано"
                info_parts = line.split(',', 1)
                info_part = info_parts[0]
                line = f'{info_part} group-title="{normalized_category}",{info_parts[1]}'

            match = re.search(r'tvg-id="?([^"\s]+)"?', line)
            tvg_id = match.group(1) if match else None
            cleaned_name = clean_channel_name(line.split(",", 1)[1])
            line = f"{line.split(',')[0]},{cleaned_name}"
            current_channel = {"info": line, "stream": None, "tvg-id": tvg_id, "category": normalized_category}

        elif line.startswith("#EXTGRP:"):
            current_group = line.replace("#EXTGRP:", "").strip()

        elif line.startswith("#EXTVLCOPT:http-user-agent="):
            user_agent = line.split("=")[1].strip()

        elif line.startswith("#EXTVLCOPT:http-referrer="):
            referrer = line.split("=")[1].strip()

        elif line.startswith("#EXTVLCOPT:http-origin="):
            http_origin = line.split("=")[1].strip()

        elif line and current_channel:
            current_channel["stream"] = line
            current_channel["user_agent"] = user_agent
            current_channel["referrer"] = referrer
            current_channel["http_origin"] = http_origin
            channels.append(current_channel)
            current_channel = None
            user_agent = None
            referrer = None
            http_origin = None
            current_group = None

    return channels

async def check_channels(channels):
    tasks = []
    for channel in channels:
        task = is_stream_working(
            channel["stream"],
            user_agent=channel.get("user_agent"),
            referer=channel.get("referrer"),
            http_origin=channel.get("http_origin")
        )
        tasks.append(task)

    results = await asyncio.gather(*tasks)

    for channel, is_working in zip(channels, results):
        if is_working:
            print(f"Channel {channel['tvg-id']} - Stream is working.")
        else:
            print(f"Channel {channel['tvg-id']} - Stream is not working or blocked.")

    return results

async def merge_m3u_channels_async(*channel_lists):
    all_channels = []
    for channel_list in channel_lists:
        all_channels.extend(channel_list)

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
    with open(filename, "w", encoding="utf-8") as f:
        f.write(f"{url_tvg}\n")

        sorted_channels = sorted(channels, key=lambda channel: extract_channel_name(channel['info']).lower())

        for channel in sorted_channels:
            f.write(f"{channel['info']}\n")

            stream_url = channel['stream']
            if format_type == 'TiviMate':
                if channel.get("user_agent"):
                    stream_url += f'|User-Agent="{channel["user_agent"]}"'

                if channel.get("referrer"):
                    stream_url += f'|Referer="{channel["referrer"]}"'

                if channel.get("http_origin"):
                    stream_url += f'|Origin="{channel["http_origin"]}"'

            elif format_type == 'Televizo':
                if channel.get("referrer"):
                    f.write(f"#EXTVLCOPT:http-referrer={channel['referrer']}\n")

                if channel.get("http_origin"):
                    f.write(f"#EXTVLCOPT:http-origin={channel['http_origin']}\n")

                if channel.get("user_agent"):
                    f.write(f"#EXTVLCOPT:http-user-agent={channel['user_agent']}\n")

            f.write(f"{stream_url}\n")

async def main():
    parser = argparse.ArgumentParser(description="Merge and validate IPTV m3u playlists.")
    parser.add_argument("url1", help="URL to the first m3u file")
    parser.add_argument("url2", help="URL to the second m3u file")
    parser.add_argument("url3", help="URL to the third m3u file")
    parser.add_argument("url4", help="URL to the fourth m3u file")
    parser.add_argument("url5", help="URL to the fifth m3u file")
    parser.add_argument("url6", help="URL to the sixth m3u file")
    parser.add_argument("url7", help="URL to the seventh m3u file with Torrent TV channels")
    args = parser.parse_args()

    m3u_data1 = await download_m3u(args.url1)
    m3u_data2 = await download_m3u(args.url2)
    m3u_data3 = await download_m3u(args.url3)
    m3u_data4 = await download_m3u(args.url4)
    m3u_data5 = await download_m3u(args.url5)
    m3u_data6 = await download_m3u(args.url6)
    m3u_data7 = await download_m3u(args.url7)

    url_tvg = extract_url_tvg()

    channels1 = parse_m3u(m3u_data1)
    channels2 = parse_m3u(m3u_data2)
    channels3 = parse_m3u(m3u_data3)
    channels4 = parse_m3u(m3u_data4)
    channels5 = parse_m3u(m3u_data5)
    channels6 = parse_m3u(m3u_data6)
    channels7 = parse_m3u(m3u_data7)

    torrent_tv_channels_7 = [
        channel for channel in channels7
        if channel.get("category") == "↕️ Торрент ТВ ↕️"
    ]

    #merged_channels_123456 = await merge_m3u_channels_async(channels1, channels2, channels3, channels4, channels5)

    merged_torrent_tv_channels = await merge_m3u_channels_without_check(
        #channels6 + torrent_tv_channels_7
        channels6
    )

    #merged_channels = merged_channels_123456 + merged_torrent_tv_channels
    merged_channels = merged_torrent_tv_channels

    write_m3u("tivimate.m3u", merged_channels, url_tvg, "TiviMate")
    print("TiviMate playlist written to tivimate.m3u")

    write_m3u("televizo.m3u", merged_channels, url_tvg, "Televizo")
    print("Televizo playlist written to televizo.m3u")

if __name__ == "__main__":
    asyncio.run(main())
