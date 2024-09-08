import sys
import os
import json
import firebase_admin
from firebase_admin import credentials, firestore
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from bs4 import BeautifulSoup
from googletrans import Translator
from datetime import datetime
import time
import re

# 표준 출력 인코딩을 UTF-8로 변경
sys.stdout.reconfigure(encoding='utf-8')

# Firebase Admin SDK 초기화 (GitHub Actions에서 환경 변수로 자격증명 설정)
firebase_credential_json = os.environ.get("FIREBASE_SERVICE_ACCOUNT_KEY")
if firebase_credential_json:
    cred_dict = json.loads(firebase_credential_json)
    cred = credentials.Certificate(cred_dict)
    firebase_admin.initialize_app(cred)
else:
    raise ValueError("Firebase 자격증명 정보가 설정되지 않았습니다.")

# Firestore 클라이언트 초기화
db = firestore.client()

# Translator 객체 생성
translator = Translator()

# ChromeDriver 자동 설치 및 경로 설정
service = Service(ChromeDriverManager().install())
options = webdriver.ChromeOptions()
options.add_argument('--headless')  # 서버 환경에서는 headless 모드 사용
options.add_argument('--no-sandbox')
options.add_argument('--disable-dev-shm-usage')
driver = webdriver.Chrome(service=service, options=options)

# 기본 URL 설정
base_url = 'https://www.cnnvd.org.cn/home/warn'
driver.get(base_url)

def split_text(text, max_length):
    #중국어 텍스트를 최대 길이로 쪼개는 함수
    current_length = 0
    chunks = []
    current_chunk = []

    for char in text:
        current_length += 1
        current_chunk.append(char)
        if current_length >= max_length:
            chunks.append(''.join(current_chunk))
            current_chunk = []
            current_length = 0

    if current_chunk:
        chunks.append(''.join(current_chunk))

    return chunks

def translate_text(text, src_lang='zh-cn', dest_lang='ko'):
    #텍스트를 쪼개서 번역하고 결과를 합치는 함수
    max_chunk_size = 500
    chunks = split_text(text, max_chunk_size)
    translated_chunks = []

    for chunk in chunks:
        try:
            if chunk.strip():
                translated_chunk = translator.translate(chunk, src=src_lang, dest=dest_lang).text
                translated_chunks.append(translated_chunk)
        except Exception as e:
            print(f"번역 중 오류 발생: {e}")
            translated_chunks.append("[번역 실패]")

    return ' '.join(translated_chunks)

def click_element(driver, by, value, max_attempts=5):
    #지정한 요소를 클릭하고, 실패 시 재시도
    for attempt in range(max_attempts):
        try:
            element = WebDriverWait(driver, 30).until(
                EC.element_to_be_clickable((by, value))
            )
            driver.execute_script("arguments[0].click();", element)
            return
        except Exception as e:
            print(f"클릭 시도 {attempt + 1}/{max_attempts} 실패: {e}")
            time.sleep(2)
    raise Exception("지정한 요소를 클릭하는 데 실패했습니다.")

def extract_content(soup):
    #본문에서 MsoTableGrid가 있는 부분을 서브 콘텐츠로, 나머지는 메인 콘텐츠로 추출
    main_content = []
    sub_content = []

    for element in soup.select('.detail-content > *'):
        if 'MsoTableGrid' in element.get('class', []):
            sub_content.append(element.get_text(strip=True))
        else:
            main_content.append(element.get_text(strip=True))

    return '\n'.join(main_content), '\n'.join(sub_content)

def extract_release_date(subtitle):
    #Subtitle에서 날짜를 추출하는 함수
    date_pattern = r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}"
    match = re.search(date_pattern, subtitle)
    if match:
        return datetime.strptime(match.group(), "%Y-%m-%d %H:%M:%S")
    return None

def is_duplicate(db, title):
    #Firestore에 이미 존재하는 제목인지 확인(Reports컬렉션의 데이터와 중복여부검사)
    docs = db.collection('Reports2').where('Title', '==', title).get()
    return len(docs) > 0

# 총 12페이지 데이터를 가져옴
for page in range(1):
    print(f"Processing page {page + 1}...")
    try:
        time.sleep(3)
        WebDriverWait(driver, 20).until(
            EC.invisibility_of_element_located((By.CLASS_NAME, 'el-loading-mask'))
        )

        content_links = WebDriverWait(driver, 30).until(
            EC.presence_of_all_elements_located((By.CLASS_NAME, 'content-title'))
        )
        print(f"Found {len(content_links)} content links on page {page + 1}.")

        for i in range(len(content_links)):
            print(f"Processing content {i + 1} on page {page + 1}...")
            try:
                content_links = driver.find_elements(By.CLASS_NAME, 'content-title')
                if i >= len(content_links):
                    print(f"인덱스 오류: 요소를 찾을 수 없음.")
                    continue

                driver.execute_script("arguments[0].click();", content_links[i])

                WebDriverWait(driver, 30).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, '.detail-info.el-col.el-col-16'))
                )

                html = driver.page_source
                soup = BeautifulSoup(html, 'html.parser')

                main_title_elem = soup.select_one('.detail-title')
                sub_title_elem = soup.select_one('.detail-subtitle')
                main_title = main_title_elem.get_text(strip=True) if main_title_elem else "제목 없음"
                sub_title = sub_title_elem.get_text(strip=True) if sub_title_elem else "서브 제목 없음"
                main_content, sub_content = extract_content(soup)

                translated_main_title = translate_text(main_title)
                translated_sub_title = translate_text(sub_title)
                translated_main_content = translate_text(main_content)
                translated_sub_content = translate_text(sub_content)

                crawling_time = datetime.now()
                release_date = extract_release_date(sub_title)

                report_data = {
                    "Title": translated_main_title,
                    "Subtitle": translated_sub_title,
                    "CrawlingDate": crawling_time,
                    "ReleaseDate": release_date,
                    "viewCount": 0,
                    "MainContent": translated_main_content,
                    "SubContent": translated_sub_content
                }

                if not is_duplicate(db, translated_main_title):
                    db.collection('Reports3').add(report_data)
                    print(f"{translated_main_title} 저장 완료.")
                else:
                    print(f"{translated_main_title}은 이미 저장되어 있습니다.")

                click_element(driver, By.CLASS_NAME, 'el-page-header__left')

            except Exception as e:
                print("본문 크롤링 중 오류 발생:", str(e))
                continue

        try:
            WebDriverWait(driver, 20).until(
                EC.invisibility_of_element_located((By.CLASS_NAME, 'el-loading-mask'))
            )
            click_element(driver, By.CLASS_NAME, 'el-icon-arrow-right')
            WebDriverWait(driver, 30).until(
                EC.presence_of_element_located((By.CLASS_NAME, 'content-title'))
            )
            time.sleep(3)

        except Exception as e:
            print("다음 페이지로 이동 중 오류 발생:", str(e))
            break

    except Exception as e:
        print("페이지 로드 중 오류 발생:", str(e))
        break

driver.quit()
