
Using Docling's image crops' bounding boxes, extract each image as an svg:

```python
import subprocess
import tempfile
import pypdfium2 as pdfium

def extract_region_to_svg(pdf_path: str, page_index: int, bbox: tuple, output_svg: str):
    """
    bbox: (left, bottom, right, top) in PDF points (72 DPI, origin at bottom-left)
    """
    pdf = pdfium.PdfDocument(pdf_path)
    page = pdf[page_index]
    
    # Set the cropbox to the target region
    left, bottom, right, top = bbox
    page.set_cropbox(left, bottom, right, top)
    
    # Save cropped page to a temporary PDF and convert via pdftocairo
    with tempfile.NamedTemporaryFile(suffix=".pdf") as tmp_pdf:
        new_pdf = pdfium.PdfDocument.new()
        new_pdf.import_pages(pdf, pages=[page_index])
        new_pdf.save(tmp_pdf.name)
        
        subprocess.run(
            ["pdftocairo", "-svg", tmp_pdf.name, output_svg],
            check=True
        )

# Example: Extract a 200x200 pt region from the first page
extract_region_to_svg("input.pdf", 0, (100, 500, 300, 700), "region.svg")
```

Then use the following to determine whether an extracted SVG is impure—has any embedded bitmaps:

```python
import re
import xml.etree.ElementTree as ET

def is_pure_vector_svg(file_path):
  # 1. Check raw text for base64 encoded raster images (PNG, JPEG, GIF, WebP)
  with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

  if re.search(
      r'data:image/(png|jpeg|jpg|gif|webp|bmp)', content, re.IGNORECASE
  ):
    return False, 'Contains embedded base64 raster image data.'

  # 2. Parse XML structure to check for <image> elements
  try:
    tree = ET.parse(file_path)
    root = tree.getroot()
  except ET.ParseError as e:
    return False, f'Invalid XML/SVG file: {e}'

  for elem in root.iter():
    # Strip XML namespace (e.g., '{http://w3.org}image' -> 'image')
    tag_name = elem.tag.split('}')[-1].lower()
    if tag_name == 'image':
      return False, 'Contains an <image> tag (linked or embedded raster).'

  return True, 'Pure vector SVG (no bitmaps found).'
```

If an image is purge SVG then store it as an SVG source. 
Otherwise, it should be treated as a raster image and handled accordingly.

==============================================================

When evaluating raster images such as PNG, JPEG, and TIFF, we can use edge-based complexity scoring to determine how "complex" or "detailed" an image is.
This can help differentiate between simple graphics and more intricate raster images.
Highly complex images, especially smaller ones that have a complexity above `os.environ.get("IMAGE_COMPLEXITY_THRESHOLD", 0.5)`, are not suitable for use in posters because they will not scale well and may appear visually cluttered or noisy at a three-foot viewing distance.

```python
import cv2
import numpy as np
import math

def complexity_score(image_path, penalty_power=0.15):
    img = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
    h, w = img.shape
    original_area = w * h

    # 1. Light blur to ignore subtle sensor noise / faint gradients
    blurred = cv2.GaussianBlur(img, (3, 3), 0)

    # 2. Detect strong structural edges
    edges = cv2.Canny(blurred, threshold1=50, threshold2=150)
    edge_count = np.count_nonzero(edges)

    # 3. Standard normalized density (0.0 to 1.0)
    base_density = edge_count / original_area

    # 4. Penalize larger dimensions so sparse large images score even lower
    size_penalty = (original_area / 10000.0) ** penalty_power
    
    return base_density / max(size_penalty, 1.0)
```