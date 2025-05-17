import requests
import xmltodict
import shutil
import os
import gzip
import re
import sys

# Files
M3U_FILE = "televizo.m3u"
EPG_GZ_FILE = "myepg.xml.gz"
EPG_XML_FILE = "epg.xml"
OUTPUT_EPG = "my-epg.xml"

# 1. Download and extract EPG if needed
def download_epg(url, epg_file):
    response = requests.get(url, stream=True)
    if response.status_code == 200:
        with open(epg_file, "wb") as f:
            f.write(response.content)
        print(f"✅ EPG downloaded successfully: {url}")

        with open(epg_file, "rb") as f:
            if f.read(2) == b"\x1f\x8b":
                print("📦 EPG is archived, extracting...")
                with gzip.open(epg_file, "rb") as gz:
                    with open(EPG_XML_FILE, "wb") as xml_f:
                        xml_f.write(gz.read())
                print("✅ EPG extracted successfully.")
            else:
                print("✅ EPG is not archived. Using as is.")
                os.rename(epg_file, EPG_XML_FILE)

        if os.path.exists(epg_file):
            try:
                os.remove(epg_file)
                print(f"✅ Removed archive: {epg_file}")
            except PermissionError as e:
                print(f"❌ Failed to remove {epg_file}: {e}")
    else:
        print(f"❌ Failed to download EPG: {url}, HTTP Status: {response.status_code}")
        exit()

# 2. Read M3U playlist and extract tvg-ids & channel names
def get_m3u_data(m3u_file):
    if not os.path.exists(m3u_file):
        print(f"❌ File not found: {m3u_file}")
        exit()

    tvg_ids = set()
    channel_names = set()

    with open(m3u_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("#EXTINF"):
                tvg_id_match = re.search(r'tvg-id="([^"]+)"', line, re.IGNORECASE)
                name_match = re.search(r',(.+)$', line)

                if tvg_id_match:
                    tvg_ids.add(tvg_id_match.group(1).strip().lower())

                if name_match:
                    channel_names.add(name_match.group(1).strip().lower())

    print(f"✅ Found {len(tvg_ids)} unique tvg-ids in M3U.")
    print(f"✅ Found {len(channel_names)} unique channel names in M3U.")
    return tvg_ids, channel_names

# 🔧 Вспомогательная функция для безопасного получения display-name
def normalize_display_names(display):
    names = []
    if isinstance(display, list):
        for name in display:
            if isinstance(name, dict):
                names.append(name.get("#text", "").strip().lower())
            else:
                names.append(str(name).strip().lower())
    else:
        if isinstance(display, dict):
            names.append(display.get("#text", "").strip().lower())
        else:
            names.append(str(display).strip().lower())
    return names

# 3. Filter EPG based on M3U tvg-ids and names
def filter_epg(m3u_tvg_ids, m3u_channel_names):
    if not os.path.exists(EPG_XML_FILE):
        print("❌ EPG file not found! Exiting...")
        exit()

    with open(EPG_XML_FILE, "r", encoding="utf-8") as f:
        epg_data = xmltodict.parse(f.read())

    if "tv" not in epg_data or "channel" not in epg_data["tv"]:
        print("❌ Error: EPG file is empty or has an incorrect format.")
        exit()

    filtered_channels = []
    new_programs = []
    epg_channel_map = {}

    for channel in epg_data["tv"]["channel"]:
        channel_id = channel["@id"].strip().lower()
        display_names = normalize_display_names(channel.get("display-name", []))
        epg_channel_map[channel_id] = display_names

    valid_channel_ids = set()
    for channel_id, names in epg_channel_map.items():
        if channel_id in m3u_tvg_ids or any(name in m3u_channel_names for name in names):
            valid_channel_ids.add(channel_id)

    print(f"✅ Found {len(valid_channel_ids)} matching channels in EPG.")

    for channel in epg_data["tv"]["channel"]:
        if channel["@id"].strip().lower() in valid_channel_ids:
            filtered_channels.append(channel)

    for program in epg_data["tv"].get("programme", []):
        if program["@channel"].strip().lower() in valid_channel_ids:
            new_programs.append(program)

    epg_data["tv"]["channel"] = filtered_channels
    epg_data["tv"]["programme"] = new_programs

    with open(OUTPUT_EPG, "w", encoding="utf-8") as f:
        f.write(xmltodict.unparse(epg_data, pretty=True))

    print(f"✅ Filtered EPG saved as {OUTPUT_EPG}")

    with open(OUTPUT_EPG, "rb") as f_in:
        with gzip.open(EPG_GZ_FILE, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)

    print(f"✅ Filtered EPG archived as {EPG_GZ_FILE}")

    if os.path.exists(EPG_XML_FILE):
        try:
            os.remove(EPG_XML_FILE)
            print(f"✅ Removed temporary file: {EPG_XML_FILE}")
        except PermissionError as e:
            print(f"❌ Failed to remove {EPG_XML_FILE}: {e}")

    if os.path.exists(OUTPUT_EPG):
        try:
            os.remove(OUTPUT_EPG)
            print(f"✅ Removed temporary file: {OUTPUT_EPG}")
        except PermissionError as e:
            print(f"❌ Failed to remove {OUTPUT_EPG}: {e}")

# 🔥 Main process
if __name__ == "__main__":
    epg_urls = sys.argv[1:]

    if not epg_urls:
        print("❌ No EPG URLs provided! Usage: python script.py <EPG_URL1> <EPG_URL2> ...")
        exit()

    combined_epg = {"tv": {"channel": [], "programme": []}}

    for index, url in enumerate(epg_urls, start=1):
        epg_file = f"epg_{index}.xml.gz"
        download_epg(url, epg_file)

        with open(EPG_XML_FILE, "r", encoding="utf-8") as f:
            epg_data = xmltodict.parse(f.read())

        combined_epg["tv"]["channel"].extend(epg_data["tv"]["channel"])
        combined_epg["tv"]["programme"].extend(epg_data["tv"].get("programme", []))

    with open(EPG_XML_FILE, "w", encoding="utf-8") as f:
        f.write(xmltodict.unparse(combined_epg, pretty=True))

    m3u_tvg_ids, m3u_channel_names = get_m3u_data(M3U_FILE)
    filter_epg(m3u_tvg_ids, m3u_channel_names)
