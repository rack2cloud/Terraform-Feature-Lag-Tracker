import requests
import feedparser
import json
import re
import sys
import time
from datetime import datetime, timezone
from dateutil import parser

# --- CONFIGURATION HUB ---
CLOUD_CONFIG = {
    "aws": {
        "urls": ["https://aws.amazon.com/about-aws/whats-new/recent/feed/"],
        "tf_repo": "hashicorp/terraform-provider-aws",
        "stop_words": {'amazon', 'aws', 'now', 'supports', 'available', 'introducing', 'general', 'availability', 'for', 'with', 'announcing'},
        "service_heuristic": "Amazon (.*?) "
    },
    "azure": {
        # RETRY STRATEGY: Try these 3 URLs in order until one works
        "urls": [
            "https://azurecomcdn.azureedge.net/en-us/updates/feed/", # CDN (Often fastest)
            "https://azure.microsoft.com/en-us/updates/feed/",       # Main
            "https://azure.microsoft.com/en-us/blog/feed/"           # Backup (Blog)
        ],
        "tf_repo": "hashicorp/terraform-provider-azurerm",
        "stop_words": {'azure', 'microsoft', 'public', 'preview', 'general', 'availability', 'now', 'available', 'support', 'in', 'generally'},
        "service_heuristic": "Azure (.*?) "
    },
    "gcp": {
        "urls": ["https://cloud.google.com/feeds/gcp-release-notes.xml"],
        "tf_repo": "hashicorp/terraform-provider-google",
        "stop_words": {'google', 'cloud', 'platform', 'gcp', 'beta', 'ga', 'release', 'notes', 'available', 'support'},
        "service_heuristic": "^(.*?):" 
    }
}

GITHUB_API_BASE = "https://api.github.com/repos"
OUTPUT_FILE = "r2c_lag_data.json"

# Generic Headers often work better for RSS than spoofed Chrome headers
HEADERS = {
    'User-Agent': 'Rack2Cloud-Bot/1.0',
    'Accept': 'application/rss+xml, application/xml, text/xml, */*'
}

def make_aware(dt):
    """Force a datetime to be timezone-aware (UTC)."""
    if dt is None: return datetime.now(timezone.utc)
    if dt.tzinfo is None: return dt.replace(tzinfo=timezone.utc)
    return dt

class FeatureRecord:
    def __init__(self, cloud, service, feature, date, link):
        self.cloud = cloud
        self.service = service
        self.feature = feature
        self.ga_date = make_aware(date)
        self.link = link
        self.tf_status = "Not Supported"
        self.tf_version = "--"
        self.lag_days = 0

    def to_dict(self):
        clean_name = re.sub(r'[^a-zA-Z0-9]', '-', self.feature[:20]).lower()
        return {
            "id": f"{self.cloud}-{clean_name}",
            "cloud": self.cloud,
            "service": self.service[:20], 
            "feature": self.feature,
            "link": self.link,
            "status": self.tf_status,
            "version": self.tf_version,
            "lag": self.lag_days,
            "date": self.ga_date.strftime("%Y-%m-%d")
        }

def fetch_feed_with_failover(cloud_name, config):
    print(f"📡 [{cloud_name.upper()}] Fetching Cloud Feed...")
    
    valid_entries = []
    
    # Try every URL in the list
    for url in config['urls']:
        try:
            print(f"   👉 Trying: {url} ...")
            resp = requests.get(url, headers=HEADERS, timeout=10)
            
            if resp.status_code != 200:
                print(f"      ⚠️ Failed (HTTP {resp.status_code})")
                continue
                
            feed = feedparser.parse(resp.content)
            
            if not feed.entries:
                print(f"      ⚠️ Parsed as empty (Soft Block?)")
                continue
                
            # If we get here, we have data!
            print(f"      ✅ Success! Found {len(feed.entries)} items.")
            valid_entries = feed.entries[:20] # Take top 20
            break # Stop trying other URLs
            
        except Exception as e:
            print(f"      ❌ Error: {e}")
            continue
            
    if not valid_entries:
        print(f"   ❌ ALL URLs FAILED for {cloud_name}.")
        return []

    # Process the valid entries
    records = []
    for entry in valid_entries:
        title = entry.title
        link = entry.link
        
        service = "General"
        match = re.search(config['service_heuristic'], title)
        if match:
            service = match.group(1).replace(",", "").strip()
        
        try:
            # Try multiple date fields
            date_str = entry.get('updated', entry.get('published', entry.get('date')))
            dt = parser.parse(date_str)
        except:
            dt = datetime.now(timezone.utc)

        records.append(FeatureRecord(
            cloud=cloud_name,
            service=service,
            feature=title,
            date=dt,
            link=link
        ))
    return records

def fetch_tf_releases(repo):
    print(f"📦 [{repo}] Fetching Terraform Releases...")
    url = f"{GITHUB_API_BASE}/{repo}/releases"
    try:
        resp = requests.get(url, headers=HEADERS)
        if resp.status_code != 200:
            return []
        data = []
        for item in resp.json():
            try:
                dt = parser.parse(item['published_at'])
                data.append({
                    "version": item.get('tag_name', 'v0.0.0'),
                    "date": make_aware(dt), 
                    "body": (item.get('body') or "").lower()
                })
            except: continue
        return data
    except: return []

def process_cloud(cloud_name, config):
    features = fetch_feed_with_failover(cloud_name, config)
    if not features: return []

    releases = fetch_tf_releases(config['tf_repo'])
    if releases:
        releases.sort(key=lambda x: x['date'])
        for feat in features:
            valid_releases = [r for r in releases if r['date'] >= feat.ga_date]
            
            raw_tokens = re.findall(r'\w+', feat.feature.lower())
            tokens = set(raw_tokens) - config['stop_words']
            
            best_match = None
            for release in valid_releases:
                hits = 0
                for t in tokens:
                    if t in release['body']: hits += 1
                
                score = hits / len(tokens) if tokens else 0
                if score > 0.45: 
                    best_match = release
                    break
            
            if best_match:
                feat.tf_status = "Supported"
                feat.tf_version = best_match['version']
                lag = (best_match['date'] - feat.ga_date).days
                feat.lag_days = lag if lag >= 0 else 0
            else:
                feat.tf_status = "Not Supported"
                now = datetime.now(timezone.utc)
                feat.lag_days = (now - feat.ga_date).days

    return [f.to_dict() for f in features]

def main():
    all_data = []
    for cloud, config in CLOUD_CONFIG.items():
        try:
            data = process_cloud(cloud, config)
            all_data.extend(data)
            time.sleep(1)
        except Exception as e:
            print(f"❌ CRITICAL ERROR processing {cloud}: {e}")
            continue

    if not all_data:
        print("❌ No data collected from any cloud.")
        sys.exit(1)

    with open(OUTPUT_FILE, 'w') as f:
        json.dump(all_data, f, indent=2)
    
    print(f"✅ Success! Wrote {len(all_data)} total records to {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
