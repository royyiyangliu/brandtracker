"""可靠抓各品类 cgid：记录每页所有 product-search 的 cgid+total。临时脚本。"""
import re, json
from playwright.sync_api import sync_playwright
UA=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36")
HEAD={"accept":"*/*","accept-language":"en-US,en;q=0.9","sec-ch-ua":'"Chromium";v="145", "Not(A:Brand";v="24", "Google Chrome";v="145"',"sec-ch-ua-mobile":"?0","sec-ch-ua-platform":'"Windows"'}
STEALTH=("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});window.chrome={runtime:{}};")
PAGES=[("rings","https://www.bulgari.com/en-us/jewelry/rings"),
       ("necklaces","https://www.bulgari.com/en-us/jewelry/necklaces"),
       ("bracelets","https://www.bulgari.com/en-us/jewelry/bracelets"),
       ("earrings","https://www.bulgari.com/en-us/jewelry/earrings"),
       ("engagement-rings","https://www.bulgari.com/en-us/engagement-and-wedding/engagement-rings"),
       ("wedding-bands","https://www.bulgari.com/en-us/engagement-and-wedding/wedding-bands")]

def main():
    with sync_playwright() as p:
        b=p.chromium.launch(headless=True,args=["--disable-blink-features=AutomationControlled"])
        c=b.new_context(user_agent=UA,locale="en-us",viewport={"width":1440,"height":1000},extra_http_headers=HEAD); c.add_init_script(STEALTH); pg=c.new_page()
        seen=[]
        def onresp(r):
            if "product-search" in r.url:
                m=re.search(r"cgid%3D(\d+)",r.url)
                try: j=r.json()
                except: j={}
                seen.append((m.group(1) if m else None, j.get("total"), r.url))
        pg.on("response",onresp)
        for name,url in PAGES:
            seen.clear()
            try:
                r=pg.goto(url,wait_until="domcontentloaded",timeout=60000); pg.wait_for_timeout(4500)
                # 主列表 = total 最大或第一个；都列出
                uniq={}
                for cgid,tot,u in seen:
                    if cgid and cgid not in uniq: uniq[cgid]=tot
                print(f"  {name:18} {r.status if r else '?'}  cgid->total: {uniq}")
            except Exception as e:
                print(f"  {name:18} ERROR {str(e)[:50]}")
        b.close()

if __name__=="__main__":
    main()
