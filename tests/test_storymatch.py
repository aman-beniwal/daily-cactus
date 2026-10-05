import copy
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'scripts'))
import storymatch as sm
import newsroom as nr


def candidate(sid, title, teaser=''):
    return {'id': sid, 'title': title, 'teaser': teaser}


def test_units_and_possessives():
    assert sm.numbers('₹450 crore') == sm.numbers('450 cr') == sm.numbers('Rs 450 crore')
    assert sm.numbers('$8.2 billion') == sm.numbers('$8.2B')
    assert sm.numbers('20 workers') != sm.numbers('20%')
    assert sm.entities("Nvidia’s deal with OpenAI") == sm.entities('Nvidia deal with OpenAI')


def test_distinct_company_events_survive():
    a = sm.fingerprint('Nvidia buys Orion for $8 billion', 'Nvidia CEO Jensen Huang announced the acquisition.')
    b = sm.fingerprint('Nvidia reports $8 billion revenue', 'Nvidia CEO Jensen Huang reported results.')
    assert sm.same_story(a, b) < sm.MATCH


def test_grounding_checks_own_text_and_quantity_units():
    card = {'hook': 'The deal gives Quanastra Rs 450 crore.',
            'points': ['Partners include NEURA and TKMS, with 12,000 managers affected.',
                       'On Monday the CEO discussed AI with the RBI.']}
    misses = sm.grounding(card, 'Quanastra received ₹450 Cr. There are 12,000 users.')
    assert 'name:neura' in misses and 'name:tkms' in misses
    assert 'number:12000:manager' in misses
    assert not any('4500000000' in m or m in ('name:monday', 'name:ceo', 'name:rbi') for m in misses)


def test_backfill_and_fallback_remove_reworded_story():
    a = candidate('ai-a', 'IIT Madras Unicorn raises ₹450 crore', 'IIT Madras Unicorn fund first close.')
    b = candidate('india-b', 'Unicorn and IIT Madras secure Rs 450 cr', 'The IIT Madras Unicorn fund closed.')
    c = candidate('india-c', 'Skyroot Vikram rocket reaches orbit')
    digest = {'sections': [{'slug': 'ai', 'stories': [a]}, {'slug': 'india-deep-tech', 'stories': [b, c]}]}
    sel = nr.validate_selection({'lead': 'ai-a', 'frontpage': ['india-b']}, digest)
    assert sel['frontpage'] == ['india-c']
    fallback = nr.code_ranked_selection(digest)
    assert fallback['frontpage'] == ['india-c']


def test_followup_needs_new_focus_fact_not_background_number():
    old = sm.fingerprint('AMD buys World Labs for $8.2B', 'AMD agreed to buy World Labs for $8.2 billion.')
    repeat = sm.fingerprint('AMD buys World Labs for $8.2B', 'AMD agreed to buy World Labs for $8.2 billion. The startup raised $230 million in 2024.')
    new = sm.fingerprint('AMD buys World Labs for $9B', 'AMD increased its offer to $9 billion.')
    assert not sm.new_development(repeat, old)
    assert sm.new_development(new, old)


def test_final_guard_keeps_placement_and_rails():
    card = {'id': 'a', 'headline': 'Nvidia launches OpenShell', 'hook': 'Nvidia launches OpenShell.'}
    edition = {'lead': card, 'frontpage': [dict(card, id='b')],
               'sections': [{'slug': 'ai', 'stories': [copy.deepcopy(card)], 'also': [{'line': 'Elsewhere'}]}]}
    out, dropped = sm.dedupe_cards(edition)
    assert out['lead'] == card and not out['frontpage'] and len(dropped) == 2
    assert out['sections'][0]['also'] == [{'line': 'Elsewhere'}]
    assert len(edition['frontpage']) == 1


def test_cluster_fetch_writer_assembly_round_trip(tmp_path, monkeypatch):
    import datetime
    import build_digest as bd
    import editorial as ed
    import fetch_selected as fs
    import assemble_edition as ae

    monkeypatch.setenv('DC_OFFLINE', '1')
    now = datetime.datetime(2026, 10, 5, tzinfo=datetime.timezone.utc)
    raw = [{'slug': 'ai', 'name': 'AI', 'entries': [
        {'title': 'IIT Madras Unicorn raises ₹450 crore', 'source': 'Reuters',
         'link': 'https://example.test/one', 'summary': 'IIT Madras Unicorn received ₹450 crore.', 'published': now.isoformat()}]},
        {'slug': 'india-deep-tech', 'entries': [
         {'title': 'IIT Madras Unicorn secures Rs 450 cr', 'source': 'Another outlet',
          'link': 'https://example.test/two', 'summary': 'IIT Madras Unicorn supports Quanastra.', 'published': now.isoformat()}]}]
    sections, refs, *_ = bd.shortlist_all(raw, now, 2026, set(), set(), ed.RecentMemory(), None)
    assert sum(len(s['stories']) for s in sections) == 1
    primary = sections[0]['stories'][0]
    sibling = primary['dupes'][0]
    assert sibling in refs and primary['buzz'] == 2
    digest = {'date': '2026-10-05', 'sections': sections}
    sel = nr.validate_selection({'lead': primary['id']}, digest)
    sels = tmp_path / 'selections'; sels.mkdir()
    selected_dir = tmp_path / 'feeds' / 'selected'; selected_dir.mkdir(parents=True)
    (sels / '2026-10-05.json').write_text(json.dumps(sel))
    monkeypatch.setattr(fs, 'SELECTIONS_DIR', sels)
    monkeypatch.setattr(fs, 'SELECTED_DIR', selected_dir)
    monkeypatch.setattr(fs, 'ROOT', tmp_path)
    monkeypatch.setattr(fs, 'load_refs_for_date', lambda date: refs)
    monkeypatch.setattr(fs, 'load_digest_fallback', lambda: {})
    monkeypatch.setattr(fs, 'fetch_extract_with_image', lambda url, **kw: ('IIT Madras Unicorn received ₹450 crore. ' * 20 if url.endswith('one') else 'Quanastra is a portfolio company. ' * 20, None))
    monkeypatch.setattr(sys, 'argv', ['fetch_selected.py', '2026-10-05'])
    fs.main()
    selected = json.loads((selected_dir / '2026-10-05.json').read_text())
    assert sibling in selected['stories']
    assert 'Quanastra' in nr.writer_input(sel, selected)
    draft = {'date': '2026-10-05', 'lead': {'id': primary['id'], 'headline': primary['title'],
             'hook': 'The fund backs Quanastra and NEURA.', 'points': ['It received Rs 450 cr.']},
             'frontpage': [], 'sections': []}
    draft['frontpage'] = [copy.deepcopy(draft['lead'])]
    path = tmp_path / '2026-10-05.json'; path.write_text(json.dumps(draft))
    monkeypatch.setattr(ae, 'load_refs_for_date', lambda date: refs)
    monkeypatch.setattr(ae, 'OUT_DIR', tmp_path / 'editions')
    monkeypatch.setattr(ae, 'EXISTING_EDITIONS_DIR', tmp_path / 'prior')
    monkeypatch.setenv('DC_SELECTED_DIR', str(selected_dir))
    monkeypatch.delenv('EDITIONS_DIR', raising=False)
    out = json.loads(ae.assemble_one(path, {}, '', None).read_text())
    assert not out['frontpage'] and 'NEURA' in out['lead']['hook']  # WARN only
    quality = json.loads((selected_dir.parent / 'quality' / '2026-10-05.json').read_text())['quality']
    assert len(quality['duplicate_drops']) == 1
    misses = quality['grounding'][0]['unsupported']
    assert 'name:neura' in misses and 'name:quanastra' not in misses


def test_repeat_memory_uses_published_bodies_with_exact_seven_day_window(tmp_path, monkeypatch):
    import datetime
    import build_digest as bd
    import assemble_edition as ae
    card = {'headline': 'AMD buys World Labs for $8.2B', 'hook': 'AMD bought World Labs for $8.2 billion.'}
    for date in ['2026-09-27', '2026-09-28', '2026-10-05', '2026-10-06']:
        (tmp_path / f'{date}.json').write_text(json.dumps({'lead': card}))
    monkeypatch.setattr(bd, 'EDITIONS_DIR', str(tmp_path))
    monkeypatch.setattr(bd, 'REFS_DIR', tmp_path / 'absent')
    # UTC is still the previous date; the edition window is the IST date.
    now = datetime.datetime(2026, 10, 4, 23, 30, tzinfo=datetime.timezone.utc)
    _, _, mem, ok = bd.load_published_urls(now)
    assert ok and len(mem) == 1
    repeat = mem.check('AMD buys World Labs for $8.2B', card['hook'])
    assert repeat['date'] == '2026-09-28' and not repeat['followup']
    newer = mem.check('AMD buys World Labs for $9B', 'AMD bought World Labs for $9 billion.')
    assert newer['followup']
    monkeypatch.setenv('EDITIONS_DIR', str(tmp_path))
    assert len(ae.published_memory('2026-10-05')) == 1


def test_fallback_front_backfill_uses_best_section_score():
    a = candidate('ai-a', 'IIT Madras Unicorn raises ₹450 crore', 'IIT Madras Unicorn first close.')
    b = candidate('deep-b', a['title'], a['teaser'])
    c = candidate('deep-c', 'Skyroot Vikram reaches orbit')
    d = candidate('deep-d', 'GalaxEye launches Drishti satellite')
    digest = {'sections': [{'slug': 'ai', 'stories': [a]}, {'slug': 'deep-tech', 'stories': [b, c, d]}]}
    sel = nr.validate_selection({'lead': 'ai-a', 'frontpage': ['deep-b'],
                                'scores': {'deep-c': [1], 'deep-d': [5]}}, digest)
    assert sel['frontpage'] == ['deep-d']


def test_reworded_published_pairs_match_and_rounding_is_not_new():
    a = sm.fingerprint('Pilots body seeks role in probe review into Air India AI 171 crash',
                       'The Federation of Indian Pilots asked the ministry to consider its submissions on the AI 171 crash report.')
    b = sm.fingerprint("Air India AI-171 crash: Pilots' body seeks representation on panel reviewing draft final probe report",
                       'The Federation of Indian Pilots (FIP) wrote to the civil aviation minister about the AI 171 report.')
    assert sm.same_story(a, b) >= sm.MATCH
    old = sm.fingerprint('GalaxEye gets Rs 64 crore funding', 'GalaxEye got Rs 64 crore.')
    new = sm.fingerprint('GalaxEye gets ₹63.8 Cr funding', 'GalaxEye got ₹63.8 Cr.')
    assert not sm.new_development(new, old)


def test_shared_product_name_same_company():
    # 29 Sep 2026: the same Nvidia launch written up twice in one paper
    a = sm.fingerprint("Nvidia's new agent-safety platform, as covered in India",
                       "Nvidia's Open Agent Safety Platform, unveiled Monday, is designed to stop AI agents.")
    b = sm.fingerprint('Nvidia launches software to stop AI agents going rogue',
                       'Nvidia unveiled the Open Agent Safety Platform, an open-source tool.')
    assert sm.same_story(a, b) >= sm.MATCH
