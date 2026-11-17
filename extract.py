# -*- coding: utf-8 -*-
"""讀取議程與會議紀錄 PDF，產生 details.js（案件與決議）和 text/年度.js（全文）。

用法：
  py extract.py                 處理全部尚未處理的會議
  py extract.py --years 115     只處理民國 115 年
  py extract.py --limit 5       最多處理 5 場會議
PDF 只在記憶體中讀取，不會存到硬碟。
"""
import sys, site, os, io, re, json, time, argparse, urllib.request

sys.path.append(site.getusersitepackages())
import pypdf  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, 'data.js')
DETAILS = os.path.join(HERE, 'details.js')
TEXT_DIR = os.path.join(HERE, 'text')
UA = {'User-Agent': 'Mozilla/5.0 (meeting-minutes-indexer)'}
MAX_RES = 1500          # 每案決議最多保留的字數
ENUM = r'(?:[一二三四五六七八九十]+、|[（(][一二三四五六七八九十]+[）)])'
NAME = r'「([^「」]{8,260}?案)」'


def load_js(path, default):
    if not os.path.exists(path):
        return default
    s = open(path, encoding='utf-8-sig').read()
    if 'window.DETAILS' in s:
        s = s[s.index('window.DETAILS'):]
    return json.loads(s[s.find('{'):s.rfind('}') + 1])


def load_meetings():
    s = open(DATA, encoding='utf-8-sig').read()
    s = s[s.index('window.MEETINGS'):]
    return json.loads(s[s.index('['):s.rindex(']') + 1])


def fetch(url):
    last = None
    for i in range(3):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=90) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 + 2 * i)
    raise last


def pdf_text(data):
    reader = pypdf.PdfReader(io.BytesIO(data))
    pages = []
    for p in reader.pages:
        try:
            pages.append(p.extract_text() or '')
        except Exception:  # noqa: BLE001
            pages.append('')
    return pages


def normalize(pages):
    """把斷行接回去，並移除頁碼與每頁重複的頁首。"""
    out = []
    first_lines = {}
    for pg in pages:
        ls = [x.strip() for x in pg.splitlines() if x.strip()]
        if ls:
            first_lines[ls[0]] = first_lines.get(ls[0], 0) + 1
    for pg in pages:
        ls = [x.strip() for x in pg.splitlines() if x.strip()]
        for i, ln in enumerate(ls):
            if re.fullmatch(r'[-－ ]*\d{1,3}[-－ ]*', ln):
                continue
            if i == 0 and first_lines.get(ln, 0) >= 3:
                continue
            out.append(ln)
    s = ''.join(out)
    s = re.sub(r'[ \t　]+', ' ', s)
    s = re.sub(r'(?<=[一-鿿，。、：；「」（）]) (?=[一-鿿，。、：；「」（）])', '', s)
    return debold(s.strip())


CJK_GAP = r'(?<=[一-鿿，。、：；「」（）]) (?=[一-鿿，。、：；「」（）])'


def debold(s):
    """早期 PDF 的粗體字會重複 4 次（例如「一一一一、、、、」）。把重複的字還原。也移除頁碼文字。"""
    s = re.sub(r'第\s*\d+\s*頁\s*[，,]\s*共\s*\d+\s*頁', '', s)
    if len(re.findall(r'([一-鿿]{2,12})\1{3}', s)) + len(re.findall(r'([一-鿿「」、])\1{3}', s)) < 4:
        return s
    s = re.sub(r'((?=[^\n]{0,39}?[^\d\n])[^\n]{1,40}?)\1{3}', r'\1', s)
    s = re.sub(r'(?<!\d)(\d{1,3})\1 \1\1(?!\d)', r'\1', s)      # " 88 88" -> "8"
    s = re.sub(r'(?<!\d)(\d{1,4}) \1 \1 \1(?!\d)', r'\1', s)
    s = re.sub(r'-- --', '-', s)
    s = re.sub(r'[ \t]+', ' ', s)
    return re.sub(CJK_GAP, '', s)


def case_key(name):
    m = re.search(r'([一-鿿]{1,4}區[一-鿿]{1,8}段(?:[一-鿿]{1,3}小段)?)([\d\-]+)', name)
    return m.group(1)[-14:] + m.group(2) if m else name


def find_decision(seg):
    m = re.search(r'決議\s*[:：]', seg) or re.search(r'(?:修正)?結論如下', seg)
    return m.start() if m else -1


def merge_cases(agenda_cases, minutes_cases):
    """會議紀錄的案件為主。議程的案件只在會議紀錄沒有同一案時保留。"""
    keys = {case_key(c['name']) for c in minutes_cases}
    names = {c['name'] for c in minutes_cases}
    out, seen = [], set()
    for c in agenda_cases:
        k = case_key(c['name'])
        if c['name'] in names or k in keys or c['name'] in seen:
            continue
        seen.add(c['name'])
        out.append(c)
    return out + minutes_cases


def kind_at(text, pos):
    k = ''
    for m in re.finditer(r'(報告|討論|臨時)(?:提案|動議)|[壹貳參肆伍陸柒捌玖拾]+、\s*(報告|討論|臨時)', text[:pos]):
        k = m.group(1) or m.group(2)
    return k


def clean_name(n):
    return re.sub(r'\s+', '', n)


def parse_agenda(text):
    info = {}
    m = re.search(r'開會時間[:：]\s*(.{4,45}?)(?:。|\s*[２2二][、.．]\s*開會地點)', text)
    if m:
        info['time'] = re.sub(r'\s+', '', m.group(1))
    m = re.search(r'開會地點[:：]\s*(.{2,40}?)(?:議程|。|$)', text)
    if m:
        info['place'] = re.sub(r'\s+', '', m.group(1))
    cases, seen = [], set()
    for m in re.finditer(NAME, text):
        n = clean_name(m.group(1))
        if n in seen:
            continue
        seen.add(n)
        cases.append({'kind': kind_at(text, m.start()), 'name': n})
    info['cases'] = cases
    return info


NUM = r'([\d,]+(?:\.\d+)?)'
SEC = re.compile(r'[（(][一二三四五六七八九十]+[）)]\s*([^，。；：:（()）]{2,32}?部分)')
VERDICT = re.compile(r'(不予同意|予以同意|予以確認|同意給予|修正後通過|再提[請]?[^，。]{0,8}(?:討論|審議)|'
                     r'持續[^，。]{0,6}溝通協調|依[^，。、]{2,24}辦理|請實施者[^，。]{2,30})')


def tail_label(s, n=18):
    s = re.split(r'[，。；、：:（()）]|及|另|有關|本案', s)[-1].strip()
    return s[-n:]


def load_keywords():
    path = os.path.join(HERE, 'keywords.txt')
    out = []
    if not os.path.exists(path):
        return out
    for ln in open(path, encoding='utf-8-sig'):
        ln = ln.strip()
        if not ln or ln.startswith('#') or '=' not in ln:
            continue
        lab, pats = ln.split('=', 1)
        ws = [re.escape(w.strip()) for w in pats.split('|') if w.strip()]
        if lab.strip() and ws:
            out.append((lab.strip(), re.compile('|'.join(ws), re.I)))
    return out


KW = load_keywords()
FEE = r'(人事行政管理費|廣告銷售管理費|銷售管理費|風險管理費|信託管理費|信託費用|特殊因素費用|特殊費用|營建費用|建築設計費|貸款利息|容積移轉費用)'


def verdict(sent):
    sent = re.sub(r'(?:倘|如|若|逾期)[^，。]{0,24}(?:刪除|刪減|剔除|不予提列|不得提列)', '', sent)   # 附條件的句子不算刪除
    if re.search(r'刪除|刪減|剔除|不予提列|不得提列|不予認列|不予同意', sent):
        return '刪除'
    if re.search(r'調降|下修|酌降|降為|降至|酌減', sent):
        return '調降'
    if re.search(r'予以同意|同意', sent):
        return '同意'
    if '予以確認' in sent:
        return '確認'
    return '檢討'


def bonus_label(pre):
    code = re.findall(r'△\s*F\s*\d(?:\s*-\s*\d)?|#\s*\d+(?:-\d+)?', pre)
    name = re.findall(r'[（(]([^（()）]{2,20}?獎勵[^（()）]{0,4})[）)]|'
                      r'([一-鿿、]{2,22}?(?:之獎勵容積|獎勵容積|容積獎勵|獎勵))(?!辦法|額度|部分|項目|值)', pre)
    nm = ''
    if name:
        nm = name[-1][0] or name[-1][1]
    nm = re.sub(r'^(?:及|另|有關|本案|申請|依|符合|同意)+', '', nm)
    nm = re.sub(r'之?獎勵容積$|之?容積獎勵$', '獎勵', nm)
    c = re.sub(r'\s+', '', code[-1]) if code else ''
    return (c + ' ' + nm).strip() or tail_label(pre, 16) or '其他獎勵'


def summarize(seg):
    """從一個案件的整段文字整理重點。seg 是案名之後、下一案之前的文字。"""
    tags = [lab for lab, rx in KW if rx.search(seg)]
    k = find_decision(seg)
    if k == -1:
        return {'tags': tags} if tags else None
    body = seg[k:]
    cut = len(body)
    for pat in (r'附件\s*[-－—–]', r'聽證紀錄', r'[（(][一二三四五六七八九十]+[）)]\s*[一-鿿]{2,4}(?:委員|幹事)',
                r'[壹貳參肆伍陸柒捌玖拾]+、\s*(?:散會|討論提案|報告提案|臨時動議)'):
        m = re.search(pat, body)
        if m:
            cut = min(cut, m.start())
    dec = body[:cut]
    s = {}

    # 總結句（通常在聽證紀錄之後）
    m = re.search(r'(同意本案[^。]{0,160}?(?:修正後通過|通過)[^。]{0,200}。)', body)
    if tags:
        s['tags'] = tags
    head = dec[:260]
    if '修正後通過' in dec or (m and '修正後通過' in m.group(1)):
        s['outcome'] = '修正後通過'
    elif m:
        s['outcome'] = '通過'
    elif re.search(r'未及討論|順延|出席人數未過半', head):
        s['outcome'] = '順延'
    elif re.search(r'再次?提[請]?[^，。]{0,30}(討論|審議|報告)|再行?提會|提請?下次|下次會議|申請續審|再提[^，。]{0,12}會', dec):
        s['outcome'] = '續審'
    elif '維持原核定' in head:
        s['outcome'] = '維持原核定'
    elif '予以確認' in head:
        s['outcome'] = '予以確認'
    elif re.search(r'不予同意|不同意', head):
        s['outcome'] = '不同意'
    elif '同意' in head:
        s['outcome'] = '同意'
    elif '洽悉' in head:
        s['outcome'] = '洽悉'

    # 容積獎勵：逐項列出核給與不給的項目
    bm = None
    for m2 in SEC.finditer(dec):
        if '容積獎勵' in m2.group(1):
            bm = m2
    if bm:
        nx = SEC.search(dec, bm.end())
        part = dec[bm.end():nx.start() if nx else len(dec)]
    else:
        part = dec
    bonus, tot, last = [], 0.0, 0
    for b in re.finditer(r'(同意給予|不予給予|不予同意|不同意給予)(?:\s*' + NUM + r'\s*(?:平方公尺|㎡|m2|M2)\s*[（(]\s*(?:占|佔)?法定容積(?:之)?\s*' + NUM + r'\s*[％%])?', part):
        pre = part[last:b.start()][-160:]
        ok = b.group(1) == '同意給予'
        if ok and not b.group(3):
            continue
        if not ok and '獎勵' not in pre[-90:]:
            continue
        last = b.end()
        item = {'label': bonus_label(pre), 'ok': ok}
        code = re.match(r'(#\d+(?:-\d+)?|△F\d(?:-\d)?)', item['label'])
        if code and any(x['label'].startswith(code.group(1) + ' ') or x['label'] == code.group(1) for x in bonus):
            s['bonusMulti'] = True      # 決議列出第二組容獎數字，只保留第一組
            break
        if b.group(3):
            item['pct'] = b.group(3)
            if ok:
                try:
                    tot += float(b.group(3))
                except ValueError:
                    pass
        bonus.append(item)
    if bonus:
        s['bonus'] = bonus[:14]
        s['bonusTotal'] = round(tot, 2)

    # 關鍵數字
    m = re.search(r'(?<!承諾)共同負擔比[例率]?(?:為|約|由)?\s*' + NUM + r'\s*[%％]', dec)
    if m:
        s['burden'] = m.group(1)
    m = re.search(r'承諾[之的]?共同負擔比[例率]?(?:為|約)?\s*' + NUM + r'\s*[%％]', dec)
    if m:
        s['commit'] = m.group(1)
    changes = []
    for m in re.finditer(r'([^，。；（()）]{2,30}?)(?:為|約)?\s*' + NUM + r'\s*(元/坪|元/平方公尺|元/個|元|％|%)\s*[（(]\s*(?:前次|原)(?:為|核定為|提列)?\s*' + NUM, dec):
        changes.append({'label': tail_label(m.group(1)), 'new': m.group(2), 'unit': m.group(3), 'old': m.group(4)})
    for m in re.finditer(r'([^，。；（()）]{2,24}?)由\s*' + NUM + r'\s*(元/坪|元/平方公尺|元|％|%)?\s*(?:調整|修正|變更|增加|減少|調降|調升|降)[為至]\s*' + NUM + r'\s*(元/坪|元/平方公尺|元|％|%)', dec):
        changes.append({'label': tail_label(m.group(1)), 'old': m.group(2), 'new': m.group(4), 'unit': m.group(5)})
    if changes:
        s['changes'] = changes[:8]
    # 費用：各項管理費與特殊費用的費率，以及審議會同意、調降或刪除
    fees, seen = [], set()
    for sent in dec.split('。'):
        if not re.search(FEE, sent):
            continue
        v = verdict(sent)
        for m in re.finditer(FEE, sent):
            lab = '特殊因素費用' if '特殊' in m.group(1) else m.group(1)
            if lab in seen:
                continue
            seen.add(lab)
            item = {'label': lab, 'v': v}
            r = re.match(r'[^，。；、]{0,12}?' + NUM + r'\s*[%％]', sent[m.end():])
            if r:
                item['pct'] = r.group(1)
            if lab == '特殊因素費用':
                i = max(0, m.start() - 12)
                item['note'] = re.sub(r'^[\d.、（()）\s]+', '', sent[i:i + 130]).strip()
            fees.append(item)
    if fees:
        s['fees'] = fees
    return s or None


def parse_minutes(text):
    heads = []
    seen = set()
    for m in re.finditer(NAME, text):
        n = clean_name(m.group(1))
        if n in seen:
            continue
        pre = text[max(0, m.start() - 16):m.start()]
        post = text[m.end():m.end() + 90]
        by_enum = re.search(ENUM + r'[^「」。：:]{0,10}$', pre) and not re.search(r'決議', pre)
        if not by_enum and '承辦人' not in post:
            continue
        seen.add(n)
        heads.append((m.start(), m.end(), n))
    cases = []
    stop = re.search(r'[壹貳參肆伍陸柒捌玖拾]+、\s*散會', text)
    end_all = stop.start() if stop else len(text)
    for i, (st, en, n) in enumerate(heads):
        nxt = heads[i + 1][0] if i + 1 < len(heads) else end_all
        seg = text[en:max(en, nxt)]
        res = ''
        k = find_decision(seg)
        if k != -1:
            res = re.split(r'附件\s*[-－—–]|[壹貳參肆伍陸柒捌玖拾]+、\s*(?:散會|討論提案|報告提案|臨時動議)', seg[k:])[0].strip()
            if len(res) > MAX_RES:
                res = res[:MAX_RES] + '…'
        c = {'kind': kind_at(text, st), 'name': n, 'resolution': res}
        sm = summarize(seg)
        if sm:
            c['summary'] = sm
        cases.append(c)
    return cases


def write_js(path, prefix, obj, suffix=';\n'):
    tmp = json.dumps(obj, ensure_ascii=False, separators=(',', ':'))
    with open(path, 'w', encoding='utf-8-sig') as f:
        f.write(prefix + tmp + suffix)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--years', default='', help='民國年，用逗號分開，例如 115,114')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--redo', action='store_true', help='重新處理已處理過的會議')
    ap.add_argument('--reparse', action='store_true', help='不下載，用已存的全文重新整理案件與重點')
    ap.add_argument('--dry', action='store_true', help='搭配 --reparse，只顯示統計，不寫入檔案')
    a = ap.parse_args()
    years = {int(y) + 1911 for y in a.years.split(',') if y.strip()}

    meetings = load_meetings()
    details = load_js(DETAILS, {})
    os.makedirs(TEXT_DIR, exist_ok=True)
    if a.reparse:
        reparse(details, years, a.dry)
        return
    texts = {}      # 西元年 -> {id: 全文}
    dirty = set()

    def year_texts(y):
        if y not in texts:
            texts[y] = load_js(os.path.join(TEXT_DIR, f'{y}.js'), {})
        return texts[y]

    done = 0
    for m in meetings:
        y = int(m['date'][:4])
        if years and y not in years:
            continue
        urls = [x['url'] for x in m.get('agenda', [])] + [x['url'] for x in m.get('minutes', [])]
        old = details.get(m['id'])
        if old and old.get('urls') == urls and not a.redo and not old.get('error'):
            continue
        if a.limit and done >= a.limit:
            break
        d = {'urls': urls, 'cases': []}
        full = []
        try:
            ag_cases = []
            for x in m.get('agenda', []):
                t = normalize(pdf_text(fetch(x['url'])))
                info = parse_agenda(t)
                for k in ('time', 'place'):
                    if info.get(k) and k not in d:
                        d[k] = info[k]
                ag_cases += info['cases']
                time.sleep(0.3)
            mi_cases = []
            for x in m.get('minutes', []):
                t = normalize(pdf_text(fetch(x['url'])))
                if t:
                    full.append(t)
                mi_cases += parse_minutes(t)
                time.sleep(0.3)
            d['cases'] = merge_cases(ag_cases, mi_cases)
            d['hasText'] = bool(full)
            if m.get('minutes') and not full:
                d['scan'] = True               # 掃描檔，取不到文字
        except Exception as e:  # noqa: BLE001
            d['error'] = str(e)[:200]
        details[m['id']] = d
        if full:
            year_texts(y)[m['id']] = '\n'.join(full)
            dirty.add(y)
        done += 1
        flag = 'ERR ' + d['error'] if d.get('error') else f"{len(d['cases'])} cases" + (' (scan)' if d.get('scan') else '')
        print(f"[{done}] {m['id']}: {flag}", flush=True)
        if done % 20 == 0:
            save(details, texts, dirty)
    save(details, texts, dirty)
    print(f'done {done} meetings')


def reparse(details, years, dry):
    stat, n_case, n_sum = {}, 0, 0
    texts, dirty = {}, set()
    for f in sorted(os.listdir(TEXT_DIR)):
        if not re.fullmatch(r'\d{4}\.js', f) or (years and int(f[:4]) not in years):
            continue
        y = int(f[:4])
        ytext = load_js(os.path.join(TEXT_DIR, f), {})
        for mid, text in ytext.items():
            d = details.get(mid)
            if d is None:
                continue
            fixed = debold(text)
            if fixed != text:
                ytext[mid] = fixed
                dirty.add(y)
            # 舊資料中沒有決議的案件來自議程；名稱也做一次還原
            agenda = []
            for c in d.get('cases', []):
                if c.get('resolution') or c.get('summary'):
                    continue
                agenda.append({'kind': c.get('kind', ''), 'name': clean_name(debold(c['name']))})
            mi = parse_minutes(fixed)
            d['cases'] = merge_cases(agenda, mi)
            for c in d['cases']:
                n_case += 1
                if c.get('summary'):
                    n_sum += 1
                    o = c['summary'].get('outcome', 'none')
                    stat[o] = stat.get(o, 0) + 1
        texts[y] = ytext
    print(f'cases {n_case}, with summary {n_sum}, outcomes {json.dumps(stat, ensure_ascii=True)}')
    if not dry:
        save(details, texts, dirty)


def save(details, texts, dirty):
    for y in sorted(dirty):
        write_js(os.path.join(TEXT_DIR, f'{y}.js'), 'window.__ft && window.__ft(', texts[y], ');\n')
    dirty.clear()
    ys = sorted(int(f[:4]) for f in os.listdir(TEXT_DIR) if re.fullmatch(r'\d{4}\.js', f))
    with open(DETAILS, 'w', encoding='utf-8-sig') as f:
        f.write('window.FULLTEXT_YEARS = ' + json.dumps(ys) + ';\nwindow.DETAILS = ' +
                json.dumps(details, ensure_ascii=False, separators=(',', ':')) + ';\n')


if __name__ == '__main__':
    main()
