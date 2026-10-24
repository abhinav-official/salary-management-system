"""Small XLSX acceptance fixtures; production parsing remains API-owned."""

from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

NS = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
HEADERS = ['Employee ID', 'Name', 'Email', 'Department', 'Level', 'Country', 'Annual Salary']


def workbook(path: Path, sheets):
    with ZipFile(path, 'w') as archive:
        archive.writestr('[Content_Types].xml', '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/></Types>')
        root = ET.Element(NS + 'workbook')
        collection = ET.SubElement(root, NS + 'sheets')
        rels = ET.Element('Relationships', xmlns='http://schemas.openxmlformats.org/package/2006/relationships')
        for index, (name, rows) in enumerate(sheets.items(), 1):
            ET.SubElement(collection, NS + 'sheet', {'name': name, 'sheetId': str(index), '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id': f'rId{index}'})
            ET.SubElement(rels, 'Relationship', Id=f'rId{index}', Target=f'worksheets/sheet{index}.xml', Type='http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet')
            sheet = ET.Element(NS + 'worksheet')
            data = ET.SubElement(sheet, NS + 'sheetData')
            for number, values in rows.items():
                row = ET.SubElement(data, NS + 'row', r=str(number))
                for column, value in enumerate(values, 1):
                    if value is None:
                        continue
                    cell = ET.SubElement(row, NS + 'c', r=f'{chr(64+column)}{number}', t='inlineStr')
                    if isinstance(value, tuple):
                        cell.set('t', 'n')
                        ET.SubElement(cell, NS + 'f').text = value[0]
                        ET.SubElement(cell, NS + 'v').text = value[1]
                    else:
                        ET.SubElement(ET.SubElement(cell, NS + 'is'), NS + 't').text = value
            archive.writestr(f'xl/worksheets/sheet{index}.xml', ET.tostring(sheet))
        archive.writestr('xl/workbook.xml', ET.tostring(root))
        archive.writestr('xl/_rels/workbook.xml.rels', ET.tostring(rels))


def write_fixtures(directory: Path):
    valid_rows = {3: HEADERS, 5: ['UI-IMPORT-0001', 'Imported One', 'ui-import-0001@example.com', 'Imported Operations', 'L1', 'India', '123.455'],
                  9: ['UI-IMPORT-0002', 'Imported Two', 'ui-import-0002@example.com', 'Imported Operations', 'L8', 'US', '100.00']}
    workbook(directory / 'valid.xlsx', {'Employees': valid_rows, 'Notes': {1: ['Choose Employees for import']}})
    for prefix in ('stale', 'busy'):
        rows = {3: HEADERS}
        for number in (5, 9):
            values = list(valid_rows[number])
            values[0] = prefix.upper() + '-' + values[0]
            values[2] = prefix + '-' + values[2]
            rows[number] = values
        workbook(directory / f'{prefix}.xlsx', {'Employees': rows})
    invalid = {1: HEADERS}
    for number in range(2, 122):
        invalid[number] = [f'INVALID-{number}', 'Invalid Employee', f'invalid-{number}@example.com', 'Imported Operations', 'L1', 'India', '-0.001']
    invalid[2][1:3] = [None, 'invalid-email']
    invalid[5][0:3:2] = ['BAD-DUP', 'duplicate@example.com']
    invalid[6][0:3:2] = ['bad-dup', 'DUPLICATE@EXAMPLE.COM']
    invalid[8][-1] = ('1+1', '2')
    invalid[120][0:3:2] = ['emp00000', 'PERSON00000@EXAMPLE.COM']
    workbook(directory / 'invalid.xlsx', {'Employees': invalid})
    workbook(directory / 'headers.xlsx', {'Employees': {4: [*HEADERS[:-1], 'email'], 8: valid_rows[5]}})
    (directory / 'corrupt.xlsx').write_bytes(b'This is not a workbook')
