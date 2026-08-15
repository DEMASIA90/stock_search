from dataclasses import dataclass

@dataclass(frozen=True)
class Stock:
    ticker: str
    name: str
    market: str
    currency: str

US = [
    Stock('AAPL','Apple','US','USD'), Stock('MSFT','Microsoft','US','USD'),
    Stock('NVDA','NVIDIA','US','USD'), Stock('AMZN','Amazon','US','USD'),
    Stock('GOOGL','Alphabet A','US','USD'), Stock('META','Meta Platforms','US','USD'),
    Stock('TSLA','Tesla','US','USD'), Stock('AVGO','Broadcom','US','USD'),
    Stock('BRK-B','Berkshire Hathaway B','US','USD'), Stock('JPM','JPMorgan Chase','US','USD'),
    Stock('V','Visa','US','USD'), Stock('MA','Mastercard','US','USD'),
    Stock('LLY','Eli Lilly','US','USD'), Stock('WMT','Walmart','US','USD'),
    Stock('ORCL','Oracle','US','USD'), Stock('NFLX','Netflix','US','USD'),
    Stock('COST','Costco','US','USD'), Stock('AMD','AMD','US','USD'),
    Stock('CRM','Salesforce','US','USD'), Stock('BAC','Bank of America','US','USD'),
    Stock('KO','Coca-Cola','US','USD'), Stock('PEP','PepsiCo','US','USD'),
    Stock('MCD',"McDonald's",'US','USD'), Stock('DIS','Walt Disney','US','USD'),
    Stock('INTC','Intel','US','USD'), Stock('QCOM','Qualcomm','US','USD'),
    Stock('TXN','Texas Instruments','US','USD'), Stock('AMAT','Applied Materials','US','USD'),
    Stock('MU','Micron Technology','US','USD'), Stock('UBER','Uber','US','USD'),
    Stock('PLTR','Palantir','US','USD'), Stock('ABNB','Airbnb','US','USD'),
    Stock('NKE','Nike','US','USD'), Stock('SBUX','Starbucks','US','USD'),
    Stock('PYPL','PayPal','US','USD'), Stock('SOFI','SoFi Technologies','US','USD'),
    Stock('GS','Goldman Sachs','US','USD'), Stock('MS','Morgan Stanley','US','USD'),
    Stock('XOM','Exxon Mobil','US','USD'), Stock('CVX','Chevron','US','USD'),
    Stock('UNH','UnitedHealth','US','USD'), Stock('JNJ','Johnson & Johnson','US','USD'),
    Stock('PG','Procter & Gamble','US','USD'), Stock('HD','Home Depot','US','USD'),
    Stock('CSCO','Cisco','US','USD'), Stock('IBM','IBM','US','USD'),
    Stock('GE','GE Aerospace','US','USD'), Stock('CAT','Caterpillar','US','USD'),
]

KR = [
    Stock('005930.KS','삼성전자','KR','KRW'), Stock('000660.KS','SK하이닉스','KR','KRW'),
    Stock('373220.KS','LG에너지솔루션','KR','KRW'), Stock('207940.KS','삼성바이오로직스','KR','KRW'),
    Stock('005380.KS','현대차','KR','KRW'), Stock('000270.KS','기아','KR','KRW'),
    Stock('068270.KS','셀트리온','KR','KRW'), Stock('105560.KS','KB금융','KR','KRW'),
    Stock('055550.KS','신한지주','KR','KRW'), Stock('035420.KS','NAVER','KR','KRW'),
    Stock('035720.KS','카카오','KR','KRW'), Stock('005490.KS','POSCO홀딩스','KR','KRW'),
    Stock('006400.KS','삼성SDI','KR','KRW'), Stock('051910.KS','LG화학','KR','KRW'),
    Stock('012330.KS','현대모비스','KR','KRW'), Stock('028260.KS','삼성물산','KR','KRW'),
    Stock('000810.KS','삼성화재','KR','KRW'), Stock('012450.KS','한화에어로스페이스','KR','KRW'),
    Stock('329180.KS','HD현대중공업','KR','KRW'), Stock('015760.KS','한국전력','KR','KRW'),
    Stock('033780.KS','KT&G','KR','KRW'), Stock('009150.KS','삼성전기','KR','KRW'),
    Stock('017670.KS','SK텔레콤','KR','KRW'), Stock('066570.KS','LG전자','KR','KRW'),
    Stock('011170.KS','롯데케미칼','KR','KRW'), Stock('086790.KS','하나금융지주','KR','KRW'),
    Stock('024110.KS','기업은행','KR','KRW'), Stock('316140.KS','우리금융지주','KR','KRW'),
    Stock('047050.KS','포스코인터내셔널','KR','KRW'), Stock('034020.KS','두산에너빌리티','KR','KRW'),
    Stock('247540.KQ','에코프로비엠','KR','KRW'), Stock('196170.KQ','알테오젠','KR','KRW'),
    Stock('028300.KQ','HLB','KR','KRW'), Stock('086520.KQ','에코프로','KR','KRW'),
    Stock('402340.KS','SK스퀘어','KR','KRW'), Stock('042660.KS','한화오션','KR','KRW'),
    Stock('010140.KS','삼성중공업','KR','KRW'), Stock('267260.KS','HD현대일렉트릭','KR','KRW'),
    Stock('010130.KS','고려아연','KR','KRW'), Stock('003670.KS','포스코퓨처엠','KR','KRW'),
    Stock('096770.KS','SK이노베이션','KR','KRW'), Stock('003550.KS','LG','KR','KRW'),
    Stock('030200.KS','KT','KR','KRW'), Stock('032830.KS','삼성생명','KR','KRW'),
    Stock('018260.KS','삼성에스디에스','KR','KRW'), Stock('009540.KS','HD한국조선해양','KR','KRW'),
    Stock('010950.KS','S-Oil','KR','KRW'), Stock('090430.KS','아모레퍼시픽','KR','KRW'),
]

ALL = US + KR
