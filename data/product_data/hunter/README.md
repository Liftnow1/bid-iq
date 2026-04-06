# Hunter PDF Library - Optimizely CMP Export

## What's Here

- **`Optimizely_CMP_PDF_Inventory.xlsx`** — Master inventory mapping 399 PDFs to their 65 parent collections in Optimizely CMP
- **`pdfs/`** — Flat download of all 705 PDFs from the global Optimizely CMP library (includes PDFs not assigned to any collection)

## Spreadsheet Structure

### Sheet 1: "PDF Inventory"
| Column | Description |
|--------|-------------|
| # | Row number |
| Collection Name | The Optimizely CMP collection this PDF belongs to |
| PDF Filename | The full filename of the PDF |

- Header row is frozen for easy scrolling
- Filterable by collection name
- 399 PDFs across 37 collections

### Sheet 2: "Collection Summary"
| Column | Description |
|--------|-------------|
| Collection Name | All 65 collections |
| PDF Count | Number of PDFs in each collection |
| Has PDFs? | YES/NO flag |

- Color-coded: green = has PDFs, red = no PDFs
- Totals row at bottom

## How to Organize PDFs into Collection Subfolders

The PDFs in `pdfs/` are flat-downloaded (no folder structure). Use the spreadsheet to organize them into collection-based subfolders.

### Option 1: Python Script

```python
import openpyxl
import shutil
import os

# Paths
xlsx_path = "Optimizely_CMP_PDF_Inventory.xlsx"
pdf_source = "pdfs"
pdf_dest = "organized"

wb = openpyxl.load_workbook(xlsx_path)
ws = wb["PDF Inventory"]

for row in ws.iter_rows(min_row=2, values_only=True):
    row_num, collection, filename = row
    if not filename or filename == "__NONE__":
        continue

    # Sanitize collection name for folder
    safe_collection = collection.replace("/", "-").replace("\\", "-").strip()
    dest_dir = os.path.join(pdf_dest, safe_collection)
    os.makedirs(dest_dir, exist_ok=True)

    src = os.path.join(pdf_source, filename)
    if os.path.exists(src):
        shutil.copy2(src, os.path.join(dest_dir, filename))
        print(f"  Copied: {filename} -> {safe_collection}/")
    else:
        # Try matching without extension or with slight name variations
        print(f"  NOT FOUND: {filename}")

print("Done!")
```

### Option 2: AI Prompt

If you're using Claude or another AI assistant with file access, paste this prompt:

> I have a folder called `pdfs/` containing ~705 flat-downloaded PDF files, and an Excel spreadsheet called `Optimizely_CMP_PDF_Inventory.xlsx` that maps 399 of those PDFs to their parent collections (65 collections total, 37 with PDFs).
>
> Please read the spreadsheet and organize the PDFs from `pdfs/` into subfolders named after their collections. PDFs not listed in the spreadsheet can stay in a separate `uncategorized/` folder. The spreadsheet has two sheets — use "PDF Inventory" which has columns: #, Collection Name, PDF Filename.

## Notes

- **Duplicate PDFs**: Some PDFs appear in multiple collections. The script above will copy (not move) so duplicates are preserved in each collection folder.
- **Missing `.pdf` extension**: A few filenames in the CMP don't end in `.pdf` (e.g., `BB08321-00 Road Force® WalkAway™ Brochure`). These were downloaded with `.pdf` appended.
- **Special characters**: Some filenames contain `®`, `™`, `+`, `()` — these are URL-decoded in the downloaded files.
- **705 vs 399**: The global library has 705 downloadable PDFs; only 399 are assigned to collections. The remaining ~306 are uncategorized.
- **3 unavailable files**: 3 PDFs returned HTTP 403 (forbidden/expired) and could not be downloaded: `AAPEX-NPS-RFW_18x18.pdf`, `Bush-MobileService-SEMAposter_24x36_20251013.pdf`, `SEMA-UADAS-Pullup.pdf`

## Source

- **URL**: https://cmp.optimizely.com/cloud/library-collections/
- **Date exported**: March 2026
- **Method**: Automated extraction via AG Grid API + parallel curl downloads
