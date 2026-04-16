import os
import urllib.request
import time
import ssl

# SSL 인증서 오류 방지
ssl._create_default_https_context = ssl._create_unverified_context

foods = [
    # 기존 45종
    {"file":"01_김치.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/4/44/Korean_cuisine-Gimchi-01.jpg"},
    {"file":"02_비빔밥.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/4/44/Dolsot-bibimbap.jpg"},
    {"file":"03_불고기.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/0/0a/Korean_cuisine-Bulgogi-01.jpg"},
    {"file":"04_김밥.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/7/7e/Gimbap_%28pixabay%29.jpg"},
    {"file":"05_떡볶이.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/4/4d/Tteok-bokki.jpg"},
    {"file":"06_된장찌개.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/4/44/Korean_cuisine-Doenjang_jjigae-01.jpg"},
    {"file":"07_삼겹살.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/5/59/Korean_barbeque-Samgyeopsal-01.jpg"},
    {"file":"08_잡채.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/5/54/Korean_cuisine-Japchae-01.jpg"},
    {"file":"09_순두부찌개.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/3/31/Korean_cuisine-Sundubu_jjigae-01.jpg"},
    {"file":"10_갈비탕.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/9/9e/Korean_cuisine-Galbitang-01.jpg"},
    {"file":"11_냉면.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/a/ac/Korean_cuisine-Mul_naengmyeon-01.jpg"},
    {"file":"12_김치찌개.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/3/34/Korean_cuisine-Kimchi_jjigae-01.jpg"},
    {"file":"13_제육볶음.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/1/12/Korean_cuisine-Jeyuk_bokkeum-01.jpg"},
    {"file":"14_파전.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/e/e3/Korean_cuisine-Pajeon-01.jpg"},
    {"file":"15_감자탕.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/5/56/Gamjatang.jpg"},
    {"file":"16_칼국수.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/0/0c/Kalguksu.jpg"},
    {"file":"17_삼계탕.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/f/f8/Korean_cuisine-Samgyetang-01.jpg"},
    {"file":"18_족발.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/9/96/Korean_cuisine-Jokbal-01.jpg"},
    {"file":"19_라면.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/b/bc/Ramyeon.jpg"},
    {"file":"20_닭갈비.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/1/1a/Dakgalbi.jpg"},
    {"file":"21_볶음밥.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/2/2d/Bokkeumbap.jpg"},
    {"file":"22_만두.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/4/4b/Korean_cuisine-Mandu-01.jpg"},
    {"file":"23_육회.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/8/89/Korean_cuisine-Yukhoe-01.jpg"},
    {"file":"24_떡국.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/a/a2/Tteokguk.jpg"},
    {"file":"25_순대.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/6/69/Korean_blood_sausage-Sundae-01.jpg"},
    {"file":"26_치킨.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/5/5a/Korean_fried_chicken_%28cropped%29.jpg"},
    {"file":"27_해물탕.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/d/d2/Korean_cuisine-Haemultang-01.jpg"},
    {"file":"28_오징어볶음.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/0/0e/Korean_cuisine-Ojingeo_bokkeum-01.jpg"},
    {"file":"29_떡.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/3/3d/Korean_rice_cake-Tteok-13.jpg"},
    {"file":"30_김치볶음밥.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/8/81/Kimchi_bokkeumbap.jpg"},
    {"file":"31_닭가슴살.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/a/ae/Grilled_chicken_breast_%281%29.jpg"},
    {"file":"32_샐러드.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/9/94/Salad_platter.jpg"},
    {"file":"33_고구마.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/5/5e/GunGoguma.jpg"},
    {"file":"34_계란.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/4/44/Boiled_eggs.jpg"},
    {"file":"35_현미밥.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/f/fa/Brown_rice.jpg"},
    {"file":"36_오트밀.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/d/d2/Porridge.jpg"},
    {"file":"37_연어.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/3/39/SalmonSashimi.jpg"},
    {"file":"38_아보카도.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/6/6d/Good_Food_Display_-_NCI_Visuals_Online.jpg"},
    {"file":"39_두부.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/0/03/Korean_cuisine-Dubu-01.jpg"},
    {"file":"40_바나나.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/8/8a/Banana-Single.jpg"},
    {"file":"41_프로틴쉐이크.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/6/6d/Protein_shake.jpg"},
    {"file":"42_그릭요거트.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/e/ea/Turkish_strained_yogurt.jpg"},
    {"file":"43_스테이크.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/4/4c/Filet_Mignon_Beef_Steak.jpg"},
    {"file":"44_브로콜리.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/0/03/Broccoli_and_cross_section_edit.jpg"},
    {"file":"45_견과류.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/1/10/Mixed_nuts_small_pile.jpg"},

    # 신규 55종
    {"file":"46_짜장면.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/2/29/Jajangmyeon_by_stu_spivack.jpg"},
    {"file":"47_짬뽕.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/0/07/Jjamppong_by_Ryu_in_Jeongseon.jpg"},
    {"file":"48_탕수육.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/6/6d/Tangsuyuk.jpg"},
    {"file":"49_마라탕.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/0/01/Malatang_in_Beijing.jpg"},
    {"file":"50_햄버거.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/4/47/Hamburger_%28black_bg%29.jpg"},
    {"file":"51_피자.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/a/a3/Eq_it-na_pizza-margherita_sep2005_sml.jpg"},
    {"file":"52_파스타.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/e/e0/Spaghetti_Bolognese_mit_Parmesan_oder_Grana_Padano.jpg"},
    {"file":"53_초밥.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/9/90/Sushi_platter.jpg"},
    {"file":"54_돈까스.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/6/6f/Tonkatsu_by_yosshi.jpg"},
    {"file":"55_쌀국수.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/5/53/Pho_Bo.jpg"},
    {"file":"56_카레.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/a/a8/Curry_Rice_by_ayustety.jpg"},
    {"file":"57_우동.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/f/fa/Kitsune_udon_by_wallyg.jpg"},
    {"file":"58_리조또.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/6/6e/Risotto_alla_milanese.jpg"},
    {"file":"59_타코.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/7/73/001_Tacos_de_carnitas%2C_carne_asada_y_al_pastor.jpg"},
    {"file":"60_샌드위치.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/5/50/Blt-sandwich.jpg"},
    {"file":"61_베이글.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/1/14/Bagel_with_cream_cheese.jpg"},
    {"file":"62_크로와상.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/2/28/Croissant.jpg"},
    {"file":"63_핫도그.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/b/b1/Hot_dog_with_mustard.png"},
    {"file":"64_감자튀김.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/6/67/Fries_2.jpg"},
    {"file":"65_단백질바.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/f/ff/Clif_Bar.jpg"},
    {"file":"66_단호박.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/f/f6/Kabocha_squash.jpg"},
    {"file":"67_방울토마토.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/6/68/Cherry_tomatoes.jpg"},
    {"file":"68_사과.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/1/15/Red_Apple.jpg"},
    {"file":"69_오렌지.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/c/c4/Orange-Fruit-Pieces.jpg"},
    {"file":"70_블루베리.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/1/15/Blueberries.jpg"},
    {"file":"71_키위.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/b/b8/Kiwi_%28Actinidia_chinensis%29_1_Luc_Viatour.jpg"},
    {"file":"72_딸기.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/2/29/PerfectStrawberry.jpg"},
    {"file":"73_파프리카.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/8/87/Red_capsicum_and_cross_section.jpg"},
    {"file":"74_버섯.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/2/24/Champignon_mushroom.jpg"},
    {"file":"75_보쌈.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/8/8a/Korean_cuisine-Bossam-01.jpg"},
    {"file":"76_수육.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/9/98/Suyuk.jpg"},
    {"file":"77_마카롱.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/a/a3/Macarons_in_Paris.jpg"},
    {"file":"78_아이스크림.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/3/31/Ice_Cream_dessert_02.jpg"},
    {"file":"79_와플.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/5/5b/Waffles_with_Strawberries.jpg"},
    {"file":"80_팬케이크.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/4/43/Blueberry_pancakes_%281%29.jpg"},
    {"file":"81_김치전.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/2/22/Kimchijeon.jpg"},
    {"file":"82_미역국.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/6/66/Korean_cuisine-Miyeokguk-01.jpg"},
    {"file":"83_콩나물국.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/8/8b/Kongnamul-guk.jpg"},
    {"file":"84_잔치국수.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/9/91/Janchi_guksu.jpg"},
    {"file":"85_비빔냉면.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/0/05/Bibim_naengmyeon.jpg"},
    {"file":"86_멸치볶음.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/5/5a/Myeolchi_bokkeum.jpg"},
    {"file":"87_두부조림.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/7/77/Dubu-jorim.jpg"},
    {"file":"88_감자조림.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/3/3b/Gamja-jorim.jpg"},
    {"file":"89_계란찜.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/d/df/Gyeran_jjim.jpg"},
    {"file":"90_깍두기.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/f/fb/Kkakdugi.jpg"},
    {"file":"91_아메리카노.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/0/09/Iced_Americano.jpg"},
    {"file":"92_카페라떼.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/c/c6/Latte_art_3.jpg"},
    {"file":"93_녹차.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/e/e0/Green_tea_in_a_glass_cup.jpg"},
    {"file":"94_콜라.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/e/e8/Coca-Cola_glass_with_ice.jpg"},
    {"file":"95_사이다.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/4/46/Sprite_Glass.jpg"},
    {"file":"96_과일주스.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/0/05/Orange_juice_1_edit1.jpg"},
    {"file":"97_초콜릿.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/f/f2/Chocolate.jpg"},
    {"file":"98_쿠키.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/f/f1/2ChocolateChipCookies.jpg"},
    {"file":"99_호떡.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/a/ae/Hotteok_2.jpg"},
    {"file":"100_붕어빵.jpg", "url":"https://upload.wikimedia.org/wikipedia/commons/6/6f/Bungeoppang.jpg"}
]

# 저장 폴더 생성 (.tmp/test_images/)
save_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".tmp", "test_images")
os.makedirs(save_dir, exist_ok=True)

# 💡 핵심 수정: 위키미디어 공식 봇 정책에 맞춘 명확한 User-Agent 사용
headers = {
    'User-Agent': 'NutriLensDataTester/1.0 (Research Purpose; contact: nutrilens@test.com)'
}

success_cnt = 0
fail_cnt = 0

print(f"다운로드 시작! 총 100장의 이미지를 {save_dir}에 저장합니다.\n")

for i, food in enumerate(foods, 1):
    save_path = os.path.join(save_dir, food['file'])
    
    # 이미 파일이 있으면 건너뛰기
    if os.path.exists(save_path) and os.path.getsize(save_path) > 0:
        print(f"[{i}/100] 이미 존재함: {food['file']}")
        success_cnt += 1
        continue
        
    try:
        req = urllib.request.Request(food['url'], headers=headers)
        with urllib.request.urlopen(req) as response, open(save_path, 'wb') as out_file:
            data = response.read()
            out_file.write(data)
        print(f"[{i}/100] 다운로드 성공: {food['file']}")
        success_cnt += 1
        
        # 💡 핵심 수정: 서버 부하 방지를 위해 대기 시간을 1.5초로 넉넉하게 연장
        time.sleep(1.5) 
        
    except Exception as e:
        print(f"[{i}/100] 다운로드 실패: {food['file']} (에러: {e})")
        fail_cnt += 1

print(f"\n완료! 성공: {success_cnt}장, 실패: {fail_cnt}장")