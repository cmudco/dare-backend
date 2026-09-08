"""Fail deployment early when document-processing native libraries cannot load.

Run with the same Python environment as the RQ workers. No model downloads or
provider calls are made; converter imports alone do not exercise OpenCV.
"""

import cv2
import numpy as np
from docling.document_converter import DocumentConverter

# Exercise the image operation as well as the dynamic loader.
image = cv2.cvtColor(np.zeros((2, 2, 3), dtype=np.uint8), cv2.COLOR_BGR2GRAY)
if image.shape != (2, 2):
    raise RuntimeError("OpenCV image conversion failed")
print("Document runtime ready (OpenCV and Docling imports)")
