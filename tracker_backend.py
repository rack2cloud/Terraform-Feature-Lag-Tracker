import requests
import feedparser
import json
import re
import sys
import time
from datetime import datetime, timezone
from dateutil import parser
from bs4 import BeautifulSoup  # BeautifulSoup is usually available in standard runners or via dependency

# --- CONFIGURATION HUB ---
CLOUD_CONFIG = {
    "aws": {
        "urls": ["https://aws.amazon.com/about-aws/whats-new/recent/feed/"],
        "tf_repo": "hashicorp/terraform-provider-aws",
        "stop_words": {'amazon', 'aws', 'now', 'supports', 'available', 'introducing', 'general', 'availability', 'for', 'with', 'announcing', 'new', 'capabilities', 'feature', 'region', 'launch'},
        "service_heuristic": r"Amazon\s+(.*?)\b",
        "resource_prefix": "aws_"
    },
    "azure": {
        "urls": [
            "https://azurecomcdn.azureedge.net/en-us/updates/feed/", 
            "https://azure.microsoft.com/en-us/updates/feed/",       
            "https://azure.microsoft.com/en-us/blog/feed/"           
        ],
        "tf_repo": "hashicorp/terraform-provider-azurerm",
        "stop_words": {'azure', 'microsoft', 'public', 'preview', 'general', 'availability', 'now', 'available', 'support', 'in', 'generally', 'updates', 'update', 'new', 'announcing'},
        "service_heuristic": r"Azure\s+(.*?)\b",
        "resource_prefix": "azurerm_"
    },
    "gcp": {
        "urls": ["https://cloud.google.com/feeds/gcp-release-notes.xml"],
        "tf_repo": "hashicorp/terraform-provider-google",
        "stop_words": {'google', 'cloud', 'platform', 'gcp', 'beta', 'ga', 'release', 'notes', 'available', 'support', 'feature', 'launch', 'new', 'announcing'},
        "service_heuristic": r"^(.*?):",
        "resource_prefix": "google_"
    }
}

# GLOBAL SYNONYMS
SYNONYMS = {
    "elastic kubernetes service": "eks", "kubernetes": "eks", "elastic compute cloud": "ec2", "ec2": "ec2",
    "simple storage service": "s3", "relational database service": "rds", "lambda": "lambda", "dynamodb": "dynamodb",
    "cloudwatch": "cloudwatch", "identity and access management": "iam", "bedrock": "bedrock", "sagemaker": "sagemaker",
    "vpc": "vpc", "compute engine": "compute", "kubernetes engine": "container", "cloud storage": "storage",
    "cloud sql": "sql", "cloud run": "cloudrun", "bigquery": "bigquery", "security command center": "scc",
    "vpc service controls": "vpcsc", "cloud functions": "cloudfunctions", "artifact registry": "artifactregistry",
    "kubernetes service": "kubernetes", "aks": "kubernetes", "cosmos db": "cosmosdb", "blob storage": "storage",
    "virtual machines": "virtual_machine", "virtual network": "virtual_network"
}

GITHUB_API_BASE = "https://api.github.com/repos"
OUTPUT_FILE = "r2c_lag_data.json"
HEADERS = {'User-Agent': 'Rack2Cloud-Bot/2.0', 'Accept': 'application/rss+xml, application/xml, text/xml, */*'}

def make_aware(dt):
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
        clean_name = re.sub(r'[^a-zA-Z0-9]', '-', self.feature[:25]).lower()
        return {
            "id": f"{self.cloud}-{clean_name}", "cloud": self.cloud, "service": self.service[:25], 
            "feature": self.feature, "link": self.link, "status": self.tf_status, "version": self.tf_version,
            "lag": self.lag_days, "date": self.ga_date.strftime("%Y-%m-%d")
        }

# --- HISTORICAL SCRAPERS (2024 Backfill) ---
def fetch_aws_archive():
    print("⏳ [AWS] Deep Scan (2024 Archives)...")
    articles = []
    # Loop through 2024 months. 
    # NOTE: GitHub Actions time limit is ~6 hrs, this takes ~2 mins.
    for month in range(1, 13): 
        url = f"https://aws.amazon.com/about-aws/whats-new/2024/{month:02d}/"
        try:
            r = requests.get(url, headers=HEADERS, timeout=10)
            if r.status_code != 200: continue
            
            # Simple Regex parsing to avoid heavy BS4 dependency if not present
            # Looking for: <li class="whats-new-list-item"> ... <h3>Title</h3> ... </li>
            matches = re.findall(r'<li class="whats-new-list-item">.*?<a href="(.*?)">.*?<h3>(.*?)</h3>', r.text, re.DOTALL)
            
            for link_suffix, title in matches:
                title = title.strip()
                link = f"https://aws.amazon.com{link_suffix}"
                
                # Service Heuristic
                service = "General"
                if "Amazon" in title:
                    parts = title.split("Amazon ")
                    if len(parts) > 1: service = parts[1].split(" ")[0]
                
                # Approximate date (1st of the month) since extracting exact date via regex is messy
                dt = datetime(2024, month, 1, tzinfo=timezone.utc)
                
                articles.append(FeatureRecord("aws", service.replace(",", ""), title, dt, link))
        except: continue
    print(f"   ✅ Found {len(articles)} historical AWS items.")
    return articles

def fetch_azure_blog_archive():
    print("⏳ [AZURE] Deep Scan (Blog Archives)...")
    articles = []
    # Azure Blog RSS is deep by default. We just parse the main feed aggressively.
    url = "https://azure.microsoft.com/en-us/blog/feed/"
    try:
        d = feedparser.parse(url)
        for entry in d.entries:
            # We accept older entries here
            dt = parser.parse(entry.published)
            if dt.year < 2024: continue # Only want 2024+
            
            title = entry.title
            match = re.search(r"Azure\s+(.*?)\b", title)
            service = match.group(1) if match else "General"
            
            articles.append(FeatureRecord("azure", service, title, dt, entry.link))
    except: pass
    print(f"   ✅ Found {len(articles)} historical Azure items.")
    return articles

# --- STANDARD FETCHERS ---
def fetch_feed_with_failover(cloud_name, config):
    print(f"📡 [{cloud_name.upper()}] Fetching Live Feed...")
    valid_entries = []
    for url in config['urls']:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            if resp.status_code != 200: continue
            feed = feedparser.parse(resp.content)
            if not feed.entries: continue
            valid_entries = feed.entries[:35]
            break
        except: continue
            
    records = []
    for entry in valid_entries:
        title = entry.title
        match = re.search(config['service_heuristic'], title)
        service = match.group(1).replace(",", "").strip() if match else "General"
        try: dt = parser.parse(entry.get('updated', entry.get('published')))
        except: dt = datetime.now(timezone.utc)
        records.append(FeatureRecord(cloud_name, service, title, dt, entry.link))
    return records

def fetch_tf_releases(repo):
    print(f"📦 [{repo}] Fetching Terraform Releases...")
    # Fetch 2 pages to get deeper history for matching older features
    all_releases = []
    for page in range(1, 3): 
        url = f"{GITHUB_API_BASE}/{repo}/releases?page={page}&per_page=100"
        try:
            resp = requests.get(url, headers=HEADERS)
            if resp.status_code != 200: break
            for item in resp.json():
                try:
                    dt = parser.parse(item['published_at'])
                    all_releases.append({
                        "version": item.get('tag_name', 'v0.0.0'),
                        "date": make_aware(dt), 
                        "body": (item.get('body') or "").lower()
                    })
                except: continue
        except: break
    return all_releases

def process_features(features, releases, cloud_name, config):
    if not releases: return [f.to_dict() for f in features]
    releases.sort(key=lambda x: x['date'])
    
    processed = []
    for feat in features:
        valid_releases = [r for r in releases if r['date'] >= feat.ga_date]
        
        # Matching Logic
        raw_tokens = re.findall(r'\w+', feat.feature.lower())
        tokens = set()
        for t in raw_tokens:
            if t not in config['stop_words']:
                tokens.add(t)
                if t.endswith('s'): tokens.add(t[:-1])
        
        service_raw = feat.service.lower()
        service_token = SYNONYMS.get(service_raw, service_raw)
        resource_guess = f"{config['resource_prefix']}{service_token}"
        
        best_match = None
        for release in valid_releases:
            body = release['body']
            resource_match = resource_guess in body
            service_match = service_token in body
            hits = sum(1 for t in tokens if t in body)
            score = hits / len(tokens) if tokens else 0
            
            threshold = 0.20 if resource_match else (0.25 if service_match else 0.30)
            
            if score >= threshold: 
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
        
        processed.append(feat.to_dict())
    return processed

def main():
    all_records = []
    
    # 1. READ EXISTING FILE (To preserve old data)
    try:
        with open(OUTPUT_FILE, 'r') as f:
            existing = json.load(f)
            print(f"📂 Loaded {len(existing)} existing records.")
            # Convert dicts back to FeatureRecords? No, just append dicts to final list.
            all_records.extend(existing)
    except:
        print("⚠️ No existing file found.")

    # 2. DECIDE: DO WE NEED A DEEP SCAN?
    # If we have less than 100 records, assume we need to backfill 2024.
    run_deep_scan = len(all_records) < 100
    
    new_features = []
    
    if run_deep_scan:
        print("🚀 Record count low. Engaging DEEP HISTORY SCAN (2024)...")
        new_features.extend(fetch_aws_archive())
        new_features.extend(fetch_azure_blog_archive())
    
    # 3. FETCH LIVE FEEDS (Daily Update)
    for cloud, config in CLOUD_CONFIG.items():
        new_features.extend(fetch_feed_with_failover(cloud, config))

    # 4. MATCH NEW FEATURES
    # We only run the expensive matching logic on the NEW items
    final_new_data = []
    if new_features:
        # Load releases once per cloud to save API calls
        aws_rels = fetch_tf_releases(CLOUD_CONFIG['aws']['tf_repo'])
        az_rels = fetch_tf_releases(CLOUD_CONFIG['azure']['tf_repo'])
        gcp_rels = fetch_tf_releases(CLOUD_CONFIG['gcp']['tf_repo'])
        
        # Group by cloud
        aws_feats = [f for f in new_features if f.cloud == 'aws']
        az_feats = [f for f in new_features if f.cloud == 'azure']
        gcp_feats = [f for f in new_features if f.cloud == 'gcp']
        
        final_new_data.extend(process_features(aws_feats, aws_rels, 'aws', CLOUD_CONFIG['aws']))
        final_new_data.extend(process_features(az_feats, az_rels, 'azure', CLOUD_CONFIG['azure']))
        final_new_data.extend(process_features(gcp_feats, gcp_rels, 'gcp', CLOUD_CONFIG['gcp']))

    # 5. MERGE & DEDUPLICATE
    # Combine old + new
    all_records.extend(final_new_data)
    
    # Dedup based on Feature Name to prevent duplicates
    unique_map = {item['feature']: item for item in all_records}
    final_list = list(unique_map.values())
    
    # Sort by Date (Newest First)
    final_list.sort(key=lambda x: x['date'], reverse=True)

    with open(OUTPUT_FILE, 'w') as f:
        json.dump(final_list, f, indent=2)
    
    print(f"✅ Success! Total Database Size: {len(final_list)} records.")

if __name__ == "__main__":
    main()
