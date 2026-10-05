"""Deterministic, conservative story matching shared by all newsroom stages.

A fingerprint is evidence, not an event id: actors alone never prove that two
reports describe the same event. Keep placement/source decisions at the caller.
"""
from __future__ import annotations

import copy
import re
from decimal import Decimal
from functools import lru_cache

MATCH = 0.8
_STOP = set('''a an the of to in on for and as with its is at by from after over
says said amid into new up how why what will could may be are has have was were
it this that than then about more most also been would year years first global
news report reports week day days just not but out who all can her his their they
our your you we us one two three per via vs set sets gets get make makes back
india indian china chinese america american world global ai ceo cfo gdp rbi upi us
uk eu government company companies startup startups tech technology billion
million crore lakh cr bn inc ltd limited corp corporation group ventures
launch launches launched launching announce announces announced announcing
raise raises raised raising close closes closed closing fund funds funding
investment invests invests partners partnership venture unveils unveiled
backs backed next big major move says says plan plans planned eyes aims seek
seeks see sees mark marks agree agrees agreed deal business industry market
markets power growth future latest today yesterday tomorrow monday tuesday
wednesday thursday friday saturday sunday january february march april june
july august september october november december jan feb mar apr jun jul aug
sep sept oct nov dec the this these those there here but while without despite
which when where whether why during against through under between before
than now only other some such each much many still just even very so no yes
not it its them he she i we you me my his her our your their both either
neither all any every according source sources limited summary text context
mechanism comes means because instead already across around roughly about
nearly almost says said told reports reported using use used users including
include includes also would should must might can cannot become becomes
became make makes made take takes took give gives given get gets got say
need needs needed offer offers offered want wants wanted worth worth new
last end start starts started over million billion trillion percent percentage
points basis people workers jobs work model models system systems data safety
agents agent chip chips chipmaker revenue profit quarter cut cuts job percent
'''.split())
# Content words remain available as event anchors even when capitalised titles
# make them unreliable as names.
_GENERIC = _STOP | set('''buy buys buying build builds building test tests testing
launching ban bans banned banish study research warns warning risk risks
authority tariff tariffs import imports export exports probe review order
court final draft crash panel role seeks seeks probe launch partnership
rs inr usd mn bn cr pm president minister chief executive officer
project projects mw gw mwh gwh twh if follow published published2 ist
roundup updates schedule preview thousands
artificial intelligence robotics robot robots funding talks push asks signed
sign signs policy rules approved approves approval regulator regulatory
supply demand spending development secure security autonomous autonomy
hiring infrastructure industrial green hydrogen energy renewable solar
'''.split())
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:[-.][A-Za-z0-9]+)*")
_NUMBER = re.compile(
    r"(?<![\w.])(?P<currency>US\$|\$|₹|Rs\.?|INR|USD|€|£)?\s*"
    r"(?P<value>\d[\d,]*(?:\.\d+)?)\s*-?\s*"
    r"(?P<scale>lakh[- ]crore|crores?|cr|lakhs?|lacs?|trillion|billion|million|"
    r"thousand|bn|mn|tn|[bmk])?(?![a-zA-Z])\s*"
    r"(?P<unit>%|percent(?:age)?|basis points?|bps?|gwh|twh|gw|mw|kwh|km|"
    r"millimetres?|mm|metres?|meters?|tonnes?|tons?|minutes?|hours?|days?|"
    r"years?|months?|workers?|jobs?|employees?|managers?|startups?|companies|"
    r"people|users?|drones?|satellites?|districts?)?", re.I)
_SCALE = {'k': 1000, 'thousand': 1000, 'm': 10**6, 'mn': 10**6, 'million': 10**6,
          'b': 10**9, 'bn': 10**9, 'billion': 10**9, 't': 10**12, 'tn': 10**12,
          'trillion': 10**12, 'cr': 10**7, 'crore': 10**7, 'lakh': 10**5,
          'lac': 10**5, 'lakh crore': 10**12}


def clean(text):
    text = re.sub(r"['’]s\b", '', str(text or ''))
    return text.replace('==', '').replace('__', '').replace('’', "'")


def numbers(text):
    """Canonical quantities. A unit is part of the key; 20 jobs != 20%."""
    found = set()
    for m in _NUMBER.finditer(clean(text)):
        value = Decimal(m['value'].replace(',', ''))
        scale = (m['scale'] or '').casefold().replace('-', ' ').rstrip('s')
        currency = (m['currency'] or '').casefold().rstrip('.')
        unit = (m['unit'] or '').casefold()
        if currency in ('₹', 'rs', 'inr') or (not currency and not unit and scale in ('cr', 'crore', 'lakh', 'lac', 'lakh crore')):
            unit = 'inr'
        elif currency in ('$', 'us$', 'usd'):
            unit = 'usd'
        elif currency:
            unit = currency
        elif unit.startswith('percent'):
            unit = '%'
        elif unit in ('bp', 'bps', 'basis point', 'basis points'):
            unit = 'bp'
        else:
            unit = unit.rstrip('s')
            unit = {'companie': 'company', 'employee': 'worker', 'job': 'worker',
                    'meter': 'metre', 'millimetre': 'mm', 'ton': 'tonne'}.get(unit, unit)
        value *= _SCALE.get(scale, 1)
        found.add(f'{value.normalize():f}:{unit or "number"}')
    return found


def nouns(text):
    words = set()
    for w in _WORD.findall(clean(text)):
        w = w.casefold().replace('speciality', 'specialty')
        # "Madras-backed" must still share "madras" with "IIT Madras".
        words.update(w.split('-') if '-' in w else [w])
    words = {w[:-3] if w.endswith('ing') and len(w) > 6 else w for w in words}
    return {w[:-1] if w.endswith('s') and len(w) > 4 and not w.endswith('ss') else w
            for w in words if w not in _STOP and len(w) > 2}


def entities(text, sentence_initial=False):
    text = clean(text)
    result = set()
    for m in _WORD.finditer(text):
        w = m.group()
        if w.casefold() in _GENERIC or len(w) < 2 or not any(c.isupper() for c in w):
            continue
        # For grounding omit single sentence-initial words, but keep acronyms,
        # internal capitals and multiword names ("World Labs", "Sam Altman").
        before = text[:m.start()].rstrip()
        initial = not before or before[-1] in '.!?\n'
        tail = text[m.end():]
        multi = re.match(r'\s+[A-Z][a-z]+', tail)
        if sentence_initial and initial and w.istitle() and not multi:
            continue
        result.add(w.casefold())
    return result


_TITLE_WORDS = {'prime', 'minister', 'chief', 'executive', 'officer', 'president', 'governor',
                'secretary', 'director', 'chairman', 'chair', 'ceo', 'cfo', 'cto', 'founder',
                'md', 'head', 'senator', 'sen', 'judge', 'justice'}
_GLUE = {'the', 'of', 'and', 'for', 'in'}


def product_names(text):
    """Distinctive multi-word proper names ('Open Agent Safety Platform',
    'Gemini 4 Argon'): runs of 3+ capitalised words that are not just a job
    title. Two cards naming the same such thing about the same company are
    the same story (v9: the 29 Sep Nvidia platform card written twice)."""
    out = set()
    for m in re.finditer(r"(?<![.!?]\s)(?<!^)\b((?:[A-Z][\w-]*|\d+)(?:\s+(?:[A-Z][\w-]*|\d+)){2,})", text):
        words = [w.casefold() for w in m.group(1).split()]
        # a phrase with a job title in it is a person ("Nvidia CEO Jensen
        # Huang"), and people recur across unrelated stories: never a match key
        if not set(words) & _TITLE_WORDS and sum(w not in _GLUE for w in words) >= 3:
            out.add(' '.join(words))
    return out


@lru_cache(maxsize=16384)
def fingerprint(title, text=''):
    head = clean(title)
    body = clean(text)
    head_names = entities(head)
    primary = next((w.casefold() for w in _WORD.findall(head) if w.casefold() in head_names), '')
    lead_clause = re.split(r'[,;]', head.casefold())[0].replace('jantar mantar', 'delhi')
    places = set(re.findall(r'\b(?:mumbai|delhi|jaipur|bengaluru|chennai|kolkata|paris|london|kyiv|estonia|ireland)\b', lead_clause))
    return {'primary': primary, 'places': places, 'entities': entities(head + ' ' + body), 'numbers': numbers(head + ' ' + body),
            'nouns': nouns(head + ' ' + body), 'head': nouns(head),
            'head_entities': head_names, 'title': head.casefold(),
            'countries': {w.replace('indian', 'india').replace('chinese', 'china') for w in re.findall(r'\b(?:india|indian|iran|china|chinese|pakistan|nepal)\b', head.casefold())},
            'verdicts': verdicts(head + ' ' + body),
            'focus_entities': entities(head + ' ' + re.split(r'(?<=[.!?])\s+(?=[A-Z])', body)[0]),
            'focus_numbers': numbers(head + ' ' + re.split(r'(?<=[.!?])\s+(?=[A-Z])', body)[0]),
            'focus_verdicts': verdicts(head + ' ' + re.split(r'(?<=[.!?])\s+(?=[A-Z])', body)[0]),
            'names': product_names(head + '. ' + body)}


def verdicts(text):
    forms = {'approves': 'approved', 'rejects': 'rejected', 'blocks': 'blocked',
             'overturns': 'overturned', 'bans': 'banned', 'clears': 'cleared',
             'dismisses': 'dismissed', 'halts': 'halted', 'resumes': 'resumed',
             'names': 'appointed', 'named': 'appointed', 'appoints': 'appointed',
             'appointed': 'appointed', 'creates': 'created', 'created': 'created',
             'summons': 'summoned', 'summoned': 'summoned'}
    words = set(re.findall(r'\b[a-z]+\b', clean(text).casefold()))
    return {forms.get(w, w) for w in words if w in forms or w in (
        'approved', 'rejected', 'blocked', 'overturned', 'acquitted', 'convicted',
        'banned', 'cleared', 'dismissed', 'halted', 'resumed')}


def same_story(a, b):
    """Score in [0, 1]. MATCH is deliberately precision-first."""
    if a['title'] and a['title'] == b['title']:
        return 1.0
    if a['countries'] and b['countries'] and not a['countries'] & b['countries']:
        return 0.0
    if a['places'] and b['places'] and not a['places'] & b['places']:
        return 0.0
    ea, eb = a['entities'], b['entities']
    shared = ea & eb
    head = a['head'] & b['head']
    hn = min(len(a['head']), len(b['head']))
    content = a['nouns'] & b['nouns']
    # Headline anchors stop a shared background number/actor in long cards
    # from merging separate developments about the same company.
    if len(head) >= 4 and len(head) / max(1, len(a['head'] | b['head'])) >= .6:
        return .94
    anchor = a['head_entities'] & b['head_entities']
    if len(anchor) >= 2 and len(head) >= 3 and len(head) / max(1, len(a['head'] | b['head'])) >= .85:
        return .93
    event_anchor = head - a['head_entities'] - b['head_entities'] - {'chief', 'call', 'say', 'new', 'report'}
    focus_shared = a['focus_entities'] & b['focus_entities']
    primary_shared = a['primary'] in b['head_entities'] and b['primary'] in a['head_entities']
    if primary_shared and (event_anchor or len(anchor) >= 3) and anchor and len(head) >= 2 and len(focus_shared) >= 3 and len(focus_shared) / max(1, max(len(a['focus_entities']), len(b['focus_entities']))) >= .6:
        return .9
    quantities = {n for n in a['numbers'] & b['numbers']
                  if not n.endswith(':number')}
    if (event_anchor or len(anchor) >= 3) and (len(anchor) >= 2 or len(head) / max(1, hn) >= .6) and anchor and len(shared) >= 2 and quantities and len(head) >= 2 and len(head) / max(1, hn) >= .35 and len(content) >= 3:
        return .88
    shared_counts = {n for n in a['numbers'] & b['numbers'] if n.endswith(':number')
                     and Decimal(n.split(':')[0]) > 31 and not re.fullmatch(r'20\d\d:number', n)}
    if anchor and len(head) >= 2 and len(shared_counts) >= 2 and len(content) >= 5:
        return .86
    # A shared identifier (flight number etc.) plus a shared headline actor and
    # most of the headline words: same event, reworded.
    if len(anchor) >= 2 and shared_counts and len(head) >= 4 and len(head) / max(1, hn) >= .6:
        return .85
    # Same company in both headlines + the same distinctive product/programme
    # name in the text (29 Sep: "Nvidia's new agent-safety platform" vs
    # "Nvidia launches software to stop AI agents going rogue").
    if a['primary'] and a['primary'] == b['primary'] and a.get('names', set()) & b.get('names', set()):
        return .87
    body_overlap = len(content) / max(1, min(len(a['nouns']), len(b['nouns'])))
    head_entity_overlap = len(anchor) / max(1, min(len(a['head_entities']), len(b['head_entities'])))
    if head_entity_overlap >= .67 and len(shared) >= 3 and len(shared) / max(1, max(len(ea), len(eb))) >= .8 and len(head) >= 2 and len(head) / max(1, len(a['head'] | b['head'])) >= .3 and len(content) >= 5:
        return .84
    if anchor and len(head) >= 2 and len(shared) >= 4 and body_overlap >= .5:
        return .82
    return 0.0


def card_text(card):
    points = card.get('points') or []
    if isinstance(points, str):
        points = [points]
    return ' '.join(str(x) for x in [card.get('hook', ''), *points, card.get('summary', '')] if x)


def card_fp(card):
    return fingerprint(card.get('headline') or card.get('title') or card.get('line', ''),
                       card_text(card) or card.get('teaser') or card.get('summary', ''))


def candidate_fp(item):
    return fingerprint(item.get('title', ''), (item.get('teaser') or item.get('summary') or '')[:300])


def cards(edition):
    """Full cards in placement order. Rails/opportunities have separate rules."""
    if isinstance(edition.get('lead'), dict):
        yield edition['lead']
    yield from (c for c in edition.get('frontpage', []) or [] if isinstance(c, dict))
    for section in edition.get('sections', []) or []:
        yield from (c for c in section.get('stories', []) or [] if isinstance(c, dict))


def grounding(card, source_text):
    """Warn-only surface check; absence is a review cue, not proof of invention."""
    text = ' '.join([str(card.get('hook') or ''), *(
        card.get('points') if isinstance(card.get('points'), list) else [card.get('points') or ''])])
    source = clean(source_text).casefold()
    misses = ['number:' + n for n in sorted(numbers(text) - numbers(source))]
    misses += ['name:' + n for n in sorted(entities(text, sentence_initial=True))
               if not re.search(r'(?<!\w)' + re.escape(n) + r'(?!\w)', source)]
    return misses


def new_development(new, old):
    """Only quantified facts or explicit verdicts allow a matched follow-up."""
    # Dates and bare counts/versions are too weak to turn a rerun into news.
    def rounded(n):  # "Rs 64 crore" vs "Rs 63.8 crore" is not a new fact
        value, _, unit = n.partition(':')
        return any(unit == o.partition(':')[2] and abs(Decimal(value) - Decimal(o.partition(':')[0]))
                   <= Decimal(value) * Decimal('0.02') for o in old['numbers'])
    new_numbers = {n for n in new['focus_numbers'] - old['numbers']
                   if n.split(':')[-1] not in ('number', 'year', 'month', 'day', 'hour') and not rounded(n)}
    return bool(new_numbers or new['focus_verdicts'] - old['verdicts'])


def dedupe_cards(edition):
    """Return a copy and removal evidence; preserve lead > front > sections."""
    result = copy.deepcopy(edition)
    kept, dropped = [], []
    def keep(card):
        fp = card_fp(card)
        match = next((c for c, f in kept if (
            card.get('id') and card.get('id') == c.get('id')) or (
            card.get('url') and card.get('url') == c.get('url')) or same_story(fp, f) >= MATCH), None)
        if match is not None:
            dropped.append({'kept': match.get('id') or match.get('headline'),
                            'dropped': card.get('id') or card.get('headline')})
            return False
        kept.append((card, fp))
        return True
    if isinstance(result.get('lead'), dict):
        keep(result['lead'])
    result['frontpage'] = [c for c in result.get('frontpage', []) or [] if keep(c)]
    for sec in result.get('sections', []) or []:
        sec['stories'] = [c for c in sec.get('stories', []) or [] if keep(c)]
    return result, dropped


def cluster_candidates(sections, rank=None):
    """Collapse across sections, keeping the best source. No transitive chaining.

    All members must match the representative; A~B and B~C alone never merges C.
    Metadata includes duplicate ids so refs and fetched text can survive.
    """
    entries = [s for sec in sections if sec.get('slug') != 'opportunities'
               for s in sec.get('stories', [])]
    ordered = sorted(entries, key=rank, reverse=True) if rank else entries
    kept, aliases, events = [], {}, []
    for item in ordered:
        fp = candidate_fp(item)
        match = next((k for k, f in kept if item.get('id') == k.get('id') or same_story(fp, f) >= MATCH), None)
        if match is None:
            kept.append((item, fp))
            continue
        if item['id'] != match['id']:
            dupes = set(match.get('dupes', [])) | {item['id']} | set(item.get('dupes', []))
            match['dupes'] = sorted(dupes - {match['id']})
            aliases[item['id']] = match['id']
            events.append({'kept': match['id'], 'dropped': item['id']})
        outlets = {x.get('source', '').casefold() for x in entries
                   if x['id'] in {match['id'], *match.get('dupes', [])}}
        match['buzz'] = max(match.get('buzz', 1), len(outlets))
    survivors = {id(k) for k, _ in kept}
    for sec in sections:
        if sec.get('slug') != 'opportunities':
            sec['stories'] = [s for s in sec.get('stories', []) if id(s) in survivors]
    return aliases, events


def unique_selection(sel, digest):
    """Reject repeated story picks, backfill that section by editor score."""
    result = copy.deepcopy(sel)
    candidates = {s['id']: s for sec in digest.get('sections', [])
                  for s in sec.get('stories', [])}
    section_of = {s['id']: sec['slug'] for sec in digest.get('sections', [])
                  for s in sec.get('stories', [])}
    scores = sel.get('_scores') or {}
    fps = {i: candidate_fp(s) for i, s in candidates.items()}
    placed = []
    def unique(i):
        return i in fps and all(i != j and same_story(fps[i], fps[j]) < MATCH for j in placed)
    def take(i, backfill=True):
        if unique(i):
            placed.append(i)
            return i
        if backfill and i in section_of:
            pool = sorted((j for j in candidates if section_of[j] == section_of[i]
                           and not candidates[j].get('flags') and not candidates[j].get('seen')),
                          key=lambda j: -scores.get(j, 0))
            for j in pool:
                if unique(j):
                    placed.append(j)
                    return j
        return None
    if result.get('lead'):
        result['lead'] = take(result['lead'])
    result['frontpage'] = [j for i in result.get('frontpage', []) if (j := take(i))]
    for sec in result.get('sections', []):
        sec['stories'] = [j for i in sec.get('stories', []) if (j := take(i))]
    # Rails never reserve a position ahead of a full card.
    for sec in result.get('sections', []):
        sec['also'] = [j for i in sec.get('also', []) if (j := take(i, False))]
    result['longform'] = [j for i in result.get('longform', []) if (j := take(i, False))]
    result['dupes'] = {i: candidates[i]['dupes'] for i in candidates if candidates[i].get('dupes')}
    result['followups'] = [i for i in candidates if candidates[i].get('followup')]
    return result


def source_text(sid, stories, dupes=None):
    own = stories.get(sid) or {}
    ids = [sid, *own.get('dupes', []), *(dupes or {}).get(sid, [])]
    return '\n'.join((stories.get(i) or {}).get('fulltext') or '' for i in dict.fromkeys(ids))


def filter_repeats(edition, memory):
    """Final body-aware repeat check, also catches terse/missing RSS teasers."""
    result = copy.deepcopy(edition)
    dropped = []
    def keep(card):
        match = memory.check(card.get('headline', ''), card_text(card), card.get('url'))
        if match and not match['followup']:
            dropped.append({'kept': match['date'] + ': ' + match['headline'],
                            'dropped': card.get('id') or card.get('headline')})
            return False
        if match:
            card['followup'] = True
        return True
    if result.get('lead') and not keep(result['lead']):
        result['lead'] = None
    result['frontpage'] = [c for c in result.get('frontpage', []) if keep(c)]
    for sec in result.get('sections', []):
        sec['stories'] = [c for c in sec.get('stories', []) if keep(c)]
    # If a stale lead slipped through selection, preserve the best new card.
    if not result.get('lead'):
        if result['frontpage']:
            result['lead'] = result['frontpage'].pop(0)
        else:
            for sec in result.get('sections', []):
                if sec.get('stories'):
                    result['lead'] = sec['stories'].pop(0)
                    break
    return result, dropped
