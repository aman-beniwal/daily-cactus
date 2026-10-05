#!/usr/bin/env python3
"""Offline published/replay scorecard: quality_report.py DATE or START..END.

Reads origin/gh-pages and historical inputs with git show. Never checks out or
writes published editions, fetches articles, or calls a model. --json writes
pair-level evidence, including every digest merge, for manual review.
"""
import argparse
import copy
import contextlib
import io
import os
import tempfile
import datetime as dt
import json
import pathlib
import re
import subprocess

import editorial as ed
import storymatch as sm

ROOT = pathlib.Path(__file__).resolve().parents[1]


def git_json(ref, path, optional=False):
    run = subprocess.run(['git', 'show', f'{ref}:{path}'], cwd=ROOT, capture_output=True, text=True)
    if run.returncode:
        if optional:
            return None
        raise ValueError(run.stderr.strip())
    return json.loads(run.stdout)


def dates(spec):
    first, _, last = spec.partition('..')
    start, end = dt.date.fromisoformat(first), dt.date.fromisoformat(last or first)
    if end < start:
        raise ValueError('range end precedes start')
    return [str(start + dt.timedelta(days=i)) for i in range((end - start).days + 1)]


def historical_digests(wanted):
    found = {}
    commits = subprocess.check_output(['git', 'log', '--format=%H', '--', 'feeds/digest_lean.json'], cwd=ROOT, text=True)
    for commit in commits.splitlines():
        digest = git_json(commit, 'feeds/digest_lean.json', True)
        if digest and digest.get('date') in wanted and digest['date'] not in found:
            found[digest['date']] = (commit, digest)
        if len(found) == len(wanted):
            break
    return found


def pairs(edition):
    cs = list(sm.cards(edition))
    fps = [sm.card_fp(c) for c in cs]
    return [{'a': a.get('headline'), 'b': b.get('headline'), 'a_id': a.get('id'), 'b_id': b.get('id'),
             'score': sm.same_story(fps[i], fps[j])}
            for i, a in enumerate(cs) for j, b in enumerate(cs) if j > i
            and (sm.same_story(fps[i], fps[j]) >= sm.MATCH or
                 (a.get('url') and a.get('url') == b.get('url')))]


def memory_for(date, published):
    memory = ed.RecentMemory()
    for old, edition in published.items():
        if 0 < (dt.date.fromisoformat(date) - dt.date.fromisoformat(old)).days <= 7:
            for c in sm.cards(edition):
                memory.add(old, c.get('headline', ''), c.get('url'), text=sm.card_text(c))
    return memory


def cross_pairs(edition, published):
    result = []
    date = edition['date']
    for card in sm.cards(edition):
        fp = sm.card_fp(card)
        for old, e in published.items():
            if not 0 < (dt.date.fromisoformat(date) - dt.date.fromisoformat(old)).days <= 7:
                continue
            for past in sm.cards(e):
                pfp = sm.card_fp(past)
                if sm.same_story(fp, pfp) >= sm.MATCH or (card.get('url') and card.get('url') == past.get('url')):
                    result.append({'a': card['headline'], 'b': past['headline'], 'old_date': old,
                                   'followup': sm.new_development(fp, pfp)})
    return result


def identify(edition, refs, selected=None):
    out = copy.deepcopy(edition)
    by_url = {r.get('url'): sid for sid, r in refs.items() if r.get('url')}
    by_url.update({s.get('url'): sid for sid, s in (selected or {}).get('stories', {}).items() if s.get('url')})
    for card in sm.cards(out):
        if not card.get('id'):
            card['id'] = by_url.get(card.get('url'))
    return out


def filter_cards(edition, predicate):
    out = copy.deepcopy(edition)
    if out.get('lead') and not predicate(out['lead']):
        out['lead'] = None
    out['frontpage'] = [c for c in out.get('frontpage', []) if predicate(c)]
    for sec in out.get('sections', []):
        sec['stories'] = [c for c in sec.get('stories', []) if predicate(c)]
    return out


def other_counts(edition, previous):
    today = dt.date.fromisoformat(edition['date'])
    invalid = 0
    for opp in edition.get('opportunities', []):
        text = ' '.join(str(opp.get(k, '')) for k in ('name', 'when', 'summary'))
        e = {'title': text, 'summary': ''}
        from build_digest import extract_event_date
        when = ed._as_date(opp.get('when')) or extract_event_date(text, today.year)
        invalid += bool((when and when < today + dt.timedelta(days=2)) or
                        ed.score_opportunity(e, today) == float('-inf') or
                        re.search(r'\b(?:IIT|NIT|BITS|college|university|students? only|eDC)\b', text, re.I) or
                        (not ed.OPP_REMOTE.search(text) and re.search(r'\b(?:London|Paris|Houston|USA|Canada|Singapore|Dubai|Africa)\b', text, re.I)))
    old_quotes = {q['label']: q for q in (previous.get('markets') or {}).get('quotes', [])}
    suspect = sum(1 for q in (edition.get('markets') or {}).get('quotes', [])
                  if q.get('label') in old_quotes and q.get('value') == old_quotes[q['label']].get('value')
                  and q.get('change_pct') != old_quotes[q['label']].get('change_pct'))
    return {'opportunities_flagged': invalid, 'market_unchanged_price_changed_pct': suspect}


def measure(edition, selected, published, previous, dupe_map=None):
    miss = []
    for card in sm.cards(edition):
        source = sm.source_text(card.get('id'), selected.get('stories', {}), dupe_map)
        miss.append({'id': card.get('id'), 'headline': card.get('headline'),
                     'source_missing': not bool(source), 'unsupported': sm.grounding(card, source)})
    same, cross = pairs(edition), cross_pairs(edition, published)
    repeats = {p['a'] for p in cross if not p['followup']}
    return {'cards': len(list(sm.cards(edition))), 'same_day_pairs': len(same),
            'cross_day_repeats': len(repeats), 'cross_day_matches': len({p['a'] for p in cross}),
            'grounding_misses': sum(len(m['unsupported']) for m in miss),
            'missing_sources': sum(m['source_missing'] for m in miss),
            **other_counts(edition, previous), 'same_pairs': same, 'cross_pairs': cross, 'grounding': miss}


def assemble_replay(draft, refs, selected, published):
    """Run the real assembler, redirecting all reads/writes to a temp fixture."""
    import assemble_edition as ae
    attrs = ('ROOT', 'REFS_DIR', 'REFS_FILE_LEGACY', 'OUT_DIR', 'EXISTING_EDITIONS_DIR')
    saved = {key: getattr(ae, key) for key in attrs}
    env_keys = ('DC_SELECTED_DIR', 'DC_QUALITY_DIR', 'EDITIONS_DIR')
    old_env = {key: os.environ.get(key) for key in env_keys}
    try:
        with tempfile.TemporaryDirectory(prefix='cactus-replay-') as temporary:
            root = pathlib.Path(temporary)
            for name in ('refs', 'selected', 'quality', 'past'):
                (root / name).mkdir()
            date = draft['date']
            path = root / f'{date}.json'
            path.write_text(json.dumps(draft))
            (root / 'refs' / f'{date}.json').write_text(json.dumps(refs))
            (root / 'selected' / f'{date}.json').write_text(json.dumps(selected))
            for old, edition in published.items():
                (root / 'past' / f'{old}.json').write_text(json.dumps(edition))
            ae.ROOT, ae.REFS_DIR = root, root / 'refs'
            ae.REFS_FILE_LEGACY, ae.OUT_DIR = root / 'no-legacy.json', root / 'out'
            ae.EXISTING_EDITIONS_DIR = root / 'past'
            os.environ.update(DC_SELECTED_DIR=str(root / 'selected'), DC_QUALITY_DIR=str(root / 'quality'),
                              EDITIONS_DIR=str(root / 'past'))
            with contextlib.redirect_stdout(io.StringIO()):
                out = ae.assemble_one(path, {}, '', draft.get('markets'))
            return json.loads(out.read_text()), json.loads((root / 'quality' / f'{date}.json').read_text())['quality']
    finally:
        for key, value in saved.items():
            setattr(ae, key, value)
        for key, value in old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def report(spec):
    wanted = dates(spec)
    span = dates(str(dt.date.fromisoformat(wanted[0]) - dt.timedelta(days=7)) + '..' + wanted[-1])
    published = {d: e for d in span if (e := git_json('origin/gh-pages', f'editions/{d}.json', True))}
    digests = historical_digests(wanted)
    rows = []
    for date in wanted:
        if date not in published or date not in digests:
            raise ValueError(f'{date}: missing published edition or historical digest')
        commit, digest = digests[date]
        refs = git_json(commit, f'feeds/refs/{date}.json')
        writer_commit = subprocess.check_output(['git', 'log', '-1', '--format=%H', '--', f'drafts/{date}.json'], cwd=ROOT, text=True).strip()
        selected = git_json(writer_commit, f'feeds/selected/{date}.json')
        draft = git_json(writer_commit, f'drafts/{date}.json')
        before = identify(published[date], refs, selected)
        prev_date = str(dt.date.fromisoformat(date) - dt.timedelta(days=1))
        prev = published.get(prev_date, {})
        before_score = measure(before, selected, published, prev)
        memory = memory_for(date, published)
        clustered = copy.deepcopy(digest)
        original = {s['id']: s for sec in digest['sections'] for s in sec.get('stories', [])}
        # Same source preference used by the fetch ranker; no network calls.
        from build_digest import source_weight
        aliases, merges = sm.cluster_candidates(clustered['sections'], rank=lambda s: source_weight(s.get('source', '')) + ed.pr_penalty(ed.pr_cues(s)))
        dupe_map = {}
        for pair in merges:
            pair['a'] = original[pair['kept']]['title']
            pair['b'] = original[pair['dropped']]['title']
            group = [pair['kept'], pair['dropped']]
            for i in group:
                dupe_map.setdefault(i, set()).update(group)
        # Expand each member to all siblings, preserving each card's own source.
        for i, ds in list(dupe_map.items()):
            dupe_map[i] = sorted(set(ds).union(*(dupe_map.get(d, set()) for d in ds)) - {i})
        repeated, followups, candidate_repeats = set(), set(), []
        for sid, s in original.items():
            r = memory.check(s['title'], s.get('teaser', ''), refs.get(sid, {}).get('url'))
            if r:
                (followups if r['followup'] else repeated).add(sid)
                candidate_repeats.append({'id': sid, 'title': s['title'], **r})
        # Do not pretend to write replacement cards without a writer call.
        replay = identify(draft, refs)
        for c in sm.cards(replay):
            c['url'] = refs.get(c.get('id'), {}).get('url')
            if c.get('id') in followups:
                c['followup'] = True
        replay = filter_cards(replay, lambda c: c.get('id') not in repeated)
        # Candidate groups also remove differently-worded draft cards.
        used = set()
        def once(c):
            key = aliases.get(c.get('id'), c.get('id'))
            if key and key in used:
                return False
            used.add(key)
            return True
        replay = filter_cards(replay, once)
        replay['markets'] = before.get('markets', {})
        replay_refs = copy.deepcopy(refs)
        for sid, ds in dupe_map.items():
            if sid in replay_refs:
                replay_refs[sid]['dupes'] = ds
        replay, quality = assemble_replay(replay, replay_refs, selected, published)
        drops = quality['duplicate_drops'] + quality['repeat_drops']
        rows.append({'date': date, 'digest_commit': commit, 'writer_commit': writer_commit,
                     'before': before_score, 'after': measure(replay, selected, published, prev, dupe_map),
                     'candidate_merges': merges, 'candidate_repeats': candidate_repeats,
                     'final_drops': drops, 'assembly_quality': quality})
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('date', help='YYYY-MM-DD or YYYY-MM-DD..YYYY-MM-DD')
    parser.add_argument('--json', type=pathlib.Path, help='write detailed evidence to this file')
    args = parser.parse_args()
    rows = report(args.date)
    print('| Date | Cards B/A | Same-day pairs B/A | Cross-day repeats B/A | Grounding misses B/A | Opp flags B/A | Market flags B/A |')
    print('|---|---:|---:|---:|---:|---:|---:|')
    for row in rows:
        keys = ['cards', 'same_day_pairs', 'cross_day_repeats', 'grounding_misses', 'opportunities_flagged', 'market_unchanged_price_changed_pct']
        print('| ' + row['date'] + ' | ' + ' | '.join(f"{row['before'][k]}/{row['after'][k]}" for k in keys) + ' |')
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(rows, indent=2, ensure_ascii=False) + '\n')


if __name__ == '__main__':
    main()
