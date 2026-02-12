import csv
import glob
import os
import zipfile
import re
import xml.etree.ElementTree as ET

def detect_encoding(path):
    with open(path, 'rb') as f:
        start = f.read(4)

    # UTF‑16 LE BOM
    if start.startswith(b'\xff\xfe'):
        return 'utf-16-le'

    # UTF‑16 BE BOM
    if start.startswith(b'\xfe\xff'):
        return 'utf-16-be'

    # UTF‑8 BOM
    if start.startswith(b'\xef\xbb\xbf'):
        return 'utf-8-sig'

    # Fallback
    return 'utf-8'

def get_cell_text(cell_element, shared_strings, ns):
    cell_type = cell_element.attrib.get('t')
    v = cell_element.find('a:v', ns)
    if v is None:
        return ""
    if cell_type == 's':  # shared string
        idx = int(v.text)
        return shared_strings[idx] if idx < len(shared_strings) else ""
    return v.text

def read_xlsx(path):
    """
    Minimal XLSX reader: returns a list of values from the first column.
    Assumes the first sheet and simple text cells.
    """
    with zipfile.ZipFile(path, 'r') as z:
        # Shared strings (string table)
        shared_strings = []
        if 'xl/sharedStrings.xml' in z.namelist():
            xml = z.read('xl/sharedStrings.xml')
            root = ET.fromstring(xml)
            for si in root.findall('.//{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t'):
                shared_strings.append(si.text)

        # Worksheet data
        sheet_name = 'xl/worksheets/sheet1.xml'
        if sheet_name not in z.namelist():
            return []


	# Parse the sheet XML as you already do
        xml = z.read(sheet_name)
        root = ET.fromstring(xml)
        ns = {'a': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}


        # Collect E and F per row
        rows = {}  # row_index(int) -> {'E': val, 'F': val}
        ref_re = re.compile(r'^([A-Z]+)(\d+)$')

        for row in root.findall('.//a:row', ns):
            for c in row.findall('a:c', ns):
                ref = c.attrib.get('r', '')  # e.g., "E12"
                m = ref_re.match(ref)
                if not m:
                    continue
                col_letters, row_idx_str = m.group(1), m.group(2)
                if col_letters not in ('E', 'F'):
                    continue
                row_idx = int(row_idx_str)
                val = get_cell_text(c, shared_strings, ns)
                if row_idx not in rows:
                    rows[row_idx] = {}
                rows[row_idx][col_letters] = val

        # Build a list of (E, F) tuples in ascending row order
        ef_tuples = []
        for r in sorted(rows.keys()):
            e_val = rows[r].get('E', "").strip()
            f_val = rows[r].get('F', "").strip()
            ef_tuples.append((e_val, f_val))
        # ef_tuples is now: [(E_row1, F_row1), (E_row2, F_row2), ...]
        return ef_tuples


def main():
    folder = os.path.dirname(os.path.abspath(__file__))

    # --- Find attendance CSV ---
    csv_files = glob.glob(os.path.join(folder, "*.csv"))
    if not csv_files:
        print("No attendance CSV found.")
        return

    attendance_file = csv_files[0]

    # --- Read attendance CSV ---
    encoding = detect_encoding(attendance_file)
    attendance = {}  # name -> time
    with open(attendance_file, newline='', encoding=encoding) as f:
        reader = csv.reader(f, delimiter='\t')
        header = next(reader, None)
        for row in reader:
            if len(row) < 5:
                continue
            name = row[0].strip()
            time = row[3].strip()
            attendance[name] = time

    # --- Read Polls XLSX files ---
    poll_files = [
        f for f in glob.glob(os.path.join(folder, "*.xlsx"))
        if not f.lower().endswith(".xlsm")
    ]

    poll_data = []  # list of sets of names
    for file in poll_files:
        cols_ef = read_xlsx(file)
        pollanswers = {}

        for e, f in cols_ef[1:]:
            if f is None:
                f = ""  # optional: normalize None to empty string
            if e in pollanswers:                  # already has something -> append with comma
                pollanswers[e] += ", " + f
            else:                     # first value -> just set it
                pollanswers[e] = f
        poll_data.append(pollanswers)

    # --- Build output ---
    poll_count = len(poll_data)
    poll_headers = [f"Poll{i+1}" for i in range(poll_count)]

    output_rows = []
    for name, time in attendance.items():
        row = [name, time]
        poll_sum = 0
        for poll_set in poll_data:
            if name in poll_set:
                row.append(poll_set[name])
                poll_sum += 1
            else:
                row.append("")
        row.insert(2, poll_sum)  # Insert PollSum after time
        output_rows.append(row)

    # --- Write combined.csv ---
    output_file = os.path.join(folder, "combined.csv")
    with open(output_file, "w", newline='', encoding='utf-8') as f:
        writer = csv.writer(f,delimiter=';')
        writer.writerow(["Name", "AttendanceTime", "PollSum"] + poll_headers)
        writer.writerows(output_rows)

    print(f"Done! Output saved to: {output_file}")


if __name__ == "__main__":
    main()

