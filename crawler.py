import os
import json
import requests
import subprocess
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urljoin
from datetime import datetime, timedelta, timezone
from dateutil import parser
from dotenv import load_dotenv

load_dotenv()
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
GLOBAL_URL_FILE = os.path.join(BASE_DIR, "all_image_urls.txt")
MAX_URLS = 5000
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

try:
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        config_doc = json.load(f)
except Exception as e:
    print(f"Lỗi đọc config: {e}")
    exit(1)

# Đọc các URL đã có
processed_history = set()
existing_urls = []
if os.path.exists(GLOBAL_URL_FILE):
    with open(GLOBAL_URL_FILE, 'r', encoding='utf-8') as f:
        for line in f:
            u = line.strip()
            if u:
                existing_urls.append(u)
                processed_history.add(u)

def check_image_recent(url, max_days):
    if not url: return False
    try:
        r = requests.head(url, headers=HEADERS, timeout=10, allow_redirects=True)
        if r.status_code != 200: return False
        last_mod = r.headers.get("Last-Modified")
        if last_mod:
            mod_date = parser.parse(last_mod)
            if mod_date.tzinfo is None: mod_date = mod_date.replace(tzinfo=timezone.utc)
            if (datetime.now(timezone.utc) - mod_date) > timedelta(days=max_days): return False
        return True
    except: return False

def get_prioritized_patterns(domain_config):
    patterns = set()
    raw_patterns = set()
    replacements = domain_config.get("replacements", {})
    if isinstance(replacements, list):
        raw_patterns.update(replacements)
    elif isinstance(replacements, dict):
        raw_patterns.update(replacements.keys())
    for r in domain_config.get("rules", []):
        pat = r.get("pattern")
        if pat: raw_patterns.add(pat)
        
    for p in raw_patterns:
        patterns.add(p)
        if p.endswith('.jpg') or p.endswith('.webp') or p.endswith('.png') or p.endswith('.jpeg'):
            base_p = p.rsplit('.', 1)[0]
            patterns.add(f"{base_p}.jpg")
            patterns.add(f"{base_p}.webp")
            patterns.add(f"{base_p}.png")
            patterns.add(f"{base_p}.jpeg")
            
    return patterns

def extract_image_from_wp_api(item, domain_config):
    patterns = get_prioritized_patterns(domain_config)
    html = item.get('content', {}).get('rendered', '')
    if html:
        soup = BeautifulSoup(html, 'html.parser')
        all_imgs = soup.find_all('img')
        if patterns:
            for img in all_imgs:
                src = img.get('src')
                if src and any(p in src for p in patterns): return src
    og_url = item.get('yoast_head_json', {}).get('og_image', [{}])[0].get('url')
    if og_url: return og_url
    if html and all_imgs: return all_imgs[0].get('src')
    return None

def find_best_image_on_product_page(product_url, domain_config):
    try:
        r = requests.get(product_url, headers=HEADERS, timeout=20)
        if r.status_code != 200: return None
        soup = BeautifulSoup(r.text, "html.parser")
        patterns = get_prioritized_patterns(domain_config)
        all_imgs = soup.find_all('img')
        if patterns:
            for img in all_imgs:
                src = img.get('src') or img.get('data-src') or img.get('data-lazy-src')
                if src and any(p in src for p in patterns): return src
        img_sel = domain_config.get("image_url_selector")
        if img_sel:
            imgs = soup.select(img_sel)
            if imgs: return imgs[0].get('src') or imgs[0].get('data-src') or imgs[0].get('data-lazy-src')
        og = soup.find('meta', property='og:image')
        if og and og.get('content'): return og.get('content')
        for img in all_imgs:
            src = img.get('src') or img.get('data-src') or img.get('data-lazy-src')
            if src: return src
    except: pass
    return None

def scrape_api(domain_config, domain_netloc):
    new_urls = []
    page = 1
    max_pages = domain_config.get("max_api_pages", 5)
    max_days = domain_config.get("max_days_old", 1)
    
    while page <= max_pages:
        api_url = f"https://{domain_netloc}/wp-json/wp/v2/product?per_page=100&page={page}&orderby=date&order=desc"
        try:
            r = requests.get(api_url, headers=HEADERS, timeout=20)
            if r.status_code != 200: break
            products = r.json()
            if not products or not isinstance(products, list): break
            
            for p in products:
                img_url = extract_image_from_wp_api(p, domain_config)
                if not img_url: continue
                if img_url in processed_history: continue
                
                if domain_config.get("check_recency", True):
                    if not check_image_recent(img_url, max_days): return new_urls
                
                new_urls.append(img_url)
                processed_history.add(img_url)
            page += 1
        except: break
    return new_urls

def scrape_html_list(domain_config, domain_netloc):
    new_urls = []
    base_url = domain_config.get("base_url", f"https://{domain_netloc}/shop")
    product_selector = domain_config.get("product_url_selector", "a.woocommerce-LoopProduct-link")
    try:
        r = requests.get(base_url, headers=HEADERS, timeout=20)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, 'html.parser')
            for node in soup.select(product_selector):
                product_url = node.get('href')
                if not product_url: continue
                product_url = urljoin(base_url, product_url)
                
                img_url = find_best_image_on_product_page(product_url, domain_config)
                if img_url and img_url not in processed_history:
                    new_urls.append(img_url)
                    processed_history.add(img_url)
    except: pass
    return new_urls

def scrape_prevnext(domain_config, domain_netloc):
    new_urls = []
    base_url = domain_config.get("base_url", f"https://{domain_netloc}/shop")
    first_sel = domain_config.get("first_product_selector", ".product-small a.woocommerce-LoopProduct-link")
    next_sel = domain_config.get("next_product_selector", "a:has(i.icon-angle-right)")
    max_items = 100
    try:
        r = requests.get(base_url, headers=HEADERS, timeout=20)
        if r.status_code != 200: return []
        soup = BeautifulSoup(r.text, "html.parser")
        first_a = soup.select_one(first_sel)
        if not first_a or not first_a.get('href'): return []
        current_url = urljoin(base_url, first_a.get('href'))
        count = 0
        while current_url and count < max_items:
            r2 = requests.get(current_url, headers=HEADERS, timeout=20)
            if r2.status_code != 200: break
            img_url = find_best_image_on_product_page(current_url, domain_config)
            
            if img_url:
                if img_url in processed_history: break
                new_urls.append(img_url)
                processed_history.add(img_url)
            
            soup2 = BeautifulSoup(r2.text, "html.parser")
            next_tag = soup2.select_one(next_sel)
            if not next_tag or not next_tag.get('href'): break
            current_url = urljoin(current_url, next_tag.get('href'))
            count += 1
    except: pass
    return new_urls

import re

def scrape_homepage_direct(domain_config, domain_netloc):
    new_urls = []
    base_url = domain_config.get("base_url", f"https://{domain_netloc}/shop")
    product_selector = domain_config.get("product_url_selector")
    
    try:
        r = requests.get(base_url, headers=HEADERS, timeout=20)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, 'html.parser')
            
            nodes = []
            if product_selector:
                nodes = soup.select(product_selector)
            else:
                for a in soup.find_all('a', href=True):
                    if '/product/' in a['href'].lower() or '/shop/' in a['href'].lower():
                        nodes.append(a)
                        
            for node in nodes:
                img = node.find('img')
                if not img: continue
                
                src = img.get('src') or img.get('data-src') or img.get('data-lazy-src')
                if src:
                    # Khôi phục link ảnh gốc (xóa hậu tố kích thước thumbnail của WordPress vd: -300x300.jpg -> .jpg)
                    src = re.sub(r'-\d+x\d+(\.[a-zA-Z]+)$', r'\1', src)
                    
                    if src not in processed_history:
                        if domain_config.get("check_recency", True):
                            max_days = domain_config.get("max_days_old", 1)
                            if not check_image_recent(src, max_days):
                                continue
                        
                        new_urls.append(src)
                        processed_history.add(src)
    except: pass
    return new_urls

def process_domain(domain):
    domain_config = config_doc.get("domains", {}).get(domain, {})
    source_type = domain_config.get("source_type", "api")
    print(f"👉 Đang cào [{domain}] (Engine: {source_type.upper()})...", end=" ", flush=True)
    
    if source_type in ["api", "api-attachment"]:
        return scrape_api(domain_config, domain)
    elif source_type == "product-list":
        return scrape_html_list(domain_config, domain)
    elif source_type == "prevnext":
        return scrape_prevnext(domain_config, domain)
    elif source_type == "html-images":
        return scrape_homepage_direct(domain_config, domain)
    else:
        return scrape_api(domain_config, domain)

def push_to_github():
    print("Pushing to GitHub...")
    try:
        subprocess.run(["git", "status"], cwd=BASE_DIR, capture_output=True, text=True, timeout=10, check=True)
        subprocess.run(["git", "add", "all_image_urls.txt"], cwd=BASE_DIR, capture_output=True, text=True, timeout=10)
        
        diff = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=BASE_DIR, capture_output=True, text=True, timeout=10)
        if diff.returncode == 0:
            print("Không có thay đổi để push.")
            return

        log_res = subprocess.run(["git", "log", "--oneline", "-1"], cwd=BASE_DIR, capture_output=True, text=True, timeout=10)
        
        if log_res.returncode == 0 and log_res.stdout.strip():
            subprocess.run(["git", "commit", "--amend", "--no-edit", "--reset-author"], cwd=BASE_DIR, capture_output=True, text=True, timeout=15)
        else:
            subprocess.run(["git", "commit", "-m", "Update image urls"], cwd=BASE_DIR, capture_output=True, text=True, timeout=15)
            
        push_res = subprocess.run(["git", "push", "--force"], cwd=BASE_DIR, capture_output=True, text=True, timeout=60)
        if push_res.returncode == 0:
            print("Push GitHub thành công!")
        else:
            print(f"Lỗi push: {push_res.stderr}")
    except Exception as e:
        print(f"Lỗi git push: {e}")

def send_telegram_report(report_text):
    bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
    chat_id = os.getenv('TELEGRAM_CHAT_ID')
    if not bot_token or not chat_id:
        return
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {'chat_id': chat_id, 'text': report_text}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Lỗi gửi Telegram: {e}")

def main():
    domains = list(config_doc.get("domains", {}).keys())
    all_new_urls = []
    
    print("="*50)
    print("🚀 BẮT ĐẦU CHẠY CRAWLER (CHẾ ĐỘ TUẦN TỰ)")
    print("="*50)
    
    start_time = datetime.now()
    report_lines = [f"🕷 BÁO CÁO CRAWLER KTB", f"Thời gian: {start_time.strftime('%Y-%m-%d %H:%M:%S')}", ""]
    
    try:
        for d in domains:
            try:
                new_urls = process_domain(d)
                if new_urls:
                    print(f"✅ Xong! Tìm thấy {len(new_urls)} ảnh mới.")
                    report_lines.append(f"✅ {d}: {len(new_urls)} ảnh")
                    all_new_urls.extend(new_urls)
                else:
                    print(f"⚠️ Xong! Không có ảnh mới.")
            except Exception as e:
                print(f"❌ Lỗi: {e}")
                report_lines.append(f"❌ {d}: Lỗi")
    except KeyboardInterrupt:
        print("\n\n🛑 NGƯỜI DÙNG ĐÃ DỪNG CRAWLER ĐỘT NGỘT (Ctrl+C)!")
        print("Đang tiến hành lưu các ảnh đã tìm thấy và đẩy lên Github...\n")
        report_lines.append("\n🛑 DỪNG ĐỘT NGỘT BỞI NGƯỜI DÙNG (Ctrl+C)!")

                
    if all_new_urls:
        print(f"\n🎉 TỔNG CỘNG: {len(all_new_urls)} URL mới. Đang cập nhật file...")
        report_lines.append("")
        report_lines.append(f"🎉 TỔNG CỘNG MỚI: {len(all_new_urls)} ảnh")
        
        # Đảo ngược để url thu thập sớm nhất nằm sau, mới nhất nằm trên cùng
        all_new_urls.reverse()
        
        # Hợp nhất với danh sách cũ
        final_list = all_new_urls + existing_urls
        # Lọc trùng lặp nhưng giữ nguyên thứ tự
        seen = set()
        unique_final_list = []
        for u in final_list:
            if u not in seen:
                seen.add(u)
                unique_final_list.append(u)
                
        unique_final_list = unique_final_list[:MAX_URLS]
        
        with open(GLOBAL_URL_FILE, 'w', encoding='utf-8') as f:
            f.write('\n'.join(unique_final_list) + '\n')
            
        push_to_github()
    else:
        print("\n💤 Không tìm thấy URL nào mới trên tất cả các trang.")
        report_lines.append("")
        report_lines.append("💤 Không có ảnh mới nào.")

    # Send report
    exec_time = (datetime.now() - start_time).total_seconds()
    report_lines.append(f"⏱ Thời gian chạy: {exec_time:.1f}s")
    send_telegram_report("\n".join(report_lines))
    print(f"⏱ Thời gian chạy: {exec_time:.1f}s")

if __name__ == "__main__":
    main()
