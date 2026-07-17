import requests, json, time, os

H = {'User-Agent': 'Mozilla/5.0'}

EN_CN = {
    'France':'法国','Germany':'德国','Spain':'西班牙','Italy':'意大利',
    'Netherlands':'荷兰','Portugal':'葡萄牙','Belgium':'比利时',
    'Brazil':'巴西','Argentina':'阿根廷','Uruguay':'乌拉圭','Colombia':'哥伦比亚',
    'Chile':'智利','Peru':'秘鲁','Ecuador':'厄瓜多尔',
    'Mexico':'墨西哥','USA':'美国','United States':'美国','Canada':'加拿大',
    'Costa Rica':'哥斯达黎加','Panama':'巴拿马','Honduras':'洪都拉斯','Jamaica':'牙买加',
    'Japan':'日本','South Korea':'韩国','Korea Republic':'韩国',
    'Australia':'澳大利亚','Saudi Arabia':'沙特','Iran':'伊朗',
    'Qatar':'卡塔尔','Morocco':'摩洛哥','Senegal':'塞内加尔','Tunisia':'突尼斯',
    'Algeria':'阿尔及利亚','Egypt':'埃及','Nigeria':'尼日利亚',
    'Cameroon':'喀麦隆','Ghana':'加纳','South Africa':'南非',
    'Ivory Coast':'科特迪瓦','Cote dIvoire':'科特迪瓦',
    'Croatia':'克罗地亚','Serbia':'塞尔维亚','Switzerland':'瑞士',
    'Denmark':'丹麦','Sweden':'瑞典','Norway':'挪威','Poland':'波兰',
    'Austria':'奥地利','Czech Republic':'捷克','Czechia':'捷克',
    'Slovakia':'斯洛伐克','Hungary':'匈牙利','Romania':'罗马尼亚',
    'Turkey':'土耳其','Greece':'希腊','Ukraine':'乌克兰',
    'Russia':'俄罗斯','Wales':'威尔士','Scotland':'苏格兰',
    'Paraguay':'巴拉圭','Bolivia':'玻利维亚','Venezuela':'委内瑞拉',
    'Iraq':'伊拉克','New Zealand':'新西兰',
    'Bosnia':'波黑','Bosnia and Herzegovina':'波黑',
    'Finland':'芬兰','Ireland':'爱尔兰','Northern Ireland':'北爱尔兰',
    'Iceland':'冰岛','Bulgaria':'保加利亚','Slovenia':'斯洛文尼亚',
    'England':'英格兰','China':'中国','China PR':'中国',
    'Israel':'以色列','Georgia':'格鲁吉亚',
    'Korea DPR':'朝鲜','North Korea':'朝鲜',
    'Uzbekistan':'乌兹别克斯坦','Montenegro':'黑山',
    'North Macedonia':'北马其顿','Armenia':'亚美尼亚',
    'Belarus':'白俄罗斯','Moldova':'摩尔多瓦',
    'Kosovo':'科索沃','Luxembourg':'卢森堡',
    'South Sudan':'南苏丹','Cuba':'古巴','Haiti':'海地',
    'Guatemala':'危地马拉','El Salvador':'萨尔瓦多',
    'Yugoslavia':'南斯拉夫','Czechoslovakia':'捷克斯洛伐克',
    'Soviet Union':'苏联','West Germany':'西德','East Germany':'东德',
    'Zaire':'扎伊尔','Trinidad and Tobago':'特立尼达和多巴哥',
    'United Arab Emirates':'阿联酋','Kuwait':'科威特','Oman':'阿曼',
    'Bahrain':'巴林','Jordan':'约旦','Vietnam':'越南','Thailand':'泰国',
    'Syria':'叙利亚','Lebanon':'黎巴嫩','Palestine':'巴勒斯坦',
    'Indonesia':'印度尼西亚','India':'印度',
}
def t(name):
    if name in EN_CN: return EN_CN[name]
    for en,cn in EN_CN.items():
        if en.lower()==name.lower(): return cn
    return name

def fetch_all(cid, label):
    matches=[]
    page=1
    while True:
        try:
            url='https://api.fifa.com/api/v3/calendar/matches?idCompetition='+cid+'&language=en&count=100&page='+str(page)
            r=requests.get(url,headers=H,timeout=15)
            if r.status_code!=200:
                print('  HTTP '+str(r.status_code)+' at page '+str(page))
                break
            data=r.json()
            results=data.get('Results',[])
            if not results:
                print('  Empty at page '+str(page))
                break
            count=0
            last_date=''
            for m in results:
                hg=m.get('HomeTeamScore'); ag=m.get('AwayTeamScore')
                if hg is None or ag is None: continue
                h=m.get('Home',{}).get('TeamName',[{}])
                a=m.get('Away',{}).get('TeamName',[{}])
                hn=h[0].get('Description','') if h else ''
                an=a[0].get('Description','') if a else ''
                if not hn or not an: continue
                date=(m.get('Date','') or '')[:10]
                if not date: continue
                last_date=date
                matches.append({'home_team':t(hn),'away_team':t(an),'home_goals':int(hg),'away_goals':int(ag),'league':label,'date':date})
                count+=1
            print('  Page '+str(page)+': '+str(count)+' scored, last date='+last_date)
            page+=1
            time.sleep(0.15)
            if page>50: break
        except Exception as e:
            print('  Error page '+str(page)+': '+str(e)); break
    return matches

print('Fetching ALL World Cup history...')
wc=fetch_all('17','世界杯')
print('  World Cup total: '+str(len(wc)))

print('Fetching ALL WC Qualifiers...')
wcq=fetch_all('520','世预赛')
print('  WC Qualifiers total: '+str(len(wcq)))

all_m=wc+wcq
seen=set(); unique=[]
for m in all_m:
    key=m['home_team']+'|'+m['away_team']+'|'+m['date']
    if key not in seen:
        seen.add(key); unique.append(m)
print('Total unique: '+str(len(unique)))

os.makedirs('data/raw',exist_ok=True)
with open('data/raw/fifa_nt_matches.json','w',encoding='utf-8') as f:
    json.dump(unique,f,ensure_ascii=False,indent=1)
print('Saved!')
