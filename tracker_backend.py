import requests
import feedparser
import json
import re
import sys
import time
from datetime import datetime, timezone
from dateutil import parser
from bs4 import BeautifulSoup

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
HEADERS = {'User-Agent': 'Rack2Cloud-Bot/2.1', 'Accept': 'application/rss+xml, application/xml, text/xml, */*'}

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

# --- HISTORICAL SCRAPERS (ROBUST BS4 VERSION) ---
def fetch_aws_archive():
    print("⏳ [AWS] Deep Scan (2024 Archives)...")
    articles = []
    
    # Scan 2024 + 2025 (Jan)
    target_months = []
    for m in range(1, 13): target_months.append((2024, m))
    target_months.append((2025, 1))

    for year, month in target_months:
        url = f"https://aws.amazon.com/about-aws/whats-new/{year}/{month:02d}/"
        try:
            r = requests.get(url, headers=HEADERS, timeout=10)
            if r.status_code != 200: continue
            
            soup = BeautifulSoup(r.content, 'html.parser')
            
            # Find generic list items rather than specific classes
            for item in soup.find_all('li'):
                # Look for a link inside the list item
                link_tag = item.find('a')
                if not link_tag: continue
                
                href = link_tag.get('href', '')
                text = link_tag.get_text().strip()
                
                # Filter: Must be a "whats-new" link and have reasonable length
                if '/about-aws/whats-new/20' in href and len(text) > 10:
                    full_link = f"https://aws.amazon.com{href}" if href.startswith('/') else href
                    
                    # Service Heuristic
                    service = "General"
                    if "Amazon" in text:
                        parts = text.split("Amazon ")
                        if len(parts) > 1: service = parts[1].split(" ")[0]
                    
                    dt = datetime(year, month, 1, tzinfo=timezone.utc)
                    articles.append(FeatureRecord("aws", service.replace(",", ""), text, dt, full_link))
                    
        except Exception as e:
            continue
            
    print(f"   ✅ Found {len(articles)} historical AWS items.")
    return articles

def fetch_azure_blog_archive():
    print("⏳ [AZURE] Deep Scan (Blog Archives)...")
    articles = []
    url = "https://azure.microsoft.com/en-us/blog/feed/"
    try:
        d = feedparser.parse(url)
        for entry in d.entries:
            dt = parser.parse(entry.published)
            # Accept anything from 2024 onwards
            if dt.year < 2024: continue 
            
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
    # Fetch 3 pages to get deeper history (needed for 2024 matching)
    all_releases = []
    for page in range(1, 4): 
        url = f"{GITHUB_API_BASE}/{repo}/
