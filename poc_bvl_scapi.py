"""摸宝格丽 SCAPI 商品接口（参数/鉴权/翻页/响应）+ 各品类 cgid。临时脚本。"""
import re, json
from playwright.sync_api import sync_playwright
UA=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36")
HEAD={"accept":"*/*","accept-language":"en-US,en;q=0.9","sec-ch-ua":'"Chromium";v="145", "Not(A:Brand";v="24", "Google Chrome";v="145"',"sec-ch-ua-mobile":"?0","sec-ch-ua-platform":'"Windows"'}
STEALTH=("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});window.chrome={runtime:{}};")

def main():
    with sync_playwright() as p:
        b=p.chromium.launch(headless=True,args=["--disable-blink-features=AutomationControlled"])
        c=b.new_context(user_agent=UA,locale="en-us",viewport={"width":1440,"height":1200},extra_http_headers=HEAD); c.add_init_script(STEALTH); pg=c.new_page()
        reqs=[]   # (url, headers)
        resp=[]   # (url, json)
        def onreq(r):
            if "product-search" in r.url:
                reqs.append((r.url, r.headers))
        def onresp(r):
            if "product-search" in r.url:
                try:
                    if "json" in (r.headers.get("content-type","")): resp.append((r.url, r.json()))
                except: pass
        pg.on("request",onreq); pg.on("response",onresp)
        pg.goto("https://www.bulgari.com/en-us/jewelry/rings",wait_until="domcontentloaded",timeout=60000); pg.wait_for_timeout(5000)

        print("=== product-search 请求 ===")
        if reqs:
            u,h=reqs[0]
            print("URL:",u[:240])
            print("有Authorization头:", "authorization" in {k.lower() for k in h})
            auth=next((v for k,v in h.items() if k.lower()=="authorization"),None)
        else:
            auth=None
            print("无 product-search 请求")
        print("\n=== product-search 响应结构 ===")
        if resp:
            u,j=resp[0]
            print("顶层键:",list(j.keys()))
            print("total/count:",j.get("total"),j.get("count"),"limit:",j.get("limit"),"offset:",j.get("offset"))
            hh=j.get("hits") or []
            if hh:
                print("hit[0] 键:",list(hh[0].keys()))
                print("hit[0]:",json.dumps(hh[0],ensure_ascii=False)[:500])

        # 用捕获的 Authorization 头，重放接口拿全量（limit=200）测试翻页
        if reqs and auth:
            base=reqs[0][0].split("?")[0]
            qs=reqs[0][0].split("?")[1] if "?" in reqs[0][0] else ""
            params=dict(re.findall(r"([^=&]+)=([^&]*)",qs))
            cgid=params.get("refine") or params.get("refine_1") or ""
            print("\n原始 query 参数键:",list(params.keys()))
            test=pg.evaluate("""async (args)=>{
                const [base,auth]=args;
                const u=base+"?siteId=bulgari&refine=cgid%3D241472&limit=200&offset=0";
                try{const r=await fetch(u,{headers:{'Authorization':auth}});const j=await r.json();
                  return {status:r.status, total:j.total, n:(j.hits||[]).length, firstPrice:(j.hits&&j.hits[0]||{}).price, cur:j.currency||(j.hits&&j.hits[0]||{}).currency};
                }catch(e){return {err:String(e).slice(0,80)}}
            }""",[base,auth])
            print("重放(limit=200) ->",test)
        b.close()

if __name__=="__main__":
    main()
