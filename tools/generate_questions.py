#!/usr/bin/env python3
"""Generate 1,200 deterministic MCQ records in each category, with no answer leaks in web assets.
Dependencies: pycountry, countryinfo, babel and RDKit (only needed to REGENERATE the checked-in JSON).
Game/seed operations need Node.js only; regenerate with `python tools/generate_questions.py`.
Fact pools are offline reference data; human editorial review is recommended before public quiz release.
"""
from __future__ import annotations
import json, random, re
from collections import Counter
from pathlib import Path
from countryinfo import CountryInfo
from babel import Locale
from rdkit.Chem import GetPeriodicTable

ROOT = Path(__file__).resolve().parent.parent
rng = random.Random(20261111)
rows = []
seen = set()
LIMIT=1200

def add(cat, prompt, correct, wrongs, explanation='', unique_hint=''):
    prompt=' '.join(str(prompt).split())
    key=(cat,prompt,unique_hint)
    if key in seen: return
    correct=str(correct)
    wrongs=list(dict.fromkeys(str(x) for x in wrongs if str(x)!=correct))
    if len(wrongs)<3: return
    options = [correct] + rng.sample(wrongs,3)
    rng.shuffle(options)
    rows.append(dict(id=f'{cat}-{sum(x["category"]==cat for x in rows)+1:04d}',category=cat,prompt=prompt,options=options,correctIndex=options.index(correct),explanation=explanation))
    seen.add(key)

# ===== SCIENCE =====
pt = GetPeriodicTable()
elements=[(pt.GetElementName(z).capitalize(),pt.GetElementSymbol(z),z) for z in range(1,119)]
for name,symbol,z in elements:
    other_symbol=[x[1] for x in elements if x[1]!=symbol]
    other_element=[x[0] for x in elements if x[0]!=name]
    add('science',f'원자번호 {z}번 원소의 원소기호는 무엇일까요?',symbol,other_symbol)
    add('science',f'원소기호 {symbol}의 원소 이름은 무엇인가요?',name,other_element)
    add('science',f'전기적으로 중성인 원자번호 {z}번 원소의 전자 수는?',z,[z+1,z+2,max(0,z-1),max(0,z-2),z+3])
    add('science',f'{name} 원자의 원자핵에 있는 양성자 수는?',z,[z+1,z+2,max(0,z-1),max(0,z-2),z+3])
formula_templates=[
('일정한 속력 {a} m/s로 {b}초 이동했다. 이동 거리는?',lambda a,b:a*b,'m'),
('질량 {a} kg인 물체의 가속도가 {b} m/s²일 때 알짜힘은?',lambda a,b:a*b,'N'),
('전류 {a} A가 저항 {b} Ω을 흐른다. 옴의 법칙에 따른 전압은?',lambda a,b:a*b,'V'),
('전력 {a} W 기기를 {b}시간 사용했다. 소비 전력량은?',lambda a,b:a*b,'Wh'),
('밀도 {a} kg/m³인 물질의 부피가 {b} m³이다. 질량은?',lambda a,b:a*b,'kg'),
('압력 {a} Pa가 면적 {b} m²에 균일하게 작용한다. 힘은?',lambda a,b:a*b,'N'),
('질량 {a} kg의 물체에 중력가속도 10 m/s²를 적용한다. 무게는?',lambda a,b:a*10,'N'),
('주파수 {a} Hz인 파동이 {b}초 동안 만든 진동 횟수는?',lambda a,b:a*b,'회'),
('길이가 {a} m인 정사각형 2개를 나란히 붙인 면적은?',lambda a,b:2*a*a,'m²'),
('한 변이 {a} cm인 정육면체의 부피는?',lambda a,b:a**3,'cm³'),
]
for k in range(1050):
    t=k%len(formula_templates)
    fmt,fn,unit=formula_templates[t]
    a=rng.randint(2,29); b=rng.randint(2,19)
    val=fn(a,b)
    options=[f'{x} {unit}' for x in (val+1,val+2,max(0,val-1),val+10,val*2,max(0,val//2),val+5) if x!=val]
    add('science',fmt.format(a=a,b=b),f'{val} {unit}',options)
    if sum(x['category']=='science' for x in rows)>=LIMIT:break

# ===== ENGLISH =====
# Common vocabulary with unambiguous primary Korean glosses; editorially maintained pairs.
VOCAB="""
ability|능력;absence|부재;accident|사고;achievement|성취;activity|활동;advantage|이점;advice|조언;agreement|합의;agriculture|농업;ambition|야망;analysis|분석;ancestor|조상;announcement|발표;anxiety|불안;apology|사과;appearance|외모;application|신청;appointment|약속;argument|논쟁;arrangement|배치;arrival|도착;assistant|조수;atmosphere|대기;attempt|시도;attention|주의;attitude|태도;audience|청중;authority|권한;awareness|인식;background|배경;balance|균형;behavior|행동;benefit|혜택;biology|생물학;boundary|경계;budget|예산;campaign|운동;candidate|후보자;capacity|용량;capital|수도;category|범주;celebration|축하;challenge|도전;character|성격;charity|자선;chemistry|화학;citizen|시민;climate|기후;colleague|동료;collection|수집;combination|조합;commerce|상업;communication|의사소통;community|공동체;comparison|비교;competition|경쟁;complaint|불평;concentration|집중;conclusion|결론;confidence|자신감;conflict|갈등;consequence|결과;consideration|고려;construction|건설;consumer|소비자;contribution|기여;conversation|대화;cooperation|협력;courage|용기;creativity|창의성;criterion|기준;culture|문화;customer|고객;deadline|마감일;decision|결정;definition|정의;delivery|배달;democracy|민주주의;departure|출발;description|설명;destination|목적지;development|발전;difference|차이;difficulty|어려움;direction|방향;disability|장애;disaster|재난;discovery|발견;discussion|토론;distribution|분배;diversity|다양성;document|문서;economy|경제;education|교육;efficiency|효율성;electricity|전기;emergency|비상사태;emotion|감정;employee|직원;employer|고용주;employment|고용;encouragement|격려;energy|에너지;engineer|기술자;environment|환경;equipment|장비;estimate|추정;ethics|윤리;evidence|증거;exception|예외;exchange|교환;experience|경험;experiment|실험;explanation|해명;exploration|탐험;expression|표현;facility|시설;failure|실패;familiarity|친숙함;feature|특징;feedback|의견;flexibility|유연성;foundation|기초;freedom|자유;frequency|빈도;friendship|우정;function|기능;generation|세대;geography|지리학;government|정부;graduation|졸업;gratitude|감사;guidance|안내;habit|습관;harvest|수확;health|건강;heritage|유산;horizon|지평선;hospitality|환대;humanity|인류;identity|정체성;imagination|상상력;improvement|개선;independence|독립;industry|산업;influence|영향;information|정보;innovation|혁신;inspiration|영감;instruction|지시;insurance|보험;intention|의도;interaction|상호작용;interest|관심;interpretation|해석;interview|면접;introduction|소개;invention|발명;investment|투자;invitation|초대;investigation|조사;justice|정의;knowledge|지식;landscape|풍경;language|언어;leadership|지도력;legislation|입법;liberty|자유권;literature|문학;location|위치;loyalty|충성;maintenance|유지보수;management|관리;manufacturing|제조;measurement|측정;medication|약물;membership|회원자격;memory|기억;message|메시지;method|방법;migration|이주;mission|임무;motivation|동기;negotiation|협상;neighborhood|이웃;network|네트워크;nutrition|영양;observation|관찰;obstacle|장애물;occupation|직업;opportunity|기회;organization|조직;orientation|방향설정;outcome|결과물;ownership|소유권;participation|참여;partnership|동반관계;patience|인내;performance|성과;permission|허가;personality|성격특성;perspective|관점;phenomenon|현상;philosophy|철학;population|인구;possibility|가능성;preparation|준비;presentation|발표자료;preservation|보존;priority|우선순위;procedure|절차;productivity|생산성;profession|전문직;progress|진전;promotion|승진;proposal|제안;protection|보호;qualification|자격;quality|품질;quantity|수량;reaction|반응;recognition|인정;recommendation|추천;recovery|회복;recruitment|채용;reduction|감소;reference|참고;reflection|반영;regulation|규정;relationship|관계;reliability|신뢰성;requirement|요구조건;research|연구;reservation|예약;resistance|저항;resolution|해결;resource|자원;responsibility|책임;restoration|복구;restriction|제한;retirement|은퇴;revenue|매출;revision|수정;satisfaction|만족;security|보안;selection|선택;sensitivity|민감도;sequence|순서;significance|중요성;simulation|모의실험;society|사회;solution|해결책;stability|안정성;standard|표준;statistics|통계;strategy|전략;structure|구조;substitution|대체;suggestion|제안사항;sustainability|지속가능성;technology|기술;temperature|온도;tendency|경향;theory|이론;tradition|전통;transaction|거래;transformation|변환;transition|전환;transportation|운송;uncertainty|불확실성;understanding|이해;unemployment|실업;uniqueness|독특함;universe|우주;vacation|휴가;validation|검증;variation|변동;velocity|속도;verification|확인;victory|승리;visibility|가시성;volunteer|자원봉사자;vulnerability|취약점;welfare|복지;wisdom|지혜;workforce|노동력;workplace|직장
"""
raw=[tuple(x.strip().split('|')) for x in VOCAB.split(';') if '|' in x]
pairs=list(dict(raw).items())
# Keep only pairs whose gloss uniquely identifies its English word for reverse questions.
gloss_counts=Counter(ko for _,ko in pairs)
all_ens=[en for en,ko in pairs]; all_kos=list(set(ko for en,ko in pairs))
for en,ko in pairs:
    wrong_en=[x for x in all_ens if x!=en]
    wrong_ko=[x for x in all_kos if x!=ko]
    add('english',f'영단어 “{en}”의 뜻으로 알맞은 것은?',ko,wrong_ko)
    add('english',f'“{en}”를 한국어로 가장 알맞게 옮기면?',ko,wrong_ko)
    if gloss_counts[ko]==1:
        add('english',f'“{ko}”에 해당하는 영어 단어는?',en,wrong_en)
    if sum(x['category']=='english' for x in rows)>=LIMIT:break
# Grammatical items for extra breadth when vocabulary count is below limit
grammars=[
 ('study','studies','studying','studied','English'),('watch','watches','watching','watched','TV'),
 ('fix','fixes','fixing','fixed','bicycles'),('teach','teaches','teaching','taught','math'),
 ('carry','carries','carrying','carried','a backpack'),('wash','washes','washing','washed','the dishes'),
 ('play','plays','playing','played','chess'),('cook','cooks','cooking','cooked','dinner'),
 ('write','writes','writing','wrote','a diary'),('practice','practices','practicing','practiced','piano'),
 ('visit','visits','visiting','visited','the library'),('clean','cleans','cleaning','cleaned','the office'),
 ('drink','drinks','drinking','drank','tea'),('enjoy','enjoys','enjoying','enjoyed','jazz'),
 ('use','uses','using','used','a computer'),('walk','walks','walking','walked','to school'),
]
for n in range(2000):
    if sum(x['category']=='english' for x in rows)>=LIMIT:break
    kind=n%5
    if kind==0:
        base,third,ing,past,obj=rng.choice(grammars)
        sub=rng.choice(['She','He','My brother','My sister','The teacher'])
        day=rng.choice(['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'])
        add('english',f'빈칸에 올바른 동사 형태를 고르세요: {sub} ___ {obj} every {day}.',third,[base,ing,past])
    elif kind==1:
        noun=rng.choice(['apple','orange','umbrella','elephant','idea','artist','engine','island','office','airport'])
        setting=rng.choice(['I imagined','She described','We mentioned','They discovered','He noticed'])
        add('english',f'처음 언급하는 단수 명사에 알맞은 관사는? {setting} ___ {noun}.', 'an', ['a','some','any','many'])
    elif kind==2:
        year=rng.randint(1990,2025)
        sub=rng.choice(['I have worked here','She has lived in Seoul','He has studied English','They have been friends'])
        add('english',f'현재완료 문장의 시간 표현: {sub} ___ {year}.','since',['for','during','until','by'])
    elif kind==3:
        period=rng.randint(2,45)
        unit=rng.choice(['days','months','years','weeks'])
        sub=rng.choice(['I have worked here','She has lived in Seoul','He has studied English','They have been friends'])
        add('english',f'현재완료 문장의 시간 표현: {sub} ___ {period} {unit}.','for',['since','until','by','at'])
    else:
        day=rng.choice(['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'])
        act=rng.choice(['The meeting is','The class starts','The flight leaves','The interview takes place','The event happens'])
        add('english',f'요일 앞에 올바른 전치사는? {act} ___ {day}.','on',['in','at','to','for'])

# ===== HISTORY =====
# Year-anchored core milestones, kept intentionally conservative (not necessarily exact day/month).
HISTORY="""
476|서로마 제국 멸망;527|유스티니아누스 1세 동로마 황제 즉위;570|이슬람 창시자 무함마드 출생;610|무함마드가 첫 계시를 받았다고 전해지는 시기;622|이슬람력의 기점인 히즈라;632|무함마드 사망;660|백제 멸망;668|고구려 멸망;676|신라의 삼국 통일 완성;698|발해 건국;711|이베리아반도 이슬람 정복 시작;751|탈라스 전투;800|카롤루스 대제 서로마 황제 대관;843|베르됭 조약;918|고려 건국;935|신라 멸망;936|고려 후삼국 통일;960|중국 송나라 건국;1054|동서 교회 대분열;1066|노르만인의 잉글랜드 정복;1096|제1차 십자군 전쟁 시작;1206|칭기즈 칸 몽골 제국 건국;1215|잉글랜드 마그나 카르타 승인;1231|몽골의 고려 침입 시작;1271|쿠빌라이 칸 원나라 국호 사용;1274|원나라의 제1차 일본 원정;1299|오스만 제국 건국 시기;1337|백년전쟁 시작;1347|유럽에서 흑사병 확산 시작;1368|명나라 건국;1377|직지심체요절 금속활자 인쇄;1388|위화도 회군;1392|조선 건국;1402|혼일강리역대국도지도 제작;1418|세종 즉위;1443|훈민정음 창제;1446|훈민정음 반포;1453|오스만 제국 콘스탄티노폴리스 정복;1492|콜럼버스의 첫 아메리카 항해;1498|바스쿠 다 가마 인도 항로 개척;1517|마르틴 루터 종교개혁 시작;1521|아스테카 제국 멸망;1534|영국 수장령 제정;1543|코페르니쿠스 천구의 회전에 관하여 출간;1555|아우크스부르크 화의;1582|그레고리력 도입;1588|잉글랜드가 스페인 무적함대 격퇴;1592|임진왜란 발발;1597|정유재란 시작;1598|임진왜란 종료;1600|영국 동인도회사 설립;1603|일본 에도 막부 시작;1607|제임스타운 식민지 건설;1618|30년 전쟁 시작;1620|메이플라워호 북아메리카 도착;1636|병자호란 발발;1642|영국 내전 시작;1644|청나라 베이징 입성;1648|베스트팔렌 조약;1660|영국 왕정복고;1687|뉴턴 프린키피아 출간;1688|영국 명예혁명;1707|그레이트브리튼 왕국 성립;1712|증기기관 뉴커먼 기관 최초 상용화;1756|7년 전쟁 시작;1762|루소 사회계약론 출간;1769|제임스 와트 증기기관 특허;1776|미국 독립선언;1783|파리 조약으로 미국 독립 승인;1787|미국 헌법 제정;1789|프랑스 혁명 시작;1796|제너 우두법 접종 실험;1804|나폴레옹 프랑스 황제 즉위;1815|워털루 전투;1821|그리스 독립전쟁 시작;1830|프랑스 7월 혁명;1839|아편전쟁 시작;1848|유럽 혁명과 공산당 선언 발표;1853|크림 전쟁 시작;1859|다윈 종의 기원 출간;1861|미국 남북전쟁 시작;1863|미국 노예해방 선언 발효;1865|미국 남북전쟁 종료;1868|일본 메이지 유신;1869|수에즈 운하 개통;1871|독일 제국 성립;1876|조선 강화도 조약 체결;1882|조선 임오군란 발생;1884|조선 갑신정변 발생;1885|파스퇴르 광견병 백신 사용;1894|동학농민운동과 청일전쟁 시작;1895|시모노세키 조약 체결;1896|제1회 근대 올림픽 개최;1897|대한제국 선포;1903|라이트 형제 동력 비행 성공;1904|러일전쟁 시작;1905|아인슈타인 특수상대성이론 발표;1906|샌프란시스코 대지진;1910|한일병합조약 체결;1912|타이타닉호 침몰;1914|제1차 세계대전 발발;1917|러시아 혁명;1918|제1차 세계대전 종전;1919|한국 3·1 운동;1920|국제연맹 출범;1922|소비에트 연방 성립;1923|튀르키예 공화국 수립;1927|린드버그 대서양 단독 무착륙 비행;1928|페니실린 발견;1929|미국 대공황 시작;1933|히틀러 독일 총리 취임;1936|스페인 내전 발발;1937|중일전쟁 전면화;1939|제2차 세계대전 발발;1941|진주만 공습;1944|노르망디 상륙작전;1945|제2차 세계대전 종전;1947|인도와 파키스탄 분리 독립;1948|대한민국 정부 수립;1949|중화인민공화국 수립;1950|한국전쟁 발발;1953|한국전쟁 정전협정 체결;1955|반둥 회의;1957|스푸트니크 1호 발사;1960|4·19 혁명;1961|유리 가가린 인류 최초 우주 비행;1962|쿠바 미사일 위기;1963|마틴 루서 킹 나에게는 꿈이 있습니다 연설;1964|미국 민권법 제정;1965|한일기본조약 체결;1969|아폴로 11호 달 착륙;1971|중화인민공화국 유엔 대표권 획득;1972|미국 닉슨 대통령 중국 방문;1973|제1차 석유 파동;1975|베트남전쟁 종료;1979|이란 혁명;1980|광주 5·18 민주화운동;1981|미국 우주왕복선 첫 비행;1986|체르노빌 원전 사고;1987|대한민국 6월 민주항쟁;1988|서울 올림픽 개최;1989|베를린 장벽 붕괴;1990|독일 통일;1991|소련 해체;1992|마스트리히트 조약 체결;1993|유럽연합 발족;1994|남아프리카공화국 첫 보통선거 실시;1995|세계무역기구 WTO 출범;1997|아시아 금융위기;1998|국제우주정거장 첫 모듈 발사;1999|유로화 전자화폐 도입;2000|남북 정상회담 개최;2001|미국 9·11 테러;2002|한일 월드컵 개최;2003|인간 게놈 프로젝트 완성 발표;2004|인도양 대지진과 쓰나미;2005|교토의정서 발효;2007|아이폰 첫 출시;2008|세계 금융위기;2010|아이티 대지진;2011|동일본 대지진;2012|힉스 보손 발견 발표;2015|파리 기후협정 채택;2016|영국 브렉시트 국민투표;2019|인류 최초 블랙홀 사진 공개;2020|세계보건기구 코로나19 팬데믹 선언;2022|제임스 웹 우주망원경 첫 과학 영상 공개
"""
events=[(int(y),title.strip()) for tok in HISTORY.split(';') if '|' in tok for y,title in [tok.strip().split('|',1)]]
all_years=sorted(set(y for y,_ in events))
for y,title in events:
    near=[x for x in (y-30,y-18,y-9,y-3,y+3,y+9,y+18,y+30) if x>0 and x!=y]
    add('history',f'“{title}”이(가) 일어난 해는?',y,near)
    century=(y-1)//100+1
    add('history',f'“{title}”이(가) 일어난 세기는?',f'{century}세기',[f'{x}세기' for x in range(max(1,century-3),century+4) if x!=century])
for i in range(2400):
    if sum(x['category']=='history' for x in rows)>=LIMIT:break
    sample=rng.sample(events,4)
    if len(set(y for y,t in sample))!=4:continue
    ask_early=(i%2==0)
    choice=min(sample) if ask_early else max(sample)
    sorted_events=sorted(sample,key=lambda x:x[1])
    prompt=f'다음 역사적 사건 중 가장 {"먼저" if ask_early else "나중에"} 일어난 것은?'
    hint='|'.join(sorted(t for y,t in sorted_events))
    add('history',prompt,choice[1],[t for y,t in sorted_events if t!=choice[1]],unique_hint=hint)

# ===== GENERAL KNOWLEDGE =====
# Dataset information based on local CountryInfo data; omit empty/nonstandard values.
all_countries=CountryInfo().all(); locale=Locale.parse('ko')
records=[]
for rawname,c in all_countries.items():
    iso=c.get('ISO',{}).get('alpha2',''); cap=c.get('capital',''); region=c.get('region','')
    dialing=c.get('callingCodes',[]); tld=c.get('tld',[])
    if not isinstance(cap,str) or len(cap)<2 or not iso or not region:continue
    ko=locale.territories.get(iso,c.get('name',rawname))
    if not ko:continue
    records.append({'ko':ko,'en':c.get('name',rawname),'cap':cap,'iso':iso,'region':region,
      'dial':str(dialing[0]) if dialing else '', 'tld':str(tld[0]) if tld else ''})
region_ko={'Asia':'아시아','Europe':'유럽','Africa':'아프리카','Americas':'아메리카','Oceania':'오세아니아','Antarctic':'남극'}
cap_counter=Counter(d['cap'] for d in records)
caps=list(set(d['cap'] for d in records))
iso_codes=[d['iso'] for d in records]
valid_tlds=list(set(d['tld'] for d in records if re.fullmatch(r'\.[a-z]{2,3}',d['tld'])))
valid_dials=list(set(d['dial'] for d in records if d['dial'].isdigit()))
for c in records:
    country=c['ko']; cap=c['cap']; reg=c['region']; code=c['iso']; dial=c['dial']; tld=c['tld']
    add('general',f'{country}의 수도는 어디인가요?',cap,[v for v in caps if v!=cap])
    if cap_counter[cap]==1:
        add('general',f'{cap}을(를) 수도로 두는 나라는?',country,[v['ko'] for v in records if v['ko']!=country])
    if reg in region_ko:
        add('general',f'{country}이(가) 속한 대륙·지역은?',region_ko[reg],[v for k,v in region_ko.items() if k!=reg and k!='Antarctic'])
    add('general',f'{country}의 ISO 3166-1 두 글자 국가 코드는?',code,[x for x in iso_codes if x!=code])
    if re.fullmatch(r'\.[a-z]{2,3}',tld):
        add('general',f'{country}의 국가 최상위 도메인(ccTLD)은?',tld,[v for v in valid_tlds if v!=tld])
    if dial.isdigit():
        add('general',f'{country}의 국제전화 국가번호는?',f'+{dial}',[f'+{v}' for v in valid_dials if v!=dial])
    if sum(x['category']=='general' for x in rows)>=LIMIT:break
for i in range(600):
    if sum(x['category']=='general' for x in rows)>=LIMIT:break
    a=rng.randint(10,99);b=rng.randint(3,35)
    val=a+b
    add('general',f'생활 상식 계산: {a}명이 있는 행사에 {b}명이 추가되면 총 몇 명일까요?',f'{val}명',[f'{v}명' for v in (val-2,val-1,val+1,val+2,val+10)])

counts=Counter(q['category'] for q in rows)
assert set(counts)=={'science','english','history','general'}, counts
assert all(counts[k]>=1000 for k in counts),counts
assert all(len(set(q['options']))==4 and 0<=q['correctIndex']<4 for q in rows)
assert len(set(q['id'] for q in rows))==len(rows)
with open(ROOT/'data/questions.json','w',encoding='utf-8') as f:
    json.dump(rows,f,ensure_ascii=False,separators=(',',':'))
print('Generated',dict(counts),'total=',len(rows),'bytes=',(ROOT/'data/questions.json').stat().st_size)
