"""用 categories API 拿宝格丽珠宝类目树(权威 cgid)。临时脚本。"""
import re, json
from playwright.sync_api import sync_playwright
UA=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36")
HEAD={"accept":"*/*","accept-language":"en-US,en;q=0.9","sec-ch-ua":'"Chromium";v="145", "Not(A:Brand";v="24", "Google Chrome";v="145"',"sec-ch-ua-mobile":"?0","sec-ch-ua-platform":'"Windows"'}
STEALTH=("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});window.chrome={runtime:{}};")
ORG="f_ecom_bcsg_prd"

def main():
    with sync_playwright() as p:
        b=p.chromium.launch(headless=True,args=["--disable-blink-features=AutomationControlled"])
        c=b.new_context(user_agent=UA,locale="en-us",viewport={"width":1440,"height":1000},extra_http_headers=HEAD); c.add_init_script(STEALTH); pg=c.new_page()
        auth={"v":None}
        pg.on("request",lambda r:auth.__setitem__("v",next((v for k,v in r.headers.items() if k.lower()=="authorization"),auth["v"])) if "/mobify/proxy/api" in r.url and not auth["v"] else None)
        pg.goto("https://www.bulgari.com/en-us/jewelry/rings",wait_until="domcontentloaded",timeout=60000); pg.wait_for_timeout(5000)
        print("captured auth:", bool(auth["v"]))
        # 拉类目树：试 root 与 几个候选
        for root in ["root","241472","241484"]:
            u=f"https://www.bulgari.com/mobify/proxy/api/product/shopper-products/v1/organizations/{ORG}/categories/{root}?levels=2&siteId=US&locale=en-US"
            res=pg.evaluate("""async (args)=>{const[u,a]=args;try{const r=await fetch(u,{headers:{'Authorization':a}});const j=await r.json();
                const simp=(n)=>({id:n.id,name:n.name,kids:(n.categories||[]).map(k=>({id:k.id,name:k.name,kids:(k.categories||[]).map(x=>({id:x.id,name:x.name}))}))});
                return {status:r.status, tree: j.id?simp(j):j};
            }catch(e){return {err:String(e).slice(0,80)}}}""",[u,auth["v"]])
            print(f"\n=== categories/{root} ===")
            print(json.dumps(res,ensure_ascii=False)[:1500])
        b.close()

if __name__=="__main__":
    main()
