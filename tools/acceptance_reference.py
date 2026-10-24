"""Independent acceptance oracle: stdlib OOXML, rational arithmetic, integer rounding.

Deliberately imports no application, generator, database, or production validators.
"""
from collections import Counter, defaultdict
from decimal import Decimal
from fractions import Fraction
from xml.etree import ElementTree as ET
from zipfile import ZipFile

NS = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}


def workbook(path):
    with ZipFile(path) as archive:
        strings = [''.join(item.itertext()) for item in ET.fromstring(archive.read('xl/sharedStrings.xml'))]
        def sheet(number):
            rows = []
            for row in ET.fromstring(archive.read(f'xl/worksheets/sheet{number}.xml')).findall('.//s:sheetData/s:row', NS):
                values = []
                for cell in row:
                    value = cell.find('s:v', NS).text
                    values.append(strings[int(value)] if cell.get('t') == 's' else value)
                rows.append(values)
            return [dict(zip(rows[0], row)) for row in rows[1:]]
        employees, bands = sheet(1), sheet(2)
    for row in employees:
        row['minor'] = int(Decimal(row['Annual Salary']) * 100)
    for row in bands:
        for key in ('Minimum', 'Midpoint', 'Maximum'):
            row[key] = int(Decimal(row[key]) * 100)
    return employees, {(r['Country'], r['Level']): r for r in bands}


def display(value):
    value = Fraction(value)
    negative = value < 0
    cents, remainder = divmod(abs(value.numerator) * 100, value.denominator)
    cents += remainder * 2 >= value.denominator
    return ('-' if negative else '') + f'{cents // 100}.{cents % 100:02d}'


def median(values):
    ordered = sorted(values)
    n = len(ordered)
    return Fraction(ordered[n // 2]) if n % 2 else Fraction(ordered[n // 2 - 1] + ordered[n // 2], 2)


def statistic(value):
    value = Fraction(value)
    return {'minor_numerator': str(value.numerator), 'minor_denominator': str(value.denominator),
            'amount': display(value / 100)}


def verify(employees, bands, summaries, compa, equity):
    assert summaries['total_count'] == compa['total_count'] == equity['total_count'] == len(employees)
    for grouping in ('countries', 'departments', 'levels'):
        assert sum(g['count'] for g in summaries[grouping]) == len(employees)
        for group in summaries[grouping]:
            selected = [e['minor'] for e in employees if e['Country'] == group['country']
                        and (grouping != 'departments' or e['Department'].casefold() == group['department_key'])
                        and (grouping != 'levels' or e['Level'] == group['level'])]
            assert group['currency'] == next(b['Currency'] for b in bands.values() if b['Country'] == group['country'])
            assert group['count'] == len(selected)
            expected = dict.fromkeys(('average', 'median', 'minimum', 'maximum', 'spread'))
            if selected:
                expected = dict(average=Fraction(sum(selected), len(selected)), median=median(selected),
                                minimum=min(selected), maximum=max(selected), spread=max(selected)-min(selected))
            for key, value in expected.items():
                assert group[key] == (statistic(value) if value is not None else None), (grouping, group, key)
    buckets = [0, 0, 0]
    peers = defaultdict(list)
    for employee in employees:
        key = employee['Country'], employee['Level']
        midpoint = bands[key]['Midpoint']
        ratio = Fraction(employee['minor'], midpoint)
        buckets[0 if ratio < Fraction(4, 5) else 2 if ratio > Fraction(6, 5) else 1] += 1
        peers[key].append(employee)
    assert compa['eligible_count'] == len(employees) and compa['excluded_unbanded_count'] == 0
    assert [b['count'] for b in compa['buckets']] == buckets
    assert [b['percentage'] for b in compa['buckets']] == [display(Fraction(n * 100, len(employees))) for n in buckets]
    targets = {e['Employee ID']: e for e in employees}
    assert equity['available_count'] == len(employees)
    assert equity['excluded_unbanded_count'] == equity['insufficient_peers_count'] == equity['zero_peer_median_count'] == 0
    assert len(equity['items']) == len(targets)
    for item in equity['items']:
        target = targets.pop(item['employee_id'])
        # Independent deliberately simple reference: remove by identity and sort anew.
        others = [e['minor'] for e in peers[target['Country'], target['Level']] if e['Employee ID'] != target['Employee ID']]
        peer_median = median(others)
        assert item['peer_count'] == len(others) and item['peer_median'] == statistic(peer_median)
        assert item['percentage_difference'] == display((target['minor'] - peer_median) / peer_median * 100)
        assert item['salary_minor'] == str(target['minor']) and item['state'] == 'available'
        assert item['country'] == target['Country'] and item['level'] == target['Level']
        assert item['currency'] == bands[target['Country'], target['Level']]['Currency']
        assert item['salary_amount'] == display(Fraction(target['minor'], 100))
    assert not targets
    positions = Counter('below' if e['minor'] < bands[e['Country'], e['Level']]['Minimum'] else
                        'above' if e['minor'] > bands[e['Country'], e['Level']]['Maximum'] else 'inside' for e in employees)
    assert positions == {'inside': 9500, 'below': 250, 'above': 250}
    assert Counter(e['Country'] for e in employees) == {'India': 4000, 'US': 2500, 'UK': 1200, 'Germany': 1000, 'Singapore': 800, 'UAE': 500}
