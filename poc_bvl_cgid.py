"""抓宝格丽各品类 cgid（US by-category 页）。临时脚本。"""
import re
from playwright.sync_api import sync_playwright
UA=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36")
HEAD={"accept":"*/*","accept-language":"en-US,en;q=0.9","sec-ch-ua":'"Chromium";v="145", "Not(A:Brand";v="24", "Google Chrome";v="145"',"sec-ch-ua-mobile":"?0","sec-ch-ua-platform":'"Windows"'}
STEALTH=("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});window.chrome={runtime:{}};")

PAGES=[("rings","https://www.bulgari.com/en-us/jewelry/rings"),
       ("necklaces","https://www.bulgari.com/en-us/jewelry/necklaces"),
       ("bracelets","https://www.bulgari.com/en-us/jewelry/bracelets"),
       ("earrings","https://www.bulgari.com/en-us/jewelry/earrings"),
       ("engagement","https://www.bulgari.com/en-us/engagement-and-wedding"),
       ("engagement-rings","https://www.bulgari.com/en-us/engagement-and-wedding/engagement-rings")]

def main():
    with sync_playwright() as p:
        b=p.chromium.launch(headless=True,args=["--disable-blink-features=AutomationControlled"])
        c=b.new_context(user_agent=UA,locale="en-us",viewport={"width":1440,"height":1000},extra_http_headers=HEAD); c.add_init_script(STEALTH); pg=c.new_page()
        cur={"u":None}
        pg.on("request",lambda r:cur.__setitem__("u",r.url) if "product-search" in r.url else None)
        for name,url in PAGES:
            cur["u"]=None
            try:
                r=pg.goto(url,wait_until="domcontentloaded",timeout=60000); pg.wait_for_timeout(4000)
                u=cur["u"] or ""
                m=re.search(r"cgid%3D(\d+)|cgid%253D(\d+)",u)
                cgid=(m.group(1) or m.group(2)) if m else None
                tot=re.search(r'total',u)
                print(f"  {name:18} {r.status if r else '?'}  cgid={cgid}  {url[-40:]}")
            except Exception as e:
                print(f"  {name:18} ERROR {str(e)[:50]}")
        b.close()

if __name__=="__main__":
    main()
