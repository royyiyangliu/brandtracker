"""探 bulgari.cn (Magento) 戒指品类/商品接口/价格/货号。临时脚本。"""
import re, json
from playwright.sync_api import sync_playwright
UA=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36")
HEAD={"accept":"text/html,*/*;q=0.8","accept-language":"zh-CN,zh;q=0.9","sec-ch-ua":'"Chromium";v="145", "Not(A:Brand";v="24", "Google Chrome";v="145"',"sec-ch-ua-mobile":"?0","sec-ch-ua-platform":'"Windows"'}
STEALTH=("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});window.chrome={runtime:{}};")

def main():
    with sync_playwright() as p:
        b=p.chromium.launch(headless=True,args=["--disable-blink-features=AutomationControlled"])
        c=b.new_context(user_agent=UA,locale="zh-cn",viewport={"width":1440,"height":1200},extra_http_headers=HEAD); c.add_init_script(STEALTH); pg=c.new_page()
        api=[]
        def onresp(r):
            if re.search(r"/rest/.*/(products|catalog|search)",r.url,re.I):
                try:
                    if "json" in (r.headers.get("content-type","")): api.append((r.url, r.json()))
                except: pass
        pg.on("response",onresp)
        # 试 by-category 戒指页（猜 URL）+ 从首页导航找
        for url in ["https://www.bulgari.cn/zh-cn/categories/jewelry/by-category/rings",
                    "https://www.bulgari.cn/zh-cn/categories/jewelry/rings"]:
            try:
                r=pg.goto(url,wait_until="domcontentloaded",timeout=60000); pg.wait_for_timeout(4000)
                pg.mouse.wheel(0,3000); pg.wait_for_timeout(2000)
                d=pg.evaluate("""()=>{
                    const hrefs=[...new Set([...document.querySelectorAll('a[href]')].map(e=>e.getAttribute('href')||''))];
                    const prod=hrefs.filter(h=>/\\/products?\\/|\\.html/.test(h)&&/[A-Z]{2}\\d{4,}/.test(h)).slice(0,5);
                    const txt=document.body.innerText||'';
                    const price=(txt.match(/[¥￥]\\s?[\\d,]{3,}/g)||[]).slice(0,3);
                    const tot=(txt.match(/\\d+\\s*(款|件|个|results?)/)||[])[0]||null;
                    return {final:location.pathname, status:document.title.slice(0,30), nProd:prod.length, prod, price, tot};
                }""")
                print(f"[CN] {r.status if r else '?'} {url[-40:]}")
                print(f"     final={d['final']} 商品{d['nProd']} {d['prod']} 价格={d['price']} 总量={d['tot']}")
            except Exception as e:
                print(f"[CN] {url[-30:]} ERROR {str(e)[:50]}")
        print("\n=== Magento catalog/product API 命中 ===")
        for u,j in api[:5]:
            keys=list(j.keys()) if isinstance(j,dict) else type(j).__name__
            print("  ",u[:110]," keys=",keys[:8] if isinstance(keys,list) else keys)
            s=json.dumps(j,ensure_ascii=False)
            pm=re.search(r'"(sku|price|final_price|name)"\s*:\s*("?[\w一-鿿.,]+)',s)
            print("     片段:",s[:300])
        b.close()

if __name__=="__main__":
    main()
