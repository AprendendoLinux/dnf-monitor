import glob, os
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch()
    for path in sorted(glob.glob("/work/out/*.html")):
        name = os.path.basename(path)[:-5]
        for label, width in (("desktop", 760), ("mobile", 390)):
            page = b.new_page(viewport={"width": width, "height": 800}, device_scale_factor=1)
            page.goto("file://" + path)
            page.screenshot(path="/work/out/%s.%s.png" % (name, label), full_page=True)
            page.close()
    b.close()
print("ok")
